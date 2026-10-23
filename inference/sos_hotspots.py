"""
MeshSOS SOS Hotspots Layer Generator.
Aggregates and clusters active emergency SOS alerts by proximity, category, and recency.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class SosAlert:
    node_id: int
    seq: int
    lat: float
    lon: float
    accuracy: float
    category: str
    battery: int
    text: str
    timestamp: float
    is_cancelled: bool = False


@dataclass
class SosHotspot:
    hotspot_id: str
    center_lat: float
    center_lon: float
    count: int
    categories: Dict[str, int]
    urgency_score: float
    alerts: List[SosAlert]

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "properties": {
                "hotspot_id": self.hotspot_id,
                "count": self.count,
                "categories": self.categories,
                "urgency_score": round(self.urgency_score, 2),
                "layer": "sos_hotspots",
            },
            "geometry": {
                "type": "Point",
                "coordinates": [self.center_lon, self.center_lat],
            },
        }


class SosHotspotDetector:
    def __init__(self, cluster_distance_meters: float = 150.0):
        self.cluster_distance_meters = cluster_distance_meters
        self.alerts: Dict[Tuple[int, int], SosAlert] = {}

    def ingest_sos(self, event: dict):
        lat = float(event.get("lat", 0.0))
        lon = float(event.get("lon", 0.0))
        key = (int(event["node_id"]), int(event["seq"]))
        self.alerts[key] = SosAlert(
            node_id=int(event["node_id"]),
            seq=int(event["seq"]),
            lat=lat,
            lon=lon,
            accuracy=float(event.get("accuracy", 5.0)),
            category=str(event.get("category", "OTHER")),
            battery=int(event.get("battery", 100)),
            text=str(event.get("text", "")),
            timestamp=float(event.get("timestamp", 0.0)),
        )

    def ingest_ok_now(self, event: dict):
        nid = int(event["node_id"])
        target_seq = int(event.get("target_sos_seq", 0))
        # Cancel all active SOS alerts originating from this node
        for alert in self.alerts.values():
            if alert.node_id == nid:
                alert.is_cancelled = True
        if target_seq > 0 and (nid, target_seq) in self.alerts:
            self.alerts[(nid, target_seq)].is_cancelled = True

    def get_hotspots(self) -> List[SosHotspot]:
        active_alerts = [a for a in self.alerts.values() if not a.is_cancelled]
        if not active_alerts:
            return []

        clusters: List[List[SosAlert]] = []
        visited: Set[Tuple[int, int]] = set()

        for i, a in enumerate(active_alerts):
            k = (a.node_id, a.seq)
            if k in visited:
                continue
            visited.add(k)
            cluster = [a]

            for other in active_alerts[i + 1 :]:
                ok = (other.node_id, other.seq)
                if ok in visited:
                    continue
                # Rough distance
                dist_m = math.hypot((a.lat - other.lat) * 111000.0, (a.lon - other.lon) * 88000.0)
                if dist_m <= self.cluster_distance_meters:
                    visited.add(ok)
                    cluster.append(other)
            clusters.append(cluster)

        hotspots = []
        for idx, cl in enumerate(clusters):
            c_lat = sum(a.lat for a in cl) / len(cl)
            c_lon = sum(a.lon for a in cl) / len(cl)
            cat_counts: Dict[str, int] = {}
            for a in cl:
                cat_counts[a.category] = cat_counts.get(a.category, 0) + 1

            # Weight severity: TRAPPED=4, FLOOD=3, MEDICAL=3, COLLAPSE=4, OTHER=1
            severity_weights = {"TRAPPED": 4.0, "COLLAPSE": 4.0, "FLOOD": 3.0, "MEDICAL": 3.0, "OTHER": 1.0}
            urgency = sum(severity_weights.get(a.category, 1.0) for a in cl)

            hotspots.append(
                SosHotspot(
                    hotspot_id=f"hotspot-{idx+1}",
                    center_lat=c_lat,
                    center_lon=c_lon,
                    count=len(cl),
                    categories=cat_counts,
                    urgency_score=urgency,
                    alerts=cl,
                )
            )

        hotspots.sort(key=lambda h: h.urgency_score, reverse=True)
        return hotspots
