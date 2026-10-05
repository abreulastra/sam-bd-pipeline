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


def _search_page(session: requests.Session, token: str, page: int, published_from: str) -> str:
    _throttle()
    payload = {
        "PageIndex": page,
        "PageSize": PAGE_SIZE,
        "Title": "", "Description": "", "Reference": "",
        "PublishedFrom": published_from, "PublishedTo": "",
        "DeadlineFrom": "", "DeadlineTo": "",
        "Countries": COUNTRY_IDS, "Agencies": [], "UNSPSCs": [], "NoticeTypes": [],
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


def fetch_opportunities(
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
    fetch_descriptions: bool = True,
) -> list[dict]:
    """
    UNGM notices for Latin America, the Caribbean and the USA, published in
    the last `days_back` days, excluding individual-consultant calls and
    anything already in the sheet (`skip_keys`). Returns [] if the search
    stops returning parseable rows (markup or filters changed).
    """
    skip_keys = skip_keys or set()
    cap = min(limit, MAX_NEW_ROWS) if limit is not None else MAX_NEW_ROWS
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    counts = {"seen": 0, "individual": 0, "past_deadline": 0, "duplicate": 0, "too_old": 0}
    results = []

    try:
        session, token = _open_session()
    except Exception as exc:
        logger.error("UNGM: could not start a search session: %s", exc)
        return []

    for page in range(MAX_PAGES):
        try:
            html = _search_page(session, token, page, cutoff.strftime("%d-%b-%Y"))
        except Exception as exc:
            logger.error("UNGM search failed (page=%d): %s", page, exc)
            break

        notices = _parse_rows(html)
        if not notices:
            if page == 0:
                logger.error(
                    "UNGM: search returned no parseable rows on the first page -- "
                    "the markup or the search contract changed; writing nothing this run."
                )
                return []
            break

        reached_cutoff = False
        for notice in notices:
            counts["seen"] += 1
            published = _parse_date(notice["published"])
            if published and published < cutoff:
                reached_cutoff = True
                break
            if SKIP_NOTICE_TYPE_RE.search(notice["noticeType"]):
                counts["individual"] += 1
                continue
            deadline = _parse_date(notice["deadline"])
            if deadline and deadline < today:
                counts["past_deadline"] += 1
                continue
            if duplicate_key_for(notice["id"]) in skip_keys:
                counts["duplicate"] += 1
                continue

            description = _fetch_description(session, notice["id"]) if fetch_descriptions else ""
            results.append(_to_pipeline_row(notice, description))
            if len(results) >= cap:
                if limit is None:
                    logger.warning("UNGM: hit MAX_NEW_ROWS=%d -- stopping early", MAX_NEW_ROWS)
                break

        if reached_cutoff or len(results) >= cap or len(notices) < PAGE_SIZE:
            break
    else:
        logger.warning("UNGM: read MAX_PAGES=%d without reaching the %d-day cutoff", MAX_PAGES, days_back)

    logger.info(
        "UNGM: %d seen | %d new | %d individual-consultant | %d past deadline | %d already in sheet",
        counts["seen"], len(results), counts["individual"], counts["past_deadline"], counts["duplicate"],
    )
    return results
