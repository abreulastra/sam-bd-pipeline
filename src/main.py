
import json
import os
import random
import time
from datetime import datetime, timedelta, UTC

import gspread
import pandas as pd
import requests
import yaml
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


API_BASE = "https://api.sam.gov/opportunities/v2/search"

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


def load_config(path="config/settings.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_gspread_client():
    service_account_json = get_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds_info = json.loads(service_account_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)


def mmddyyyy(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y")


def normalize_date(value) -> str:
    return (value or "")[:10]


def opp_url_from_notice(notice_id: str) -> str:
    return f"https://sam.gov/opp/{notice_id}/view" if notice_id else ""


def build_params(api_key, posted_from, posted_to, organization_code, limit, offset, keyword=None):
    params = {
        "api_key": api_key,
        "postedFrom": mmddyyyy(posted_from),
        "postedTo": mmddyyyy(posted_to),
        "limit": limit,
        "offset": offset,
        "organizationCode": organization_code,
    }
    if keyword:
        params["title"] = keyword
    return params


def fetch_page(params, retries=3):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(API_BASE, params=params, timeout=45)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            last_exc = exc
            if getattr(exc, "response", None) is not None and exc.response.status_code in (401, 403):
                raise RuntimeError(
                    f"SAM API authentication failed ({exc.response.status_code}): "
                    f"{exc.response.text[:300]}"
                ) from exc
            time.sleep((2 ** attempt) + random.random())
    raise last_exc


def ensure_headers(ws, headers):
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(headers)
        return headers

    existing_headers = ws.row_values(1)
    changed = False
    for h in headers:
        if h not in existing_headers:
            existing_headers.append(h)
            changed = True

    if changed:
        ws.delete_rows(1)
        ws.insert_row(existing_headers, 1)

    return ws.row_values(1)


def get_existing_ids(ws, header, required_headers):
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return set()

    df_existing = pd.DataFrame(vals[1:], columns=header).reindex(columns=required_headers)
    if "noticeId" not in df_existing.columns:
        return set()

    return set(df_existing["noticeId"].astype(str))


def build_row(item, agency_code, api_pulled_at_utc):
    notice_id = str(item.get("noticeId", "") or "").strip()
    naics = str(item.get("naicsCode", "") or "").strip()

    return {
        "noticeId": notice_id,
        "title": item.get("title"),
        "solicitationNumber": item.get("solicitationNumber"),
        "postedDate": normalize_date(item.get("postedDate")),
        "type": item.get("type"),
        "setAside": item.get("setAside"),
        "naicsCode": naics,
        "fullParentPathName": item.get("fullParentPathName"),
        "fullParentPathCode": item.get("fullParentPathCode"),
        "agencyCodeQueried": agency_code,
        "apiPulledAtUTC": api_pulled_at_utc,
        "oppUrl": opp_url_from_notice(notice_id),
    }


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
    keyword = os.getenv("KEYWORDS") or None

    gc = build_gspread_client()
    sh = gc.open_by_url(sheet_url)

    try:
        ws = sh.worksheet(worksheet_title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_title, rows=2000, cols=20)

    header = ensure_headers(ws, REQUIRED_HEADERS)

    try:
        log_ws = sh.worksheet(runlog_title)
    except gspread.WorksheetNotFound:
        log_ws = sh.add_worksheet(title=runlog_title, rows=1000, cols=10)

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

                naics = str(item.get("naicsCode", "") or "").strip()
                if not (naics.startswith("5") and naics not in exclude_naics):
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
    notes = "; ".join([f"{k}:{v}" for k, v in per_agency_counts.items() if v > 0]) or "no new rows"

    log_ws.insert_row(
        [
            api_pulled_at_utc,
            mmddyyyy(posted_from),
            mmddyyyy(posted_to),
            agencies_str,
            (keyword or ""),
            inserted,
            notes,
        ],
        index=2,
        value_input_option="USER_ENTERED",
    )

    print(f"Done -> {sh.url} | Data tab: {worksheet_title} | RunLog tab: {runlog_title}")


if __name__ == "__main__":
    main()
