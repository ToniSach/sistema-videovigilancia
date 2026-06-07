package com.ipn.mx.onvif.ui

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.NotificationItem
import com.ipn.mx.onvif.network.RetrofitClient
import com.ipn.mx.onvif.service.NotificationWebSocketService
import kotlinx.coroutines.launch

/**
 * Panel in-app del historial de notificaciones / eventos.
 *
 * Funcionalidad:
 *   - GET /api/v1/mobile/notifications/history al abrir y en pull-to-refresh.
 *   - Recibe eventos en vivo desde [NotificationWebSocketService] vía broadcast
 *     local (los inserta arriba de la lista sin esperar al próximo fetch).
 *   - Badge de no-leídas calculado contra `notif_last_seen_ms` en SharedPreferences.
 *     Al abrir el fragment, marca todo como leído (timestamp = now).
 *   - Click en evento → navega a LiveView (la cámara concreta queda abierta tras
 *     volver atrás).
 *
 * Extiende BaseMenuFragment para conservar el toolbar de 3 puntos.
 */
class NotificationsPanelFragment : BaseMenuFragment() {

    companion object {
        private const val PREFS = "auth_prefs"
        private const val KEY_LAST_SEEN_MS = "notif_last_seen_ms"
    }

    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var recycler: RecyclerView
    private lateinit var emptyState: LinearLayout
    private lateinit var unreadBadge: TextView
    private lateinit var btnOpenConfig: ImageButton

    private val adapter = NotificationAdapter { item ->
        // Click en una alerta → abrir directamente la grabación del momento
        // (pestaña Eventos) y reproducir EN CADENA desde ahí. PlaybackFragment
        // resuelve qué clip cubre el instante de la alerta (targetTime) y, si la
        // cámara es dual, recorta por lente automáticamente.
        markLatestAsSeen()
        val date = dateFromIso(item.createdAt)
        if (item.cameraId <= 0 || date == null) {
            Toast.makeText(requireContext(),
                "Esta alerta no tiene grabación asociada", Toast.LENGTH_SHORT).show()
            return@NotificationAdapter
        }
        val args = Bundle().apply {
            putInt("cameraId", item.cameraId)
            putString("date", date)
            putString("mode", "event")
            putString("targetTime", item.createdAt)
        }
        try {
            findNavController().navigate(R.id.playbackFragment, args)
        } catch (e: Exception) {
            Toast.makeText(requireContext(),
                "No se pudo abrir la grabación: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    /** Extrae "YYYY-MM-DD" de un createdAt ISO ("2026-05-31T03:15:00"). */
    private fun dateFromIso(iso: String?): String? {
        if (iso.isNullOrBlank()) return null
        return iso.substringBefore('T').takeIf { it.length == 10 }
    }

    private val liveEventReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action != NotificationWebSocketService.ACTION_EVENT_RECEIVED) return
            val item = NotificationItem(
                // id < 0 marca evento "en vivo" sin id de BD (el siguiente fetch lo trae)
                id = -1,
                cameraId = intent.getIntExtra(NotificationWebSocketService.EXTRA_CAMERA_ID, 0),
                eventType = intent.getStringExtra(NotificationWebSocketService.EXTRA_EVENT_TYPE)
                            ?: "unknown",
                confidence = intent.getDoubleExtra(
                    NotificationWebSocketService.EXTRA_CONFIDENCE, 0.0).toFloat(),
                createdAt = isoFromEpochSec(
                    intent.getDoubleExtra(NotificationWebSocketService.EXTRA_TIMESTAMP, 0.0)),
            )
            // Tipos retirados: no mostrar 'cámara reconectada' ni 'manipulación'.
            if (item.eventType in listOf("camera_reconnected", "tampering")) return
            adapter.prependLive(item)
            updateUnreadBadge()
            emptyState.visibility = View.GONE
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_notifications_panel, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        swipeRefresh   = view.findViewById(R.id.swipeRefresh)
        recycler       = view.findViewById(R.id.rvNotifications)
        emptyState     = view.findViewById(R.id.emptyState)
        unreadBadge    = view.findViewById(R.id.tvUnreadBadge)
        btnOpenConfig  = view.findViewById(R.id.btnOpenConfig)

        recycler.layoutManager = LinearLayoutManager(requireContext())
        recycler.adapter = adapter

        // Cargar last_seen guardado para calcular el badge inicial
        val prefs = requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        adapter.setLastSeen(prefs.getLong(KEY_LAST_SEEN_MS, 0L))

        swipeRefresh.setOnRefreshListener { loadHistory() }
        btnOpenConfig.setOnClickListener {
            // Abrir la pantalla de preferencias (CRUD)
            try { findNavController().navigate(R.id.eventConfigFragment) }
            catch (e: Exception) {
                Toast.makeText(requireContext(),
                    "No se pudo abrir configuracion: ${e.message}",
                    Toast.LENGTH_SHORT).show()
            }
        }

        loadHistory()
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter(NotificationWebSocketService.ACTION_EVENT_RECEIVED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requireContext().registerReceiver(liveEventReceiver, filter,
                Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            requireContext().registerReceiver(liveEventReceiver, filter)
        }
    }

    override fun onStop() {
        try { requireContext().unregisterReceiver(liveEventReceiver) }
        catch (_: Exception) {}
        // Marcar todo como visto al salir
        markLatestAsSeen()
        super.onStop()
    }

    // ── Red ───────────────────────────────────────────────────────────────────

    private fun loadHistory() {
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext())
        if (baseUrl == null) {
            swipeRefresh.isRefreshing = false
            Toast.makeText(requireContext(),
                "Sin sesion", Toast.LENGTH_SHORT).show()
            return
        }
        val api = RetrofitClient.create(baseUrl, requireContext())
        swipeRefresh.isRefreshing = true
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.getNotificationsHistory(limit = 50)
                if (resp.isSuccessful) {
                    val items = (resp.body()?.data ?: emptyList())
                        .filter { it.eventType !in listOf("camera_reconnected", "tampering") }
                    adapter.submitList(items)
                    emptyState.visibility = if (items.isEmpty()) View.VISIBLE
                                            else View.GONE
                    updateUnreadBadge()
                } else {
                    Toast.makeText(requireContext(),
                        getString(R.string.notifications_load_error) +
                            " (HTTP ${resp.code()})",
                        Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(),
                    "${getString(R.string.notifications_load_error)}: ${e.message}",
                    Toast.LENGTH_LONG).show()
            } finally {
                swipeRefresh.isRefreshing = false
            }
        }
    }

    // ── Badge / last_seen ─────────────────────────────────────────────────────

    private fun updateUnreadBadge() {
        val count = adapter.unreadCount()
        if (count <= 0) {
            unreadBadge.visibility = View.GONE
        } else {
            unreadBadge.visibility = View.VISIBLE
            unreadBadge.text = if (count > 99) "99+" else count.toString()
        }
    }

    private fun markLatestAsSeen() {
        val latest = adapter.newestTimestampMs()
        if (latest <= 0) return
        val prefs = requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.edit().putLong(KEY_LAST_SEEN_MS, latest).apply()
        adapter.setLastSeen(latest)
        updateUnreadBadge()
    }

    private fun isoFromEpochSec(epochSec: Double): String {
        if (epochSec <= 0) return ""
        val sdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss",
            java.util.Locale.US).apply {
            timeZone = java.util.TimeZone.getTimeZone("UTC")
        }
        return sdf.format(java.util.Date((epochSec * 1000).toLong()))
    }
}
