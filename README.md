# SAM BD Pipeline

Automated business development pipeline for **C230 Consulting Group**.

Pulls federal contracting opportunities from **SAM.gov**, Devex email alerts, **DevelopmentAid**'s external API, and IDB BEO's procurement site, filters and de-duplicates them, and writes results to a shared **Google Sheet** for daily review.

Opportunity scoring and analysis is handled separately by the [`sam-bd-agent`](https://github.com/abreulastra/sam-bd-agent) repository.

---

## What it does

| Source | Tab | Schedule |
|---|---|---|
| SAM.gov Opportunities API | `Opportunities` | Weekdays, 11:15 AM UTC |
| Devex email alerts | `Pipeline` | Weekdays, 12:00 PM UTC |
| DevelopmentAid external API | `Pipeline` | Weekdays, 12:00 PM UTC |
| IDB BEO procurement scrape | `Pipeline` | Weekdays, 12:00 PM UTC |

---

## Architecture

```
SAM.gov API  ──────────────────→  src/main.py
                                        ↓
                               Google Sheet: Opportunities tab

Gmail (Devex) ─────────────────┐
DevelopmentAid API ─────────────┼→  src/email_pipeline/
IDB BEO web scrape ─────────────┘         ↓
                               Google Sheet: Pipeline tab
                                        ↓
                          (analyzed and pruned separately by sam-bd-agent)
```

---

## Repository Structure

```
sam-bd-pipeline/
├── src/
│   ├── main.py                        # SAM.gov pipeline entry point
│   ├── collect_sam.py                 # SAM.gov API client
│   ├── filters.py                     # NAICS filtering logic
│   ├── sheets_client.py               # Google Sheets helpers (shared)
│   ├── config.py                      # Config and env loader
│   ├── utils.py                       # Utilities
│   └── email_pipeline/
│       ├── run_email_pipeline.py      # Email pipeline entry point (CLI)
│       ├── fetch_gmail.py             # Gmail API client (Devex only)
│       ├── parse_devex.py             # Devex HTML parser
│       ├── fetch_developmentaid_api.py # DevelopmentAid external API client
│       ├── parse_developmentaid.py    # Unused — retired in favor of the API client above
│       ├── fetch_idb_beo.py           # IDB BEO procurement web scrape
│       ├── normalize.py               # Deduplication and language detection
│       └── write_pipeline_sheet.py    # Writes to Pipeline tab
├── config/
│   └── settings.yaml                  # SAM.gov pipeline configuration
├── tests/
│   └── test_parsers.py                # Parser unit tests (28 tests)
├── .github/workflows/
│   ├── collect.yml                    # SAM.gov daily workflow
│   ├── pipeline_email_daily.yml       # Email pipeline daily workflow
│   └── keep-alive.yml                 # Prevents GitHub disabling scheduled jobs
├── .env.example                       # Environment variable template
├── get_refresh_token.py               # Regenerates GMAIL_REFRESH_TOKEN when it expires
└── requirements.txt
```

---

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/abreulastra/sam-bd-pipeline.git
cd sam-bd-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your credentials
```

### 3. Run the SAM.gov pipeline

```bash
python src/main.py
```

### 4. Run the email pipeline

```bash
# Dry run — searches Gmail and prints results without writing to Sheets
python -m src.email_pipeline.run_email_pipeline --dry-run

# Real run — writes new rows to the Pipeline tab
python -m src.email_pipeline.run_email_pipeline --days 1

# All options
python -m src.email_pipeline.run_email_pipeline --help
```

**CLI flags:**

| Flag | Default | Description |
|---|---|---|
| `--days` | `7` | Days back to search (Gmail search window / DevelopmentAid `postedFrom`) |
| `--dry-run` | off | Print results without writing to Sheets |
| `--limit` | none | Max emails to process (Devex) / max *new* opportunities to fully fetch (DevelopmentAid); IDB BEO ignores it |
| `--source` | `all` | Filter: `devex`, `developmentaid`, `idbbeo`, or `all` |

---

## Environment Variables

Create a `.env` file (never commit this). See `.env.example` for the full template.

```env
# SAM.gov
SAM_API_KEY=SAM-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit

# Google Sheets (service account)
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
GOOGLE_SHEET_ID=YOUR_SHEET_ID

# Gmail OAuth (for email pipeline)
GMAIL_CLIENT_ID=your_client_id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your_client_secret
GMAIL_REFRESH_TOKEN=your_refresh_token
GMAIL_ACCOUNT_EMAIL=your-email@yourdomain.com

# DevelopmentAid external API (member companies only)
DEVELOPMENTAID_API_KEY=your_developmentaid_api_key
```

---

## GitHub Actions Secrets

Set these in **Settings → Secrets and variables → Actions**:

| Secret | Used by | Description |
|---|---|---|
| `SAM_API_KEY` | SAM.gov workflow | SAM.gov API key |
| `SHEET_URL` | SAM.gov workflow | Full Google Sheet URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Both workflows | Service account credentials JSON |
| `GOOGLE_SHEET_ID` | Email workflow | Sheet ID (the part between /d/ and /edit) |
| `GMAIL_CLIENT_ID` | Email workflow | OAuth client ID |
| `GMAIL_CLIENT_SECRET` | Email workflow | OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | Email workflow | OAuth refresh token |
| `GMAIL_ACCOUNT_EMAIL` | Email workflow | Gmail account to read |
| `DEVELOPMENTAID_API_KEY` | Email workflow | DevelopmentAid external API key |

---

## NAICS Filtering

Opportunities are **excluded** if their NAICS code falls in these sectors:

| Prefix | Sector |
|---|---|
| 11 | Agriculture, Forestry, Fishing |
| 21 | Mining, Oil and Gas |
| 22 | Utilities |
| 23 | Construction |
| 31–33 | Manufacturing |
| 42 | Wholesale Trade |
| 44–45 | Retail Trade |
| 48–49 | Transportation and Warehousing |
| 52 | Finance and Insurance |
| 53 | Real Estate |
| 55 | Management of Companies |
| 62 | Health Care and Social Assistance |
| 71 | Arts, Entertainment, Recreation |
| 72 | Accommodation and Food Services |

Additional specific codes (janitorial, telecom, insurance, etc.) are excluded via `config/settings.yaml`.

**Opportunities with no NAICS code are always included** — passed to the agent for review.

---

## Agency Scope

`config/settings.yaml`'s `agency_codes` controls which agencies are queried. As of 2026-08, this is a **curated allowlist** — each code triggers its own SAM.gov query, so only these agencies are collected at all:

```yaml
agency_codes:
  - "019"   # State, Department of
  - "524"   # Millennium Challenge Corporation
  - "011"   # U.S. Trade and Development Agency (USTDA)
  - "016"   # Labor, Department of
  - "013"   # Commerce, Department of
  - "077"   # U.S. International Development Finance Corporation (DFC)
  - "012"   # Agriculture, Department of (USDA)
```

Leave `agency_codes: []` to remove the restriction and search all of SAM.gov instead (subject to the NAICS filtering above) — this was the previous default, but it pulled in far more volume than the Opportunities-tab scoring pipeline could process, and most of it (domestic agency operations — Interior, HHS, Homeland Security, NASA, GSA, etc.) doesn't match C230's international development / MEL / justice-sector advisory focus anyway.

Codes are CGAC / SAM.gov `organizationCode` values. Look up a department's code via SAM.gov's Federal Hierarchy API:

```bash
curl "https://api.sam.gov/prod/federalorganizations/v1/orgs?api_key=$SAM_API_KEY&fhorgname=<NAME>&fhorgtype=Department%2FInd.%20agency&status=active"
```

Separately, `exclude_agencies` filters out specific top-level departments **after** they're fetched, matched against the first segment of `fullParentPathName` (e.g. `"DEPT OF DEFENSE"`). With a curated `agency_codes` allowlist this is now redundant in practice (DoD/VA are never queried), but it's left in place as a harmless safety net in case `agency_codes` is ever widened again:

```yaml
exclude_agencies:
  - "DEPT OF DEFENSE"
  - "VETERANS AFFAIRS, DEPARTMENT OF"
```

As of 2026-07, these two alone accounted for ~64% of all collected rows.

Unrestricted, this returns roughly 8,000+ opportunities/week government-wide before NAICS filtering — `max_records` in `config/settings.yaml` is set to `20000` to give headroom above that.

---

## Re-checking High-Priority Opportunities

SAM.gov opportunities sometimes get amended after they're first posted (deadline extended, scope changed). Since `sam-bd-agent` only scores a row once (skips anything with a `fitLabel` already set) and this pipeline's dedup skips any `noticeId` already ingested, an amendment would otherwise go unnoticed forever.

Each `collect_sam_opportunities` run re-fetches every row where `fitLabel == "high"` directly by `noticeId`, and if SAM.gov's current `title`, `naicsCode`, `type`, or `deadline` differs from what's stored, it updates the row and clears `fitLabel`/`reviewSummary`/`deadlineNote`/`reviewedAtUTC` so `sam-bd-agent` re-scores it on its next run.

This deliberately does **not** skip rows whose stored deadline has already passed — skipping would mean never discovering that SAM.gov extended a deadline on something already marked expired, which defeats the point. The tradeoff: nothing currently prunes the high-fit set (only low-fit rows get deleted), so this re-check list grows unbounded over time. Not a problem at current volume, but worth revisiting if it ever becomes a real latency/cost concern.

---

## DevelopmentAid API

DevelopmentAid opportunities used to arrive as forwarded alert emails, parsed from inconsistent HTML layouts. As of 2026-08, this pipeline instead calls DevelopmentAid's own external API directly (`fetch_developmentaid_api.py`) — member-company access, authenticated via `X-API-KEY`. `parse_developmentaid.py` (the old email parser) is left in the repo but no longer called.

**Scope**, set in `fetch_developmentaid_api.py`, matches what the retired email alerts covered:
- `LOCATIONS = [4, 103]` — Latin America & the Caribbean (region) + Mexico (country; a subset of the region, kept explicit for parity with the old "Mexico Contracts" saved search)
- `STATUSES = [2, 3]` — forecast, open (excludes closed/awarded/cancelled/shortlisted)
- Both `/tenders/search` and `/grants/search` are queried, each `--days` back by `postedFrom`/`postedTill`

For each match, `GET /tenders/{id}` (or `/grants/{id}`) fetches full details — donor, country, description, deadline, and a `documents[]` list — and each attachment (up to 5 per opportunity) is downloaded via `GET /{kind}/{id}/documents/{docId}`, text-extracted with `pdfplumber` (as `fetch_idb_beo.py` does for IDB's ToR PDFs), then reduced to a short heuristic excerpt around the first scope-defining heading (`utils.extract_excerpt`, ~1,500 chars total) before being stored in `torText`.

### Limits

Two separate limits apply, per DevelopmentAid support (`ops@developmentaid.org`):

| Limit | What counts | Notes |
|---|---|---|
| **100 unique tenders / rolling 24h** | each `GET /{kind}/{id}` — attachment downloads don't count separately | Membership plan quota. **This is the one that actually binds.** |
| **30 requests / minute** | every request, searches included | Throttle protection; raised from 20 on 2026-09-05 and now documented in their Swagger page |

A 7-day window is ~320 items, so a backlog necessarily drains over several days. Steady state is ~46 new/day, comfortably inside the quota. `fetch_developmentaid_api.py` therefore:

- spaces every request ≥ 2.2s apart (search pages, detail, each document — not just per item),
- on a `429` waits out a full 60s window, then aborts the run rather than grinding through hundreds of items (grinding is what previously kept the key locked out for 12+ hours),
- **checks the Pipeline tab's existing `duplicateKey`s first**, skipping already-ingested items before any detail/attachment request — searches are free against the tender quota, detail fetches aren't, so this is what keeps us inside it, and
- stops at `DAILY_TENDER_BUDGET` (90, leaving headroom under 100), **counting rows already written today**, so a manual run stacked on the scheduled one can't blow the quota. Deferred items are picked up next run — dedup means nothing is lost by leaving them.

`--limit N` caps new items below whatever the budget allows — handy for a cheap live test.

**Deliberately not used to filter anything.** The attachment text is stored for `sam-bd-agent`'s scoring to use, but every matching opportunity is still written to the sheet regardless of its content — a keyword or content-based hard filter here risks silently dropping a real opportunity that doesn't happen to use the expected terms, the same risk that made the SAM.gov `exclude_agencies` decision (above) require actual data first. If this needs to get more selective later, that's `sam-bd-agent`'s call to make with its existing LLM scoring, not a blocklist in the ingestion layer.

Deduplication uses DevelopmentAid's own numeric tender/grant ID (`developmentaid-api:tender:<id>` / `developmentaid-api:grant:<id>`) via `make_duplicate_key`'s `stable_id` param — far more reliable than the old text-based key, and immune to the two-different-HTML-layouts bug that caused duplicate rows under the email-based approach (see git history for `normalize.py`). One expected, harmless side effect: since the key format changed, an opportunity already ingested by email before the cutover and now also matched by the API will insert once more as a "new" row — a one-time overlap, not an ongoing issue.

Update/amendment detection (like the SAM.gov high-priority re-check above) was intentionally not built for this source yet, even though DevelopmentAid's `modifiedDate`/`modifiedAfter` filter would make it easier than SAM.gov's API allowed — it's a natural follow-up, not a blocker for the initial integration.

---

## Google Sheet Structure

Both pipelines write to the same spreadsheet:

**`Opportunities` tab** — SAM.gov federal contracting opportunities:

| Column | Description |
|---|---|
| `noticeId` | SAM.gov's unique ID for the opportunity |
| `title` | Opportunity title |
| `solicitationNumber` | SAM.gov solicitation number |
| `postedDate` | Date SAM.gov posted the notice |
| `type` | e.g. `Solicitation`, `Combined Synopsis/Solicitation`, `Presolicitation` |
| `setAside` | Set-aside code, if any |
| `naicsCode` | NAICS code |
| `fullParentPathName` / `fullParentPathCode` | Full agency hierarchy |
| `agencyCodeQueried` | Agency code this row was fetched under, or `ALL` |
| `apiPulledAtUTC` | When this pipeline fetched it |
| `oppUrl` | Link to the opportunity on sam.gov |
| `deadline` | Response deadline (`responseDeadLine` from SAM.gov), `YYYY-MM-DD` |
| `fitLabel` / `reviewSummary` / `deadlineNote` / `deadlineISO` / `reviewedAtUTC` / `emailedAtUTC` | Filled by `sam-bd-agent` — this pipeline never populates them, only clears them back to blank when [re-checking a high-priority row](#re-checking-high-priority-opportunities) that changed, to trigger a re-score |

Rows are written by **matching each value to the sheet's actual current header, by column name** — never by a hardcoded position. This is deliberate: `sam-bd-agent` appends its own columns to this same tab, so any code that assumes a fixed column order will silently misalign once that header changes (this exact bug corrupted the sheet on 2026-07-16 — see `sheets_client.ensure_headers` and `main.py`'s row-building for the fix). If you ever add a new column, add it to `REQUIRED_HEADERS` and nothing else needs to change.

Both workflows also carry a `concurrency` block so overlapping runs can't happen — that same 2026-07-16 incident was caused by two runs racing on the header at once.

**`Pipeline` tab** — Devex (email) + DevelopmentAid (API) + IDB BEO (web scrape) opportunities:

| Column | Description |
|---|---|
| `source` | `Devex`, `DevelopmentAid`, or `IDB BEO` |
| `emailDate` | Date the Devex alert email was sent (or fetch date for API/scrape sources) |
| `alertName` | Devex saved-search name, or `DevelopmentAid API (Tender/Grant)`, or `IDB BEO` |
| `opportunityTitle` | Opportunity title |
| `donorClient` | Donor or client if available |
| `countryRegion` | Country or region if available |
| `opportunityType` | `Tenders & Grants`, `Tender`, `Grant`, or a sub-sector string (IDB BEO) |
| `deadline` / `deadlineISO` | Submission deadline, raw and parsed |
| `url` | Link to the opportunity |
| `torText` | Extracted attachment/document text (DevelopmentAid, IDB BEO) — informational only, not used to filter rows; `sam-bd-agent`'s scoring is the judgment call |
| `resourceLinks` | Pipe-separated attachment download URLs (DevelopmentAid — requires `DEVELOPMENTAID_API_KEY` to actually download) |
| `language` | `English` or `Spanish` (auto-detected) |
| `duplicateKey` | Deterministic key for deduplication |
| `pipelineStatus` | Always `New` on first write |
| `emailedAtUTC` / `fitScore` / `fitLabel` / `reviewSummary` | Filled later by `sam-bd-agent` |

Rows are never overwritten by this pipeline — only appended when the `duplicateKey` is new.

`sam-bd-agent` will later **delete** any row it scores `fitLabel: low` from both the `Opportunities` and `Pipeline` tabs, right after scoring it — this is the only process that removes rows, and it does so to keep the sheet from growing unbounded with opportunities nobody will act on.

---

## Downstream Analysis

Opportunity scoring, prioritization, and email digests are handled by the separate **[sam-bd-agent](https://github.com/abreulastra/sam-bd-agent)** repository. This pipeline is responsible only for ingestion and deduplication. The Google Sheet is the shared data layer between the two systems.

---

## Obtaining Gmail OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com) → your project
2. Enable the **Gmail API** (APIs & Services → Library)
3. Go to **Google Auth Platform → Audience** → set to External, add test users
4. Go to **Clients → Create client** → type: **Desktop app**
5. Copy the **Client ID** and **Client Secret** shown right after creation (the secret is only shown once — if it's lost, delete the client and create a new one rather than hunting for it later)
6. Run `get_refresh_token.py` locally to generate the refresh token:

```bash
export GMAIL_CLIENT_ID="..."
export GMAIL_CLIENT_SECRET="..."
python get_refresh_token.py
```

7. A browser window opens — sign in as the Gmail account the pipeline reads, approve access, copy the printed refresh token.
8. Update the `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and `GMAIL_REFRESH_TOKEN` GitHub secrets with the new values.

### Troubleshooting: `RefreshError: invalid_grant` / "Token has been expired or revoked"

This means the stored `GMAIL_REFRESH_TOKEN` is dead — Google revokes refresh tokens after ~6 months of inactivity, or immediately if the OAuth client is deleted, the account's password changes, or access is manually revoked. Fix: repeat steps 6–8 above to mint a new one.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

---

## Security Notes

- Never commit `.env`, service account JSON, or OAuth tokens
- `.env` is in `.gitignore` and will never be committed
- All secrets are passed via environment variables or GitHub Secrets
- The keep-alive workflow runs on the 1st of each month to prevent GitHub from disabling scheduled jobs

---

## Author

Raúl Abreu-Lastra — C230 Consulting Group
