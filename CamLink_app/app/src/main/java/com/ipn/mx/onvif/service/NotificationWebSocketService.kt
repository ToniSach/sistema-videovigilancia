package com.ipn.mx.onvif.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import com.ipn.mx.onvif.MainActivity
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.NotificationEvent
import com.ipn.mx.onvif.network.NotificationWsClient
import com.ipn.mx.onvif.network.RetrofitClient
import java.util.concurrent.atomic.AtomicInteger

/**
 * Foreground service que mantiene viva la conexión WebSocket de notificaciones
 * con el servidor NVR. Reconecta automáticamente y muestra una notificación
 * por cada evento recibido.
 *
 * Uso:
 *   NotificationWebSocketService.start(context)   // tras login OK
 *   NotificationWebSocketService.stop(context)    // en logout
 *
 * Permisos requeridos (manifest):
 *   - FOREGROUND_SERVICE
 *   - FOREGROUND_SERVICE_DATA_SYNC  (API 34+)
 *   - POST_NOTIFICATIONS            (API 33+, runtime)
 *   - INTERNET
 */
class NotificationWebSocketService : Service() {

    companion object {
        private const val TAG = "NotifWsService"

        // Notificación foreground persistente (la pegatina "App escuchando")
        private const val CHANNEL_ID_PERSISTENT = "camlink_ws_persistent"
        private const val NOTIF_ID_PERSISTENT = 1001

        // Notificaciones de eventos (las que ve el usuario)
        const val CHANNEL_ID_EVENTS = "camlink_events"

        // Broadcast in-app: cualquier fragment (NotificationsPanel) que esté
        // abierto puede escuchar eventos en vivo sin pasar por el WS.
        const val ACTION_EVENT_RECEIVED = "com.ipn.mx.onvif.EVENT_RECEIVED"
        const val EXTRA_EVENT_TYPE      = "event_type"
        const val EXTRA_CAMERA_ID       = "camera_id"
        const val EXTRA_CAMERA_NAME     = "camera_name"
        const val EXTRA_TIMESTAMP       = "timestamp"
        const val EXTRA_CONFIDENCE      = "confidence"

        // Generador incremental para que cada evento sea una notificación distinta
        private val eventNotifId = AtomicInteger(2000)

        fun start(ctx: Context) {
            // minSdk=29 ≥ O (API 26), así que startForegroundService está
            // siempre disponible. No hace falta fallback a startService.
            ctx.startForegroundService(Intent(ctx, NotificationWebSocketService::class.java))
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, NotificationWebSocketService::class.java))
        }
    }

    private var wsClient: NotificationWsClient? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        try {
            ensureChannels()
            // En Android 14+ (API 34) un foreground service con foregroundServiceType
            // declarado en el manifest DEBE invocar startForeground con el bit del
            // tipo concreto, o el sistema lanza MissingForegroundServiceTypeException
            // y mata el proceso (== crash visible al usuario). ServiceCompat hace
            // el routing API-correcto según versión.
            val notif = buildPersistentNotification("Conectando…")
            val typeFlag = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                // dataSync = sincronización de datos en red. Coincide con el
                // foregroundServiceType="dataSync" del AndroidManifest.
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            } else {
                0
            }
            ServiceCompat.startForeground(this, NOTIF_ID_PERSISTENT, notif, typeFlag)
            Log.i(TAG, "Servicio creado y en foreground (typeFlag=$typeFlag)")
        } catch (e: Exception) {
            // Si por alguna razón startForeground falla (permisos perdidos,
            // foreground denegado, etc.) preferimos detener limpiamente el
            // servicio en vez de dejar que el sistema mate el proceso entero.
            Log.e(TAG, "Fallo al iniciar foreground; deteniendo servicio: ${e.message}", e)
            try { stopSelf() } catch (_: Exception) {}
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (wsClient == null) {
            connectWs()
        }
        // START_STICKY: el sistema reinicia el servicio si lo mata por memoria
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy — cerrando WS")
        wsClient?.stop()
        wsClient = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------
    // Conexión WS
    // ------------------------------------------------------------------
    private fun connectWs() {
        val baseUrl = RetrofitClient.buildBaseUrl(this)
        val token = RetrofitClient.getAccessToken(this)

        if (baseUrl == null || token.isNullOrBlank()) {
            Log.w(TAG, "Sin baseUrl/token — no puedo conectar WS, paro servicio")
            updatePersistentNotification("Sin sesión — inicia sesión en la app")
            // El servicio se queda vivo pero "ocioso" hasta que el usuario haga login
            // y otra llamada a start() lo reactive. NO llamamos stopSelf() para que
            // el sistema no nos mate inmediatamente; el usuario verá la notif.
            return
        }

        wsClient = NotificationWsClient(
            baseHttpUrl = baseUrl,
            accessToken = token,
            onEvent = { ev -> handleEvent(ev) },
            onConnectionChange = { connected, error ->
                val msg = if (connected) "Conectado al servidor NVR"
                          else "Reconectando… ${error ?: ""}".trim()
                updatePersistentNotification(msg)
            },
        ).also { it.start() }
    }

    // ------------------------------------------------------------------
    // Notificaciones
    // ------------------------------------------------------------------
    private fun ensureChannels() {
        // minSdk=29 ≥ O (API 26); los canales de notificación están siempre
        // disponibles, no hace falta el guard de SDK_INT.
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

        // Canal de la notificación persistente (la del foreground service)
        if (nm.getNotificationChannel(CHANNEL_ID_PERSISTENT) == null) {
            val ch = NotificationChannel(
                CHANNEL_ID_PERSISTENT,
                "Conexión NVR",
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                description = "Mantiene la app conectada al servidor para recibir alertas"
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }

        // Canal de eventos (notificaciones que vibran y suenan)
        if (nm.getNotificationChannel(CHANNEL_ID_EVENTS) == null) {
            val ch = NotificationChannel(
                CHANNEL_ID_EVENTS,
                "Alertas de cámara",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Detecciones de personas, vehículos, movimiento y desconexiones"
                enableVibration(true)
                enableLights(true)
            }
            nm.createNotificationChannel(ch)
        }
    }

    private fun buildPersistentNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID_PERSISTENT)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)  // placeholder — sustituir por ic_launcher
            .setContentTitle("CamLink")
            .setContentText(text)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(pi)
            .build()
    }

    private fun updatePersistentNotification(text: String) {
        val notif = buildPersistentNotification(text)
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID_PERSISTENT, notif)
    }

    private fun handleEvent(ev: NotificationEvent) {
        Log.i(TAG, "Evento recibido: ${ev.eventType} cam=${ev.cameraId}")

        // Broadcast in-app para fragments abiertos (panel de notif en vivo).
        // setPackage para que sólo lo reciba esta app (no requiere exported).
        try {
            val intent = Intent(ACTION_EVENT_RECEIVED).setPackage(packageName).apply {
                putExtra(EXTRA_EVENT_TYPE,   ev.eventType)
                putExtra(EXTRA_CAMERA_ID,    ev.cameraId)
                putExtra(EXTRA_CAMERA_NAME,  ev.cameraName)
                putExtra(EXTRA_TIMESTAMP,    ev.timestamp)
                putExtra(EXTRA_CONFIDENCE,   ev.confidence)
            }
            sendBroadcast(intent)
        } catch (e: Exception) {
            Log.w(TAG, "No pude emitir broadcast in-app: ${e.message}")
        }

        // Android 13+: requiere POST_NOTIFICATIONS aceptado en runtime
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                Log.w(TAG, "POST_NOTIFICATIONS no concedido; no se muestra notif")
                return
            }
        }

        val pi = PendingIntent.getActivity(
            this, ev.cameraId,
            Intent(this, MainActivity::class.java).apply {
                putExtra("openCameraId", ev.cameraId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notif = NotificationCompat.Builder(this, CHANNEL_ID_EVENTS)
            .setSmallIcon(android.R.drawable.stat_notify_more)
            .setContentTitle(ev.displayTitle())
            .setContentText(ev.displayBody())
            .setStyle(NotificationCompat.BigTextStyle().bigText(
                "${ev.displayBody()} • ${formatTime(ev.timestamp)}"
            ))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        try {
            NotificationManagerCompat.from(this)
                .notify(eventNotifId.incrementAndGet(), notif)
        } catch (se: SecurityException) {
            Log.w(TAG, "No se pudo mostrar notif: ${se.message}")
        }
    }

    private fun formatTime(epoch: Double): String {
        val d = java.util.Date((epoch * 1000).toLong())
        val sdf = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
        return sdf.format(d)
    }
}
