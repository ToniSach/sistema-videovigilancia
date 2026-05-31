package com.ipn.mx.onvif.util

import android.content.Context
import android.os.Build
import java.util.UUID

/**
 * Genera (o recupera) un UUID estable por instalación de la app.
 *
 * El backend usa este UUID como clave única en `mobile_devices.device_uuid`
 * — debe ser el mismo entre arranques mientras la app no se reinstale.
 * Si el usuario desinstala y reinstala, se generará uno nuevo (lo cual está
 * bien: el dispositivo cuenta como otro al re-vincular).
 *
 * Lo guardamos en el mismo `auth_prefs` para que se borre en logout (asi el
 * próximo escaneo crea un device record limpio, evitando colisiones por
 * fcm_token obsoleto si llegáramos a usar FCM).
 */
object DeviceIdentity {

    private const val KEY_DEVICE_UUID = "device_uuid_persistent"
    private const val PREFS = "auth_prefs"

    fun getOrCreateUuid(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY_DEVICE_UUID, null)?.let { return it }
        val uuid = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_DEVICE_UUID, uuid).apply()
        return uuid
    }

    /**
     * Nombre legible que aparecerá en el panel de dispositivos del backend.
     * Combina marca + modelo (ej. "Samsung SM-A536B").
     */
    fun friendlyDeviceName(): String {
        val brand = Build.BRAND?.replaceFirstChar { it.uppercase() } ?: "Android"
        val model = Build.MODEL ?: "device"
        return if (model.startsWith(brand, ignoreCase = true)) model
               else "$brand $model"
    }
}
