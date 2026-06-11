/*
 * ============================================================================
 * MÓDULO: RecordingsFragment — Lista plana de grabaciones (CamLink)
 * ============================================================================
 *
 * PROPÓSITO
 *   Pantalla que lista TODAS las grabaciones disponibles (sin filtro de cámara
 *   ni fecha) en un RecyclerView, con pull-to-refresh y estados de carga/vacío/
 *   error. Tocar una grabación la abre en PlaybackFragment.
 *
 * RESPONSABILIDAD
 *   - Pedir las grabaciones al backend (GET /recordings) y volcarlas al adapter.
 *   - Gestionar los estados visuales (cargando, contenido, vacío, error+reintento).
 *   - Navegar a la reproducción pasando solo el `recordingId` (modo legado, una
 *     única grabación).
 *
 * DEPENDENCIAS
 *   - RetrofitClient + ApiService.getRecordings(): origen de datos.
 *   - RecordingAdapter: render de cada fila.
 *   - BaseMenuFragment: base común (menú de opciones de la toolbar).
 *
 * COMPONENTES RELACIONADOS
 *   - RecordingsHostFragment / TimelineFragment: vistas hermanas de grabaciones
 *     (con selector de cámara/fecha y reproducción en cadena del día).
 *   - PlaybackFragment: destino de la reproducción.
 *
 * PUNTO DE ENTRADA
 *   Destino de Navigation que muestra la lista global de grabaciones.
 *
 * PIPELINE(S)
 *   #14 Reproducción histórica — etapa de listado (consume /recordings).
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch

/**
 * Fragment de la lista global de grabaciones.
 *
 * Rol: vista de listado simple del Pipeline #14. La instancia el NavController;
 * en onViewCreated cablea el RecyclerView + SwipeRefresh y dispara la primera
 * carga. Usa `viewLifecycleOwner.lifecycleScope` para que las corrutinas de red
 * se cancelen al destruirse la vista.
 *
 * Dependencias: RecordingAdapter (filas) y ApiService.getRecordings (datos).
 */
class RecordingsFragment : BaseMenuFragment() {

    private lateinit var adapter: RecordingAdapter
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var stateContainer: LinearLayout
    private lateinit var stateProgress: ProgressBar
    private lateinit var stateIcon: ImageView
    private lateinit var stateText: TextView
    private lateinit var btnRetry: MaterialButton

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_recordings, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        adapter = RecordingAdapter(
            onPlay = { rec ->
                val bundle = Bundle().apply { putString("recordingId", rec.id) }
                findNavController().navigate(R.id.action_recordings_to_playback, bundle)
            },
            onFavorite = { rec ->
                android.widget.Toast.makeText(
                    requireContext(), "Favoritos: próximamente", android.widget.Toast.LENGTH_SHORT
                ).show()
            }
        )

        view.findViewById<RecyclerView>(R.id.rvRecordings).apply {
            layoutManager = LinearLayoutManager(requireContext())
            this.adapter  = this@RecordingsFragment.adapter
        }

        swipe          = view.findViewById(R.id.swipeRecordings)
        stateContainer = view.findViewById(R.id.stateContainer)
        stateProgress  = view.findViewById(R.id.stateProgress)
        stateIcon      = view.findViewById(R.id.stateIcon)
        stateText      = view.findViewById(R.id.stateText)
        btnRetry       = view.findViewById(R.id.btnRetry)

        swipe.setColorSchemeResources(R.color.accent_cyan)
        swipe.setOnRefreshListener { loadRecordings(refresh = true) }
        btnRetry.setOnClickListener { loadRecordings() }

        loadRecordings()
    }

    /**
     * Carga la lista de grabaciones del backend y actualiza la UI según el
     * resultado (contenido / vacío / error).
     *
     * @param refresh true cuando viene del pull-to-refresh (no muestra el estado
     *   de "cargando" a pantalla completa, solo el spinner del SwipeRefresh).
     * Llamado por: onViewCreated (carga inicial), pull-to-refresh y botón de
     * reintento. Usa endpoint GET /recordings (ApiService.getRecordings).
     */
    private fun loadRecordings(refresh: Boolean = false) {
        if (!refresh) showLoading()
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: run {
            showError("Sin URL de servidor"); return
        }
        val api = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val recordings = api.getRecordings().data
                adapter.submitList(recordings)
                if (recordings.isEmpty()) showEmpty() else showContent()
            } catch (e: Exception) {
                showError("No se pudieron cargar las grabaciones")
            } finally {
                swipe.isRefreshing = false
            }
        }
    }

    // ── Estados ───────────────────────────────────────────────────────────────
    private fun showLoading() {
        stateContainer.visibility = View.VISIBLE
        stateProgress.visibility = View.VISIBLE
        stateIcon.visibility = View.GONE
        btnRetry.visibility = View.GONE
        stateText.text = "Cargando grabaciones…"
    }

    private fun showContent() {
        stateContainer.visibility = View.GONE
    }

    private fun showEmpty() {
        stateContainer.visibility = View.VISIBLE
        stateProgress.visibility = View.GONE
        stateIcon.visibility = View.VISIBLE
        btnRetry.visibility = View.GONE
        stateText.text = "Aún no hay grabaciones"
    }

    private fun showError(msg: String) {
        stateContainer.visibility = View.VISIBLE
        stateProgress.visibility = View.GONE
        stateIcon.visibility = View.VISIBLE
        btnRetry.visibility = View.VISIBLE
        stateText.text = msg
    }
}
