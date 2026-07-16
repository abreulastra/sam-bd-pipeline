import json

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from config import get_env

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_gspread_client():
    creds_info = json.loads(get_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, title, rows=2000, cols=20):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_headers(ws, headers):
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(headers)
        return list(headers)

    existing = ws.row_values(1)
    new_cols = [h for h in headers if h not in existing]
    if new_cols:
        existing = existing + new_cols
        # Single overwrite of row 1 — avoids the delete+insert race where a
        # concurrent run can read/write mid-way through and corrupt the header.
        ws.update([existing], "A1")

    return existing


def get_existing_ids(ws, header, required_headers):
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return set()

    df = pd.DataFrame(vals[1:], columns=header).reindex(columns=required_headers)
    if "noticeId" not in df.columns:
        return set()

    return set(df["noticeId"].astype(str))
