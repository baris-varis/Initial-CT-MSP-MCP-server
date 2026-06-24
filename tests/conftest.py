"""Shared fixtures and sample CT.gov API responses."""
from __future__ import annotations

import pytest

# ── Sample raw CT.gov API data ────────────────────────────────────────────────

def _make_raw_study(
    nct_id: str = "NCT05678901",
    title: str = "A Study of Sotorasib in KRAS G12C NSCLC",
    status: str = "RECRUITING",
    phases: list | None = None,
    conditions: list | None = None,
    has_turkey: bool = True,
) -> dict:
    locations = [
        {
            "country": "United States",
            "city": "Boston",
            "facility": "Massachusetts General Hospital",
            "status": "RECRUITING",
        }
    ]
    if has_turkey:
        locations.append({
            "country": "Turkey",
            "city": "Istanbul",
            "facility": "Istanbul University Hospital",
            "status": "RECRUITING",
        })

    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": title,
            },
            "statusModule": {
                "overallStatus": status,
                "lastUpdatePostDateStruct": {"date": "2024-05-01"},
            },
            "designModule": {
                "phases": phases or ["PHASE2"],
                "studyType": "INTERVENTIONAL",
            },
            "conditionsModule": {
                "conditions": conditions or ["Non-Small Cell Lung Cancer"],
            },
            "armsInterventionsModule": {
                "interventions": [
                    {"interventionType": "DRUG", "interventionName": "Sotorasib"}
                ]
            },
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion Criteria:\n- KRAS G12C mutation\n",
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "N/A",
                "healthyVolunteers": False,
            },
            "contactsLocationsModule": {"locations": locations},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Amgen"}},
        }
    }


def _make_search_response(studies: list | None = None, total: int | None = None) -> dict:
    studies = studies or [_make_raw_study()]
    return {
        "studies": studies,
        "totalCount": total if total is not None else len(studies),
        "nextPageToken": None,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def raw_study() -> dict:
    return _make_raw_study()


@pytest.fixture
def raw_study_no_turkey() -> dict:
    return _make_raw_study(has_turkey=False)


@pytest.fixture
def raw_search_response() -> dict:
    return _make_search_response()


@pytest.fixture
def raw_empty_response() -> dict:
    return {"studies": [], "totalCount": 0, "nextPageToken": None}


@pytest.fixture
def raw_turkey_study() -> dict:
    return _make_raw_study(
        nct_id="NCT11223344",
        title="Breast Cancer Trial in Turkey",
        conditions=["Breast Cancer"],
        has_turkey=True,
    )


# Expose helpers for tests that build custom responses
make_raw_study = _make_raw_study
make_search_response = _make_search_response
