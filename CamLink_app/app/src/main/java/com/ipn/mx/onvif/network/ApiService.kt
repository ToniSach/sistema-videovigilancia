/*
 * ============================================================================
 * MÓDULO: network/ApiService — contrato REST del cliente Android (Retrofit)
 * ============================================================================
 *
 * PROPÓSITO
 *   Interfaz Retrofit que declara TODOS los endpoints REST que el cliente móvil
 *   consume del backend Flask. Cada método es una corrutina `suspend` que mapea
 *   1:1 a una ruta bajo `/api/v1/` (la base la fija RetrofitClient). Retrofit
 *   genera la implementación en tiempo de ejecución; aquí solo vive el contrato.
 *
 * RESPONSABILIDAD
 *   Definir verbo HTTP (@GET/@POST/@PUT/@PATCH/@DELETE), ruta, parámetros
 *   (@Path/@Query/@Body) y tipo de respuesta (data class de model/ApiModels que
 *   Gson deserializa). NO contiene lógica: la autenticación, el refresh y los
 *   reintentos los aplican los interceptores y el Authenticator de OkHttp.
 *
 * DEPENDENCIAS
 *   - model/ApiModels ......... DTOs de request/response (Gson).
 *   - network/RetrofitClient .. construye la instancia y fija baseUrl + headers.
 *   - network/JwtAuthenticator  refresca el JWT ante 401 (transparente aquí).
 *
 * COMPONENTES RELACIONADOS
 *   Consumido por los *Fragment / *ViewModel y servicios de la app vía la
 *   instancia que devuelve RetrofitClient.create(...).
 *
 * PUNTO DE ENTRADA
 *   `RetrofitClient.create(baseUrl, context).create(ApiService::class.java)`.
 *
 * PIPELINES (etapa: capa de transporte HTTP, alimenta a casi todos)
 *   #2 Auth ........... login / refresh / logout / devices/register.
 *   #7 ONVIF / #8 PTZ . cameras + ptz/<dir> + leds + audio talk-back.
 *   #9 IA ............. ai/status (gatea personalización de notificaciones).
 *   #10 Eventos ....... mobile/notifications/history + events/ack.
 *   #11 Grabación ..... recordings/manual/… (grabación manual).
 *   #12 Clips / #14 Reproducción .. recordings/ + timeline + playback firmado.
 *   #13 Notificaciones  notifications/preferences + telegram/….
 * ============================================================================
 */
package com.ipn.mx.onvif.network

import com.ipn.mx.onvif.model.*
import retrofit2.Response
import retrofit2.http.*

/**
 * Contrato Retrofit con todos los endpoints REST del backend NVR/VMS.
 *
 * Rol: capa de transporte HTTP de la app. Cada función `suspend` ejecuta una
 * petición a `/api/v1/<ruta>` y devuelve el DTO ya deserializado (o un
 * `Response<...>` cuando el caller necesita inspeccionar el código de estado).
 *
 * Quién la instancia: [RetrofitClient.create]. Quién la consume: fragments,
 * view-models y servicios de la app.
 */
interface ApiService {

    // ── Auth (Pipeline #2) ──────────────────────────────────────────────────────

    /**
     * Inicia sesión con usuario/contraseña. Backend: `POST /api/v1/auth/login`.
     *
     * @param body credenciales (username + password).
     * @return [LoginResponse] con access_token + refresh_token.
     * Llamado por: el flujo de login manual (alternativo al QR).
     */
    @POST("auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    /**
     * Refresca el access_token usando el refresh_token. Backend:
     * `POST /api/v1/auth/refresh`. El header Authorization lo añade
     * [JwtAuthenticator] explícitamente con el refresh_token (no el
     * access_token caducado).
     *
     * @return [RefreshResponse] con el nuevo access_token.
     * Llamado por: [JwtAuthenticator.tryRefresh] de forma transparente ante 401.
     */
    @POST("auth/refresh")
    suspend fun refresh(): RefreshResponse

    /**
     * Cierra la sesión / revoca el token en el servidor. Backend:
     * `POST /api/v1/auth/logout`.
     *
     * @return `Response<Unit>` (solo interesa el código de estado).
     */
    @POST("auth/logout")
    suspend fun logout(): Response<Unit>

    // ── Registro de dispositivo móvil vía QR (Pipeline #2) ────────────────────
    // No requiere JWT (el link_token sirve como prueba de autorización del
    // desktop que lo generó). Devuelve access_token + refresh_token reales.

    /**
     * Canjea el link_token del QR por una sesión real. Backend:
     * `POST /api/v1/devices/register`. Registra el dispositivo en
     * `mobile_devices` y devuelve los tokens JWT del usuario que generó el QR.
     *
     * @param body link_token (del QR) + device_uuid + device_name + platform.
     * @return [DeviceRegisterResponse] con access_token, refresh_token y user.
     * Llamado por: QrScanFragment tras leer el QR del desktop.
     */
    @POST("devices/register")
    suspend fun registerDevice(
        @Body body: DeviceRegisterRequest,
    ): Response<DeviceRegisterResponse>

    // ── Cámaras (Pipelines #3 Live / #7 ONVIF) ────────────────────────────────

    /**
     * Lista las cámaras accesibles para el usuario (propias + compartidas).
     * Backend: `GET /api/v1/cameras/`.
     *
     * @return [CameraListResponse] (wrapper { success, data: [CameraResponse] }).
     * Llamado por: la pantalla de cámaras / live para poblar la rejilla.
     */
    @GET("cameras/")
    suspend fun getCameras(): Response<CameraListResponse>

    /**
     * Detalle de una cámara concreta (incluye URLs de stream/HLS/WebRTC).
     * Backend: `GET /api/v1/cameras/{id}`.
     *
     * @param id identificador de la cámara.
     * @return [CameraResponse] con todas las URLs de directo.
     */
    @GET("cameras/{id}")
    suspend fun getCamera(@Path("id") id: Int): CameraResponse

    /**
     * Mueve la cámara PTZ en una dirección (ONVIF ContinuousMove). Backend:
     * `POST /api/v1/cameras/{id}/ptz/{direction}`. El backend expone
     * /ptz/<direction> (up|down|left|right|stop); NO existe /ptz con body.
     *
     * @param id cámara objetivo.
     * @param direction una de: up | down | left | right | stop.
     * @return `Response<Unit>` (solo código de estado).
     * Llamado por: los controles de joystick PTZ del live (Pipeline #8 PTZ).
     */
    @POST("cameras/{id}/ptz/{direction}")
    suspend fun ptzMove(
        @Path("id") id: Int,
        @Path("direction") direction: String,
    ): Response<Unit>

    // ── Audio bidireccional (talk-back) (Pipeline #3 Live) ────────────────────
    // El backend valida permiso "control_audio". Si la cámara no soporta audio,
    // devuelve 400/500 con error claro — el cliente lo mostrará como Toast.

    /**
     * Inicia el talk-back (envío de audio del móvil a la cámara). Backend:
     * `POST /api/v1/cameras/{id}/audio/talk`.
     *
     * @param id cámara objetivo (debe soportar audio + permiso control_audio).
     * @return `Response<Unit>`; 400/500 si la cámara no admite audio.
     */
    @POST("cameras/{id}/audio/talk")
    suspend fun audioTalkStart(@Path("id") id: Int): Response<Unit>

    /**
     * Detiene el talk-back iniciado con [audioTalkStart]. Backend:
     * `POST /api/v1/cameras/{id}/audio/stop`.
     *
     * @param id cámara objetivo.
     * @return `Response<Unit>`.
     */
    @POST("cameras/{id}/audio/stop")
    suspend fun audioTalkStop(@Path("id") id: Int): Response<Unit>

    // ── LEDs / IR-Cut (modo noche) (Pipeline #7 ONVIF) ────────────────────────
    // state ∈ {on, off, auto}. "on" = IR ENCENDIDO (modo noche),
    // "off" = IR-Cut activo (modo día), "auto" = decide la cámara.

    /**
     * Ajusta los LEDs IR / IR-Cut (modo día/noche). Backend:
     * `POST /api/v1/cameras/{id}/leds/{state}`.
     *
     * @param id cámara objetivo.
     * @param state on (IR encendido, noche) | off (IR-Cut, día) | auto.
     * @return `Response<Unit>`.
     */
    @POST("cameras/{id}/leds/{state}")
    suspend fun setLedState(
        @Path("id") id: Int,
        @Path("state") state: String,
    ): Response<Unit>

    // ── Grabación manual (Pipeline #11 Grabación) ─────────────────────────────

    /**
     * Inicia una grabación manual de la cámara. Backend:
     * `POST /api/v1/recordings/manual/start/{id}`.
     *
     * @param id cámara objetivo.
     * @return `Response<Unit>`.
     * Llamado por: el botón de grabación manual del live.
     */
    @POST("recordings/manual/start/{id}")
    suspend fun startManualRecording(@Path("id") id: Int): Response<Unit>

    /**
     * Detiene la grabación manual en curso. Backend:
     * `POST /api/v1/recordings/manual/stop/{id}`.
     *
     * @param id cámara objetivo.
     * @return `Response<Unit>`.
     */
    @POST("recordings/manual/stop/{id}")
    suspend fun stopManualRecording(@Path("id") id: Int): Response<Unit>

    /**
     * Consulta si hay una grabación manual activa para la cámara. Backend:
     * `GET /api/v1/recordings/manual/status/{id}`.
     *
     * @param id cámara objetivo.
     * @return cuerpo JSON crudo (`ResponseBody`); el caller lo parsea a mano.
     * Llamado por: el live para mostrar el indicador de grabación activa.
     */
    @GET("recordings/manual/status/{id}")
    suspend fun manualRecordingStatus(@Path("id") id: Int): Response<okhttp3.ResponseBody>

    // ── Grabaciones: lista/playback (Pipelines #12 Clips / #14 Reproducción) ──

    /**
     * Lista de grabaciones. Backend: `GET /api/v1/recordings/`.
     * Sin parámetros = últimas de las cámaras accesibles. Con `cameraId` +
     * `date` (YYYY-MM-DD) = todas las del día de esa cámara, con `playback_url`
     * firmada y `type` (event|continuous) — la pantalla de Grabaciones lo usa
     * para las pestañas Por lente / Eventos y para reproducir en cadena
     * (timeline).
     *
     * @param cameraId cámara a filtrar (null = todas las accesibles).
     * @param date día YYYY-MM-DD (null = recientes).
     * @param limit tope de resultados (por defecto 200).
     * @return [RecordingListResponse].
     */
    @GET("recordings/")
    suspend fun getRecordings(
        @Query("camera_id") cameraId: Int? = null,
        @Query("date") date: String? = null,
        @Query("limit") limit: Int = 200,
    ): RecordingListResponse

    /**
     * Detalle de una grabación concreta. Backend:
     * `GET /api/v1/recordings/{id}`.
     *
     * @param id identificador de la grabación.
     * @return [RecordingDetailResponse] con la URL de playback firmada.
     */
    @GET("recordings/{id}")
    suspend fun getRecording(@Path("id") id: String): RecordingDetailResponse

    /**
     * Timeline de grabaciones de una cámara en una fecha. Backend:
     * `GET /api/v1/recordings/timeline`.
     *
     * @param cameraId cámara objetivo.
     * @param date día YYYY-MM-DD.
     * @return [TimelineResponse] con los segmentos del día.
     * Llamado por: la vista de timeline de grabaciones.
     */
    @GET("recordings/timeline")
    suspend fun getTimeline(
        @Query("camera_id") cameraId: Int,
        @Query("date") date: String,
    ): Response<TimelineResponse>

    /**
     * Elimina una grabación. Backend: `DELETE /api/v1/recordings/{id}`.
     *
     * @param id identificador de la grabación.
     * @return `Response<Unit>`.
     */
    @DELETE("recordings/{id}")
    suspend fun deleteRecording(@Path("id") id: String): Response<Unit>

    // ── Notificaciones: historial e interacciones (Pipeline #10 Eventos) ──────

    /**
     * Historial paginado de eventos/notificaciones. Backend:
     * `GET /api/v1/mobile/notifications/history`. `cursor` es el timestamp
     * (Double, epoch s) del último evento de la página anterior; null para la
     * primera página (paginación por cursor descendente).
     *
     * @param limit eventos por página (por defecto 20).
     * @param cursor epoch-s del último evento de la página previa (null = inicio).
     * @return [NotificationsHistoryResponse] con la lista y la paginación.
     * Llamado por: la pantalla de Notificaciones (scroll infinito).
     */
    @GET("mobile/notifications/history")
    suspend fun getNotificationsHistory(
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: Double? = null,
    ): Response<NotificationsHistoryResponse>

    /**
     * Marca un evento como acknowledged (lectura desde el móvil). Backend:
     * `PATCH /api/v1/events/{id}/acknowledge`.
     *
     * @param id identificador del evento.
     * @return `Response<Unit>`.
     */
    @PATCH("events/{id}/acknowledge")
    suspend fun acknowledgeEvent(@Path("id") id: Int): Response<Unit>

    // ── Estado de la IA (Pipeline #9 IA) ──────────────────────────────────────

    /**
     * Estado de la IA: qué cámara/lente tiene YOLO activo. Backend:
     * `GET /api/v1/ai/status`. Las notificaciones por IA SOLO existen para la
     * cámara con IA activa; la app lo consulta para gatear la personalización
     * de notificaciones.
     *
     * @return [AiStatusResponse] con las cámaras IA activas.
     */
    @GET("ai/status")
    suspend fun getAiStatus(): Response<AiStatusResponse>

    // ── Preferencias de notificación: CRUD (Pipeline #13 Notificaciones) ──────

    /**
     * Lista las reglas de preferencia de notificación del usuario. Backend:
     * `GET /api/v1/notifications/preferences`.
     *
     * @return [NotificationPreferencesResponse] con las reglas existentes.
     */
    @GET("notifications/preferences")
    suspend fun getNotificationPreferences(): Response<NotificationPreferencesResponse>

    /**
     * Crea una nueva regla de preferencia. Backend:
     * `POST /api/v1/notifications/preferences`.
     *
     * @param body regla a crear (tipo de evento, canales, horario, días…).
     * @return [PreferenceMutationResponse] con el id creado.
     */
    @POST("notifications/preferences")
    suspend fun createNotificationPreference(
        @Body body: PreferenceMutation,
    ): Response<PreferenceMutationResponse>

    /**
     * Actualiza una regla existente. Backend:
     * `PUT /api/v1/notifications/preferences/{id}`.
     *
     * @param id regla a modificar.
     * @param body campos a actualizar (todos opcionales en PUT).
     * @return [PreferenceMutationResponse].
     */
    @PUT("notifications/preferences/{id}")
    suspend fun updateNotificationPreference(
        @Path("id") id: Int,
        @Body body: PreferenceMutation,
    ): Response<PreferenceMutationResponse>

    /**
     * Elimina una regla de preferencia. Backend:
     * `DELETE /api/v1/notifications/preferences/{id}`.
     *
     * @param id regla a eliminar.
     * @return `Response<Unit>`.
     */
    @DELETE("notifications/preferences/{id}")
    suspend fun deleteNotificationPreference(
        @Path("id") id: Int,
    ): Response<Unit>

    // ── Vinculación de Telegram (Pipeline #13 Notificaciones) ─────────────────

    /**
     * Genera un código de vinculación + deep link para el usuario autenticado.
     * Backend: `POST /api/v1/telegram/generate-code`.
     *
     * @return [TelegramCodeResponse] con código, deep link y caducidad.
     * Llamado por: la pantalla de vinculación de Telegram.
     */
    @POST("telegram/generate-code")
    suspend fun generateTelegramCode(): Response<TelegramCodeResponse>

    /**
     * Polling: indica si el código ya fue consumido por el bot (linked=true).
     * Backend: `GET /api/v1/telegram/link-status`.
     *
     * @param code código devuelto por [generateTelegramCode].
     * @return [TelegramLinkStatusResponse] con linked / expired.
     * Llamado por: el sondeo periódico de la pantalla de Telegram.
     */
    @GET("telegram/link-status")
    suspend fun telegramLinkStatus(
        @Query("code") code: String,
    ): Response<TelegramLinkStatusResponse>
}
