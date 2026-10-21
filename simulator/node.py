"""
MeshSOS Virtual Node for Network Simulator.
Represents a physical device with radio, battery, GPS coordinates,
periodic heartbeat generator, and relay core.
"""

from __future__ import annotations
import math
import random
from typing import Dict, List, Optional, Set, Tuple

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from protocol.mesh_protocol import (
    Header,
    HeartbeatFlags,
    HeartbeatMessage,
    LowbatMessage,
    LowbatReason,
    MessageType,
    SosCategory,
    SosMessage,
    AckMessage,
    MeshMessage,
)
from simulator.relay_core import RelayCore, RelayCoreConfig


class SimNode:
    def __init__(
        self,
        node_id: int,
        x: float,
        y: float,
        radio_range: float = 75.0,  # meters
        heartbeat_interval: float = 15.0,  # seconds
        battery: float = 100.0,
        is_gateway: bool = False,
        relay_config: Optional[RelayCoreConfig] = None,
    ):
        self.node_id = node_id
        self.x = x
        self.y = y
        self.radio_range = radio_range
        self.heartbeat_interval = heartbeat_interval
        self.battery = battery
        self.is_gateway = is_gateway
        self.is_alive = True
        self.is_moving = False
        self.vx = 0.0
        self.vy = 0.0

        self.seq = 0
        self.relay = RelayCore(node_id, relay_config)

        # Neighbour table: neighbour_id -> (last_seen_time, rssi_estimate)
        self.neighbours: Dict[int, Tuple[float, float]] = {}

        # Energy & Radio proxy counters
        self.tx_count = 0
        self.rx_count = 0
        self.radio_on_seconds = 0.0

        # Heartbeat timer
        self.next_heartbeat_time = 0.0

    def next_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFFFF
        if self.seq == 0:
            self.seq = 1
        return self.seq

    def distance_to(self, other: SimNode) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def can_hear(self, other: SimNode) -> bool:
        if not self.is_alive or not other.is_alive:
            return False
        return self.distance_to(other) <= other.radio_range

    def update_position(self, dt: float, min_x: float = 0, max_x: float = 1000, min_y: float = 0, max_y: float = 1000):
        if not self.is_alive or not self.is_moving:
            return
        self.x += self.vx * dt
        self.y += self.vy * dt
        # Bounce off boundaries
        if self.x < min_x:
            self.x = min_x
            self.vx = -self.vx
        elif self.x > max_x:
            self.x = max_x
            self.vx = -self.vx
        if self.y < min_y:
            self.y = min_y
            self.vy = -self.vy
        elif self.y > max_y:
            self.y = max_y
            self.vy = -self.vy

    def record_neighbour(self, neighbour_id: int, now: float, distance: float):
        # Rough RSSI proxy: -40 dBm at 1m to -95 dBm at max range
        rssi = -40.0 - (50.0 * (distance / max(1.0, self.radio_range)))
        is_new = neighbour_id not in self.neighbours or (now - self.neighbours[neighbour_id][0] > 60.0)
        self.neighbours[neighbour_id] = (now, rssi)
        if is_new:
            return self.relay.on_new_neighbour_discovered(neighbour_id, now)
        return []

    def clean_stale_neighbours(self, now: float, timeout: float = 45.0):
        stale = [nid for nid, (ts, _) in self.neighbours.items() if now - ts > timeout]
        for nid in stale:
            del self.neighbours[nid]

    def create_heartbeat(self, now: float, ttl: int = 4) -> HeartbeatMessage:
        self.clean_stale_neighbours(now)
        # Select up to 3 strongest recent neighbours
        sorted_nbrs = sorted(self.neighbours.items(), key=lambda item: item[1][1], reverse=True)
        digest = [nid & 0xFFFF for nid, _ in sorted_nbrs[:3]]

        flags = 0
        if self.is_moving:
            flags |= HeartbeatFlags.MOVING
        if self.is_gateway:
            flags |= HeartbeatFlags.GATEWAY_CAPABLE

        grid_x = int(self.x // 100)
        grid_y = int(self.y // 100)

        header = Header(
            version=1,
            msg_type=MessageType.HEARTBEAT,
            node_id=self.node_id,
            seq=self.next_seq(),
            ttl=ttl,
            timestamp=int(now),
        )
        return HeartbeatMessage(
            header=header,
            grid_x=grid_x,
            grid_y=grid_y,
            battery=max(0, min(100, int(self.battery))),
            flags=flags,
            neighbours=digest,
        )

    def create_sos(
        self,
        now: float,
        text: str = "HELP NEEDED",
        category: SosCategory = SosCategory.OTHER,
        ttl: int = 8,
        pubkey: bytes = b"\x00" * 32,
        signature: bytes = b"\x00" * 64,
    ) -> SosMessage:
        header = Header(
            version=1,
            msg_type=MessageType.SOS,
            node_id=self.node_id,
            seq=self.next_seq(),
            ttl=ttl,
            timestamp=int(now),
        )
        # Approx lat/lon mapping for simulation
        lat = 37.7749 + (self.y / 111000.0)
        lon = -122.4194 + (self.x / (111000.0 * math.cos(math.radians(37.7749))))
        return SosMessage(
            header=header,
            lat=lat,
            lon=lon,
            accuracy=5.0,
            category=category,
            battery=max(0, min(100, int(self.battery))),
            text=text,
            pubkey=pubkey,
            signature=signature,
        )

    def create_lowbat(self, now: float, reason: LowbatReason, ttl: int = 4) -> LowbatMessage:
        header = Header(
            version=1,
            msg_type=MessageType.LOWBAT,
            node_id=self.node_id,
            seq=self.next_seq(),
            ttl=ttl,
            timestamp=int(now),
        )
        return LowbatMessage(
            header=header,
            reason=reason,
            battery=max(0, min(100, int(self.battery))),
            last_grid_x=int(self.x // 100),
            last_grid_y=int(self.y // 100),
        )
