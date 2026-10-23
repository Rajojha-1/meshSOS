package org.meshsos.relay

import org.meshsos.protocol.*
import java.util.concurrent.ConcurrentHashMap
import java.util.PriorityQueue

enum class PriorityLevel(val level: Int) {
    SOS(0),
    OK_NOW(1),
    LOWBAT(2),
    HEARTBEAT(3);

    companion object {
        fun fromType(type: MessageType): PriorityLevel = when (type) {
            MessageType.SOS -> SOS
            MessageType.OK_NOW -> OK_NOW
            MessageType.LOWBAT -> LOWBAT
            MessageType.HEARTBEAT -> HEARTBEAT
            MessageType.ACK -> SOS
        }
    }
}

data class QueuedMessage(
    val priority: Int,
    val scheduledTimeMs: Long,
    val message: MeshMessage,
    var overheardCount: Int = 0,
    val kSuppressionThreshold: Int = 2,
    val suppressionEnabled: Boolean = true
) : Comparable<QueuedMessage> {
    override fun compareTo(other: QueuedMessage): Int {
        val pComp = this.priority.compareTo(other.priority)
        return if (pComp != 0) pComp else this.scheduledTimeMs.compareTo(other.scheduledTimeMs)
    }
}

class BoundedDedupeCache(private val maxSize: Int = 2048) {
    private val map = object : LinkedHashMap<Pair<Long, Int>, Long>(maxSize, 0.75f, true) {
        override fun removeEldestEntry(eldest: Map.Entry<Pair<Long, Int>, Long>?): Boolean {
            return size > maxSize
        }
    }

    @Synchronized
    fun hasSeen(nodeId: Long, seq: Int): Boolean {
        return map.containsKey(Pair(nodeId, seq))
    }

    @Synchronized
    fun markSeen(nodeId: Long, seq: Int, nowMs: Long) {
        map[Pair(nodeId, seq)] = nowMs
    }

    @Synchronized
    fun size(): Int = map.size
}

class RateLimiter(private val maxPerMinute: Int = 20) {
    private val history = ConcurrentHashMap<Long, MutableList<Long>>()

    @Synchronized
    fun isAllowed(nodeId: Long, nowMs: Long): Boolean {
        val windowStart = nowMs - 60000L
        val timestamps = history.getOrPut(nodeId) { mutableListOf() }
        timestamps.removeAll { it < windowStart }
        if (timestamps.size >= maxPerMinute) {
            return false
        }
        timestamps.add(nowMs)
        return true
    }
}

class StoreAndForwardStore(private val expiryMs: Long = 6 * 3600 * 1000L) {
    data class Entry(val message: MeshMessage, val expiryTime: Long, var lastRetryTime: Long)
    private val store = ConcurrentHashMap<Pair<Long, Int>, Entry>()

    fun put(message: MeshMessage, nowMs: Long) {
        val key = Pair(message.header.nodeId, message.header.seq)
        store[key] = Entry(message, nowMs + expiryMs, nowMs)
    }

    fun remove(nodeId: Long, seq: Int) {
        store.remove(Pair(nodeId, seq))
    }

    fun getPendingMessages(nowMs: Long, minRetryIntervalMs: Long = 10000L): List<MeshMessage> {
        val expired = store.filter { nowMs > it.value.expiryTime }.keys
        expired.forEach { store.remove(it) }

        val candidates = mutableListOf<MeshMessage>()
        for ((_, entry) in store) {
            if (nowMs - entry.lastRetryTime >= minRetryIntervalMs) {
                candidates.add(entry.message)
                entry.lastRetryTime = nowMs
            }
        }
        return candidates
    }
}

data class RelayCoreConfig(
    val enableSuppression: Boolean = true,
    val kHeartbeat: Int = 2,
    val kLowbat: Int = 2,
    val kSos: Int = 0, // No suppression for SOS by default
    val delayMinMs: Long = 50L,
    val delayMaxMs: Long = 400L,
    val sosDelayMaxMs: Long = 80L,
    val maxRatePerMin: Int = 20,
    val dedupeCacheSize: Int = 2048
)

class RelayCore(val nodeId: Long, val config: RelayCoreConfig = RelayCoreConfig()) {
    val dedupeCache = BoundedDedupeCache(config.dedupeCacheSize)
    val rateLimiter = RateLimiter(config.maxRatePerMin)
    val storeAndForward = StoreAndForwardStore()
    val ackedSos = ConcurrentHashMap.newKeySet<Pair<Long, Int>>()
    val pendingTransmissions = ConcurrentHashMap<Pair<Long, Int>, QueuedMessage>()

    var receivedTotal = 0
    var dedupeDropped = 0
    var ttlExpired = 0
    var rateLimited = 0
    var suppressed = 0
    var forwarded = 0
    var acksProcessed = 0

    @Synchronized
    fun onReceive(message: MeshMessage, nowMs: Long, randomFraction: Double = 0.5): QueuedMessage? {
        receivedTotal++
        val hdr = message.header
        val msgKey = Pair(hdr.nodeId, hdr.seq)

        // 1. Process ACK
        if (hdr.type == MessageType.ACK && message is AckMessage) {
            val targetKey = Pair(message.ackedNodeId, message.ackedSeq)
            ackedSos.add(targetKey)
            storeAndForward.remove(message.ackedNodeId, message.ackedSeq)
            pendingTransmissions.remove(targetKey)
            acksProcessed++
        }

        // 2. Counter-based suppression check for overheard duplicates
        val pending = pendingTransmissions[msgKey]
        if (pending != null) {
            pending.overheardCount++
            if (pending.suppressionEnabled && pending.kSuppressionThreshold > 0) {
                if (pending.overheardCount >= pending.kSuppressionThreshold) {
                    suppressed++
                    pendingTransmissions.remove(msgKey)
                }
            }
            return null
        }

        // 3. Deduplication check
        if (dedupeCache.hasSeen(hdr.nodeId, hdr.seq)) {
            dedupeDropped++
            return null
        }
        dedupeCache.markSeen(hdr.nodeId, hdr.seq, nowMs)

        // 4. Rate limiting check
        if (!rateLimiter.isAllowed(hdr.nodeId, nowMs)) {
            rateLimited++
            return null
        }

        // 5. If SOS already ACKed, drop
        if (hdr.type == MessageType.SOS && ackedSos.contains(msgKey)) {
            return null
        }

        // 6. TTL check and decrement
        if (hdr.ttl <= 1) {
            ttlExpired++
            return null
        }

        val forwardHeader = Header(
            version = hdr.version,
            type = hdr.type,
            nodeId = hdr.nodeId,
            seq = hdr.seq,
            ttl = hdr.ttl - 1,
            timestamp = hdr.timestamp
        )

        val forwardMsg: MeshMessage = when (message) {
            is HeartbeatMessage -> message.copy(header = forwardHeader)
            is SosMessage -> message.copy(header = forwardHeader)
            is OkNowMessage -> message.copy(header = forwardHeader)
            is LowbatMessage -> message.copy(header = forwardHeader)
            is AckMessage -> message.copy(header = forwardHeader, hopCount = message.hopCount + 1)
        }

        // 7. Store & Forward critical messages
        if (hdr.type == MessageType.SOS || hdr.type == MessageType.OK_NOW || hdr.type == MessageType.LOWBAT) {
            storeAndForward.put(forwardMsg, nowMs)
        }

        // 8. Suppression and jitter calculation
        val prio = PriorityLevel.fromType(hdr.type).level
        val (kVal, suppressionOn, delayMs) = when (hdr.type) {
            MessageType.SOS -> {
                val span = maxOf(5L, config.sosDelayMaxMs - config.delayMinMs)
                val delay = config.delayMinMs + (randomFraction * span).toLong()
                Triple(config.kSos, (config.kSos > 0) && config.enableSuppression, delay)
            }
            MessageType.LOWBAT -> {
                val span = maxOf(10L, config.delayMaxMs - config.delayMinMs)
                val delay = config.delayMinMs + (randomFraction * span).toLong()
                Triple(config.kLowbat, config.enableSuppression, delay)
            }
            else -> {
                val span = maxOf(10L, config.delayMaxMs - config.delayMinMs)
                val delay = config.delayMinMs + (randomFraction * span).toLong()
                Triple(config.kHeartbeat, config.enableSuppression, delay)
            }
        }

        val scheduledTime = nowMs + delayMs
        val queued = QueuedMessage(
            priority = prio,
            scheduledTimeMs = scheduledTime,
            message = forwardMsg,
            overheardCount = 0,
            kSuppressionThreshold = kVal,
            suppressionEnabled = suppressionOn
        )
        pendingTransmissions[msgKey] = queued
        return queued
    }

    @Synchronized
    fun popReadyTransmission(nowMs: Long): MeshMessage? {
        val ready = pendingTransmissions.filter { nowMs >= it.value.scheduledTimeMs }
        if (ready.isEmpty()) return null

        val best = ready.minByOrNull { it.value } ?: return null
        pendingTransmissions.remove(best.key)
        forwarded++
        return best.value.message
    }

    fun onNewNeighbourDiscovered(neighbourId: Long, nowMs: Long): List<MeshMessage> {
        return storeAndForward.getPendingMessages(nowMs)
    }
}
