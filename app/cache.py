"""Small SQLite key/value cache with expiry.

Keepa bills per lookup and the catalog APIs rate-limit, so anything fetched is
kept for a while: catalog data for weeks, prices for hours.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

DAY = 86400


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, exp REAL NOT NULL)"
        )
        self._db.commit()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            row = self._db.execute("SELECT v, exp FROM kv WHERE k = ?", (key,)).fetchone()
        if not row:
            return None
        v, exp = row
        if exp < time.time():
            return None
        return json.loads(v)

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO kv (k, v, exp) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time() + ttl),
            )
            self._db.commit()

    def purge(self) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM kv WHERE exp < ?", (time.time(),))
            self._db.commit()
            return cur.rowcount
