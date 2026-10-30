"""SQLite persistence: HTTP response cache, registered farms, sentinel alerts and mandi price history.

One small file-backed database keeps the deployment to a single container. All access goes
through a lock because FastAPI handlers and the background sentinel share the connection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    url         TEXT NOT NULL,
    fetched_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS farms (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    lat              REAL NOT NULL,
    lon              REAL NOT NULL,
    crop             TEXT NOT NULL,
    sowing_date      TEXT,
    irrigation       TEXT NOT NULL DEFAULT 'flood',
    language         TEXT NOT NULL DEFAULT 'en',
    telegram_chat_id TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id     INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    facts       TEXT NOT NULL,
    valid_for   TEXT NOT NULL,
    delivered   TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (farm_id, kind, valid_for)
);

CREATE TABLE IF NOT EXISTS mandi_prices (
    arrival_date TEXT NOT NULL,
    state        TEXT NOT NULL,
    district     TEXT NOT NULL,
    market       TEXT NOT NULL,
    commodity    TEXT NOT NULL,
    variety      TEXT NOT NULL,
    grade        TEXT NOT NULL,
    min_price    REAL,
    max_price    REAL,
    modal_price  REAL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (arrival_date, market, commodity, variety, grade)
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ http cache
    def cache_get(self, key: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT payload, url, fetched_at, expires_at FROM http_cache WHERE key = ?", (key,)
            ).fetchone()

    def cache_put(self, key: str, payload: str, url: str, ttl_s: float) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache (key, payload, url, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (key, payload, url, now, now + ttl_s),
            )
            self._conn.commit()

    def cache_prune(self, max_stale_s: float) -> int:
        cutoff = time.time() - max_stale_s
        with self._lock:
            cur = self._conn.execute("DELETE FROM http_cache WHERE expires_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ farms
    def add_farm(self, farm: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO farms (name, lat, lon, crop, sowing_date, irrigation, language, telegram_chat_id, created_at)
                   VALUES (:name, :lat, :lon, :crop, :sowing_date, :irrigation, :language, :telegram_chat_id, :created_at)""",
                {**farm, "created_at": utcnow_iso()},
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM farms WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def list_farms(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM farms ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_farm(self, farm_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM farms WHERE id = ?", (farm_id,)).fetchone()
        return dict(row) if row else None

    def delete_farm(self, farm_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM farms WHERE id = ?", (farm_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ alerts
    def insert_alert(self, alert: dict[str, Any]) -> dict[str, Any] | None:
        """Store an alert unless the same (farm, kind, valid_for) was already raised. Returns the new row."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO alerts (farm_id, kind, severity, title, message, facts, valid_for, created_at)
                   VALUES (:farm_id, :kind, :severity, :title, :message, :facts, :valid_for, :created_at)""",
                {**alert, "facts": json.dumps(alert["facts"], ensure_ascii=False), "created_at": utcnow_iso()},
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            row = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _alert_row(row)

    def mark_delivered(self, alert_id: int, channel: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE alerts SET delivered = ? WHERE id = ?", (channel, alert_id))
            self._conn.commit()

    def list_alerts(self, farm_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM alerts"
        args: tuple[Any, ...] = ()
        if farm_id is not None:
            query += " WHERE farm_id = ?"
            args = (farm_id,)
        query += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(query, (*args, limit)).fetchall()
        return [_alert_row(r) for r in rows]

    # ------------------------------------------------------------------ mandi history
    def upsert_mandi_prices(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        now = utcnow_iso()
        with self._lock:
            self._conn.executemany(
                """INSERT OR REPLACE INTO mandi_prices
                   (arrival_date, state, district, market, commodity, variety, grade, min_price, max_price, modal_price, fetched_at)
                   VALUES (:arrival_date, :state, :district, :market, :commodity, :variety, :grade,
                           :min_price, :max_price, :modal_price, :fetched_at)""",
                [{**r, "fetched_at": now} for r in records],
            )
            self._conn.commit()

    def mandi_history(self, commodity: str, market: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT arrival_date, AVG(modal_price) AS modal_price FROM mandi_prices
                   WHERE lower(commodity) = lower(?) AND lower(market) = lower(?)
                   GROUP BY arrival_date ORDER BY arrival_date DESC LIMIT ?""",
                (commodity, market, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def _alert_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["facts"] = json.loads(out["facts"])
    return out
