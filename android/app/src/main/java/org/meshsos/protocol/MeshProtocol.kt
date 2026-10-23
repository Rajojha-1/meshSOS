package org.meshsos.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets

enum class MessageType(val code: Int) {
    HEARTBEAT(0x01),
    SOS(0x02),
    OK_NOW(0x03),
    LOWBAT(0x04),
    ACK(0x05);

    companion object {
        fun fromCode(code: Int): MessageType = entries.first { it.code == code }
    }
}

enum class LowbatReason(val code: Int) {
    BATTERY_DEPLETED(0x01),
    USER_STOP(0x02);

    companion object {
        fun fromCode(code: Int): LowbatReason = entries.first { it.code == code }
    }
}

enum class SosCategory(val code: Int) {
    OTHER(0x00),
    MEDICAL(0x01),
    TRAPPED(0x02),
    FLOOD(0x03),
    COLLAPSE(0x04);

    companion object {
        fun fromCode(code: Int): SosCategory = entries.first { it.code == code }
    }
}

enum class AckStatus(val code: Int) {
    RECEIVED_BY_GATEWAY(0x01),
    FORWARDED_TO_CONTROL(0x02),
    RESCUER_ASSIGNED(0x03);

    companion object {
        fun fromCode(code: Int): AckStatus = entries.first { it.code == code }
    }
}

data class Header(
    val version: Int,
    val type: MessageType,
    val nodeId: Long, // 32-bit unsigned
    val seq: Int,     // 16-bit unsigned
    val ttl: Int,     // 8-bit unsigned
    val timestamp: Long // 32-bit unsigned
) {
    fun pack(buffer: ByteBuffer) {
        buffer.put(version.toByte())
        buffer.put(type.code.toByte())
        buffer.putInt((nodeId and 0xFFFFFFFFL).toInt())
        buffer.putShort((seq and 0xFFFF).toShort())
        buffer.put((ttl and 0xFF).toByte())
        buffer.putInt((timestamp and 0xFFFFFFFFL).toInt())
    }

    companion object {
        const val SIZE = 13

        fun unpack(buffer: ByteBuffer): Header {
            val version = buffer.get().toInt() and 0xFF
            val type = MessageType.fromCode(buffer.get().toInt() and 0xFF)
            val nodeId = buffer.int.toLong() and 0xFFFFFFFFL
            val seq = buffer.short.toInt() and 0xFFFF
            val ttl = buffer.get().toInt() and 0xFF
            val timestamp = buffer.int.toLong() and 0xFFFFFFFFL
            return Header(version, type, nodeId, seq, ttl, timestamp)
        }
    }
}

sealed class MeshMessage(open val header: Header) {
    abstract fun toBytes(): ByteArray
}

data class HeartbeatMessage(
    override val header: Header,
    val gridX: Short,
    val gridY: Short,
    val battery: Int,
    val flags: Int,
    val neighbours: List<Int>
) : MeshMessage(header) {
    override fun toBytes(): ByteArray {
        val totalSize = Header.SIZE + 4 + 1 + 1 + 1 + (neighbours.size * 2)
        val buffer = ByteBuffer.allocate(totalSize).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putShort(gridX)
        buffer.putShort(gridY)
        buffer.put(battery.toByte())
        buffer.put(flags.toByte())
        buffer.put(neighbours.size.toByte())
        for (n in neighbours) {
            buffer.putShort((n and 0xFFFF).toShort())
        }
        return buffer.array()
    }

    companion object {
        fun unpack(header: Header, buffer: ByteBuffer): HeartbeatMessage {
            val gridX = buffer.short
            val gridY = buffer.short
            val battery = buffer.get().toInt() and 0xFF
            val flags = buffer.get().toInt() and 0xFF
            val count = buffer.get().toInt() and 0xFF
            val neighbours = ArrayList<Int>(count)
            for (i in 0 until count) {
                neighbours.add(buffer.short.toInt() and 0xFFFF)
            }
            return HeartbeatMessage(header, gridX, gridY, battery, flags, neighbours)
        }
    }
}

data class SosMessage(
    override val header: Header,
    val lat: Float,
    val lon: Float,
    val accuracy: Float,
    val category: SosCategory,
    val battery: Int,
    val text: String,
    val pubkey: ByteArray,   // 32 bytes
    var signature: ByteArray // 64 bytes
) : MeshMessage(header) {
    fun unsignedPayloadBytes(): ByteArray {
        val textBytes = text.toByteArray(StandardCharsets.UTF_8)
        val size = Header.SIZE + 4 + 4 + 4 + 1 + 1 + 1 + textBytes.size + 32
        val buffer = ByteBuffer.allocate(size).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putFloat(lat)
        buffer.putFloat(lon)
        buffer.putFloat(accuracy)
        buffer.put(category.code.toByte())
        buffer.put(battery.toByte())
        buffer.put(textBytes.size.toByte())
        buffer.put(textBytes)
        buffer.put(pubkey)
        return buffer.array()
    }

    fun toCompactBleBytes(): ByteArray {
        val buffer = ByteBuffer.allocate(Header.SIZE + 4 + 4 + 1 + 1).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putFloat(lat)
        buffer.putFloat(lon)
        buffer.put((battery and 0xFF).toByte())
        buffer.put(category.code.toByte())
        return buffer.array()
    }

    override fun toBytes(): ByteArray {
        val unsigned = unsignedPayloadBytes()
        val total = ByteArray(unsigned.size + 64)
        System.arraycopy(unsigned, 0, total, 0, unsigned.size)
        System.arraycopy(signature, 0, total, unsigned.size, 64)
        return total
    }

    companion object {
        fun unpack(header: Header, buffer: ByteBuffer): SosMessage {
            val lat = buffer.float
            val lon = buffer.float
            if (buffer.remaining() <= 2) {
                val battery = buffer.get().toInt() and 0xFF
                val cat = if (buffer.hasRemaining()) SosCategory.fromCode(buffer.get().toInt() and 0xFF) else SosCategory.OTHER
                return SosMessage(header, lat, lon, 5.0f, cat, battery, "EMERGENCY HELP NEEDED", ByteArray(32), ByteArray(64))
            }
            val acc = buffer.float
            val cat = SosCategory.fromCode(buffer.get().toInt() and 0xFF)
            val battery = buffer.get().toInt() and 0xFF
            val textLen = buffer.get().toInt() and 0xFF
            val textBytes = ByteArray(textLen)
            buffer.get(textBytes)
            val text = String(textBytes, StandardCharsets.UTF_8)
            val pubkey = ByteArray(32)
            buffer.get(pubkey)
            val signature = ByteArray(64)
            buffer.get(signature)
            return SosMessage(header, lat, lon, acc, cat, battery, text, pubkey, signature)
        }
    }
}

data class OkNowMessage(
    override val header: Header,
    val targetSosSeq: Int,
    val battery: Int,
    val text: String,
    val pubkey: ByteArray,
    var signature: ByteArray
) : MeshMessage(header) {
    fun unsignedPayloadBytes(): ByteArray {
        val textBytes = text.toByteArray(StandardCharsets.UTF_8)
        val size = Header.SIZE + 2 + 1 + 1 + textBytes.size + 32
        val buffer = ByteBuffer.allocate(size).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putShort((targetSosSeq and 0xFFFF).toShort())
        buffer.put(battery.toByte())
        buffer.put(textBytes.size.toByte())
        buffer.put(textBytes)
        buffer.put(pubkey)
        return buffer.array()
    }

    fun toCompactBleBytes(): ByteArray {
        val buffer = ByteBuffer.allocate(Header.SIZE + 2 + 1).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putShort((targetSosSeq and 0xFFFF).toShort())
        buffer.put((battery and 0xFF).toByte())
        return buffer.array()
    }

    override fun toBytes(): ByteArray {
        val unsigned = unsignedPayloadBytes()
        val total = ByteArray(unsigned.size + 64)
        System.arraycopy(unsigned, 0, total, 0, unsigned.size)
        System.arraycopy(signature, 0, total, unsigned.size, 64)
        return total
    }

    companion object {
        fun unpack(header: Header, buffer: ByteBuffer): OkNowMessage {
            val targetSeq = buffer.short.toInt() and 0xFFFF
            val battery = buffer.get().toInt() and 0xFF
            if (!buffer.hasRemaining()) {
                return OkNowMessage(header, targetSeq, battery, "I'm OK now", ByteArray(32), ByteArray(64))
            }
            val textLen = buffer.get().toInt() and 0xFF
            val textBytes = ByteArray(textLen)
            buffer.get(textBytes)
            val text = String(textBytes, StandardCharsets.UTF_8)
            val pubkey = ByteArray(32)
            buffer.get(pubkey)
            val signature = ByteArray(64)
            buffer.get(signature)
            return OkNowMessage(header, targetSeq, battery, text, pubkey, signature)
        }
    }
}

data class LowbatMessage(
    override val header: Header,
    val reason: LowbatReason,
    val battery: Int,
    val lastGridX: Short,
    val lastGridY: Short
) : MeshMessage(header) {
    override fun toBytes(): ByteArray {
        val size = Header.SIZE + 1 + 1 + 2 + 2
        val buffer = ByteBuffer.allocate(size).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.put(reason.code.toByte())
        buffer.put(battery.toByte())
        buffer.putShort(lastGridX)
        buffer.putShort(lastGridY)
        return buffer.array()
    }

    companion object {
        fun unpack(header: Header, buffer: ByteBuffer): LowbatMessage {
            val reason = LowbatReason.fromCode(buffer.get().toInt() and 0xFF)
            val battery = buffer.get().toInt() and 0xFF
            val lastGridX = buffer.short
            val lastGridY = buffer.short
            return LowbatMessage(header, reason, battery, lastGridX, lastGridY)
        }
    }
}

data class AckMessage(
    override val header: Header,
    val ackedNodeId: Long,
    val ackedSeq: Int,
    val gatewayId: Long,
    val hopCount: Int,
    val status: AckStatus
) : MeshMessage(header) {
    override fun toBytes(): ByteArray {
        val size = Header.SIZE + 4 + 2 + 4 + 1 + 1
        val buffer = ByteBuffer.allocate(size).order(ByteOrder.BIG_ENDIAN)
        header.pack(buffer)
        buffer.putInt((ackedNodeId and 0xFFFFFFFFL).toInt())
        buffer.putShort((ackedSeq and 0xFFFF).toShort())
        buffer.putInt((gatewayId and 0xFFFFFFFFL).toInt())
        buffer.put((hopCount and 0xFF).toByte())
        buffer.put(status.code.toByte())
        return buffer.array()
    }

    companion object {
        fun unpack(header: Header, buffer: ByteBuffer): AckMessage {
            val ackedNid = buffer.int.toLong() and 0xFFFFFFFFL
            val ackedSeq = buffer.short.toInt() and 0xFFFF
            val gwId = buffer.int.toLong() and 0xFFFFFFFFL
            val hops = buffer.get().toInt() and 0xFF
            val status = AckStatus.fromCode(buffer.get().toInt() and 0xFF)
            return AckMessage(header, ackedNid, ackedSeq, gwId, hops, status)
        }
    }
}

object MeshProtocolDeserializer {
    fun deserialize(bytes: ByteArray): MeshMessage {
        val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN)
        val header = Header.unpack(buffer)
        return when (header.type) {
            MessageType.HEARTBEAT -> HeartbeatMessage.unpack(header, buffer)
            MessageType.SOS -> SosMessage.unpack(header, buffer)
            MessageType.OK_NOW -> OkNowMessage.unpack(header, buffer)
            MessageType.LOWBAT -> LowbatMessage.unpack(header, buffer)
            MessageType.ACK -> AckMessage.unpack(header, buffer)
        }
    }
}
