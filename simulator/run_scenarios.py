"""
Executable script to run and compare mesh simulator scenarios.
Generates metrics and traces for Phase 1 and Phase 2 evaluation.
"""

import json
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from simulator.node import SimNode
from simulator.network_simulator import NetworkSimulator
from simulator.relay_core import RelayCoreConfig
from protocol.mesh_protocol import SosCategory


def run_evaluation_suite():
    print("=" * 60)
    print("MeshSOS Simulator Phase 1 Evaluation Suite")
    print("=" * 60)

    # 1. 100-node Random Mesh Scaling Run
    sim = NetworkSimulator(width=800, height=800, packet_loss_rate=0.03, seed=999)
    # 98 normal nodes, 2 gateways
    for i in range(100):
        is_gw = (i >= 98)
        x = sim.rng.uniform(50, 750)
        y = sim.rng.uniform(50, 750)
        node = SimNode(
            node_id=1000 + i,
            x=x,
            y=y,
            radio_range=110.0,
            heartbeat_interval=15.0,
            is_gateway=is_gw,
        )
        sim.add_node(node)

    # Run initial warm-up for 10 seconds to discover neighbours
    sim.run_until(10.0)

    # Trigger SOS from a corner node
    sim.trigger_sos(1000, text="Scaling Test SOS", category=SosCategory.MEDICAL)

    # Run for 20 more seconds
    sim.run_until(30.0)

    print(f"Nodes: 100 (2 Gateways)")
    print(f"Total transmissions: {sim.metrics.total_transmissions}")
    print(f"SOS generated: {sim.metrics.total_sos_generated}, Delivered: {sim.metrics.total_sos_delivered_to_gateway}")
    if sim.metrics.latencies_sec:
        print(f"SOS delivery latency: {sim.metrics.avg_latency_sec*1000:.1f} ms")
        print(f"Average hops traversed: {sim.metrics.avg_hops:.1f}")
    print(f"Dedupe drops: {sim.metrics.dedupe_drops}")
    print(f"Suppression drops: {sim.metrics.suppression_drops}")

    results = {
        "nodes": 100,
        "transmissions": sim.metrics.total_transmissions,
        "sos_generated": sim.metrics.total_sos_generated,
        "sos_delivered": sim.metrics.total_sos_delivered_to_gateway,
        "avg_latency_ms": sim.metrics.avg_latency_sec * 1000.0,
        "avg_hops": sim.metrics.avg_hops,
        "dedupe_drops": sim.metrics.dedupe_drops,
        "suppression_drops": sim.metrics.suppression_drops,
    }

    out_file = Path(__file__).parent / "phase1_metrics.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved metrics to {out_file}")
    print("=" * 60)


if __name__ == "__main__":
    run_evaluation_suite()
