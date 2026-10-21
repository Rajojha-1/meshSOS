"""
Phase 3 Acceptance Tests for Backend API and Dashboard Pipeline.
Verifies event ingestion, simulation execution, and silent zone extraction with evidence text.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_recent_events


@pytest.fixture
def client():
    return TestClient(app)


def test_dashboard_root_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "MeshSOS" in res.text
    assert "Silent Zones" in res.text


def test_ingest_endpoint(client):
    batch = {
        "gateway_id": 9999,
        "events": [
            {
                "type": "HEARTBEAT",
                "node_id": 12345,
                "seq": 1,
                "grid_x": 10,
                "grid_y": 20,
                "battery": 95,
                "flags": 0,
                "timestamp": 1700000000.0,
                "neighbours": [12346],
            }
        ],
    }
    res = client.post("/api/ingest", json=batch)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["ingested"] == 1

    # Verify SQLite database stored the event
    db_events = get_recent_events(limit=5)
    assert any(e.get("node_id") == 12345 for e in db_events)


def test_simulation_makes_silent_zone_appear_with_evidence(client):
    """
    Phase 3 Done When Criterion:
    'Done when: running a simulator scenario makes the silent zone appear on the map with evidence text.'
    """
    # 1. Reset
    client.post("/api/reset")

    # 2. Trigger collapse simulation scenario
    sim_res = client.post("/api/simulate/run", json={"scenario": "collapse"})
    assert sim_res.status_code == 200
    sim_data = sim_res.json()
    assert sim_data["status"] == "simulation_executed"
    assert sim_data["silent_zones_found"] >= 1

    # 3. Query the silent zones layer endpoint
    layer_res = client.get("/api/layers/silent-zones")
    assert layer_res.status_code == 200
    geojson = layer_res.json()
    features = geojson["features"]
    assert len(features) >= 1

    zone_props = features[0]["properties"]
    assert zone_props["confidence"] >= 0.70
    assert zone_props["n_silent"] >= 10

    # Verify evidence narrative text is present
    evidence_text = zone_props["reason"]
    assert "phones went silent" in evidence_text
    assert "reporting" in evidence_text
    print(f"\n[Phase 3 Evidence Verified] {evidence_text} (Confidence: {zone_props['confidence']})")


def test_all_layers_endpoint(client):
    res = client.get("/api/layers/all")
    assert res.status_code == 200
    data = res.json()
    assert "silent_zones" in data
    assert "sos_hotspots" in data
    assert "active_coverage" in data
    assert "contact_graph" in data
    assert "stats" in data
