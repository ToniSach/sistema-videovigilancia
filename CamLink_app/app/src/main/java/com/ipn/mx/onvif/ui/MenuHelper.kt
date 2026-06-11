/*
 * ============================================================================
 * MÓDULO: MenuHelper — Helper de menú emergente (CamLink Android)
 * ============================================================================
 *
 * PROPÓSITO
 *   Mostrar un PopupMenu anclado a un botón con las acciones de directo
 *   (Grabaciones, Notificaciones, Cerrar sesión) y resolver su navegación
 *   según el destino actual, sin que cada pantalla reimplemente ese routing.
 *
 * RESPONSABILIDAD
 *   Inflar R.menu.menu_live_view sobre un anchor y, por ítem, elegir la acción
 *   de Navigation correcta en función del currentDestination (cada origen tiene
 *   su propia action hacia Grabaciones/Notificaciones).
 *
 * DEPENDENCIAS
 *   - PopupMenu (AppCompat) — menú anclado a una vista.
 *   - Navigation Component (findNavController) — navegación de los ítems.
 *
 * COMPONENTES RELACIONADOS
 *   - BaseMenuFragment — menú overflow del toolbar (camino alternativo/principal).
 *   - LiveViewFragment / CameraListFragment / RecordingsFragment — orígenes
 *     posibles desde los que se invoca este popup.
 *
 * PUNTO DE ENTRADA
 *   Objeto singleton; se llama MenuHelper.show(fragment, anchor) desde un botón.
 *
 * PIPELINE(S)
 *   #11/#12 Grabación/Clips · #13 Notificaciones · #2 Auth (cerrar sesión).
 * ============================================================================
 */
package com.ipn.mx.onvif.ui

import android.view.View
import android.widget.Toast
import androidx.appcompat.widget.PopupMenu
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R

/**
 * Helper singleton del menú emergente de acciones de directo.
 *
 * ROL: centraliza la construcción del PopupMenu y el routing de sus ítems,
 * consultando el destino actual para elegir la action de Navigation correcta.
 * QUIÉN LO CONSUME: las pantallas que exponen un botón de menú propio.
 */
object MenuHelper {

    /**
     * Infla y muestra el PopupMenu de acciones de directo anclado a una vista,
     * eligiendo la navegación de cada ítem según el destino actual del NavController.
     *
     * @param fragment fragment desde el que se invoca (aporta context + NavController).
     * @param anchor vista a la que se ancla el popup (normalmente el botón pulsado).
     */
    fun show(fragment: Fragment, anchor: View) {
        val popup = PopupMenu(fragment.requireContext(), anchor)
        popup.menuInflater.inflate(R.menu.menu_live_view, popup.menu)
        popup.setOnMenuItemClickListener { item ->
            val nav = fragment.findNavController()
            when (item.itemId) {

                R.id.menuGrabaciones -> {
                    val current = nav.currentDestination?.id
                    when (current) {
                        R.id.liveViewFragment ->
                            nav.navigate(R.id.action_liveView_to_recordings)
                        R.id.cameraListFragment ->
                            nav.navigate(R.id.action_cameraList_to_recordings)
                        else ->
                            nav.navigate(R.id.recordingsFragment)
                    }
                    true
                }

                R.id.menuNotificaciones -> {
                    val current = nav.currentDestination?.id
                    when (current) {
                        R.id.liveViewFragment ->
                            nav.navigate(R.id.action_liveView_to_eventConfig)
                        R.id.cameraListFragment ->
                            nav.navigate(R.id.action_cameraList_to_eventConfig)
                        R.id.recordingsFragment ->
                            nav.navigate(R.id.action_recordings_to_eventConfig)
                        else ->
                            nav.navigate(R.id.eventConfigFragment)
                    }
                    true
                }

                R.id.menuCerrarSesion -> {
                    nav.popBackStack(R.id.qrScanFragment, false)
                    true
                }

                else -> false
            }
        }
        popup.show()
    }
}