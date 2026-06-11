/*
 * ============================================================================
 * MÓDULO: NotificationWsClient — cliente WebSocket del canal de notificaciones
 *         (Pipeline #13 Notificaciones, lado móvil)
 * ============================================================================
 *
 * PROPÓSITO
 *   Encapsular la conexión OkHttp WebSocket contra /ws/notifications: handshake
 *   con token en la query, mensajería ping/pong, reconexión con backoff
 *   exponencial y parseo del JSON de eventos a NotificationEvent. No tiene nada
 *   de Android UI: es código de red puro reutilizable.
 *
 * RESPONSABILIDAD
 *   - Abrir/cerrar el socket y mantener una única conexión viva.
 *   - Reconectar con backoff (1,2,4,8,16,30s) cuando la red o el servidor caen.
 *   - Responder "pong" a los pings del servidor (keep-alive a nivel de protocolo).
 *   - Despachar cada mensaje "event" como NotificationEvent vía callback onEvent.
 *   - Informar conexión/desconexión vía callback onConnectionChange.
 *
 * DEPENDENCIAS
 *   - OkHttp (WebSocket) y org.json (parseo).
 *   - network/RetrofitClient provee la baseUrl http(s) y el token (los pasa el
 *     llamador, este cliente sólo los recibe en el constructor).
 *
 * COMPONENTES RELACIONADOS
 *   - service/NotificationWebSocketService lo instancia y lo aloja en background.
 *   - Backend WSNotificationBroker._serialize_event produce el JSON que parsea
 *     NotificationEvent.fromJson.
 *
 * PUNTO DE ENTRADA
 *   Constructor + start()/stop(). Los callbacks se ejecutan en un hilo de OkHttp.
 *
 * PIPELINE(S)
 *   #13 Notificaciones — transporte (capa WebSocket entre backend y dispositivo).
 * ============================================================================
 */
package com.ipn.mx.onvif.network

import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Cliente WebSocket reutilizable contra `ws://host:port/ws/notifications?token=...`.
 *
 * Conserva una única conexión viva con reconexión exponencial cuando el servidor
 * cae o la red cambia. Está pensado para correr dentro de un foreground service
 * (ver NotificationWebSocketService) que mantenga el proceso en background.
 *
 * Mensajería:
 *   - server -> cliente : JSON con `type` en {hello, event, ping, pong, error}
 *   - cliente -> server : texto plano "ping" / "pong" (cuando el servidor pingea)
 *
 * Callbacks ([onEvent] / [onConnectionChange]) se invocan en un hilo de OkHttp;
 * el caller debe re-postear al main thread si toca UI o NotificationManager.
 *
 * Quién lo instancia: NotificationWebSocketService.connectWs() (un único cliente
 * por sesión, vivo mientras el foreground service exista).
 *
 * @property baseHttpUrl URL http(s) del backend ("http://192.168.1.10:5000"); se
 *           convierte a ws(s) internamente.
 * @property accessToken JWT de acceso que se envía como query param `token`.
 * @property onEvent callback por cada mensaje "event" (NotificationEvent).
 * @property onConnectionChange callback (conectado, mensajeError?) en cada cambio.
 */
class NotificationWsClient(
    private val baseHttpUrl: String,    // "http://192.168.1.10:5000"
    private val accessToken: String,
    private val onEvent: (NotificationEvent) -> Unit,
    private val onConnectionChange: (Boolean, String?) -> Unit,
) {

    companion object {
        private const val TAG = "NotificationWsClient"
        // Backoff: 1, 2, 4, 8, 16, 30 (cap). Ms.
        private val RECONNECT_DELAYS_MS = longArrayOf(1_000, 2_000, 4_000, 8_000, 16_000, 30_000)
    }

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // readTimeout = 0 (sin límite): el servidor mantiene la conexión
        // viva con pings cada ~25s. OkHttp cierra solo si la TCP cae.
        .readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)  // WS-level pings (frame ping/pong)
        .retryOnConnectionFailure(true)
        .build()

    @Volatile private var ws: WebSocket? = null
    private val shouldRun = AtomicBoolean(false)
    private val isConnected = AtomicBoolean(false)
    private var reconnectAttempt = 0
    private val reconnectThread = Object()

    /**
     * Arranca el loop de conexión en un hilo daemon dedicado ("NotifWS-Loop").
     * Idempotente: una segunda llamada mientras ya corre se ignora.
     * Llamado por: NotificationWebSocketService.connectWs.
     */
    fun start() {
        if (shouldRun.getAndSet(true)) {
            Log.d(TAG, "start() ignorado: ya está corriendo")
            return
        }
        Thread({ runLoop() }, "NotifWS-Loop").apply { isDaemon = true; start() }
    }

    /**
     * Detiene el loop, cierra el socket (código 1000 "client_stop") y despierta el
     * hilo de espera de backoff para que termine sin reintentar.
     * Llamado por: NotificationWebSocketService.onDestroy.
     */
    fun stop() {
        shouldRun.set(false)
        try { ws?.close(1000, "client_stop") } catch (_: Exception) {}
        ws = null
        synchronized(reconnectThread) { reconnectThread.notifyAll() }
    }

    /** @return true si el socket está actualmente conectado (handshake OK). */
    fun isAlive(): Boolean = isConnected.get()

    // ------------------------------------------------------------------
    // Loop con reconexión
    // ------------------------------------------------------------------
    /**
     * Bucle principal del hilo daemon: abre el socket (bloqueante hasta que muere),
     * y al volver espera el backoff correspondiente antes de reintentar, mientras
     * shouldRun siga activo. Llama a: openSocket. Llamado por: start (en su hilo).
     */
    private fun runLoop() {
        while (shouldRun.get()) {
            try {
                openSocket()
                // openSocket() devuelve cuando la conexión muere; esperar
                // backoff antes de reintentar.
                if (!shouldRun.get()) break
                val delay = RECONNECT_DELAYS_MS[
                    minOf(reconnectAttempt, RECONNECT_DELAYS_MS.size - 1)
                ]
                reconnectAttempt++
                Log.i(TAG, "Reintentando WS en ${delay}ms (intento $reconnectAttempt)")
                synchronized(reconnectThread) {
                    try { reconnectThread.wait(delay) } catch (_: InterruptedException) {}
                }
            } catch (e: Exception) {
                Log.e(TAG, "Excepción en loop WS: ${e.message}", e)
                try { Thread.sleep(2_000) } catch (_: InterruptedException) {}
            }
        }
    }

    /**
     * Abre el WebSocket (deriva la URL ws(s) y añade ?token=) y BLOQUEA en un
     * CountDownLatch hasta que la conexión muere (onClosed/onFailure), para que el
     * runLoop sepa cuándo aplicar el backoff. Llama a: handleTextMessage por cada
     * mensaje. Conecta a: /ws/notifications. Llamado por: runLoop.
     */
    private fun openSocket() {
        val wsUrl = baseHttpUrl
            .replaceFirst("http://", "ws://")
            .replaceFirst("https://", "wss://")
            .trimEnd('/') +
            "/ws/notifications?token=$accessToken"

        Log.i(TAG, "Conectando a $wsUrl")
        val req = Request.Builder().url(wsUrl).build()

        // Latch para esperar a que la conexión muera
        val deathLatch = java.util.concurrent.CountDownLatch(1)

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WS abierto (HTTP ${response.code})")
                isConnected.set(true)
                reconnectAttempt = 0
                onConnectionChange(true, null)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleTextMessage(webSocket, text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS cerrando code=$code reason=$reason")
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS cerrado code=$code reason=$reason")
                isConnected.set(false)
                onConnectionChange(false, "closed:$code")
                deathLatch.countDown()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val msg = t.message ?: t.javaClass.simpleName
                Log.w(TAG, "WS fallo: $msg")
                isConnected.set(false)
                onConnectionChange(false, msg)
                deathLatch.countDown()
            }
        }

        try {
            ws = httpClient.newWebSocket(req, listener)
            deathLatch.await()
        } finally {
            ws = null
        }
    }

    /**
     * Despacha un mensaje de texto del servidor según su campo `type`:
     * ping→responde "pong", hello→log, event→onEvent(NotificationEvent), error→log.
     * @param webSocket socket origen (para responder pong).
     * @param text payload JSON crudo. Llamado por: el WebSocketListener (onMessage).
     */
    private fun handleTextMessage(webSocket: WebSocket, text: String) {
        try {
            val json = JSONObject(text)
            when (val type = json.optString("type")) {
                "ping" -> {
                    // El servidor sondea; respondemos para mantenernos vivos.
                    webSocket.send("pong")
                }
                "pong" -> {
                    // Respuesta a un ping nuestro; nada que hacer.
                }
                "hello" -> {
                    Log.i(TAG, "hello recibido: user_id=${json.opt("user_id")}")
                }
                "event" -> {
                    onEvent(NotificationEvent.fromJson(json))
                }
                "error" -> {
                    Log.e(TAG, "WS error del servidor: $text")
                }
                else -> {
                    Log.d(TAG, "WS mensaje desconocido type=$type")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error parseando WS message: ${e.message}", e)
        }
    }
}

/**
 * Modelo de notificación recibido por WS desde el backend.
 * Coincide con el JSON producido por WSNotificationBroker._serialize_event.
 *
 * Es el DTO de transporte del Pipeline #13: lo construye [fromJson] y lo consumen
 * NotificationWebSocketService (push) y, vía broadcast, NotificationsPanelFragment.
 */
data class NotificationEvent(
    val eventType: String,
    val cameraId: Int,
    val cameraName: String,
    val timestamp: Double,
    val confidence: Double,
    val metadataJson: String,
) {
    companion object {
        /**
         * Construye un NotificationEvent desde el JSON del backend, con defaults
         * seguros para cada campo ausente.
         * @param j objeto JSON del mensaje "event".
         * @return el evento parseado. Llamado por: NotificationWsClient.handleTextMessage.
         */
        fun fromJson(j: JSONObject): NotificationEvent = NotificationEvent(
            eventType = j.optString("event_type", "unknown"),
            cameraId = j.optInt("camera_id", -1),
            cameraName = j.optString("camera_name", ""),
            timestamp = j.optDouble("timestamp", 0.0),
            confidence = j.optDouble("confidence", 0.0),
            metadataJson = j.optJSONObject("metadata")?.toString() ?: "{}",
        )
    }

    /**
     * Título legible para la notificación de Android según el tipo de evento.
     * @return texto traducido (p. ej. "Persona detectada"); para tipos no mapeados
     *         capitaliza el eventType. Llamado por: NotificationWebSocketService.handleEvent.
     */
    fun displayTitle(): String = when (eventType) {
        "person" -> "Persona detectada"
        "vehicle" -> "Vehículo detectado"
        "motion" -> "Movimiento detectado"
        "camera_offline" -> "Cámara desconectada"
        "camera_reconnected" -> "Cámara reconectada"
        "tampering" -> "Posible manipulación"
        else -> eventType.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }

    /**
     * Cuerpo legible: nombre de cámara (o "Cámara N") más el porcentaje de
     * confianza si lo hay.
     * @return texto para el cuerpo de la notificación. Llamado por:
     *         NotificationWebSocketService.handleEvent.
     */
    fun displayBody(): String {
        val cam = if (cameraName.isNotBlank()) cameraName else "Cámara $cameraId"
        val conf = if (confidence > 0) " (${(confidence * 100).toInt()}%)" else ""
        return "$cam$conf"
    }
}
