from datetime import datetime


def mmddyyyy(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y")


def normalize_date(value) -> str:
    return (value or "")[:10]


def opp_url_from_notice(notice_id: str) -> str:
    return f"https://sam.gov/opp/{notice_id}/view" if notice_id else ""
