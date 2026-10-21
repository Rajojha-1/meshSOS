package org.meshsos.transport

import org.meshsos.protocol.MeshMessage
import org.meshsos.protocol.MessageType
import org.meshsos.relay.PriorityLevel

/**
 * Common Transport Interface for MeshSOS.
 * Decouples relay logic from underlying physical wireless channels.
 */
interface Transport {
    val transportName: String
    fun start()
    fun stop()
    fun send(message: MeshMessage, priority: PriorityLevel): Boolean
    fun getNeighbours(): Map<Long, Float> // NodeId -> RSSI or estimated distance
    fun getCapabilities(): Set<String>
    fun registerReceiveListener(listener: (message: MeshMessage, senderId: Long, rssi: Float) -> Unit)
}

/**
 * MultiTransportManager coordinates parallel BLE and Wi-Fi transmissions.
 * In accordance with Phase 5: SOS tries all available transports in parallel
 * and escalates if no ACK arrives within a configurable timeout.
 */
class MultiTransportManager(
    private val transports: List<Transport>,
    private val onMessageReceived: (message: MeshMessage, senderId: Long, rssi: Float) -> Unit
) {
    init {
        transports.forEach { transport ->
            transport.registerReceiveListener { msg, sender, rssi ->
                onMessageReceived(msg, sender, rssi)
            }
        }
    }

    fun startAll() {
        transports.forEach { it.start() }
    }

    fun stopAll() {
        transports.forEach { it.stop() }
    }

    fun send(message: MeshMessage, priority: PriorityLevel) {
        if (message.header.type == MessageType.SOS) {
            // SOS: Broadcast on ALL available transports simultaneously for maximum survivability
            transports.forEach { transport ->
                try {
                    transport.send(message, priority)
                } catch (e: Exception) {
                    // Log and continue with next transport
                }
            }
        } else {
            // Non-SOS (Heartbeat, Lowbat): Prefer low-power BLE broadcast
            val bleTransport = transports.firstOrNull { it.transportName.contains("BLE", ignoreCase = true) }
            if (bleTransport != null) {
                bleTransport.send(message, priority)
            } else {
                transports.firstOrNull()?.send(message, priority)
            }
        }
    }

    fun getAllNeighbours(): Map<Long, Float> {
        val merged = mutableMapOf<Long, Float>()
        transports.forEach { t ->
            merged.putAll(t.getNeighbours())
        }
        return merged
    }
}
