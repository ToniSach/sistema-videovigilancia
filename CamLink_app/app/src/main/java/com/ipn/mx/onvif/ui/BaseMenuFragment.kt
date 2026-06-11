/*
 * ============================================================================
 * MÓDULO: BaseMenuFragment — Fragment base con menú compartido (CamLink Android)
 * ============================================================================
 *
 * PROPÓSITO
 *   Clase base de los fragments con sesión iniciada que necesitan el menú
 *   "overflow" del toolbar (Configurar notificaciones, Vincular Telegram,
 *   Cerrar sesión), para no duplicar ese cableado en cada pantalla.
 *
 * RESPONSABILIDAD
 *   - Registrar un MenuProvider lifecycle-aware (API moderna, no la deprecated
 *     setHasOptionsMenu) ligado a viewLifecycleOwner + RESUMED.
 *   - Inflar R.menu.menu_main y enrutar sus ítems por Navigation.
 *   - Implementar "Cerrar sesión": parar el WS de notificaciones, borrar token
 *     y prefs, y volver al QR (login).
 *
 * DEPENDENCIAS
 *   - Navigation Component (findNavController) — navegación de los ítems.
 *   - RetrofitClient.clearToken — limpieza de sesión.
 *   - NotificationWebSocketService.stop — detener push antes de borrar token.
 *
 * COMPONENTES RELACIONADOS
 *   - LiveViewFragment y CameraListFragment heredan de esta clase.
 *   - MenuHelper — menú alternativo (PopupMenu) para acciones específicas.
 *
 * PUNTO DE ENTRADA
 *   Clase abstracta: no es un destino de navegación; se extiende.
 *
 * PIPELINE(S)
 *   #2 Auth (cierre de sesión) · #13 Notificaciones (parada del WS).
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.view.Menu
import android.view.MenuInflater
import android.view.MenuItem
import android.view.View
import androidx.core.view.MenuHost
import androidx.core.view.MenuProvider
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R
import com.ipn.mx.onvif.network.RetrofitClient
import com.ipn.mx.onvif.service.NotificationWebSocketService

/**
 * Fragment base que añade el menú "overflow" (3 puntos) al toolbar nativo.
 *
 * ROL Y RESPONSABILIDAD
 *   Clase abstracta de la que heredan las pantallas con sesión (LiveView,
 *   CameraList) para compartir el menú de opciones y la lógica de cierre de
 *   sesión, sin repetir el cableado del MenuProvider en cada una.
 *
 * QUIÉN LA CONSUME
 *   La extienden los fragments de la app; el menú se registra contra el
 *   MenuHost (la Activity host) en onViewCreated.
 *
 * CICLO DE VIDA ANDROID RELEVANTE
 *   Migrado de la API deprecated (setHasOptionsMenu + onCreateOptionsMenu +
 *   onOptionsItemSelected) a la API moderna MenuProvider/MenuHost, que es
 *   lifecycle-aware: se pasa viewLifecycleOwner + RESUMED para que el menú
 *   solo esté activo cuando el fragment es visible, sin riesgos de leak.
 *
 * PIPELINE
 *   #2 Auth (cierre de sesión) · #13 Notificaciones (parada del WS).
 */
abstract class BaseMenuFragment : Fragment(), MenuProvider {

    override fun onViewCreated(view: View, savedInstanceState: android.os.Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        // Registrar como proveedor de menú vinculado al ciclo de vida de la vista.
        (requireActivity() as MenuHost).addMenuProvider(
            this, viewLifecycleOwner, Lifecycle.State.RESUMED
        )
    }

    // ── MenuProvider ──────────────────────────────────────────────────────────

    override fun onCreateMenu(menu: Menu, menuInflater: MenuInflater) {
        menuInflater.inflate(R.menu.menu_main, menu)
    }

    /**
     * Enruta el ítem de menú pulsado: a config de notificaciones, a vincular
     * Telegram o a cerrar sesión (para el WS, limpia token+prefs y vuelve al QR).
     *
     * @param menuItem ítem seleccionado de R.menu.menu_main.
     * @return true si se consumió el ítem; false para los no reconocidos.
     * Llamado por: el MenuHost cuando el usuario toca el menú overflow.
     */
    override fun onMenuItemSelected(menuItem: MenuItem): Boolean {
        val nav = findNavController()
        return when (menuItem.itemId) {
            R.id.menuConfigNotificaciones -> {
                // CRUD de preferencias (qué eventos, canales, días).
                nav.navigate(R.id.eventConfigFragment)
                true
            }
            R.id.menuVincularTelegram -> {
                nav.navigate(R.id.telegramLinkFragment)
                true
            }
            R.id.menuCerrarSesion -> {
                // Parar el WS de notificaciones ANTES de borrar el token: si
                // lo hacemos al revés, el WS intentará reconectar con baseUrl
                // nulo y mete logs ruidosos.
                try {
                    NotificationWebSocketService.stop(requireContext().applicationContext)
                } catch (e: Exception) {
                    android.util.Log.w("BaseMenuFragment", "stop WS: ${e.message}")
                }
                RetrofitClient.clearToken(requireContext())

                val prefs = requireContext().getSharedPreferences(
                    "auth_prefs", android.content.Context.MODE_PRIVATE
                )
                prefs.edit().clear().apply()

                nav.navigate(R.id.qrScanFragment)
                true
            }
            else -> false
        }
    }
}
