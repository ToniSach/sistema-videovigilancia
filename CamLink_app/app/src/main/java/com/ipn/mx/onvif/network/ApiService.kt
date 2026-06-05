package com.ipn.mx.onvif.network

import com.ipn.mx.onvif.model.*
import retrofit2.Response
import retrofit2.http.*

interface ApiService {

    // ── Auth ──────────────────────────────────────────────────────────────────

    @POST("auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    /**
     * Refresca el access_token usando el refresh_token.
     * El header Authorization lo añade [JwtAuthenticator] explícitamente con
     * el refresh_token (no el access_token caducado).
     */
    @POST("auth/refresh")
    suspend fun refresh(): RefreshResponse

    @POST("auth/logout")
    suspend fun logout(): Response<Unit>

    // ── Registro de dispositivo móvil vía QR ─────────────────────────────────
    // No requiere JWT (el link_token sirve como prueba de autorización del
    // desktop que lo generó). Devuelve access_token + refresh_token reales.

    @POST("devices/register")
    suspend fun registerDevice(
        @Body body: DeviceRegisterRequest,
    ): Response<DeviceRegisterResponse>

    // ── Cámaras ───────────────────────────────────────────────────────────────

    @GET("cameras/")
    suspend fun getCameras(): Response<CameraListResponse>

    @GET("cameras/{id}")
    suspend fun getCamera(@Path("id") id: Int): CameraResponse

    // PTZ por dirección (ONVIF ContinuousMove). El backend expone
    // /ptz/<direction> (up|down|left|right|stop); NO existe /ptz con body.
    @POST("cameras/{id}/ptz/{direction}")
    suspend fun ptzMove(
        @Path("id") id: Int,
        @Path("direction") direction: String,
    ): Response<Unit>

    // ── Audio bidireccional (talk-back) ──────────────────────────────────────
    // El backend valida permiso "control_audio". Si la cámara no soporta audio,
    // devuelve 400/500 con error claro — el cliente lo mostrará como Toast.

    @POST("cameras/{id}/audio/talk")
    suspend fun audioTalkStart(@Path("id") id: Int): Response<Unit>

    @POST("cameras/{id}/audio/stop")
    suspend fun audioTalkStop(@Path("id") id: Int): Response<Unit>

    // ── LEDs / IR-Cut (modo noche) ────────────────────────────────────────────
    // state ∈ {on, off, auto}. "on" = IR ENCENDIDO (modo noche),
    // "off" = IR-Cut activo (modo día), "auto" = decide la cámara.

    @POST("cameras/{id}/leds/{state}")
    suspend fun setLedState(
        @Path("id") id: Int,
        @Path("state") state: String,
    ): Response<Unit>

    // ── Grabación manual ──────────────────────────────────────────────────────

    @POST("recordings/manual/start/{id}")
    suspend fun startManualRecording(@Path("id") id: Int): Response<Unit>

    @POST("recordings/manual/stop/{id}")
    suspend fun stopManualRecording(@Path("id") id: Int): Response<Unit>

    @GET("recordings/manual/status/{id}")
    suspend fun manualRecordingStatus(@Path("id") id: Int): Response<okhttp3.ResponseBody>

    // ── Grabaciones (lista/playback) ──────────────────────────────────────────

    @GET("recordings/")
    suspend fun getRecordings(): RecordingListResponse

    @GET("recordings/{id}")
    suspend fun getRecording(@Path("id") id: String): RecordingDetailResponse

    /** Timeline de grabaciones de una cámara en una fecha (YYYY-MM-DD). */
    @GET("recordings/timeline")
    suspend fun getTimeline(
        @Query("camera_id") cameraId: Int,
        @Query("date") date: String,
    ): Response<TimelineResponse>

    @DELETE("recordings/{id}")
    suspend fun deleteRecording(@Path("id") id: String): Response<Unit>

    // ── Notificaciones: historial e interacciones ────────────────────────────

    /**
     * Historial paginado. `cursor` es el timestamp (Double, epoch s) del último
     * evento de la página anterior; null para la primera página.
     */
    @GET("mobile/notifications/history")
    suspend fun getNotificationsHistory(
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: Double? = null,
    ): Response<NotificationsHistoryResponse>

    /** Marca un evento como acknowledged (lectura desde el móvil). */
    @PATCH("events/{id}/acknowledge")
    suspend fun acknowledgeEvent(@Path("id") id: Int): Response<Unit>

    // ── Estado de la IA (para gatear la personalización de notificaciones) ────
    @GET("ai/status")
    suspend fun getAiStatus(): Response<AiStatusResponse>

    // ── Preferencias de notificación (CRUD) ──────────────────────────────────

    @GET("notifications/preferences")
    suspend fun getNotificationPreferences(): Response<NotificationPreferencesResponse>

    @POST("notifications/preferences")
    suspend fun createNotificationPreference(
        @Body body: PreferenceMutation,
    ): Response<PreferenceMutationResponse>

    @PUT("notifications/preferences/{id}")
    suspend fun updateNotificationPreference(
        @Path("id") id: Int,
        @Body body: PreferenceMutation,
    ): Response<PreferenceMutationResponse>

    @DELETE("notifications/preferences/{id}")
    suspend fun deleteNotificationPreference(
        @Path("id") id: Int,
    ): Response<Unit>

    // ── Vinculación de Telegram ───────────────────────────────────────────────

    /** Genera un código de vinculación + deep link para el usuario autenticado. */
    @POST("telegram/generate-code")
    suspend fun generateTelegramCode(): Response<TelegramCodeResponse>

    /** Polling: indica si el código ya fue consumido por el bot (linked=true). */
    @GET("telegram/link-status")
    suspend fun telegramLinkStatus(
        @Query("code") code: String,
    ): Response<TelegramLinkStatusResponse>
}
