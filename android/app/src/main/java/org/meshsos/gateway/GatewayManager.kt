package org.meshsos.gateway

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import org.meshsos.protocol.MeshMessage
import org.meshsos.protocol.AckMessage
import org.meshsos.protocol.Header
import org.meshsos.protocol.MessageType
import org.meshsos.protocol.AckStatus
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * Gateway Manager implementing Phase 7:
 * - Gateway election by battery and uplink availability.
 * - Batches peer message digests into compact HTTP uplinks to the backend.
 * - Ingests server ACKs and floods them back through the local mesh.
 */
class GatewayManager(
    private val context: Context,
    defaultUrl: String = "https://meshsos.raj-ojha.in"
) {
    var backendBaseUrl: String
        get() {
            val saved = context.getSharedPreferences("meshsos_config", Context.MODE_PRIVATE)
                .getString("backend_url", null)
            return if (!saved.isNullOrBlank() && !saved.contains("10.0.2.2") && !saved.contains("localhost")) {
                saved
            } else {
                "https://meshsos.raj-ojha.in"
            }
        }
        set(value) {
            context.getSharedPreferences("meshsos_config", Context.MODE_PRIVATE)
                .edit()
                .putString("backend_url", value)
                .apply()
        }

    private val pendingUplinkQueue = ConcurrentLinkedQueue<MeshMessage>()
    private var isGatewayElected = false

    @Volatile
    private var lastLat: Double? = null
    @Volatile
    private var lastLon: Double? = null

    fun updateLocation(lat: Double, lon: Double) {
        if (lat != 0.0 && lon != 0.0) {
            lastLat = lat
            lastLon = lon
        }
    }

    fun checkUplinkAvailable(): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
        val network = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    /**
     * Computes gateway suitability score in [0.0, 1.0].
     * Higher score wins gateway role among cluster peers.
     */
    fun computeGatewayScore(batteryPercent: Int, hasInternet: Boolean): Double {
        if (!hasInternet) return 0.0
        val batteryFactor = (batteryPercent / 100.0) * 0.7
        val stabilityFactor = 0.3
        return batteryFactor + stabilityFactor
    }

    fun queueForUplink(message: MeshMessage) {
        pendingUplinkQueue.add(message)
    }

    /**
     * Flushes queued digests to backend ingest API.
     * Returns list of ACK messages received from the server to route back into mesh.
     */
    fun flushBatchToBackend(gatewayNodeId: Long): List<AckMessage> {
        if (!checkUplinkAvailable() || pendingUplinkQueue.isEmpty()) return emptyList()

        val batch = mutableListOf<MeshMessage>()
        while (batch.size < 50 && !pendingUplinkQueue.isEmpty()) {
            val msg = pendingUplinkQueue.poll() ?: break
            batch.add(msg)
        }
        if (batch.isEmpty()) return emptyList()

        val generatedAcks = mutableListOf<AckMessage>()
        try {
            val url = URL("$backendBaseUrl/api/ingest")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.connectTimeout = 5000
            conn.readTimeout = 5000

            // Format simple JSON payload
            val jsonPayload = buildBatchJson(gatewayNodeId, batch)
            OutputStreamWriter(conn.outputStream).use { it.write(jsonPayload) }

            val responseCode = conn.responseCode
            if (responseCode in 200..299) {
                // For any SOS delivered in this batch, create an ACK from gateway
                val nowSec = System.currentTimeMillis() / 1000L
                batch.filter { it.header.type == MessageType.SOS }.forEach { sos ->
                    val ack = AckMessage(
                        header = Header(
                            version = 1,
                            type = MessageType.ACK,
                            nodeId = gatewayNodeId,
                            seq = (sos.header.seq + 1000),
                            ttl = 8,
                            timestamp = nowSec
                        ),
                        ackedNodeId = sos.header.nodeId,
                        ackedSeq = sos.header.seq,
                        gatewayId = gatewayNodeId,
                        hopCount = 1,
                        status = AckStatus.RECEIVED_BY_GATEWAY
                    )
                    generatedAcks.add(ack)
                }
            }
        } catch (e: Exception) {
            // Re-queue on network error
            batch.forEach { pendingUplinkQueue.add(it) }
        }
        return generatedAcks
    }

    private fun buildBatchJson(gatewayId: Long, messages: List<MeshMessage>): String {
        val items = messages.joinToString(",") { msg ->
            val h = msg.header
            val base = """"node_id":${h.nodeId},"seq":${h.seq},"type":"${h.type.name}","timestamp":${h.timestamp}"""
            when (msg) {
                is org.meshsos.protocol.HeartbeatMessage -> {
                    val nbrStr = msg.neighbours.joinToString(prefix = "[", postfix = "]")
                    val locStr = if (lastLat != null && lastLon != null) ""","lat":$lastLat,"lon":$lastLon""" else ""
                    """{$base,"grid_x":${msg.gridX},"grid_y":${msg.gridY},"battery":${msg.battery},"flags":${msg.flags},"neighbours":$nbrStr$locStr}"""
                }
                is org.meshsos.protocol.SosMessage -> {
                    val finalLat = if (msg.lat != 0.0f) msg.lat else (lastLat?.toFloat() ?: 0.0f)
                    val finalLon = if (msg.lon != 0.0f) msg.lon else (lastLon?.toFloat() ?: 0.0f)
                    val safeText = msg.text.replace("\"", "\\\"")
                    """{$base,"lat":$finalLat,"lon":$finalLon,"accuracy":${msg.accuracy},"category":"${msg.category.name}","battery":${msg.battery},"text":"$safeText"}"""
                }
                is org.meshsos.protocol.OkNowMessage -> {
                    val safeText = msg.text.replace("\"", "\\\"")
                    """{$base,"target_sos_seq":${msg.targetSosSeq},"battery":${msg.battery},"text":"$safeText"}"""
                }
                is org.meshsos.protocol.LowbatMessage -> {
                    """{$base,"reason":"${msg.reason.name}","battery":${msg.battery},"last_grid_x":${msg.lastGridX},"last_grid_y":${msg.lastGridY}}"""
                }
                is org.meshsos.protocol.AckMessage -> {
                    """{$base,"acked_node_id":${msg.ackedNodeId},"acked_seq":${msg.ackedSeq},"gateway_id":${msg.gatewayId},"hop_count":${msg.hopCount},"status":"${msg.status.name}"}"""
                }
            }
        }
        return """{"gateway_id":$gatewayId,"events":[$items]}"""
    }
}
