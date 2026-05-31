package com.ipn.mx.onvif.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.RetrofitClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Vinculación de la cuenta de Telegram del usuario.
 *
 * Flujo (usa el backend existente):
 *   1. POST /telegram/generate-code → {code, telegram_deep_link, instrucciones}
 *   2. El usuario toca "Abrir Telegram" (deep link t.me/bot?start=CODE) o envía
 *      manualmente "/vincular CODE" al bot.
 *   3. Polling GET /telegram/link-status?code=CODE cada 2.5s hasta linked=true.
 */
class TelegramLinkFragment : Fragment() {

    private lateinit var tvCode: TextView
    private lateinit var tvExpiry: TextView
    private lateinit var tvInstructions: TextView
    private lateinit var tvStatus: TextView
    private lateinit var btnOpenTelegram: MaterialButton
    private lateinit var btnRegenerate: MaterialButton

    private var currentCode: String? = null
    private var deepLink: String? = null
    private var botUsername: String? = null
    private var pollJob: Job? = null

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_telegram_link, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        tvCode          = view.findViewById(R.id.tvCode)
        tvExpiry        = view.findViewById(R.id.tvExpiry)
        tvInstructions  = view.findViewById(R.id.tvInstructions)
        tvStatus        = view.findViewById(R.id.tvStatus)
        btnOpenTelegram = view.findViewById(R.id.btnOpenTelegram)
        btnRegenerate   = view.findViewById(R.id.btnRegenerate)

        btnOpenTelegram.setOnClickListener { openTelegram() }
        btnRegenerate.setOnClickListener { generateCode() }

        generateCode()
    }

    private fun generateCode() {
        pollJob?.cancel()
        tvCode.text = "..."
        tvStatus.text = "Generando codigo..."
        btnOpenTelegram.isEnabled = false

        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: run {
            tvStatus.text = "Sin URL de servidor"
            return
        }
        val api = RetrofitClient.create(baseUrl, requireContext())

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = api.generateTelegramCode()
                val data = resp.body()?.data
                if (!resp.isSuccessful || data == null) {
                    val code = resp.code()
                    tvStatus.text = if (code == 401) "Sesion expirada, vuelve a vincular el dispositivo"
                                    else "No se pudo generar el codigo (HTTP $code)"
                    return@launch
                }

                if (!data.botConfigured) {
                    tvCode.text = "------"
                    tvStatus.text = "El bot de Telegram no esta configurado en el servidor. " +
                            "Pidele al administrador que lo configure desde la app de escritorio."
                    return@launch
                }

                currentCode = data.code
                deepLink     = data.telegramDeepLink
                botUsername  = data.botUsername

                tvCode.text = data.code
                tvExpiry.text = "Valido por ${data.expiresInSeconds / 60} min"
                tvInstructions.text = if (data.instructions.isNotEmpty())
                    data.instructions.mapIndexed { i, s -> "${i + 1}. $s" }.joinToString("\n")
                else
                    "1. Abre Telegram y busca @${data.botUsername}\n" +
                    "2. Enviale: /vincular ${data.code}\n" +
                    "3. Espera la confirmacion aqui"
                btnOpenTelegram.isEnabled = true
                tvStatus.text = "Esperando vinculacion..."

                startPolling(data.code)
            } catch (e: Exception) {
                tvStatus.text = "Error de conexion: ${e.message}"
            }
        }
    }

    private fun startPolling(code: String) {
        pollJob?.cancel()
        val baseUrl = RetrofitClient.buildBaseUrl(requireContext()) ?: return
        val api = RetrofitClient.create(baseUrl, requireContext())

        pollJob = viewLifecycleOwner.lifecycleScope.launch {
            while (isActive) {
                delay(2500)
                try {
                    val resp = api.telegramLinkStatus(code)
                    val data = resp.body()?.data ?: continue
                    if (data.linked) {
                        tvStatus.text = "Telegram vinculado correctamente. Ya recibiras las alertas."
                        tvStatus.setTextColor(0xFF22C55E.toInt())
                        Toast.makeText(requireContext(), "Telegram vinculado", Toast.LENGTH_LONG).show()
                        return@launch
                    }
                    if (data.expired) {
                        tvStatus.text = "El codigo expiro. Pulsa \"Generar codigo nuevo\"."
                        tvStatus.setTextColor(0xFFEF4444.toInt())
                        return@launch
                    }
                } catch (_: Exception) {
                    // Reintento silencioso en el siguiente ciclo (red inestable).
                }
            }
        }
    }

    private fun openTelegram() {
        val link = deepLink
            ?: botUsername?.let { "https://t.me/$it" }
            ?: run {
                Toast.makeText(requireContext(), "Deep link no disponible", Toast.LENGTH_SHORT).show()
                return
            }
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link)))
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "No se pudo abrir Telegram: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        pollJob?.cancel()
        pollJob = null
    }
}
