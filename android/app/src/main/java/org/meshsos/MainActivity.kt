package org.meshsos

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
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
        val nowSec = System.currentTimeMillis() / 1000L
        val seq = (1..65535).random()
        activeSosSeq = seq

        val intent = Intent(this, MeshForegroundService::class.java).apply {
            action = MeshForegroundService.ACTION_SEND_SOS
            putExtra("sos_text", "EMERGENCY DISTRESS CALL FROM DEVICE")
        }
        ContextCompat.startForegroundService(this, intent)

        // Reveal "I'm OK now" button
        btnOkNow.visibility = View.VISIBLE
        btnSos.text = "🚨 SOS ACTIVE - DISPATCHED"
        btnSos.isEnabled = false
        tvStatusBadge.text = "DISTRESS ACTIVE"
        tvStatusBadge.setTextColor(0xFFEF4444.toInt())

        Toast.makeText(this, "Emergency SOS broadcast to mesh & gateway!", Toast.LENGTH_LONG).show()
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
        btnSos.text = "🚨 SEND SOS"
        btnSos.isEnabled = true
        tvStatusBadge.text = "MESH ACTIVE"
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
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 101) {
            startMeshService()
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
