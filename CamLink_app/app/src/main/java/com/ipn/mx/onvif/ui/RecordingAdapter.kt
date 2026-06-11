/*
 * ============================================================================
 * MÓDULO: RecordingAdapter — Adaptador RecyclerView de grabaciones (CamLink)
 * ============================================================================
 *
 * PROPÓSITO
 *   Adaptador que pinta cada grabación (RecordingResponse) como una fila
 *   (item_recording): título con fecha/hora legible, subtítulo con duración y
 *   cámara, icono de alerta y botón de favorito.
 *
 * RESPONSABILIDAD
 *   - Enlazar los datos de cada RecordingResponse a su ViewHolder.
 *   - Propagar los gestos a los callbacks `onPlay` (tocar fila/botón play) y
 *     `onFavorite` (estrella).
 *   - Formatear fecha/hora (ISO → "dd MMM yyyy · HH:mm") y duración (m:ss).
 *
 * DEPENDENCIAS
 *   - model.RecordingResponse: DTO de cada grabación.
 *   - Layout item_recording (vistas de la fila).
 *
 * COMPONENTES RELACIONADOS
 *   - RecordingsFragment: lo crea y le inyecta los callbacks de navegación.
 *
 * PUNTO DE ENTRADA
 *   Se instancia desde RecordingsFragment.onViewCreated.
 *
 * PIPELINE(S)
 *   #14 Reproducción histórica — etapa de listado (UI).
 * ============================================================================
 */
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
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Adaptador de la lista de grabaciones.
 *
 * Rol: render del listado del Pipeline #14. Lo crea RecordingsFragment, que le
 * pasa los callbacks de interacción.
 *
 * @property items lista mutable interna de grabaciones (se reemplaza vía submitList).
 * @property onPlay invocado al tocar la fila o el botón play (abre PlaybackFragment).
 * @property onFavorite invocado al tocar la estrella (marcar/desmarcar favorito).
 */
class RecordingAdapter(
    private val items: MutableList<RecordingResponse> = mutableListOf(),
    private val onPlay:     (RecordingResponse) -> Unit,
    private val onFavorite: (RecordingResponse) -> Unit
) : RecyclerView.Adapter<RecordingAdapter.ViewHolder>() {

    inner class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvName:     TextView    = view.findViewById(R.id.tvRecordingName)
        val tvSub:      TextView    = view.findViewById(R.id.tvRecordingSub)
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

        // Título: fecha/hora legible (si no hay, el nombre de archivo).
        holder.tvName.text = formatDateTime(rec.startedAt) ?: rec.filename ?: "Grabación"
        // Subtítulo: duración + cámara.
        holder.tvSub.text = "${formatDuration(rec.durationSeconds)} · Cámara ${rec.cameraId}"

        holder.ivAlert.visibility = if (rec.hasAlert) View.VISIBLE else View.GONE
        holder.btnFav.setImageResource(
            if (rec.isFavorite) R.drawable.ic_star_active else R.drawable.ic_star_inactive
        )

        // Tocar la tarjeta o el botón reproduce; la estrella marca favorito.
        holder.itemView.setOnClickListener { onPlay(rec) }
        holder.btnPlay.setOnClickListener { onPlay(rec) }
        holder.btnFav.setOnClickListener  { onFavorite(rec) }
    }

    private fun formatDateTime(iso: String?): String? {
        if (iso.isNullOrBlank()) return null
        return try {
            val clean = iso.substringBefore('.')  // sin microsegundos
            val parsed = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(clean)
            if (parsed != null)
                SimpleDateFormat("dd MMM yyyy · HH:mm", Locale("es")).format(parsed)
            else iso
        } catch (e: Exception) {
            iso
        }
    }

    private fun formatDuration(seconds: Int): String {
        val m = seconds / 60
        val s = seconds % 60
        return "%d:%02d".format(m, s)
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
