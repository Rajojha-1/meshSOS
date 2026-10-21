"""
MeshSOS Backend SQLite Database.
Stores all raw ingested mesh events, ACKs, and inference evaluation snapshots.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent / "meshsos.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            timestamp REAL NOT NULL,
            received_at REAL NOT NULL,
            payload_json TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_node_seq ON events(node_id, seq)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, received_at)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluated_at REAL NOT NULL,
            silent_zones_json TEXT NOT NULL,
            sos_hotspots_json TEXT NOT NULL,
            active_coverage_json TEXT NOT NULL,
            stats_json TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def insert_events(events: List[Dict[str, Any]], received_at: Optional[float] = None) -> int:
    if received_at is None:
        received_at = time.time()
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted = 0
    for ev in events:
        nid = int(ev.get("node_id", 0))
        seq = int(ev.get("seq", 0))
        mtype = str(ev.get("type", "HEARTBEAT"))
        ts = float(ev.get("timestamp", received_at))
        payload_str = json.dumps(ev)
        cursor.execute(
            """
            INSERT INTO events (node_id, seq, type, timestamp, received_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (nid, seq, mtype, ts, received_at, payload_str),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def get_recent_events(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT payload_json, received_at FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = json.loads(r["payload_json"])
        d["db_received_at"] = r["received_at"]
        result.append(d)
    return result


def clear_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events")
    cursor.execute("DELETE FROM evaluations")
    conn.commit()
    conn.close()
