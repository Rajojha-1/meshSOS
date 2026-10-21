"""
MeshSOS Discrete-Event Network Simulator.
Simulates multi-hop propagation, packet loss, mobility, fault injection
(collapses, battery deaths, partitions), and records routing metrics.
"""

from __future__ import annotations
import heapq
import math
import random
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from protocol.mesh_protocol import (
    Header,
    HeartbeatMessage,
    LowbatMessage,
    LowbatReason,
    MessageType,
    SosCategory,
    SosMessage,
    AckMessage,
    MeshMessage,
)
from simulator.node import SimNode
from simulator.relay_core import RelayCoreConfig


class EventType(IntEnum):
    HEARTBEAT_TICK = 1
    RADIO_TX = 2
    NODE_FORWARD_CHECK = 3
    FAULT_INJECTION = 4
    SCHEDULED_SOS = 5


@dataclass(order=True)
class SimEvent:
    timestamp: float
    event_type: int
    node_id: int = field(compare=False)
    data: Any = field(default=None, compare=False)


@dataclass
class SimulationMetrics:
    total_transmissions: int = 0
    total_receptions: int = 0
    total_sos_generated: int = 0
    total_sos_delivered_to_gateway: int = 0
    latencies_sec: List[float] = field(default_factory=list)
    hops_traversed: List[int] = field(default_factory=list)
    dedupe_drops: int = 0
    suppression_drops: int = 0
    ttl_drops: int = 0
    per_node_tx: Dict[int, int] = field(default_factory=dict)
    per_node_rx: Dict[int, int] = field(default_factory=dict)

    @property
    def sos_delivery_ratio(self) -> float:
        if self.total_sos_generated == 0:
            return 0.0
        return self.total_sos_delivered_to_gateway / self.total_sos_generated

    @property
    def avg_latency_sec(self) -> float:
        if not self.latencies_sec:
            return 0.0
        return sum(self.latencies_sec) / len(self.latencies_sec)

    @property
    def avg_hops(self) -> float:
        if not self.hops_traversed:
            return 0.0
        return sum(self.hops_traversed) / len(self.hops_traversed)

    @property
    def tx_per_delivered_message(self) -> float:
        if self.total_sos_delivered_to_gateway == 0:
            return float(self.total_transmissions)
        return self.total_transmissions / self.total_sos_delivered_to_gateway


class NetworkSimulator:
    def __init__(
        self,
        width: float = 1000.0,
        height: float = 1000.0,
        packet_loss_rate: float = 0.05,
        seed: int = 42,
    ):
        self.width = width
        self.height = height
        self.packet_loss_rate = packet_loss_rate
        self.rng = random.Random(seed)

        self.nodes: Dict[int, SimNode] = {}
        self.event_queue: List[SimEvent] = []
        self.current_time: float = 0.0

        self.metrics = SimulationMetrics()

        # Gateway log of received packets (feeds the inference engine / dashboard)
        self.gateway_received_events: List[Dict[str, Any]] = []

        # SOS tracking for latency: (node_id, seq) -> origination_time
        self.sos_origination_times: Dict[Tuple[int, int], float] = {}
        self.delivered_sos_keys: Set[Tuple[int, int]] = set()

    def add_node(self, node: SimNode):
        self.nodes[node.node_id] = node
        # Schedule initial heartbeat with slight random phase offset
        initial_hb = self.rng.uniform(0.5, max(1.0, node.heartbeat_interval))
        self.schedule_event(initial_hb, EventType.HEARTBEAT_TICK, node.node_id)

    def schedule_event(self, timestamp: float, event_type: EventType, node_id: int, data: Any = None):
        heapq.heappush(self.event_queue, SimEvent(timestamp, int(event_type), node_id, data))

    def broadcast_packet(self, sender: SimNode, message: MeshMessage, air_time_ms: float = 5.0):
        sender.tx_count += 1
        self.metrics.total_transmissions += 1
        self.metrics.per_node_tx[sender.node_id] = self.metrics.per_node_tx.get(sender.node_id, 0) + 1

        tx_time = self.current_time + (air_time_ms / 1000.0)

        for receiver in self.nodes.values():
            if receiver.node_id == sender.node_id or not receiver.is_alive:
                continue

            # Check radio propagation range
            dist = sender.distance_to(receiver)
            if dist <= sender.radio_range:
                # Stochastic channel packet loss
                if self.rng.random() < self.packet_loss_rate:
                    continue

                # Schedule reception event at receiver
                self.schedule_event(
                    tx_time,
                    EventType.RADIO_TX,
                    receiver.node_id,
                    data={"sender_id": sender.node_id, "message": message, "distance": dist},
                )

    def process_radio_rx(self, receiver: SimNode, sender_id: int, message: MeshMessage, distance: float):
        receiver.rx_count += 1
        self.metrics.total_receptions += 1
        self.metrics.per_node_rx[receiver.node_id] = self.metrics.per_node_rx.get(receiver.node_id, 0) + 1

        # Record neighbour for digests
        receiver.record_neighbour(sender_id, self.current_time, distance)

        hdr = message.header
        msg_key = (hdr.node_id, hdr.seq)

        # If receiver is a gateway, log the event for backend/inference ingestion
        if receiver.is_gateway:
            event_record = message.to_dict()
            event_record["gateway_received_at"] = self.current_time
            event_record["gateway_id"] = receiver.node_id
            self.gateway_received_events.append(event_record)

            # If SOS, track delivery metrics
            if hdr.msg_type == MessageType.SOS and msg_key not in self.delivered_sos_keys:
                self.delivered_sos_keys.add(msg_key)
                self.metrics.total_sos_delivered_to_gateway += 1
                if msg_key in self.sos_origination_times:
                    latency = self.current_time - self.sos_origination_times[msg_key]
                    self.metrics.latencies_sec.append(latency)

                # Traversed hops: default initial TTL (e.g. 8) - current TTL + 1
                orig_ttl = 8
                hops = max(1, orig_ttl - hdr.ttl + 1)
                self.metrics.hops_traversed.append(hops)

                # Gateway automatically produces and floods an ACK back
                ack = AckMessage(
                    header=Header(1, MessageType.ACK, receiver.node_id, receiver.next_seq(), 8, int(self.current_time)),
                    acked_node_id=hdr.node_id,
                    acked_seq=hdr.seq,
                    gateway_id=receiver.node_id,
                    hop_count=1,
                    status=1,  # RECEIVED_BY_GATEWAY
                )
                self.broadcast_packet(receiver, ack)

        # Pass message to node's relay core
        rand_frac = self.rng.random()
        queued = receiver.relay.on_receive(message, self.current_time, random_fraction=rand_frac)
        if queued is not None:
            # Schedule forward check at queued scheduled time
            self.schedule_event(queued.scheduled_time, EventType.NODE_FORWARD_CHECK, receiver.node_id)

    def trigger_sos(self, node_id: int, text: str = "HELP NEEDED", category: SosCategory = SosCategory.OTHER, pubkey: bytes = b"\x00"*32, sig: bytes = b"\x00"*64):
        node = self.nodes.get(node_id)
        if not node or not node.is_alive:
            return
        sos = node.create_sos(self.current_time, text=text, category=category, pubkey=pubkey, signature=sig)
        msg_key = (sos.header.node_id, sos.header.seq)
        self.sos_origination_times[msg_key] = self.current_time
        self.metrics.total_sos_generated += 1
        # Broadcast locally
        self.broadcast_packet(node, sos)

    def inject_collapse(self, center_x: float, center_y: float, radius: float):
        """Disaster collapse event: instantly destroys/disables all nodes inside region."""
        affected = []
        for node in self.nodes.values():
            if node.is_alive and math.hypot(node.x - center_x, node.y - center_y) <= radius:
                node.is_alive = False
                affected.append(node.node_id)
        return affected

    def inject_battery_death(self, node_id: int):
        """Simulates low-battery beacon followed by radio shutdown."""
        node = self.nodes.get(node_id)
        if not node or not node.is_alive:
            return
        node.battery = 3.0
        lowbat = node.create_lowbat(self.current_time, reason=LowbatReason.BATTERY_DEPLETED)
        # Broadcast final gasp beacon
        self.broadcast_packet(node, lowbat)
        # Shutdown radio
        node.is_alive = False

    def inject_user_stop(self, node_id: int):
        """User toggled stop-sharing in settings."""
        node = self.nodes.get(node_id)
        if not node or not node.is_alive:
            return
        lowbat = node.create_lowbat(self.current_time, reason=LowbatReason.USER_STOP)
        self.broadcast_packet(node, lowbat)
        node.is_alive = False

    def run_until(self, end_time: float):
        """Main simulation event loop."""
        while self.event_queue and self.event_queue[0].timestamp <= end_time:
            ev = heapq.heappop(self.event_queue)
            self.current_time = ev.timestamp
            node = self.nodes.get(ev.node_id)
            if not node:
                continue

            if ev.event_type == EventType.HEARTBEAT_TICK:
                if node.is_alive:
                    hb = node.create_heartbeat(self.current_time)
                    self.broadcast_packet(node, hb)
                    # Schedule next periodic heartbeat
                    next_time = self.current_time + node.heartbeat_interval
                    self.schedule_event(next_time, EventType.HEARTBEAT_TICK, node.node_id)

            elif ev.event_type == EventType.RADIO_TX:
                rx_data = ev.data
                self.process_radio_rx(node, rx_data["sender_id"], rx_data["message"], rx_data["distance"])

            elif ev.event_type == EventType.NODE_FORWARD_CHECK:
                if node.is_alive:
                    ready_msg = node.relay.pop_ready_transmission(self.current_time)
                    if ready_msg:
                        self.broadcast_packet(node, ready_msg)

            elif ev.event_type == EventType.SCHEDULED_SOS:
                self.trigger_sos(node.node_id, text=ev.data.get("text", "EMERGENCY"))

        # Consolidate stats from node relay cores
        for node in self.nodes.values():
            self.metrics.dedupe_drops += node.relay.stats["dedupe_dropped"]
            self.metrics.suppression_drops += node.relay.stats["suppressed"]
            self.metrics.ttl_drops += node.relay.stats["ttl_expired"]

        return self.metrics
