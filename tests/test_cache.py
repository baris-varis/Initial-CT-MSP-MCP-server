"""Tests for TrialCache — SQLite in tmp_path, no network."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cache import TrialCache


@pytest.fixture
def cache(tmp_path):
    return TrialCache(db_path=str(tmp_path / "test.db"), status_ttl_days=7, meta_ttl_days=30)


@pytest.fixture
def sample_payload() -> dict:
    return {
        "studies": [{"nct_id": "NCT12345", "title": "Test Study", "status": "RECRUITING"}],
        "total_count": 1,
        "next_page_token": None,
    }


class TestCacheMissAndHit:
    def test_miss_returns_none_none(self, cache):
        result, cached_at = cache.get("nonexistent_key")
        assert result is None
        assert cached_at is None

    def test_set_then_get_returns_payload(self, cache, sample_payload):
        key = cache.search_key("cancer", None, None, ["RECRUITING"], None, None, 20, None)
        cache.set(key, sample_payload)
        result, cached_at = cache.get(key)
        assert result is not None
        assert result["total_count"] == 1
        assert result["studies"][0]["nct_id"] == "NCT12345"

    def test_set_returns_iso_timestamp(self, cache, sample_payload):
        key = "test_key"
        ts = cache.set(key, sample_payload)
        assert "T" in ts  # ISO 8601

    def test_get_returns_cached_at_timestamp(self, cache, sample_payload):
        key = "test_key"
        set_ts = cache.set(key, sample_payload)
        _, get_ts = cache.get(key)
        assert get_ts == set_ts

    def test_overwrite_updates_payload(self, cache, sample_payload):
        key = "test_key"
        cache.set(key, sample_payload)
        updated = {**sample_payload, "total_count": 99}
        cache.set(key, updated)
        result, _ = cache.get(key)
        assert result["total_count"] == 99


class TestTTLExpiry:
    def test_expired_entry_returns_none_with_cached_at(self, tmp_path, sample_payload):
        # TTL=0 means everything is immediately expired
        cache = TrialCache(db_path=str(tmp_path / "ttl.db"), status_ttl_days=0)
        key = "exp_key"
        cache.set(key, sample_payload)
        result, cached_at = cache.get(key)
        assert result is None         # expired
        assert cached_at is not None  # but key existed

    def test_fresh_entry_returned(self, tmp_path, sample_payload):
        cache = TrialCache(db_path=str(tmp_path / "fresh.db"), status_ttl_days=7)
        key = "fresh_key"
        cache.set(key, sample_payload)
        result, _ = cache.get(key)
        assert result is not None  # still fresh


class TestKeyGeneration:
    def test_same_params_same_key(self, cache):
        k1 = cache.search_key("cancer", "KRAS", None, ["RECRUITING"], None, "Turkey", 20, None)
        k2 = cache.search_key("cancer", "KRAS", None, ["RECRUITING"], None, "Turkey", 20, None)
        assert k1 == k2

    def test_different_conditions_different_keys(self, cache):
        k1 = cache.search_key("lung cancer", None, None, ["RECRUITING"], None, None, 20, None)
        k2 = cache.search_key("breast cancer", None, None, ["RECRUITING"], None, None, 20, None)
        assert k1 != k2

    def test_different_country_different_key(self, cache):
        k1 = cache.search_key("cancer", None, None, ["RECRUITING"], None, None, 20, None)
        k2 = cache.search_key("cancer", None, None, ["RECRUITING"], None, "Turkey", 20, None)
        assert k1 != k2

    def test_trial_key_case_insensitive(self, cache):
        k1 = cache.trial_key("NCT12345")
        k2 = cache.trial_key("nct12345")
        assert k1 == k2

    def test_trial_key_differs_from_search_key(self, cache):
        tk = cache.trial_key("NCT12345")
        sk = cache.search_key("cancer", None, None, ["RECRUITING"], None, None, 20, None)
        assert tk != sk

    def test_status_order_independent(self, cache):
        k1 = cache.search_key("cancer", None, None, ["RECRUITING", "NOT_YET_RECRUITING"], None, None, 20, None)
        k2 = cache.search_key("cancer", None, None, ["NOT_YET_RECRUITING", "RECRUITING"], None, None, 20, None)
        assert k1 == k2


class TestInvalidate:
    def test_invalidate_removes_entry(self, cache, sample_payload):
        key = "to_delete"
        cache.set(key, sample_payload)
        cache.invalidate(key)
        result, cached_at = cache.get(key)
        assert result is None
        assert cached_at is None  # completely gone
