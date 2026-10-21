"""
RelayCore unit test verifying two relay node instances relay and dedupe messages.
"""

import pytest
from simulator.relay_core import RelayCore, RelayCoreConfig
from protocol.mesh_protocol import Header, SosMessage, MessageType, SosCategory


def test_two_nodes_relay_and_dedupe():
    node_a = RelayCore(101, RelayCoreConfig(enable_suppression=True))
    node_b = RelayCore(102, RelayCoreConfig(enable_suppression=True))

    now = 1700000000.0

    sos = SosMessage(
        header=Header(1, MessageType.SOS, 101, 1, 8, int(now)),
        lat=37.7749,
        lon=-122.4194,
        accuracy=4.5,
        category=SosCategory.TRAPPED,
        battery=75,
        text="Need help trapped on 2nd floor",
        pubkey=b"\x42" * 32,
        signature=b"\x01" * 64,
    )

    # Node B receives from Node A
    queued_b = node_b.on_receive(sos, now, random_fraction=0.1)
    assert queued_b is not None
    assert queued_b.message.header.ttl == 7
    assert node_b.dedupe_cache.size() == 1

    # Overheard duplicate arrives at Node B
    second_rx = node_b.on_receive(sos, now + 0.01, random_fraction=0.1)
    assert second_rx is None

    # Advance clock and pop transmission from Node B
    ready_b = node_b.pop_ready_transmission(now + 1.0)
    assert ready_b is not None
    assert ready_b.header.msg_type == MessageType.SOS

    # Third receive arrives after forward: dropped by dedupe
    third_rx = node_b.on_receive(sos, now + 1.5)
    assert third_rx is None
    assert node_b.stats["dedupe_dropped"] > 0
