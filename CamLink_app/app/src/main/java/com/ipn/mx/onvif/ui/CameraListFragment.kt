/*
 * ============================================================================
 * MÓDULO: CameraListFragment — Rejilla de cámaras del usuario (CamLink Android)
 * ============================================================================
 *
 * PROPÓSITO
 *   Mostrar varias cámaras a la vez en una rejilla paginada (2 por página) con
 *   vista previa en vivo de cada una, como "panel de control" desde el que el
 *   usuario salta al directo a pantalla completa (LiveViewFragment).
 *
 * RESPONSABILIDAD
 *   - Cargar las cámaras del usuario (GET /cameras) y aplanarlas en "tiles":
 *     mono = 1 tile; dual-lens = 2 tiles (L1/L2), vistos como independientes.
 *   - Reproducir cada slot con ExoPlayer (HLS preferido, RTSP de fallback) en
 *     calidad MEDIA (varias a la vez es lo más pesado), en silencio.
 *   - Paginar, ocultar/desconectar slots y navegar al live del tile tocado
 *     (pasando cameraId + lens).
 *
 * DEPENDENCIAS
 *   - ExoPlayer/Media3 (HLS+RTSP) — un reproductor por slot (máx. 2).
 *   - RetrofitClient + ApiService — GET /cameras.
 *   - go2rtc (sidecar) — restream HLS/RTSP del que se nutren los slots.
 *   - BaseMenuFragment — menú compartido.
 *
 * COMPONENTES RELACIONADOS
 *   - LiveViewFragment — destino al tocar un tile (action_cameraList_to_liveView).
 *   - MainActivity — host de navegación (pestaña inferior).
 *
 * PUNTO DE ENTRADA
 *   Destino de Navigation R.id.cameraListFragment (pestaña inferior).
 *
 * PIPELINE(S)
 *   #1 Inicio (listado de cámaras) · #3 Live (previews) · #5 go2rtc.
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch

/**
 * Fragment de rejilla de cámaras (2 por página) con vista previa en vivo.
 *
 * ROL Y RESPONSABILIDAD
 *   Vista "multi-cámara": reproduce hasta 2 feeds simultáneos (un ExoPlayer por
 *   slot, en silencio y calidad media), pagina sobre la lista de tiles y enruta
 *   al directo a pantalla completa del tile tocado.
 *
 * QUIÉN LA INSTANCIA / CONSUME
 *   La instancia el Navigation Component como destino R.id.cameraListFragment
 *   (pestaña inferior). Al tocar un tile navega a LiveViewFragment pasando
 *   "cameraId" y "lens".
 *
 * CICLO DE VIDA ANDROID RELEVANTE
 *   - onViewCreated: monta PlayerView+label por slot y cablea paginación/botones.
 *   - onPause / onDestroyView: stopAllStreams() libera ambos ExoPlayer (clave
 *     para no mantener conexiones a go2rtc en segundo plano).
 *   - onResume: re-renderiza la página actual (reanuda los streams).
 *
 * MIGRACIÓN
 *   Antes decodificaba el endpoint MJPEG `/cameras/{id}/stream` (eliminado del
 *   backend). Ahora cada slot usa ExoPlayer sobre el restream HLS/RTSP de go2rtc,
 *   igual que LiveViewFragment, sin transcode MJPEG ni 2ª conexión a la cámara.
 *
 * PIPELINE
 *   #1 Inicio · #3 Live · #5 go2rtc.
 */
class CameraListFragment : BaseMenuFragment() {

    // ── Estado ────────────────────────────────────────────────────────────────
    // Cada "tile" es un panel del grid. Una cámara dual-lens produce DOS tiles
    // (L1 y L2) → se ven ambos lentes a la vez como si fueran cámaras
    // independientes. Una cámara mono produce un solo tile.
    private data class Tile(
        val camera: CameraResponse,
        val lens: String?,        // null | "l1" | "l2"
        val url: String,          // HLS preferido
        val fallbackUrl: String?, // RTSP de respaldo
        val label: String,
    )
    private var tiles: List<Tile> = emptyList()
    private var pageIndex = 0                          // página actual (2 tiles por página)
    private val pageSize  = 2
    private val players = arrayOfNulls<ExoPlayer>(2)   // un ExoPlayer por slot

    /** Rejilla en calidad MEDIA por defecto (varias a la vez = lo más pesado).
     *  Añade el sufijo _medium al nombre del substream de go2rtc (cam_X[_lY] →
     *  cam_X[_lY]_medium); el src va al final de la URL (HLS y RTSP). */
    private fun toMedium(url: String?): String? =
        if (url.isNullOrBlank()) url else "${url}_medium"

    /**
     * Aplana las cámaras en tiles del grid: dual-lens válida → 2 (L1/L2);
     * cualquier otra → 1. Todas las URLs se fijan a calidad media (toMedium).
     *
     * @param cams cámaras de GET /cameras (CameraResponse).
     * @return lista de [Tile] que pagina renderPage().
     * Llamado por: loadCameras.
     */
    private fun buildTiles(cams: List<CameraResponse>): List<Tile> {
        val out = mutableListOf<Tile>()
        for (c in cams) {
            if (c.isDualLens && !c.streamUrlL1.isNullOrBlank() && !c.streamUrlL2.isNullOrBlank()) {
                out.add(Tile(c, "l1",
                             toMedium(c.hlsUrlL1?.takeIf { it.isNotBlank() } ?: c.streamUrlL1!!)!!,
                             toMedium(c.streamUrlL1), "${c.name} · L1"))
                out.add(Tile(c, "l2",
                             toMedium(c.hlsUrlL2?.takeIf { it.isNotBlank() } ?: c.streamUrlL2!!)!!,
                             toMedium(c.streamUrlL2), "${c.name} · L2"))
            } else {
                out.add(Tile(c, null, toMedium(c.liveHlsUrl ?: c.liveUrl)!!,
                             toMedium(c.liveUrl), c.name))
            }
        }
        return out
    }

    // ── Vistas ────────────────────────────────────────────────────────────────
    private lateinit var feedCam1:      FrameLayout
    private lateinit var feedCam2:      FrameLayout
    private lateinit var btnRemoveCam1: ImageButton
    private lateinit var btnRemoveCam2: ImageButton
    private lateinit var tvCamName1:    TextView
    private lateinit var tvCamName2:    TextView
    private lateinit var playerView1:   PlayerView
    private lateinit var playerView2:   PlayerView
    private lateinit var btnPrevPage:   ImageButton
    private lateinit var btnNextPage:   ImageButton
    private lateinit var tvPageInfo:    TextView

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_camera_list, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        // Obtener vistas del layout
        feedCam1      = view.findViewById(R.id.feedCam1)
        feedCam2      = view.findViewById(R.id.feedCam2)
        btnRemoveCam1 = view.findViewById(R.id.btnRemoveCam1)
        btnRemoveCam2 = view.findViewById(R.id.btnRemoveCam2)

        // Agregar PlayerView y TextView dinámicamente a cada feed
        playerView1 = addPlayerViewTo(feedCam1)
        playerView2 = addPlayerViewTo(feedCam2)
        tvCamName1  = addNameLabelTo(feedCam1)
        tvCamName2  = addNameLabelTo(feedCam2)

        // Botones de paginación
        btnPrevPage = view.findViewById(R.id.btnPrevPage)
        btnNextPage = view.findViewById(R.id.btnNextPage)
        tvPageInfo  = view.findViewById(R.id.tvPageInfo)

        // Navegación a liveView al tocar un feed
        feedCam1.setOnClickListener { navigateToLiveView(pageIndex * pageSize) }
        feedCam2.setOnClickListener { navigateToLiveView(pageIndex * pageSize + 1) }

        // Desconectar cámara (detiene el stream local del slot)
        btnRemoveCam1.setOnClickListener { stopSlot(0); hideSlot(feedCam1, tvCamName1) }
        btnRemoveCam2.setOnClickListener { stopSlot(1); hideSlot(feedCam2, tvCamName2) }

        // Paginación
        btnPrevPage.setOnClickListener {
            if (pageIndex > 0) { pageIndex--; renderPage() }
        }
        btnNextPage.setOnClickListener {
            val totalPages = ((tiles.size - 1) / pageSize) + 1
            if (pageIndex < totalPages - 1) { pageIndex++; renderPage() }
        }

        // Botón "ver en grande" → abre la vista En Vivo.
        view.findViewById<ImageButton>(R.id.btnVideoList).setOnClickListener {
            findNavController().navigate(R.id.action_cameraList_to_liveView)
        }

        // Cargar cámaras del servidor
        loadCameras()
    }

    override fun onPause() {
        super.onPause()
        stopAllStreams()
    }

    override fun onResume() {
        super.onResume()
        if (tiles.isNotEmpty()) renderPage()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        stopAllStreams()
    }

    // ── Red: cargar lista de cámaras ──────────────────────────────────────────

    /**
     * Pide las cámaras del usuario al backend y, si hay, construye los tiles y
     * renderiza la primera página. Endpoint: GET /cameras (ApiService.getCameras).
     * Mapea 401/403 a mensajes legibles.
     * Llamado por: onViewCreated. Llama a: buildTiles, renderPage.
     */
    private fun loadCameras() {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = api.getCameras()
                if (response.isSuccessful) {
                    val cams = response.body()?.data ?: emptyList()
                    tiles = buildTiles(cams)
                    if (tiles.isNotEmpty()) {
                        pageIndex = 0
                        renderPage()
                    } else {
                        Toast.makeText(requireContext(), "No hay cámaras registradas", Toast.LENGTH_LONG).show()
                    }
                } else {
                    val msg = when (response.code()) {
                        401  -> "Sesión expirada"
                        403  -> "Sin permisos para ver cámaras"
                        else -> "Error ${response.code()}"
                    }
                    Toast.makeText(requireContext(), msg, Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error de conexión: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    // ── Paginación: mostrar 2 cámaras de la página actual ─────────────────────

    private fun renderPage() {
        stopAllStreams()

        val totalPages = ((tiles.size - 1) / pageSize) + 1
        tvPageInfo.text = getString(R.string.page_format, pageIndex + 1, totalPages)
        btnPrevPage.isEnabled = pageIndex > 0
        btnNextPage.isEnabled = pageIndex < totalPages - 1

        val tile1 = tiles.getOrNull(pageIndex * pageSize)
        val tile2 = tiles.getOrNull(pageIndex * pageSize + 1)

        if (tile1 != null) {
            feedCam1.visibility = View.VISIBLE
            tvCamName1.text = tile1.label
            startRtspStream(tile1, slot = 0, playerView = playerView1)
        } else {
            feedCam1.visibility = View.INVISIBLE
        }

        if (tile2 != null) {
            feedCam2.visibility = View.VISIBLE
            tvCamName2.text = tile2.label
            startRtspStream(tile2, slot = 1, playerView = playerView2)
        } else {
            feedCam2.visibility = View.INVISIBLE
        }
    }

    // ── Stream (go2rtc) por slot: HLS preferido, RTSP de fallback ────────────

    /**
     * Reproduce un tile en un slot con ExoPlayer: HLS preferido y, si falla,
     * reintenta UNA vez con la URL RTSP de respaldo. El audio va a 0 (rejilla
     * silenciosa).
     *
     * @param tile feed a reproducir (aporta url HLS y fallbackUrl RTSP).
     * @param slot índice del reproductor (0 o 1) en el array players.
     * @param playerView PlayerView del slot donde se renderiza el vídeo.
     * Llamado por: renderPage.
     */
    private fun startRtspStream(tile: Tile, slot: Int, playerView: PlayerView) {
        players[slot]?.release()

        val url = tile.url
        val fallback = tile.fallbackUrl

        var triedFallback = false
        players[slot] = ExoPlayer.Builder(requireContext()).build().also { exo ->
            playerView.player = exo
            exo.addListener(object : Player.Listener {
                override fun onPlayerError(error: PlaybackException) {
                    if (!triedFallback && !fallback.isNullOrBlank() && fallback != url) {
                        triedFallback = true
                        exo.setMediaItem(MediaItem.fromUri(fallback))
                        exo.prepare()
                        exo.playWhenReady = true
                    }
                }
            })
            exo.setMediaItem(MediaItem.fromUri(url))
            exo.prepare()
            exo.playWhenReady = true
            exo.volume = 0f  // rejilla en silencio
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun stopSlot(slot: Int) {
        players[slot]?.release()
        players[slot] = null
    }

    private fun stopAllStreams() {
        for (i in players.indices) {
            players[i]?.release()
            players[i] = null
        }
    }

    private fun hideSlot(feed: FrameLayout, label: TextView) {
        label.text = ""
        feed.visibility = View.INVISIBLE
    }

    /**
     * Navega al directo a pantalla completa del tile indicado, pasando su
     * cameraId y el lens tocado (para que LiveView abra ESE feed y no siempre L1).
     *
     * @param tileIndex índice global del tile en la lista (no relativo a página).
     * Llamado por: los click listeners de feedCam1/feedCam2.
     */
    private fun navigateToLiveView(tileIndex: Int) {
        val tile = tiles.getOrNull(tileIndex) ?: return
        // Pasar el LENTE tocado para que LiveView abra ese feed (antes abría
        // siempre el L1 porque solo se enviaba el id de cámara).
        val bundle = Bundle().apply {
            putString("cameraId", tile.camera.id.toString())
            tile.lens?.let { putString("lens", it) }
        }
        findNavController().navigate(R.id.action_cameraList_to_liveView, bundle)
    }

    /** Añade un PlayerView de Media3 que ocupa todo el FrameLayout. */
    @OptIn(UnstableApi::class)
    private fun addPlayerViewTo(parent: FrameLayout): PlayerView {
        val pv = PlayerView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            useController = false
            resizeMode = androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_ZOOM
        }
        parent.addView(pv, 0)
        return pv
    }

    /** Añade un TextView con el nombre de la cámara en la parte inferior del feed. */
    private fun addNameLabelTo(parent: FrameLayout): TextView {
        val tv = TextView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ).also { it.gravity = android.view.Gravity.BOTTOM }
            setPadding(12, 4, 12, 4)
            setBackgroundColor(0xAA000000.toInt())
            setTextColor(0xFFFFFFFF.toInt())
            textSize = 12f
        }
        parent.addView(tv)
        return tv
    }
}
