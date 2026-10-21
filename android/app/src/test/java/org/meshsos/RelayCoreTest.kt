package org.meshsos

import org.junit.Assert.*
import org.junit.Test
import org.meshsos.protocol.*
import org.meshsos.relay.RelayCore
import org.meshsos.relay.RelayCoreConfig

/**
 * Phase 4 Acceptance Test:
 * Verifies two relay core instances relay and dedupe messages identically to the reference logic.
 */
class RelayCoreTest {

    @Test
    fun testTwoNodesRelayAndDedupe() {
        val nodeA = RelayCore(101L, RelayCoreConfig(enableSuppression = true))
        val nodeB = RelayCore(102L, RelayCoreConfig(enableSuppression = true))

        val nowMs = 1700000000000L

        // Node A creates an SOS message
        val sos = SosMessage(
            header = Header(
                version = 1,
                type = MessageType.SOS,
                nodeId = 101L,
                seq = 1,
                ttl = 8,
                timestamp = nowMs / 1000L
            ),
            lat = 37.7749f,
            lon = -122.4194f,
            accuracy = 4.5f,
            category = SosCategory.TRAPPED,
            battery = 75,
            text = "Need help trapped on 2nd floor",
            pubkey = ByteArray(32) { 0x42 },
            signature = ByteArray(64) { 0x01 }
        )

        // Node B receives the SOS from Node A
        val queuedAtB = nodeB.onReceive(sos, nowMs, randomFraction = 0.1)
        assertNotNull("Node B must schedule forward for new SOS", queuedAtB)
        assertEquals("TTL must be decremented by 1", 7, queuedAtB!!.message.header.ttl)
        assertEquals("Dedupe cache should have 1 entry", 1, nodeB.dedupeCache.size())

        // Duplicate transmission arrives at Node B (e.g. overheard from another neighbour)
        val secondReceive = nodeB.onReceive(sos, nowMs + 10L, randomFraction = 0.1)
        assertNull("Duplicate message must NOT be re-scheduled for forwarding", secondReceive)

        // Advance clock and pop transmission from Node B
        val readyAtB = nodeB.popReadyTransmission(nowMs + 1000L)
        assertNotNull("Node B should pop ready transmission after scheduled delay", readyAtB)
        assertEquals("Forwarded message must match SOS type", MessageType.SOS, readyAtB!!.header.type)

        // Sending the exact same message again after forward should be dropped by dedupe
        val thirdReceive = nodeB.onReceive(sos, nowMs + 1500L)
        assertNull("Dedupe cache must drop previously forwarded message", thirdReceive)
        assertTrue("Dedupe drop counter must be > 0", nodeB.dedupeDropped > 0)
    }

    @Test
    fun testSerializationRoundTrip() {
        val hb = HeartbeatMessage(
            header = Header(1, MessageType.HEARTBEAT, 0xA1B2C3D4L, 101, 8, 1700000000L),
            gridX = 1420.toShort(),
            gridY = (-850).toShort(),
            battery = 87,
            flags = 5,
            neighbours = listOf(0x0012, 0x0045, 0x00A9)
        )
        val bytes = hb.toBytes()
        val deserialized = MeshProtocolDeserializer.deserialize(bytes) as HeartbeatMessage

        assertEquals(hb.header.nodeId, deserialized.header.nodeId)
        assertEquals(hb.header.seq, deserialized.header.seq)
        assertEquals(hb.gridX, deserialized.gridX)
        assertEquals(hb.gridY, deserialized.gridY)
        assertEquals(hb.neighbours, deserialized.neighbours)
    }
}
