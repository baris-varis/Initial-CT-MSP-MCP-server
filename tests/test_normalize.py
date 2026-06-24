"""Unit tests for normalize.py — no I/O, no mocking required."""
from __future__ import annotations

import pytest
from normalize import normalize_study, normalize_search_response
from tests.conftest import make_raw_study, make_search_response


class TestNormalizeStudy:
    def test_basic_fields(self, raw_study):
        result = normalize_study(raw_study)
        assert result["nct_id"] == "NCT05678901"
        assert result["title"] == "A Study of Sotorasib in KRAS G12C NSCLC"
        assert result["status"] == "RECRUITING"
        assert result["status_unknown_flag"] is False
        assert result["phases"] == ["PHASE2"]
        assert result["study_type"] == "INTERVENTIONAL"
        assert result["conditions"] == ["Non-Small Cell Lung Cancer"]
        assert result["lead_sponsor"] == "Amgen"
        assert result["last_update_post_date"] == "2024-05-01"
        assert result["url"] == "https://clinicaltrials.gov/study/NCT05678901"

    def test_retrieved_at_populated(self, raw_study):
        result = normalize_study(raw_study)
        assert result["retrieved_at"]
        assert "T" in result["retrieved_at"]  # ISO 8601

    def test_retrieved_at_custom(self, raw_study):
        ts = "2024-06-01T00:00:00+00:00"
        result = normalize_study(raw_study, retrieved_at=ts)
        assert result["retrieved_at"] == ts

    def test_has_turkey_site_true(self, raw_study):
        result = normalize_study(raw_study)
        assert result["has_turkey_site"] is True
        assert len(result["turkey_sites"]) == 1
        site = result["turkey_sites"][0]
        assert site["city"] == "Istanbul"
        assert site["facility"] == "Istanbul University Hospital"

    def test_has_turkey_site_false(self, raw_study_no_turkey):
        result = normalize_study(raw_study_no_turkey)
        assert result["has_turkey_site"] is False
        assert result["turkey_sites"] == []

    def test_interventions(self, raw_study):
        result = normalize_study(raw_study)
        assert len(result["interventions"]) == 1
        assert result["interventions"][0]["type"] == "DRUG"
        assert result["interventions"][0]["name"] == "Sotorasib"

    def test_eligibility(self, raw_study):
        elig = normalize_study(raw_study)["eligibility"]
        assert "KRAS G12C" in elig["criteria_text"]
        assert elig["sex"] == "ALL"
        assert elig["min_age"] == "18 Years"
        assert elig["max_age"] == "N/A"
        assert elig["healthy_volunteers"] is False

    def test_locations_all_present(self, raw_study):
        locs = normalize_study(raw_study)["locations"]
        countries = {loc["country"] for loc in locs}
        assert "United States" in countries
        assert "Turkey" in countries

    def test_status_unknown_flag(self):
        raw = make_raw_study(status="UNKNOWN")
        result = normalize_study(raw)
        assert result["status_unknown_flag"] is True
        assert result["status"] == "UNKNOWN"

    def test_missing_fields_return_empty_not_null(self):
        result = normalize_study({})
        assert result["phases"] == []
        assert result["conditions"] == []
        assert result["interventions"] == []
        assert result["locations"] == []
        assert result["turkey_sites"] == []
        assert result["has_turkey_site"] is False
        assert result["nct_id"] == ""
        assert result["title"] == ""

    def test_no_pii_fields(self, raw_study):
        """Contact/PII fields must not appear in output."""
        result = normalize_study(raw_study)
        forbidden = {"email", "phone", "contact", "contactName", "contactEMail"}
        flat_keys = set(str(result).lower().split())
        # Quick sanity: eligibility text might contain "contact" in natural language — check keys only
        assert not (forbidden & set(result.keys()))

    def test_turkey_spelling_variants(self):
        """Both 'Turkey' and 'Türkiye' should be recognised as Turkish sites."""
        for country_name in ("Turkey", "Türkiye", "türkiye", "TURKEY", "Turkey (Türkiye)"):
            raw = make_raw_study(has_turkey=False)
            raw["protocolSection"]["contactsLocationsModule"]["locations"] = [
                {"country": country_name, "city": "Ankara", "facility": "Test", "status": "RECRUITING"}
            ]
            result = normalize_study(raw)
            assert result["has_turkey_site"] is True, f"Failed for country={country_name!r}"


class TestNormalizeSearchResponse:
    def test_total_count(self, raw_search_response):
        result = normalize_search_response(raw_search_response)
        assert result["total_count"] == 1

    def test_studies_list(self, raw_search_response):
        result = normalize_search_response(raw_search_response)
        assert len(result["studies"]) == 1
        assert result["studies"][0]["nct_id"] == "NCT05678901"

    def test_next_page_token_none(self, raw_search_response):
        result = normalize_search_response(raw_search_response)
        assert result["next_page_token"] is None

    def test_empty_response(self, raw_empty_response):
        result = normalize_search_response(raw_empty_response)
        assert result["studies"] == []
        assert result["total_count"] == 0

    def test_shared_retrieved_at(self, raw_search_response):
        ts = "2024-06-15T12:00:00+00:00"
        result = normalize_search_response(raw_search_response, retrieved_at=ts)
        assert all(s["retrieved_at"] == ts for s in result["studies"])
