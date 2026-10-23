"""
MeshSOS Inference Engine.
Central coordinator aggregating incoming mesh event streams, maintaining the contact graph,
and generating the 3 critical map layers (Silent Zones, SOS Hotspots, Active Coverage).
"""

from __future__ import annotations
import time
from typing import Any, Dict, List, Optional

from inference.contact_graph import ContactGraph
from inference.silent_zone_detector import SilentZoneDetector, SilentZone
from inference.sos_hotspots import SosHotspotDetector, SosHotspot
from inference.active_coverage import ActiveCoverageAggregator, CoverageCell


class InferenceEngine:
    def __init__(
        self,
        window_seconds: float = 300.0,
        overdue_multiplier: float = 3.0,
        cluster_radius_meters: float = 250.0,
        min_cluster_size: int = 3,
    ):
        self.graph = ContactGraph(window_seconds=window_seconds)
        self.silent_detector = SilentZoneDetector(
            contact_graph=self.graph,
            overdue_multiplier=overdue_multiplier,
            cluster_radius_meters=cluster_radius_meters,
            min_cluster_size=min_cluster_size,
        )
        self.sos_detector = SosHotspotDetector()
        self.coverage_aggregator = ActiveCoverageAggregator(contact_graph=self.graph)
        self.last_evaluation_time: float = 0.0

    def ingest_event(self, event: dict, now: Optional[float] = None) -> None:
        """Ingest a single event dictionary (HEARTBEAT, SOS, OK_NOW, LOWBAT, ACK)."""
        mtype = event.get("type", "HEARTBEAT")
        ts = float(event.get("gateway_received_at", event.get("timestamp", time.time())))
        if now is None:
            now = ts

        # Update contact graph
        self.graph.ingest_event(event, now=now)

        # Route to SOS detector
        if mtype == "SOS":
            ev_lat = float(event.get("lat", 0.0))
            ev_lon = float(event.get("lon", 0.0))
            if ev_lat == 0.0 and ev_lon == 0.0:
                # 1. Borrow from contact graph telemetry if previously heard
                tele = self.graph.nodes.get(int(event["node_id"]))
                if tele and (tele.lat != 0.0 or tele.lon != 0.0):
                    event["lat"] = tele.lat
                    event["lon"] = tele.lon
                # 2. Borrow from the gateway node that heard this BLE relay
                elif "gateway_id" in event:
                    gw_tele = self.graph.nodes.get(int(event["gateway_id"]))
                    if gw_tele and (gw_tele.lat != 0.0 or gw_tele.lon != 0.0):
                        event["lat"] = gw_tele.lat
                        event["lon"] = gw_tele.lon
            # After all fallback attempts, reject (0,0) — Null Island pins help nobody
            final_lat = float(event.get("lat", 0.0))
            final_lon = float(event.get("lon", 0.0))
            if final_lat == 0.0 and final_lon == 0.0:
                return  # Skip — no usable location for this SOS
            self.sos_detector.ingest_sos(event)
        elif mtype == "OK_NOW":
            self.sos_detector.ingest_ok_now(event)

    def ingest_batch(self, events: List[dict], now: Optional[float] = None) -> None:
        for ev in events:
            self.ingest_event(ev, now=now)

    def evaluate(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Compute the 3 layers and export GeoJSON FeatureCollections."""
        if now is None:
            now = time.time()
        self.last_evaluation_time = now

        # Prune stale graph edges
        self.graph.prune_old_edges(now)

        silent_zones = self.silent_detector.detect_silent_zones(now)
        sos_hotspots = self.sos_detector.get_hotspots()
        active_cells = self.coverage_aggregator.get_coverage_layer(now)

        silent_geojson = {
            "type": "FeatureCollection",
            "features": [z.to_geojson_feature() for z in silent_zones],
        }
        sos_geojson = {
            "type": "FeatureCollection",
            "features": [h.to_geojson_feature() for h in sos_hotspots],
        }
        coverage_geojson = {
            "type": "FeatureCollection",
            "features": [c.to_geojson_feature() for c in active_cells],
        }

        # Build contact graph payload for UI visualization
        graph_nodes = []
        for nid, tele in self.graph.nodes.items():
            graph_nodes.append({
                "id": nid,
                "lat": tele.lat,
                "lon": tele.lon,
                "battery": tele.battery,
                "last_seen": tele.last_seen,
                "status": "reporting" if (now - tele.last_seen <= 180.0 and tele.last_message_type != "LOWBAT") else ("lowbat" if tele.last_message_type == "LOWBAT" else "silent"),
                "expected_interval": round(tele.expected_interval, 1),
                "last_type": tele.last_message_type,
            })

        graph_edges = [
            {"source": u, "target": v, "last_seen": ts}
            for (u, v), ts in self.graph.edges.items()
        ]

        active_count = sum(1 for n in graph_nodes if n["status"] == "reporting")
        silent_count = sum(1 for n in graph_nodes if n["status"] == "silent")

        return {
            "evaluated_at": now,
            "silent_zones": silent_geojson,
            "sos_hotspots": sos_geojson,
            "active_coverage": coverage_geojson,
            "contact_graph": {
                "nodes": graph_nodes,
                "edges": graph_edges,
            },
            "stats": {
                "total_nodes_tracked": len(self.graph.nodes),
                "active_nodes_count": active_count,
                "silent_nodes_count": silent_count,
                "silent_zones_count": len(silent_zones),
                "sos_hotspots_count": len(sos_hotspots),
            },
        }
