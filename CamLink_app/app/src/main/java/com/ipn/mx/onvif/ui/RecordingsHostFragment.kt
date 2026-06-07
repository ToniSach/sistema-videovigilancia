package com.ipn.mx.onvif.ui

import android.app.DatePickerDialog
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Spinner
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.tabs.TabLayout
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.model.RecordingResponse
import com.ipn.mx.onvif.model.TimelineSegment
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * Pantalla de Grabaciones con DOS pestañas sobre la misma lista del día:
 *   - "Por lente": grabaciones continuas. En cámaras dual-lens muestra un
 *     selector L1/L2 y reproduce recortado por lente (recorte CLIENTE en el
 *     reproductor, sin coste de servidor).
 *   - "Eventos": grabaciones de alerta (clips /events/).
 *
 * Tocar una grabación abre PlaybackFragment con la lista completa del día (del
 * tipo de la pestaña) para reproducir EN CADENA tipo timeline desde la elegida.
 *
 * Es la pestaña "Grabaciones" de la barra inferior (destino timelineFragment).
 */
class RecordingsHostFragment : BaseMenuFragment() {

    private lateinit var spinnerCamera: Spinner
    private lateinit var btnDate: MaterialButton
    private lateinit var lensGroup: MaterialButtonToggleGroup
    private lateinit var tabs: TabLayout
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var rv: RecyclerView
    private lateinit var tvState: TextView

    private var cameras: List<CameraResponse> = emptyList()
    private val cal = Calendar.getInstance()
    private val dateFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
    private val adapter = TimelineAdapter { seg -> openPlayback(seg) }

    // Cache de las grabaciones del día (ambos tipos) para filtrar por pestaña
    // sin re-pedir al backend al cambiar de tab.
    private var dayRecordings: List<RecordingResponse> = emptyList()
    private var currentLens = "l1"   // lente activo (cámaras dual)

    private val selectedCamera get() = cameras.getOrNull(spinnerCamera.selectedItemPosition)
    private val isDual get() = selectedCamera?.isDualLens == true
    /** Tipo según la pestaña: 0 = continuas (por lente), 1 = eventos. */
    private val tabMode get() = if (tabs.selectedTabPosition == 1) "event" else "continuous"

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_recordings_host, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        spinnerCamera = view.findViewById(R.id.spinnerCamera)
        btnDate       = view.findViewById(R.id.btnDate)
        lensGroup     = view.findViewById(R.id.lensGroup)
        tabs          = view.findViewById(R.id.tabs)
        swipe         = view.findViewById(R.id.swipeTimeline)
        rv            = view.findViewById(R.id.rvTimeline)
        tvState       = view.findViewById(R.id.tvState)

        rv.layoutManager = LinearLayoutManager(requireContext())
        rv.adapter = adapter

        swipe.setColorSchemeResources(R.color.accent_cyan)
        swipe.setOnRefreshListener { loadRecordings() }
        btnDate.setOnClickListener { pickDate() }
        updateDateButton()

        lensGroup.check(R.id.btnLensL1)
        lensGroup.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            currentLens = if (checkedId == R.id.btnLensL2) "l2" else "l1"
        }

        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab?) = applyFilter()
            override fun onTabUnselected(tab: TabLayout.Tab?) {}
            override fun onTabReselected(tab: TabLayout.Tab?) {}
        })

        loadCameras()
    }

    private fun updateDateButton() { btnDate.text = dateFmt.format(cal.time) }

    private fun pickDate() {
        DatePickerDialog(
            requireContext(),
            { _, y, m, d ->
                cal.set(y, m, d)
                updateDateButton()
                loadRecordings()
            },
            cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)
        ).show()
    }

    private fun loadCameras() {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api = RetrofitClient.create(baseUrl, requireContext())
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.getCameras()
                cameras = resp.body()?.data ?: emptyList()
                val names = cameras.map { it.name }
                spinnerCamera.adapter = ArrayAdapter(
                    requireContext(), android.R.layout.simple_spinner_dropdown_item, names
                )
                spinnerCamera.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(p: android.widget.AdapterView<*>?, v: View?, pos: Int, id: Long) {
                        updateLensVisibility()
                        loadRecordings()
                    }
                    override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
                }
                if (cameras.isNotEmpty()) {
                    updateLensVisibility()
                    loadRecordings()
                } else showState("No hay cámaras")
            } catch (e: Exception) {
                showState("Error de conexión: ${e.message}")
            }
        }
    }

    private fun updateLensVisibility() {
        lensGroup.visibility = if (isDual) View.VISIBLE else View.GONE
        if (isDual && lensGroup.checkedButtonId == View.NO_ID) lensGroup.check(R.id.btnLensL1)
    }

    private fun loadRecordings() {
        val cam = selectedCamera ?: return
        val date = dateFmt.format(cal.time)
        showState("Cargando…")
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api = RetrofitClient.create(baseUrl, requireContext())
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.getRecordings(cameraId = cam.id, date = date)
                // Más recientes primero en la lista.
                dayRecordings = (resp.data ?: emptyList()).sortedByDescending { it.startedAt }
                applyFilter()
            } catch (e: Exception) {
                showState("No se pudo cargar grabaciones")
            } finally {
                swipe.isRefreshing = false
            }
        }
    }

    /** Filtra la lista cacheada por el tipo de la pestaña y la pinta. */
    private fun applyFilter() {
        val mode = tabMode
        val filtered = dayRecordings.filter { it.type == mode }
        val segs = filtered.map { r ->
            TimelineSegment(
                recordingId = r.id.toIntOrNull() ?: 0,
                start = r.startedAt,
                end = null,
                durationSeconds = r.durationSeconds,
                fileSizeMb = 0.0,
                type = r.type,
            )
        }
        adapter.submit(segs)
        if (segs.isEmpty()) {
            showState(if (mode == "event") "Sin eventos este día" else "Sin grabaciones este día")
        } else hideState()
    }

    private fun openPlayback(seg: TimelineSegment) {
        val cam = selectedCamera ?: return
        val args = Bundle().apply {
            putInt("cameraId", cam.id)
            putString("date", dateFmt.format(cal.time))
            putString("mode", tabMode)                 // continuous | event
            putInt("startRecordingId", seg.recordingId)
            putBoolean("isDualLens", isDual)
            if (isDual) putString("lens", currentLens)
        }
        try {
            findNavController().navigate(R.id.playbackFragment, args)
        } catch (e: Exception) {
            showState("No se pudo abrir la reproducción")
        }
    }

    private fun showState(msg: String) { tvState.text = msg; tvState.visibility = View.VISIBLE }
    private fun hideState() { tvState.visibility = View.GONE }
}
