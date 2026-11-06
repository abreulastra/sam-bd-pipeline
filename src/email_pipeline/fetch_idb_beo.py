"""
Scraper for IDB BEO (Bank-Executed Operations) procurement opportunities.
Source: https://beo-procurement.iadb.org/en/home

The table is JavaScript-rendered and the ToR PDFs require session cookies
set by the BEO page. We use a single Playwright session to:
  1. Load the BEO page (establishes cookies)
  2. Scrape the opportunity table
  3. Download each ToR in the same session and extract its text

ToRs come as PDF or Word (.docx); the type is detected from the bytes, not
the header. Some sit behind an IDB/Microsoft login and can't be read
publicly; an occasional HTML page instead of the file is transient and is
retried once (both seen live 2026-11-06).

The extracted ToR text is stored in the `torText` column so the agent can
use it directly when scoring without needing to re-fetch.
"""
import html
import io
import re
import logging
import zipfile
import pdfplumber
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

BEO_URL = "https://beo-procurement.iadb.org/en/home"
DONOR = "Inter-American Development Bank"
MAX_PDF_CHARS = 8000
MAX_PAGES = 10  # safety cap; the table had 2 pages (27 rows) in Oct 2026

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


def _extract_docx_text(body: bytes, max_chars: int = MAX_PDF_CHARS) -> str:
    """Text of a Word .docx (a zip with word/document.xml), stdlib only."""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception as e:
        logger.warning("DOCX text extraction failed: %s", e)
        return ""
    paragraphs = []
    for para in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", para)
        if runs:
            paragraphs.append(html.unescape("".join(runs)))
    return "\n".join(p for p in paragraphs if p.strip())[:max_chars]


def _document_kind(body: bytes, content_type: str) -> str:
    """'pdf', 'docx', or '' -- by magic bytes first, header second."""
    if body[:5] == b"%PDF-" or "pdf" in content_type:
        return "pdf"
    if body[:2] == b"PK" and b"word/document.xml" in body[:4000]:
        return "docx"
    if "wordprocessingml" in content_type:
        return "docx"
    return ""


def _is_login_page(url: str) -> bool:
    return "login.microsoftonline.com" in url or "/oauth2/" in url


def _extract_pdf_text(body: bytes, max_chars: int = MAX_PDF_CHARS) -> str:
    """Extract text from PDF bytes using pdfplumber."""
    try:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            return "\n".join(pages_text)[:max_chars]
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        return ""


def _parse_rows(html: str) -> list[dict]:
    """Parse one rendered page of the BEO table into Pipeline-compatible dicts."""
    soup = BeautifulSoup(html, "html.parser")
    tor_links = soup.find_all("a", href=re.compile(r"document\.cfm\?id=", re.IGNORECASE))

    rows = []
    seen_ids = set()
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

        rows.append({
            "selectionId": selection_id,
            "opportunityTitle": title,
            "donorClient": DONOR,
            "countryRegion": country,
            "opportunityType": sub_sector,
            "deadline": deadline,
            "deadlineISO": deadline_iso,
            "url": tor_url,
            "torText": "",
        })
    return rows


_FIRST_ID_JS = (
    "() => { const a = document.querySelector(\"table tr a[href*='document.cfm']\");"
    " return a ? a.textContent.trim() : ''; }"
)


def _scrape_all_pages(page) -> list[dict]:
    """
    Parse page 1, then follow the pager's "Next" link until it disappears.

    The table is paginated (20 rows/page); reading only page 1 silently drops
    anything beyond the 20 newest if more than 20 are posted between runs.
    A failure on a later page keeps what was already collected.
    """
    rows = _parse_rows(page.content())
    if not rows:
        return []

    seen_ids = {r["selectionId"] for r in rows}
    for page_no in range(2, MAX_PAGES + 1):
        # The pager items are plain <li>s (no <a>); JS handles the click, and the
        # "next" item is removed from the DOM on the last page.
        next_link = page.query_selector("li.pager__item--next")
        if not next_link:
            break

        before = page.evaluate(_FIRST_ID_JS)
        try:
            next_link.click()
            page.wait_for_function(
                "before => { const a = document.querySelector(\"table tr a[href*='document.cfm']\");"
                " return a && a.textContent.trim() !== before; }",
                arg=before,
                timeout=30000,
            )
            page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning("IDB BEO: could not load page %d (%s) -- keeping pages read so far", page_no, e)
            break

        new_rows = [r for r in _parse_rows(page.content()) if r["selectionId"] not in seen_ids]
        if not new_rows:
            break
        seen_ids.update(r["selectionId"] for r in new_rows)
        rows.extend(new_rows)
        logger.info("IDB BEO: page %d added %d opportunities", page_no, len(new_rows))
    else:
        logger.warning("IDB BEO: hit the %d-page cap; more pages may exist", MAX_PAGES)

    return rows


def fetch_opportunities() -> list[dict]:
    """
    Scrape all active BEO procurement opportunities using a single Playwright
    session. Returns Pipeline-tab-compatible dicts with torText populated.
    """
    opportunities = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()

            # Step 1: Load BEO page -- establishes session cookies needed for PDF downloads.
            # "networkidle" never settled on the BEO page in a local run (60s timeout);
            # waiting for the table itself is what we actually need.
            page.goto(BEO_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("table tr a[href*='document.cfm']", timeout=45000)
            page.wait_for_timeout(2000)
            logger.info("IDB BEO: page loaded")

            # Step 2: Parse the table, across all pages
            rows = _scrape_all_pages(page)
            if not rows:
                logger.warning("IDB BEO: no ToR links found after JS render")
                browser.close()
                return []
            logger.info("IDB BEO: found %d opportunity links", len(rows))

            # Step 3: Download each ToR using the live session (cookies intact)
            for row in rows:
                try:
                    for attempt in (1, 2):
                        resp = page.request.get(row["url"])
                        body = resp.body()
                        ctype = resp.headers.get("content-type", "").lower()
                        kind = _document_kind(body, ctype) if resp.status == 200 else ""
                        if kind or _is_login_page(resp.url) or attempt == 2:
                            break
                        page.wait_for_timeout(2000)  # transient HTML page instead of the file

                    if kind:
                        text = (_extract_pdf_text if kind == "pdf" else _extract_docx_text)(body)
                        row["torText"] = text
                        logger.info("IDB BEO ToR read: %s (%s, %d chars)", row["selectionId"], kind, len(text))
                    elif _is_login_page(resp.url):
                        logger.info("IDB BEO ToR not public (needs IDB login): %s", row["selectionId"])
                    else:
                        logger.warning(
                            "IDB BEO ToR fetch failed: %s — status %d | type %r | url %s | %d bytes | starts %r",
                            row["selectionId"], resp.status, ctype, resp.url[:120], len(body), body[:80],
                        )
                except Exception as e:
                    logger.warning("IDB BEO ToR error for %s: %s", row["selectionId"], e)

            browser.close()
            opportunities = rows

    except Exception as e:
        logger.error("IDB BEO: Playwright session failed: %s", e)

    logger.info("IDB BEO: parsed %d opportunities", len(opportunities))
    return opportunities
