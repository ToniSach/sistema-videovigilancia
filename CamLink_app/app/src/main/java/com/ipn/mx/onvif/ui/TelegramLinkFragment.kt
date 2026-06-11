/*
 * ============================================================================
 * MÓDULO: TelegramLinkFragment — vinculación de Telegram del usuario
 *         (Pipeline #13 Notificaciones, canal Telegram, lado móvil)
 * ============================================================================
 *
 * PROPÓSITO
 *   Permitir al usuario enlazar su cuenta de Telegram con el sistema para recibir
 *   alertas por ese canal: genera un código en el backend, ofrece abrir el bot
 *   (deep link) con el comando copiado al portapapeles, y hace polling hasta
 *   confirmar la vinculación.
 *
 * RESPONSABILIDAD
 *   - Solicitar un código de vinculación (POST /telegram/generate-code).
 *   - Mostrar instrucciones y abrir el bot (deep link t.me) copiando "/vincular CODE".
 *   - Hacer polling del estado (GET /telegram/link-status) cada 2.5s hasta que
 *     linked=true o expired=true.
 *   - Gestionar casos: bot no configurado, sesión expirada, código caducado.
 *
 * DEPENDENCIAS
 *   - network/RetrofitClient + ApiService (generateTelegramCode, telegramLinkStatus).
 *   - Coroutines (lifecycleScope) para el polling cancelable.
 *
 * COMPONENTES RELACIONADOS
 *   - Backend de Telegram (bot + endpoints de vinculación).
 *   - EventConfigFragment (donde el usuario activa el canal "telegram" por evento).
 *
 * PUNTO DE ENTRADA
 *   Destino de navegación abierto desde el menú/toolbar; sin argumentos.
 *
 * PIPELINE(S)
 *   #13 Notificaciones — configuración del canal Telegram.
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
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
 *
 * Ciclo de vida: arranca generando código en onViewCreated; cancela el job de
 * polling en onDestroyView para no fugar la coroutine. Quién lo instancia: el
 * Navigation Component al abrir la pantalla desde el menú.
 *
 * Pipeline: #13 Notificaciones (canal Telegram).
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

    /**
     * Pide un código nuevo al backend, lo pinta junto con las instrucciones y
     * arranca el polling de estado. Maneja sesión expirada (401) y bot no
     * configurado en el servidor.
     * Endpoint: POST /telegram/generate-code.
     * Llamado por: onViewCreated y el botón "Generar código nuevo".
     * Llama a: startPolling.
     */
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
                // Instrucciones SIEMPRE explícitas sobre el paso manual: el
                // deep link de Telegram sólo auto-envia /start la PRIMERA vez
                // que abres el bot; si ya lo habias abierto antes, hay que
                // enviar el comando a mano. Por eso al pulsar "Abrir Telegram"
                // copiamos "/vincular CODE" al portapapeles.
                tvInstructions.text =
                    "1. Pulsa \"Abrir Telegram\" (copiaremos el comando).\n" +
                    "2. En el chat del bot, pega y envia: /vincular ${data.code}\n" +
                    "3. Espera la confirmacion aqui mismo."
                btnOpenTelegram.isEnabled = true
                tvStatus.text = "Esperando vinculacion..."

                startPolling(data.code)
            } catch (e: Exception) {
                tvStatus.text = "Error de conexion: ${e.message}"
            }
        }
    }

    /**
     * Sondea el estado de vinculación cada 2.5s hasta linked=true (éxito) o
     * expired=true (caducado), o hasta que se cancele la coroutine. Errores de red
     * se reintentan en silencio en el siguiente ciclo.
     * @param code código de vinculación a consultar.
     * Endpoint: GET /telegram/link-status?code=CODE.
     * Llamado por: generateCode.
     */
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

    /**
     * Abre el bot de Telegram (deep link o https://t.me/usuario) y copia
     * "/vincular CODE" al portapapeles para que el usuario sólo lo pegue y envíe
     * (cubre el caso en que el bot ya fue iniciado y el deep link no auto-envía).
     * Llamado por: el botón "Abrir Telegram".
     */
    private fun openTelegram() {
        val link = deepLink
            ?: botUsername?.let { "https://t.me/$it" }
            ?: run {
                Toast.makeText(requireContext(), "Deep link no disponible", Toast.LENGTH_SHORT).show()
                return
            }

        // Copiar "/vincular CODE" al portapapeles para que el usuario solo
        // tenga que pegarlo y enviarlo. Esto resuelve el caso en que el bot
        // ya fue iniciado antes y el deep link no auto-envia /start CODE.
        currentCode?.let { code ->
            try {
                val cmd = "/vincular $code"
                val cb = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cb.setPrimaryClip(ClipData.newPlainText("vincular", cmd))
                Toast.makeText(
                    requireContext(),
                    "Comando copiado: pegalo y envialo en el chat del bot",
                    Toast.LENGTH_LONG
                ).show()
            } catch (_: Exception) { /* no critico */ }
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
