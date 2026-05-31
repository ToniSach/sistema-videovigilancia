package com.ipn.mx.onvif.ui

import android.view.View
import android.widget.Toast
import androidx.appcompat.widget.PopupMenu
import androidx.fragment.app.Fragment
import androidx.navigation.fragment.findNavController
import com.ipn.mx.onvif.R

object MenuHelper {

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