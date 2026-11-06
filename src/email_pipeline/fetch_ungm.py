"""
UN Global Marketplace (UNGM) procurement notices -> Pipeline tab.

Public search, no account: https://www.ungm.org/Public/Notice

How it works (verified live 2026-10-05):
  - GET /Public/Notice sets a session cookie and carries an ASP.NET
    anti-forgery token in a hidden __RequestVerificationToken input. The
    search rejects requests without both (HTTP 400).
  - POST /Public/Notice/Search takes a JSON body and returns HTML table rows
    (not JSON). Headers must include RequestVerificationToken, Origin and
    X-Requested-With.
  - PageSize is fixed at 15 -- the server 400s on any other value -- so
    results are paged. Sorting by DatePublished descending works, as does the
    PublishedFrom window and the Countries filter (ids from the search form's
    selNoticeCountry list, captured in COUNTRY_IDS below).
  - The notice detail page /Public/Notice/{id} is public and carries the
    description, which becomes torText.

Volume with the LAC + USA country filter: ~117 notices per 7 days (~17/day,
8 pages). Caps and a tripwire are kept for the same reason as the World Bank
fetcher: a silent markup or filter change must never flood the sheet.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta
from html import unescape

import requests
from bs4 import BeautifulSoup

from email_pipeline.normalize import infer_language, make_duplicate_key

logger = logging.getLogger(__name__)

SOURCE = "UNGM"
BASE = "https://www.ungm.org"
SEARCH_URL = f"{BASE}/Public/Notice/Search"
NOTICE_PAGE = f"{BASE}/Public/Notice"
NOTICE_URL = f"{BASE}/Public/Notice/{{id}}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

PAGE_SIZE = 15        # server-enforced; any other value returns HTTP 400
MAX_PAGES = 25        # ~375 notices, well past a week of LAC+USA volume
MAX_NEW_ROWS = 60     # more than this in one run means something is wrong
REQUEST_TIMEOUT = 45
MIN_REQUEST_INTERVAL = 1.0
TOR_TEXT_CHARS = 1500

# "Call for individual consultant" notices are jobs for a person, not firm
# contracts -- the same reason the World Bank fetcher drops INDV selections.
SKIP_NOTICE_TYPE_RE = re.compile(r"individual consultant|individual contractor", re.I)

# Beneficiary country/territory ids from the search form (selNoticeCountry),
# captured 2026-10-05: Latin America, the Caribbean and the USA.
COUNTRY_IDS = [
    2299, 2300, 2301, 2303, 2307, 2310, 2313, 2315, 2317, 2320, 2330, 2333,
    2336, 2340, 2343, 2349, 2350, 2352, 2354, 2374, 2377, 2380, 2381, 2382,
    2393, 2425, 2430, 2441, 2449, 2451, 2452, 2457, 2464, 2465, 2467, 2485,
    2497, 2501, 2508, 2509, 2512, 2514, 2515,
]

# Second pass (2026-11): global notices. The country filter can't target
# them -- "Multiple destinations" is a label on results, not a filter option --
# so this pass searches with NO country filter, restricted to four UNSPSC
# service segments (UNGM tree node ids from /UNSPSC/Children/109), and keeps
# only rows whose country is "Multiple destinations" or blank. Measured live
# 2026-11-06: ~350 notices/7 days in these segments (~24 pages), ~23 of them
# multi-country, ~15 after dropping individual-consultant calls. A server-side
# *category* filter like the World Bank fetcher's procurement_group=CS, not a
# keyword filter.
GLOBAL_SERVICE_UNSPSCS = [
    107363,  # 80000000 Management and Business Professionals and Administrative Services
    107371,  # 81000000 Engineering and Research and Technology Based Services
    107413,  # 86000000 Education and Training Services
    107433,  # 93000000 Politics and Civic Affairs Services
]
MAX_GLOBAL_PAGES = 40
# The global pass adds ~25 search requests to the ~10 of the LAC pass. At the
# 1s base interval a live test got a 429 on page 14 (2026-11-06), so global
# pages wait this much longer. A 429 still only ends the global pass.
GLOBAL_EXTRA_DELAY = 1.5
# The pipeline runs daily, so the global pass doesn't need the full `--days`
# window: 3 days (~150 notices, ~10 pages instead of ~24) still covers one
# missed run. A repeat test from a busy IP got a 429 on page 15 even at 2.5s.
GLOBAL_DAYS_BACK = 3
MULTI_COUNTRY_RE = re.compile(r"multiple destinations|^\s*$", re.I)

_next_allowed = 0.0


def _throttle():
    global _next_allowed
    wait = _next_allowed - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _next_allowed = time.monotonic() + MIN_REQUEST_INTERVAL


def _open_session() -> tuple[requests.Session, str]:
    """Load the public notice page for its cookie + anti-forgery token."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    _throttle()
    resp = session.get(NOTICE_PAGE, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    match = re.search(
        r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', resp.text
    )
    if not match:
        raise RuntimeError("UNGM: no __RequestVerificationToken on /Public/Notice")
    return session, match.group(1)


def _search(session: requests.Session, token: str, page: int, published_from: str,
            countries: list, unspscs: list) -> str:
    _throttle()
    payload = {
        "PageIndex": page,
        "PageSize": PAGE_SIZE,
        "Title": "", "Description": "", "Reference": "",
        "PublishedFrom": published_from, "PublishedTo": "",
        "DeadlineFrom": "", "DeadlineTo": "",
        "Countries": countries, "Agencies": [], "UNSPSCs": unspscs, "NoticeTypes": [],
        "SortField": "DatePublished", "SortAscending": False,
        "isPicker": False, "IsSustainable": False, "IsActive": True,
        "NoticeDisplayType": None, "NoticeSearchTotalLabelId": "noticeSearchTotal",
        "TypeOfCompetitions": [],
    }
    resp = session.post(
        SEARCH_URL,
        json=payload,
        timeout=REQUEST_TIMEOUT,
        headers={
            "RequestVerificationToken": token,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE,
            "Referer": NOTICE_PAGE,
        },
    )
    resp.raise_for_status()
    return resp.text


def _search_page(session: requests.Session, token: str, page: int, published_from: str) -> str:
    """Pass 1: LAC + USA beneficiary countries, every category."""
    return _search(session, token, page, published_from, COUNTRY_IDS, [])


def _search_global_page(session: requests.Session, token: str, page: int, published_from: str) -> str:
    """Pass 2: no country filter, service segments only (see GLOBAL_SERVICE_UNSPSCS)."""
    time.sleep(GLOBAL_EXTRA_DELAY)
    return _search(session, token, page, published_from, [], GLOBAL_SERVICE_UNSPSCS)


def _parse_rows(html: str) -> list[dict]:
    """
    Pull notices out of the search response. Cell order (verified live):
    deadline, published date, agency, notice type, reference, country.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for row in soup.select("div.tableRow.dataRow"):
        notice_id = (row.get("data-noticeid") or "").strip()
        title_el = row.select_one(".ungm-title")
        cells = [c.get_text(" ", strip=True) for c in row.select("div.tableCell")]
        values = [c for c in cells[1:] if c]  # cells[0] holds the action buttons
        if not notice_id or not title_el or len(values) < 6:
            continue
        rows.append({
            "id": notice_id,
            "title": unescape(title_el.get_text(" ", strip=True)),
            "deadline": values[1],
            "published": values[2],
            "agency": values[3],
            "noticeType": values[4],
            "reference": values[5],
            "country": values[6] if len(values) > 6 else "",
        })
    return rows


def _parse_date(value: str) -> date | None:
    """UNGM renders dates as '05-Oct-2026', deadlines as '26-Oct-2026 10:00 (GMT -5.00)'."""
    match = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})", value or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d-%b-%Y").date()
    except ValueError:
        return None


def duplicate_key_for(notice_id) -> str:
    return make_duplicate_key(SOURCE, "", "", "", stable_id=f"ungm:{notice_id}")


def _fetch_description(session: requests.Session, notice_id: str) -> str:
    """Opening text of the public notice page -- becomes torText."""
    try:
        _throttle()
        resp = session.get(NOTICE_URL.format(id=notice_id), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        marker = text.find("Description")
        if marker != -1:
            text = text[marker + len("Description"):].strip()
        return text[:TOR_TEXT_CHARS]
    except Exception as exc:
        logger.warning("UNGM: could not read notice %s: %s", notice_id, exc)
        return ""


def _to_pipeline_row(notice: dict, description: str) -> dict:
    deadline = _parse_date(notice["deadline"])
    deadline_iso = deadline.isoformat() if deadline else ""
    title = notice["title"]
    return {
        "source": SOURCE,
        "alertName": f"UNGM ({notice['agency']})" if notice["agency"] else "UNGM",
        "opportunityTitle": title,
        "donorClient": notice["agency"],
        "countryRegion": notice["country"],
        "opportunityType": notice["noticeType"],
        "status": notice["reference"],
        "deadline": notice["deadline"],
        "deadlineISO": deadline_iso,
        "url": NOTICE_URL.format(id=notice["id"]),
        "torText": description,
        "resourceLinks": "",
        "language": infer_language(title),
        "duplicateKey": duplicate_key_for(notice["id"]),
    }


def _scan(search_page, session, token, cutoff, today, max_pages, keep, skip_keys,
          results, cap, counts, fetch_descriptions, label) -> bool:
    """
    Page through one search until the date cutoff, a short page or max_pages,
    appending kept notices to `results`. Returns False if the first page had no
    parseable rows (markup or search contract changed).
    """
    for page in range(max_pages):
        try:
            html = search_page(session, token, page, cutoff.strftime("%d-%b-%Y"))
        except Exception as exc:
            logger.error("UNGM %s search failed (page=%d): %s", label, page, exc)
            return True

        notices = _parse_rows(html)
        if not notices:
            return page != 0

        reached_cutoff = False
        for notice in notices:
            counts["seen"] += 1
            published = _parse_date(notice["published"])
            if published and published < cutoff:
                reached_cutoff = True
                break
            if not keep(notice):
                counts["not_multi_country"] += 1
                continue
            if SKIP_NOTICE_TYPE_RE.search(notice["noticeType"]):
                counts["individual"] += 1
                continue
            deadline = _parse_date(notice["deadline"])
            if deadline and deadline < today:
                counts["past_deadline"] += 1
                continue
            key = duplicate_key_for(notice["id"])
            if key in skip_keys:
                counts["duplicate"] += 1
                continue

            description = _fetch_description(session, notice["id"]) if fetch_descriptions else ""
            results.append(_to_pipeline_row(notice, description))
            skip_keys.add(key)
            if len(results) >= cap:
                if cap == MAX_NEW_ROWS:
                    logger.warning("UNGM: hit MAX_NEW_ROWS=%d -- stopping early", MAX_NEW_ROWS)
                return True

        if reached_cutoff or len(notices) < PAGE_SIZE:
            return True
    logger.warning("UNGM %s: read %d pages without reaching the cutoff", label, max_pages)
    return True


def fetch_opportunities(
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
    fetch_descriptions: bool = True,
) -> list[dict]:
    """
    UNGM notices published in the last `days_back` days, excluding
    individual-consultant calls and anything already in the sheet
    (`skip_keys`):
      1. beneficiary country in Latin America, the Caribbean or the USA;
      2. "Multiple destinations" (global) notices in the consulting, research,
         training and civic-affairs service segments.
    Returns [] if the main search stops returning parseable rows (markup or
    filters changed); a broken second pass only skips the global notices.
    """
    skip_keys = set(skip_keys or ())
    cap = min(limit, MAX_NEW_ROWS) if limit is not None else MAX_NEW_ROWS
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    results = []

    try:
        session, token = _open_session()
    except Exception as exc:
        logger.error("UNGM: could not start a search session: %s", exc)
        return []

    lac = {"seen": 0, "individual": 0, "past_deadline": 0, "duplicate": 0, "not_multi_country": 0}
    ok = _scan(_search_page, session, token, cutoff, today, MAX_PAGES, lambda n: True, skip_keys,
               results, cap, lac, fetch_descriptions, "LAC+USA")
    if not ok:
        logger.error(
            "UNGM: search returned no parseable rows on the first page -- "
            "the markup or the search contract changed; writing nothing this run."
        )
        return []
    n_lac = len(results)
    logger.info(
        "UNGM LAC+USA: %d seen | %d new | %d individual-consultant | %d past deadline | %d already in sheet",
        lac["seen"], n_lac, lac["individual"], lac["past_deadline"], lac["duplicate"],
    )

    if len(results) < cap:
        glob = {"seen": 0, "individual": 0, "past_deadline": 0, "duplicate": 0, "not_multi_country": 0}
        global_cutoff = today - timedelta(days=min(days_back, GLOBAL_DAYS_BACK))
        ok = _scan(_search_global_page, session, token, global_cutoff, today, MAX_GLOBAL_PAGES,
                   lambda n: bool(MULTI_COUNTRY_RE.search(n["country"] or "")), skip_keys,
                   results, cap, glob, fetch_descriptions, "global")
        if not ok:
            logger.warning("UNGM global: first page unparseable -- skipping global notices this run")
        logger.info(
            "UNGM global (services, multiple destinations): %d seen | %d new | %d single-country | "
            "%d individual-consultant | %d past deadline | %d already in sheet",
            glob["seen"], len(results) - n_lac, glob["not_multi_country"], glob["individual"],
            glob["past_deadline"], glob["duplicate"],
        )
    return results
