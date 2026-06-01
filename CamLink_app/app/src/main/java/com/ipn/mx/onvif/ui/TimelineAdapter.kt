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
        holder.sub.text = "%d:%02d · %.1f MB".format(mins, secs, seg.fileSizeMb)

        if (seg.type == "event") {
            holder.badge.visibility = View.VISIBLE
            holder.badge.text = "EVENTO"
            holder.badge.setBackgroundResource(R.drawable.bg_badge_event)
        } else {
            holder.badge.visibility = View.GONE
        }

        holder.itemView.setOnClickListener { onClick(seg) }
    }

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
