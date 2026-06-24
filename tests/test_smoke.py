"""
Live smoke tests — require real CT.gov API access.
Skipped by default; run with: pytest -m smoke

These tests verify the full stack end-to-end against the live API.
They count as AC1, AC2, AC3 live verification.
"""
from __future__ import annotations

import pytest

import server


pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def test_live_search_nsclc_kras():
    """AC1 live: search for KRAS G12C NSCLC trials — expect ≥1 recruiting result."""
    result = await server._run_search(
        condition="non-small cell lung cancer",
        term="KRAS G12C",
        intervention=None,
        statuses=["RECRUITING"],
        phases=None,
        country=None,
        page_size=5,
        page_token=None,
        force_refresh=True,
    )

    assert not result.get("error"), f"API error: {result}"
    assert result["total_count"] >= 1, "Expected ≥1 recruiting NSCLC/KRAS G12C trial"

    study = result["studies"][0]
    assert study["nct_id"].startswith("NCT")
    assert study["status"]
    assert study["retrieved_at"]
    assert study["freshness"] == "live"
    print(f"\n  Found {result['total_count']} trials. First: {study['nct_id']} — {study['title']}")


async def test_live_search_turkey_breast_cancer():
    """AC2 live: Turkey breast cancer trials — has_turkey_site/turkey_sites correct."""
    result = await server.search_turkey_trials(
        condition="breast cancer",
        page_size=5,
        force_refresh=True,
    )

    assert not result.get("error"), f"API error: {result}"
    print(f"\n  Turkey breast cancer trials: {result['total_count']} total")

    for study in result["studies"]:
        assert study["has_turkey_site"] is True, f"{study['nct_id']} missing has_turkey_site"
        assert len(study["turkey_sites"]) > 0, f"{study['nct_id']} has empty turkey_sites"


async def test_live_get_trial():
    """AC3 live: get full trial details for a real NCT ID."""
    # First get a real NCT from search
    search = await server._run_search(
        condition="non-small cell lung cancer",
        term=None,
        intervention=None,
        statuses=["RECRUITING"],
        phases=None,
        country=None,
        page_size=1,
        page_token=None,
        force_refresh=True,
    )
    assert not search.get("error")
    assert len(search["studies"]) > 0

    nct_id = search["studies"][0]["nct_id"]
    result = await server.get_trial(nct_id=nct_id, force_refresh=True)

    assert not result.get("error"), f"get_trial error for {nct_id}: {result}"
    assert result["nct_id"] == nct_id
    assert result["eligibility"]["criteria_text"]  # eligibility text present
    assert len(result["locations"]) >= 1
    assert result["freshness"] == "live"
    print(f"\n  get_trial({nct_id}): {len(result['locations'])} locations, eligibility ok")


async def test_live_dual_source_search():
    """AC4 live: dual_source_search merges world + Turkey."""
    result = await server.dual_source_search(
        condition="breast cancer",
        page_size=5,
        force_refresh=True,
    )

    assert not result.get("error"), f"dual_source_search error: {result}"
    assert "turkey_count" in result
    assert "note" in result
    ncts = [s["nct_id"] for s in result["studies"]]
    assert len(ncts) == len(set(ncts)), "Duplicate NCT IDs found after merge"
    print(f"\n  dual_source_search: {result['total_count']} world, {result['turkey_count']} Turkey")
