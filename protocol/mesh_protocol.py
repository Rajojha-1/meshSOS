"""
MeshSOS Canonical Protocol Implementation.
Handles binary serialization/deserialization and JSON representation
for all MeshSOS message types (HEARTBEAT, SOS, OK_NOW, LOWBAT, ACK).
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple, Union
from cryptography.hazmat.primitives.asymmetric import ed25519


class MessageType(IntEnum):
    HEARTBEAT = 0x01
    SOS = 0x02
    OK_NOW = 0x03
    LOWBAT = 0x04
    ACK = 0x05


class LowbatReason(IntEnum):
    BATTERY_DEPLETED = 0x01
    USER_STOP = 0x02


class SosCategory(IntEnum):
    OTHER = 0x00
    MEDICAL = 0x01
    TRAPPED = 0x02
    FLOOD = 0x03
    COLLAPSE = 0x04


class HeartbeatFlags(IntEnum):
    MOVING = 0x01
    CHARGING = 0x02
    GATEWAY_CAPABLE = 0x04


class AckStatus(IntEnum):
    RECEIVED_BY_GATEWAY = 0x01
    FORWARDED_TO_CONTROL = 0x02
    RESCUER_ASSIGNED = 0x03


HEADER_FORMAT = ">BBIHBI"  # version (1B), type (1B), node_id (4B), seq (2B), ttl (1B), timestamp (4B)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 13 bytes


@dataclass
class Header:
    version: int
    msg_type: MessageType
    node_id: int
    seq: int
    ttl: int
    timestamp: int

    def pack(self) -> bytes:
        return struct.pack(
            HEADER_FORMAT,
            self.version,
            int(self.msg_type),
            self.node_id,
            self.seq,
            self.ttl,
            self.timestamp,
        )

    @classmethod
    def unpack(cls, data: bytes) -> Header:
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Data too short for header: {len(data)} < {HEADER_SIZE}")
        version, mtype, node_id, seq, ttl, ts = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        return cls(
            version=version,
            msg_type=MessageType(mtype),
            node_id=node_id,
            seq=seq,
            ttl=ttl,
            timestamp=ts,
        )


@dataclass
class HeartbeatMessage:
    header: Header
    grid_x: int  # 16-bit signed grid X (e.g. ~100m grid cell)
    grid_y: int  # 16-bit signed grid Y
    battery: int  # 0 - 100 %
    flags: int  # bitfield: MOVING(1), CHARGING(2), GATEWAY_CAPABLE(4)
    neighbours: List[int] = field(default_factory=list)  # up to 3 16-bit node IDs

    def __post_init__(self):
        if self.header.msg_type != MessageType.HEARTBEAT:
            raise ValueError("Invalid message type for HeartbeatMessage")
        if len(self.neighbours) > 3:
            raise ValueError("Heartbeat allows at most 3 neighbours in digest to fit legacy BLE frame")

    def to_bytes(self) -> bytes:
        header_bytes = self.header.pack()
        payload_base = struct.pack(">hhBBB", self.grid_x, self.grid_y, self.battery, self.flags, len(self.neighbours))
        neighbour_bytes = b"".join(struct.pack(">H", n & 0xFFFF) for n in self.neighbours)
        return header_bytes + payload_base + neighbour_bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> HeartbeatMessage:
        header = Header.unpack(data)
        offset = HEADER_SIZE
        grid_x, grid_y, battery, flags, n_count = struct.unpack_from(">hhBBB", data, offset)
        offset += struct.calcsize(">hhBBB")
        neighbours = []
        for _ in range(n_count):
            (nid,) = struct.unpack_from(">H", data, offset)
            neighbours.append(nid)
            offset += 2
        return cls(
            header=header,
            grid_x=grid_x,
            grid_y=grid_y,
            battery=battery,
            flags=flags,
            neighbours=neighbours,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.header.version,
            "type": self.header.msg_type.name,
            "node_id": self.header.node_id,
            "seq": self.header.seq,
            "ttl": self.header.ttl,
            "timestamp": self.header.timestamp,
            "grid_x": self.grid_x,
            "grid_y": self.grid_y,
            "battery": self.battery,
            "flags": self.flags,
            "neighbours": self.neighbours,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HeartbeatMessage:
        header = Header(
            version=d["version"],
            msg_type=MessageType[d["type"]],
            node_id=d["node_id"],
            seq=d["seq"],
            ttl=d["ttl"],
            timestamp=d["timestamp"],
        )
        return cls(
            header=header,
            grid_x=d["grid_x"],
            grid_y=d["grid_y"],
            battery=d["battery"],
            flags=d["flags"],
            neighbours=d.get("neighbours", []),
        )


@dataclass
class SosMessage:
    header: Header
    lat: float
    lon: float
    accuracy: float
    category: SosCategory
    battery: int
    text: str
    pubkey: bytes  # 32 bytes Ed25519 public key
    signature: bytes = b"\x00" * 64  # 64 bytes Ed25519 signature

    def __post_init__(self):
        if self.header.msg_type != MessageType.SOS:
            raise ValueError("Invalid message type for SosMessage")
        if not isinstance(self.category, SosCategory):
            self.category = SosCategory(self.category)
        if len(self.pubkey) != 32:
            raise ValueError(f"Pubkey must be exactly 32 bytes, got {len(self.pubkey)}")
        if len(self.signature) != 64:
            raise ValueError(f"Signature must be exactly 64 bytes, got {len(self.signature)}")

    def unsigned_payload_bytes(self) -> bytes:
        header_bytes = self.header.pack()
        text_bytes = self.text.encode("utf-8")
        if len(text_bytes) > 255:
            raise ValueError("SOS text too long (> 255 bytes)")
        body = struct.pack(
            ">fffBBB",
            self.lat,
            self.lon,
            self.accuracy,
            int(self.category),
            self.battery,
            len(text_bytes),
        ) + text_bytes + self.pubkey
        return header_bytes + body

    def sign(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        data_to_sign = self.unsigned_payload_bytes()
        self.signature = private_key.sign(data_to_sign)

    def verify(self) -> bool:
        if self.signature == b"\x00" * 64:
            return False
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(self.pubkey)
            pub.verify(self.signature, self.unsigned_payload_bytes())
            return True
        except Exception:
            return False

    def to_bytes(self) -> bytes:
        return self.unsigned_payload_bytes() + self.signature

    @classmethod
    def from_bytes(cls, data: bytes) -> SosMessage:
        header = Header.unpack(data)
        offset = HEADER_SIZE
        lat, lon, acc, cat, battery, text_len = struct.unpack_from(">fffBBB", data, offset)
        offset += struct.calcsize(">fffBBB")
        text = data[offset : offset + text_len].decode("utf-8", errors="replace")
        offset += text_len
        pubkey = data[offset : offset + 32]
        offset += 32
        sig = data[offset : offset + 64]
        return cls(
            header=header,
            lat=lat,
            lon=lon,
            accuracy=acc,
            category=SosCategory(cat),
            battery=battery,
            text=text,
            pubkey=pubkey,
            signature=sig,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.header.version,
            "type": self.header.msg_type.name,
            "node_id": self.header.node_id,
            "seq": self.header.seq,
            "ttl": self.header.ttl,
            "timestamp": self.header.timestamp,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "accuracy": round(self.accuracy, 2),
            "category": self.category.name,
            "battery": self.battery,
            "text": self.text,
            "pubkey_hex": self.pubkey.hex(),
            "signature_hex": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SosMessage:
        header = Header(
            version=d["version"],
            msg_type=MessageType[d["type"]],
            node_id=d["node_id"],
            seq=d["seq"],
            ttl=d["ttl"],
            timestamp=d["timestamp"],
        )
        return cls(
            header=header,
            lat=d["lat"],
            lon=d["lon"],
            accuracy=d["accuracy"],
            category=SosCategory[d["category"]],
            battery=d["battery"],
            text=d["text"],
            pubkey=bytes.fromhex(d["pubkey_hex"]),
            signature=bytes.fromhex(d["signature_hex"]),
        )


@dataclass
class OkNowMessage:
    header: Header
    target_sos_seq: int
    battery: int
    text: str
    pubkey: bytes  # 32 bytes
    signature: bytes = b"\x00" * 64  # 64 bytes

    def __post_init__(self):
        if self.header.msg_type != MessageType.OK_NOW:
            raise ValueError("Invalid message type for OkNowMessage")
        if len(self.pubkey) != 32:
            raise ValueError(f"Pubkey must be exactly 32 bytes, got {len(self.pubkey)}")
        if len(self.signature) != 64:
            raise ValueError(f"Signature must be exactly 64 bytes, got {len(self.signature)}")

    def unsigned_payload_bytes(self) -> bytes:
        header_bytes = self.header.pack()
        text_bytes = self.text.encode("utf-8")
        body = struct.pack(">HBB", self.target_sos_seq, self.battery, len(text_bytes)) + text_bytes + self.pubkey
        return header_bytes + body

    def sign(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        self.signature = private_key.sign(self.unsigned_payload_bytes())

    def verify(self) -> bool:
        if self.signature == b"\x00" * 64:
            return False
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(self.pubkey)
            pub.verify(self.signature, self.unsigned_payload_bytes())
            return True
        except Exception:
            return False

    def to_bytes(self) -> bytes:
        return self.unsigned_payload_bytes() + self.signature

    @classmethod
    def from_bytes(cls, data: bytes) -> OkNowMessage:
        header = Header.unpack(data)
        offset = HEADER_SIZE
        target_seq, battery, text_len = struct.unpack_from(">HBB", data, offset)
        offset += struct.calcsize(">HBB")
        text = data[offset : offset + text_len].decode("utf-8", errors="replace")
        offset += text_len
        pubkey = data[offset : offset + 32]
        offset += 32
        sig = data[offset : offset + 64]
        return cls(
            header=header,
            target_sos_seq=target_seq,
            battery=battery,
            text=text,
            pubkey=pubkey,
            signature=sig,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.header.version,
            "type": self.header.msg_type.name,
            "node_id": self.header.node_id,
            "seq": self.header.seq,
            "ttl": self.header.ttl,
            "timestamp": self.header.timestamp,
            "target_sos_seq": self.target_sos_seq,
            "battery": self.battery,
            "text": self.text,
            "pubkey_hex": self.pubkey.hex(),
            "signature_hex": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> OkNowMessage:
        header = Header(
            version=d["version"],
            msg_type=MessageType[d["type"]],
            node_id=d["node_id"],
            seq=d["seq"],
            ttl=d["ttl"],
            timestamp=d["timestamp"],
        )
        return cls(
            header=header,
            target_sos_seq=d["target_sos_seq"],
            battery=d["battery"],
            text=d["text"],
            pubkey=bytes.fromhex(d["pubkey_hex"]),
            signature=bytes.fromhex(d["signature_hex"]),
        )


@dataclass
class LowbatMessage:
    header: Header
    reason: LowbatReason
    battery: int
    last_grid_x: int
    last_grid_y: int

    def __post_init__(self):
        if self.header.msg_type != MessageType.LOWBAT:
            raise ValueError("Invalid message type for LowbatMessage")
        if not isinstance(self.reason, LowbatReason):
            self.reason = LowbatReason(self.reason)

    def to_bytes(self) -> bytes:
        header_bytes = self.header.pack()
        payload = struct.pack(">BBhh", int(self.reason), self.battery, self.last_grid_x, self.last_grid_y)
        return header_bytes + payload

    @classmethod
    def from_bytes(cls, data: bytes) -> LowbatMessage:
        header = Header.unpack(data)
        reason_val, battery, gx, gy = struct.unpack_from(">BBhh", data, HEADER_SIZE)
        return cls(
            header=header,
            reason=LowbatReason(reason_val),
            battery=battery,
            last_grid_x=gx,
            last_grid_y=gy,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.header.version,
            "type": self.header.msg_type.name,
            "node_id": self.header.node_id,
            "seq": self.header.seq,
            "ttl": self.header.ttl,
            "timestamp": self.header.timestamp,
            "reason": self.reason.name,
            "battery": self.battery,
            "last_grid_x": self.last_grid_x,
            "last_grid_y": self.last_grid_y,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LowbatMessage:
        header = Header(
            version=d["version"],
            msg_type=MessageType[d["type"]],
            node_id=d["node_id"],
            seq=d["seq"],
            ttl=d["ttl"],
            timestamp=d["timestamp"],
        )
        return cls(
            header=header,
            reason=LowbatReason[d["reason"]],
            battery=d["battery"],
            last_grid_x=d["last_grid_x"],
            last_grid_y=d["last_grid_y"],
        )


@dataclass
class AckMessage:
    header: Header
    acked_node_id: int
    acked_seq: int
    gateway_id: int
    hop_count: int
    status: AckStatus

    def __post_init__(self):
        if self.header.msg_type != MessageType.ACK:
            raise ValueError("Invalid message type for AckMessage")
        if not isinstance(self.status, AckStatus):
            self.status = AckStatus(self.status)

    def to_bytes(self) -> bytes:
        header_bytes = self.header.pack()
        payload = struct.pack(">IHIBB", self.acked_node_id, self.acked_seq, self.gateway_id, self.hop_count, int(self.status))
        return header_bytes + payload

    @classmethod
    def from_bytes(cls, data: bytes) -> AckMessage:
        header = Header.unpack(data)
        acked_nid, acked_seq, gw_id, hops, status = struct.unpack_from(">IHIBB", data, HEADER_SIZE)
        return cls(
            header=header,
            acked_node_id=acked_nid,
            acked_seq=acked_seq,
            gateway_id=gw_id,
            hop_count=hops,
            status=AckStatus(status),
        )

    def to_dict(self) -> dict:
        return {
            "version": self.header.version,
            "type": self.header.msg_type.name,
            "node_id": self.header.node_id,
            "seq": self.header.seq,
            "ttl": self.header.ttl,
            "timestamp": self.header.timestamp,
            "acked_node_id": self.acked_node_id,
            "acked_seq": self.acked_seq,
            "gateway_id": self.gateway_id,
            "hop_count": self.hop_count,
            "status": self.status.name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AckMessage:
        header = Header(
            version=d["version"],
            msg_type=MessageType[d["type"]],
            node_id=d["node_id"],
            seq=d["seq"],
            ttl=d["ttl"],
            timestamp=d["timestamp"],
        )
        return cls(
            header=header,
            acked_node_id=d["acked_node_id"],
            acked_seq=d["acked_seq"],
            gateway_id=d["gateway_id"],
            hop_count=d["hop_count"],
            status=AckStatus[d["status"]],
        )


MeshMessage = Union[HeartbeatMessage, SosMessage, OkNowMessage, LowbatMessage, AckMessage]


def deserialize_message(data: bytes) -> MeshMessage:
    if len(data) < HEADER_SIZE:
        raise ValueError("Data too short to deserialize MeshMessage")
    mtype = data[1]
    if mtype == MessageType.HEARTBEAT:
        return HeartbeatMessage.from_bytes(data)
    elif mtype == MessageType.SOS:
        return SosMessage.from_bytes(data)
    elif mtype == MessageType.OK_NOW:
        return OkNowMessage.from_bytes(data)
    elif mtype == MessageType.LOWBAT:
        return LowbatMessage.from_bytes(data)
    elif mtype == MessageType.ACK:
        return AckMessage.from_bytes(data)
    else:
        raise ValueError(f"Unknown message type: {mtype}")


def message_from_dict(d: dict) -> MeshMessage:
    mtype = d.get("type")
    if mtype == "HEARTBEAT":
        return HeartbeatMessage.from_dict(d)
    elif mtype == "SOS":
        return SosMessage.from_dict(d)
    elif mtype == "OK_NOW":
        return OkNowMessage.from_dict(d)
    elif mtype == "LOWBAT":
        return LowbatMessage.from_dict(d)
    elif mtype == "ACK":
        return AckMessage.from_dict(d)
    else:
        raise ValueError(f"Unknown message dict type: {mtype}")
