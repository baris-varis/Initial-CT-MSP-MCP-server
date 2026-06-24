"""
ClinicalTrials.gov MCP Server — OncoHub Tumor Council Integration
Transport: Streamable HTTP (claude.ai custom connector compatible)
Endpoint:  /mcp  (default FastMCP path)

Tools exposed:
  search_trials         — parameterized worldwide search
  get_trial             — single study by NCT ID (full eligibility + locations)
  search_turkey_trials  — convenience wrapper: country="Turkey"
  dual_source_search    — primary council entry: world + Turkey merged, NCT-deduped

This server DOES NOT perform clinical eligibility assessment or scoring.
It provides raw normalized data; interpretation is the consuming system's job.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from cache import TrialCache
from ctgov_client import ClinicalTrialsClient
from normalize import normalize_search_response, normalize_study

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PORT = int(os.getenv("PORT", "8000"))
CACHE_PATH = os.getenv("CACHE_PATH", ".cache/ctgov.db")
STATUS_TTL_DAYS = int(os.getenv("STATUS_TTL_DAYS", "7"))
META_TTL_DAYS = int(os.getenv("META_TTL_DAYS", "30"))
BASE_URL = os.getenv("CTGOV_BASE_URL", "https://clinicaltrials.gov/api/v2/studies")
RATE_INTERVAL = float(os.getenv("CTGOV_RATE_INTERVAL", "1.0"))
TIMEOUT = float(os.getenv("CTGOV_TIMEOUT", "30.0"))

# ── Singletons ────────────────────────────────────────────────────────────────

_client = ClinicalTrialsClient(
    base_url=BASE_URL,
    timeout=TIMEOUT,
    rate_interval=RATE_INTERVAL,
)
_cache = TrialCache(
    db_path=CACHE_PATH,
    status_ttl_days=STATUS_TTL_DAYS,
    meta_ttl_days=META_TTL_DAYS,
)

# ── FastMCP server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    "ctgov-oncohub",
    instructions=(
        "ClinicalTrials.gov v2 API server for OncoHub Tumor Council. "
        "Use dual_source_search as the primary entry point for council sessions. "
        "All results include freshness metadata — always surface retrieved_at to clinicians."
    ),
    # Public read-only server — DNS rebinding protection not applicable
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# ── Internal helpers ──────────────────────────────────────────────────────────


def _stamp_freshness(studies: list[dict], freshness: str, cached_at: Optional[str]) -> list[dict]:
    for s in studies:
        s["freshness"] = freshness
        if cached_at:
            s["cached_at"] = cached_at
    return studies


async def _run_search(
    condition: str,
    term: Optional[str],
    intervention: Optional[str],
    statuses: list[str],
    phases: Optional[list[str]],
    country: Optional[str],
    page_size: int,
    page_token: Optional[str],
    force_refresh: bool,
) -> dict:
    cache_key = _cache.search_key(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=statuses,
        phases=phases,
        country=country,
        page_size=page_size,
        page_token=page_token,
    )

    if not force_refresh:
        cached, cached_at = _cache.get(cache_key)
        if cached is not None:
            logger.info("Cache hit: %s (country=%r)", cache_key[:8], country)
            cached["studies"] = _stamp_freshness(cached["studies"], "cached", cached_at)
            return cached

    raw = await _client.search(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=statuses,
        phases=phases,
        country=country,
        page_size=page_size,
        page_token=page_token,
    )

    if raw.get("error"):
        return raw

    normalized = normalize_search_response(raw)
    _cache.set(cache_key, normalized)
    normalized["studies"] = _stamp_freshness(normalized["studies"], "live", None)
    return normalized


# ── MCP Tools ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def search_trials(
    condition: str,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    phases: Optional[list[str]] = None,
    country: Optional[str] = None,
    page_size: int = 20,
    page_token: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """
    Search ClinicalTrials.gov v2 for clinical trials.

    Args:
        condition: Disease/condition (required). E.g. "non-small cell lung cancer"
        term: Free-text biomarker or keyword. E.g. "KRAS G12C"
        intervention: Drug or target. E.g. "sotorasib"
        statuses: Recruiting statuses to filter (default: RECRUITING, NOT_YET_RECRUITING).
                  Valid: RECRUITING, NOT_YET_RECRUITING, ACTIVE_NOT_RECRUITING, COMPLETED,
                  TERMINATED, WITHDRAWN, SUSPENDED, ENROLLING_BY_INVITATION, UNKNOWN
        phases: Phase filter list. Valid: PHASE1, PHASE2, PHASE3, PHASE4, EARLY_PHASE1, NA
        country: Country name for location filter. E.g. "Turkey"
        page_size: Results per page (max 50 recommended)
        page_token: Cursor for next page (from previous response)
        force_refresh: Skip cache and fetch live data
    Returns:
        {studies: [...], total_count: int, next_page_token: str|null}
        Each study includes freshness: "live"|"cached" and retrieved_at (ISO UTC).
    """
    _statuses = statuses if statuses is not None else ["RECRUITING", "NOT_YET_RECRUITING"]
    return await _run_search(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=_statuses,
        phases=phases,
        country=country,
        page_size=min(page_size, 50),
        page_token=page_token,
        force_refresh=force_refresh,
    )


@mcp.tool()
async def get_trial(nct_id: str, force_refresh: bool = False) -> dict:
    """
    Fetch full details for a single trial by NCT ID.

    Returns complete eligibility criteria text and all site locations.
    Args:
        nct_id: NCT number. E.g. "NCT05678901"
        force_refresh: Skip cache and fetch live data
    Returns:
        Single normalized study record with full eligibility + locations.
    """
    nct_upper = nct_id.strip().upper()
    cache_key = _cache.trial_key(nct_upper)

    if not force_refresh:
        cached, cached_at = _cache.get(cache_key)
        if cached is not None:
            cached["freshness"] = "cached"
            cached["cached_at"] = cached_at
            return cached

    raw = await _client.get_study(nct_upper)
    if raw.get("error"):
        return raw

    normalized = normalize_study(raw)
    _cache.set(cache_key, normalized)
    normalized["freshness"] = "live"
    return normalized


@mcp.tool()
async def search_turkey_trials(
    condition: str,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    phases: Optional[list[str]] = None,
    page_size: int = 20,
    page_token: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """
    Search for clinical trials with sites in Turkey.

    Convenience wrapper around search_trials with country="Turkey".
    Each result includes has_turkey_site: true and populated turkey_sites list.

    Args:
        condition: Disease/condition (required)
        term: Free-text biomarker or keyword
        intervention: Drug or target name
        statuses: Recruiting statuses (default: RECRUITING, NOT_YET_RECRUITING)
        phases: Phase filter list
        page_size: Results per page (max 50)
        page_token: Pagination cursor
        force_refresh: Skip cache
    """
    _statuses = statuses if statuses is not None else ["RECRUITING", "NOT_YET_RECRUITING"]
    return await _run_search(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=_statuses,
        phases=phases,
        country="Turkey",
        page_size=min(page_size, 50),
        page_token=page_token,
        force_refresh=force_refresh,
    )


@mcp.tool()
async def dual_source_search(
    condition: str,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    phases: Optional[list[str]] = None,
    page_size: int = 20,
    force_refresh: bool = False,
) -> dict:
    """
    PRIMARY COUNCIL ENTRY POINT: search worldwide AND Turkey, merge results.

    Makes two sequential CT.gov queries (world + Turkey), deduplicates by NCT ID,
    and marks has_turkey_site on all results. Use this for OncoHub A2.3 integration.

    Args:
        condition: Disease/condition. Maps from A1 tumor field
        term: Biomarker/keyword. Maps from A1 biomarker field
        intervention: Drug/target. Maps from A1 treatment field
        statuses: Recruiting statuses (default: RECRUITING, NOT_YET_RECRUITING)
        phases: Phase filter. Maps from A1 treatment line/phase
        page_size: Results per page for each sub-query (max 50)
        force_refresh: Skip cache for both sub-queries
    Returns:
        {studies: [...merged+deduped...], total_count: int, turkey_count: int,
         next_page_token: str|null, note: str}
        Studies sourced from Turkey only appear once, with has_turkey_site: true.
    """
    _statuses = statuses if statuses is not None else ["RECRUITING", "NOT_YET_RECRUITING"]
    capped_size = min(page_size, 50)

    # Sequential to respect rate limiting
    world = await _run_search(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=_statuses,
        phases=phases,
        country=None,
        page_size=capped_size,
        page_token=None,
        force_refresh=force_refresh,
    )
    if world.get("error"):
        return world

    turkey = await _run_search(
        condition=condition,
        term=term,
        intervention=intervention,
        statuses=_statuses,
        phases=phases,
        country="Turkey",
        page_size=capped_size,
        page_token=None,
        force_refresh=force_refresh,
    )

    turkey_search_error: Optional[str] = None
    if turkey.get("error"):
        turkey_search_error = turkey.get("message", "Turkey search failed")
        turkey_studies: list[dict] = []
        turkey_total = 0
    else:
        turkey_studies = turkey.get("studies", [])
        turkey_total = turkey.get("total_count", 0)

    # Build lookup: NCT → turkey study (for enriching world results)
    turkey_by_nct = {s["nct_id"]: s for s in turkey_studies}
    turkey_ncts = set(turkey_by_nct)

    # Merge: world results enriched with Turkey site data
    merged: list[dict] = []
    seen_ncts: set[str] = set()

    for study in world.get("studies", []):
        nct = study["nct_id"]
        seen_ncts.add(nct)
        if nct in turkey_ncts:
            tr = turkey_by_nct[nct]
            study["has_turkey_site"] = True
            study["turkey_sites"] = tr.get("turkey_sites", [])
        merged.append(study)

    # Append Turkey-only studies not present in world results
    for study in turkey_studies:
        if study["nct_id"] not in seen_ncts:
            study["has_turkey_site"] = True
            merged.append(study)

    result: dict = {
        "studies": merged,
        "total_count": world.get("total_count", 0),
        "turkey_count": turkey_total,
        "next_page_token": world.get("next_page_token"),
        "note": (
            "Results merged from worldwide and Turkey-specific searches. "
            "Deduped by NCT ID. has_turkey_site=true where Turkish sites confirmed."
        ),
    }
    if turkey_search_error:
        result["turkey_search_error"] = turkey_search_error

    return result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting ClinicalTrials.gov MCP server on port %d", PORT)
    logger.info("Cache: %s (STATUS_TTL=%dd, META_TTL=%dd)", CACHE_PATH, STATUS_TTL_DAYS, META_TTL_DAYS)
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
