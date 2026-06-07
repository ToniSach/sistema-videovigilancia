package com.ipn.mx.onvif

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.AppBarConfiguration
import androidx.navigation.ui.setupActionBarWithNavController
import androidx.navigation.ui.setupWithNavController
import com.google.android.material.appbar.AppBarLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.ipn.mx.onvif.network.JwtAuthenticator
import com.ipn.mx.onvif.network.RetrofitClient
import com.ipn.mx.onvif.service.NotificationWebSocketService

class MainActivity : AppCompatActivity() {

    private val notifPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        // No bloqueamos la app si el usuario rechaza; las notif simplemente
        // no aparecerán. El servicio WS sigue corriendo igual.
        android.util.Log.i("MainActivity", "POST_NOTIFICATIONS granted=$granted")
    }

    /**
     * Cuando JwtAuthenticator no consigue refrescar el token (refresh_token
     * expirado o revocado), emite este broadcast y aquí navegamos al QR para
     * que el usuario re-inicie sesión. Toast informativo para que entienda
     * por qué saltó del live al login.
     */
    private val sessionExpiredReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            android.util.Log.w("MainActivity", "SESSION_EXPIRED recibido")
            runOnUiThread {
                Toast.makeText(
                    this@MainActivity,
                    "Sesión expirada, vuelve a iniciar sesión",
                    Toast.LENGTH_LONG,
                ).show()
                // Parar el WS service y limpiar token+prefs antes de navegar
                try { NotificationWebSocketService.stop(applicationContext) } catch (_: Exception) {}
                RetrofitClient.clearToken(this@MainActivity)
                getSharedPreferences("auth_prefs", Context.MODE_PRIVATE).edit().clear().apply()
                try {
                    val navHostFragment = supportFragmentManager
                        .findFragmentById(R.id.navHostFragment) as NavHostFragment
                    navHostFragment.navController.navigate(R.id.qrScanFragment)
                } catch (e: Exception) {
                    android.util.Log.e("MainActivity", "No pude navegar a QR: ${e.message}")
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Toolbar propia como ActionBar de soporte (el tema es NoActionBar).
        // Sin esto, setupActionBarWithNavController lanzaba IllegalStateException
        // y, sobre todo, el menú (Notificaciones/Telegram/Grabaciones) no tenía
        // dónde mostrarse.
        val toolbar = findViewById<MaterialToolbar>(R.id.topToolbar)
        setSupportActionBar(toolbar)

        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.navHostFragment) as NavHostFragment
        val navController = navHostFragment.navController

        // Destinos de primer nivel (pestañas de la barra inferior): sin flecha
        // de atrás. El resto (playback, telegram, config) sí muestran la flecha.
        val appBarConfig = AppBarConfiguration(
            setOf(
                R.id.liveViewFragment,
                R.id.cameraListFragment,
                R.id.timelineFragment,
                R.id.notificationsPanelFragment,
            )
        )
        setupActionBarWithNavController(navController, appBarConfig)

        // Barra de navegación inferior enlazada al NavController (los IDs del
        // menú coinciden con los destinos → navega sola al tocar cada pestaña).
        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNav)
        bottomNav.setupWithNavController(navController)

        // En la pantalla de login (QR) ocultamos toolbar + barra inferior para
        // una experiencia limpia de "fuera de sesión".
        val appBar = findViewById<AppBarLayout>(R.id.appBar)
        navController.addOnDestinationChangedListener { _, destination, _ ->
            val isLogin = destination.id == R.id.qrScanFragment
            appBar.visibility = if (isLogin) android.view.View.GONE else android.view.View.VISIBLE
            bottomNav.visibility = if (isLogin) android.view.View.GONE else android.view.View.VISIBLE
        }

        // Link de notificación: si la app se abrió tocando una push de evento,
        // navegar al timeline de esa cámara (la push lleva extras openCameraId).
        handleNotificationIntent(navController)

        maybeRequestNotificationPermission()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        try {
            val navHostFragment = supportFragmentManager
                .findFragmentById(R.id.navHostFragment) as NavHostFragment
            handleNotificationIntent(navHostFragment.navController)
        } catch (_: Exception) {}
    }

    /** Si el intent trae openCameraId (de una notificación push), abre la
     *  grabación del evento de esa cámara/fecha y la reproduce en cadena. */
    private fun handleNotificationIntent(navController: androidx.navigation.NavController) {
        val camId = intent?.getIntExtra("openCameraId", -1) ?: -1
        if (camId <= 0) return
        // Consumir los extras para no re-navegar en rotaciones.
        intent.removeExtra("openCameraId")
        val date = intent.getStringExtra("openDate")    // YYYY-MM-DD, opcional
        val time = intent.getStringExtra("openTime")    // ISO, opcional
        intent.removeExtra("openTime")
        if (date.isNullOrBlank()) return
        val args = Bundle().apply {
            putInt("cameraId", camId)
            putString("date", date)
            putString("mode", "event")
            if (!time.isNullOrBlank()) putString("targetTime", time)
        }
        try {
            navController.navigate(R.id.playbackFragment, args)
        } catch (e: Exception) {
            android.util.Log.w("MainActivity", "No pude abrir grabación desde notif: ${e.message}")
        }
    }

    override fun onStart() {
        super.onStart()
        // Registrar el receiver SOLO mientras la activity está visible
        // (no recibimos broadcasts si la app está cerrada — el WS service
        // se encarga en background con su propia lógica de reconexión).
        val filter = IntentFilter(JwtAuthenticator.ACTION_SESSION_EXPIRED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(sessionExpiredReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(sessionExpiredReceiver, filter)
        }
    }

    override fun onStop() {
        try { unregisterReceiver(sessionExpiredReceiver) } catch (_: Exception) {}
        super.onStop()
    }

    /**
     * Pide POST_NOTIFICATIONS si estamos en Android 13+ y aún no la tenemos.
     * Sin este permiso el foreground service corre pero el usuario no ve nada.
     */
    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ActivityCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            notifPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.navHostFragment) as NavHostFragment
        return navHostFragment.navController.navigateUp() || super.onSupportNavigateUp()
    }
}