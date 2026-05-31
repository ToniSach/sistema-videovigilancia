package com.ipn.mx.onvif.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.model.RecordingResponse

class RecordingAdapter(
    private val items: MutableList<RecordingResponse> = mutableListOf(),
    private val onPlay:     (RecordingResponse) -> Unit,
    private val onFavorite: (RecordingResponse) -> Unit
) : RecyclerView.Adapter<RecordingAdapter.ViewHolder>() {

    inner class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvName:     TextView    = view.findViewById(R.id.tvRecordingName)
        val btnPlay:    ImageButton = view.findViewById(R.id.btnPlay)
        val btnFav:     ImageButton = view.findViewById(R.id.btnFavorite)
        val ivAlert:    ImageView   = view.findViewById(R.id.ivAlert)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder =
        ViewHolder(
            LayoutInflater.from(parent.context)
                .inflate(R.layout.item_recording, parent, false)
        )

    override fun getItemCount() = items.size

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val rec = items[position]

        // Nombre: usar filename si no hay otro campo descriptivo
        holder.tvName.text = rec.filename

        // Alerta visible solo si la grabación fue marcada como evento
        holder.ivAlert.visibility = if (rec.hasAlert) View.VISIBLE else View.GONE

        // Ícono de favorito
        holder.btnFav.setImageResource(
            if (rec.isFavorite) R.drawable.ic_star_active else R.drawable.ic_star_inactive
        )

        holder.btnPlay.setOnClickListener { onPlay(rec) }
        holder.btnFav.setOnClickListener  { onFavorite(rec) }
    }

    /** Reemplaza toda la lista y notifica al RecyclerView. */
    fun submitList(newItems: List<RecordingResponse>) {
        val oldSize = items.size
        items.clear()
        if (oldSize > 0) notifyItemRangeRemoved(0, oldSize)
        items.addAll(newItems)
        if (newItems.isNotEmpty()) notifyItemRangeInserted(0, newItems.size)
    }
}
