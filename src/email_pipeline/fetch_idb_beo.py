"""
Scraper for IDB BEO (Bank-Executed Operations) procurement opportunities.
Source: https://beo-procurement.iadb.org/en/home

Each row in the table links directly to a Terms of Reference PDF via the
Selection # link (document.cfm?id=...). No login is required to access them.
The page renders server-side HTML — no Playwright needed.
"""
import re
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BEO_URL = "https://beo-procurement.iadb.org/en/home"
SOURCE = "IDB BEO"
DONOR = "Inter-American Development Bank"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_DEADLINE_RE = re.compile(r"(\d{2}-\w{3}-\d{4})")


def _parse_deadline(text: str) -> str:
    m = _DEADLINE_RE.search(text)
    return m.group(1) if m else text.strip()


def _parse_deadline_iso(deadline: str) -> str:
    if not deadline:
        return ""
    try:
        from dateutil import parser as dp
        return dp.parse(deadline).date().isoformat()
    except Exception:
        return ""


def _extract_meta(row_text: str) -> tuple[str, str]:
    """Extract sub-sector and country from the full text of a table row."""
    sub_sector = ""
    country = ""

    # The row text (with | separators) looks like:
    # "...|Sub-Sector:|Gender Equality & Womens Empowerment|...|Operation Country:|Regional|..."
    m = re.search(r"Sub-Sector:\|([^|]+)", row_text)
    if m:
        sub_sector = m.group(1).strip()

    m = re.search(r"Operation Country:\|([^|]+)", row_text)
    if m:
        country = m.group(1).strip()

    return sub_sector, country


def fetch_opportunities() -> list[dict]:
    """
    Scrape all active BEO procurement opportunities and return them as
    Pipeline-tab-compatible dicts. Each opportunity's url points to the
    Terms of Reference PDF, which the agent will read when scoring.
    """
    try:
        resp = requests.get(BEO_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch IDB BEO page: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Anchor on Selection # links — each goes directly to the ToR PDF
    tor_links = soup.find_all("a", href=re.compile(r"document\.cfm\?id=", re.IGNORECASE))
    if not tor_links:
        logger.warning("IDB BEO: no ToR links found on page")
        return []

    logger.info("IDB BEO: found %d opportunity links", len(tor_links))

    seen_ids = set()
    opportunities = []

    for link in tor_links:
        selection_id = link.get_text(strip=True)
        if not selection_id or selection_id in seen_ids:
            continue
        seen_ids.add(selection_id)

        tor_url = link["href"]
        if not tor_url.startswith("http"):
            tor_url = "https://www.iadb.org" + tor_url

        # Walk up to the enclosing <tr>
        tr = link.find_parent("tr")
        if not tr:
            continue

        cells = tr.find_all("td", recursive=False)

        # Title: second <td>
        title = ""
        if len(cells) > 1:
            title = cells[1].get_text(" ", strip=True)
            title = re.sub(r"^\s*UPDATE\s*", "", title, flags=re.IGNORECASE).strip()

        if not title:
            continue

        # Deadline: third <td>
        deadline_raw = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        deadline = _parse_deadline(deadline_raw)
        deadline_iso = _parse_deadline_iso(deadline)

        # Sub-sector and country are in label elements within the row
        row_text = tr.get_text("|", strip=True)
        sub_sector, country = _extract_meta(row_text)

        # If sub-sector/country are in a sibling <tr> (some table layouts),
        # also check the immediately following sibling
        if not sub_sector and not country:
            next_tr = tr.find_next_sibling("tr")
            if next_tr:
                next_text = next_tr.get_text("|", strip=True)
                sub_sector, country = _extract_meta(next_text)

        opportunities.append({
            "selectionId": selection_id,
            "opportunityTitle": title,
            "donorClient": DONOR,
            "countryRegion": country,
            "opportunityType": sub_sector,
            "deadline": deadline,
            "deadlineISO": deadline_iso,
            "url": tor_url,
        })

    logger.info("IDB BEO: parsed %d opportunities", len(opportunities))
    return opportunities
