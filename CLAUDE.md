# Working notes for Claude

[README.md](README.md) describes the system: sources, sheet columns, workflows, setup. This file only holds the constraints that aren't obvious from the code and that have already broken production once.

## This repo's job

Ingest and deduplicate. It **appends** rows to the `Opportunities` (SAM.gov) and `Pipeline` (Devex, DevelopmentAid, IDB BEO, World Bank, UNGM, CDMX) tabs and **never deletes** them.

Scoring, deleting low-fit rows and sending the email digest all belong to the private `sam-bd-agent` repo, which reads the same Google Sheet. Don't add LLM calls, fit judgments or keyword/content filtering here — that was decided deliberately: a filter at ingestion silently drops opportunities the agent would have scored as viable.

## Never write sheet rows by column position

`sam-bd-agent` appends its own columns (`fitLabel`, `reviewSummary`, `emailedAtUTC`, …) to the same tabs, so the live header is not the list in this repo. Build every row against the header `ensure_headers` returns:

```python
header = ensure_headers(ws, REQUIRED_HEADERS)
row = [row_dict.get(h, "") for h in header]
```

`src/main.py` and `src/email_pipeline/write_pipeline_sheet.py` (`build_row`) both do this. Positional writes plus two overlapping runs corrupted the Opportunities tab on 2026-07-16 (misaligned columns, ~2,500 duplicates; the tab had to be cleared and repopulated).

## Dedupe also checks the agent's `Rejected` tab

`append_opportunities` and `load_pipeline_state` (`write_pipeline_sheet.py`) treat a `duplicateKey` as already-seen if it's in `Pipeline` **or** in `Rejected` (rows with `tab == "Pipeline"`). The agent deletes low-fit rows from `Pipeline`, so checking only `Pipeline` re-ingested every rejected item daily for as long as the source site listed it (15 of 27 IDB BEO rows on 2026-10-07; B260226 was logged 3 times in 3 days) — and re-spent LLM scoring and, for DevelopmentAid, detail-fetch quota on it. The `Rejected` read never raises; if the tab is unreadable it falls back to Pipeline-only dedupe.

Related guards — keep them:
- `ensure_headers` (`src/sheets_client.py`) writes the whole header row in one atomic update.
- Both workflows have a `concurrency:` block so a scheduled run can't overlap a manual one.

## Workflows that install Playwright are pinned to `ubuntu-24.04`

`pipeline_email_daily.yml` runs `playwright install --with-deps chromium`, which doesn't support Ubuntu 26.04. GitHub moved `ubuntu-latest` to 26.04 on 2026-10-19, so every run since then died at "Install dependencies" (`Playwright does not support chromium on ubuntu26.04-x64`) — before any of our code ran, so no in-app alert could fire. The pipeline reads a 7-day window (`--days 7`), so a fixed run recovers what was missed as long as it happens within a week of the last good run. Don't switch back to `ubuntu-latest` until Playwright is upgraded to a release that supports the newer image. Workflows that don't use Playwright (`collect.yml`, `keep-alive.yml`) can stay on `ubuntu-latest`.

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

- **IDB BEO** (`fetch_idb_beo.py`): the ToR PDFs need session cookies from the same Playwright session that scraped the table, so its PDF reading has to stay here — it can't move to `sam-bd-agent` the way SAM.gov attachment reading did. The table is **paginated** (20 rows/page; 27 in Oct 2026) and `_scrape_all_pages` follows the pager. The pager items are plain `<li>`s with no `<a>` — click `li.pager__item--next`, which is removed from the DOM on the last page. Don't go back to `wait_until="networkidle"` for the first load: it timed out locally on this site; wait for the table selector instead.
- **Devex**: no API; metadata only, parsed from alert emails. Not developing it further is a decision (2026-09), not an oversight.
- **SAM.gov**: attachments are passed on as `resourceLinks` (pipe-separated URLs); `sam-bd-agent` downloads and reads them. The pipeline re-checks rows the agent scored `high`/`medium` (`recheck_high_priority` in `src/main.py`) so amended notices get re-scored; when one becomes an Award Notice it only records awardee/amount/date — awards are not emailed.
- **World Bank** (`fetch_worldbank.py`): the API silently ignores most params (`procurement_category`, `srt`, `order`, date ranges) and has no `publ_date` — only `*_exact` filters and `rows`/`os` paging work (newest-first by `noticedate`). The 2026-09-14 version relied on the ignored ones and pulled 26k archive rows. Keep the tripwire (non-CS-REOI notice → write nothing), the page/row caps, and the date cutoff; test changes with `--source worldbank --dry-run` first. Scope is LAC firm consulting REOIs only (Raúl's choice, 2026-09-16).
- **UNGM** (`fetch_ungm.py`): the search needs a session cookie + `__RequestVerificationToken` from `/Public/Notice`, sent as the `RequestVerificationToken` header with `Origin`/`X-Requested-With`; without them it's a 400. `PageSize` is server-pinned at 15, so it pages. The response is **HTML rows, not JSON** — keep the tripwire (first page unparseable → write nothing), the page/row caps, and the individual-consultant filter. Countries are filtered server-side with ids from the search form's `selNoticeCountry` list (LAC + USA, captured 2026-10-05); re-capture them if UNGM renumbers.
- **CDMX** (`fetch_cdmx.py`): the **only source filtered by keyword at ingestion** — a deliberate exception to the no-filtering rule, decided with Raúl on 2026-10-05 after measuring the portal: ~11 open tenders at a time (uniforms, sanitation, tree trimming) and ~2 consulting tenders in a whole year. `looks_relevant` keeps consulting/research/evaluation titles and drops goods purchases and clinical services ("estudios de colposcopia" and friends are the main false positives). If C230 ever wants full CDMX coverage, remove that gate rather than widening the regex indefinitely. torText comes from the anexo técnico PDF; keep the pdfminer log silencing or runs drown in CropBox warnings.
- **Awards tab**: market-wide SAM.gov award notices, kept as reference data only. The agent doesn't read it.
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
