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
 * Fragment base que añade el menú de 3 barras al toolbar nativo.
 *
 * Migrado de la API deprecated (setHasOptionsMenu + onCreateOptionsMenu +
 * onOptionsItemSelected) a la API moderna MenuProvider/MenuHost. La nueva
 * API es lifecycle-aware: pasamos viewLifecycleOwner + RESUMED para que el
 * menú solo esté activo cuando el fragment es visible, sin riesgos de leak.
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

    override fun onMenuItemSelected(menuItem: MenuItem): Boolean {
        val nav = findNavController()
        return when (menuItem.itemId) {
            R.id.menuGrabaciones -> {
                when (nav.currentDestination?.id) {
                    R.id.liveViewFragment   -> nav.navigate(R.id.action_liveView_to_recordings)
                    R.id.cameraListFragment -> nav.navigate(R.id.action_cameraList_to_recordings)
                    else                    -> nav.navigate(R.id.recordingsFragment)
                }
                true
            }
            R.id.menuNotificaciones -> {
                // Abre el PANEL de historial (NotificationsPanelFragment).
                // El CRUD de preferencias (EventConfigFragment) queda accesible
                // desde un botón "Configurar" dentro del panel.
                nav.navigate(R.id.notificationsPanelFragment)
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
