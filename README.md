# ClinicalTrials.gov MCP Server — OncoHub

A production-quality MCP (Model Context Protocol) server that wraps the [ClinicalTrials.gov v2 API](https://clinicaltrials.gov/data-api/api). Designed for the **OncoHub Tumor Council** as the data source for `clinical_trial_matching_module v2 / A2.3`.

**What this server does:** structured, TTL-cached access to CT.gov trial data (worldwide + Turkey) via 4 MCP tools.
**What it does not do:** clinical eligibility assessment, scoring, or any form of clinical judgment — that is the consuming system's responsibility.

---

## Why Python + FastMCP?

- CT.gov v2 is a plain JSON REST API — no special SDK needed
- FastMCP's HTTP transport satisfies claude.ai's remote connector requirement (STDIO won't work there)
- `httpx` + `asyncio` give clean async retry/backoff
- SQLite cache requires zero infrastructure beyond the process itself

---

## Project Structure

```
CT-MSP/
├── server.py          # FastMCP server + 4 MCP tools (entry point)
├── ctgov_client.py    # Async httpx client, rate limiting, retry
├── normalize.py       # Raw CT.gov JSON → flat OncoHub schema
├── cache.py           # SQLite TTL cache (no external deps)
├── models.py          # Pydantic models (schema documentation)
├── tests/
│   ├── conftest.py    # Shared fixtures + sample API responses
│   ├── test_normalize.py
│   ├── test_client.py
│   ├── test_cache.py
│   ├── test_tools.py  # MCP tool integration tests (mocked)
│   └── test_smoke.py  # Live API smoke tests (opt-in)
├── Dockerfile
├── render.yaml        # One-click Render deployment
├── pyproject.toml
└── .env.example
```

---

## Local Setup

**Requirements:** Python 3.11+, pip

```bash
# 1. Clone / navigate to project directory
cd CT-MSP

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Configure environment
cp .env.example .env
# Edit .env if needed (defaults work for local development)

# 5. Run the server
python server.py
# Server starts on http://localhost:8000
# MCP endpoint: http://localhost:8000/mcp
```

### Test with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

This opens a browser UI where you can call tools interactively.

### Run tests

```bash
pytest                         # all unit tests (no network)
pytest -m smoke                # live CT.gov smoke tests (requires internet)
pytest tests/test_normalize.py # specific module
```

---

## Public Deployment

> **Required** for claude.ai connector — claude.ai only connects to public HTTPS endpoints.

### Option A: Render (recommended — free tier, auto-HTTPS)

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → **New** → **Web Service** → connect your repo.
3. Render auto-detects `render.yaml` — click **Apply**.
4. Wait ~3 min for the first build.
5. Your URL: `https://ctgov-mcp-oncohub.onrender.com` (or your custom name).
6. **Free tier caveat:** the service sleeps after 15 min of inactivity (first request takes ~30s to wake). The SQLite cache resets on restart since `/tmp` is ephemeral. Upgrade to Starter ($7/mo) for a persistent disk (`/data`).

**Persistent disk (Starter plan):** uncomment the `disk:` section in `render.yaml` and set `CACHE_PATH=/data/ctgov.db`.

### Option B: Railway

```bash
# Install Railway CLI
npm i -g @railway/cli
railway login
railway init
railway up
```

Set environment variables via `railway variables set STATUS_TTL_DAYS=7 ...`

### Option C: Fly.io

```bash
fly launch       # detects Dockerfile automatically
fly deploy
fly secrets set STATUS_TTL_DAYS=7 META_TTL_DAYS=30
```

Add a volume for persistent cache:
```bash
fly volumes create ctgov_cache --size 1
```
Then set `CACHE_PATH=/data/ctgov.db` in `fly.toml`.

### Option D: Any Docker host

```bash
docker build -t ctgov-mcp .
docker run -p 8000:8000 \
  -e STATUS_TTL_DAYS=7 \
  -v ctgov_cache:/data \
  -e CACHE_PATH=/data/ctgov.db \
  ctgov-mcp
```

---

## Adding to claude.ai as a Custom Connector

> **Prerequisites:**
> - Server running at a public HTTPS URL (e.g. `https://ctgov-mcp-oncohub.onrender.com`)
> - claude.ai **Pro, Team, or Enterprise** plan
> - OncoHub Project must be **private**

### Steps (Individual / Pro)

1. Go to **claude.ai → [Your name] → Settings → Connectors**.
2. Click **"+"** → **"Add custom connector"**.
3. **Name:** `ClinicalTrials.gov (OncoHub)`
4. **URL:** `https://<your-host>/mcp`
5. **Auth:** leave OAuth fields blank (this server has no auth).
6. Click **Add**.

### Steps (Team / Enterprise)

1. An **Owner** goes to **Organization Settings → Connectors → Add connector** (same fields as above).
2. Members connect it via **[+] → Connectors** in any conversation.

### Activating in the Tumor Council Project

1. Open the **OncoHub** project.
2. In a conversation, click **"+" (bottom-left) → Connectors**.
3. Toggle **ClinicalTrials.gov (OncoHub)** ON.
4. The system prompt should reference tool names (e.g. `dual_source_search`) for the module to call them automatically during report generation.

> **Note:** `claude_desktop_config.json` (STDIO transport) does NOT work with claude.ai Projects — only remote HTTP/SSE connectors work there.

---

## MCP Tools Reference

### `dual_source_search` ← Primary council entry point

Searches worldwide AND Turkey in two sequential calls, merges by NCT ID, marks `has_turkey_site`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `condition` | str | required | Disease/condition (maps from A1 tumor field) |
| `term` | str? | null | Biomarker / keyword (maps from A1 biomarker field) |
| `intervention` | str? | null | Drug or target (maps from A1 treatment field) |
| `statuses` | list[str]? | RECRUITING, NOT_YET_RECRUITING | Overall status filter |
| `phases` | list[str]? | null | Phase filter (maps from A1 treatment line) |
| `page_size` | int | 20 | Results per sub-query (max 50) |
| `force_refresh` | bool | false | Skip cache |

**Returns:** `{studies: [...], total_count, turkey_count, next_page_token, note}`

---

### `search_trials`

Parameterized worldwide search.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `condition` | str | required | Disease/condition |
| `term` | str? | null | Free-text biomarker or keyword |
| `intervention` | str? | null | Drug or target |
| `statuses` | list[str]? | RECRUITING, NOT_YET_RECRUITING | See valid values below |
| `phases` | list[str]? | null | See valid values below |
| `country` | str? | null | Country name for location filter |
| `page_size` | int | 20 | Max 50 recommended |
| `page_token` | str? | null | Pagination cursor |
| `force_refresh` | bool | false | Skip cache |

**Valid `statuses`:** `RECRUITING`, `NOT_YET_RECRUITING`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `SUSPENDED`, `ENROLLING_BY_INVITATION`, `UNKNOWN`

**Valid `phases`:** `EARLY_PHASE1`, `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4`, `NA`

---

### `search_turkey_trials`

Convenience wrapper: `search_trials` with `country="Turkey"`. Same parameters (minus `country`).

---

### `get_trial`

Fetch full details for a single study.

| Parameter | Type | Description |
|---|---|---|
| `nct_id` | str | NCT number, e.g. `"NCT05678901"` |
| `force_refresh` | bool | Skip cache |

**Returns:** single normalized study with full `eligibility.criteria_text` and all `locations`.

---

## Normalized Study Schema

Every tool returns studies in this flat schema:

```json
{
  "nct_id": "NCT05678901",
  "title": "A Study of Sotorasib...",
  "status": "RECRUITING",
  "status_unknown_flag": false,
  "phases": ["PHASE2"],
  "study_type": "INTERVENTIONAL",
  "conditions": ["Non-Small Cell Lung Cancer"],
  "interventions": [{"type": "DRUG", "name": "Sotorasib"}],
  "eligibility": {
    "criteria_text": "Inclusion Criteria:\n- KRAS G12C...",
    "sex": "ALL",
    "min_age": "18 Years",
    "max_age": "N/A",
    "healthy_volunteers": false
  },
  "locations": [
    {"country": "Turkey", "city": "Istanbul", "facility": "IUH", "status": "RECRUITING"}
  ],
  "has_turkey_site": true,
  "turkey_sites": [{"city": "Istanbul", "facility": "IUH", "status": "RECRUITING"}],
  "lead_sponsor": "Amgen",
  "last_update_post_date": "2024-05-01",
  "url": "https://clinicaltrials.gov/study/NCT05678901",
  "retrieved_at": "2026-06-24T10:30:00+00:00",
  "freshness": "live",
  "cached_at": null
}
```

**Key fields for clinicians:**
- `status_unknown_flag: true` → status unverified for 2+ years, verify before acting
- `retrieved_at` → when data was fetched from CT.gov (always surface this)
- `freshness: "cached"` → data served from local cache; `cached_at` shows when it was stored

---

## OncoHub Integration: A1 → A2.3 Field Mapping

| A1 Patient Profile Field | `dual_source_search` Parameter | Notes |
|---|---|---|
| Tumor type / primary diagnosis | `condition` | Required |
| Biomarker (e.g. KRAS G12C, PD-L1) | `term` | Free text, forwarded to `query.term` |
| Current/target drug | `intervention` | Forwarded to `query.intr` |
| Treatment line / phase preference | `phases` | e.g. `["PHASE2","PHASE3"]` |
| Eligibility status filter | `statuses` | Default: RECRUITING + NOT_YET_RECRUITING |

**Module flow:**
```
A1 Patient Card
    ↓
A2.3 dual_source_search(condition, term, intervention, phases)
    ↓
Normalized studies[] with has_turkey_site, freshness, retrieved_at
    ↓
A3 Eligibility Matrix
    ↓
A5 Council Report Output
```

The server provides data only. A3 performs eligibility matching; A5 formats the output.

---

## Cache & Freshness Configuration

| Variable | Default | Description |
|---|---|---|
| `STATUS_TTL_DAYS` | 7 | Max age for cached recruiting/status data |
| `META_TTL_DAYS` | 30 | Reserved for future eligibility-only caching |
| `CACHE_PATH` | `.cache/ctgov.db` | SQLite file path |

**Why TTL matters for patient safety:** Trial status changes frequently. A "RECRUITING" status cached 30 days ago may no longer be accurate. STATUS_TTL_DAYS=7 ensures clinicians see data verified within one week, or the server fetches fresh data.

**Cache behavior:**
- Hit (fresh): returns stored data with `freshness: "cached"` + `cached_at` timestamp
- Miss or expired: fetches from CT.gov, updates cache, returns with `freshness: "live"`
- `force_refresh: true`: always fetches live, updates cache

**Cloud deployments:** On free-tier hosts with ephemeral storage, the cache resets on each restart — all requests go live until the cache warms up again. Use a persistent volume for production.

---

## Rate Limiting & CT.gov ToU Compliance

- Minimum 1 second between API requests (`CTGOV_RATE_INTERVAL=1.0`)
- Automatic exponential-backoff retry on network/5xx errors (max 3 attempts)
- No contact-harvesting: email, phone, and investigator PII are never included in output
- No bulk corpus download: `pageSize` capped at 50 per call
- `User-Agent` header identifies this client to CT.gov

---

## No Authentication Required

CT.gov v2 is a public API — no API key, no OAuth. This server also has no auth on its `/mcp` endpoint (it serves read-only public data). For production deployments where you want to restrict access, place a reverse proxy with IP allowlisting or a simple bearer token in front.
