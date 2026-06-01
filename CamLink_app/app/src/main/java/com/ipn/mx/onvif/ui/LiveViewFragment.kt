package com.ipn.mx.onvif.ui

import android.annotation.SuppressLint
import android.content.pm.ActivityInfo
import android.os.Bundle
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.appcompat.app.AppCompatActivity
import androidx.constraintlayout.widget.ConstraintLayout
import androidx.constraintlayout.widget.ConstraintSet
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch

class LiveViewFragment : BaseMenuFragment() {

    // ── Estado: "feeds" ───────────────────────────────────────────────────────
    // Una cámara mono = 1 feed; una dual-lens = 2 feeds (L1 y L2). La navegación
    // prev/next cicla por feeds, así cada lente se ve por separado por WebRTC/RTSP
    // (go2rtc) sin tocar el layout.
    private data class Feed(
        val camera: CameraResponse,
        val lens: String?,        // null (mono) | "l1" | "l2"
        val url: String,          // URL preferida (HLS si existe)
        val fallbackUrl: String?, // RTSP de respaldo si el HLS falla
        val label: String,
    )
    private var feeds: List<Feed> = emptyList()
    private var currentFeedIndex = 0
    private val currentFeed get() = feeds.getOrNull(currentFeedIndex)
    private val currentCamera get() = currentFeed?.camera
    // Para no entrar en bucle de fallback HLS→RTSP→HLS por feed.
    private var triedFallback = false

    /** Construye la lista de feeds: dual-lens → 2 (L1/L2); mono → 1.
     *  Prefiere HLS (fiable en ExoPlayer); RTSP queda como fallback. */
    private fun buildFeeds(cams: List<CameraResponse>): List<Feed> {
        val out = mutableListOf<Feed>()
        for (c in cams) {
            if (c.isDualLens && !c.streamUrlL1.isNullOrBlank() && !c.streamUrlL2.isNullOrBlank()) {
                val urlL1 = c.hlsUrlL1?.takeIf { it.isNotBlank() } ?: c.streamUrlL1!!
                val urlL2 = c.hlsUrlL2?.takeIf { it.isNotBlank() } ?: c.streamUrlL2!!
                out.add(Feed(c, "l1", urlL1, c.streamUrlL1, "${c.name} · L1"))
                out.add(Feed(c, "l2", urlL2, c.streamUrlL2, "${c.name} · L2"))
            } else {
                val url = c.liveHlsUrl ?: c.liveUrl
                out.add(Feed(c, null, url, c.liveUrl, c.name))
            }
        }
        return out
    }

    // ── ExoPlayer ─────────────────────────────────────────────────────────────
    private var player: ExoPlayer? = null

    // ── Referencias a vistas (para ocultar controles no soportados + fullscreen)
    private var rootLayout: ConstraintLayout? = null
    private var cameraFeedRef: FrameLayout? = null
    private var controlsCardRef: View? = null
    private var joystickCardRef: View? = null
    private var bottomRowRefs: List<View> = emptyList()
    private var btnMicRef: ImageButton? = null
    private var btnFullscreenRef: ImageButton? = null
    private var isFullscreen = false

    // ── Estado de controles ───────────────────────────────────────────────────
    private var isRecording = false
    private var micOn       = false
    private var nightOn     = false
    private var lastPtzDir: String? = null   // última dirección PTZ enviada
    // Selector de calidad (HLS): high = nativo, medium = 480p, low = 360p.
    private val qualities = listOf("high", "medium", "low")
    private val qualityLabels = mapOf("high" to "Alta", "medium" to "Media", "low" to "Baja")
    private var qualityIndex = 0
    private val currentQuality get() = qualities[qualityIndex]

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_live_view, container, false)

    @OptIn(UnstableApi::class)
    @SuppressLint("ClickableViewAccessibility")
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val cameraFeed    = view.findViewById<FrameLayout>(R.id.cameraFeedContainer)
        val btnRecord     = view.findViewById<ImageButton>(R.id.btnRecord)
        val btnMic        = view.findViewById<ImageButton>(R.id.btnMic)
        val btnNight      = view.findViewById<ImageButton>(R.id.btnNight)
        val btnPrevFeed   = view.findViewById<ImageButton>(R.id.btnPrevFeed)
        val btnNextFeed   = view.findViewById<ImageButton>(R.id.btnNextFeed)
        val btnRemoveCam  = view.findViewById<ImageButton>(R.id.btnRemoveCam)
        val btnToggleView = view.findViewById<ImageButton>(R.id.btnVideoList)
        val btnFullscreen = view.findViewById<ImageButton>(R.id.btnFullscreen)
        val joystickOuter = view.findViewById<FrameLayout>(R.id.joystickOuter)
        val joystickThumb = view.findViewById<View>(R.id.joystickThumb)

        // Guardar refs para ocultar controles no soportados y la pantalla completa.
        rootLayout       = view as? ConstraintLayout
        cameraFeedRef    = cameraFeed
        controlsCardRef  = view.findViewById(R.id.controlsCard)
        joystickCardRef  = view.findViewById(R.id.joystickCard)
        // NO incluir btnFullscreen aquí: debe seguir visible en pantalla
        // completa para poder SALIR (antes se ocultaba a sí mismo → quedabas
        // atrapado en fullscreen).
        bottomRowRefs    = listOf(btnToggleView)
        btnMicRef        = btnMic
        btnFullscreenRef = btnFullscreen

        // Preparar el SurfaceView para ExoPlayer dentro del FrameLayout
        val surfaceView = SurfaceView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        }
        cameraFeed.addView(surfaceView, 0)

        // Cargar lista de cámaras del servidor
        loadCameras(surfaceView)

        // ── Navegación entre cámaras ─────────────────────────────────────────
        btnPrevFeed.setOnClickListener {
            if (feeds.isEmpty()) return@setOnClickListener
            currentFeedIndex = (currentFeedIndex - 1 + feeds.size) % feeds.size
            playCurrentCamera(surfaceView)
        }
        btnNextFeed.setOnClickListener {
            if (feeds.isEmpty()) return@setOnClickListener
            currentFeedIndex = (currentFeedIndex + 1) % feeds.size
            playCurrentCamera(surfaceView)
        }

        // ── Joystick PTZ ─────────────────────────────────────────────────────
        // El backend mueve por DIRECCIÓN (ONVIF ContinuousMove): up/down/left/
        // right y stop al soltar. El joystick decide la dirección dominante.
        joystickOuter.setOnTouchListener { _, event ->
            val cx     = joystickOuter.width / 2f
            val cy     = joystickOuter.height / 2f
            val radius = (joystickOuter.width / 2f) - (joystickThumb.width / 2f)

            when (event.action) {
                MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE -> {
                    var dx   = event.x - cx
                    var dy   = event.y - cy
                    val dist = Math.hypot(dx.toDouble(), dy.toDouble()).toFloat()
                    if (dist > radius) { dx *= radius / dist; dy *= radius / dist }
                    joystickThumb.translationX = dx
                    joystickThumb.translationY = dy

                    // Zona muerta + dirección dominante (eje con mayor desplazam.)
                    val dead = radius * 0.30f
                    val dir = when {
                        dist < dead          -> null
                        Math.abs(dx) > Math.abs(dy) -> if (dx > 0) "right" else "left"
                        else                 -> if (dy > 0) "down" else "up"
                    }
                    if (dir != null && dir != lastPtzDir) {
                        lastPtzDir = dir
                        sendPtzDirection(dir)
                    }
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    joystickThumb.animate().translationX(0f).translationY(0f).setDuration(150).start()
                    if (lastPtzDir != null) {
                        lastPtzDir = null
                        sendPtzDirection("stop")
                    }
                }
            }
            true
        }

        // ── Grabación manual ─────────────────────────────────────────────────
        btnRecord.setOnClickListener {
            val cam = currentCamera ?: run {
                Toast.makeText(requireContext(), "Sin cámara activa", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            // Tinte visual inmediato para feedback (rojo encendido = grabando)
            val nextState = !isRecording
            toggleRecordButtonTint(btnRecord, nextState)
            callApi(
                action = { api ->
                    if (nextState) api.startManualRecording(cam.id)
                    else           api.stopManualRecording(cam.id)
                },
                onOk = {
                    isRecording = nextState
                    Toast.makeText(
                        requireContext(),
                        if (isRecording) "Grabación iniciada" else "Grabación detenida",
                        Toast.LENGTH_SHORT,
                    ).show()
                },
                onFail = { code, msg ->
                    // Revertir tinte si el backend rechazó
                    toggleRecordButtonTint(btnRecord, isRecording)
                    val human = when (code) {
                        403 -> "Sin permiso para grabar esta cámara"
                        409 -> if (nextState) "Ya está grabando" else "No estaba grabando"
                        else -> "Error grabación: $msg"
                    }
                    Toast.makeText(requireContext(), human, Toast.LENGTH_LONG).show()
                },
            )
        }

        // ── Micrófono (talk-back hacia la cámara) ────────────────────────────
        btnMic.setOnClickListener {
            val cam = currentCamera ?: return@setOnClickListener
            val nextState = !micOn
            btnMic.isSelected = nextState
            callApi(
                action = { api ->
                    if (nextState) api.audioTalkStart(cam.id)
                    else           api.audioTalkStop(cam.id)
                },
                onOk = {
                    micOn = nextState
                    Toast.makeText(
                        requireContext(),
                        if (micOn) "Micrófono enviando audio" else "Micrófono detenido",
                        Toast.LENGTH_SHORT,
                    ).show()
                },
                onFail = { code, msg ->
                    btnMic.isSelected = micOn  // revertir
                    val human = when (code) {
                        403 -> "Sin permiso para hablar por esta cámara"
                        in 400..499 -> "La cámara no soporta talk-back"
                        else -> "Error mic: $msg"
                    }
                    Toast.makeText(requireContext(), human, Toast.LENGTH_LONG).show()
                },
            )
        }

        // ── Modo noche (IR-Cut LED) ──────────────────────────────────────────
        // 3 estados: día (off) ↔ noche (on). "auto" lo dejamos como long-press.
        btnNight.setOnClickListener {
            val cam = currentCamera ?: return@setOnClickListener
            val nextState = !nightOn
            btnNight.isSelected = nextState
            val ledState = if (nextState) "on" else "off"
            callApi(
                action = { api -> api.setLedState(cam.id, ledState) },
                onOk = {
                    nightOn = nextState
                    Toast.makeText(
                        requireContext(),
                        if (nightOn) "Modo noche ON" else "Modo noche OFF",
                        Toast.LENGTH_SHORT,
                    ).show()
                },
                onFail = { code, msg ->
                    btnNight.isSelected = nightOn  // revertir
                    val human = when (code) {
                        403 -> "Sin permiso para LEDs de esta cámara"
                        in 400..499 -> "La cámara no expone IR-Cut por ONVIF"
                        else -> "Error LEDs: $msg"
                    }
                    Toast.makeText(requireContext(), human, Toast.LENGTH_LONG).show()
                },
            )
        }
        btnNight.setOnLongClickListener {
            // Modo "auto" — la cámara decide día/noche con su sensor de luz
            val cam = currentCamera ?: return@setOnLongClickListener true
            callApi(
                action = { api -> api.setLedState(cam.id, "auto") },
                onOk = {
                    nightOn = false
                    btnNight.isSelected = false
                    Toast.makeText(requireContext(), "Modo noche: AUTO", Toast.LENGTH_SHORT).show()
                },
                onFail = { _, msg ->
                    Toast.makeText(requireContext(), "AUTO no disponible: $msg", Toast.LENGTH_LONG).show()
                },
            )
            true
        }

        // ── Botones de navegación ─────────────────────────────────────────────
        btnRemoveCam.setOnClickListener {
            findNavController().navigateUp()
        }
        btnToggleView.setOnClickListener {
            findNavController().navigate(R.id.action_liveView_to_cameraList)
        }
        btnFullscreen.setOnClickListener { toggleFullscreen() }

        // ── Selector de calidad (cicla Alta → Media → Baja) ──────────────────
        val btnQuality = view.findViewById<MaterialButton>(R.id.btnQuality)
        btnQuality.text = qualityLabels[currentQuality]
        btnQuality.setOnClickListener {
            qualityIndex = (qualityIndex + 1) % qualities.size
            btnQuality.text = qualityLabels[currentQuality]
            Toast.makeText(requireContext(), "Calidad: ${qualityLabels[currentQuality]}", Toast.LENGTH_SHORT).show()
            playCurrentCamera(surfaceView)  // recargar con la nueva calidad
        }
    }

    /** Aplica la calidad al stream: high = url tal cual; medium/low → añade el
     *  sufijo al nombre del substream de go2rtc (cam_X[_lY] → cam_X[_lY]_low). */
    private fun applyQuality(url: String?, q: String): String? {
        if (url.isNullOrBlank() || q == "high") return url
        return "${url}_$q"
    }

    // ── Mostrar/ocultar controles según capacidades de la cámara ──────────────
    /** Oculta el micrófono si la cámara no tiene audio y el joystick si no tiene
     *  PTZ — así no hay botones "muertos" (diseño más limpio). */
    private fun updateControlsForCamera(cam: CameraResponse?) {
        // El "mic" (talk-back) del backend usa el micrófono del SERVIDOR, no el
        // del teléfono → no es una función de la app móvil. Se oculta para no
        // mostrar un botón que siempre da error.
        btnMicRef?.visibility = View.GONE
        // El joystick PTZ se muestra siempre: el flag has_ptz no es fiable
        // (el escritorio mueve la cámara aunque venga en false). Si la cámara no
        // tiene PTZ, el backend simplemente ignora el movimiento.
        joystickCardRef?.visibility = View.VISIBLE
    }

    // ── Pantalla completa (landscape + inmersivo + video a pantalla) ──────────
    private fun toggleFullscreen() {
        val root = rootLayout ?: return
        val act = activity ?: return
        isFullscreen = !isFullscreen

        val bottomNav = act.findViewById<View>(R.id.bottomNav)
        if (isFullscreen) {
            act.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            (act as? AppCompatActivity)?.supportActionBar?.hide()
            bottomNav?.visibility = View.GONE
            setSystemBarsHidden(true)
            controlsCardRef?.visibility = View.GONE
            joystickCardRef?.visibility = View.GONE
            bottomRowRefs.forEach { it.visibility = View.GONE }
            applyFeedFullscreen(root, true)
            btnFullscreenRef?.setImageResource(R.drawable.ic_fullscreen)
        } else {
            act.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            (act as? AppCompatActivity)?.supportActionBar?.show()
            bottomNav?.visibility = View.VISIBLE
            setSystemBarsHidden(false)
            controlsCardRef?.visibility = View.VISIBLE
            bottomRowRefs.forEach { it.visibility = View.VISIBLE }
            applyFeedFullscreen(root, false)
            // El joystick vuelve solo si la cámara lo soporta.
            updateControlsForCamera(currentCamera)
        }
    }

    /** Expande/contrae el contenedor del video usando ConstraintSet. */
    private fun applyFeedFullscreen(root: ConstraintLayout, full: Boolean) {
        val cs = ConstraintSet().apply { clone(root) }
        val id = R.id.cameraFeedContainer
        if (full) {
            cs.setDimensionRatio(id, null)              // quitar el 16:9
            cs.constrainHeight(id, ConstraintSet.MATCH_CONSTRAINT)
            cs.connect(id, ConstraintSet.BOTTOM, ConstraintSet.PARENT_ID, ConstraintSet.BOTTOM, 0)
            cs.setMargin(id, ConstraintSet.START, 0)
            cs.setMargin(id, ConstraintSet.END, 0)
            cs.setMargin(id, ConstraintSet.TOP, 0)
        } else {
            cs.setDimensionRatio(id, "16:9")
            cs.constrainHeight(id, ConstraintSet.MATCH_CONSTRAINT)
            cs.clear(id, ConstraintSet.BOTTOM)
            val m = (12 * resources.displayMetrics.density).toInt()
            cs.setMargin(id, ConstraintSet.START, m)
            cs.setMargin(id, ConstraintSet.END, m)
            cs.setMargin(id, ConstraintSet.TOP, m)
        }
        cs.applyTo(root)
    }

    private fun setSystemBarsHidden(hidden: Boolean) {
        val window = activity?.window ?: return
        WindowCompat.setDecorFitsSystemWindows(window, !hidden)
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        if (hidden) {
            controller.hide(WindowInsetsCompat.Type.systemBars())
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    // ── Red: cargar cámaras ───────────────────────────────────────────────────

    private fun loadCameras(surfaceView: SurfaceView) {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = api.getCameras()
                if (response.isSuccessful) {
                    val cams = response.body()?.data ?: emptyList()
                    feeds = buildFeeds(cams)
                    if (feeds.isNotEmpty()) {
                        currentFeedIndex = 0
                        playCurrentCamera(surfaceView)
                    } else {
                        Toast.makeText(requireContext(), "No hay cámaras registradas", Toast.LENGTH_LONG).show()
                    }
                } else {
                    val errorMsg = when (response.code()) {
                        401 -> "Sesión expirada, vuelve a iniciar sesión"
                        403 -> "Sin permisos para ver cámaras"
                        500 -> "Error interno del servidor"
                        else -> "Error ${response.code()}"
                    }
                    Toast.makeText(requireContext(), errorMsg, Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error de conexión: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    // ── ExoPlayer: reproducir RTSP ────────────────────────────────────────────

    private fun playCurrentCamera(surfaceView: SurfaceView) {
        val feed = currentFeed ?: return

        // Liberar instancia anterior
        player?.release()
        triedFallback = false

        // Aplicar la calidad elegida (high/medium/low) a la URL preferida y a la
        // de respaldo (go2rtc tiene substreams cam_X[_lY]_low/_medium).
        val primaryUrl = applyQuality(feed.url, currentQuality) ?: feed.url
        val fallbackUrl = applyQuality(feed.fallbackUrl, currentQuality)

        player = ExoPlayer.Builder(requireContext()).build().also { exo ->
            exo.setVideoSurfaceView(surfaceView)
            // ExoPlayer detecta el tipo por la URL: .m3u8 → HLS, rtsp:// → RTSP
            // (ambos módulos están en el classpath). No hace falta factory manual.
            exo.addListener(object : Player.Listener {
                override fun onPlayerError(error: PlaybackException) {
                    if (!triedFallback && !fallbackUrl.isNullOrBlank() && fallbackUrl != primaryUrl) {
                        // HLS falló → reintentar una vez con RTSP directo.
                        triedFallback = true
                        android.util.Log.w("LiveView", "HLS falló (${error.errorCodeName}); probando RTSP: $fallbackUrl")
                        exo.setMediaItem(MediaItem.fromUri(fallbackUrl))
                        exo.prepare()
                        exo.playWhenReady = true
                    } else {
                        Toast.makeText(
                            requireContext(),
                            "No se pudo reproducir ${feed.label}: ${error.errorCodeName}",
                            Toast.LENGTH_LONG,
                        ).show()
                    }
                }
            })
            exo.setMediaItem(MediaItem.fromUri(primaryUrl))
            exo.prepare()
            exo.playWhenReady = true
        }

        // Ajustar qué controles se muestran según las capacidades de la cámara.
        updateControlsForCamera(feed.camera)
        Toast.makeText(requireContext(), feed.label, Toast.LENGTH_SHORT).show()
    }

    // ── PTZ ───────────────────────────────────────────────────────────────────

    /** Envía un movimiento PTZ por dirección (up/down/left/right/stop). */
    private fun sendPtzDirection(direction: String) {
        val camera = currentCamera ?: return
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.ptzMove(camera.id, direction)
                if (!resp.isSuccessful && direction != "stop" && resp.code() != 423) {
                    val human = when (resp.code()) {
                        403 -> "Sin permiso para mover esta cámara"
                        404, in 400..499 -> "Esta cámara no soporta PTZ"
                        else -> "Error PTZ: ${resp.code()}"
                    }
                    Toast.makeText(requireContext(), human, Toast.LENGTH_SHORT).show()
                }
            } catch (_: Exception) {
                // best-effort: no spamear toasts en cada movimiento
            }
        }
    }

    // ── Helpers de control de botones ─────────────────────────────────────────

    /**
     * Llama un endpoint Retrofit que devuelve Response<Unit>. Despacha en el
     * lifecycleScope del viewLifecycleOwner y enruta el resultado a onOk/onFail.
     * Cualquier ConnectException o IOException se reporta como onFail(-1, msg).
     */
    private fun callApi(
        action: suspend (com.ipn.mx.onvif.network.ApiService) -> retrofit2.Response<Unit>,
        onOk: () -> Unit,
        onFail: (Int, String) -> Unit,
    ) {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext())
        if (baseUrl == null) {
            onFail(-1, "Sin URL de servidor")
            return
        }
        val api = RetrofitClient.create(baseUrl, requireContext())
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = action(api)
                if (resp.isSuccessful) {
                    onOk()
                } else {
                    val body = try { resp.errorBody()?.string()?.take(200) ?: "" }
                              catch (_: Exception) { "" }
                    onFail(resp.code(), body.ifBlank { "HTTP ${resp.code()}" })
                }
            } catch (e: Exception) {
                onFail(-1, e.message ?: e.javaClass.simpleName)
            }
        }
    }

    /** Tinta el botón de grabar en rojo si está activo, neutro si no. */
    private fun toggleRecordButtonTint(btn: ImageButton, recording: Boolean) {
        // isSelected dispara el state selector del drawable de fondo si existe.
        btn.isSelected = recording
        // Refuerzo visual con tint del icono (color rojo cuando graba).
        val color = if (recording) android.graphics.Color.parseColor("#ef4444")
                    else           android.graphics.Color.parseColor("#94a3b8")
        btn.imageTintList = android.content.res.ColorStateList.valueOf(color)
    }

    // ── Ciclo de vida del player ──────────────────────────────────────────────

    override fun onPause() {
        super.onPause()
        player?.pause()
    }

    override fun onResume() {
        super.onResume()
        player?.play()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        player?.release()
        player = null
        // Si salimos estando en pantalla completa, restaurar el chrome del
        // sistema y la orientación para no dejar la app "rota" en otras pantallas.
        if (isFullscreen) {
            (activity as? AppCompatActivity)?.supportActionBar?.show()
            setSystemBarsHidden(false)
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            isFullscreen = false
        }
        rootLayout = null
        cameraFeedRef = null
        controlsCardRef = null
        joystickCardRef = null
        bottomRowRefs = emptyList()
        btnMicRef = null
        btnFullscreenRef = null
    }
}
