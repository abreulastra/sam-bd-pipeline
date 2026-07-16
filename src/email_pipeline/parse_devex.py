"""
Parser for Devex alert emails from alerts@devex.com.
Extracts opportunity titles and URLs from the HTML body.
"""
import logging
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Devex wraps links through track.pstmrk.it — we decode the embedded URL
_TRACKING_PATTERN = re.compile(r"track\.pstmrk\.it/\w+/([^?#\s]+)", re.IGNORECASE)

# After decoding, skip navigation/social/management links
_SKIP_DECODED_PATTERNS = re.compile(
    r"devex\.com/(home|jobs|news|people|organizations|pro|user|account|unsubscribe|settings)"
    r"|devex\.zendesk"
    r"|linkedin\.com|twitter\.com|facebook\.com|instagram\.com|servedbyadbutler"
    r"|mailto:|#|javascript:"
    r"|devex\.com/?\?access_key"
    r"|funding/r\?access_key"
    r"|filter%5Btype%5D|filter\[type\]",
    re.IGNORECASE,
)

# Skip generic labels
_GENERIC_TEXT = re.compile(
    r"^(read more|view|apply|details|more|learn more|click here|see all|"
    r"manage your alert|create new alert|funding search|here|www\.devex\.com)$",
    re.IGNORECASE,
)

# Minimum title length to be considered an opportunity
_MIN_TITLE_LEN = 15


def parse_alert_name(subject: str) -> str:
    match = re.search(r"Business Alert[:\s]+(.+?)$", subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _decode_tracking_url(href: str) -> str:
    """Decode a track.pstmrk.it tracking URL to get the real destination."""
    m = _TRACKING_PATTERN.search(href)
    if m:
        return unquote(m.group(1))
    return href


def parse_opportunities(html: str, subject: str) -> list[dict]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    alert_name = parse_alert_name(subject)
    results = []
    seen_titles = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        decoded = _decode_tracking_url(href)

        if _SKIP_DECODED_PATTERNS.search(decoded):
            continue

        title = _extract_title(a)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue

        if _GENERIC_TEXT.match(title):
            continue

        # The alert-name header link itself sometimes matches the title filters above
        if alert_name and title.strip().lower() == alert_name.strip().lower():
            continue

        # Deduplicate by title since all tracking URLs look similar
        title_key = title.lower().strip()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        title_tr = a.find_parent("tr")
        status = _extract_status(title_tr)
        donor, country, deadline = _extract_metadata_rows(title_tr)

        results.append({
            "opportunityTitle": title,
            "url": decoded,
            "donorClient": donor,
            "countryRegion": country,
            "deadline": deadline,
            "opportunityType": "Tenders & Grants",
            "status": status,
            "alertName": alert_name,
        })

    logger.debug("Devex: extracted %d opportunities from '%s'", len(results), subject)
    return results


def _extract_title(a_tag) -> str:
    text = a_tag.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    # Skip if it's a generic label
    if text.lower() in {"read more", "view", "apply", "details", "more", "learn more", "click here"}:
        return ""
    return text


def _extract_status(title_tr) -> str:
    """The status badge (OPEN/FORECAST/...) is the second <td> in the title's own row."""
    if title_tr is None:
        return ""
    tds = title_tr.find_all("td")
    if len(tds) >= 2:
        return tds[1].get_text(strip=True)
    return ""


def _extract_metadata_rows(title_tr) -> tuple[str, str, str]:
    """
    Devex lays out donor/country/deadline as three plain-text <tr> rows
    immediately following the title row (no field labels in the HTML).
    """
    if title_tr is None:
        return "", "", ""

    values = []
    sib = title_tr
    for _ in range(3):
        sib = sib.find_next_sibling("tr") if sib is not None else None
        values.append(sib.get_text(separator=" ", strip=True) if sib is not None else "")

    donor, country, deadline = values
    return donor, country, deadline


def _clean_url(url: str) -> str:
    # Remove common tracking params
    url = re.sub(r"[?&](utm_[^&]+|ref=[^&]+|source=[^&]+)", "", url)
    url = url.rstrip("?&")
    return url
