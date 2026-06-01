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
