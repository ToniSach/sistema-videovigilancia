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
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.timepicker.MaterialTimePicker
import com.google.android.material.timepicker.TimeFormat
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
        )
        private const val DEBOUNCE_MS = 400L
    }

    private lateinit var container: LinearLayout
    private lateinit var loading: ProgressBar
    private var tvAiNotice: TextView? = null

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
        tvAiNotice = view.findViewById(R.id.tvAiNotice)
        renderRows()
        checkAiThenLoad()
    }

    /**
     * Regla de producto: sin IA activa NO se pueden personalizar notificaciones.
     * Consulta /ai/status; si no hay ninguna cámara con IA, muestra el aviso y
     * deshabilita la edición. Si sí la hay, carga las preferencias normalmente.
     */
    private fun checkAiThenLoad() {
        val api = buildApi() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val aiActive = try {
                val resp = api.getAiStatus()
                (resp.body()?.data?.activeCount ?: 0) > 0
            } catch (e: Exception) {
                Log.w(TAG, "No pude consultar estado IA: ${e.message}")
                true  // ante fallo de red, no bloqueamos (fail-open)
            }
            setEditingEnabled(aiActive)
            if (aiActive) loadPreferences()
        }
    }

    private fun setEditingEnabled(enabled: Boolean) {
        tvAiNotice?.visibility = if (enabled) View.GONE else View.VISIBLE
        container.visibility = if (enabled) View.VISIBLE else View.GONE
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

    // Chips de día → valor backend (0=domingo..6=sábado).
    private val dayChipIds = listOf(
        R.id.chipMon to 1, R.id.chipTue to 2, R.id.chipWed to 3,
        R.id.chipThu to 4, R.id.chipFri to 5, R.id.chipSat to 6, R.id.chipSun to 0,
    )

    private fun attachListeners(row: View, type: String) {
        val swEnabled = row.findViewById<SwitchCompat>(R.id.switchEnabled)
        val cbPush    = row.findViewById<CheckBox>(R.id.cbPush)
        val cbTel     = row.findViewById<CheckBox>(R.id.cbTelegram)
        val cbSched   = row.findViewById<CheckBox>(R.id.cbSchedule)
        val btnStart  = row.findViewById<MaterialButton>(R.id.btnTimeStart)
        val btnEnd    = row.findViewById<MaterialButton>(R.id.btnTimeEnd)

        val onChange = { _: View -> saveFromRow(row, type) }
        swEnabled.setOnCheckedChangeListener { v, _ -> onChange(v) }
        cbPush.setOnCheckedChangeListener   { v, _ -> onChange(v) }
        cbTel.setOnCheckedChangeListener    { v, _ -> onChange(v) }
        for ((chipId, _) in dayChipIds) {
            row.findViewById<Chip>(chipId).setOnCheckedChangeListener { v, _ -> onChange(v) }
        }
        cbSched.setOnCheckedChangeListener { v, on ->
            btnStart.isEnabled = on
            btnEnd.isEnabled = on
            onChange(v)
        }
        btnStart.setOnClickListener { pickTime(btnStart) { onChange(btnStart) } }
        btnEnd.setOnClickListener   { pickTime(btnEnd)   { onChange(btnEnd) } }
    }

    private fun pickTime(btn: MaterialButton, after: () -> Unit) {
        val parts = btn.text.toString().split(":")
        val h = parts.getOrNull(0)?.toIntOrNull() ?: 0
        val m = parts.getOrNull(1)?.toIntOrNull() ?: 0
        val picker = MaterialTimePicker.Builder()
            .setTimeFormat(TimeFormat.CLOCK_24H)
            .setHour(h).setMinute(m).build()
        picker.addOnPositiveButtonClickListener {
            btn.text = "%02d:%02d".format(picker.hour, picker.minute)
            after()
        }
        picker.show(parentFragmentManager, "time")
    }

    private fun bindRowFromDto(row: View, dto: NotificationPreferenceDto?) {
        val swEnabled = row.findViewById<SwitchCompat>(R.id.switchEnabled)
        val cbPush    = row.findViewById<CheckBox>(R.id.cbPush)
        val cbTel     = row.findViewById<CheckBox>(R.id.cbTelegram)
        val cbSched   = row.findViewById<CheckBox>(R.id.cbSchedule)
        val btnStart  = row.findViewById<MaterialButton>(R.id.btnTimeStart)
        val btnEnd    = row.findViewById<MaterialButton>(R.id.btnTimeEnd)

        // Suprimir listeners mientras seteamos para no disparar saves
        swEnabled.setOnCheckedChangeListener(null)
        cbPush.setOnCheckedChangeListener(null)
        cbTel.setOnCheckedChangeListener(null)
        cbSched.setOnCheckedChangeListener(null)
        for ((chipId, _) in dayChipIds) row.findViewById<Chip>(chipId).setOnCheckedChangeListener(null)

        if (dto == null) {
            swEnabled.isChecked = false
            cbPush.isChecked    = true
            cbTel.isChecked     = false
            for ((chipId, _) in dayChipIds) row.findViewById<Chip>(chipId).isChecked = true
            cbSched.isChecked = false
            btnStart.text = "00:00"; btnEnd.text = "23:59"
        } else {
            swEnabled.isChecked = dto.enabled
            // "app" = recibir en este móvil (WebSocket LAN). Aceptamos "push"
            // por compatibilidad con datos antiguos (FCM fue eliminado).
            cbPush.isChecked    = "app" in dto.channels || "push" in dto.channels
            cbTel.isChecked     = "telegram" in dto.channels
            val days = dto.days.ifEmpty { listOf(0, 1, 2, 3, 4, 5, 6) }.toSet()
            for ((chipId, dayVal) in dayChipIds) {
                row.findViewById<Chip>(chipId).isChecked = dayVal in days
            }
            val hasSched = !dto.scheduleStart.isNullOrBlank() && !dto.scheduleEnd.isNullOrBlank()
            cbSched.isChecked = hasSched
            btnStart.text = dto.scheduleStart ?: "00:00"
            btnEnd.text   = dto.scheduleEnd ?: "23:59"
        }
        btnStart.isEnabled = cbSched.isChecked
        btnEnd.isEnabled   = cbSched.isChecked
        attachListeners(row, row.tag as String)
    }

    /** Lee TODOS los controles de la fila y dispara el guardado debounced. */
    private fun saveFromRow(row: View, type: String) {
        val enabled = row.findViewById<SwitchCompat>(R.id.switchEnabled).isChecked
        val push    = row.findViewById<CheckBox>(R.id.cbPush).isChecked
        val tel     = row.findViewById<CheckBox>(R.id.cbTelegram).isChecked
        val days = dayChipIds.filter { row.findViewById<Chip>(it.first).isChecked }.map { it.second }
        val schedOn = row.findViewById<CheckBox>(R.id.cbSchedule).isChecked
        val start = if (schedOn) row.findViewById<MaterialButton>(R.id.btnTimeStart).text.toString() else null
        val end   = if (schedOn) row.findViewById<MaterialButton>(R.id.btnTimeEnd).text.toString() else null
        scheduleSave(type, enabled, push, tel, days, start, end)
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
        eventType: String, enabled: Boolean, push: Boolean, telegram: Boolean,
        days: List<Int>, start: String?, end: String?,
    ) {
        pendingSaves[eventType]?.cancel()
        pendingSaves[eventType] = viewLifecycleOwner.lifecycleScope.launch {
            try {
                kotlinx.coroutines.delay(DEBOUNCE_MS)
                savePreference(eventType, enabled, push, telegram, days, start, end)
            } catch (_: kotlinx.coroutines.CancellationException) {
                // Cancelado por un toque más reciente; ignorar
            }
        }
    }

    private suspend fun savePreference(
        eventType: String, enabled: Boolean, push: Boolean, telegram: Boolean,
        days: List<Int>, start: String?, end: String?,
    ) {
        val api = buildApi() ?: return
        val channels = buildList {
            if (push)     add("app")        // recibir en la app (WebSocket LAN)
            if (telegram) add("telegram")
        }.ifEmpty { listOf("app") }
        // Si no hay días marcados, lo tratamos como "todos" (evitar silencio total accidental).
        val daysFinal = days.ifEmpty { listOf(0, 1, 2, 3, 4, 5, 6) }
        val existing = current[eventType]
        try {
            if (existing == null) {
                // CREATE
                val resp = api.createNotificationPreference(
                    PreferenceMutation(
                        eventType = eventType,
                        cameraId  = null,
                        enabled   = enabled,
                        channels  = channels,
                        daysOfWeek = daysFinal,
                        scheduleStart = start,
                        scheduleEnd = end,
                    )
                )
                if (resp.isSuccessful) {
                    val newId = resp.body()?.data?.id
                    if (newId != null) {
                        current[eventType] = NotificationPreferenceDto(
                            id = newId, eventType = eventType, cameraId = null,
                            enabled = enabled, channels = channels, days = daysFinal,
                            scheduleStart = start, scheduleEnd = end,
                        )
                    }
                    toastIfVisible(R.string.pref_save_ok)
                } else toastIfVisible(R.string.pref_save_failed)
            } else {
                // UPDATE
                val resp = api.updateNotificationPreference(
                    existing.id,
                    PreferenceMutation(
                        enabled = enabled,
                        channels = channels,
                        daysOfWeek = daysFinal,
                        scheduleStart = start,
                        scheduleEnd = end,
                    )
                )
                if (resp.isSuccessful) {
                    current[eventType] = existing.copy(
                        enabled = enabled, channels = channels, days = daysFinal,
                        scheduleStart = start, scheduleEnd = end,
                    )
                    toastIfVisible(R.string.pref_save_ok)
                } else toastIfVisible(R.string.pref_save_failed)
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
