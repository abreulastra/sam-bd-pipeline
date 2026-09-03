"""
DevelopmentAid External API client for tenders and grants.
Docs: https://www.developmentaid.org/api/external
Auth: X-API-KEY header.

Replaces the old email-based DevelopmentAid ingestion (parse_developmentaid.py,
now unused) -- the API gives structured donor/country/deadline/description
directly instead of guessing at inconsistent alert-email HTML layouts, plus a
lastModifiedDate the emails never had.

Filtered to Latin America & the Caribbean (region id 4) + Mexico (country id
103, a subset of 4 but kept explicit for parity with the old "Mexico
Contracts" saved search) and statuses forecast/open (2, 3) -- matching the
scope of what previously arrived via the DevelopmentAid alert emails.

Each matching tender/grant's attachments are downloaded and text-extracted
(same approach as fetch_idb_beo.py), then reduced to a short heuristic
excerpt (utils.extract_excerpt -- no LLM call) around the first scope-
defining heading found, so torText gives sam-bd-agent's scoring more signal
than just the title/description without dumping raw multi-page PDF text
into the sheet. Deliberately not used to filter anything out here; that
judgment stays in sam-bd-agent.
"""
import io
import logging
import random
import time
from datetime import UTC, datetime, timedelta

import pdfplumber
import requests

from utils import extract_excerpt

logger = logging.getLogger(__name__)

API_BASE = "https://www.developmentaid.org/api/external"

# Latin America & the Caribbean (region) + Mexico (country) -- matches the
# "LAC Contracts" / "LAC Grants" / "Mexico Contracts" saved searches this
# replaces.
LOCATIONS = [4, 103]

# forecast, open -- excludes closed/awarded/cancelled/shortlisted
STATUSES = [2, 3]

PAGE_SIZE = 100
MAX_DOCS_PER_ITEM = 5
MAX_PDF_CHARS = 8000  # raw-extraction search space, before excerpting
EXCERPT_CHARS_PER_DOC = 800
COMBINED_EXCERPT_MAX_CHARS = 1500


def _request(method, path, api_key, json_body=None, retries=3):
    url = f"{API_BASE}{path}"
    headers = {"X-API-KEY": api_key}
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, headers=headers, json=json_body, timeout=45)
            if r.status_code == 401:
                raise RuntimeError(f"DevelopmentAid API authentication failed (401): {r.text[:300]}")
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(exc.response, "status_code", None) if getattr(exc, "response", None) is not None else None
            if status == 401:
                raise
            time.sleep((2 ** attempt) + random.random())
    raise last_exc


def _search(kind, api_key, posted_from, posted_till):
    """kind: 'tenders' or 'grants'. Paginates through all matching results."""
    items = []
    page = 1
    while True:
        body = {
            "sort": "posted_date.desc",
            "page": page,
            "size": PAGE_SIZE,
            "filter": {
                "locations": LOCATIONS,
                "statuses": STATUSES,
                "postedFrom": posted_from,
                "postedTill": posted_till,
            },
        }
        data = _request("POST", f"/{kind}/search", api_key, json_body=body).json()
        batch = data.get("items", []) or []
        items.extend(batch)
        total = data.get("total", 0)
        if not batch or len(items) >= total:
            break
        page += 1
        time.sleep(0.35)
    return items


def _fetch_detail(kind, api_key, item_id):
    return _request("GET", f"/{kind}/{item_id}", api_key).json()


def _extract_pdf_text(body: bytes, max_chars: int = MAX_PDF_CHARS) -> str:
    try:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            return "\n".join(pages_text)[:max_chars]
    except Exception as e:
        logger.warning("DevelopmentAid: PDF text extraction failed: %s", e)
        return ""


def _fetch_documents_text(kind, api_key, item_id, documents) -> tuple[str, list[str]]:
    """
    Downloads each attachment, extracts its text, and reduces it to a short
    heuristic excerpt (extract_excerpt) rather than storing the raw dump --
    keeps the sheet light and on-topic without an LLM call. Returns
    (combined excerpt, resource URLs).
    """
    excerpts = []
    urls = []
    for doc in (documents or [])[:MAX_DOCS_PER_ITEM]:
        doc_id = doc.get("id")
        if not doc_id:
            continue
        doc_url = f"{API_BASE}/{kind}/{item_id}/documents/{doc_id}"
        urls.append(doc_url)
        try:
            resp = _request("GET", f"/{kind}/{item_id}/documents/{doc_id}", api_key)
            content_type = resp.headers.get("content-type", "").lower()
            if "pdf" in content_type:
                text = _extract_pdf_text(resp.content)
                excerpt = extract_excerpt(text, max_chars=EXCERPT_CHARS_PER_DOC)
                if excerpt:
                    excerpts.append(excerpt)
            else:
                logger.debug("DevelopmentAid: skipping non-PDF document %s (%s)", doc_id, content_type)
        except Exception as e:
            logger.warning("DevelopmentAid: failed to fetch document %s for %s %s: %s", doc_id, kind, item_id, e)
    return "\n\n---\n\n".join(excerpts)[:COMBINED_EXCERPT_MAX_CHARS], urls


def _parse_deadline_iso(deadline: str) -> str:
    if not deadline:
        return ""
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(deadline)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    return ""


def _to_pipeline_row(kind, api_key, detail: dict) -> dict:
    item_id = detail["id"]
    donors = detail.get("donors") or []
    donor = ", ".join(d.get("name", "") for d in donors if d.get("name"))

    # locations is a list of location objects ({id, name, abbreviation,
    # regionName, level}), not plain strings -- the API's own Swagger
    # example uses a "string" placeholder that doesn't reflect this.
    locations = detail.get("locations") or []
    country = ", ".join(
        loc.get("name", "") for loc in locations if isinstance(loc, dict) and loc.get("name")
    )

    deadline = detail.get("deadline") or ""
    tor_text, doc_urls = _fetch_documents_text(kind, api_key, item_id, detail.get("documents"))

    singular = "tender" if kind == "tenders" else "grant"

    return {
        "opportunityTitle": detail.get("name", ""),
        "donorClient": donor,
        "countryRegion": country,
        "opportunityType": "Tender" if kind == "tenders" else "Grant",
        "status": (detail.get("status") or "").capitalize(),
        "deadline": deadline,
        "deadlineISO": _parse_deadline_iso(deadline),
        "url": detail.get("url") or "",
        "resourceLinks": "|".join(doc_urls),
        "torText": tor_text,
        "stableId": f"developmentaid-api:{singular}:{item_id}",
    }


def fetch_opportunities(api_key: str, days_back: int = 7) -> list[dict]:
    """
    Fetch tenders + grants from DevelopmentAid's API for Latin America & the
    Caribbean + Mexico, posted in the last `days_back` days. Returns
    Pipeline-tab-compatible dicts (plus a "stableId" key the caller uses to
    build the duplicateKey).
    """
    posted_from = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    posted_till = datetime.now(UTC).strftime("%Y-%m-%d")

    results = []
    for kind in ("tenders", "grants"):
        try:
            items = _search(kind, api_key, posted_from, posted_till)
        except Exception as e:
            logger.error("DevelopmentAid %s search failed: %s", kind, e)
            continue

        logger.info("DevelopmentAid: %d %s matched (LAC + Mexico, open/forecast)", len(items), kind)

        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            try:
                detail = _fetch_detail(kind, api_key, item_id)
            except Exception as e:
                logger.warning("DevelopmentAid: failed to fetch %s %s detail: %s", kind, item_id, e)
                continue
            results.append(_to_pipeline_row(kind, api_key, detail))
            time.sleep(0.2)

    logger.info("DevelopmentAid API: %d opportunities fetched", len(results))
    return results
