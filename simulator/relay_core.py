"""
MeshSOS Relay Core.
Transport-independent routing, deduplication, suppression, priority queueing,
store-and-forward, and rate limiting logic.
Designed to run identically in the simulator (Python) and Android app (Kotlin).
"""

from __future__ import annotations
import heapq
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional, Set, Tuple

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from protocol.mesh_protocol import (
    Header,
    MeshMessage,
    MessageType,
    SosMessage,
    OkNowMessage,
    LowbatMessage,
    HeartbeatMessage,
    AckMessage,
)


class PriorityLevel(IntEnum):
    SOS = 0
    OK_NOW = 1
    LOWBAT = 2
    HEARTBEAT = 3


def get_message_priority(msg_type: MessageType) -> PriorityLevel:
    if msg_type == MessageType.SOS:
        return PriorityLevel.SOS
    elif msg_type == MessageType.OK_NOW:
        return PriorityLevel.OK_NOW
    elif msg_type == MessageType.LOWBAT:
        return PriorityLevel.LOWBAT
    else:
        return PriorityLevel.HEARTBEAT


@dataclass(order=True)
class QueuedMessage:
    priority: int
    scheduled_time: float
    message: MeshMessage = field(compare=False)
    overheard_count: int = field(default=0, compare=False)
    k_suppression_threshold: int = field(default=2, compare=False)
    suppression_enabled: bool = field(default=True, compare=False)


class BoundedDedupeCache:
    """Bounded LRU cache for message deduplication by (node_id, seq)."""

    def __init__(self, max_size: int = 2048):
        self.max_size = max_size
        self._cache: OrderedDict[Tuple[int, int], float] = OrderedDict()

    def has_seen(self, node_id: int, seq: int) -> bool:
        key = (node_id, seq)
        if key in self._cache:
            self._cache.move_to_end(key)
            return True
        return False

    def mark_seen(self, node_id: int, seq: int, now: float) -> None:
        key = (node_id, seq)
        self._cache[key] = now
        self._cache.move_to_end(key)
        if len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def size(self) -> int:
        return len(self._cache)


class RateLimiter:
    """Sliding-window per-node rate limiter to prevent flooding attacks."""

    def __init__(self, max_per_minute: int = 15):
        self.max_per_minute = max_per_minute
        self._history: Dict[int, List[float]] = {}

    def is_allowed(self, node_id: int, now: float) -> bool:
        window_start = now - 60.0
        timestamps = self._history.get(node_id, [])
        # Prune older than 60s
        timestamps = [t for t in timestamps if t >= window_start]
        self._history[node_id] = timestamps
        if len(timestamps) >= self.max_per_minute:
            return False
        timestamps.append(now)
        return True


class StoreAndForwardStore:
    """Stores critical messages (SOS, OK_NOW, LOWBAT) with expiration."""

    def __init__(self, expiry_seconds: float = 6 * 3600.0):
        self.expiry_seconds = expiry_seconds
        # key: (node_id, seq) -> (message, expiry_time, last_retry_time)
        self.store: Dict[Tuple[int, int], Tuple[MeshMessage, float, float]] = {}

    def put(self, message: MeshMessage, now: float) -> None:
        key = (message.header.node_id, message.header.seq)
        self.store[key] = (message, now + self.expiry_seconds, now)

    def remove(self, node_id: int, seq: int) -> None:
        self.store.pop((node_id, seq), None)

    def get_pending_messages(self, now: float, min_retry_interval: float = 10.0) -> List[MeshMessage]:
        expired_keys = [k for k, v in self.store.items() if now > v[1]]
        for k in expired_keys:
            del self.store[k]

        candidates = []
        for k, (msg, expiry, last_retry) in list(self.store.items()):
            if now - last_retry >= min_retry_interval:
                candidates.append(msg)
                self.store[k] = (msg, expiry, now)
        return candidates


class RelayCoreConfig:
    def __init__(
        self,
        enable_suppression: bool = True,
        k_heartbeat: int = 2,
        k_lowbat: int = 2,
        k_sos: int = 0,  # 0 means NO suppression for SOS by default (safety first)
        delay_min_ms: float = 50.0,
        delay_max_ms: float = 400.0,
        sos_delay_max_ms: float = 80.0,
        max_rate_per_min: int = 20,
        dedupe_cache_size: int = 2048,
    ):
        self.enable_suppression = enable_suppression
        self.k_heartbeat = k_heartbeat
        self.k_lowbat = k_lowbat
        self.k_sos = k_sos
        self.delay_min_ms = delay_min_ms
        self.delay_max_ms = delay_max_ms
        self.sos_delay_max_ms = sos_delay_max_ms
        self.max_rate_per_min = max_rate_per_min
        self.dedupe_cache_size = dedupe_cache_size


class RelayCore:
    """
    Main relay engine instance per node.
    Evaluates inbound messages, applies deduplication, TTL decrement,
    counter-based suppression, ACK routing, and queues messages for transmission.
    """

    def __init__(self, node_id: int, config: Optional[RelayCoreConfig] = None):
        self.node_id = node_id
        self.config = config or RelayCoreConfig()
        self.dedupe_cache = BoundedDedupeCache(max_size=self.config.dedupe_cache_size)
        self.rate_limiter = RateLimiter(max_per_minute=self.config.max_rate_per_min)
        self.store_and_forward = StoreAndForwardStore()
        self.acked_sos: Set[Tuple[int, int]] = set()

        # Scheduled pending transmissions: key (node_id, seq) -> QueuedMessage
        self.pending_transmissions: Dict[Tuple[int, int], QueuedMessage] = {}

        # Statistical counters
        self.stats = {
            "received_total": 0,
            "dedupe_dropped": 0,
            "ttl_expired": 0,
            "rate_limited": 0,
            "suppressed": 0,
            "forwarded": 0,
            "acks_processed": 0,
        }

    def on_receive(
        self,
        message: MeshMessage,
        now: float,
        random_fraction: float = 0.5,
    ) -> Optional[QueuedMessage]:
        """
        Process a received message. Returns a QueuedMessage if scheduled for forward, or None.
        `random_fraction` is in [0.0, 1.0] used for jitter calculation.
        """
        self.stats["received_total"] += 1
        hdr = message.header
        msg_key = (hdr.node_id, hdr.seq)

        # 1. Check ACK processing
        if hdr.msg_type == MessageType.ACK:
            ack: AckMessage = message  # type: ignore
            ack_target = (ack.acked_node_id, ack.acked_seq)
            self.acked_sos.add(ack_target)
            self.store_and_forward.remove(ack.acked_node_id, ack.acked_seq)
            # If we had a pending transmission for this SOS, cancel it
            self.pending_transmissions.pop(ack_target, None)
            self.stats["acks_processed"] += 1

        # 2. Check if this is an overheard duplicate for a pending transmission (Counter-based suppression)
        if msg_key in self.pending_transmissions:
            pending = self.pending_transmissions[msg_key]
            pending.overheard_count += 1
            # If overheard count has reached or exceeded k threshold, suppress!
            if pending.suppression_enabled and pending.k_suppression_threshold > 0:
                if pending.overheard_count >= pending.k_suppression_threshold:
                    self.stats["suppressed"] += 1
                    del self.pending_transmissions[msg_key]
            return None

        # 3. Deduplication check
        if self.dedupe_cache.has_seen(hdr.node_id, hdr.seq):
            self.stats["dedupe_dropped"] += 1
            return None

        # Mark as seen in dedupe cache
        self.dedupe_cache.mark_seen(hdr.node_id, hdr.seq, now)

        # 4. Rate limiting check (origin-based)
        if not self.rate_limiter.is_allowed(hdr.node_id, now):
            self.stats["rate_limited"] += 1
            return None

        # 5. Check if already ACKed (for SOS)
        if hdr.msg_type == MessageType.SOS and msg_key in self.acked_sos:
            return None

        # 6. TTL check and decrement
        if hdr.ttl <= 1:
            self.stats["ttl_expired"] += 1
            return None

        # Decrement TTL for forwarding
        forward_hdr = Header(
            version=hdr.version,
            msg_type=hdr.msg_type,
            node_id=hdr.node_id,
            seq=hdr.seq,
            ttl=hdr.ttl - 1,
            timestamp=hdr.timestamp,
        )

        forward_msg: MeshMessage
        if hdr.msg_type == MessageType.HEARTBEAT:
            hb: HeartbeatMessage = message  # type: ignore
            forward_msg = HeartbeatMessage(
                header=forward_hdr,
                grid_x=hb.grid_x,
                grid_y=hb.grid_y,
                battery=hb.battery,
                flags=hb.flags,
                neighbours=hb.neighbours,
            )
        elif hdr.msg_type == MessageType.SOS:
            sos: SosMessage = message  # type: ignore
            forward_msg = SosMessage(
                header=forward_hdr,
                lat=sos.lat,
                lon=sos.lon,
                accuracy=sos.accuracy,
                category=sos.category,
                battery=sos.battery,
                text=sos.text,
                pubkey=sos.pubkey,
                signature=sos.signature,
            )
        elif hdr.msg_type == MessageType.OK_NOW:
            ok: OkNowMessage = message  # type: ignore
            forward_msg = OkNowMessage(
                header=forward_hdr,
                target_sos_seq=ok.target_sos_seq,
                battery=ok.battery,
                text=ok.text,
                pubkey=ok.pubkey,
                signature=ok.signature,
            )
        elif hdr.msg_type == MessageType.LOWBAT:
            lb: LowbatMessage = message  # type: ignore
            forward_msg = LowbatMessage(
                header=forward_hdr,
                reason=lb.reason,
                battery=lb.battery,
                last_grid_x=lb.last_grid_x,
                last_grid_y=lb.last_grid_y,
            )
        elif hdr.msg_type == MessageType.ACK:
            ack_msg: AckMessage = message  # type: ignore
            forward_msg = AckMessage(
                header=forward_hdr,
                acked_node_id=ack_msg.acked_node_id,
                acked_seq=ack_msg.acked_seq,
                gateway_id=ack_msg.gateway_id,
                hop_count=ack_msg.hop_count + 1,
                status=ack_msg.status,
            )
        else:
            return None

        # 7. Add to Store & Forward if critical message
        if hdr.msg_type in (MessageType.SOS, MessageType.OK_NOW, MessageType.LOWBAT):
            self.store_and_forward.put(forward_msg, now)

        # 8. Compute suppression parameters & random backoff jitter
        prio = get_message_priority(hdr.msg_type)
        if hdr.msg_type == MessageType.SOS:
            delay_span = (self.config.sos_delay_max_ms - self.config.delay_min_ms) / 1000.0
            delay = (self.config.delay_min_ms / 1000.0) + (random_fraction * max(0.005, delay_span))
            k_val = self.config.k_sos
            suppression_on = (k_val > 0) and self.config.enable_suppression
        elif hdr.msg_type == MessageType.LOWBAT:
            delay_span = (self.config.delay_max_ms - self.config.delay_min_ms) / 1000.0
            delay = (self.config.delay_min_ms / 1000.0) + (random_fraction * delay_span)
            k_val = self.config.k_lowbat
            suppression_on = self.config.enable_suppression
        else:
            delay_span = (self.config.delay_max_ms - self.config.delay_min_ms) / 1000.0
            delay = (self.config.delay_min_ms / 1000.0) + (random_fraction * delay_span)
            k_val = self.config.k_heartbeat
            suppression_on = self.config.enable_suppression

        scheduled_time = now + delay
        queued = QueuedMessage(
            priority=int(prio),
            scheduled_time=scheduled_time,
            message=forward_msg,
            overheard_count=0,
            k_suppression_threshold=k_val,
            suppression_enabled=suppression_on,
        )
        self.pending_transmissions[msg_key] = queued
        return queued

    def pop_ready_transmission(self, now: float) -> Optional[MeshMessage]:
        """Check if any pending transmission has matured past scheduled_time."""
        ready_keys = [
            k for k, q in self.pending_transmissions.items() if now >= q.scheduled_time
        ]
        if not ready_keys:
            return None

        # Pick highest priority (lowest integer value) among ready
        ready_keys.sort(key=lambda k: (self.pending_transmissions[k].priority, self.pending_transmissions[k].scheduled_time))
        best_key = ready_keys[0]
        queued = self.pending_transmissions.pop(best_key)
        self.stats["forwarded"] += 1
        return queued.message

    def on_new_neighbour_discovered(self, neighbour_id: int, now: float) -> List[MeshMessage]:
        """Triggered when a new neighbour enters range to flush store-and-forward."""
        pending = self.store_and_forward.get_pending_messages(now)
        return pending
