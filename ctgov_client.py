"""
Async HTTP client for the ClinicalTrials.gov v2 REST API.

Responsibilities:
  - Parameter validation and query string mapping
  - Exponential-backoff retry (network/5xx errors, max 3 attempts)
  - Rate limiting (≥1 s between requests, CT.gov ToU)
  - Structured error dicts — never raises for caller

Does NOT normalize response data — that is normalize.py's job.
Does NOT contact-harvest or store PII fields.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any, Optional

import httpx

from models import VALID_PHASES, VALID_STATUSES

logger = logging.getLogger(__name__)


def _err(etype: str, message: str, retry: bool = False) -> dict:
    return {"error": True, "type": etype, "message": message, "retry_suggested": retry}


def _build_ssl_context() -> ssl.SSLContext:
    """
    CT.gov's WAF (Cloudflare) fingerprints TLS client hellos and blocks some
    Python builds whose default cipher ordering it doesn't recognise.
    Setting ciphers to 'DEFAULT' produces a fingerprint that passes the check.
    """
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT")
    return ctx


class ClinicalTrialsClient:
    def __init__(
        self,
        base_url: str = "https://clinicaltrials.gov/api/v2/studies",
        timeout: float = 30.0,
        rate_interval: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url
        self._max_retries = max_retries
        self._rate_interval = rate_interval
        self._last_call: float = 0.0
        self._rate_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Accept": "application/json",
                "User-Agent": "OncoHub-MCP-Server/1.0 (clinical research; not-for-profit)",
            },
            verify=_build_ssl_context(),
            follow_redirects=True,
        )

    # ── Rate limiting ────────────────────────────────────────────────────────

    async def _rate_wait(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._rate_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    # ── Core HTTP ────────────────────────────────────────────────────────────

    async def _fetch(self, url: str, params: dict[str, Any]) -> dict:
        await self._rate_wait()

        for attempt in range(self._max_retries):
            try:
                resp = await self._http.get(url, params=params)
            except httpx.TimeoutException as exc:
                logger.warning("CT.gov timeout (attempt %d): %s", attempt + 1, exc)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                return _err("NETWORK", f"Request timed out after {self._max_retries} attempts.", retry=True)
            except httpx.NetworkError as exc:
                logger.warning("CT.gov network error (attempt %d): %s", attempt + 1, exc)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                return _err("NETWORK", f"Network error after {self._max_retries} attempts: {exc}", retry=True)

            if resp.status_code == 429:
                return _err("RATE_LIMIT", "CT.gov API rate limit exceeded. Retry later.", retry=True)

            if resp.status_code == 404:
                return _err("NOT_FOUND", f"Resource not found: {url}", retry=False)

            if resp.status_code >= 500:
                logger.warning("CT.gov server error %d (attempt %d)", resp.status_code, attempt + 1)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                return _err("API_ERROR", f"CT.gov server error HTTP {resp.status_code}.", retry=True)

            if resp.status_code >= 400:
                return _err("API_ERROR", f"CT.gov client error HTTP {resp.status_code}.", retry=False)

            try:
                return resp.json()
            except Exception as exc:
                return _err("API_ERROR", f"Invalid JSON from CT.gov: {exc}", retry=False)

        return _err("NETWORK", "Exhausted all retry attempts.", retry=True)

    # ── Validation ───────────────────────────────────────────────────────────

    def _validate_statuses(self, statuses: list[str]) -> Optional[dict]:
        invalid = [s for s in statuses if s not in VALID_STATUSES]
        if invalid:
            return _err(
                "BAD_PARAM",
                f"Invalid status values: {invalid}. Valid values: {sorted(VALID_STATUSES)}",
                retry=False,
            )
        return None

    def _validate_phases(self, phases: list[str]) -> Optional[dict]:
        invalid = [p for p in phases if p not in VALID_PHASES]
        if invalid:
            return _err(
                "BAD_PARAM",
                f"Invalid phase values: {invalid}. Valid values: {sorted(VALID_PHASES)}",
                retry=False,
            )
        return None

    # ── Public API ───────────────────────────────────────────────────────────

    async def search(
        self,
        condition: str,
        term: Optional[str] = None,
        intervention: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        phases: Optional[list[str]] = None,
        country: Optional[str] = None,
        page_size: int = 20,
        page_token: Optional[str] = None,
    ) -> dict:
        if statuses is None:
            statuses = ["RECRUITING", "NOT_YET_RECRUITING"]

        if err := self._validate_statuses(statuses):
            return err
        if phases and (err := self._validate_phases(phases)):
            return err

        params: dict[str, Any] = {
            "query.cond": condition,
            "filter.overallStatus": ",".join(statuses),
            "pageSize": min(page_size, 1000),
            "format": "json",
            "countTotal": "true",
        }
        if term:
            params["query.term"] = term
        if intervention:
            params["query.intr"] = intervention
        if country:
            params["query.locn"] = country
        if page_token:
            params["pageToken"] = page_token
        if phases:
            params["filter.advanced"] = "AREA[Phase](" + " OR ".join(phases) + ")"

        logger.info("CT.gov search: cond=%r country=%r phases=%s", condition, country, phases)
        return await self._fetch(self._base_url, params)

    async def get_study(self, nct_id: str) -> dict:
        url = f"{self._base_url}/{nct_id.strip().upper()}"
        logger.info("CT.gov get_study: %s", nct_id)
        return await self._fetch(url, {"format": "json"})

    async def close(self) -> None:
        await self._http.aclose()
