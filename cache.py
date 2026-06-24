"""
SQLite-backed TTL cache for CT.gov study data.

Two TTL tiers (from .env):
  STATUS_TTL_DAYS (default 7)  — applies to all cached entries (status is the most volatile field)
  META_TTL_DAYS   (default 30) — reserved for future eligibility-only caching

Cache backend is a single SQLite file — no external services required.
In stateless cloud deployments, set CACHE_PATH to ephemeral storage (/tmp) or
mount a persistent disk and point CACHE_PATH there (see README).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


class TrialCache:
    def __init__(
        self,
        db_path: str = ".cache/ctgov.db",
        status_ttl_days: int = 7,
        meta_ttl_days: int = 30,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._status_ttl = timedelta(days=status_ttl_days)
        self._meta_ttl = timedelta(days=meta_ttl_days)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    key      TEXT PRIMARY KEY,
                    payload  TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                )
                """
            )

    # ── Key generation ──────────────────────────────────────────────────────

    @staticmethod
    def _make_key(**kwargs: object) -> str:
        sig = "|".join(
            f"{k}={v}" for k, v in sorted(kwargs.items()) if v is not None
        )
        return hashlib.sha256(sig.encode()).hexdigest()

    def search_key(
        self,
        condition: str,
        term: Optional[str],
        intervention: Optional[str],
        statuses: list[str],
        phases: Optional[list[str]],
        country: Optional[str],
        page_size: int,
        page_token: Optional[str],
    ) -> str:
        return self._make_key(
            condition=condition.lower().strip(),
            term=term.lower().strip() if term else None,
            intervention=intervention.lower().strip() if intervention else None,
            statuses=",".join(sorted(statuses)),
            phases=",".join(sorted(phases)) if phases else None,
            country=country.lower().strip() if country else None,
            page_size=page_size,
            page_token=page_token,
        )

    def trial_key(self, nct_id: str) -> str:
        return self._make_key(nct_id=nct_id.upper().strip())

    # ── Read / Write ─────────────────────────────────────────────────────────

    def get(self, key: str) -> tuple[Optional[dict], Optional[str]]:
        """
        Returns (payload, cached_at_iso) if fresh, or (None, cached_at_iso) if
        expired/missing.  Second element is None only when the key doesn't exist.
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT payload, cached_at FROM entries WHERE key = ?", (key,)
            ).fetchone()

        if row is None:
            return None, None

        cached_at_str: str = row[1]
        cached_at = datetime.fromisoformat(cached_at_str)
        age = datetime.now(timezone.utc) - cached_at

        if age <= self._status_ttl:
            return json.loads(row[0]), cached_at_str

        return None, cached_at_str  # expired but key existed

    def set(self, key: str, payload: dict) -> str:
        """Persist payload and return the cached_at ISO timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entries (key, payload, cached_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), now),
            )
        return now

    def invalidate(self, key: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM entries WHERE key = ?", (key,))
