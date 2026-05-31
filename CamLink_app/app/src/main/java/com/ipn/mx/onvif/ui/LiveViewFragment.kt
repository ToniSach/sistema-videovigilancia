package com.ipn.mx.onvif.ui

import android.annotation.SuppressLint
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
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.rtsp.RtspMediaSource
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.model.PtzRequest
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch

class LiveViewFragment : BaseMenuFragment() {

    // ── Estado de la cámara activa ────────────────────────────────────────────
    private var cameras: List<CameraResponse> = emptyList()
    private var currentCamIndex = 0
    private val currentCamera get() = cameras.getOrNull(currentCamIndex)

    // ── ExoPlayer ─────────────────────────────────────────────────────────────
    private var player: ExoPlayer? = null

    // ── Estado de controles ───────────────────────────────────────────────────
    private var isRecording = false
    private var micOn       = false
    private var nightOn     = false

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
            if (cameras.isEmpty()) return@setOnClickListener
            currentCamIndex = (currentCamIndex - 1 + cameras.size) % cameras.size
            playCurrentCamera(surfaceView)
        }
        btnNextFeed.setOnClickListener {
            if (cameras.isEmpty()) return@setOnClickListener
            currentCamIndex = (currentCamIndex + 1) % cameras.size
            playCurrentCamera(surfaceView)
        }

        // ── Joystick PTZ ─────────────────────────────────────────────────────
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

                    val normX = dx / radius
                    val normY = dy / radius
                    sendPtz(normX, normY)
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    joystickThumb.animate().translationX(0f).translationY(0f).setDuration(150).start()
                    sendPtz(0f, 0f)  // detener movimiento
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
        btnFullscreen.setOnClickListener {
            Toast.makeText(requireContext(), "Pantalla completa: pendiente", Toast.LENGTH_SHORT).show()
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
                    cameras = response.body()?.data ?: emptyList()
                    if (cameras.isNotEmpty()) {
                        currentCamIndex = 0
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

    @OptIn(UnstableApi::class)
    private fun playCurrentCamera(surfaceView: SurfaceView) {
        val camera = currentCamera ?: return

        // Liberar instancia anterior
        player?.release()

        player = ExoPlayer.Builder(requireContext()).build().also { exo ->
            exo.setVideoSurfaceView(surfaceView)

            val mediaSource = RtspMediaSource.Factory()
                .createMediaSource(MediaItem.fromUri(camera.rtspUrl))

            exo.setMediaSource(mediaSource)
            exo.prepare()
            exo.playWhenReady = true
        }

        Toast.makeText(requireContext(), camera.name, Toast.LENGTH_SHORT).show()
    }

    // ── PTZ ───────────────────────────────────────────────────────────────────

    private fun sendPtz(normX: Float, normY: Float) {
        val camera = currentCamera ?: return
        if (!camera.hasPtz) return  // la cámara iCSee no tiene PTZ

        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                api.sendPtz(camera.id, PtzRequest(normX, normY))
            } catch (_: Exception) {
                // PTZ es best-effort — no mostrar error al usuario en cada movimiento
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
    }
}
