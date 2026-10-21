"""
Phase 1 Acceptance Tests:
1. Multi-hop propagation: SOS crossing 5+ hops to reach a gateway.
2. Deduplication: verifies duplicate transmissions are dropped.
3. Relay comparison: counter-based suppression reduces transmissions vs plain flooding.
"""

import pytest
from simulator.node import SimNode
from simulator.network_simulator import NetworkSimulator, SimulationMetrics
from simulator.relay_core import RelayCoreConfig
from protocol.mesh_protocol import SosCategory


def test_sos_crossing_5_plus_hops():
    """
    Construct a linear chain of 7 nodes:
    Node 1 (x=0) -> Node 2 (x=60) -> Node 3 (x=120) -> Node 4 (x=180) -> Node 5 (x=240) -> Node 6 (x=300) -> Node 7 (Gateway, x=360)
    Radio range is 70m, so each node can only talk to its immediate adjacent neighbours.
    An SOS generated at Node 1 must traverse at least 6 hops to reach Gateway Node 7!
    """
    sim = NetworkSimulator(width=500, height=200, packet_loss_rate=0.0, seed=123)

    nodes = []
    for i in range(7):
        is_gw = (i == 6)
        n = SimNode(
            node_id=100 + i,
            x=float(i * 60),
            y=50.0,
            radio_range=70.0,
            heartbeat_interval=60.0,  # long heartbeat to isolate SOS
            is_gateway=is_gw,
        )
        nodes.append(n)
        sim.add_node(n)

    # Let simulator run for 0.5s to settle
    sim.run_until(0.5)

    # Trigger SOS at node 100
    sim.trigger_sos(100, text="Chain Test SOS", category=SosCategory.TRAPPED)

    # Run for 5 seconds to allow multi-hop propagation
    sim.run_until(5.0)

    # Acceptance checks:
    assert sim.metrics.total_sos_generated == 1
    assert sim.metrics.total_sos_delivered_to_gateway == 1
    assert len(sim.metrics.hops_traversed) == 1

    traversed_hops = sim.metrics.hops_traversed[0]
    assert traversed_hops >= 5, f"Expected >= 5 hops, got {traversed_hops}"
    assert sim.metrics.avg_latency_sec > 0.0

    # Verify deduplication was active across the chain
    total_dedupe = sum(n.relay.stats["dedupe_dropped"] for n in nodes)
    assert total_dedupe > 0, "Deduplication must have dropped duplicate overheard transmissions"


def test_suppression_reduces_transmissions_vs_flooding():
    """
    In a dense mesh grid (e.g. 5x5 = 25 nodes, range 100m in 250x250m area),
    compare total transmissions between:
    1. Unsuppressed flooding (k=0 / suppression disabled)
    2. Counter-based suppression (k=2)
    With a fixed reproducible seed.
    """
    seed = 42

    def run_mesh_scenario(enable_suppression: bool, k_val: int) -> SimulationMetrics:
        sim = NetworkSimulator(width=300, height=300, packet_loss_rate=0.02, seed=seed)
        cfg = RelayCoreConfig(
            enable_suppression=enable_suppression,
            k_heartbeat=k_val,
            k_sos=k_val,
            k_lowbat=k_val,
            delay_min_ms=30.0,
            delay_max_ms=150.0,
            sos_delay_max_ms=150.0,
        )

        node_id = 1
        for row in range(5):
            for col in range(5):
                is_gw = (row == 4 and col == 4)
                n = SimNode(
                    node_id=node_id,
                    x=float(col * 60),
                    y=float(row * 60),
                    radio_range=95.0,
                    heartbeat_interval=30.0,
                    is_gateway=is_gw,
                    relay_config=cfg,
                )
                sim.add_node(n)
                node_id += 1

        sim.run_until(0.5)
        # Node 1 (top-left) triggers SOS
        sim.trigger_sos(1, text="Density Test SOS")
        sim.run_until(4.0)
        return sim.metrics

    metrics_flooding = run_mesh_scenario(enable_suppression=False, k_val=0)
    metrics_suppressed = run_mesh_scenario(enable_suppression=True, k_val=2)

    assert metrics_flooding.total_sos_delivered_to_gateway >= 1
    assert metrics_suppressed.total_sos_delivered_to_gateway >= 1

    # Counter-based suppression must noticeably reduce transmissions
    tx_flood = metrics_flooding.total_transmissions
    tx_suppressed = metrics_suppressed.total_transmissions

    assert tx_suppressed < tx_flood, f"Suppressed TX ({tx_suppressed}) must be less than Flooding TX ({tx_flood})"
    reduction_pct = ((tx_flood - tx_suppressed) / tx_flood) * 100.0
    print(f"\n[Phase 1 Eval] Flooding TX: {tx_flood}, Suppressed TX: {tx_suppressed}, Reduction: {reduction_pct:.1f}%")
