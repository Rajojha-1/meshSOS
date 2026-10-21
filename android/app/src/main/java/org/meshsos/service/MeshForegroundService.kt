package org.meshsos.service

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
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

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildForegroundNotification("MeshSOS active: scanning for peer beacons")
        startForeground(NOTIFICATION_ID, notification)

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MeshSOS::ServiceWakeLock").apply {
            acquire(10 * 60 * 1000L) // Bounded wakelock
        }

        privacyManager = PrivacyManager(this)
        batteryManager = BatteryManager(this)
        gatewayManager = GatewayManager(this)
        keyManager = KeyManager()

        val myNodeId = privacyManager.getOrRotateNodeId()
        relayCore = RelayCore(myNodeId)

        // Initialize BLE transport
        val bleTransport = BleTransport(this)
        transportManager = MultiTransportManager(listOf(bleTransport)) { msg, sender, rssi ->
            handleInboundMessage(msg, sender, rssi)
        }
        transportManager.startAll()

        // Schedule periodic heartbeat & relay queue processing
        scheduler.scheduleWithFixedDelay({ runTick() }, 5, 5, TimeUnit.SECONDS)
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
                NotificationManager.IMPORTANCE_LOW
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
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        super.onDestroy()
        scheduler.shutdown()
        transportManager.stopAll()
        wakeLock?.let { if (it.isHeld) it.release() }
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
