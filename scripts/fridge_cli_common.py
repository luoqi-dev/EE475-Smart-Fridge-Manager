from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_detection_layer.collision_resolver import ensure_collision_schema

DB_PATH = REPO_ROOT / "data" / "db" / "fridge.db"


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_collision_schema(conn)
    return conn


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_seconds(iso_utc: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    shifted = dt + timedelta(seconds=seconds)
    return shifted.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (table_name,),
    ).fetchone()
    return row is not None
