/*
 * ============================================================================
 * MÓDULO: PlaybackFragment — Reproductor de grabaciones históricas (CamLink)
 * ============================================================================
 *
 * PROPÓSITO
 *   Pantalla que reproduce grabaciones almacenadas en el backend NVR mediante
 *   ExoPlayer (Media3). Reproduce desde las URLs FIRMADAS (token HMAC) que
 *   entrega el backend, encadenando los segmentos de un día para verlos como
 *   una línea de tiempo continua.
 *
 * RESPONSABILIDAD
 *   - Construir la lista (playlist) de MediaItem a reproducir según el modo:
 *       · Legado: un único `recordingId`.
 *       · Día/timeline: `cameraId` + `date` + `mode` → todas las grabaciones
 *         del día de ese tipo, ordenadas y reproducidas EN CADENA.
 *   - Posicionar el índice inicial (por `startRecordingId` o por `targetTime`,
 *     buscando el segmento más cercano al instante del evento de la push).
 *   - Cámaras dual-lens: el RECORTE de lente lo hace el BACKEND server-side; el
 *     fragment solo añade `?lens=l1|l2` a la URL firmada y re-pide al servidor
 *     al alternar el lente (ver streamUrl / reloadWithCurrentLens).
 *   - Controles de transporte (play/pause, anterior/siguiente, seek) y barra
 *     de progreso sincronizada con la posición de ExoPlayer.
 *
 * DEPENDENCIAS
 *   - androidx.media3 (ExoPlayer + PlayerView): decodificación/render del vídeo.
 *   - RetrofitClient + ApiService: resuelve la URL base y consulta metadatos de
 *     grabaciones (getRecordings / getRecording / getCamera). El vídeo en sí NO
 *     pasa por Retrofit, se sirve por HTTP con URL firmada directa a ExoPlayer.
 *   - model.RecordingResponse: DTO de cada grabación (incluye playbackUrl/fileUrl).
 *
 * COMPONENTES RELACIONADOS
 *   - RecordingsHostFragment / RecordingsFragment / TimelineFragment: listan las
 *     grabaciones y navegan aquí con los argumentos de reproducción.
 *   - MainActivity.handleNotificationIntent: abre este fragment al tocar una
 *     push de evento (argumentos cameraId/date/mode=event/targetTime).
 *
 * PUNTO DE ENTRADA
 *   Destino de Navigation `playbackFragment`. Se instancia al navegar desde las
 *   listas de grabaciones o desde el deep-link de notificación.
 *
 * PIPELINE(S)
 *   #14 Reproducción histórica — etapa final (cliente reproduce los segmentos
 *   grabados por el Pipeline #11 Grabación, servidos con URL firmada).
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.view.LayoutInflater
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
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
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
 *   1. Legado: un único `recordingId`.
 *   2. Timeline: `cameraId` + `date` + `mode` → reproduce EN CADENA todas las
 *      grabaciones del día de ese tipo.
 *
 * Recorte dual-lens: SERVER-SIDE. La grabación es el combinado (dos lentes
 * apilados); el backend devuelve solo el lente pedido vía `?lens=l1|l2` en la
 * URL firmada (recortado + cacheado). Antes se intentaba recortar en el cliente
 * con una matriz sobre TextureView, pero sangraba el otro lente en el letterbox;
 * el recorte en servidor es fiable y se ve con `PlayerView` (encaje automático).
 *
 * Lo instancian RecordingsHostFragment / TimelineFragment / RecordingsFragment
 * (vía NavController) y MainActivity al tocar una notificación push de evento.
 *
 * Ciclo de vida: los argumentos se leen en onCreate; ExoPlayer se crea de forma
 * perezosa tras resolver la playlist en loadAndPlay (onViewCreated); se pausa en
 * onPause y se LIBERA en onDestroyView (release) para no fugar el codec/surface.
 *
 * Pipeline #14 (reproducción histórica).
 */
class PlaybackFragment : Fragment() {

    private var player: ExoPlayer? = null
    private var playerView: PlayerView? = null
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
    private var currentLens: String? = null     // null = sin recorte | "l1" | "l2"

    // Estado de la lista (para reconstruir URLs al cambiar de lente sin re-pedir).
    private var recordings: List<RecordingResponse> = emptyList()
    private var baseUrlCached: String? = null

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

        // PlayerView de Media3 (encaje de aspecto automático). El recorte ya
        // viene del servidor, así que no hace falta transformar la superficie.
        val pv = PlayerView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            useController = false
        }
        videoContainer.addView(pv, 0)
        playerView = pv

        btnLensToggle?.setOnClickListener {
            currentLens = if (currentLens == "l2") "l1" else "l2"
            updateLensButtonText()
            reloadWithCurrentLens()   // re-pide el otro lente al servidor
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

    /**
     * URL absoluta de reproducción de una grabación, con recorte por lente
     * server-side si la cámara es dual y hay lente activo (`&lens=`).
     *
     * Prefiere `playbackUrl` y cae a `fileUrl`; si la URL es relativa la
     * antepone con la base cacheada. La URL ya viene FIRMADA (token HMAC) del
     * backend, por lo que ExoPlayer la consume directamente.
     *
     * @param rec grabación de la que obtener la URL.
     * @return URL absoluta lista para ExoPlayer, o null si no hay URL/base.
     * Llamado por: buildMediaItems.
     */
    private fun streamUrl(rec: RecordingResponse): String? {
        val base = baseUrlCached ?: return null
        val raw = rec.playbackUrl?.let { if (it.startsWith("http")) it else base + it }
            ?: rec.fileUrl?.let { if (it.startsWith("http")) it else base + it }
            ?: return null
        if (!isDualLens || currentLens == null) return raw
        val sep = if (raw.contains("?")) "&" else "?"
        return "$raw${sep}lens=$currentLens"
    }

    /**
     * Convierte la lista actual de grabaciones en MediaItem para la playlist de
     * ExoPlayer (uno por segmento, con su URL firmada y el lente activo).
     *
     * @return lista de MediaItem en el mismo orden que `recordings`.
     * Llamado por: loadAndPlay, reloadWithCurrentLens. Llama a: streamUrl.
     */
    private fun buildMediaItems(): List<MediaItem> =
        recordings.mapNotNull { streamUrl(it)?.let { u -> MediaItem.fromUri(u) } }

    /**
     * Resuelve la playlist, crea el ExoPlayer y arranca la reproducción.
     *
     * Flujo: resuelve la URL base → (si vino por cámara) consulta getCamera para
     * saber si es dual-lens y activar el toggle → construye la lista del día
     * (buildDayPlaylist) o la grabación única (getRecording) → arma los MediaItem
     * y reproduce desde el índice inicial calculado.
     *
     * @param btnPlayPause botón cuyo icono se sincroniza con onIsPlayingChanged.
     * Llamado por: onViewCreated. Llama a: buildDayPlaylist, buildMediaItems,
     * ApiService.getCamera/getRecordings/getRecording.
     */
    @OptIn(UnstableApi::class)
    private fun loadAndPlay(btnPlayPause: ImageButton) {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        baseUrlCached = baseUrl
        val api = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                // Si venimos por cámara y no sabíamos si es dual, consultarlo para
                // activar el recorte por lente automáticamente.
                if (argCameraId > 0 && !isDualLens) {
                    try {
                        isDualLens = api.getCamera(argCameraId).isDualLens
                        refreshLensUi()
                    } catch (_: Exception) {}
                }

                val startIndex: Int
                if (argCameraId > 0 && !argDate.isNullOrBlank()) {
                    val (recs, idx) = buildDayPlaylist(api)
                    recordings = recs
                    startIndex = idx
                } else {
                    val rec = argRecordingId?.let { api.getRecording(it).data }
                    recordings = listOfNotNull(rec)
                    startIndex = 0
                }

                val items = buildMediaItems()
                if (items.isEmpty()) {
                    Toast.makeText(requireContext(), "No hay grabaciones para reproducir", Toast.LENGTH_LONG).show()
                    return@launch
                }

                player = ExoPlayer.Builder(requireContext()).build().also { exo ->
                    playerView?.player = exo
                    exo.setMediaItems(items, startIndex.coerceIn(0, items.size - 1), 0L)
                    exo.prepare()
                    exo.playWhenReady = true
                    exo.addListener(object : Player.Listener {
                        override fun onIsPlayingChanged(isPlaying: Boolean) {
                            btnPlayPause.setImageResource(
                                if (isPlaying) R.drawable.ic_record_stop else R.drawable.ic_play
                            )
                        }
                    })
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error al cargar grabación: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    /** Re-pide la lista con el lente actual conservando posición e índice. */
    private fun reloadWithCurrentLens() {
        val p = player ?: return
        if (recordings.isEmpty()) return
        val idx = p.currentMediaItemIndex
        val pos = p.currentPosition
        val items = buildMediaItems()
        if (items.isEmpty()) return
        p.setMediaItems(items, idx.coerceIn(0, items.size - 1), pos)
        p.prepare()
        p.play()
    }

    /**
     * Grabaciones del día filtradas por tipo, ordenadas, + índice inicial.
     *
     * Pide `getRecordings(cameraId, date)`, filtra por `argMode` (o todas),
     * ordena por hora de inicio y calcula el índice de arranque: por
     * `argStartRecordingId` si llega, o por el segmento más cercano a
     * `argTargetTime` (instante del evento de una push).
     *
     * @param api cliente Retrofit ya construido con la URL base.
     * @return par (lista de grabaciones del día, índice inicial en esa lista).
     * Llamado por: loadAndPlay. Usa endpoint GET /recordings.
     */
    private suspend fun buildDayPlaylist(
        api: com.ipn.mx.onvif.network.ApiService
    ): Pair<List<RecordingResponse>, Int> {
        val all = api.getRecordings(cameraId = argCameraId, date = argDate).data ?: emptyList()
        val filtered = all
            .filter { argMode == "all" || it.type == argMode }
            .sortedBy { it.startedAt }
        if (filtered.isEmpty()) return emptyList<RecordingResponse>() to 0

        var startIndex = 0
        if (argStartRecordingId > 0) {
            val idx = filtered.indexOfFirst { (it.id.toIntOrNull() ?: -1) == argStartRecordingId }
            if (idx >= 0) startIndex = idx
        } else if (!argTargetTime.isNullOrBlank()) {
            val target = parseIso(argTargetTime)
            if (target > 0) {
                val idx = filtered.indices.minByOrNull {
                    val t = parseIso(filtered[it].startedAt)
                    if (t > 0) kotlin.math.abs(t - target) else Long.MAX_VALUE
                }
                startIndex = idx ?: 0
            }
        }
        return filtered to startIndex
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
        playerView = null
        btnLensToggle = null
    }
}
