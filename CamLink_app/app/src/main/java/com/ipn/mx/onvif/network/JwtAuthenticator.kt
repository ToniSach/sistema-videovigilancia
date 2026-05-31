package com.ipn.mx.onvif.network

import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.gson.Gson
import com.ipn.mx.onvif.model.RefreshResponse
import okhttp3.Authenticator
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.Route
import java.util.concurrent.TimeUnit

/**
 * Authenticator de OkHttp que automatiza el refresh del JWT.
 *
 * Flujo:
 *  1. Cualquier petición autenticada que devuelve 401 entra aquí.
 *  2. Si ya intentamos refrescar para esta misma petición, devolvemos null
 *     (rendirse: el servidor seguirá devolviendo 401 y el caller lo verá).
 *  3. Si el access_token actual ya cambió desde que se mandó la petición
 *     (otro thread lo refrescó), reintentamos con el token nuevo SIN refrescar.
 *  4. En caso contrario, llamamos POST /auth/refresh con el refresh_token,
 *     guardamos el nuevo access_token y reintentamos con él.
 *  5. Si el refresh falla (401), emitimos broadcast LOCAL "session_expired"
 *     para que la app navegue al QrScanFragment y devolvemos null.
 *
 * Reentrada segura: usa un lock por instancia + comparación del token actual
 * vs el que iba en la request fallida, para que múltiples requests en vuelo
 * que reciban 401 simultáneamente solo disparen UN refresh.
 *
 * IMPORTANTE: este Authenticator NO debe usarse en el `OkHttpClient` que
 * llama a /auth/refresh (loop infinito). Usamos un client dedicado SIN
 * Authenticator para esa llamada (ver [refreshHttpClient]).
 */
class JwtAuthenticator(
    private val appContext: Context,
    private val baseApiUrl: String,   // "http://192.168.1.10:5000/api/v1/"
) : Authenticator {

    companion object {
        private const val TAG = "JwtAuth"
        const val ACTION_SESSION_EXPIRED = "com.ipn.mx.onvif.SESSION_EXPIRED"
        // Header que marca un request que YA pasó por el authenticator
        // (para no entrar en bucle si el reintento también devuelve 401).
        private const val HDR_RETRY = "X-Jwt-Retry"
    }

    private val refreshLock = Any()

    // Cliente plano SIN Authenticator para la llamada de refresh.
    // Crea uno solo (vive durante toda la app).
    private val refreshHttpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val gson = Gson()

    override fun authenticate(route: Route?, response: Response): Request? {
        // Cortar el bucle: si ya marcamos un reintento, no volver a intentar.
        if (response.request.header(HDR_RETRY) != null) {
            Log.d(TAG, "401 también tras reintento; rendirse")
            return null
        }

        // Token con el que vino la request fallida (puede ser stale).
        val staleToken = extractBearer(response.request.header("Authorization"))

        synchronized(refreshLock) {
            val current = RetrofitClient.accessToken
            // Otro thread ya refrescó: usar el nuevo token sin pedir refresh.
            if (current != null && current != staleToken) {
                Log.d(TAG, "Token ya refrescado por otro thread; reintentando con el nuevo")
                return response.request.newBuilder()
                    .header("Authorization", "Bearer $current")
                    .header(HDR_RETRY, "1")
                    .build()
            }

            val newAccess = tryRefresh()
            if (newAccess == null) {
                Log.w(TAG, "Refresh falló; emitiendo SESSION_EXPIRED y abandonando")
                emitSessionExpired()
                return null
            }

            return response.request.newBuilder()
                .header("Authorization", "Bearer $newAccess")
                .header(HDR_RETRY, "1")
                .build()
        }
    }

    /**
     * Llama POST /auth/refresh con el refresh_token. Devuelve el nuevo access
     * o null si falló (en cuyo caso la sesión está perdida).
     */
    private fun tryRefresh(): String? {
        val prefs = appContext.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        val refreshToken = prefs.getString("jwt_refresh_token", null)
        if (refreshToken.isNullOrBlank()) {
            Log.w(TAG, "No hay refresh_token guardado; imposible refrescar")
            return null
        }

        val url = baseApiUrl.trimEnd('/') + "/auth/refresh"
        // Body vacío JSON ("{}") porque flask-jwt-extended @jwt_required(refresh=True)
        // no exige body, pero algunos backends rechazan POST sin Content-Type.
        val body = "{}".toRequestBody("application/json".toMediaTypeOrNull())
        val req = Request.Builder()
            .url(url)
            .post(body)
            .header("Authorization", "Bearer $refreshToken")
            .build()

        return try {
            refreshHttpClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    Log.w(TAG, "Refresh HTTP ${resp.code}")
                    return null
                }
                val raw = resp.body?.string() ?: return null
                val parsed = gson.fromJson(raw, RefreshResponse::class.java)
                val newAccess = parsed?.accessToken
                if (newAccess.isNullOrBlank()) {
                    Log.w(TAG, "Refresh OK pero sin access_token en respuesta")
                    return null
                }
                // Persistir nuevo access_token en memoria y disco.
                RetrofitClient.accessToken = newAccess
                appContext.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
                    .edit()
                    .putString("jwt_access_token", newAccess)
                    .apply()
                Log.i(TAG, "Refresh OK — access_token renovado")
                newAccess
            }
        } catch (e: Exception) {
            Log.e(TAG, "Excepción durante refresh: ${e.message}", e)
            null
        }
    }

    private fun extractBearer(header: String?): String? {
        if (header.isNullOrBlank()) return null
        val parts = header.split(" ", limit = 2)
        return if (parts.size == 2 && parts[0].equals("Bearer", ignoreCase = true)) parts[1]
               else null
    }

    private fun emitSessionExpired() {
        try {
            val intent = Intent(ACTION_SESSION_EXPIRED).setPackage(appContext.packageName)
            appContext.sendBroadcast(intent)
        } catch (e: Exception) {
            Log.w(TAG, "No se pudo emitir SESSION_EXPIRED: ${e.message}")
        }
    }
}
