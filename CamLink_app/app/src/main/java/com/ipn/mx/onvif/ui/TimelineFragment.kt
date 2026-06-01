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
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.CameraResponse
import com.ipn.mx.onvif.model.TimelineSegment
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * Timeline de grabaciones: elige cámara + fecha y lista los segmentos del día
 * (continuos y de eventos). Tocar uno abre PlaybackFragment.
 *
 * Puede recibir argumentos `cameraId` (Int) y `date` (YYYY-MM-DD) para abrirse
 * directamente en una cámara/fecha — lo usa el link de las notificaciones.
 */
class TimelineFragment : BaseMenuFragment() {

    private lateinit var spinnerCamera: Spinner
    private lateinit var btnDate: MaterialButton
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var rv: RecyclerView
    private lateinit var tvState: TextView

    private var cameras: List<CameraResponse> = emptyList()
    private val cal = Calendar.getInstance()
    private val dateFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
    private val adapter = TimelineAdapter { seg -> openPlayback(seg) }

    // Argumentos opcionales (desde notificación).
    private var argCameraId: Int = -1
    private var argDate: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        argCameraId = arguments?.getInt("cameraId", -1) ?: -1
        argDate = arguments?.getString("date")
        if (!argDate.isNullOrBlank()) {
            try { cal.time = dateFmt.parse(argDate!!)!! } catch (_: Exception) {}
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_timeline, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        spinnerCamera = view.findViewById(R.id.spinnerCamera)
        btnDate       = view.findViewById(R.id.btnDate)
        swipe         = view.findViewById(R.id.swipeTimeline)
        rv            = view.findViewById(R.id.rvTimeline)
        tvState       = view.findViewById(R.id.tvState)

        rv.layoutManager = LinearLayoutManager(requireContext())
        rv.adapter = adapter

        swipe.setColorSchemeResources(R.color.accent_cyan)
        swipe.setOnRefreshListener { loadTimeline() }
        btnDate.setOnClickListener { pickDate() }
        updateDateButton()

        loadCameras()
    }

    private fun updateDateButton() {
        btnDate.text = dateFmt.format(cal.time)
    }

    private fun pickDate() {
        DatePickerDialog(
            requireContext(),
            { _, y, m, d ->
                cal.set(y, m, d)
                updateDateButton()
                loadTimeline()
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
                val ad = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, names)
                spinnerCamera.adapter = ad
                // Si vino una cámara por argumento, seleccionarla.
                val idx = cameras.indexOfFirst { it.id == argCameraId }
                if (idx >= 0) spinnerCamera.setSelection(idx)

                spinnerCamera.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(p: android.widget.AdapterView<*>?, v: View?, pos: Int, id: Long) {
                        loadTimeline()
                    }
                    override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
                }
                if (cameras.isNotEmpty()) loadTimeline() else showState("No hay cámaras")
            } catch (e: Exception) {
                showState("Error de conexión: ${e.message}")
            }
        }
    }

    private fun loadTimeline() {
        val pos = spinnerCamera.selectedItemPosition
        val cam = cameras.getOrNull(pos) ?: return
        val date = dateFmt.format(cal.time)
        showState("Cargando…")
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api = RetrofitClient.create(baseUrl, requireContext())
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.getTimeline(cam.id, date)
                val segs = resp.body()?.data?.segments ?: emptyList()
                adapter.submit(segs)
                if (segs.isEmpty()) showState("Sin grabaciones este día") else hideState()
            } catch (e: Exception) {
                showState("No se pudo cargar el timeline")
            } finally {
                swipe.isRefreshing = false
            }
        }
    }

    private fun openPlayback(seg: TimelineSegment) {
        val bundle = Bundle().apply { putString("recordingId", seg.recordingId.toString()) }
        findNavController().navigate(R.id.playbackFragment, bundle)
    }

    private fun showState(msg: String) {
        tvState.text = msg
        tvState.visibility = View.VISIBLE
    }

    private fun hideState() {
        tvState.visibility = View.GONE
    }
}
