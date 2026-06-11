/*
 * ============================================================================
 * MÓDULO: NotificationWebSocketService — foreground service del canal de
 *         notificaciones en LAN (Pipeline #13 Notificaciones, lado móvil)
 * ============================================================================
 *
 * PROPÓSITO
 *   Mantener VIVO en background el WebSocket /ws/notifications contra el backend
 *   NVR para recibir EventData en tiempo real (sin FCM/Internet) y convertir cada
 *   evento en una notificación push de Android. Es un Service de tipo foreground
 *   (con notificación persistente) porque sólo así el sistema garantiza que el
 *   proceso y su conexión sobreviven a Doze / app en segundo plano.
 *
 * RESPONSABILIDAD
 *   - Promocionarse a foreground con la notificación persistente "App escuchando".
 *   - Crear el cliente WebSocket (NotificationWsClient) con baseUrl + token y
 *     reconectar automáticamente (la lógica de reconexión vive en el cliente).
 *   - Por cada EventData recibido: (a) emitir un broadcast in-app para los
 *     fragments abiertos (NotificationsPanelFragment) y (b) postear una
 *     notificación de Android que, al tocarla, abre el playback del evento.
 *   - Reflejar el estado de conexión en el texto de la notificación persistente.
 *
 * DEPENDENCIAS
 *   - network/NotificationWsClient .. conexión WS + parseo de NotificationEvent.
 *   - network/RetrofitClient ........ baseUrl + access token de la sesión.
 *   - MainActivity .................. destino del PendingIntent (deep-link a la
 *                                     grabación vía extras openCameraId/openDate).
 *
 * COMPONENTES RELACIONADOS
 *   - Backend WSNotificationBroker (publica EventData por /ws/notifications).
 *   - NotificationsPanelFragment (escucha ACTION_EVENT_RECEIVED en vivo).
 *   - MainActivity.handleNotificationIntent (consume openCameraId/openDate).
 *
 * PUNTO DE ENTRADA
 *   Compañero NotificationWebSocketService.start(ctx) (tras login OK) y stop(ctx)
 *   (en logout / sesión expirada). Declarado en AndroidManifest con
 *   foregroundServiceType="dataSync".
 *
 * PIPELINE(S)
 *   #13 Notificaciones — etapa de transporte/entrega en el dispositivo: recibe
 *   del backend y materializa la notificación visible.
 * ============================================================================
 */
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
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
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
 * Rol en el sistema: es el único componente del cliente Android que escucha el
 * backend de forma continua (el resto de pantallas hacen REST puntuales). Por eso
 * corre como foreground service: sin él, Android suspendería el proceso y se
 * perderían las alertas con la app en background.
 *
 * Ciclo de vida Android:
 *   - onCreate(): crea canales + se promociona a foreground (OBLIGATORIO en <5s).
 *   - onStartCommand(): arranca el WS si aún no existe; devuelve START_STICKY para
 *     que el sistema lo reinicie si lo mata por memoria.
 *   - onDestroy(): cierra el WS limpiamente.
 *   Quién lo instancia: el propio sistema, vía start()/startForegroundService()
 *   llamado tras un login correcto; se detiene en logout o SESSION_EXPIRED.
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
 *
 * Pipeline: #13 Notificaciones (entrega en dispositivo).
 */
class NotificationWebSocketService : Service() {

    companion object {
        private const val TAG = "NotifWsService"

        // Notificación foreground persistente (la pegatina "App escuchando")
        private const val CHANNEL_ID_PERSISTENT = "camlink_ws_persistent"
        private const val NOTIF_ID_PERSISTENT = 1001

        // Notificaciones de eventos (las que ve el usuario)
        const val CHANNEL_ID_EVENTS = "camlink_events"

        // Patrón de vibración al recibir una alerta (espera, vibra, pausa, vibra).
        private val VIBRATION_PATTERN = longArrayOf(0, 250, 150, 250)

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

        /**
         * Arranca el servicio en modo foreground. Llamar tras un login correcto.
         * @param ctx contexto de aplicación (se usa para lanzar el Intent).
         * Llamado por: el flujo de post-login y MainActivity al reactivar sesión.
         */
        fun start(ctx: Context) {
            // minSdk=29 ≥ O (API 26), así que startForegroundService está
            // siempre disponible. No hace falta fallback a startService.
            ctx.startForegroundService(Intent(ctx, NotificationWebSocketService::class.java))
        }

        /**
         * Detiene el servicio (cierra el WS vía onDestroy). Llamar en logout o
         * cuando JwtAuthenticator emite SESSION_EXPIRED.
         * @param ctx contexto de aplicación.
         * Llamado por: MainActivity.sessionExpiredReceiver y el flujo de logout.
         */
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
    /**
     * Construye el NotificationWsClient con la baseUrl + token de la sesión y lo
     * arranca. Si no hay sesión (sin baseUrl/token) deja el servicio vivo pero
     * ocioso mostrando "Sin sesión" en la notificación persistente.
     * Llama a: RetrofitClient.buildBaseUrl/getAccessToken, NotificationWsClient.start.
     * Llamado por: onStartCommand (primer arranque del WS).
     */
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
    /**
     * Crea (idempotente) los dos NotificationChannel necesarios: el persistente
     * de baja importancia (foreground service) y el de eventos (importancia alta,
     * con vibración/luz). Llamado por: onCreate.
     */
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
                vibrationPattern = VIBRATION_PATTERN
                enableLights(true)
            }
            nm.createNotificationChannel(ch)
        }
    }

    /**
     * Construye la notificación persistente (ongoing) del foreground service.
     * @param text texto de estado a mostrar (p. ej. "Conectado al servidor NVR").
     * @return la Notification lista para startForeground/notify.
     * Llamado por: onCreate y updatePersistentNotification.
     */
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

    /**
     * Re-emite la notificación persistente con un nuevo texto de estado.
     * @param text nuevo texto (estado de conexión).
     * Llamado por: connectWs (sin sesión) y el callback onConnectionChange del WS.
     */
    private fun updatePersistentNotification(text: String) {
        val notif = buildPersistentNotification(text)
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID_PERSISTENT, notif)
    }

    /**
     * Procesa un EventData recibido por el WS: emite el broadcast in-app y, si hay
     * permiso, publica la notificación de Android cuyo PendingIntent abre el
     * playback del evento (extras openCameraId/openDate leídos por MainActivity).
     * @param ev evento ya parseado desde el JSON del backend.
     * Llamado por: el callback onEvent del NotificationWsClient.
     * Llama a: sendBroadcast (in-app), NotificationManagerCompat.notify.
     */
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

        // Al tocar la notificación se abre el TIMELINE de grabaciones de esa
        // cámara en la fecha del evento (openCameraId + openDate los lee
        // MainActivity.handleNotificationIntent).
        val eventDate = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
            .format(java.util.Date((ev.timestamp * 1000).toLong()))
        val pi = PendingIntent.getActivity(
            this, ev.cameraId,
            Intent(this, MainActivity::class.java).apply {
                putExtra("openCameraId", ev.cameraId)
                putExtra("openDate", eventDate)
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        // Vibración explícita: aunque el canal ya pide vibrar, algunos ROMs
        // (p.ej. MIUI) la ignoran o el canal se creó sin patrón en instalaciones
        // previas (los canales son inmutables). Disparar el Vibrator garantiza el
        // aviso háptico al recibir la notificación.
        vibrate()

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

    /** Dispara una vibración corta (patrón doble) al recibir una alerta. */
    private fun vibrate() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = getSystemService(VibratorManager::class.java)
                vm?.defaultVibrator?.vibrate(
                    VibrationEffect.createWaveform(VIBRATION_PATTERN, -1)
                )
            } else {
                @Suppress("DEPRECATION")
                val v = getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v?.vibrate(VibrationEffect.createWaveform(VIBRATION_PATTERN, -1))
                } else {
                    @Suppress("DEPRECATION")
                    v?.vibrate(VIBRATION_PATTERN, -1)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "No se pudo vibrar: ${e.message}")
        }
    }

    private fun formatTime(epoch: Double): String {
        val d = java.util.Date((epoch * 1000).toLong())
        val sdf = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
        return sdf.format(d)
    }
}
