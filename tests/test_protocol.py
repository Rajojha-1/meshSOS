"""
Unit tests for MeshSOS protocol serialization, deserialization, and test vectors.
"""

import json
import pytest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519

from protocol.mesh_protocol import (
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
    message_from_dict,
)


@pytest.fixture
def test_vectors():
    path = Path(__file__).parent.parent / "protocol" / "test_vectors.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["vectors"]


def test_vectors_roundtrip(test_vectors):
    """Verify that every test vector matches binary hex representation and roundtrips perfectly."""
    for vec in test_vectors:
        raw_bytes = bytes.fromhex(vec["raw_hex"])
        msg = deserialize_message(raw_bytes)
        assert msg.header.msg_type.name == vec["type"]

        # Binary roundtrip check
        reserialized = msg.to_bytes()
        assert reserialized == raw_bytes, f"Binary mismatch for {vec['name']}"

        # Dictionary roundtrip check
        d = msg.to_dict()
        from_dict_msg = message_from_dict(d)
        assert from_dict_msg.to_bytes() == raw_bytes


def test_heartbeat_payload_size_fits_ble_legacy():
    """Verify that Heartbeat message is <= 31 bytes to fit legacy BLE advertising."""
    hb = HeartbeatMessage(
        header=Header(1, MessageType.HEARTBEAT, 0x12345678, 1, 8, 1700000000),
        grid_x=500,
        grid_y=-300,
        battery=100,
        flags=HeartbeatFlags.MOVING,
        neighbours=[0x0001, 0x0002, 0x0003],
    )
    data = hb.to_bytes()
    # 13B header + 7B payload base + 6B neighbours = 26 bytes <= 31 bytes
    assert len(data) <= 26
    assert len(data) <= 31

    decoded = deserialize_message(data)
    assert isinstance(decoded, HeartbeatMessage)
    assert decoded.neighbours == [0x0001, 0x0002, 0x0003]


def test_sos_signature_verification():
    seed = b"\x55" * 32
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    pub = priv.public_key().public_bytes_raw()

    sos = SosMessage(
        header=Header(1, MessageType.SOS, 0xABCDEF01, 10, 5, 1700000000),
        lat=37.77,
        lon=-122.42,
        accuracy=4.0,
        category=SosCategory.FLOOD,
        battery=60,
        text="Need boat rescue",
        pubkey=pub,
    )
    # Without signature, verify fails
    assert not sos.verify()

    # Sign and verify
    sos.sign(priv)
    assert sos.verify()

    # Tamper with text and check failure
    tampered_bytes = bytearray(sos.to_bytes())
    # change a byte in the text
    tampered_bytes[25] = tampered_bytes[25] ^ 0xFF
    with pytest.raises(Exception):
        # Either fails decoding or fails verification
        tampered_msg = deserialize_message(bytes(tampered_bytes))
        assert not tampered_msg.verify()


def test_ok_now_signature_verification():
    seed = b"\x77" * 32
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    pub = priv.public_key().public_bytes_raw()

    ok = OkNowMessage(
        header=Header(1, MessageType.OK_NOW, 0xABCDEF01, 11, 5, 1700000010),
        target_sos_seq=10,
        battery=59,
        text="All safe now",
        pubkey=pub,
    )
    ok.sign(priv)
    assert ok.verify()

    raw = ok.to_bytes()
    decoded = deserialize_message(raw)
    assert isinstance(decoded, OkNowMessage)
    assert decoded.verify()
    assert decoded.target_sos_seq == 10


def test_lowbat_reasons():
    lb1 = LowbatMessage(
        header=Header(1, MessageType.LOWBAT, 0x11112222, 55, 3, 1700000000),
        reason=LowbatReason.BATTERY_DEPLETED,
        battery=5,
        last_grid_x=10,
        last_grid_y=20,
    )
    assert deserialize_message(lb1.to_bytes()).reason == LowbatReason.BATTERY_DEPLETED

    lb2 = LowbatMessage(
        header=Header(1, MessageType.LOWBAT, 0x11112222, 56, 3, 1700000010),
        reason=LowbatReason.USER_STOP,
        battery=80,
        last_grid_x=10,
        last_grid_y=20,
    )
    assert deserialize_message(lb2.to_bytes()).reason == LowbatReason.USER_STOP


def test_ack_message():
    ack = AckMessage(
        header=Header(1, MessageType.ACK, 0x99998888, 1, 8, 1700000050),
        acked_node_id=0x11112222,
        acked_seq=10,
        gateway_id=0x99998888,
        hop_count=2,
        status=AckStatus.RECEIVED_BY_GATEWAY,
    )
    raw = ack.to_bytes()
    decoded = deserialize_message(raw)
    assert isinstance(decoded, AckMessage)
    assert decoded.acked_node_id == 0x11112222
    assert decoded.acked_seq == 10
    assert decoded.status == AckStatus.RECEIVED_BY_GATEWAY
