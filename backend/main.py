"""
MeshSOS FastAPI Backend.
Provides ingestion API, layer querying, contact graph streaming,
and interactive simulation execution for the rescuer dashboard.
"""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

sys.path.append(str(Path(__file__).parent.parent))

from backend.database import init_db, insert_events, get_recent_events, clear_db
from inference.engine import InferenceEngine
from simulator.network_simulator import NetworkSimulator
from simulator.node import SimNode
from protocol.mesh_protocol import SosCategory

app = FastAPI(
    title="MeshSOS Backend & Rescue API",
    description="Emergency mesh network ingestion and silence inference engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Inference Engine instance
inference_engine = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)

# Initialize Database on startup
init_db()





class IngestBatchRequest(BaseModel):
    gateway_id: Optional[int] = None
    events: List[Dict[str, Any]]


class SimulateScenarioRequest(BaseModel):
    scenario: str = "collapse"  # "collapse", "battery", "walker", "sos"


@app.post("/api/ingest")
def ingest_events_batch(req: IngestBatchRequest):
    """Ingest a batch of events relayed from a mesh gateway."""
    now = time.time()
    events = req.events
    for ev in events:
        if req.gateway_id and "gateway_id" not in ev:
            ev["gateway_id"] = req.gateway_id
        if "gateway_received_at" not in ev:
            ev["gateway_received_at"] = now

    insert_events(events, received_at=now)
    inference_engine.ingest_batch(events, now=now)
    return {"status": "ok", "ingested": len(events), "timestamp": now}


@app.get("/api/layers/all")
def get_all_layers():
    """Retrieve all 3 map layers, contact graph, and stats."""
    return inference_engine.evaluate(now=time.time())


@app.get("/api/layers/silent-zones")
def get_silent_zones():
    eval_result = inference_engine.evaluate(now=time.time())
    return eval_result["silent_zones"]


@app.get("/api/layers/sos-hotspots")
def get_sos_hotspots():
    eval_result = inference_engine.evaluate(now=time.time())
    return eval_result["sos_hotspots"]


@app.get("/api/layers/active-coverage")
def get_active_coverage():
    eval_result = inference_engine.evaluate(now=time.time())
    return eval_result["active_coverage"]


@app.get("/api/graph")
def get_contact_graph():
    eval_result = inference_engine.evaluate(now=time.time())
    return eval_result["contact_graph"]


@app.get("/api/stats")
def get_stats():
    eval_result = inference_engine.evaluate(now=time.time())
    return eval_result["stats"]


@app.get("/api/events")
def get_events(limit: int = 50):
    return get_recent_events(limit=limit)


@app.post("/api/reset")
def reset_system():
    global inference_engine
    clear_db()
    inference_engine = InferenceEngine(overdue_multiplier=3.0, cluster_radius_meters=300.0, min_cluster_size=3)
    return {"status": "reset_successful"}


@app.post("/api/simulate/run")
def run_simulation_scenario(req: SimulateScenarioRequest):
    """
    Executes a canned simulation scenario into the live backend:
    - "collapse": 12 phones collapse in Block A, 13 continue reporting -> High confidence silent zone
    - "battery": 10 phones send LOWBAT before shutdown -> excluded from disaster
    - "sos": generates user emergency SOS packets with trapped/flood tags
    """
    now = time.time()

    if req.scenario == "collapse":
        # 1. Past normal heartbeats
        t_base = now - 60.0
        batch = []
        for i in range(15):
            nid = 101 + i
            batch.append({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 10 + (i % 3),
                "grid_y": 10 + (i // 3),
                "battery": 82,
                "flags": 0,
                "timestamp": t_base,
                "gateway_received_at": t_base,
                "neighbours": [101 + ((i + 1) % 15)],
            })
        for j in range(10):
            nid = 201 + j
            batch.append({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 14 + j,
                "grid_y": 11,
                "battery": 91,
                "flags": 1,
                "timestamp": t_base,
                "gateway_received_at": t_base,
                "neighbours": [201 + ((j + 1) % 10)],
            })

        # 2. Collapse occurred 55s ago! Surviving nodes report right now
        for nid in [113, 114, 115]:
            batch.append({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 12,
                "grid_y": 12,
                "battery": 80,
                "flags": 0,
                "timestamp": now,
                "gateway_received_at": now,
                "neighbours": [113, 114],
            })
        for j in range(10):
            nid = 201 + j
            batch.append({
                "type": "HEARTBEAT",
                "node_id": nid,
                "grid_x": 14 + j,
                "grid_y": 11,
                "battery": 90,
                "flags": 1,
                "timestamp": now,
                "gateway_received_at": now,
                "neighbours": [201 + ((j + 1) % 10)],
            })

        insert_events(batch, received_at=now)
        inference_engine.ingest_batch(batch, now=now)
        eval_result = inference_engine.evaluate(now=now)
        return {
            "status": "simulation_executed",
            "scenario": "collapse",
            "silent_zones_found": len(eval_result["silent_zones"]["features"]),
            "zones": eval_result["silent_zones"]["features"],
        }

    elif req.scenario == "battery":
        batch = []
        for i in range(8):
            batch.append({
                "type": "LOWBAT",
                "node_id": 301 + i,
                "reason": "BATTERY_DEPLETED",
                "battery": 3,
                "last_grid_x": 10,
                "last_grid_y": 10,
                "timestamp": now - 30.0,
                "gateway_received_at": now - 30.0,
            })
        for j in range(5):
            batch.append({
                "type": "HEARTBEAT",
                "node_id": 401 + j,
                "grid_x": 20,
                "grid_y": 20,
                "battery": 88,
                "timestamp": now,
                "gateway_received_at": now,
            })
        insert_events(batch, received_at=now)
        inference_engine.ingest_batch(batch, now=now)
        eval_result = inference_engine.evaluate(now=now)
        return {
            "status": "simulation_executed",
            "scenario": "battery",
            "silent_zones_found": len(eval_result["silent_zones"]["features"]),
        }

    elif req.scenario == "sos":
        batch = [
            {
                "type": "SOS",
                "node_id": 9991,
                "seq": 1,
                "lat": 28.6695,
                "lon": 77.4540,
                "accuracy": 4.5,
                "category": "TRAPPED",
                "battery": 42,
                "text": "Collapsed stairwell, 4 people trapped",
                "timestamp": now,
                "gateway_received_at": now,
            },
            {
                "type": "SOS",
                "node_id": 9992,
                "seq": 1,
                "lat": 28.6698,
                "lon": 77.4545,
                "accuracy": 6.0,
                "category": "MEDICAL",
                "battery": 35,
                "text": "Severe injury, need stretcher",
                "timestamp": now,
                "gateway_received_at": now,
            },
        ]
        insert_events(batch, received_at=now)
        inference_engine.ingest_batch(batch, now=now)
        eval_result = inference_engine.evaluate(now=now)
        return {
            "status": "simulation_executed",
            "scenario": "sos",
            "sos_hotspots_found": len(eval_result["sos_hotspots"]["features"]),
        }

    else:
        raise HTTPException(status_code=400, detail="Unknown scenario")


# Mount dashboard static directory at root
dashboard_dir = Path(__file__).parent.parent / "dashboard"
if dashboard_dir.exists():
    app.mount("/", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard_static")
