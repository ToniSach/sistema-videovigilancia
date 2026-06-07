package com.ipn.mx.onvif.ui

import android.graphics.Matrix
import android.graphics.RectF
import android.os.Bundle
import android.view.LayoutInflater
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.SeekBar
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import com.google.android.material.button.MaterialButton
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.RecordingResponse
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Reproductor de grabaciones. Dos modos:
 *
 *  1. Legado: un único `recordingId` (String) → reproduce esa grabación.
 *  2. Timeline: `cameraId` + `date` + `mode` (continuous|event) → carga TODAS
 *     las grabaciones del día de ese tipo y las reproduce EN CADENA (timeline),
 *     empezando en `startRecordingId` o en la que cubre `targetTime` (link de
 *     una alerta). ExoPlayer encadena los MediaItems automáticamente.
 *
 * Dual-lens: el archivo es el combinado (dos lentes apilados). El recorte por
 * lente se hace en el CLIENTE con una matriz sobre el TextureView (sin re-pedir
 * ni transcodificar nada en el servidor); el toggle cambia de lente al instante.
 *   - l2 = mitad superior, l1 = mitad inferior (igual que go2rtc/desktop).
 */
class PlaybackFragment : Fragment() {

    private var player: ExoPlayer? = null
    private var textureView: TextureView? = null
    private var btnLensToggle: MaterialButton? = null
    private var isSeekingByUser = false

    // Argumentos
    private var argRecordingId: String? = null
    private var argCameraId = -1
    private var argDate: String? = null
    private var argMode = "all"
    private var argStartRecordingId = -1
    private var argTargetTime: String? = null
    private var isDualLens = false

    // Estado de recorte por lente
    private var currentLens: String? = null     // null = sin recorte | "l1" | "l2"
    private var videoW = 0
    private var videoH = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        arguments?.let { a ->
            argRecordingId = a.getString("recordingId")?.takeIf { it.isNotBlank() }
            argCameraId = a.getInt("cameraId", -1)
            argDate = a.getString("date")
            argMode = a.getString("mode", "all") ?: "all"
            argStartRecordingId = a.getInt("startRecordingId", -1)
            argTargetTime = a.getString("targetTime")
            isDualLens = a.getBoolean("isDualLens", false)
            currentLens = a.getString("lens")
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_playback, container, false)

    @OptIn(UnstableApi::class)
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val videoContainer = view.findViewById<FrameLayout>(R.id.videoContainer)
        val seekBar        = view.findViewById<SeekBar>(R.id.seekBar)
        val btnPlayPause   = view.findViewById<ImageButton>(R.id.btnPlayPause)
        val btnPrevious    = view.findViewById<ImageButton>(R.id.btnPrevious)
        val btnNext        = view.findViewById<ImageButton>(R.id.btnNext)
        btnLensToggle      = view.findViewById(R.id.btnLensToggle)

        // TextureView (en vez de PlayerView) para poder recortar por lente con
        // una matriz. Se inserta detrás del botón de lente (índice 0).
        val tv = TextureView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> applyLensTransform() }
        }
        videoContainer.addView(tv, 0)
        textureView = tv

        // Toggle de lente (visibilidad real se decide en refreshLensUi, una vez
        // sabemos si la cámara es dual — el link de alerta no lo informa).
        btnLensToggle?.setOnClickListener {
            currentLens = if (currentLens == "l2") "l1" else "l2"
            updateLensButtonText()
            applyLensTransform()   // recorte instantáneo, sin recargar
        }
        refreshLensUi()

        loadAndPlay(btnPlayPause)

        // ── Controles ─────────────────────────────────────────────────────────
        btnPlayPause.setOnClickListener {
            player?.let {
                if (it.isPlaying) {
                    it.pause()
                    btnPlayPause.setImageResource(R.drawable.ic_play)
                } else {
                    it.play()
                    btnPlayPause.setImageResource(R.drawable.ic_record_stop)
                }
            }
        }
        // En modo timeline saltan a la grabación anterior/siguiente del día.
        btnPrevious.setOnClickListener {
            val p = player ?: return@setOnClickListener
            if (p.hasPreviousMediaItem() && p.currentPosition < 3000) p.seekToPreviousMediaItem()
            else p.seekTo(0)
        }
        btnNext.setOnClickListener {
            val p = player ?: return@setOnClickListener
            if (p.hasNextMediaItem()) p.seekToNextMediaItem()
            else Toast.makeText(requireContext(), "Sin grabación siguiente", Toast.LENGTH_SHORT).show()
        }

        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) {
                    val duration = player?.duration ?: return
                    if (duration > 0) player?.seekTo(duration * progress / 1000L)
                }
            }
            override fun onStartTrackingTouch(sb: SeekBar?) { isSeekingByUser = true }
            override fun onStopTrackingTouch(sb: SeekBar?)  { isSeekingByUser = false }
        })

        viewLifecycleOwner.lifecycleScope.launch {
            while (isActive) {
                if (!isSeekingByUser) {
                    val duration = player?.duration ?: 0L
                    val position = player?.currentPosition ?: 0L
                    if (duration > 0) seekBar.progress = (position * 1000 / duration).toInt()
                }
                delay(1000)
            }
        }
    }

    private fun updateLensButtonText() {
        btnLensToggle?.text = if (currentLens == "l2") "Lente 2" else "Lente 1"
    }

    /** Muestra/oculta el toggle de lente según si la cámara es dual. */
    private fun refreshLensUi() {
        if (isDualLens) {
            if (currentLens == null) currentLens = "l1"
            btnLensToggle?.visibility = View.VISIBLE
            updateLensButtonText()
        } else {
            currentLens = null
            btnLensToggle?.visibility = View.GONE
        }
    }

    @OptIn(UnstableApi::class)
    private fun loadAndPlay(btnPlayPause: ImageButton) {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                // Si venimos por cámara (timeline/alerta) y no sabíamos si es dual,
                // consultarlo para activar el recorte por lente automáticamente.
                if (argCameraId > 0 && !isDualLens) {
                    try {
                        isDualLens = api.getCamera(argCameraId).isDualLens
                        refreshLensUi()
                    } catch (_: Exception) {}
                }

                // Construir la lista de reproducción (URL absolutas firmadas) +
                // el índice donde empezar.
                val (urls, startIndex) = if (argCameraId > 0 && !argDate.isNullOrBlank()) {
                    buildDayPlaylist(api, baseUrl)
                } else {
                    val rec = argRecordingId?.let { api.getRecording(it).data }
                    val u = rec?.let { absUrl(it, baseUrl) }
                    (listOfNotNull(u)) to 0
                }

                if (urls.isEmpty()) {
                    Toast.makeText(requireContext(), "No hay grabaciones para reproducir", Toast.LENGTH_LONG).show()
                    return@launch
                }

                player = ExoPlayer.Builder(requireContext()).build().also { exo ->
                    textureView?.let { exo.setVideoTextureView(it) }
                    exo.setMediaItems(urls.map { MediaItem.fromUri(it) }, startIndex, 0L)
                    exo.prepare()
                    exo.playWhenReady = true
                    exo.addListener(object : Player.Listener {
                        override fun onIsPlayingChanged(isPlaying: Boolean) {
                            btnPlayPause.setImageResource(
                                if (isPlaying) R.drawable.ic_record_stop else R.drawable.ic_play
                            )
                        }
                        override fun onVideoSizeChanged(size: VideoSize) {
                            videoW = size.width
                            videoH = size.height
                            applyLensTransform()
                        }
                    })
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error al cargar grabación: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    /** Pide las grabaciones del día, filtra por tipo de la pestaña, las ordena
     *  cronológicamente y devuelve (urls, índiceInicial). */
    private suspend fun buildDayPlaylist(
        api: com.ipn.mx.onvif.network.ApiService, baseUrl: String
    ): Pair<List<String>, Int> {
        val all = api.getRecordings(cameraId = argCameraId, date = argDate).data ?: emptyList()
        val filtered = all
            .filter { argMode == "all" || it.type == argMode }
            .sortedBy { it.startedAt }   // ascendente = orden timeline
        if (filtered.isEmpty()) return emptyList<String>() to 0

        // Índice de inicio: por id explícito, o por la grabación que cubre/precede
        // a targetTime (link de alerta), o 0.
        var startIndex = 0
        if (argStartRecordingId > 0) {
            val idx = filtered.indexOfFirst { (it.id.toIntOrNull() ?: -1) == argStartRecordingId }
            if (idx >= 0) startIndex = idx
        } else if (!argTargetTime.isNullOrBlank()) {
            val target = parseIso(argTargetTime)
            if (target > 0) {
                // Grabación con inicio MÁS CERCANO al instante de la alerta.
                val idx = filtered.indices.minByOrNull {
                    val t = parseIso(filtered[it].startedAt)
                    if (t > 0) kotlin.math.abs(t - target) else Long.MAX_VALUE
                }
                startIndex = idx ?: 0
            }
        }
        val urls = filtered.mapNotNull { absUrl(it, baseUrl) }
        // Si alguna URL faltó, recalcular el índice por seguridad (mapNotNull
        // podría descolocarlo); como las firmadas siempre vienen, es defensivo.
        return urls to startIndex.coerceIn(0, (urls.size - 1).coerceAtLeast(0))
    }

    private fun absUrl(rec: RecordingResponse, baseUrl: String): String? {
        val u = rec.playbackUrl?.let { if (it.startsWith("http")) it else baseUrl + it } ?: rec.fileUrl
        return u?.takeIf { it.isNotBlank() }
    }

    /** Aplica el recorte por lente sobre el TextureView. Cada lente del combinado
     *  es 16:9; lo encajamos (fit) centrado en el contenedor. Sin lente = vídeo
     *  completo encajado. */
    private fun applyLensTransform() {
        val tv = textureView ?: return
        val cw = tv.width.toFloat()
        val ch = tv.height.toFloat()
        if (cw <= 0f || ch <= 0f || videoW <= 0 || videoH <= 0) return

        val lens = currentLens
        // Región de origen (en coordenadas de la vista, donde por defecto el buffer
        // completo ocupa [0,0,cw,ch]).
        val src = when (lens) {
            "l2" -> RectF(0f, 0f, cw, ch / 2f)   // mitad superior
            "l1" -> RectF(0f, ch / 2f, cw, ch)   // mitad inferior
            else -> RectF(0f, 0f, cw, ch)
        }
        // Tamaño del contenido a mostrar (px de origen) y su encaje centrado.
        val contentW = videoW.toFloat()
        val contentH = if (lens == null) videoH.toFloat() else videoH / 2f
        val scale = minOf(cw / contentW, ch / contentH)
        val dW = contentW * scale
        val dH = contentH * scale
        val ox = (cw - dW) / 2f
        val oy = (ch - dH) / 2f
        val dst = RectF(ox, oy, ox + dW, oy + dH)

        val m = Matrix()
        m.setRectToRect(src, dst, Matrix.ScaleToFit.FILL)
        tv.setTransform(m)
        tv.invalidate()
    }

    private fun parseIso(iso: String?): Long {
        if (iso.isNullOrBlank()) return 0
        return try {
            val clean = iso.substringBefore('.').replace("Z", "")
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(clean)?.time ?: 0
        } catch (e: Exception) { 0 }
    }

    override fun onPause() {
        super.onPause()
        player?.pause()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        player?.release()
        player = null
        textureView = null
        btnLensToggle = null
    }
}
