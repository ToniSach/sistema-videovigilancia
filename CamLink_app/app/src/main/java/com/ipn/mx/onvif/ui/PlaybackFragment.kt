package com.ipn.mx.onvif.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.SeekBar
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class PlaybackFragment : Fragment() {

    private var recordingId: String? = null
    private var player: ExoPlayer?   = null
    private var isSeekingByUser      = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        recordingId = arguments?.getString("recordingId")
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_playback, container, false)

    @OptIn(UnstableApi::class)
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val videoContainer = view.findViewById<FrameLayout>(R.id.videoContainer)
        val seekBar        = view.findViewById<SeekBar>(R.id.seekBar)
        val btnPlayPause   = view.findViewById<ImageButton>(R.id.btnPlayPause)
        val btnPrevious    = view.findViewById<ImageButton>(R.id.btnPrevious)
        val btnNext        = view.findViewById<ImageButton>(R.id.btnNext)

        // Añadir PlayerView de Media3 al contenedor
        val playerView = PlayerView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            useController = false  // usamos nuestros propios controles
        }
        videoContainer.addView(playerView)

        // Cargar y reproducir la grabación
        loadAndPlay(playerView, seekBar, btnPlayPause)

        // ── Controles ─────────────────────────────────────────────────────────
        btnPlayPause.setOnClickListener {
            player?.let {
                if (it.isPlaying) {
                    it.pause()
                    btnPlayPause.setImageResource(R.drawable.ic_play)
                } else {
                    it.play()
                    btnPlayPause.setImageResource(R.drawable.ic_record_stop)
                }
            }
        }

        btnPrevious.setOnClickListener {
            player?.seekTo(0)
        }

        btnNext.setOnClickListener {
            Toast.makeText(requireContext(), "Sin grabación siguiente", Toast.LENGTH_SHORT).show()
        }

        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) {
                    val duration = player?.duration ?: return
                    player?.seekTo((duration * progress / 1000L))
                }
            }
            override fun onStartTrackingTouch(sb: SeekBar?) { isSeekingByUser = true }
            override fun onStopTrackingTouch(sb: SeekBar?)  { isSeekingByUser = false }
        })

        // Actualizar seekBar cada segundo
        viewLifecycleOwner.lifecycleScope.launch {
            while (isActive) {
                if (!isSeekingByUser) {
                    val duration = player?.duration ?: 0L
                    val position = player?.currentPosition ?: 0L
                    if (duration > 0) {
                        seekBar.progress = (position * 1000 / duration).toInt()
                    }
                }
                delay(1000)
            }
        }
    }

    @OptIn(UnstableApi::class)
    private fun loadAndPlay(playerView: PlayerView, seekBar: SeekBar, btnPlayPause: ImageButton) {
        val id = recordingId ?: return

        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api     = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val recording = api.getRecording(id).data
                val url = recording.fileUrl

                if (url.isNullOrEmpty()) {
                    Toast.makeText(requireContext(), "URL de grabación no disponible", Toast.LENGTH_LONG).show()
                    return@launch
                }

                player = ExoPlayer.Builder(requireContext()).build().also { exo ->
                    playerView.player = exo
                    exo.setMediaItem(MediaItem.fromUri(url))
                    exo.prepare()
                    exo.playWhenReady = true
                    exo.addListener(object : Player.Listener {
                        override fun onIsPlayingChanged(isPlaying: Boolean) {
                            btnPlayPause.setImageResource(
                                if (isPlaying) R.drawable.ic_record_stop else R.drawable.ic_play
                            )
                        }
                    })
                }

            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error al cargar grabación: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        player?.release()
        player = null
    }
}
