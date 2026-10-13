"""
Google Sheets writer for the Pipeline tab.
Reuses the existing gspread client from sheets_client.py.
"""
import logging
import os
import sys
from datetime import datetime, timezone

import gspread

# Allow imports from src/ when run as a module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sheets_client import build_gspread_client, ensure_headers, get_or_create_worksheet

logger = logging.getLogger(__name__)

PIPELINE_TAB = "Pipeline"

# sam-bd-agent logs every opportunity it scores low to this tab (its config
# key is `rejected_title`) and deletes the row from Pipeline in the same run.
# Without checking it, a rejected item that's still posted on the source site
# is re-ingested -- and re-scored -- on every run until it expires.
REJECTED_TAB = "Rejected"

PIPELINE_HEADERS = [
    "source",
    "emailDate",
    "emailSubject",
    "alertName",
    "opportunityTitle",
    "donorClient",
    "countryRegion",
    "opportunityType",
    "status",
    "deadline",
    "deadlineISO",
    "url",
    "torText",
    "resourceLinks",
    "language",
    "fitScore",
    "fitLabel",
    "reviewSummary",
    "duplicateKey",
    "processedAtUTC",
    "owner",
    "pipelineStatus",
]


def get_existing_duplicate_keys(ws, header: list[str]) -> set[str]:
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return set()

    try:
        col_idx = header.index("duplicateKey")
    except ValueError:
        return set()

    return {row[col_idx] for row in vals[1:] if len(row) > col_idx and row[col_idx]}


def get_rejected_duplicate_keys(sh) -> set[str]:
    """
    duplicateKeys of Pipeline opportunities the agent already rejected.

    Read-only and never raises: if the tab is missing or unreadable we fall
    back to the old behaviour (dedupe against Pipeline only) rather than
    failing the run. The agent prunes this tab (`rejected_retention_days`),
    so a rejection only suppresses re-ingestion for that long.
    """
    try:
        values = sh.worksheet(REJECTED_TAB).get_all_values()
    except gspread.WorksheetNotFound:
        return set()
    except Exception as e:
        logger.warning("Could not read %s tab (%s) -- deduping against Pipeline only", REJECTED_TAB, e)
        return set()

    if len(values) <= 1:
        return set()
    header = values[0]
    try:
        key_i = header.index("duplicateKey")
    except ValueError:
        return set()
    tab_i = header.index("tab") if "tab" in header else None

    return {
        row[key_i]
        for row in values[1:]
        if len(row) > key_i
        and row[key_i]
        # the tab also logs Opportunities (SAM.gov) rejections; those keys
        # aren't Pipeline duplicateKeys
        and (tab_i is None or (len(row) > tab_i and row[tab_i] == PIPELINE_TAB))
    }


def build_row(opportunity: dict, header: list[str]) -> list:
    return [opportunity.get(h, "") for h in header]


def _open_spreadsheet(gc, sheet_url: str | None):
    if sheet_url:
        return gc.open_by_url(sheet_url)
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("Set GOOGLE_SHEET_ID or pass sheet_url")
    return gc.open_by_key(sheet_id)


def load_pipeline_state(source: str, sheet_url: str | None = None) -> tuple[set[str], int]:
    """
    Read-only, one sheet read. Returns:
      - every duplicateKey in the Pipeline tab and in the agent's Rejected
        log, so a fetcher can skip already-ingested or already-rejected items
        *before* spending API requests on them, and
      - how many `source` rows were already written today (UTC).

    The second is for sources under a per-day quota rather than just a rate
    limit (DevelopmentAid allows 100 unique tenders/24h). Counting what's
    already in the sheet keeps the budget correct even when a manual run
    lands on top of the scheduled one.
    """
    gc = build_gspread_client()
    sh = _open_spreadsheet(gc, sheet_url)
    try:
        ws = sh.worksheet(PIPELINE_TAB)
    except gspread.WorksheetNotFound:
        return set(), 0

    values = ws.get_all_values()
    if not values:
        return set(), 0
    header = values[0]
    keys = get_existing_duplicate_keys(ws, header)
    # Already-rejected items must be skipped too, or fetchers re-spend API
    # quota (DevelopmentAid detail fetches) on tenders the agent threw out.
    keys |= get_rejected_duplicate_keys(sh)

    try:
        src_i = header.index("source")
        at_i = header.index("processedAtUTC")
    except ValueError:
        return keys, 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used_today = sum(
        1
        for row in values[1:]
        if len(row) > max(src_i, at_i)
        and row[src_i] == source
        and row[at_i].startswith(today)
    )
    return keys, used_today


def append_opportunities(opportunities: list[dict], sheet_url: str | None = None) -> dict:
    """
    Append new opportunities to the Pipeline tab, skipping duplicates.
    Returns a summary dict with counts.
    """
    gc = build_gspread_client()
    sh = _open_spreadsheet(gc, sheet_url)

    ws = get_or_create_worksheet(sh, PIPELINE_TAB, rows=5000, cols=len(PIPELINE_HEADERS) + 5)
    header = ensure_headers(ws, PIPELINE_HEADERS)

    existing_keys = get_existing_duplicate_keys(ws, header)
    logger.info("Existing duplicate keys in Pipeline tab: %d", len(existing_keys))
    rejected_keys = get_rejected_duplicate_keys(sh)
    logger.info("Duplicate keys in Rejected tab (already scored low): %d", len(rejected_keys))
    existing_keys |= rejected_keys

    new_rows = []
    skipped = 0

    for opp in opportunities:
        key = opp.get("duplicateKey", "")
        if key in existing_keys:
            skipped += 1
            logger.debug("Skipping duplicate: %s", key)
            continue
        new_rows.append(build_row(opp, header))
        existing_keys.add(key)

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        logger.info("Appended %d new rows to Pipeline tab", len(new_rows))
    else:
        logger.info("No new rows to append")

    return {
        "appended": len(new_rows),
        "skipped": skipped,
        "total_processed": len(opportunities),
    }
