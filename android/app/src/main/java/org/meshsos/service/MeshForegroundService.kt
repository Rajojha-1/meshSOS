package org.meshsos.service

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import org.meshsos.battery.BatteryManager
import org.meshsos.crypto.KeyManager
import org.meshsos.gateway.GatewayManager
import org.meshsos.privacy.PrivacyManager
import org.meshsos.protocol.*
import org.meshsos.relay.PriorityLevel
import org.meshsos.relay.RelayCore
import org.meshsos.transport.BleTransport
import org.meshsos.transport.MultiTransportManager
import java.util.*
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Foreground Service running MeshSOS mesh radio and relay logic.
 * Declares android:foregroundServiceType="connectedDevice" for Android 14+ compatibility.
 */
class MeshForegroundService : Service() {

    private val CHANNEL_ID = "meshsos_channel"
    private val NOTIFICATION_ID = 911

    private lateinit var relayCore: RelayCore
    private lateinit var transportManager: MultiTransportManager
    private lateinit var batteryManager: BatteryManager
    private lateinit var gatewayManager: GatewayManager
    private lateinit var privacyManager: PrivacyManager
    private lateinit var keyManager: KeyManager

    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private var wakeLock: PowerManager.WakeLock? = null
    private var currentSeq = 1

    private var locListener: android.location.LocationListener? = null
    @Volatile
    private var lastKnownLocation: Pair<Double, Double>? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildForegroundNotification("MeshSOS active: scanning for peer beacons")
        startForeground(NOTIFICATION_ID, notification)

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MeshSOS::ServiceWakeLock").apply {
            setReferenceCounted(false)
            acquire()
        }

        privacyManager = PrivacyManager(this)
        batteryManager = BatteryManager(this)
        gatewayManager = GatewayManager(this)
        keyManager = KeyManager()

        initLocationTracking()

        val myNodeId = privacyManager.getOrRotateNodeId()
        relayCore = RelayCore(myNodeId)

        // Initialize BLE transport
        val bleTransport = BleTransport(this)
        transportManager = MultiTransportManager(listOf(bleTransport)) { msg, sender, rssi ->
            handleInboundMessage(msg, sender, rssi)
        }
        transportManager.startAll()

        // Schedule periodic heartbeat & relay queue processing (immediate 1s startup)
        scheduler.scheduleWithFixedDelay({ runTick() }, 1, 5, TimeUnit.SECONDS)
    }

    private fun initLocationTracking() {
        try {
            val lm = getSystemService(Context.LOCATION_SERVICE) as? android.location.LocationManager ?: return
            
            // Check cached positions
            val locGps = lm.getLastKnownLocation(android.location.LocationManager.GPS_PROVIDER)
            val locNet = lm.getLastKnownLocation(android.location.LocationManager.NETWORK_PROVIDER)
            val best = locGps ?: locNet
            if (best != null) {
                lastKnownLocation = Pair(best.latitude, best.longitude)
                gatewayManager.updateLocation(best.latitude, best.longitude)
            }

            val listener = object : android.location.LocationListener {
                override fun onLocationChanged(loc: android.location.Location) {
                    lastKnownLocation = Pair(loc.latitude, loc.longitude)
                    gatewayManager.updateLocation(loc.latitude, loc.longitude)
                    getSharedPreferences("meshsos_config", Context.MODE_PRIVATE).edit()
                        .putFloat("cached_lat", loc.latitude.toFloat())
                        .putFloat("cached_lon", loc.longitude.toFloat())
                        .apply()
                }
                override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
                override fun onProviderEnabled(provider: String) {}
                override fun onProviderDisabled(provider: String) {}
            }
            locListener = listener

            if (lm.isProviderEnabled(android.location.LocationManager.GPS_PROVIDER)) {
                lm.requestLocationUpdates(android.location.LocationManager.GPS_PROVIDER, 3000L, 2.0f, listener, android.os.Looper.getMainLooper())
            }
            if (lm.isProviderEnabled(android.location.LocationManager.NETWORK_PROVIDER)) {
                lm.requestLocationUpdates(android.location.LocationManager.NETWORK_PROVIDER, 3000L, 2.0f, listener, android.os.Looper.getMainLooper())
            }
        } catch (e: SecurityException) {
            // Permission pending from user
        } catch (e: Exception) {}
    }

    companion object {
        const val ACTION_SEND_SOS = "org.meshsos.ACTION_SEND_SOS"
        const val ACTION_SEND_OK_NOW = "org.meshsos.ACTION_SEND_OK_NOW"
    }

    private var lastHeartbeatMs: Long = 0L

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action == ACTION_SEND_SOS) {
            val text = intent.getStringExtra("sos_text") ?: "EMERGENCY HELP NEEDED"
            val (currLat, currLon) = getBestLocation()
            gatewayManager.updateLocation(currLat, currLon)
            val lat = intent.getFloatExtra("lat", currLat.toFloat())
            val lon = intent.getFloatExtra("lon", currLon.toFloat())
            val nowMs = System.currentTimeMillis()
            val dummyPub = ByteArray(32) { 0x42 }
            val sos = SosMessage(
                header = Header(1, MessageType.SOS, relayCore.nodeId, nextSeq(), 8, nowMs / 1000L),
                lat = lat,
                lon = lon,
                accuracy = 5.0f,
                category = SosCategory.OTHER,
                battery = batteryManager.getBatteryPercent(),
                text = text,
                pubkey = dummyPub,
                signature = ByteArray(64) { 0x01 }
            )
            try {
                sos.signature = keyManager.signData(sos.unsignedPayloadBytes())
            } catch (e: Exception) {}

            transportManager.send(sos, PriorityLevel.SOS)
            if (gatewayManager.checkUplinkAvailable()) {
                gatewayManager.queueForUplink(sos)
                scheduler.execute { runTick() }
            }
        } else if (action == ACTION_SEND_OK_NOW) {
            val targetSeq = intent.getIntExtra("target_seq", 1)
            val nowMs = System.currentTimeMillis()
            val dummyPub = ByteArray(32) { 0x42 }
            val ok = OkNowMessage(
                header = Header(1, MessageType.OK_NOW, relayCore.nodeId, nextSeq(), 8, nowMs / 1000L),
                targetSosSeq = targetSeq,
                battery = batteryManager.getBatteryPercent(),
                text = "I'm OK now",
                pubkey = dummyPub,
                signature = ByteArray(64) { 0x01 }
            )
            try {
                ok.signature = keyManager.signData(ok.unsignedPayloadBytes())
            } catch (e: Exception) {}

            transportManager.send(ok, PriorityLevel.OK_NOW)
            if (gatewayManager.checkUplinkAvailable()) {
                gatewayManager.queueForUplink(ok)
                scheduler.execute { runTick() }
            }
        }
        return START_STICKY
    }

    private fun handleInboundMessage(message: MeshMessage, senderId: Long, rssi: Float) {
        val nowMs = System.currentTimeMillis()
        val queued = relayCore.onReceive(message, nowMs, randomFraction = Math.random())

        if (gatewayManager.checkUplinkAvailable()) {
            gatewayManager.queueForUplink(message)
        }
    }

    private fun runTick() {
        val nowMs = System.currentTimeMillis()
        val batteryPct = batteryManager.getBatteryPercent()

        // 1. Critical Battery Check
        if (batteryManager.checkCriticalBattery(batteryPct)) {
            val lowbat = LowbatMessage(
                header = Header(1, MessageType.LOWBAT, relayCore.nodeId, nextSeq(), 5, nowMs / 1000L),
                reason = LowbatReason.BATTERY_DEPLETED,
                battery = batteryPct,
                lastGridX = 0,
                lastGridY = 0
            )
            transportManager.send(lowbat, PriorityLevel.LOWBAT)
            stopSelf()
            return
        }

        // 2. Transmit periodic heartbeat if interval elapsed
        val neighbours = transportManager.getAllNeighbours()
        val trickleInterval = batteryManager.updateTrickleInterval(neighbours.keys)

        if (nowMs - lastHeartbeatMs >= trickleInterval || lastHeartbeatMs == 0L) {
            lastHeartbeatMs = nowMs
            val digest = neighbours.keys.take(3).map { (it and 0xFFFF).toInt() }
            val (currLat, currLon) = getBestLocation()
            val (gx, gy) = privacyManager.quantizeToGridCell(currLat, currLon)
            val hb = HeartbeatMessage(
                header = Header(1, MessageType.HEARTBEAT, relayCore.nodeId, nextSeq(), 8, nowMs / 1000L),
                gridX = gx,
                gridY = gy,
                battery = batteryPct,
                flags = 0,
                neighbours = digest
            )
            transportManager.send(hb, PriorityLevel.HEARTBEAT)
            if (gatewayManager.checkUplinkAvailable()) {
                gatewayManager.queueForUplink(hb)
            }
        }

        // Check and pop any matured relay transmissions
        var readyMsg = relayCore.popReadyTransmission(nowMs)
        while (readyMsg != null) {
            val prio = PriorityLevel.fromType(readyMsg.header.type)
            transportManager.send(readyMsg, prio)
            readyMsg = relayCore.popReadyTransmission(nowMs)
        }

        // 3. Flush gateway batch if connected
        if (gatewayManager.checkUplinkAvailable()) {
            val acks = gatewayManager.flushBatchToBackend(relayCore.nodeId)
            acks.forEach { ack ->
                relayCore.onReceive(ack, nowMs)
                transportManager.send(ack, PriorityLevel.SOS)
            }
        }
    }

    private fun nextSeq(): Int {
        currentSeq = (currentSeq + 1) and 0xFFFF
        if (currentSeq == 0) currentSeq = 1
        return currentSeq
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "MeshSOS Emergency Network",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Keeps peer-to-peer disaster mesh alive with low-power BLE presence"
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildForegroundNotification(statusText: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("MeshSOS Mesh Active")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setOngoing(true)
            .build()
    }

    private fun getBestLocation(): Pair<Double, Double> {
        lastKnownLocation?.let { if (it.first != 0.0 || it.second != 0.0) return it }
        try {
            val lm = getSystemService(Context.LOCATION_SERVICE) as? android.location.LocationManager
            if (lm != null) {
                val locGps = lm.getLastKnownLocation(android.location.LocationManager.GPS_PROVIDER)
                val locNet = lm.getLastKnownLocation(android.location.LocationManager.NETWORK_PROVIDER)
                val locPass = lm.getLastKnownLocation(android.location.LocationManager.PASSIVE_PROVIDER)
                val best = locGps ?: locNet ?: locPass
                if (best != null) {
                    val pair = Pair(best.latitude, best.longitude)
                    lastKnownLocation = pair
                    return pair
                }
            }
        } catch (e: SecurityException) {
            // Fallback when permission or sensor pending
        }

        // Try cached coords from previous run
        val prefs = getSharedPreferences("meshsos_config", Context.MODE_PRIVATE)
        val cachedLat = prefs.getFloat("cached_lat", 0f).toDouble()
        val cachedLon = prefs.getFloat("cached_lon", 0f).toDouble()
        if (cachedLat != 0.0 && cachedLon != 0.0) {
            return Pair(cachedLat, cachedLon)
        }

        return Pair(28.6692, 77.4538) // Initial center only if brand-new install with GPS off
    }

    override fun onDestroy() {
        super.onDestroy()
        locListener?.let {
            val lm = getSystemService(Context.LOCATION_SERVICE) as? android.location.LocationManager
            try { lm?.removeUpdates(it) } catch (e: Exception) {}
        }
        scheduler.shutdown()
        transportManager.stopAll()
        wakeLock?.let { if (it.isHeld) it.release() }
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
