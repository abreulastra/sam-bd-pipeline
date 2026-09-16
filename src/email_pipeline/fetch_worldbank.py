"""
World Bank procurement notices -> Pipeline tab.

Public API, no auth: https://search.worldbank.org/api/v2/procnotices

What the API actually honors (verified live 2026-09-16):
  - `<field>_exact=` filters, e.g. procurement_group_exact=CS and
    notice_type_exact=Request for Expression of Interest
  - `rows` / `os` paging; results come newest-first by `noticedate`
    ("15-Sep-2026") and stay in that order across pages
What it silently IGNORES: procurement_category, srt, order, strdate/enddate.
There is no `publ_date` field. The first version of this fetcher relied on
those, never hit its date cutoff, and pulled 26k archive notices (back to
2013) into the sheet.

So this version is defensive:
  - Tripwire: if a returned notice isn't a CS Request for Expression of
    Interest, or has no parseable noticedate, the filters stopped working --
    log an error and return nothing rather than write junk.
  - Hard caps on pages read and rows returned per run.
  - Filters here: firm selections only (drops INDV = individual consultant),
    deadline not past, Latin America & Caribbean only.

Volume at time of writing: ~40 firm consulting notices/month for LAC.
`notice_text` (full notice HTML) goes into torText as its opening
TOR_TEXT_CHARS characters -- for REOIs that's the project, assignment title
and services description. (utils.extract_excerpt isn't used here: it tends to
land on "Terms of Reference ... can be found at <website>", which has no
scope.) sam-bd-agent can then score without fetching the page.
"""
import logging
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from email_pipeline.normalize import infer_language, make_duplicate_key

logger = logging.getLogger(__name__)

SOURCE = "World Bank"
API_BASE = "https://search.worldbank.org/api/v2/procnotices"
NOTICE_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"

PROCUREMENT_GROUP = "CS"  # consulting services
NOTICE_TYPE = "Request for Expression of Interest"
INDIVIDUAL_METHOD = "INDV"  # Individual Consultant Selection -- jobs, not firm contracts

PAGE_SIZE = 100
MAX_PAGES = 10       # ~1,000 notices, several weeks of global CS notices
MAX_NEW_ROWS = 40    # about a month of LAC volume; more in one run means something is wrong
REQUEST_TIMEOUT = 30
MIN_REQUEST_INTERVAL = 0.5
TOR_TEXT_CHARS = 1500

# Matched as substrings of project_ctry_name, which also uses WB region labels
# ("Caribbean", "OECS Countries", "Latin America") and WB spellings
# ("St. Lucia", "Bahamas, The", "Venezuela, Republica Bolivariana de").
LAC_COUNTRY_TERMS = (
    "latin america", "caribbean", "oecs", "western hemisphere", "central america",
    "antigua", "argentina", "bahamas", "barbados", "belize", "bolivia", "brazil", "chile",
    "colombia", "costa rica", "cuba", "dominica", "ecuador", "el salvador", "grenada",
    "guatemala", "guyana", "haiti", "honduras", "jamaica", "mexico", "nicaragua", "panama",
    "paraguay", "peru", "st. kitts", "st. lucia", "st. vincent", "saint kitts", "saint lucia",
    "saint vincent", "suriname", "trinidad", "uruguay", "venezuela",
)

_next_allowed = 0.0


def _throttle():
    global _next_allowed
    wait = _next_allowed - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _next_allowed = time.monotonic() + MIN_REQUEST_INTERVAL


def _get_page(offset: int) -> list[dict]:
    _throttle()
    params = {
        "format": "json",
        "rows": PAGE_SIZE,
        "os": offset,
        "procurement_group_exact": PROCUREMENT_GROUP,
        "notice_type_exact": NOTICE_TYPE,
    }
    resp = requests.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("procnotices") or []


def _notice_date(notice: dict) -> date | None:
    try:
        return datetime.strptime(notice.get("noticedate") or "", "%d-%b-%Y").date()
    except ValueError:
        return None


def _deadline_iso(notice: dict) -> str:
    raw = notice.get("submission_deadline_date") or ""
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def is_lac(country: str) -> bool:
    c = (country or "").lower()
    return any(term in c for term in LAC_COUNTRY_TERMS)


def duplicate_key_for(notice_id) -> str:
    return make_duplicate_key(SOURCE, "", "", "", stable_id=f"world-bank:{notice_id}")


def _to_pipeline_row(notice: dict) -> dict:
    notice_id = str(notice.get("id", ""))
    title = (notice.get("bid_description") or notice.get("project_name") or "").strip()
    notice_text = BeautifulSoup(notice.get("notice_text") or "", "html.parser").get_text(" ", strip=True)
    deadline_iso = _deadline_iso(notice)
    return {
        "source": SOURCE,
        "alertName": f"{SOURCE} API",
        "opportunityTitle": title,
        "donorClient": SOURCE,
        "countryRegion": notice.get("project_ctry_name") or "",
        "opportunityType": notice.get("procurement_method_name") or NOTICE_TYPE,
        "status": notice.get("notice_status") or "",
        "deadline": deadline_iso,
        "deadlineISO": deadline_iso,
        "url": NOTICE_URL.format(id=notice_id),
        "torText": notice_text[:TOR_TEXT_CHARS],
        "resourceLinks": "",
        "language": infer_language(title),
        "duplicateKey": duplicate_key_for(notice_id),
    }


def fetch_opportunities(
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Firm-level consulting REOIs for Latin America & the Caribbean, noticed in
    the last `days_back` days, not already in the sheet (`skip_keys`), with a
    deadline that hasn't passed. Returns [] if the API stops honoring filters.
    """
    skip_keys = skip_keys or set()
    cap = min(limit, MAX_NEW_ROWS) if limit is not None else MAX_NEW_ROWS
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    counts = {"seen": 0, "individual": 0, "not_lac": 0, "past_deadline": 0, "duplicate": 0}
    results = []

    for page in range(MAX_PAGES):
        try:
            notices = _get_page(page * PAGE_SIZE)
        except Exception as exc:
            logger.error("World Bank API error (offset=%d): %s", page * PAGE_SIZE, exc)
            break
        if not notices:
            break

        reached_cutoff = False
        for notice in notices:
            counts["seen"] += 1
            noticed = _notice_date(notice)
            if (notice.get("procurement_group") != PROCUREMENT_GROUP
                    or notice.get("notice_type") != NOTICE_TYPE or noticed is None):
                logger.error(
                    "World Bank: API returned a notice outside the requested filters "
                    "(id=%s group=%s type=%s date=%s) -- filters no longer honored; "
                    "writing nothing this run.",
                    notice.get("id"), notice.get("procurement_group"),
                    notice.get("notice_type"), notice.get("noticedate"),
                )
                return []
            if noticed < cutoff:
                reached_cutoff = True
                break
            if notice.get("procurement_method_code") == INDIVIDUAL_METHOD:
                counts["individual"] += 1
                continue
            if not is_lac(notice.get("project_ctry_name", "")):
                counts["not_lac"] += 1
                continue
            deadline = _deadline_iso(notice)
            if deadline and deadline < today.isoformat():
                counts["past_deadline"] += 1
                continue
            if duplicate_key_for(notice.get("id", "")) in skip_keys:
                counts["duplicate"] += 1
                continue

            results.append(_to_pipeline_row(notice))
            if len(results) >= cap:
                if limit is None:
                    logger.warning("World Bank: hit MAX_NEW_ROWS=%d -- stopping early", MAX_NEW_ROWS)
                break

        if reached_cutoff or len(results) >= cap:
            break
    else:
        logger.warning("World Bank: read MAX_PAGES=%d without reaching the %d-day cutoff", MAX_PAGES, days_back)

    logger.info(
        "World Bank: %d seen | %d new | %d individual | %d outside LAC | %d past deadline | %d already in sheet",
        counts["seen"], len(results), counts["individual"], counts["not_lac"],
        counts["past_deadline"], counts["duplicate"],
    )
    return results
