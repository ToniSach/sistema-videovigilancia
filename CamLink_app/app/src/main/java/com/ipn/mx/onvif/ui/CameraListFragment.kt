package com.ipn.mx.onvif.ui

import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

class CameraListFragment : BaseMenuFragment() {

    // ── Estado ────────────────────────────────────────────────────────────────
    private var cameras: List<CameraResponse> = emptyList()
    private var pageIndex = 0                          // página actual (2 cámaras por página)
    private val pageSize  = 2
    private val streamJobs = mutableMapOf<Int, Job>()  // slot (0 o 1) → corrutina MJPEG

    // ── Vistas ────────────────────────────────────────────────────────────────
    private lateinit var feedCam1:      FrameLayout
    private lateinit var feedCam2:      FrameLayout
    private lateinit var btnRemoveCam1: ImageButton
    private lateinit var btnRemoveCam2: ImageButton
    private lateinit var tvCamName1:    TextView
    private lateinit var tvCamName2:    TextView
    private lateinit var imgFeed1:      ImageView
    private lateinit var imgFeed2:      ImageView
    private lateinit var btnPrevPage:   ImageButton
    private lateinit var btnNextPage:   ImageButton
    private lateinit var tvPageInfo:    TextView

    // ── OkHttp dedicado para MJPEG (sin timeout de lectura) ──────────────────
    private val mjpegClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0,  TimeUnit.SECONDS)   // streaming infinito
        .build()

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

        // Agregar ImageView y TextView dinámicamente a cada feed
        imgFeed1   = addImageViewTo(feedCam1)
        imgFeed2   = addImageViewTo(feedCam2)
        tvCamName1 = addNameLabelTo(feedCam1)
        tvCamName2 = addNameLabelTo(feedCam2)

        // Botones de paginación — se inflan dinámicamente si no están en el XML
        btnPrevPage = view.findViewById(R.id.btnPrevPage)
        btnNextPage = view.findViewById(R.id.btnNextPage)
        tvPageInfo  = view.findViewById(R.id.tvPageInfo)

        // Navegación a liveView al tocar un feed
        feedCam1.setOnClickListener { navigateToLiveView(pageIndex * pageSize) }
        feedCam2.setOnClickListener { navigateToLiveView(pageIndex * pageSize + 1) }

        // Desconectar cámara (por ahora detiene el stream local)
        btnRemoveCam1.setOnClickListener { stopSlot(0); hideSlot(feedCam1, imgFeed1, tvCamName1) }
        btnRemoveCam2.setOnClickListener { stopSlot(1); hideSlot(feedCam2, imgFeed2, tvCamName2) }

        // Paginación
        btnPrevPage.setOnClickListener {
            if (pageIndex > 0) { pageIndex--; renderPage() }
        }
        btnNextPage.setOnClickListener {
            val totalPages = ((cameras.size - 1) / pageSize) + 1
            if (pageIndex < totalPages - 1) { pageIndex++; renderPage() }
        }

        // Botones heredados del layout original
        view.findViewById<ImageButton>(R.id.btnVideoList).setOnClickListener {
            findNavController().navigate(R.id.action_cameraList_to_liveView)
        }
        view.findViewById<ImageButton>(R.id.btnFullscreen).setOnClickListener {
            Toast.makeText(requireContext(), "Pantalla completa: pendiente", Toast.LENGTH_SHORT).show()
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
        if (cameras.isNotEmpty()) renderPage()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        stopAllStreams()
    }

    // ── Red: cargar lista de cámaras ──────────────────────────────────────────

    private fun loadCameras() {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = api.getCameras()
                if (response.isSuccessful) {
                    cameras = response.body()?.data ?: emptyList()
                    if (cameras.isNotEmpty()) {
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

        val totalPages = ((cameras.size - 1) / pageSize) + 1
        // getString con placeholders en vez de concatenar texto: pasa lint
        // SetTextI18n y permite traducciones futuras.
        tvPageInfo.text = getString(R.string.page_format, pageIndex + 1, totalPages)
        btnPrevPage.isEnabled = pageIndex > 0
        btnNextPage.isEnabled = pageIndex < totalPages - 1

        val cam1 = cameras.getOrNull(pageIndex * pageSize)
        val cam2 = cameras.getOrNull(pageIndex * pageSize + 1)

        if (cam1 != null) {
            feedCam1.visibility = View.VISIBLE
            tvCamName1.text = cam1.name
            startMjpegStream(cam1, slot = 0, imageView = imgFeed1)
        } else {
            feedCam1.visibility = View.INVISIBLE
        }

        if (cam2 != null) {
            feedCam2.visibility = View.VISIBLE
            tvCamName2.text = cam2.name
            startMjpegStream(cam2, slot = 1, imageView = imgFeed2)
        } else {
            feedCam2.visibility = View.INVISIBLE
        }
    }

    // ── MJPEG: decodificar stream multipart/x-mixed-replace ──────────────────

    private fun startMjpegStream(camera: CameraResponse, slot: Int, imageView: ImageView) {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val token   = RetrofitClient.getAccessToken(requireContext()) ?: return

        // El endpoint requiere el token como query param (no en header)
        val streamUrl = "${baseUrl}/api/v1/cameras/${camera.id}/stream?token=${token}"

        val job = viewLifecycleOwner.lifecycleScope.launch(Dispatchers.IO) {
            try {
                val request  = Request.Builder().url(streamUrl).build()
                val response = mjpegClient.newCall(request).execute()
                val body     = response.body ?: return@launch

                val inputStream = body.byteStream()
                val buffer      = ByteArrayOutputStream()
                val byteArray   = ByteArray(4096)
                var prevByte    = -1

                while (isActive) {
                    val byte = inputStream.read()
                    if (byte == -1) break

                    buffer.write(byte)

                    // Detectar fin de frame JPEG: 0xFF 0xD9
                    if (prevByte == 0xFF && byte == 0xD9) {
                        val frameBytes = buffer.toByteArray()
                        buffer.reset()

                        // Buscar inicio del JPEG (0xFF 0xD8) dentro del buffer acumulado
                        val jpegStart = findJpegStart(frameBytes)
                        if (jpegStart >= 0) {
                            val jpegBytes = frameBytes.copyOfRange(jpegStart, frameBytes.size)
                            val bitmap    = BitmapFactory.decodeByteArray(jpegBytes, 0, jpegBytes.size)
                            if (bitmap != null) {
                                withContext(Dispatchers.Main) {
                                    if (isActive) imageView.setImageBitmap(bitmap)
                                }
                            }
                        }
                    }
                    prevByte = byte
                }
            } catch (_: Exception) {
                // Stream interrumpido al cambiar página o salir — es esperado
            }
        }

        streamJobs[slot]?.cancel()
        streamJobs[slot] = job
    }

    /** Encuentra el índice del marcador de inicio JPEG (0xFF 0xD8) en el array. */
    private fun findJpegStart(bytes: ByteArray): Int {
        for (i in 0 until bytes.size - 1) {
            if (bytes[i] == 0xFF.toByte() && bytes[i + 1] == 0xD8.toByte()) return i
        }
        return -1
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun stopSlot(slot: Int) {
        streamJobs[slot]?.cancel()
        streamJobs.remove(slot)
    }

    private fun stopAllStreams() {
        streamJobs.values.forEach { it.cancel() }
        streamJobs.clear()
    }

    private fun hideSlot(feed: FrameLayout, img: ImageView, label: TextView) {
        img.setImageBitmap(null)
        label.text = ""
        feed.visibility = View.INVISIBLE
    }

    private fun navigateToLiveView(cameraIndex: Int) {
        val camera = cameras.getOrNull(cameraIndex) ?: return
        val bundle = Bundle().apply { putString("cameraId", camera.id.toString()) }
        findNavController().navigate(R.id.action_cameraList_to_liveView, bundle)
    }

    /** Añade un ImageView que ocupa todo el FrameLayout. */
    private fun addImageViewTo(parent: FrameLayout): ImageView {
        val img = ImageView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            scaleType = ImageView.ScaleType.CENTER_CROP
        }
        parent.addView(img, 0)
        return img
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
