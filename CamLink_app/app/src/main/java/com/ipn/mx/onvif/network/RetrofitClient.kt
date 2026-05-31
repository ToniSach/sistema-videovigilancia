package com.ipn.mx.onvif.network

import android.content.Context
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object RetrofitClient {

    // Token JWT en memoria — se carga desde SharedPreferences al crear el cliente
    var accessToken: String? = null

    /**
     * Crea (o recrea) el cliente apuntando a [baseUrl].
     * Llamar cada vez que el usuario se conecta a un servidor nuevo.
     *
     * El cliente incluye:
     *  - Interceptor que inyecta `Authorization: Bearer <access_token>` en cada
     *    request si hay token en memoria.
     *  - [JwtAuthenticator] que, ante 401, llama POST /auth/refresh con el
     *    refresh_token y reintenta la request original. Si el refresh también
     *    falla, emite broadcast SESSION_EXPIRED para que la app navegue al QR.
     *
     * @param baseUrl   Ej: "http://192.168.101.50:5000"  (sin /api/v1/)
     * @param context   Necesario para leer SharedPreferences en el interceptor
     */
    fun create(baseUrl: String, context: Context): ApiService {

        val prefs = context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        // Solo leer prefs si no hay token en memoria (evita race condition post-login)
        if (accessToken == null) {
            accessToken = prefs.getString("jwt_access_token", null)
        }

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

        val baseApiUrl = "$baseUrl/api/v1/"
        val authenticator = JwtAuthenticator(context.applicationContext, baseApiUrl)

        val httpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .addInterceptor { chain ->
                val req = chain.request()
                // Si el endpoint es /auth/refresh, NO inyectamos el access
                // (lo hace el JwtAuthenticator con el refresh_token explícito).
                // Para el resto: añadimos Bearer access_token si lo hay.
                val isRefresh = req.url.encodedPath.endsWith("/auth/refresh")
                val token = accessToken
                val out = if (!isRefresh && token != null) {
                    req.newBuilder()
                        .addHeader("Authorization", "Bearer $token")
                        .addHeader("Content-Type", "application/json")
                        .build()
                } else {
                    req
                }
                chain.proceed(out)
            }
            .authenticator(authenticator)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseApiUrl)
            .client(httpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    /** Guarda el token JWT en memoria y en SharedPreferences. */
    @android.annotation.SuppressLint("ApplySharedPref")  // commit() intencional
    fun saveToken(context: Context, accessToken: String, refreshToken: String) {
        this.accessToken = accessToken
        // commit() (síncrono) y NO apply() (asíncrono): inmediatamente después
        // de saveToken() navegamos a LiveViewFragment, que crea el Retrofit
        // leyendo el token desde prefs. Si apply() todavía no ha persistido,
        // Retrofit construye sin Authorization y la primera request da 401.
        context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
            .edit()
            .putString("jwt_access_token", accessToken)
            .putString("jwt_refresh_token", refreshToken)
            .commit()
    }

    /** Limpia el token de memoria y SharedPreferences (logout). */
    fun clearToken(context: Context) {
        this.accessToken = null
        context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
            .edit()
            .remove("jwt_access_token")
            .remove("jwt_refresh_token")
            .apply()
    }

    /** Construye la base URL a partir de los datos guardados en SharedPreferences. */
    fun buildBaseUrl(context: Context): String? {
        val prefs = context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        val ip   = prefs.getString("serverIp", null) ?: return null
        val port = prefs.getString("serverPort", "5000") ?: "5000"
        return "http://$ip:$port"
    }

    fun getAccessToken(context: Context): String? {
        if (accessToken != null) return accessToken
        val prefs = context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
        return prefs.getString("jwt_access_token", null)
    }
}
