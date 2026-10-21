"""
Generates canonical test vectors for MeshSOS protocol.
Dumps test_vectors.json containing binary hex representations and expected deserialized fields.
"""

import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519
from mesh_protocol import (
    Header,
    HeartbeatMessage,
    SosMessage,
    OkNowMessage,
    LowbatMessage,
    AckMessage,
    MessageType,
    LowbatReason,
    SosCategory,
    HeartbeatFlags,
    AckStatus,
    deserialize_message,
)

# Use deterministic 32-byte seed for Ed25519 key generation
SEED = b"\x42" * 32
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(SEED)
public_key = private_key.public_key()
pubkey_bytes = public_key.public_bytes_raw()

vectors = []

# 1. Heartbeat
hb = HeartbeatMessage(
    header=Header(
        version=1,
        msg_type=MessageType.HEARTBEAT,
        node_id=0xA1B2C3D4,
        seq=101,
        ttl=8,
        timestamp=1700000000,
    ),
    grid_x=1420,
    grid_y=-850,
    battery=87,
    flags=HeartbeatFlags.MOVING | HeartbeatFlags.GATEWAY_CAPABLE,
    neighbours=[0x0012, 0x0045, 0x00A9],
)
hb_bytes = hb.to_bytes()
assert deserialize_message(hb_bytes).to_bytes() == hb_bytes
vectors.append({
    "name": "heartbeat_standard",
    "type": "HEARTBEAT",
    "raw_hex": hb_bytes.hex(),
    "fields": hb.to_dict(),
})

# 2. SOS
sos = SosMessage(
    header=Header(
        version=1,
        msg_type=MessageType.SOS,
        node_id=0xFE01A234,
        seq=42,
        ttl=10,
        timestamp=1700000010,
    ),
    lat=37.774929,
    lon=-122.419418,
    accuracy=5.25,
    category=SosCategory.TRAPPED,
    battery=45,
    text="Water rising rapidly, 2nd floor",
    pubkey=pubkey_bytes,
)
sos.sign(private_key)
assert sos.verify()
sos_bytes = sos.to_bytes()
deserialized_sos = deserialize_message(sos_bytes)
assert deserialized_sos.verify()
assert deserialized_sos.to_bytes() == sos_bytes
vectors.append({
    "name": "sos_signed",
    "type": "SOS",
    "raw_hex": sos_bytes.hex(),
    "fields": sos.to_dict(),
})

# 3. OK_NOW
ok = OkNowMessage(
    header=Header(
        version=1,
        msg_type=MessageType.OK_NOW,
        node_id=0xFE01A234,
        seq=43,
        ttl=8,
        timestamp=1700000100,
    ),
    target_sos_seq=42,
    battery=43,
    text="Rescued by boat, safe now",
    pubkey=pubkey_bytes,
)
ok.sign(private_key)
assert ok.verify()
ok_bytes = ok.to_bytes()
deserialized_ok = deserialize_message(ok_bytes)
assert deserialized_ok.verify()
assert deserialized_ok.to_bytes() == ok_bytes
vectors.append({
    "name": "ok_now_signed",
    "type": "OK_NOW",
    "raw_hex": ok_bytes.hex(),
    "fields": ok.to_dict(),
})

# 4. LOWBAT
lowbat = LowbatMessage(
    header=Header(
        version=1,
        msg_type=MessageType.LOWBAT,
        node_id=0xCAFEBABE,
        seq=999,
        ttl=5,
        timestamp=1700000500,
    ),
    reason=LowbatReason.BATTERY_DEPLETED,
    battery=4,
    last_grid_x=1422,
    last_grid_y=-848,
)
lowbat_bytes = lowbat.to_bytes()
assert deserialize_message(lowbat_bytes).to_bytes() == lowbat_bytes
vectors.append({
    "name": "lowbat_beacon",
    "type": "LOWBAT",
    "raw_hex": lowbat_bytes.hex(),
    "fields": lowbat.to_dict(),
})

# 5. ACK
ack = AckMessage(
    header=Header(
        version=1,
        msg_type=MessageType.ACK,
        node_id=0x99887766,
        seq=1,
        ttl=8,
        timestamp=1700000025,
    ),
    acked_node_id=0xFE01A234,
    acked_seq=42,
    gateway_id=0x99887766,
    hop_count=3,
    status=AckStatus.RECEIVED_BY_GATEWAY,
)
ack_bytes = ack.to_bytes()
assert deserialize_message(ack_bytes).to_bytes() == ack_bytes
vectors.append({
    "name": "ack_response",
    "type": "ACK",
    "raw_hex": ack_bytes.hex(),
    "fields": ack.to_dict(),
})

output_path = Path(__file__).parent / "test_vectors.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump({"vectors": vectors, "seed_hex": SEED.hex(), "pubkey_hex": pubkey_bytes.hex()}, f, indent=2)

print(f"Generated {len(vectors)} test vectors saved to {output_path}")
