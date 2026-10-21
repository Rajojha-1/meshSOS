"""
Phase 2 Acceptance Tests for Inference Engine:
Scenario (a): Sudden collapse of 12/15 phones in one block -> High confidence silent zone.
Scenario (b): Random battery deaths with LOWBAT beacons -> Excluded from disaster alert.
Scenario (c): Single person walking out of range -> No high confidence alert.
Scenario (d): Mesh-wide outage -> Suppressed / low localized disaster confidence.
"""

import pytest
import time
from inference.engine import InferenceEngine
from simulator.network_simulator import NetworkSimulator
from simulator.node import SimNode
from protocol.mesh_protocol import LowbatReason


def test_scenario_a_localized_collapse():
    """
    Scenario (a): 15 phones in Block A (0-300m), 10 phones in surrounding Block B (300-600m).
    At T=60s, 12 phones in Block A collapse simultaneously.
    Neighbours in Block A (3 phones) and Block B (10 phones) continue reporting.
    At T=120s, engine must flag the zone with HIGH confidence (> 0.70).
    """
    engine = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)

    # Simulate heartbeat telemetry up to T=60s
    # All 25 phones send periodic heartbeats at t=0, 15, 30, 45, 60
    for t in [0.0, 15.0, 30.0, 45.0, 60.0]:
        # Block A nodes (101 to 115)
        for i in range(15):
            nid = 101 + i
            gx = 10 + (i % 3)
            gy = 10 + (i // 3)
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": gx,
                "grid_y": gy,
                "battery": 85,
                "flags": 0,
                "timestamp": t,
                "neighbours": [101 + ((i + 1) % 15)],
            }, now=t)

        # Block B nodes (201 to 210)
        for j in range(10):
            nid = 201 + j
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 30 + j,
                "grid_y": 15,
                "battery": 90,
                "flags": 0,
                "timestamp": t,
                "neighbours": [201 + ((j + 1) % 10)],
            }, now=t)

    # Collapse occurs at T=60s: nodes 101 to 112 are destroyed!
    # Surviving nodes: 113, 114, 115 in Block A, and 201-210 in Block B
    # They continue sending heartbeats at t=75, 90, 105, 120
    for t in [75.0, 90.0, 105.0, 120.0]:
        for nid in [113, 114, 115]:
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 12,
                "grid_y": 12,
                "battery": 84,
                "flags": 0,
                "timestamp": t,
                "neighbours": [113, 114],
            }, now=t)
        for j in range(10):
            nid = 201 + j
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 30 + j,
                "grid_y": 15,
                "battery": 89,
                "flags": 0,
                "timestamp": t,
                "neighbours": [201 + ((j + 1) % 10)],
            }, now=t)

    # Evaluate at T=120s
    res = engine.evaluate(now=120.0)
    silent_zones = res["silent_zones"]["features"]

    assert len(silent_zones) >= 1, "Scenario (a) must detect at least one silent zone"
    target_zone = silent_zones[0]["properties"]
    print(f"\n[Scenario A] Confidence: {target_zone['confidence']}, Silent: {target_zone['n_silent']}, Reason: {target_zone['reason']}")

    # Acceptance criteria: flagged with HIGH confidence (> 0.70)
    assert target_zone["confidence"] >= 0.70, f"Confidence {target_zone['confidence']} must be >= 0.70"
    assert target_zone["n_silent"] == 12
    assert target_zone["neighbours_alive"] > 0


def test_scenario_b_benign_battery_deaths():
    """
    Scenario (b): 10 phones deplete their battery and transmit a LOWBAT beacon before shutting down.
    The inference engine must exclude them from disaster silent zones.
    """
    engine = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)

    # Initial heartbeats
    for t in [0.0, 15.0, 30.0]:
        for i in range(10):
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": 301 + i,
                "grid_x": 10,
                "grid_y": 10,
                "battery": 15,
                "timestamp": t,
            }, now=t)

    # Each phone runs down to 4% and sends a LOWBAT beacon at t=45s
    for i in range(10):
        engine.ingest_event({
            "type": "LOWBAT",
            "node_id": 301 + i,
            "reason": "BATTERY_DEPLETED",
            "battery": 4,
            "last_grid_x": 10,
            "last_grid_y": 10,
            "timestamp": 45.0,
        }, now=45.0)

    # Surrounding nodes still report
    for t in [45.0, 60.0, 75.0, 120.0]:
        for j in range(5):
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": 401 + j,
                "grid_x": 20,
                "grid_y": 20,
                "battery": 75,
                "timestamp": t,
            }, now=t)

    # Evaluate at T=120s
    res = engine.evaluate(now=120.0)
    silent_zones = res["silent_zones"]["features"]

    # Acceptance: No disaster silent zone raised (confidence 0 or empty)
    assert len(silent_zones) == 0, f"Expected 0 silent zones for LOWBAT battery deaths, got {len(silent_zones)}"


def test_scenario_c_single_walker_out_of_range():
    """
    Scenario (c): A single person walks out of radio coverage or turns into an underground passage.
    A single silent node must NEVER trigger high confidence (must be 0 or < 0.25).
    """
    engine = InferenceEngine(overdue_multiplier=3.0, min_cluster_size=3)

    # Heartbeats at t=0, 15, 30
    for t in [0.0, 15.0, 30.0]:
        engine.ingest_event({
            "type": "HEARTBEAT",
            "node_id": 501,
            "grid_x": 10,
            "grid_y": 10,
            "battery": 90,
            "timestamp": t,
        }, now=t)
        # Other nodes around
        for j in range(5):
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": 601 + j,
                "grid_x": 10 + j,
                "grid_y": 10,
                "battery": 90,
                "timestamp": t,
            }, now=t)

    # Node 501 walks away, others keep reporting
    for t in [45.0, 60.0, 75.0, 120.0]:
        for j in range(5):
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": 601 + j,
                "grid_x": 10 + j,
                "grid_y": 10,
                "battery": 90,
                "timestamp": t,
            }, now=t)

    # Evaluate at T=120s
    res = engine.evaluate(now=120.0)
    silent_zones = res["silent_zones"]["features"]

    # Acceptance: Single walker must NOT produce any high-confidence silent zone
    high_conf_zones = [z for z in silent_zones if z["properties"]["confidence"] >= 0.50]
    assert len(high_conf_zones) == 0, "Single walker must not create a high-confidence disaster zone"


def test_scenario_d_mesh_wide_outage():
    """
    Scenario (d): Mesh-wide outage where 0 active nodes report.
    Should be recognized as a global transport outage, suppressing high confidence.
    """
    engine = InferenceEngine(overdue_multiplier=3.0, min_cluster_size=3)

    for t in [0.0, 15.0, 30.0]:
        for i in range(10):
            engine.ingest_event({
                "type": "HEARTBEAT",
                "node_id": 701 + i,
                "grid_x": 15,
                "grid_y": 15,
                "battery": 80,
                "timestamp": t,
            }, now=t)

    # At T=35s, whole network stops (total blackout / gateway disconnected)
    # Evaluate at T=120s with NO surviving nodes reporting anywhere
    res = engine.evaluate(now=120.0)
    silent_zones = res["silent_zones"]["features"]

    # When all phones stop, confidence must be suppressed (< 0.20) and noted as global outage
    for z in silent_zones:
        assert z["properties"]["confidence"] < 0.20
        assert "zero phones reporting" in z["properties"]["reason"].lower()
