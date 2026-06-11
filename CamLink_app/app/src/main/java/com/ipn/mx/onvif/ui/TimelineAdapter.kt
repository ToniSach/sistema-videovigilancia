/*
 * ============================================================================
 * MÓDULO: TimelineAdapter — Adaptador RecyclerView del timeline (CamLink)
 * ============================================================================
 *
 * PROPÓSITO
 *   Adaptador que pinta cada segmento de grabación (TimelineSegment) como una
 *   fila (item_timeline_segment): hora de inicio, duración (+ tamaño si lo hay)
 *   y un badge "EVENTO" para los segmentos de tipo evento.
 *
 * RESPONSABILIDAD
 *   - Enlazar cada TimelineSegment a su ViewHolder y formatear hora/duración.
 *   - Distinguir visualmente continuos vs eventos (badge).
 *   - Propagar el toque de una fila al callback `onClick`.
 *
 * DEPENDENCIAS
 *   - model.TimelineSegment: DTO de cada segmento.
 *   - Layout item_timeline_segment.
 *
 * COMPONENTES RELACIONADOS
 *   - TimelineFragment y RecordingsHostFragment: lo crean e inyectan el callback
 *     que navega a PlaybackFragment.
 *
 * PUNTO DE ENTRADA
 *   Se instancia desde TimelineFragment / RecordingsHostFragment.
 *
 * PIPELINE(S)
 *   #14 Reproducción histórica — etapa de timeline (UI).
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.TimelineSegment
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Adaptador de la línea de tiempo de grabaciones.
 *
 * Rol: render de los segmentos del Pipeline #14. Lo crean TimelineFragment y
 * RecordingsHostFragment.
 *
 * @property onClick invocado al tocar un segmento (abre PlaybackFragment).
 */
class TimelineAdapter(
    private val onClick: (TimelineSegment) -> Unit,
) : RecyclerView.Adapter<TimelineAdapter.VH>() {

    private val items = mutableListOf<TimelineSegment>()

    inner class VH(v: View) : RecyclerView.ViewHolder(v) {
        val time:  TextView = v.findViewById(R.id.tvSegTime)
        val sub:   TextView = v.findViewById(R.id.tvSegSub)
        val badge: TextView = v.findViewById(R.id.tvSegBadge)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_timeline_segment, parent, false))

    override fun getItemCount() = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val seg = items[position]
        holder.time.text = formatHour(seg.start)
        val mins = seg.durationSeconds / 60
        val secs = seg.durationSeconds % 60
        holder.sub.text = if (seg.fileSizeMb > 0)
            "%d:%02d · %.1f MB".format(mins, secs, seg.fileSizeMb)
        else
            "%d:%02d".format(mins, secs)

        if (seg.type == "event") {
            holder.badge.visibility = View.VISIBLE
            holder.badge.text = "EVENTO"
            holder.badge.setBackgroundResource(R.drawable.bg_badge_event)
        } else {
            holder.badge.visibility = View.GONE
        }

        holder.itemView.setOnClickListener { onClick(seg) }
    }

    /**
     * Reemplaza toda la lista de segmentos y refresca el RecyclerView.
     *
     * @param newItems nueva lista de segmentos a mostrar.
     * Llamado por: TimelineFragment.loadTimeline y RecordingsHostFragment.applyFilter.
     */
    fun submit(newItems: List<TimelineSegment>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    private fun formatHour(iso: String?): String {
        if (iso.isNullOrBlank()) return "--:--"
        return try {
            val clean = iso.substringBefore('.')
            val parsed = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(clean)
            if (parsed != null) SimpleDateFormat("HH:mm:ss", Locale.US).format(parsed) else iso
        } catch (e: Exception) { iso }
    }
}
