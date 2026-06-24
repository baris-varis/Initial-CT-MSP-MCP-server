"""
Integration tests for MCP tools — mocked client + temp cache.
Covers all 7 acceptance criteria from the brief.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import server
from cache import TrialCache
from tests.conftest import make_raw_study, make_search_response


# ── Helpers ───────────────────────────────────────────────────────────────────

def _search_raw(has_turkey=True, nct="NCT05678901", status="RECRUITING") -> dict:
    raw_study = make_raw_study(nct_id=nct, status=status, has_turkey=has_turkey)
    return make_search_response([raw_study])


def _raw_single_study(nct="NCT05678901") -> dict:
    return make_raw_study(nct_id=nct)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Replace module-level cache with a temp one for each test."""
    cache = TrialCache(db_path=str(tmp_path / "test.db"), status_ttl_days=7, meta_ttl_days=30)
    monkeypatch.setattr(server, "_cache", cache)
    return cache


# ── AC1: search_trials returns ≥1 RECRUITING study with full schema ───────────

class TestSearchTrials:
    async def test_returns_normalized_recruiting_study(self):
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = _search_raw()
            result = await server._run_search(
                condition="non-small cell lung cancer",
                term="KRAS G12C",
                intervention=None,
                statuses=["RECRUITING"],
                phases=None,
                country=None,
                page_size=20,
                page_token=None,
                force_refresh=False,
            )

        assert not result.get("error")
        assert result["total_count"] >= 1
        study = result["studies"][0]
        assert study["nct_id"] == "NCT05678901"
        assert study["status"] == "RECRUITING"
        assert study["retrieved_at"]
        assert study["freshness"] == "live"
        # Full schema check
        for field in (
            "nct_id", "title", "status", "status_unknown_flag", "phases", "study_type",
            "conditions", "interventions", "eligibility", "locations",
            "has_turkey_site", "turkey_sites", "lead_sponsor",
            "last_update_post_date", "url", "retrieved_at",
        ):
            assert field in study, f"Missing field: {field}"

    async def test_empty_result_is_not_an_error(self):
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"studies": [], "totalCount": 0}
            result = await server._run_search(
                condition="very_rare_xyz_999",
                term=None,
                intervention=None,
                statuses=["RECRUITING"],
                phases=None,
                country=None,
                page_size=20,
                page_token=None,
                force_refresh=False,
            )
        assert not result.get("error")
        assert result["studies"] == []
        assert result["total_count"] == 0


# ── AC2: search_turkey_trials — has_turkey_site correct ──────────────────────

class TestSearchTurkeyTrials:
    async def test_turkey_results_have_has_turkey_site_true(self):
        turkey_study = make_raw_study(nct_id="NCT11223344", conditions=["Breast Cancer"], has_turkey=True)
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = make_search_response([turkey_study])
            result = await server.search_turkey_trials(condition="breast cancer")

        assert not result.get("error")
        for study in result["studies"]:
            assert study["has_turkey_site"] is True
            assert len(study["turkey_sites"]) > 0

    async def test_non_turkey_study_has_turkey_site_false(self):
        non_turkey = make_raw_study(nct_id="NCT99999999", has_turkey=False)
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = make_search_response([non_turkey])
            result = await server._run_search(
                condition="cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=False,
            )
        assert result["studies"][0]["has_turkey_site"] is False
        assert result["studies"][0]["turkey_sites"] == []


# ── AC3: get_trial returns full eligibility + locations ───────────────────────

class TestGetTrial:
    async def test_full_eligibility_and_locations(self):
        with patch.object(server._client, "get_study", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _raw_single_study("NCT05678901")
            result = await server.get_trial(nct_id="NCT05678901")

        assert not result.get("error")
        assert result["nct_id"] == "NCT05678901"
        elig = result["eligibility"]
        assert "KRAS G12C" in elig["criteria_text"]
        assert elig["min_age"] == "18 Years"
        assert len(result["locations"]) >= 1
        assert result["freshness"] == "live"


# ── AC4: dual_source_search — merged, deduped, Turkey-marked ─────────────────

class TestDualSourceSearch:
    async def test_world_and_turkey_merged_deduped(self):
        world_study = make_raw_study(nct_id="NCT00000001", has_turkey=False)
        turkey_study = make_raw_study(nct_id="NCT00000002", has_turkey=True)
        world_resp = make_search_response([world_study, turkey_study])
        turkey_resp = make_search_response([turkey_study])

        call_count = {"n": 0}

        async def fake_search(condition, **kwargs):
            call_count["n"] += 1
            if kwargs.get("country") == "Turkey":
                return turkey_resp
            return world_resp

        with patch.object(server._client, "search", side_effect=fake_search):
            result = await server.dual_source_search(condition="cancer")

        assert not result.get("error")
        nct_ids = [s["nct_id"] for s in result["studies"]]
        # No duplicates
        assert len(nct_ids) == len(set(nct_ids))
        # NCT00000002 appears once with Turkey flag
        tr_study = next(s for s in result["studies"] if s["nct_id"] == "NCT00000002")
        assert tr_study["has_turkey_site"] is True
        # NCT00000001 has no Turkey site
        wld_study = next(s for s in result["studies"] if s["nct_id"] == "NCT00000001")
        assert wld_study["has_turkey_site"] is False
        assert "turkey_count" in result
        assert call_count["n"] == 2  # both sources called


# ── AC5: Invalid status → BAD_PARAM; empty result → studies:[] ───────────────

class TestErrorCases:
    async def test_invalid_status_returns_bad_param(self):
        result = await server.search_trials(
            condition="cancer", statuses=["INVALID_XYZ"]
        )
        assert result["error"] is True
        assert result["type"] == "BAD_PARAM"
        assert "INVALID_XYZ" in result["message"]
        assert result["retry_suggested"] is False

    async def test_empty_results_not_error(self):
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"studies": [], "totalCount": 0}
            result = await server.search_trials(condition="very_rare_xyz_999")
        assert not result.get("error")
        assert result["studies"] == []
        assert result["total_count"] == 0


# ── AC6: Cache freshness ──────────────────────────────────────────────────────

class TestCacheFreshness:
    async def test_second_call_returns_cached(self):
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = _search_raw()
            result1 = await server._run_search(
                condition="lung cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=False,
            )
            result2 = await server._run_search(
                condition="lung cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=False,
            )

        assert result1["studies"][0]["freshness"] == "live"
        assert result2["studies"][0]["freshness"] == "cached"
        assert mock_search.call_count == 1  # only one real API call

    async def test_force_refresh_bypasses_cache(self):
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = _search_raw()
            await server._run_search(
                condition="lung cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=False,
            )
            result_fresh = await server._run_search(
                condition="lung cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=True,
            )

        assert result_fresh["studies"][0]["freshness"] == "live"
        assert mock_search.call_count == 2  # called twice despite cache


# ── AC7: Network error → NETWORK type, no crash ───────────────────────────────

class TestNetworkError:
    async def test_network_error_returns_structured_error(self):
        network_err = {
            "error": True,
            "type": "NETWORK",
            "message": "Network error after 3 attempts",
            "retry_suggested": True,
        }
        with patch.object(server._client, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = network_err
            result = await server._run_search(
                condition="cancer", term=None, intervention=None,
                statuses=["RECRUITING"], phases=None, country=None,
                page_size=20, page_token=None, force_refresh=False,
            )

        assert result["error"] is True
        assert result["type"] == "NETWORK"
        assert result["retry_suggested"] is True

    async def test_get_trial_network_error(self):
        with patch.object(server._client, "get_study", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "error": True, "type": "NETWORK",
                "message": "Connection refused", "retry_suggested": True,
            }
            result = await server.get_trial(nct_id="NCT99999999")

        assert result["error"] is True
        assert result["type"] == "NETWORK"
