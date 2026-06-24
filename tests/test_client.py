"""Tests for ClinicalTrialsClient — HTTP calls mocked with respx."""
from __future__ import annotations

import httpx
import pytest
import respx

from ctgov_client import ClinicalTrialsClient
from tests.conftest import make_search_response, make_raw_study

BASE = "https://clinicaltrials.gov/api/v2/studies"


@pytest.fixture
def client():
    return ClinicalTrialsClient(rate_interval=0.0)


class TestValidation:
    async def test_invalid_status_returns_bad_param(self, client):
        result = await client.search(condition="cancer", statuses=["INVALID_STATUS"])
        assert result["error"] is True
        assert result["type"] == "BAD_PARAM"
        assert "INVALID_STATUS" in result["message"]
        assert result["retry_suggested"] is False

    async def test_invalid_phase_returns_bad_param(self, client):
        result = await client.search(condition="cancer", phases=["PHASE99"])
        assert result["error"] is True
        assert result["type"] == "BAD_PARAM"
        assert "PHASE99" in result["message"]

    async def test_valid_statuses_pass_validation(self, client):
        with respx.mock:
            respx.get(BASE).mock(return_value=httpx.Response(200, json=make_search_response()))
            result = await client.search(
                condition="cancer",
                statuses=["RECRUITING", "NOT_YET_RECRUITING", "COMPLETED"],
            )
        assert not result.get("error")


class TestHTTPErrors:
    async def test_rate_limit_returns_structured_error(self, client):
        with respx.mock:
            respx.get(BASE).mock(return_value=httpx.Response(429))
            result = await client.search(condition="cancer")
        assert result["error"] is True
        assert result["type"] == "RATE_LIMIT"
        assert result["retry_suggested"] is True

    async def test_server_error_retries_and_fails(self, client):
        with respx.mock:
            respx.get(BASE).mock(return_value=httpx.Response(500))
            result = await client.search(condition="cancer")
        assert result["error"] is True
        assert result["type"] == "API_ERROR"
        assert result["retry_suggested"] is True

    async def test_404_returns_not_found(self, client):
        nct = "NCT99999999"
        with respx.mock:
            respx.get(f"{BASE}/{nct}").mock(return_value=httpx.Response(404))
            result = await client.get_study(nct)
        assert result["error"] is True
        assert result["type"] == "NOT_FOUND"

    async def test_network_error_returns_network_type(self, client):
        with respx.mock:
            respx.get(BASE).mock(side_effect=httpx.NetworkError("connection refused"))
            result = await client.search(condition="cancer")
        assert result["error"] is True
        assert result["type"] == "NETWORK"
        assert result["retry_suggested"] is True


class TestSuccessfulSearch:
    async def test_returns_studies_array(self, client):
        with respx.mock:
            respx.get(BASE).mock(return_value=httpx.Response(200, json=make_search_response()))
            result = await client.search(condition="non-small cell lung cancer")
        assert "studies" in result
        assert result["totalCount"] == 1

    async def test_country_passed_as_locn(self, client):
        captured = {}

        def handler(request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=make_search_response())

        with respx.mock:
            respx.get(BASE).mock(side_effect=handler)
            await client.search(condition="cancer", country="Turkey")

        assert captured["params"].get("query.locn") == "Turkey"

    async def test_phases_passed_as_advanced_filter(self, client):
        captured = {}

        def handler(request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=make_search_response())

        with respx.mock:
            respx.get(BASE).mock(side_effect=handler)
            await client.search(condition="cancer", phases=["PHASE2", "PHASE3"])

        assert "filter.advanced" in captured["params"]
        assert "PHASE2" in captured["params"]["filter.advanced"]
        assert "PHASE3" in captured["params"]["filter.advanced"]

    async def test_get_study_calls_correct_url(self, client):
        nct = "NCT05678901"
        raw = make_raw_study()

        with respx.mock:
            respx.get(f"{BASE}/{nct}").mock(return_value=httpx.Response(200, json=raw))
            result = await client.get_study(nct)

        assert result["protocolSection"]["identificationModule"]["nctId"] == nct

    async def test_empty_studies_not_an_error(self, client):
        with respx.mock:
            respx.get(BASE).mock(
                return_value=httpx.Response(200, json={"studies": [], "totalCount": 0})
            )
            result = await client.search(condition="very_rare_cancer_xyz")
        assert not result.get("error")
        assert result.get("studies") == []
        assert result.get("totalCount") == 0
