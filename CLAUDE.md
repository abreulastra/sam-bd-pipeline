# Working notes for Claude

[README.md](README.md) describes the system: sources, sheet columns, workflows, setup. This file only holds the constraints that aren't obvious from the code and that have already broken production once.

## This repo's job

Ingest and deduplicate. It **appends** rows to the `Opportunities` (SAM.gov) and `Pipeline` (Devex, DevelopmentAid, IDB BEO) tabs and **never deletes** them.

Scoring, deleting low-fit rows and sending the email digest all belong to the private `sam-bd-agent` repo, which reads the same Google Sheet. Don't add LLM calls, fit judgments or keyword/content filtering here — that was decided deliberately: a filter at ingestion silently drops opportunities the agent would have scored as viable.

## Never write sheet rows by column position

`sam-bd-agent` appends its own columns (`fitLabel`, `reviewSummary`, `emailedAtUTC`, …) to the same tabs, so the live header is not the list in this repo. Build every row against the header `ensure_headers` returns:

```python
header = ensure_headers(ws, REQUIRED_HEADERS)
row = [row_dict.get(h, "") for h in header]
```

`src/main.py` and `src/email_pipeline/write_pipeline_sheet.py` (`build_row`) both do this. Positional writes plus two overlapping runs corrupted the Opportunities tab on 2026-07-16 (misaligned columns, ~2,500 duplicates; the tab had to be cleared and repopulated).

Related guards — keep them:
- `ensure_headers` (`src/sheets_client.py`) writes the whole header row in one atomic update.
- Both workflows have a `concurrency:` block so a scheduled run can't overlap a manual one.

## DevelopmentAid has two limits

`src/email_pipeline/fetch_developmentaid_api.py`

| Limit | Value | What counts |
|---|---|---|
| Membership quota (the binding one) | 100 unique tenders / rolling 24h | Detail fetches (`GET /{kind}/{id}`). Searches and document downloads don't. |
| Rate limit | 30 requests / minute | Every request |

- Check the sheet **before** fetching details: already-ingested items are skipped by `duplicateKey` so they cost no quota (`load_pipeline_state`).
- **Don't parallelize.** An 8-worker version hit 429s immediately and got the key locked out for more than 12 hours. Per-thread backoff doesn't help: threads don't coordinate, so the aggregate rate never drops. Requests go through one global throttle, sequentially.
- Constants: `MIN_REQUEST_INTERVAL = 2.2` s, `DAILY_TENDER_BUDGET = 90` (headroom under 100), `RATE_LIMIT_COOLDOWN = 60` s — on a 429 it waits once, then aborts the run rather than retrying into a lockout.
- The budget subtracts DevelopmentAid rows already written today (UTC), so a manual rerun doesn't blow the quota.
- The fetch queue is oldest-first, so items deferred by the budget are fetched before they age out of the `--days` search window.
- Support contact: ops@developmentaid.org. API docs: https://www.developmentaid.org/api/external

## Per-source notes

- **IDB BEO** (`fetch_idb_beo.py`): the ToR PDFs need session cookies from the same Playwright session that scraped the table, so its PDF reading has to stay here — it can't move to `sam-bd-agent` the way SAM.gov attachment reading did.
- **Devex**: no API; metadata only, parsed from alert emails. Not developing it further is a decision (2026-09), not an oversight.
- **SAM.gov**: attachments are passed on as `resourceLinks` (pipe-separated URLs); `sam-bd-agent` downloads and reads them. The pipeline only re-checks rows the agent scored `high` (`recheck_high_priority` in `src/main.py`) so amended notices get re-scored.
- **Attachment text** (`torText`) is a short heuristic excerpt (`extract_excerpt` in `src/utils.py`), not a raw dump. Its heading list covers English, Spanish and Portuguese because most LAC documents aren't in English.

## Testing

```bash
python -m pytest tests/ -v
python -m src.email_pipeline.run_email_pipeline --dry-run
python -m src.email_pipeline.run_email_pipeline --source developmentaid --days 2 --limit 3
```

- `--dry-run` writes nothing to the sheet (but DevelopmentAid detail fetches still count against the quota).
- `--limit N` caps new DevelopmentAid detail fetches — use it for cheap live tests.
- The local `.env` `SAM_API_KEY` can drift from the GitHub secret. A local 401 from SAM.gov doesn't mean production is broken — check the latest Actions run.
- `.env` holds real credentials and is gitignored; never commit it.
