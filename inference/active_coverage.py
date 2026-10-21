"""
MeshSOS Active Coverage Layer Generator.
Aggregates cells where nodes are actively transmitting heartbeats.
CRITICAL DESIGN RULE: Always labeled "active" or "reporting", NEVER "safe".
Surfaces moving/stationary state as a weak hint.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from inference.contact_graph import ContactGraph, NodeTelemetry


@dataclass
class CoverageCell:
    grid_x: int
    grid_y: int
    center_lat: float
    center_lon: float
    node_count: int
    status: str = "active/reporting"  # NEVER "safe"
    moving_count: int = 0
    stationary_count: int = 0
    node_ids: List[int] = field(default_factory=list)

    def to_geojson_feature(self) -> dict:
        delta_deg = (50.0 / 111000.0)
        polygon = [
            (self.center_lon - delta_deg, self.center_lat - delta_deg),
            (self.center_lon + delta_deg, self.center_lat - delta_deg),
            (self.center_lon + delta_deg, self.center_lat + delta_deg),
            (self.center_lon - delta_deg, self.center_lat + delta_deg),
            (self.center_lon - delta_deg, self.center_lat - delta_deg),
        ]
        return {
            "type": "Feature",
            "properties": {
                "cell": f"({self.grid_x},{self.grid_y})",
                "node_count": self.node_count,
                "status": self.status,
                "moving": self.moving_count,
                "stationary": self.stationary_count,
                "node_ids": self.node_ids,
                "layer": "active_coverage",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [polygon],
            },
        }


class ActiveCoverageAggregator:
    def __init__(self, contact_graph: ContactGraph, active_timeout_sec: float = 60.0):
        self.graph = contact_graph
        self.active_timeout_sec = active_timeout_sec

    def get_coverage_layer(self, now: float) -> List[CoverageCell]:
        active_nodes = self.graph.get_active_nodes(now, max_idle=self.active_timeout_sec)
        cells_map: Dict[Tuple[int, int], List[NodeTelemetry]] = {}

        for n in active_nodes:
            key = (n.grid_x, n.grid_y)
            if key not in cells_map:
                cells_map[key] = []
            cells_map[key].append(n)

        coverage_cells = []
        for (gx, gy), nodes in cells_map.items():
            moving = sum(1 for n in nodes if (n.flags & 1) != 0)
            stationary = len(nodes) - moving
            c_lat = sum(n.lat for n in nodes) / len(nodes)
            c_lon = sum(n.lon for n in nodes) / len(nodes)

            cell = CoverageCell(
                grid_x=gx,
                grid_y=gy,
                center_lat=c_lat,
                center_lon=c_lon,
                node_count=len(nodes),
                status="active/reporting",  # Invariant: never "safe"
                moving_count=moving,
                stationary_count=stationary,
                node_ids=[n.node_id for n in nodes],
            )
            coverage_cells.append(cell)

        return coverage_cells
