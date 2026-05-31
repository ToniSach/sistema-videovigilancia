package com.ipn.mx.onvif.model

import com.google.gson.annotations.SerializedName

// ── Auth ──────────────────────────────────────────────────────────────────────

data class LoginRequest(
    val username: String,
    val password: String
)

data class LoginResponse(
    @SerializedName("access_token")  val accessToken: String,
    @SerializedName("refresh_token") val refreshToken: String
)

/**
 * Respuesta de POST /api/v1/auth/refresh.
 * El servidor devuelve SOLO access_token (no refresca el refresh_token).
 */
data class RefreshResponse(
    @SerializedName("access_token") val accessToken: String,
)

// ── Registro de dispositivo móvil (QR scan) ──────────────────────────────────

/**
 * Body de POST /api/v1/devices/register.
 * El [linkToken] viene del QR generado por el desktop (POST /qr/generate).
 */
data class DeviceRegisterRequest(
    @SerializedName("link_token")  val linkToken: String,
    @SerializedName("device_uuid") val deviceUuid: String,
    @SerializedName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerializedName("fcm_token")   val fcmToken: String? = null,
)

data class DeviceRegisterUser(
    val id: Int,
    val username: String,
    val role: String,
)

data class DeviceRegisterPayload(
    @SerializedName("device_id")     val deviceId: Int,
    @SerializedName("access_token")  val accessToken: String,
    @SerializedName("refresh_token") val refreshToken: String,
    @SerializedName("server_url")    val serverUrl: String? = null,
    val user: DeviceRegisterUser,
)

/** Wrapper { success, data: { ... } } del backend. */
data class DeviceRegisterResponse(
    val success: Boolean,
    val data: DeviceRegisterPayload? = null,
    val error: String? = null,
)

// ── Historial de notificaciones / eventos ────────────────────────────────────

/**
 * Un evento del historial. Coincide con Event.to_dict() del backend + el campo
 * extra `thumbnail_url` que añade /mobile/notifications/history cuando hay
 * snapshot. `created_at` viene como ISO-8601 ("2026-05-28T14:30:00") en UTC.
 */
data class NotificationItem(
    val id: Int,
    @SerializedName("camera_id")    val cameraId: Int,
    @SerializedName("event_type")   val eventType: String,
    val confidence: Float = 0f,
    @SerializedName("snapshot_path") val snapshotPath: String? = null,
    @SerializedName("clip_path")     val clipPath: String? = null,
    val acknowledged: Boolean = false,
    @SerializedName("created_at")   val createdAt: String,
    @SerializedName("thumbnail_url") val thumbnailUrl: String? = null,
)

data class NotificationsHistoryPagination(
    @SerializedName("has_more")   val hasMore: Boolean = false,
    @SerializedName("next_cursor") val nextCursor: Double? = null,
    val count: Int = 0,
)

data class NotificationsHistoryResponse(
    val success: Boolean,
    val data: List<NotificationItem> = emptyList(),
    val pagination: NotificationsHistoryPagination? = null,
)

// ── Preferencias de notificación (CRUD) ──────────────────────────────────────

data class NotificationPreferenceDto(
    val id: Int,
    @SerializedName("event_type") val eventType: String,
    @SerializedName("camera_id")  val cameraId: Int? = null,
    val enabled: Boolean = true,
    val channels: List<String> = emptyList(),     // "push" | "telegram"
    val days: List<Int> = emptyList(),            // 0..6
)

data class NotificationPreferencesResponse(
    val success: Boolean,
    val data: List<NotificationPreferenceDto> = emptyList(),
)

/** Body para POST/PUT /notifications/preferences. Todos los campos opcionales en PUT. */
data class PreferenceMutation(
    @SerializedName("event_type") val eventType: String? = null,
    @SerializedName("camera_id")  val cameraId: Int? = null,
    val enabled: Boolean? = null,
    val channels: List<String>? = null,
    @SerializedName("days_of_week") val daysOfWeek: List<Int>? = null,
)

data class PreferenceMutationPayload(val id: Int)
data class PreferenceMutationResponse(
    val success: Boolean,
    val data: PreferenceMutationPayload? = null,
)

// ── Cámaras ───────────────────────────────────────────────────────────────────

data class CameraResponse(
    val id: Int,
    val name: String,
    @SerializedName("ip_address")       val ipAddress: String,
    @SerializedName("rtsp_url")         val rtspUrl: String,
    @SerializedName("has_ptz")          val hasPtz: Boolean = false,
    @SerializedName("has_audio")        val hasAudio: Boolean = false,
    @SerializedName("connection_type")  val connectionType: String = "rtsp",
    @SerializedName("resolution_width") val resolutionWidth: Int = 1920,
    @SerializedName("resolution_height")val resolutionHeight: Int = 1080,
    val fps: Int = 12
)

data class PtzRequest(
    val x: Float,       // -1.0 a 1.0  (joystick horizontal)
    val y: Float,       // -1.0 a 1.0  (joystick vertical)
    val zoom: Float = 0f
)

// ── Wrappers de respuesta del servidor ────────────────────────────────────────
// El servidor Flask envuelve todas las respuestas en { "success": true, "data": ... }

data class CameraListResponse(
    val success: Boolean,
    val data: List<CameraResponse>
)

data class RecordingListResponse(
    val success: Boolean,
    val data: List<RecordingResponse>
)

data class RecordingDetailResponse(
    val success: Boolean,
    val data: RecordingResponse
)

// ── Grabaciones ───────────────────────────────────────────────────────────────

data class RecordingResponse(
    val id: String,
    @SerializedName("camera_id")         val cameraId: Int,
    val filename: String,
    @SerializedName("started_at")        val startedAt: String,
    @SerializedName("duration_seconds")  val durationSeconds: Int = 0,
    @SerializedName("has_alert")         val hasAlert: Boolean = false,
    @SerializedName("is_favorite")       val isFavorite: Boolean = false,
    @SerializedName("file_url")          val fileUrl: String? = null
)
