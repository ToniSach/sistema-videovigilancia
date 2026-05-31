package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.widget.SwitchCompat
import androidx.lifecycle.lifecycleScope
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.NotificationPreferenceDto
import com.ipn.mx.onvif.model.PreferenceMutation
import com.ipn.mx.onvif.network.ApiService
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Pantalla de preferencias de notificación (CRUD real contra el servidor).
 *
 * Cada tipo de evento del backend (EventType) tiene una fila en la UI con:
 *   - Switch enabled/disabled (POST si no existe, PUT enabled=false si existe)
 *   - Checkbox Push  → canal "push"
 *   - Checkbox Telegram → canal "telegram"
 *
 * Por ahora las preferencias son globales (camera_id=null). Si más adelante
 * quieres personalizar por cámara, se duplican filas con un selector.
 *
 * Cambios: auto-guardado al toque (debounced 400ms para colapsar múltiples
 * clicks en un único PATCH). No requiere botón "Guardar".
 */
class EventConfigFragment : BaseMenuFragment() {

    companion object {
        private const val TAG = "EventConfig"

        // Eventos visibles en la UI. Orden = orden de despliegue.
        private val EVENT_TYPES = listOf(
            "person"             to R.string.pref_event_person,
            "vehicle"            to R.string.pref_event_vehicle,
            "motion"             to R.string.pref_event_motion,
            "camera_offline"    to R.string.pref_event_camera_offline,
            "camera_reconnected" to R.string.pref_event_camera_reconnected,
            "tampering"          to R.string.pref_event_tampering,
        )
        private const val DEBOUNCE_MS = 400L
    }

    private lateinit var container: LinearLayout
    private lateinit var loading: ProgressBar

    /** event_type → DTO actual del servidor (null si todavía no existe). */
    private val current: MutableMap<String, NotificationPreferenceDto?> = mutableMapOf()

    /** event_type → job de guardado pendiente (para debounce). */
    private val pendingSaves: MutableMap<String, Job> = mutableMapOf()

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_event_config, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        container = view.findViewById(R.id.eventsContainer)
        loading   = view.findViewById(R.id.loading)
        renderRows()
        loadPreferences()
    }

    // ── UI ────────────────────────────────────────────────────────────────────

    private fun renderRows() {
        container.removeAllViews()
        val inflater = LayoutInflater.from(requireContext())
        for ((type, labelRes) in EVENT_TYPES) {
            val row = inflater.inflate(R.layout.item_event_pref, container, false)
            row.findViewById<TextView>(R.id.tvEventName).setText(labelRes)
            // Estado inicial: todo desactivado hasta que llegue el GET
            row.findViewById<SwitchCompat>(R.id.switchEnabled).isChecked = false
            row.findViewById<CheckBox>(R.id.cbPush).isChecked = true
            row.findViewById<CheckBox>(R.id.cbTelegram).isChecked = false
            row.tag = type
            container.addView(row)
            attachListeners(row, type)
        }
    }

    private fun attachListeners(row: View, type: String) {
        val swEnabled = row.findViewById<SwitchCompat>(R.id.switchEnabled)
        val cbPush    = row.findViewById<CheckBox>(R.id.cbPush)
        val cbTel     = row.findViewById<CheckBox>(R.id.cbTelegram)

        val onChange = { _: View ->
            scheduleSave(type, swEnabled.isChecked, cbPush.isChecked, cbTel.isChecked)
        }
        swEnabled.setOnCheckedChangeListener { v, _ -> onChange(v) }
        cbPush.setOnCheckedChangeListener   { v, _ -> onChange(v) }
        cbTel.setOnCheckedChangeListener    { v, _ -> onChange(v) }
    }

    private fun bindRowFromDto(row: View, dto: NotificationPreferenceDto?) {
        val swEnabled = row.findViewById<SwitchCompat>(R.id.switchEnabled)
        val cbPush    = row.findViewById<CheckBox>(R.id.cbPush)
        val cbTel     = row.findViewById<CheckBox>(R.id.cbTelegram)
        // Suprimir listeners mientras seteamos para no disparar saves
        swEnabled.setOnCheckedChangeListener(null)
        cbPush.setOnCheckedChangeListener(null)
        cbTel.setOnCheckedChangeListener(null)
        if (dto == null) {
            swEnabled.isChecked = false
            cbPush.isChecked    = true
            cbTel.isChecked     = false
        } else {
            swEnabled.isChecked = dto.enabled
            cbPush.isChecked    = "push" in dto.channels
            cbTel.isChecked     = "telegram" in dto.channels
        }
        attachListeners(row, row.tag as String)
    }

    // ── Red ───────────────────────────────────────────────────────────────────

    private fun loadPreferences() {
        val api = buildApi() ?: return
        loading.visibility = View.VISIBLE
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.getNotificationPreferences()
                if (resp.isSuccessful) {
                    val list = resp.body()?.data ?: emptyList()
                    // Indexar por event_type (sólo prefs globales, camera_id=null)
                    val global: Map<String, NotificationPreferenceDto> =
                        list.filter { it.cameraId == null }
                            .associateBy { it.eventType }
                    for ((type, _) in EVENT_TYPES) {
                        current[type] = global[type]
                    }
                    // Reflejar en UI
                    for (i in 0 until container.childCount) {
                        val row = container.getChildAt(i)
                        val type = row.tag as String
                        bindRowFromDto(row, current[type])
                    }
                } else {
                    Toast.makeText(requireContext(),
                        "${getString(R.string.pref_load_failed)} (HTTP ${resp.code()})",
                        Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Log.e(TAG, "loadPreferences error", e)
                Toast.makeText(requireContext(),
                    "${getString(R.string.pref_load_failed)}: ${e.message}",
                    Toast.LENGTH_LONG).show()
            } finally {
                loading.visibility = View.GONE
            }
        }
    }

    private fun scheduleSave(
        eventType: String, enabled: Boolean, push: Boolean, telegram: Boolean
    ) {
        pendingSaves[eventType]?.cancel()
        pendingSaves[eventType] = viewLifecycleOwner.lifecycleScope.launch {
            try {
                kotlinx.coroutines.delay(DEBOUNCE_MS)
                savePreference(eventType, enabled, push, telegram)
            } catch (_: kotlinx.coroutines.CancellationException) {
                // Cancelado por una toque más reciente; ignorar
            }
        }
    }

    private suspend fun savePreference(
        eventType: String, enabled: Boolean, push: Boolean, telegram: Boolean,
    ) {
        val api = buildApi() ?: return
        val channels = buildList {
            if (push)     add("push")
            if (telegram) add("telegram")
        }
        val existing = current[eventType]
        try {
            if (existing == null) {
                // CREATE
                val resp = api.createNotificationPreference(
                    PreferenceMutation(
                        eventType = eventType,
                        cameraId  = null,
                        enabled   = enabled,
                        channels  = channels.ifEmpty { listOf("push") },
                        daysOfWeek = listOf(0, 1, 2, 3, 4, 5, 6),
                    )
                )
                if (resp.isSuccessful) {
                    val newId = resp.body()?.data?.id
                    if (newId != null) {
                        current[eventType] = NotificationPreferenceDto(
                            id = newId,
                            eventType = eventType,
                            cameraId = null,
                            enabled = enabled,
                            channels = channels,
                            days = listOf(0, 1, 2, 3, 4, 5, 6),
                        )
                    }
                    toastIfVisible(R.string.pref_save_ok)
                } else {
                    toastIfVisible(R.string.pref_save_failed)
                }
            } else {
                // UPDATE
                val resp = api.updateNotificationPreference(
                    existing.id,
                    PreferenceMutation(
                        enabled = enabled,
                        channels = channels.ifEmpty { listOf("push") },
                    )
                )
                if (resp.isSuccessful) {
                    current[eventType] = existing.copy(
                        enabled = enabled,
                        channels = channels,
                    )
                    toastIfVisible(R.string.pref_save_ok)
                } else {
                    toastIfVisible(R.string.pref_save_failed)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "savePreference($eventType) error", e)
            toastIfVisible(R.string.pref_save_failed)
        }
    }

    private fun buildApi(): ApiService? {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return null
        return RetrofitClient.create(baseUrl, requireContext())
    }

    private fun toastIfVisible(resId: Int) {
        if (!isAdded || view == null) return
        Toast.makeText(requireContext(), resId, Toast.LENGTH_SHORT).show()
    }
}
