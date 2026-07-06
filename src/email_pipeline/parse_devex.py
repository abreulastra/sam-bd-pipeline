"""
Parser for Devex alert emails from alerts@devex.com.
Extracts opportunity titles and URLs from the HTML body.
"""
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Devex links to skip — navigation, social, unsubscribe, etc.
_SKIP_HREF_PATTERNS = re.compile(
    r"(devex\.com/(home|jobs|news|people|organizations|funding|pro|user|account|unsubscribe|settings)|"
    r"linkedin\.com|twitter\.com|facebook\.com|instagram\.com|"
    r"mailto:|#|javascript:|"
    r"devex\.com/?$)",
    re.IGNORECASE,
)

# Devex opportunity links typically contain /opportunity/ or /funding/
_OPP_HREF_PATTERN = re.compile(
    r"devex\.com/(en/)?(opportunity|funding|news|contract|tenders|grants)/",
    re.IGNORECASE,
)

# Minimum title length to be considered an opportunity
_MIN_TITLE_LEN = 15


def parse_alert_name(subject: str) -> str:
    match = re.search(r"Business Alert[:\s]+(.+?)$", subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_opportunities(html: str, subject: str) -> list[dict]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    alert_name = parse_alert_name(subject)
    results = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        if _SKIP_HREF_PATTERNS.search(href):
            continue

        # Accept if it matches the opportunity pattern OR is a long external link
        # near opportunity content
        is_opp_link = _OPP_HREF_PATTERN.search(href)
        if not is_opp_link:
            continue

        title = _extract_title(a)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue

        # Clean tracking parameters
        clean_url = _clean_url(href)

        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        donor = _extract_nearby_donor(a)
        country = _extract_nearby_country(a)
        deadline = _extract_nearby_deadline(a)

        results.append({
            "opportunityTitle": title,
            "url": clean_url,
            "donorClient": donor,
            "countryRegion": country,
            "deadline": deadline,
            "opportunityType": "Tenders & Grants",
            "status": "",
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


def _extract_nearby_donor(a_tag) -> str:
    # Look in parent or sibling elements for donor/client label patterns
    parent = a_tag.find_parent()
    if parent is None:
        return ""
    for candidate in [parent, parent.find_parent()]:
        if candidate is None:
            continue
        text = candidate.get_text(separator=" ", strip=True)
        match = re.search(
            r"(?:donor|client|funder|funded by|issued by)[:\s]+([^\n|•]+)",
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:100]
    return ""


def _extract_nearby_country(a_tag) -> str:
    parent = a_tag.find_parent()
    if parent is None:
        return ""
    for candidate in [parent, parent.find_parent()]:
        if candidate is None:
            continue
        text = candidate.get_text(separator=" ", strip=True)
        match = re.search(
            r"(?:country|location|region)[:\s]+([^\n|•]+)",
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:100]
    return ""


def _extract_nearby_deadline(a_tag) -> str:
    parent = a_tag.find_parent()
    if parent is None:
        return ""
    for candidate in [parent, parent.find_parent()]:
        if candidate is None:
            continue
        text = candidate.get_text(separator=" ", strip=True)
        match = re.search(
            r"(?:deadline|closing date|due date|close)[:\s]+([^\n|•]+)",
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:60]
    return ""


def _clean_url(url: str) -> str:
    # Remove common tracking params
    url = re.sub(r"[?&](utm_[^&]+|ref=[^&]+|source=[^&]+)", "", url)
    url = url.rstrip("?&")
    return url
