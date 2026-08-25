"""
Scraper for IDB BEO (Bank-Executed Operations) procurement opportunities.
Source: https://beo-procurement.iadb.org/en/home

The table is JavaScript-rendered, so we use Playwright to load the page.
Each Selection # link points directly to a Terms of Reference PDF on iadb.org
(no login required). The agent reads that PDF when scoring.
"""
import re
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

BEO_URL = "https://beo-procurement.iadb.org/en/home"
DONOR = "Inter-American Development Bank"

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
    """Extract sub-sector and country from pipe-separated row text."""
    sub_sector = ""
    country = ""
    m = re.search(r"Sub-Sector:\|([^|]+)", row_text)
    if m:
        sub_sector = m.group(1).strip()
    m = re.search(r"Operation Country:\|([^|]+)", row_text)
    if m:
        country = m.group(1).strip()
    return sub_sector, country


def fetch_opportunities() -> list[dict]:
    """
    Scrape all active BEO procurement opportunities using Playwright.
    Returns Pipeline-tab-compatible dicts. url = ToR PDF link.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(BEO_URL, wait_until="networkidle", timeout=60000)
            # Wait for the table rows to appear
            page.wait_for_selector("table tr a[href*='document.cfm']", timeout=20000)
            html = page.content()
            browser.close()
    except Exception as e:
        logger.error("IDB BEO: Playwright fetch failed: %s", e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    tor_links = soup.find_all("a", href=re.compile(r"document\.cfm\?id=", re.IGNORECASE))

    if not tor_links:
        logger.warning("IDB BEO: no ToR links found after JS render")
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

        tr = link.find_parent("tr")
        if not tr:
            continue

        cells = tr.find_all("td", recursive=False)

        # Find which cell contains the ToR link, then take the next cells
        # for title and deadline (table may have a leading expand/checkbox column)
        link_cell_idx = next(
            (i for i, td in enumerate(cells) if td.find("a", href=re.compile(r"document\.cfm"))),
            0,
        )

        title = ""
        if link_cell_idx + 1 < len(cells):
            title = cells[link_cell_idx + 1].get_text(" ", strip=True)
            title = re.sub(r"^\s*UPDATE\s*", "", title, flags=re.IGNORECASE).strip()
        if not title:
            continue

        deadline_raw = cells[link_cell_idx + 2].get_text(strip=True) if link_cell_idx + 2 < len(cells) else ""
        deadline = _parse_deadline(deadline_raw)
        deadline_iso = _parse_deadline_iso(deadline)

        row_text = tr.get_text("|", strip=True)
        sub_sector, country = _extract_meta(row_text)

        if not sub_sector and not country:
            next_tr = tr.find_next_sibling("tr")
            if next_tr:
                sub_sector, country = _extract_meta(next_tr.get_text("|", strip=True))

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
