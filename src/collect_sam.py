import random
import time

import requests

from utils import mmddyyyy, normalize_date, opp_url_from_notice

API_BASE = "https://api.sam.gov/opportunities/v2/search"


def build_params(api_key, posted_from, posted_to, limit, offset, organization_code=None, keyword=None, notice_id=None):
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
    if notice_id:
        params["noticeid"] = notice_id
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


def build_award_row(item, agency_code, api_pulled_at_utc, related_opportunity_id=""):
    """Build an Awards-tab row from a SAM.gov Award Notice API item."""
    notice_id = str(item.get("noticeId", "") or "").strip()
    award = item.get("award") or {}
    awardee = (award.get("awardee") or {}).get("name") or ""
    return {
        "noticeId": notice_id,
        "title": item.get("title") or "",
        "solicitationNumber": item.get("solicitationNumber") or "",
        "awardDate": normalize_date(award.get("date")),
        "awardee": awardee,
        "awardAmount": str(award.get("amount") or ""),
        "awardNumber": str(award.get("number") or ""),
        "naicsCode": str(item.get("naicsCode", "") or "").strip(),
        "fullParentPathName": item.get("fullParentPathName") or "",
        "agencyCodeQueried": agency_code or "ALL",
        "oppUrl": opp_url_from_notice(notice_id),
        "relatedOpportunityId": related_opportunity_id,
        "apiPulledAtUTC": api_pulled_at_utc,
        "emailedAtUTC": "",
    }


def collect_awards(
    api_key,
    agency_codes,
    exclude_naics,
    exclude_agencies,
    posted_from,
    posted_to,
    existing_ids,
    limit,
    api_pulled_at_utc,
    max_records=500,
):
    """
    Fetch Award Notice records from SAM.gov for the configured agency codes.
    Returns a list of Awards-tab-compatible dicts for notices not already in
    existing_ids.
    """
    from filters import passes_agency_filter, passes_naics_filter

    new_rows = []
    query_codes = agency_codes or [None]

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
            )
            params["ptype"] = "a"  # Award Notice only

            try:
                data = fetch_page(params)
            except Exception as exc:
                print(f"  Awards fetch failed (agency={agency_code}, offset={offset}): {exc}")
                break

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

                full_parent = str(item.get("fullParentPathName", "") or "")
                if not passes_agency_filter(full_parent, exclude_agencies):
                    continue

                new_rows.append(build_award_row(item, agency_code, api_pulled_at_utc))
                existing_ids.add(notice_id)

                if len(new_rows) >= max_records:
                    break

            offset += limit
            if len(new_rows) >= max_records or (total is not None and offset >= total):
                break

            time.sleep(0.35)

    return new_rows


def build_row(item, agency_code, api_pulled_at_utc):
    notice_id = str(item.get("noticeId", "") or "").strip()
    naics = str(item.get("naicsCode", "") or "").strip()
    resource_links = item.get("resourceLinks") or []

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
        # Direct attachment download URLs from SAM.gov's own API -- more
        # reliable than scraping the notice page, whose Attachments section
        # loads asynchronously and isn't present in the initial page HTML.
        "resourceLinks": "|".join(resource_links),
    }
