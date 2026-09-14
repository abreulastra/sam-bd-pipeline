import os
import time
from datetime import datetime, timedelta, UTC

import gspread
from dotenv import load_dotenv

from collect_sam import build_award_row, build_params, build_row, collect_awards, fetch_page
from config import get_env, load_config
from filters import passes_agency_filter, passes_naics_filter
from sheets_client import (
    build_gspread_client,
    ensure_headers,
    get_existing_ids,
    get_or_create_worksheet,
)
from utils import mmddyyyy, normalize_date

# Cleared on a high-priority row when SAM.gov shows a material change, so
# sam-bd-agent (which only scores rows with a blank fitLabel) re-reviews it.
RESCORE_FIELDS = ("fitLabel", "reviewSummary", "deadlineNote", "reviewedAtUTC")
DIFF_FIELDS = ("title", "naicsCode", "type", "deadline")

REQUIRED_HEADERS = [
    "noticeId",
    "title",
    "solicitationNumber",
    "postedDate",
    "type",
    "setAside",
    "naicsCode",
    "fullParentPathName",
    "fullParentPathCode",
    "agencyCodeQueried",
    "apiPulledAtUTC",
    "oppUrl",
    "deadline",
    "resourceLinks",
    # Award-specific fields — populated when a tracked opportunity is awarded
    "awardee",
    "awardAmount",
    "awardDate",
]

AWARDS_HEADERS = [
    "noticeId",
    "title",
    "solicitationNumber",
    "awardDate",
    "awardee",
    "awardAmount",
    "awardNumber",
    "naicsCode",
    "fullParentPathName",
    "agencyCodeQueried",
    "oppUrl",
    "relatedOpportunityId",
    "apiPulledAtUTC",
    "emailedAtUTC",
]

LOG_HEADERS = [
    "runAtUTC",
    "postedFrom",
    "postedTo",
    "agencies",
    "keyword",
    "newRows",
    "notes",
]


def _extract_award_fields(item: dict) -> dict:
    """Extract award-specific fields from a SAM.gov API response item."""
    award = item.get("award") or {}
    awardee = (award.get("awardee") or {}).get("name") or ""
    return {
        "awardee": awardee,
        "awardAmount": str(award.get("amount") or ""),
        "awardDate": normalize_date(award.get("date")),
    }


def recheck_high_priority(ws, header, api_key) -> tuple[int, int, int]:
    """
    Re-fetch already-scored high/medium-fit opportunities by noticeId and:
    - If SAM.gov shows a material change (deadline, scope), clear fitLabel so
      sam-bd-agent re-scores it.
    - If the type has changed to "Award Notice", write award fields (awardee,
      awardAmount, awardDate) and set fitLabel="awarded" so the agent emails
      an award alert without re-scoring or deleting the row.

    Returns (checked, updated, awarded).
    """
    needed = ("noticeId", "fitLabel", "postedDate") + DIFF_FIELDS
    if not all(h in header for h in needed):
        return 0, 0, 0

    idx = {h: header.index(h) for h in header}
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return 0, 0, 0

    checked = 0
    updated = 0
    awarded = 0

    for row_number, row in enumerate(vals[1:], start=2):
        fit = row[idx["fitLabel"]].strip().lower() if len(row) > idx["fitLabel"] else ""
        if fit not in ("high", "medium"):
            continue

        notice_id = row[idx["noticeId"]] if len(row) > idx["noticeId"] else ""
        if not notice_id:
            continue

        posted_date_str = row[idx["postedDate"]] if len(row) > idx["postedDate"] else ""
        try:
            posted_from = datetime.strptime(posted_date_str[:10], "%Y-%m-%d")
        except ValueError:
            posted_from = datetime.now(UTC) - timedelta(days=365)

        params = build_params(
            api_key=api_key,
            posted_from=posted_from,
            posted_to=datetime.now(UTC),
            limit=1,
            offset=0,
            notice_id=notice_id,
        )
        checked += 1
        time.sleep(0.35)

        try:
            data = fetch_page(params)
        except Exception as exc:
            print(f"  Recheck failed for {notice_id}: {exc}")
            continue

        items = data.get("opportunitiesData", []) or []
        if not items:
            continue
        item = items[0]

        fresh_type = item.get("type") or ""
        is_now_awarded = fresh_type.lower() == "award notice"

        if is_now_awarded:
            # Opportunity was awarded — capture who won and mark the row so
            # the agent emails an alert without re-scoring or deleting it.
            award_fields = _extract_award_fields(item)
            cell_updates = [
                {"range": gspread.utils.rowcol_to_a1(row_number, idx["type"] + 1), "values": [[fresh_type]]},
                {"range": gspread.utils.rowcol_to_a1(row_number, idx["fitLabel"] + 1), "values": [["awarded"]]},
            ]
            for field, value in award_fields.items():
                if field in idx:
                    cell_updates.append({"range": gspread.utils.rowcol_to_a1(row_number, idx[field] + 1), "values": [[value]]})
            ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
            awarded += 1
            updated += 1
            print(f"  Awarded: {notice_id} → {award_fields.get('awardee', 'unknown')}")
            continue

        fresh = {
            "title": item.get("title") or "",
            "naicsCode": str(item.get("naicsCode", "") or "").strip(),
            "type": fresh_type,
            "deadline": normalize_date(item.get("responseDeadLine")),
        }

        changed = {
            h: v for h, v in fresh.items()
            if (row[idx[h]] if len(row) > idx[h] else "") != v
        }
        if not changed:
            continue

        cell_updates = []
        for h, v in changed.items():
            cell_updates.append({"range": gspread.utils.rowcol_to_a1(row_number, idx[h] + 1), "values": [[v]]})
        for clear_field in RESCORE_FIELDS:
            if clear_field in idx:
                cell_updates.append({"range": gspread.utils.rowcol_to_a1(row_number, idx[clear_field] + 1), "values": [[""]]})

        ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
        updated += 1

    return checked, updated, awarded


def main():
    load_dotenv()
    config = load_config()

    api_key = get_env("SAM_API_KEY")
    sheet_url = get_env("SHEET_URL")
    worksheet_title = config["worksheet_title"]
    runlog_title = config["runlog_title"]
    agency_codes = config["agency_codes"]
    days_back = int(config["days_back"])
    limit = int(config["limit"])
    max_records = int(config["max_records"])
    exclude_naics = set(config["exclude_naics"])
    exclude_agencies = set(config.get("exclude_agencies", []))
    keyword = os.getenv("KEYWORDS") or None

    gc = build_gspread_client()
    sh = gc.open_by_url(sheet_url)

    ws = get_or_create_worksheet(sh, worksheet_title, rows=2000, cols=20)
    header = ensure_headers(ws, REQUIRED_HEADERS)

    log_ws = get_or_create_worksheet(sh, runlog_title, rows=1000, cols=10)
    ensure_headers(log_ws, LOG_HEADERS)

    now_utc = datetime.now(UTC)
    posted_to = now_utc
    posted_from = now_utc - timedelta(days=days_back)
    api_pulled_at_utc = now_utc.isoformat(timespec="seconds")

    existing_ids = get_existing_ids(ws, header, REQUIRED_HEADERS)

    new_rows = []
    query_codes = agency_codes or [None]
    per_agency_counts = {(code or "ALL"): 0 for code in query_codes}

    for agency_code in query_codes:
        offset = 0
        total = None

        while True:
            params = build_params(
                api_key=api_key,
                posted_from=posted_from,
                posted_to=posted_to,
                organization_code=agency_code,
                limit=limit,
                offset=offset,
                keyword=keyword,
            )

            data = fetch_page(params)

            if total is None:
                total = int(data.get("totalRecords", 0))

            items = data.get("opportunitiesData", []) or []
            if not items:
                break

            for item in items:
                notice_id = str(item.get("noticeId", "") or "").strip()
                if not notice_id or notice_id in existing_ids:
                    continue

                naics = str(item.get("naicsCode", "") or "").strip()
                if not passes_naics_filter(naics, exclude_naics):
                    continue

                full_parent_path_name = str(item.get("fullParentPathName", "") or "")
                if not passes_agency_filter(full_parent_path_name, exclude_agencies):
                    continue

                row_dict = build_row(item, agency_code, api_pulled_at_utc)
                new_rows.append([row_dict.get(h, "") for h in header])
                existing_ids.add(notice_id)
                per_agency_counts[agency_code or "ALL"] += 1

                if len(new_rows) >= max_records:
                    break

            offset += limit
            if len(new_rows) >= max_records or offset >= total:
                break

            time.sleep(0.35)

    inserted = 0
    if new_rows:
        posted_date_idx = header.index("postedDate")
        new_rows.sort(key=lambda row: row[posted_date_idx], reverse=True)
        ws.insert_rows(new_rows, row=2, value_input_option="USER_ENTERED")
        inserted = len(new_rows)

    print(f"Inserted {inserted} new rows at the top." if inserted else "No new rows to insert.")

    checked, updated, awarded = recheck_high_priority(ws, header, api_key)
    print(f"Rechecked {checked} high/medium opportunities, {updated} updated ({awarded} newly awarded).")

    # ── Awards tab: broad market monitoring ─────────────────────────────────
    awards_title = config.get("awards_title", "Awards")
    awards_ws = get_or_create_worksheet(sh, awards_title, rows=2000, cols=20)
    awards_header = ensure_headers(awards_ws, AWARDS_HEADERS)
    existing_award_ids = get_existing_ids(awards_ws, awards_header, AWARDS_HEADERS)

    awards_days_back = int(config.get("awards_days_back", days_back))
    awards_posted_from = now_utc - timedelta(days=awards_days_back)

    new_award_rows = collect_awards(
        api_key=api_key,
        agency_codes=agency_codes,
        exclude_naics=exclude_naics,
        exclude_agencies=exclude_agencies,
        posted_from=awards_posted_from,
        posted_to=posted_to,
        existing_ids=existing_award_ids,
        limit=limit,
        api_pulled_at_utc=api_pulled_at_utc,
    )
    if new_award_rows:
        awards_ws.insert_rows(
            [[r.get(h, "") for h in awards_header] for r in new_award_rows],
            row=2,
            value_input_option="USER_ENTERED",
        )
    print(f"Awards tab: {len(new_award_rows)} new award(s) added.")

    agencies_str = ",".join(agency_codes) if agency_codes else "ALL"
    notes = "; ".join(f"{k}:{v}" for k, v in per_agency_counts.items() if v > 0) or "no new rows"

    log_ws.insert_row(
        [
            api_pulled_at_utc,
            mmddyyyy(posted_from),
            mmddyyyy(posted_to),
            agencies_str,
            keyword or "",
            inserted,
            notes,
        ],
        index=2,
        value_input_option="USER_ENTERED",
    )

    print(f"Done -> {sh.url} | Data tab: {worksheet_title} | RunLog tab: {runlog_title}")


if __name__ == "__main__":
    main()
