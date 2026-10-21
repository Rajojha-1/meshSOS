"""
MeshSOS Silent Zone Detector.
Infers catastrophic collapse, flood surges, or cut-off areas from group silence.
Distinguishes benign silence (battery depleted, user stopped, single walker)
and mesh-wide infrastructure outages from localized sudden disasters.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from inference.contact_graph import ContactGraph, NodeTelemetry


@dataclass
class SilentZone:
    zone_id: str
    confidence: float
    center_lat: float
    center_lon: float
    polygon_coords: List[Tuple[float, float]]  # [(lon, lat), ...]
    n_silent: int
    silent_node_ids: List[int]
    last_heard_span_sec: float
    neighbours_alive_count: int
    reason_text: str
    simultaneity_score: float

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "properties": {
                "zone_id": self.zone_id,
                "confidence": round(self.confidence, 3),
                "n_silent": self.n_silent,
                "silent_node_ids": self.silent_node_ids,
                "neighbours_alive": self.neighbours_alive_count,
                "last_heard_span_sec": round(self.last_heard_span_sec, 1),
                "reason": self.reason_text,
                "layer": "silent_zones",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [self.polygon_coords],
            },
        }


class SilentZoneDetector:
    def __init__(
        self,
        contact_graph: ContactGraph,
        overdue_multiplier: float = 3.0,
        cluster_radius_meters: float = 250.0,
        simultaneity_window_sec: float = 120.0,
        min_cluster_size: int = 3,
    ):
        self.graph = contact_graph
        self.overdue_multiplier = overdue_multiplier
        self.cluster_radius_meters = cluster_radius_meters
        self.simultaneity_window_sec = simultaneity_window_sec
        self.min_cluster_size = min_cluster_size

    def detect_silent_zones(self, now: float) -> List[SilentZone]:
        # Step 1 & 2: Identify overdue nodes
        overdue_nodes: List[NodeTelemetry] = []
        active_nodes: List[NodeTelemetry] = []

        for node in self.graph.nodes.values():
            # Step 3: Exclude benign silence (LOWBAT: battery died or user stopped)
            if node.last_message_type == "LOWBAT":
                continue

            time_since_seen = now - node.last_seen
            threshold = node.expected_interval * self.overdue_multiplier

            # Must have sent at least one heartbeat
            if len(node.heartbeat_history) == 0:
                continue

            if time_since_seen > threshold:
                overdue_nodes.append(node)
            else:
                active_nodes.append(node)

        if not overdue_nodes:
            return []

        # Step 4: Spatial clustering of overdue nodes
        clusters = self._cluster_nodes(overdue_nodes)

        silent_zones = []
        for cluster_idx, cluster in enumerate(clusters):
            n_silent = len(cluster)

            # Rule: A single silent node must NEVER produce high confidence!
            if n_silent < self.min_cluster_size:
                # Can evaluate as negligible or low-confidence anomaly, but do not raise high alert
                continue

            # Calculate simultaneity (how closely last-seen times cluster together)
            last_seen_times = [node.last_seen for node in cluster]
            min_seen = min(last_seen_times)
            max_seen = max(last_seen_times)
            time_span = max_seen - min_seen

            # Simultaneity score in [0.0, 1.0]
            # Higher when span is short relative to simultaneity window
            simultaneity = math.exp(-time_span / max(10.0, self.simultaneity_window_sec))

            # Center position
            avg_x = sum(node.grid_x for node in cluster) / n_silent
            avg_y = sum(node.grid_y for node in cluster) / n_silent
            center_lat = sum(node.lat for node in cluster) / n_silent
            center_lon = sum(node.lon for node in cluster) / n_silent

            # Neighbours alive: Count active nodes within 1.5x cluster radius of this zone
            neighbours_alive = 0
            for act in active_nodes:
                dist_m = math.hypot((act.grid_x - avg_x) * 100.0, (act.grid_y - avg_y) * 100.0)
                if dist_m <= self.cluster_radius_meters * 1.5:
                    neighbours_alive += 1

            # Mesh-wide outage check:
            # If 0 active nodes report across the entire mesh, this is a whole mesh / gateway disconnect,
            # NOT a localized physical collapse!
            total_active_mesh = len(active_nodes)
            if total_active_mesh == 0:
                # Mesh is completely dead / gateway partitioned; suppress high confidence
                confidence = 0.05
                reason = f"{n_silent} phones silent, but zero phones reporting anywhere on mesh (global network outage / partition)"
            else:
                # Step 5: Transparent explainable confidence score
                # Base components:
                # 1. Size factor f(n): scales from 0 at n=min_size up to 1.0 at n=12+
                size_factor = min(1.0, max(0.0, (n_silent - self.min_cluster_size + 1) / 8.0))

                # 2. Simultaneity factor
                sim_factor = simultaneity

                # 3. Contextual contrast factor (presence of living neighbours nearby confirms local event)
                # If neighbours_alive >= 2, strong proof that the rest of the surrounding mesh is working
                alive_factor = min(1.0, neighbours_alive / 2.0)

                confidence = (0.45 * size_factor) + (0.35 * sim_factor) + (0.20 * alive_factor)
                confidence = max(0.0, min(1.0, confidence))

                reason = (
                    f"{n_silent} phones went silent within {time_span:.1f}s, "
                    f"with {neighbours_alive} neighbouring phones still actively reporting"
                )

            # Generate polygon bounding box / footprint for GeoJSON
            delta_deg = (self.cluster_radius_meters / 111000.0) * 0.8
            polygon = [
                (center_lon - delta_deg, center_lat - delta_deg),
                (center_lon + delta_deg, center_lat - delta_deg),
                (center_lon + delta_deg, center_lat + delta_deg),
                (center_lon - delta_deg, center_lat + delta_deg),
                (center_lon - delta_deg, center_lat - delta_deg),
            ]

            zone = SilentZone(
                zone_id=f"zone-{int(now)}-{cluster_idx+1}",
                confidence=confidence,
                center_lat=center_lat,
                center_lon=center_lon,
                polygon_coords=polygon,
                n_silent=n_silent,
                silent_node_ids=[node.node_id for node in cluster],
                last_heard_span_sec=time_span,
                neighbours_alive_count=neighbours_alive,
                reason_text=reason,
                simultaneity_score=simultaneity,
            )
            silent_zones.append(zone)

        # Sort highest confidence first
        silent_zones.sort(key=lambda z: z.confidence, reverse=True)
        return silent_zones

    def _cluster_nodes(self, nodes: List[NodeTelemetry]) -> List[List[NodeTelemetry]]:
        """Cluster overdue nodes by spatial proximity using connected-component BFS."""
        visited: Set[int] = set()
        clusters: List[List[NodeTelemetry]] = []

        for node in nodes:
            if node.node_id in visited:
                continue

            cluster: List[NodeTelemetry] = []
            queue = [node]
            visited.add(node.node_id)

            while queue:
                curr = queue.pop(0)
                cluster.append(curr)

                for other in nodes:
                    if other.node_id in visited:
                        continue
                    dist_m = math.hypot((curr.grid_x - other.grid_x) * 100.0, (curr.grid_y - other.grid_y) * 100.0)
                    if dist_m <= self.cluster_radius_meters:
                        visited.add(other.node_id)
                        queue.append(other)

            clusters.append(cluster)
        return clusters
