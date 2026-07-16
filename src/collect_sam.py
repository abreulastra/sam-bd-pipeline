import random
import time

import requests

from utils import mmddyyyy, normalize_date, opp_url_from_notice

API_BASE = "https://api.sam.gov/opportunities/v2/search"


def build_params(api_key, posted_from, posted_to, limit, offset, organization_code=None, keyword=None):
    params = {
        "api_key": api_key,
        "postedFrom": mmddyyyy(posted_from),
        "postedTo": mmddyyyy(posted_to),
        "limit": limit,
        "offset": offset,
    }
    if organization_code:
        params["organizationCode"] = organization_code
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


def build_row(item, agency_code, api_pulled_at_utc):
    notice_id = str(item.get("noticeId", "") or "").strip()
    naics = str(item.get("naicsCode", "") or "").strip()

    return {
        "noticeId": notice_id,
        "title": item.get("title"),
        "solicitationNumber": item.get("solicitationNumber"),
        "postedDate": normalize_date(item.get("postedDate")),
        "deadline": normalize_date(item.get("responseDeadLine")),
        "type": item.get("type"),
        "setAside": item.get("setAside"),
        "naicsCode": naics,
        "fullParentPathName": item.get("fullParentPathName"),
        "fullParentPathCode": item.get("fullParentPathCode"),
        "agencyCodeQueried": agency_code or "ALL",
        "apiPulledAtUTC": api_pulled_at_utc,
        "oppUrl": opp_url_from_notice(notice_id),
    }
