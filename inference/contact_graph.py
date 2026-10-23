"""
MeshSOS Contact Graph.
Maintains a time-windowed graph of peer contacts, node statuses,
heartbeat intervals, and last known locations from received telemetry.
"""

from __future__ import annotations
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class NodeTelemetry:
    node_id: int
    last_seen: float
    grid_x: int = 0
    grid_y: int = 0
    lat: float = 0.0
    lon: float = 0.0
    battery: int = 100
    flags: int = 0
    last_message_type: str = "HEARTBEAT"
    lowbat_reason: Optional[str] = None
    heartbeat_history: List[float] = field(default_factory=list)
    recent_neighbours: List[int] = field(default_factory=list)

    @property
    def expected_interval(self) -> float:
        if len(self.heartbeat_history) < 2:
            return 15.0  # default expected interval
        diffs = [
            self.heartbeat_history[i] - self.heartbeat_history[i - 1]
            for i in range(1, len(self.heartbeat_history))
        ]
        avg = sum(diffs) / len(diffs)
        return max(5.0, min(120.0, avg))


class ContactGraph:
    def __init__(self, window_seconds: float = 300.0):
        self.window_seconds = window_seconds
        self.nodes: Dict[int, NodeTelemetry] = {}
        # Adjacency: (u, v) -> last_seen_edge_time
        self.edges: Dict[Tuple[int, int], float] = {}

    def ingest_event(self, event: dict, now: Optional[float] = None) -> None:
        event_time = float(event.get("gateway_received_at", event.get("timestamp", time.time())))
        if now is None:
            now = event_time

        nid = int(event["node_id"])
        mtype = event.get("type", "HEARTBEAT")

        if nid not in self.nodes:
            self.nodes[nid] = NodeTelemetry(node_id=nid, last_seen=event_time)

        telemetry = self.nodes[nid]
        telemetry.last_seen = max(telemetry.last_seen, event_time)
        telemetry.last_message_type = mtype
        telemetry.battery = int(event.get("battery", telemetry.battery))

        # Update position from real-world GPS if provided in event
        if "lat" in event and "lon" in event and event["lat"] is not None and event["lon"] is not None:
            try:
                ev_lat = float(event["lat"])
                ev_lon = float(event["lon"])
                if ev_lat != 0.0 or ev_lon != 0.0:
                    telemetry.lat = ev_lat
                    telemetry.lon = ev_lon
            except (ValueError, TypeError):
                pass

        if mtype == "HEARTBEAT":
            telemetry.grid_x = int(event.get("grid_x", telemetry.grid_x))
            telemetry.grid_y = int(event.get("grid_y", telemetry.grid_y))
            # Reverse the Android quantizeToGridCell: grid_x = round(lon*1000), grid_y = round(lat*1000)
            if telemetry.lat == 0.0 and telemetry.lon == 0.0 and (telemetry.grid_x != 0 or telemetry.grid_y != 0):
                telemetry.lat = telemetry.grid_y / 1000.0
                telemetry.lon = telemetry.grid_x / 1000.0
            telemetry.flags = int(event.get("flags", telemetry.flags))
            telemetry.heartbeat_history.append(event_time)
            # Keep history bounded
            if len(telemetry.heartbeat_history) > 10:
                telemetry.heartbeat_history.pop(0)

            # Record neighbour digests
            nbrs = event.get("neighbours", [])
            telemetry.recent_neighbours = nbrs
            for n in nbrs:
                edge = (min(nid, n), max(nid, n))
                self.edges[edge] = event_time

        elif mtype == "SOS":
            # Handled by real-world lat/lon check above
            pass

        elif mtype == "OK_NOW":
            telemetry.last_message_type = "OK_NOW"

        elif mtype == "LOWBAT":
            telemetry.lowbat_reason = event.get("reason", "BATTERY_DEPLETED")
            telemetry.grid_x = int(event.get("last_grid_x", telemetry.grid_x))
            telemetry.grid_y = int(event.get("last_grid_y", telemetry.grid_y))

    def prune_old_edges(self, now: float) -> None:
        cutoff = now - self.window_seconds
        stale_edges = [edge for edge, ts in self.edges.items() if ts < cutoff]
        for edge in stale_edges:
            del self.edges[edge]

    def get_active_nodes(self, now: float, max_idle: float = 180.0) -> List[NodeTelemetry]:
        return [
            node
            for node in self.nodes.values()
            if (now - node.last_seen) <= max_idle and node.last_message_type != "LOWBAT"
        ]
