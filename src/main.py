import os
import time
from datetime import datetime, timedelta, UTC

from dotenv import load_dotenv

from collect_sam import build_params, build_row, fetch_page
from config import get_env, load_config
from sheets_client import (
    build_gspread_client,
    ensure_headers,
    get_existing_ids,
    get_or_create_worksheet,
)
from utils import mmddyyyy

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
    per_agency_counts = {code: 0 for code in agency_codes}

    for agency_code in agency_codes:
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

                row_dict = build_row(item, agency_code, api_pulled_at_utc)
                new_rows.append([row_dict.get(h, "") for h in REQUIRED_HEADERS])
                existing_ids.add(notice_id)
                per_agency_counts[agency_code] += 1

                if len(new_rows) >= max_records:
                    break

            offset += limit
            if len(new_rows) >= max_records or offset >= total:
                break

            time.sleep(0.35)

    inserted = 0
    if new_rows:
        posted_date_idx = REQUIRED_HEADERS.index("postedDate")
        new_rows.sort(key=lambda row: row[posted_date_idx], reverse=True)
        ws.insert_rows(new_rows, row=2, value_input_option="USER_ENTERED")
        inserted = len(new_rows)

    print(f"Inserted {inserted} new rows at the top." if inserted else "No new rows to insert.")

    agencies_str = ",".join(agency_codes)
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
