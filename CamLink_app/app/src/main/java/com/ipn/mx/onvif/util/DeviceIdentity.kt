/*
 * ============================================================================
 * MÓDULO: util/DeviceIdentity — identidad estable del dispositivo
 * ============================================================================
 *
 * PROPÓSITO
 *   Provee un UUID estable por instalación y un nombre legible del dispositivo,
 *   ambos necesarios para registrar el móvil en el backend (`mobile_devices`).
 *
 * RESPONSABILIDAD
 *   - getOrCreateUuid: UUID persistente en SharedPreferences (clave de
 *     `mobile_devices.device_uuid`).
 *   - friendlyDeviceName: nombre marca+modelo para el panel de dispositivos.
 *
 * DEPENDENCIAS
 *   - android.os.Build (marca/modelo) + SharedPreferences "auth_prefs".
 *
 * COMPONENTES RELACIONADOS
 *   QrScanFragment compone con esto el [com.ipn.mx.onvif.model.DeviceRegisterRequest]
 *   que envía a `POST /devices/register`.
 *
 * PUNTO DE ENTRADA
 *   [getOrCreateUuid] / [friendlyDeviceName].
 *
 * PIPELINE: #2 Auth (registro de dispositivo por QR).
 * ============================================================================
 */
package com.ipn.mx.onvif.util

import android.content.Context
import android.os.Build
import java.util.UUID

/**
 * Identidad estable del dispositivo para el registro en el backend.
 *
 * Rol: utilidad sin estado (object) consumida por el flujo de alta por QR.
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

    /**
     * Devuelve el UUID persistente del dispositivo, creándolo la primera vez.
     *
     * @param context para acceder a SharedPreferences "auth_prefs".
     * @return el UUID estable (igual entre arranques hasta reinstalar/logout).
     * Llamado por: QrScanFragment al construir el DeviceRegisterRequest.
     */
    fun getOrCreateUuid(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY_DEVICE_UUID, null)?.let { return it }
        val uuid = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_DEVICE_UUID, uuid).apply()
        return uuid
    }

    /**
     * Nombre legible que aparecerá en el panel de dispositivos del backend.
     * Combina marca + modelo (ej. "Samsung SM-A536B"), evitando duplicar la
     * marca cuando el modelo ya la incluye.
     *
     * @return cadena marca+modelo para `device_name` del registro.
     * Llamado por: QrScanFragment al construir el DeviceRegisterRequest.
     */
    fun friendlyDeviceName(): String {
        val brand = Build.BRAND?.replaceFirstChar { it.uppercase() } ?: "Android"
        val model = Build.MODEL ?: "device"
        return if (model.startsWith(brand, ignoreCase = true)) model
               else "$brand $model"
    }
}
