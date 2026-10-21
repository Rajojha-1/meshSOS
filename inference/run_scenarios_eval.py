"""
Phase 2 Evaluation Script.
Executes scenarios (a), (b), (c), and (d) systematically.
Records detection delays, true-positive rates, false-alarm rates,
and outputs a calibrated confidence-versus-outcome table.
"""

import json
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from inference.engine import InferenceEngine


def evaluate_phase2_scenarios():
    print("=" * 65)
    print("MeshSOS Phase 2: Inference Engine Calibration & Evaluation")
    print("=" * 65)

    results = []

    # Scenario A: Localized Collapse
    # 15 nodes in cluster, 12 collapse at T=60s. Surviving neighbours report.
    eng_a = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)
    collapse_time = 60.0
    for t in [0.0, 15.0, 30.0, 45.0, 60.0]:
        for i in range(15):
            eng_a.ingest_event({"type": "HEARTBEAT", "node_id": 101 + i, "grid_x": 10 + (i % 3), "grid_y": 10 + (i // 3), "battery": 80, "timestamp": t}, now=t)
        for j in range(10):
            eng_a.ingest_event({"type": "HEARTBEAT", "node_id": 201 + j, "grid_x": 30 + j, "grid_y": 15, "battery": 90, "timestamp": t}, now=t)

    first_detected_time = None
    final_conf = 0.0

    # Step forward every 5s to measure exact detection delay
    for t in [65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]:
        # Surviving nodes report
        if t in [75.0, 90.0, 105.0, 120.0]:
            for nid in [113, 114, 115]:
                eng_a.ingest_event({"type": "HEARTBEAT", "node_id": nid, "grid_x": 12, "grid_y": 12, "battery": 79, "timestamp": t}, now=t)
            for j in range(10):
                eng_a.ingest_event({"type": "HEARTBEAT", "node_id": 201 + j, "grid_x": 30 + j, "grid_y": 15, "battery": 89, "timestamp": t}, now=t)

        out = eng_a.evaluate(now=t)
        zones = out["silent_zones"]["features"]
        if zones and first_detected_time is None:
            first_detected_time = t
            final_conf = zones[0]["properties"]["confidence"]
        elif zones:
            final_conf = zones[0]["properties"]["confidence"]

    delay_a = (first_detected_time - collapse_time) if first_detected_time else 0.0
    results.append({
        "scenario": "(a) Localized Collapse (12/15 phones)",
        "ground_truth": "DISASTER",
        "detected": first_detected_time is not None,
        "confidence": round(final_conf, 3),
        "detection_delay_sec": delay_a,
        "classification": "TRUE_POSITIVE" if final_conf >= 0.70 else "MISSED",
    })

    # Scenario B: Low-Battery Beacons
    eng_b = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)
    for t in [0.0, 15.0, 30.0]:
        for i in range(10):
            eng_b.ingest_event({"type": "HEARTBEAT", "node_id": 301 + i, "grid_x": 10, "grid_y": 10, "battery": 15, "timestamp": t}, now=t)
    for i in range(10):
        eng_b.ingest_event({"type": "LOWBAT", "node_id": 301 + i, "reason": "BATTERY_DEPLETED", "battery": 4, "last_grid_x": 10, "last_grid_y": 10, "timestamp": 45.0}, now=45.0)
    for t in [45.0, 60.0, 75.0, 120.0]:
        for j in range(5):
            eng_b.ingest_event({"type": "HEARTBEAT", "node_id": 401 + j, "grid_x": 20, "grid_y": 20, "battery": 75, "timestamp": t}, now=t)

    out_b = eng_b.evaluate(now=120.0)
    zones_b = out_b["silent_zones"]["features"]
    results.append({
        "scenario": "(b) Random Battery Deaths (LOWBAT)",
        "ground_truth": "BENIGN",
        "detected": len(zones_b) > 0,
        "confidence": round(zones_b[0]["properties"]["confidence"], 3) if zones_b else 0.0,
        "detection_delay_sec": 0.0,
        "classification": "TRUE_NEGATIVE" if len(zones_b) == 0 else "FALSE_POSITIVE",
    })

    # Scenario C: Single Walker Out of Range
    eng_c = InferenceEngine(overdue_multiplier=3.0, min_cluster_size=3)
    for t in [0.0, 15.0, 30.0]:
        eng_c.ingest_event({"type": "HEARTBEAT", "node_id": 501, "grid_x": 10, "grid_y": 10, "battery": 90, "timestamp": t}, now=t)
        for j in range(5):
            eng_c.ingest_event({"type": "HEARTBEAT", "node_id": 601 + j, "grid_x": 10 + j, "grid_y": 10, "battery": 90, "timestamp": t}, now=t)
    for t in [45.0, 60.0, 75.0, 120.0]:
        for j in range(5):
            eng_c.ingest_event({"type": "HEARTBEAT", "node_id": 601 + j, "grid_x": 10 + j, "grid_y": 10, "battery": 90, "timestamp": t}, now=t)

    out_c = eng_c.evaluate(now=120.0)
    zones_c = out_c["silent_zones"]["features"]
    results.append({
        "scenario": "(c) Single Walker Out of Range",
        "ground_truth": "BENIGN",
        "detected": len(zones_c) > 0,
        "confidence": round(zones_c[0]["properties"]["confidence"], 3) if zones_c else 0.0,
        "detection_delay_sec": 0.0,
        "classification": "TRUE_NEGATIVE" if len(zones_c) == 0 else "FALSE_POSITIVE",
    })

    # Scenario D: Mesh-Wide Outage (All phones offline)
    eng_d = InferenceEngine(overdue_multiplier=3.0, min_cluster_size=3)
    for t in [0.0, 15.0, 30.0]:
        for i in range(10):
            eng_d.ingest_event({"type": "HEARTBEAT", "node_id": 701 + i, "grid_x": 15, "grid_y": 15, "battery": 80, "timestamp": t}, now=t)
    out_d = eng_d.evaluate(now=120.0)
    zones_d = out_d["silent_zones"]["features"]
    conf_d = zones_d[0]["properties"]["confidence"] if zones_d else 0.0
    results.append({
        "scenario": "(d) Mesh-Wide Outage (All phones stop)",
        "ground_truth": "INFRASTRUCTURE_OUTAGE",
        "detected": len(zones_d) > 0,
        "confidence": round(conf_d, 3),
        "detection_delay_sec": 0.0,
        "classification": "CORRECT_SUPPRESSION" if conf_d < 0.20 else "INCORRECT_FLAG",
    })

    print(f"\n{'Scenario':<42} | {'Truth':<14} | {'Conf':<6} | {'Delay(s)':<8} | {'Outcome'}")
    print("-" * 85)
    for r in results:
        print(f"{r['scenario']:<42} | {r['ground_truth']:<14} | {r['confidence']:<6.2f} | {r['detection_delay_sec']:<8.1f} | {r['classification']}")

    # Save to JSON
    out_path = Path(__file__).parent / "phase2_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved Phase 2 evaluation results to {out_path}")
    print("=" * 65)


if __name__ == "__main__":
    evaluate_phase2_scenarios()
