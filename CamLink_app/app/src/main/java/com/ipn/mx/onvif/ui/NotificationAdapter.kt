/*
 * ============================================================================
 * MÓDULO: NotificationAdapter — adaptador RecyclerView del historial de alertas
 *         (Pipeline #13 Notificaciones, lado móvil)
 * ============================================================================
 *
 * PROPÓSITO
 *   Renderizar la lista de eventos (NotificationItem) del panel de notificaciones:
 *   título, subtítulo (cámara + "hace X" + confianza), icono por tipo y el punto
 *   de "no leído". Soporta inserción de eventos en vivo desde el WebSocket.
 *
 * RESPONSABILIDAD
 *   - Mantener la lista mutable de items y notificar cambios al RecyclerView.
 *   - Calcular la condición de "no leído" comparando created_at de cada item con
 *     lastSeenTimestampMs.
 *   - Formatear textos (título/subtítulo/tiempo-relativo) e iconos por event_type.
 *   - Parsear created_at ISO (con o sin milisegundos, UTC) a epoch ms.
 *
 * DEPENDENCIAS
 *   - model/NotificationItem (DTO de la fila), R.layout.item_notification.
 *
 * COMPONENTES RELACIONADOS
 *   - ui/NotificationsPanelFragment (lo crea, le pasa datos y el callback onClick).
 *
 * PUNTO DE ENTRADA
 *   Constructor con callback onClick; usado por el RecyclerView del panel.
 *
 * PIPELINE(S)
 *   #13 Notificaciones — presentación de la lista.
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.NotificationItem
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.abs

/**
 * Adapter para [NotificationsPanelFragment]. Cada item es un evento del
 * historial (o uno entrante en vivo). Click → callback para navegar a la
 * cámara correspondiente. La unread-ness se calcula contra
 * [lastSeenTimestampMs]: cualquier item con `created_at` posterior se marca.
 *
 * @property onClick callback invocado al pulsar una fila (navega al playback del
 *           evento). Quién lo instancia: NotificationsPanelFragment.
 */
class NotificationAdapter(
    private val onClick: (NotificationItem) -> Unit,
) : RecyclerView.Adapter<NotificationAdapter.VH>() {

    private val items: MutableList<NotificationItem> = mutableListOf()
    private var lastSeenMs: Long = 0L

    // Parser de "2026-05-28T14:30:00.123" o "2026-05-28T14:30:00" (UTC, sin Z)
    private val isoParser = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }
    private val isoParserMs = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    /**
     * Reemplaza por completo la lista (resultado de un fetch de historial).
     * @param newItems items recibidos del backend, ya filtrados.
     * Llamado por: NotificationsPanelFragment.loadHistory.
     */
    fun submitList(newItems: List<NotificationItem>) {
        val oldSize = items.size
        items.clear()
        if (oldSize > 0) notifyItemRangeRemoved(0, oldSize)
        items.addAll(newItems)
        if (newItems.isNotEmpty()) notifyItemRangeInserted(0, newItems.size)
    }

    /**
     * Inserta un item nuevo al inicio (notificación en vivo recibida por WS).
     * Evita duplicados si la API ya lo había traído (mismo id).
     * @param item evento entrante. Llamado por: el receiver de eventos en vivo del
     *        NotificationsPanelFragment.
     */
    fun prependLive(item: NotificationItem) {
        // Evitar duplicados si la API ya lo trajo
        if (items.any { it.id == item.id }) return
        items.add(0, item)
        notifyItemInserted(0)
    }

    /**
     * Fija la marca temporal de "última vez visto" (recalcula los puntos de no
     * leído al re-bindear las filas visibles).
     * @param timestampMs epoch ms del último item considerado leído.
     * Llamado por: NotificationsPanelFragment (init del badge y markLatestAsSeen).
     */
    fun setLastSeen(timestampMs: Long) {
        if (timestampMs == lastSeenMs) return
        lastSeenMs = timestampMs
        // Solo afecta el indicador "no leído" por item; basta con notificar
        // todo el rango visible (cheap, no recrea ViewHolders).
        if (items.isNotEmpty()) notifyItemRangeChanged(0, items.size)
    }

    /** @return nº de items con timestamp posterior a lastSeenMs (no leídos). */
    fun unreadCount(): Int = items.count { itemTimestampMs(it) > lastSeenMs }

    /** @return el timestamp (epoch ms) del item más reciente, o 0 si lista vacía. */
    fun newestTimestampMs(): Long =
        items.maxOfOrNull { itemTimestampMs(it) } ?: 0L

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_notification, parent, false)
        return VH(v)
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        // OJO: no usar el nombre `it` aquí — dentro de
        // setOnClickListener { onClick(it) } el `it` se interpreta como el
        // parámetro implícito del lambda (View!), no como nuestro item.
        // Usamos `item` y capturamos explícitamente.
        val item = items[position]
        holder.tvTitle.text = titleFor(item.eventType)
        holder.tvSubtitle.text = subtitleFor(item)
        holder.ivIcon.setImageResource(iconFor(item.eventType))
        holder.vUnread.visibility =
            if (itemTimestampMs(item) > lastSeenMs) View.VISIBLE else View.INVISIBLE
        holder.itemView.setOnClickListener { onClick(item) }
    }

    // ── Helpers de formato ────────────────────────────────────────────────────

    private fun titleFor(type: String): String = when (type) {
        "person" -> "Persona detectada"
        "vehicle" -> "Vehiculo detectado"
        "motion" -> "Movimiento"
        "camera_offline" -> "Camara desconectada"
        "camera_reconnected" -> "Camara reconectada"
        "tampering" -> "Posible manipulacion"
        else -> type.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }

    private fun iconFor(type: String): Int = when (type) {
        "person", "vehicle", "motion", "tampering" -> R.drawable.ic_alert
        "camera_offline" -> R.drawable.ic_close_red
        "camera_reconnected" -> R.drawable.ic_refresh
        else -> R.drawable.ic_alert
    }

    private fun subtitleFor(item: NotificationItem): String {
        val timeAgo = formatTimeAgo(itemTimestampMs(item))
        val cam = if (item.cameraId > 0) "Cam ${item.cameraId}" else "Sistema"
        val conf = if (item.confidence > 0f)
            " • ${(item.confidence * 100).toInt()}%" else ""
        return "$cam • $timeAgo$conf"
    }

    private fun formatTimeAgo(epochMs: Long): String {
        if (epochMs <= 0) return ""
        val diffSec = (System.currentTimeMillis() - epochMs) / 1000
        return when {
            abs(diffSec) < 60 -> "hace ${diffSec}s"
            abs(diffSec) < 3600 -> "hace ${diffSec / 60}m"
            abs(diffSec) < 86400 -> "hace ${diffSec / 3600}h"
            else -> SimpleDateFormat("d MMM HH:mm", Locale.getDefault())
                .format(Date(epochMs))
        }
    }

    /**
     * Parsea created_at (ISO UTC, con o sin milisegundos, con o sin "Z") a epoch ms.
     * @return epoch ms o 0 si no se pudo parsear. Usado para ordenar/no-leído/tiempo.
     */
    private fun itemTimestampMs(it: NotificationItem): Long {
        val raw = it.createdAt.trim().removeSuffix("Z")
        // El backend serializa con o sin millis según el caso
        val date: Date? = try { isoParserMs.parse(raw) }
                         catch (_: Exception) { try { isoParser.parse(raw) }
                                                catch (_: Exception) { null } }
        return date?.time ?: 0L
    }

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val ivIcon: ImageView = view.findViewById(R.id.ivEventIcon)
        val tvTitle: TextView = view.findViewById(R.id.tvEventTitle)
        val tvSubtitle: TextView = view.findViewById(R.id.tvEventSubtitle)
        val vUnread: View = view.findViewById(R.id.vUnreadDot)
    }
}
