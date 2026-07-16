"""
Parser for DevelopmentAid alert emails.
Extracts opportunities from the Open section of the HTML body.
"""
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Links to skip — navigation, social, account management, etc.
_SKIP_HREF_PATTERNS = re.compile(
    r"(developmentaid\.(org|info)/(login|register|account|settings|unsubscribe|about|contact|home|news|blog|organizations)|"
    r"linkedin\.com|twitter\.com|facebook\.com|instagram\.com|"
    r"mailto:|#|javascript:|"
    r"developmentaid\.(org|info)/?$)",
    re.IGNORECASE,
)

# DevelopmentAid opportunity links
_OPP_HREF_PATTERN = re.compile(
    r"developmentaid\.(org|info)/(tenders|grants|funding|opportunities|contracts|jobs)/",
    re.IGNORECASE,
)

_MIN_TITLE_LEN = 15


def parse_alert_name(subject: str) -> str:
    match = re.search(r"DevelopmentAid[:\s]+(.+?)$", subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_opportunity_type(html: str) -> str:
    if re.search(r"tender alert", html, re.IGNORECASE):
        return "Tender"
    if re.search(r"grant alert", html, re.IGNORECASE):
        return "Grant"
    return "Opportunity"


def parse_opportunities(html: str, subject: str) -> list[dict]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    alert_name = parse_alert_name(subject)
    opp_type = parse_opportunity_type(html)
    results = []
    seen_urls = set()

    # Try to find the "Open" section; only use it if it actually contains opp links
    open_section = _find_open_section(soup)
    if open_section and open_section.find("a", href=_OPP_HREF_PATTERN):
        search_root = open_section
    else:
        search_root = soup

    for a in search_root.find_all("a", href=True):
        href = a["href"].strip()

        if _SKIP_HREF_PATTERNS.search(href):
            continue

        is_opp_link = _OPP_HREF_PATTERN.search(href)
        if not is_opp_link:
            continue

        title = _extract_title(a)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue

        clean_url = _clean_url(href)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        fields = _extract_metadata_fields(a)
        donor = _lookup(fields, _DONOR_KEYS)
        country = _lookup(fields, _COUNTRY_KEYS)
        deadline = _lookup(fields, _DEADLINE_KEYS)
        deadline_iso = _parse_date_iso(deadline) if deadline else ""
        status = _extract_nearby_status(a)

        results.append({
            "opportunityTitle": title,
            "url": clean_url,
            "donorClient": donor,
            "countryRegion": country,
            "deadline": deadline,
            "deadlineISO": deadline_iso,
            "opportunityType": opp_type,
            "status": status,
            "alertName": alert_name,
        })

    logger.debug("DevelopmentAid: extracted %d opportunities from '%s'", len(results), subject)
    return results


def _find_open_section(soup: BeautifulSoup):
    """Find the container that holds the 'Open' opportunities section."""
    for tag in soup.find_all(string=re.compile(r"\bOpen\b", re.IGNORECASE)):
        parent = tag.find_parent()
        if parent:
            # Return the next sibling container or the parent itself
            next_sib = parent.find_next_sibling()
            if next_sib and next_sib.find("a", href=True):
                return next_sib
            grandparent = parent.find_parent()
            if grandparent:
                return grandparent
    return None


def _extract_title(a_tag) -> str:
    text = a_tag.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower() in {"read more", "view", "apply", "details", "more", "learn more", "click here", "ver más", "ver"}:
        return ""
    return text


_DONOR_KEYS = ("funding agency", "donor", "client", "organización", "financiado por")
_COUNTRY_KEYS = ("location", "country", "countries", "país", "países", "region", "región")
_DEADLINE_KEYS = ("deadline", "closing", "cierre", "fecha límite", "fecha limite")


def _extract_metadata_fields(a_tag) -> dict:
    """
    DevelopmentAid renders each opportunity as an outer <tr> with the title,
    followed by a sibling <tr> containing a label/value table
    (Funding agency, Location, Sectors, Budget, Deadline, ...).
    """
    outer_table = a_tag.find_parent("table")
    if outer_table is None:
        return {}
    outer_tr = outer_table.find_parent("tr")
    if outer_tr is None:
        return {}
    meta_tr = outer_tr.find_next_sibling("tr")
    if meta_tr is None:
        return {}

    fields = {}
    for row in meta_tr.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        label = tds[0].get_text(strip=True).rstrip(":").strip().lower()
        if label:
            fields[label] = tds[1].get_text(strip=True)
    return fields


def _lookup(fields: dict, keys: tuple) -> str:
    for key in keys:
        if key in fields:
            return fields[key][:100]
    return ""


def _extract_nearby_status(a_tag) -> str:
    for candidate in _nearby_containers(a_tag):
        text = candidate.get_text(separator=" ", strip=True)
        if re.search(r"\bopen\b", text, re.IGNORECASE):
            return "Open"
    return ""


def _nearby_containers(a_tag):
    parent = a_tag.find_parent()
    if parent:
        yield parent
        grandparent = parent.find_parent()
        if grandparent:
            yield grandparent


def _parse_date_iso(date_str: str) -> str:
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(date_str, dayfirst=True)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    return ""


def _clean_url(url: str) -> str:
    url = re.sub(r"[?&](utm_[^&]+|ref=[^&]+|source=[^&]+)", "", url)
    url = url.rstrip("?&")
    return url
