"""
Google Sheets writer for the Pipeline tab.
Reuses the existing gspread client from sheets_client.py.
"""
import logging
import os
import sys

# Allow imports from src/ when run as a module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sheets_client import build_gspread_client, ensure_headers, get_or_create_worksheet

logger = logging.getLogger(__name__)

PIPELINE_TAB = "Pipeline"

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


def build_row(opportunity: dict) -> list:
    return [opportunity.get(h, "") for h in PIPELINE_HEADERS]


def append_opportunities(opportunities: list[dict], sheet_url: str | None = None) -> dict:
    """
    Append new opportunities to the Pipeline tab, skipping duplicates.
    Returns a summary dict with counts.
    """
    gc = build_gspread_client()

    if sheet_url:
        sh = gc.open_by_url(sheet_url)
    else:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        if not sheet_id:
            raise ValueError("Set GOOGLE_SHEET_ID or pass sheet_url")
        sh = gc.open_by_key(sheet_id)

    ws = get_or_create_worksheet(sh, PIPELINE_TAB, rows=5000, cols=len(PIPELINE_HEADERS) + 5)
    header = ensure_headers(ws, PIPELINE_HEADERS)

    existing_keys = get_existing_duplicate_keys(ws, header)
    logger.info("Existing duplicate keys in Pipeline tab: %d", len(existing_keys))

    new_rows = []
    skipped = 0

    for opp in opportunities:
        key = opp.get("duplicateKey", "")
        if key in existing_keys:
            skipped += 1
            logger.debug("Skipping duplicate: %s", key)
            continue
        new_rows.append(build_row(opp))
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
