package com.ipn.mx.onvif.ui

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.DeviceRegisterRequest
import com.ipn.mx.onvif.model.LoginRequest
import com.ipn.mx.onvif.network.RetrofitClient
import com.ipn.mx.onvif.service.NotificationWebSocketService
import com.ipn.mx.onvif.util.DeviceIdentity
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Pantalla de vinculación inicial.
 *
 * Modos:
 *   1) Auto-login: si ya hay `serverIp` + `jwt_access_token` en prefs,
 *      navega directamente al LiveView.
 *   2) Escaneo QR: CameraX + ML Kit. El QR contiene
 *      `{ "server": "http://ip:port", "link_token": "uuid", "version": "1.0" }`.
 *      Al detectarlo, POST /api/v1/devices/register devuelve tokens reales
 *      → se guardan en prefs → navega a LiveView.
 *   3) Conexión manual: diálogo con IP/puerto/usuario/contraseña.
 *
 * Manejo de permiso CAMERA en runtime. Si el usuario lo rechaza, queda
 * disponible el botón de conexión manual.
 */
class QrScanFragment : Fragment() {

    companion object {
        private const val TAG = "QrScanFragment"
    }

    private lateinit var previewView: PreviewView
    private lateinit var tvStatus: TextView
    private lateinit var tvCameraDisabled: TextView
    private lateinit var btnGrantCamera: Button
    private lateinit var btnManualConnect: Button

    // Executor dedicado a frames de CameraX. Se cierra en onDestroyView.
    private val cameraExecutor = Executors.newSingleThreadExecutor()

    // BarcodeScanner reutilizable; solo formato QR para minimizar coste.
    private val barcodeScanner by lazy {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .build()
        )
    }

    // Evita que múltiples frames disparen el mismo registro en paralelo
    // (ML Kit puede leer el QR 5+ veces en 200 ms).
    private val isHandlingQr = AtomicBoolean(false)

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            tvCameraDisabled.visibility = View.GONE
            btnGrantCamera.visibility = View.GONE
            tvStatus.setText(R.string.qr_status_waiting)
            startCamera()
        } else {
            showCameraDeniedUi()
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_qr_scan, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        previewView      = view.findViewById(R.id.previewView)
        tvStatus         = view.findViewById(R.id.tvStatus)
        tvCameraDisabled = view.findViewById(R.id.tvCameraDisabled)
        btnGrantCamera   = view.findViewById(R.id.btnGrantCamera)
        btnManualConnect = view.findViewById(R.id.btnManualConnect)

        // Auto-login: si ya hay sesión guardada, saltarse el escaneo
        val prefs = requireContext().getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        val savedIp    = prefs.getString("serverIp", null)
        val savedToken = prefs.getString("jwt_access_token", null)
        if (savedIp != null && savedToken != null) {
            navigateToLiveView()
            return
        }

        btnGrantCamera.setOnClickListener {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
        btnManualConnect.setOnClickListener { showManualConnectDialog() }

        // Iniciar cámara si ya tenemos permiso; si no, pedirlo
        if (hasCameraPermission()) {
            startCamera()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onDestroyView() {
        try { cameraExecutor.shutdown() } catch (_: Exception) {}
        try { barcodeScanner.close() } catch (_: Exception) {}
        super.onDestroyView()
    }

    // ── Permisos ──────────────────────────────────────────────────────────────

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED

    private fun showCameraDeniedUi() {
        tvCameraDisabled.visibility = View.VISIBLE
        btnGrantCamera.visibility = View.VISIBLE
        tvStatus.setText(R.string.qr_camera_disabled)
    }

    // ── CameraX ───────────────────────────────────────────────────────────────

    @SuppressLint("UnsafeOptInUsageError")
    private fun startCamera() {
        val ctx = requireContext()
        val providerFuture = ProcessCameraProvider.getInstance(ctx)
        providerFuture.addListener({
            try {
                val cameraProvider = providerFuture.get()

                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { it.setAnalyzer(cameraExecutor, ::analyzeFrame) }

                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(
                    viewLifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            } catch (e: Exception) {
                Log.e(TAG, "Error iniciando CameraX: ${e.message}", e)
                Toast.makeText(ctx, "No se pudo iniciar la camara: ${e.message}",
                    Toast.LENGTH_LONG).show()
            }
        }, ContextCompat.getMainExecutor(ctx))
    }

    // ExperimentalGetImage es una anotación de CameraX en Java (no Kotlin),
    // así que NO usamos kotlin.OptIn (no tendría efecto) sino la versión
    // de AndroidX que sí entiende anotaciones Java marcadas con @RequiresOptIn.
    @androidx.annotation.OptIn(ExperimentalGetImage::class)
    private fun analyzeFrame(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image
        if (mediaImage == null) {
            imageProxy.close()
            return
        }
        // Si ya estamos procesando un QR detectado, descartar este frame sin
        // mandarlo a ML Kit (ahorra CPU y evita doble registro).
        if (isHandlingQr.get()) {
            imageProxy.close()
            return
        }
        val rotation = imageProxy.imageInfo.rotationDegrees
        val input = InputImage.fromMediaImage(mediaImage, rotation)
        barcodeScanner.process(input)
            .addOnSuccessListener { barcodes ->
                val raw = barcodes.firstOrNull { it.rawValue != null }?.rawValue
                if (raw != null && isHandlingQr.compareAndSet(false, true)) {
                    onQrDetected(raw)
                }
            }
            .addOnFailureListener { e ->
                Log.d(TAG, "ML Kit error: ${e.message}")
            }
            .addOnCompleteListener {
                imageProxy.close()
            }
    }

    // ── Procesado del QR ─────────────────────────────────────────────────────

    private fun onQrDetected(raw: String) {
        Log.i(TAG, "QR detectado (len=${raw.length})")
        // Parsear JSON esperado: { server, link_token, version }
        val parsed = parseQrPayload(raw)
        if (parsed == null) {
            tvStatus.setText(R.string.qr_status_invalid)
            // Permitir reintento al cabo de 1.5s (suficiente para que el usuario
            // mueva el QR si era falso positivo).
            view?.postDelayed({ isHandlingQr.set(false) }, 1_500)
            return
        }
        tvStatus.setText(R.string.qr_status_registering)
        registerDevice(parsed.serverUrl, parsed.linkToken)
    }

    private data class QrPayload(val serverUrl: String, val linkToken: String)

    private fun parseQrPayload(raw: String): QrPayload? {
        // El servidor genera JSON; aceptamos también un fallback "ip:port:token"
        // (legacy) por si llegara desde otra fuente.
        return try {
            val json = JSONObject(raw)
            val server = json.optString("server").trim()
            val token = json.optString("link_token").trim()
            if (server.isBlank() || token.isBlank()) null
            else QrPayload(serverUrl = server, linkToken = token)
        } catch (_: Exception) {
            // Fallback "host:port:token"
            val parts = raw.trim().split(":")
            if (parts.size >= 3) {
                val host = parts[0]
                val port = parts[1]
                val token = parts.drop(2).joinToString(":")
                if (host.isBlank() || port.isBlank() || token.isBlank()) null
                else QrPayload(serverUrl = "http://$host:$port", linkToken = token)
            } else null
        }
    }

    private fun registerDevice(serverUrl: String, linkToken: String) {
        // Persistir IP/puerto extraídos del QR ANTES de crear el Retrofit,
        // porque buildBaseUrl(context) los lee desde prefs.
        val (host, port) = extractHostPort(serverUrl) ?: run {
            tvStatus.setText(R.string.qr_status_invalid)
            isHandlingQr.set(false)
            return
        }
        val prefs = requireContext().getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("serverIp", host)
            .putString("serverPort", port)
            .apply()

        val api = RetrofitClient.create("http://$host:$port", requireContext())
        val req = DeviceRegisterRequest(
            linkToken  = linkToken,
            deviceUuid = DeviceIdentity.getOrCreateUuid(requireContext()),
            deviceName = DeviceIdentity.friendlyDeviceName(),
            platform   = "android",
            fcmToken   = null,
        )

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.registerDevice(req)
                if (resp.isSuccessful) {
                    val data = resp.body()?.data
                    if (data == null) {
                        tvStatus.setText(R.string.qr_status_invalid)
                        isHandlingQr.set(false)
                        return@launch
                    }
                    // Guardar tokens y navegar
                    RetrofitClient.saveToken(
                        requireContext(),
                        data.accessToken,
                        data.refreshToken,
                    )
                    // Guardar info de usuario opcional para uso futuro (panel)
                    prefs.edit()
                        .putString("serverUser", data.user.username)
                        .putString("user_role", data.user.role)
                        .putInt("device_id", data.deviceId)
                        .apply()
                    navigateToLiveView()
                } else {
                    val msg = when (resp.code()) {
                        401 -> R.string.qr_status_expired
                        400 -> R.string.qr_status_invalid
                        else -> R.string.qr_status_no_network
                    }
                    tvStatus.setText(msg)
                    view?.postDelayed({ isHandlingQr.set(false) }, 2_000)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error registrando dispositivo: ${e.message}", e)
                tvStatus.setText(R.string.qr_status_no_network)
                view?.postDelayed({ isHandlingQr.set(false) }, 2_000)
            }
        }
    }

    /**
     * "http://192.168.1.10:5000" → ("192.168.1.10", "5000"). Devuelve null si la
     * URL es inválida o no trae puerto explícito (necesario para Flask en LAN).
     */
    private fun extractHostPort(url: String): Pair<String, String>? = try {
        val u = URI(url)
        val host = u.host ?: return null
        val port = if (u.port > 0) u.port.toString() else "5000"
        host to port
    } catch (_: Exception) {
        null
    }

    // ── Diálogo de conexión manual (fallback) ─────────────────────────────────

    private fun showManualConnectDialog() {
        val dialogView = LayoutInflater.from(requireContext())
            .inflate(R.layout.dialog_manual_connect, null, false)
        val etIp       = dialogView.findViewById<EditText>(R.id.etIp)
        val etPort     = dialogView.findViewById<EditText>(R.id.etPort)
        val etUser     = dialogView.findViewById<EditText>(R.id.etUser)
        val etPassword = dialogView.findViewById<EditText>(R.id.etPassword)

        AlertDialog.Builder(requireContext())
            .setTitle(R.string.dialog_title_manual)
            .setView(dialogView)
            .setPositiveButton(R.string.btn_connect) { _, _ ->
                val ip       = etIp.text.toString().trim()
                val port     = etPort.text.toString().trim().ifEmpty { "5000" }
                val user     = etUser.text.toString().trim()
                val password = etPassword.text.toString().trim()
                if (ip.isEmpty()) {
                    Toast.makeText(requireContext(),
                        R.string.error_ip_port_required, Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                connectAndLogin(ip, port, user, password)
            }
            .setNegativeButton(R.string.btn_cancel, null)
            .show()
    }

    private fun connectAndLogin(ip: String, port: String, user: String, password: String) {
        val prefs = requireContext().getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("serverIp", ip)
            .putString("serverPort", port)
            .putString("serverUser", user)
            .putString("serverPassword", password)
            .apply()

        val baseUrl = "http://$ip:$port"
        val api = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = api.login(LoginRequest(user, password))
                RetrofitClient.saveToken(requireContext(),
                    response.accessToken, response.refreshToken)
                navigateToLiveView()
            } catch (e: retrofit2.HttpException) {
                val msg = when (e.code()) {
                    401  -> "Usuario o contrasena incorrectos"
                    404  -> "Servidor no encontrado en $ip:$port"
                    else -> "Error del servidor: ${e.code()}"
                }
                Toast.makeText(requireContext(), msg, Toast.LENGTH_LONG).show()
            } catch (e: java.net.ConnectException) {
                Toast.makeText(requireContext(),
                    "No se pudo conectar a $ip:$port — verifica que el servidor este activo",
                    Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    // ── Navegación ────────────────────────────────────────────────────────────

    private fun navigateToLiveView() {
        try {
            NotificationWebSocketService.start(requireContext().applicationContext)
        } catch (e: Exception) {
            Log.w(TAG, "No se pudo iniciar WS service: ${e.message}")
        }
        findNavController().navigate(R.id.action_qr_to_liveView)
    }
}
