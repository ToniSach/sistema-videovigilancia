package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.launch

class RecordingsFragment : BaseMenuFragment() {

    private lateinit var adapter: RecordingAdapter

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
                // TODO: llamar a endpoint de favorito cuando el servidor lo exponga
                Toast.makeText(requireContext(), "Favorito: ${rec.filename}", Toast.LENGTH_SHORT).show()
            }
        )

        view.findViewById<RecyclerView>(R.id.rvRecordings).apply {
            layoutManager = LinearLayoutManager(requireContext())
            this.adapter  = this@RecordingsFragment.adapter
        }

        loadRecordings()
    }

    private fun loadRecordings() {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val recordings = api.getRecordings().data
                adapter.submitList(recordings)

                if (recordings.isEmpty()) {
                    Toast.makeText(requireContext(), "No hay grabaciones", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error al cargar grabaciones: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }
}
