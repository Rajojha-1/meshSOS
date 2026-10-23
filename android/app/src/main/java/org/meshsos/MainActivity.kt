package org.meshsos

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.meshsos.crypto.KeyManager
import org.meshsos.privacy.PrivacyManager
import org.meshsos.protocol.*
import org.meshsos.service.MeshForegroundService

/**
 * Main Android Activity for MeshSOS.
 * - Prominent SOS button
 * - "I'm OK now" button visible ONLY when an SOS is currently active.
 * - Stop Sharing control emitting LOWBAT with USER_STOP reason.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var btnSos: Button
    private lateinit var btnOkNow: Button
    private lateinit var btnStopSharing: Button
    private lateinit var tvStatusBadge: TextView
    private lateinit var tvPeersCount: TextView

    private var activeSosSeq: Int? = null
    private lateinit var keyManager: KeyManager
    private lateinit var privacyManager: PrivacyManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        keyManager = KeyManager()
        privacyManager = PrivacyManager(this)

        btnSos = findViewById(R.id.btn_sos)
        btnOkNow = findViewById(R.id.btn_ok_now)
        btnStopSharing = findViewById(R.id.btn_stop_sharing)
        tvStatusBadge = findViewById(R.id.tv_status_badge)
        tvPeersCount = findViewById(R.id.tv_peers_count)
        val btnServerSettings: Button = findViewById(R.id.btn_server_settings)

        checkAndRequestPermissions()
        startMeshService()

        btnServerSettings.setOnClickListener {
            showServerConfigDialog()
        }

        btnSos.setOnClickListener {
            triggerSos()
        }

        btnOkNow.setOnClickListener {
            triggerOkNow()
        }

        btnStopSharing.setOnClickListener {
            stopSharing()
        }
    }

    private fun triggerSos() {
        // Check if location is enabled — prompt user if not
        if (!isLocationEnabled()) {
            AlertDialog.Builder(this)
                .setTitle("Location Required")
                .setMessage("Your location services are OFF. Rescuers won't be able to find you without your location.\n\nPlease turn on Location to send an accurate SOS.")
                .setPositiveButton("Turn On Location") { _, _ ->
                    startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
                    Toast.makeText(this, "Turn on Location, then press SOS again", Toast.LENGTH_LONG).show()
                }
                .setNegativeButton("Send Anyway (No Location)") { _, _ ->
                    dispatchSos()
                }
                .setCancelable(false)
                .show()
            return
        }
        dispatchSos()
    }

    private fun isLocationEnabled(): Boolean {
        val lm = getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return false
        return lm.isProviderEnabled(LocationManager.GPS_PROVIDER) ||
               lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
    }

    private fun dispatchSos() {
        val seq = (1..65535).random()
        activeSosSeq = seq

        // Reveal "I'm OK now" button
        btnOkNow.visibility = View.VISIBLE
        btnSos.text = "SOS ACTIVE - DISPATCHED"
        btnSos.isEnabled = false
        tvStatusBadge.text = "DISTRESS"
        tvStatusBadge.setTextColor(0xFFEF4444.toInt())

        // 1. Check cached coordinates as instant fallback
        val prefs = getSharedPreferences("meshsos_config", Context.MODE_PRIVATE)
        val cachedLat = prefs.getFloat("cached_lat", 0f)
        val cachedLon = prefs.getFloat("cached_lon", 0f)

        // 2. Request active fresh GPS fix on demand
        try {
            val fusedClient = com.google.android.gms.location.LocationServices.getFusedLocationProviderClient(this)
            fusedClient.getCurrentLocation(com.google.android.gms.location.Priority.PRIORITY_HIGH_ACCURACY, null)
                .addOnSuccessListener { loc ->
                    val finalLat = if (loc != null && (loc.latitude != 0.0 || loc.longitude != 0.0)) loc.latitude.toFloat() else cachedLat
                    val finalLon = if (loc != null && (loc.latitude != 0.0 || loc.longitude != 0.0)) loc.longitude.toFloat() else cachedLon
                    sendSosIntent(seq, finalLat, finalLon)
                }
                .addOnFailureListener {
                    sendSosIntent(seq, cachedLat, cachedLon)
                }
        } catch (e: Exception) {
            sendSosIntent(seq, cachedLat, cachedLon)
        }

        val locWarning = if (!isLocationEnabled()) " (WARNING: Location is OFF)" else ""
        Toast.makeText(this, "Emergency SOS broadcast to mesh & gateway!$locWarning", Toast.LENGTH_LONG).show()
    }

    private fun sendSosIntent(seq: Int, lat: Float, lon: Float) {
        val intent = Intent(this, MeshForegroundService::class.java).apply {
            action = MeshForegroundService.ACTION_SEND_SOS
            putExtra("sos_text", "EMERGENCY DISTRESS CALL FROM DEVICE")
            putExtra("sos_seq", seq)
            if (lat != 0.0f || lon != 0.0f) {
                putExtra("lat", lat)
                putExtra("lon", lon)
            }
        }
        ContextCompat.startForegroundService(this, intent)
    }

    private fun triggerOkNow() {
        val targetSeq = activeSosSeq ?: return
        activeSosSeq = null

        val intent = Intent(this, MeshForegroundService::class.java).apply {
            action = MeshForegroundService.ACTION_SEND_OK_NOW
            putExtra("target_seq", targetSeq)
        }
        ContextCompat.startForegroundService(this, intent)

        // Cancel SOS state
        btnOkNow.visibility = View.GONE
        btnSos.text = "SEND SOS"
        btnSos.isEnabled = true
        tvStatusBadge.text = "ACTIVE"
        tvStatusBadge.setTextColor(0xFF10B981.toInt())

        Toast.makeText(this, "SOS Cancelled: OK_NOW broadcast to rescuers", Toast.LENGTH_LONG).show()
    }

    private fun stopSharing() {
        // Stop background service and mark stop sharing
        val intent = Intent(this, MeshForegroundService::class.java)
        stopService(intent)
        tvStatusBadge.text = "STOPPED"
        tvStatusBadge.setTextColor(0xFF64748B.toInt())
        Toast.makeText(this, "Mesh sharing stopped. LOWBAT beacon transmitted.", Toast.LENGTH_SHORT).show()
    }

    private fun startMeshService() {
        val intent = Intent(this, MeshForegroundService::class.java)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun checkAndRequestPermissions() {
        val permissions = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            permissions.add(Manifest.permission.BLUETOOTH_SCAN)
            permissions.add(Manifest.permission.BLUETOOTH_ADVERTISE)
            permissions.add(Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        val missing = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 101)
        } else {
            // Once foreground location is granted, request background location for service GPS
            requestBackgroundLocationIfNeeded()
            startMeshService()
        }

        // Request exemption from battery optimizations so service remains alive when app is closed / screen is off
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
            if (pm != null && !pm.isIgnoringBatteryOptimizations(packageName)) {
                try {
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                        data = Uri.parse("package:$packageName")
                    }
                    startActivity(intent)
                } catch (e: Exception) {}
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 101) {
            requestBackgroundLocationIfNeeded()
            startMeshService()
        } else if (requestCode == 102) {
            startMeshService()
        }
    }

    private fun requestBackgroundLocationIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION),
                    102
                )
            }
        }
    }

    private fun showServerConfigDialog() {
        val prefs = getSharedPreferences("meshsos_config", MODE_PRIVATE)
        val currentUrl = prefs.getString("backend_url", "https://meshsos.raj-ojha.in") ?: "https://meshsos.raj-ojha.in"

        val input = android.widget.EditText(this).apply {
            setText(currentUrl)
            hint = "https://meshsos.raj-ojha.in"
        }

        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("Gateway Server URL")
            .setMessage("Set the IP or URL of the MeshSOS command dashboard:")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                val newUrl = input.text.toString().trim()
                if (newUrl.isNotEmpty()) {
                    prefs.edit().putString("backend_url", newUrl).apply()
                    Toast.makeText(this, "Updated server URL: $newUrl", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
