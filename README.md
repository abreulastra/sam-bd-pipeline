# SAM BD Pipeline

Automated business development pipeline for **C230 Consulting Group**.

Pulls federal contracting opportunities from **SAM.gov** and email alerts from **Devex** and **DevelopmentAid**, filters and de-duplicates them, and writes results to a shared **Google Sheet** for daily review.

Opportunity scoring and analysis is handled separately by the [`sam-bd-agent`](https://github.com/abreulastra/sam-bd-agent) repository.

---

## What it does

| Source | Tab | Schedule |
|---|---|---|
| SAM.gov Opportunities API | `Opportunities` | Weekdays, 11:15 AM UTC |
| Devex email alerts | `Pipeline` | Weekdays, 12:00 PM UTC |
| DevelopmentAid email alerts | `Pipeline` | Weekdays, 12:00 PM UTC |

---

## Architecture

```
SAM.gov API  ──────────────────→  src/main.py
                                        ↓
                               Google Sheet: Opportunities tab

Gmail (Devex + DevelopmentAid) →  src/email_pipeline/
                                        ↓
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
│       ├── fetch_gmail.py             # Gmail API client
│       ├── parse_devex.py             # Devex HTML parser
│       ├── parse_developmentaid.py    # DevelopmentAid HTML parser
│       ├── normalize.py               # Deduplication and language detection
│       └── write_pipeline_sheet.py    # Writes to Pipeline tab
├── config/
│   └── settings.yaml                  # SAM.gov pipeline configuration
├── tests/
│   └── test_parsers.py                # Parser unit tests (16 tests)
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
| `--days` | `7` | Days back to search Gmail |
| `--dry-run` | off | Print results without writing to Sheets |
| `--limit` | none | Max emails to process per source |
| `--source` | `all` | Filter: `devex`, `developmentaid`, or `all` |

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
DEVELOPMENTAID_SENDER=pipeline@yourdomain.com
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
| `DEVELOPMENTAID_SENDER` | Email workflow | DevelopmentAid forwarding address |

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

`config/settings.yaml`'s `agency_codes` controls which agencies are queried:

```yaml
agency_codes: []   # empty = no restriction, search all of SAM.gov
```

By default this is empty, so the pipeline searches opportunities from every federal agency (subject to the NAICS filtering above). To restrict collection back to a specific set of agencies, list their SAM.gov organization codes:

```yaml
agency_codes:
  - "019"   # Department of State
  - "524"   # Millennium Challenge Corporation
```

Unrestricted, this returns roughly 8,000+ opportunities/week government-wide before NAICS filtering — `max_records` in `config/settings.yaml` is set to `20000` to give headroom above that.

---

## Re-checking High-Priority Opportunities

SAM.gov opportunities sometimes get amended after they're first posted (deadline extended, scope changed). Since `sam-bd-agent` only scores a row once (skips anything with a `fitLabel` already set) and this pipeline's dedup skips any `noticeId` already ingested, an amendment would otherwise go unnoticed forever.

Each `collect_sam_opportunities` run re-fetches every row where `fitLabel == "high"` directly by `noticeId`, and if SAM.gov's current `title`, `naicsCode`, `type`, or `deadline` differs from what's stored, it updates the row and clears `fitLabel`/`reviewSummary`/`deadlineNote`/`reviewedAtUTC` so `sam-bd-agent` re-scores it on its next run.

This deliberately does **not** skip rows whose stored deadline has already passed — skipping would mean never discovering that SAM.gov extended a deadline on something already marked expired, which defeats the point. The tradeoff: nothing currently prunes the high-fit set (only low-fit rows get deleted), so this re-check list grows unbounded over time. Not a problem at current volume, but worth revisiting if it ever becomes a real latency/cost concern.

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

**`Pipeline` tab** — Email-sourced opportunities (Devex + DevelopmentAid):

| Column | Description |
|---|---|
| `source` | `Devex` or `DevelopmentAid` |
| `emailDate` | Date the alert email was sent |
| `alertName` | Alert name parsed from email subject |
| `opportunityTitle` | Extracted opportunity title |
| `donorClient` | Donor or client if visible |
| `countryRegion` | Country or region if visible |
| `opportunityType` | `Tenders & Grants`, `Tender`, `Grant`, or `Opportunity` |
| `url` | Link to the opportunity |
| `language` | `English` or `Spanish` (auto-detected) |
| `duplicateKey` | Deterministic key for deduplication |
| `pipelineStatus` | Always `New` on first write |
| `fitScore` / `fitLabel` / `reviewSummary` | Filled later by `sam-bd-agent` |

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
