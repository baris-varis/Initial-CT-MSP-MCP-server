"""
Normalize raw CT.gov v2 API responses into the flat OncoHub schema.
No external dependencies — pure transformation, fully testable.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _safe_str(obj: object, *keys: str, default: str = "") -> str:
    """Drill through nested dicts returning str, or default on any miss."""
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)  # type: ignore[assignment]
    if obj is None:
        return default
    return str(obj)


def _safe_list(obj: object, *keys: str) -> list:
    """Drill through nested dicts returning a list, or [] on any miss."""
    for k in keys:
        if not isinstance(obj, dict):
            return []
        obj = obj.get(k)  # type: ignore[assignment]
    return obj if isinstance(obj, list) else []


def _is_turkey(country: str) -> bool:
    c = country.strip().lower()
    # CT.gov sometimes returns "Turkey (Türkiye)" or other variants
    return "turkey" in c or "türkiye" in c or "turkiye" in c


def _normalize_location(loc: dict) -> dict:
    return {
        "country": _safe_str(loc, "country"),
        "city": _safe_str(loc, "city"),
        "facility": _safe_str(loc, "facility"),
        "status": _safe_str(loc, "status"),
    }


def normalize_study(raw: dict, retrieved_at: str | None = None) -> dict:
    """
    Transform one CT.gov study record into the OncoHub normalized schema.

    raw: the study object as returned by GET /studies/{nctId} or a member of
         the studies[] array from GET /studies.
    """
    if not isinstance(raw, dict):
        raw = {}

    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()

    ps = raw.get("protocolSection") or {}
    id_mod = ps.get("identificationModule") or {}
    status_mod = ps.get("statusModule") or {}
    design_mod = ps.get("designModule") or {}
    cond_mod = ps.get("conditionsModule") or {}
    arms_mod = ps.get("armsInterventionsModule") or {}
    elig_mod = ps.get("eligibilityModule") or {}
    loc_mod = ps.get("contactsLocationsModule") or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule") or {}

    nct_id = _safe_str(id_mod, "nctId")
    status = _safe_str(status_mod, "overallStatus")

    raw_locations = _safe_list(loc_mod, "locations")
    locations = [_normalize_location(loc) for loc in raw_locations if isinstance(loc, dict)]
    turkey_sites = [
        {"city": loc["city"], "facility": loc["facility"], "status": loc["status"]}
        for loc in locations
        if _is_turkey(loc["country"])
    ]

    raw_interventions = _safe_list(arms_mod, "interventions")
    interventions = [
        {
            "type": _safe_str(i, "interventionType"),
            "name": _safe_str(i, "interventionName"),
        }
        for i in raw_interventions
        if isinstance(i, dict)
    ]

    hv = elig_mod.get("healthyVolunteers")
    healthy_volunteers = bool(hv) if hv is not None else False

    return {
        "nct_id": nct_id,
        "title": _safe_str(id_mod, "briefTitle"),
        "status": status,
        "status_unknown_flag": status == "UNKNOWN",
        "phases": _safe_list(design_mod, "phases"),
        "study_type": _safe_str(design_mod, "studyType"),
        "conditions": _safe_list(cond_mod, "conditions"),
        "interventions": interventions,
        "eligibility": {
            "criteria_text": _safe_str(elig_mod, "eligibilityCriteria"),
            "sex": _safe_str(elig_mod, "sex"),
            "min_age": _safe_str(elig_mod, "minimumAge"),
            "max_age": _safe_str(elig_mod, "maximumAge"),
            "healthy_volunteers": healthy_volunteers,
        },
        "locations": locations,
        "has_turkey_site": len(turkey_sites) > 0,
        "turkey_sites": turkey_sites,
        "lead_sponsor": _safe_str(sponsor_mod, "leadSponsor", "name"),
        "last_update_post_date": _safe_str(
            status_mod, "lastUpdatePostDateStruct", "date"
        ),
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "retrieved_at": retrieved_at,
    }


def normalize_search_response(raw: dict, retrieved_at: str | None = None) -> dict:
    """Transform the full search response envelope into the OncoHub format."""
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    raw_studies = raw.get("studies") or []
    studies = [normalize_study(s, retrieved_at) for s in raw_studies if isinstance(s, dict)]
    return {
        "studies": studies,
        "total_count": raw.get("totalCount") or 0,
        "next_page_token": raw.get("nextPageToken") or None,
    }
