"""
Pydantic models documenting the normalized output schema.
These are the canonical types for OncoHub integration — tools return plain dicts
matching these shapes; use these for IDE support and validation in consumers.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Intervention(BaseModel):
    type: str
    name: str


class Eligibility(BaseModel):
    criteria_text: str
    sex: str
    min_age: str
    max_age: str
    healthy_volunteers: bool


class Location(BaseModel):
    country: str
    city: str
    facility: str
    status: str


class TurkeySite(BaseModel):
    city: str
    facility: str
    status: str


class NormalizedStudy(BaseModel):
    nct_id: str
    title: str
    status: str
    status_unknown_flag: bool
    phases: list[str] = Field(default_factory=list)
    study_type: str
    conditions: list[str] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    eligibility: Eligibility
    locations: list[Location] = Field(default_factory=list)
    has_turkey_site: bool
    turkey_sites: list[TurkeySite] = Field(default_factory=list)
    lead_sponsor: str
    last_update_post_date: str
    url: str
    retrieved_at: str
    freshness: Literal["live", "cached"] = "live"
    cached_at: Optional[str] = None


class SearchResponse(BaseModel):
    studies: list[NormalizedStudy] = Field(default_factory=list)
    total_count: int = 0
    next_page_token: Optional[str] = None


class DualSearchResponse(SearchResponse):
    turkey_count: int = 0
    turkey_search_error: Optional[str] = None
    note: str = ""


VALID_STATUSES: frozenset[str] = frozenset({
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
    "ENROLLING_BY_INVITATION",
    "UNKNOWN",
})

VALID_PHASES: frozenset[str] = frozenset({
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE2",
    "PHASE3",
    "PHASE4",
    "NA",
})
