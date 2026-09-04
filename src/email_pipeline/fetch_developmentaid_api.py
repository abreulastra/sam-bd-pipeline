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

Rate limit: DevelopmentAid allows 20 requests per minute per key (confirmed
by their support, ops@developmentaid.org, 2026-09-04 -- it isn't in the
Swagger docs). Exceeding it returned 429s for hours, not just for the rest
of the minute, so this module (a) spaces every request >= 3s apart, (b) on
a 429 waits out a full window before retrying, (c) aborts the whole fetch
if it's still limited after that rather than hammering through hundreds of
items, and (d) skips items already in the sheet *before* spending detail +
attachment requests on them -- a 7-day window is ~300 items / ~900 requests
but only ~1/7 of it is new on any given day.
"""
import io
import logging
import random
import time
from datetime import UTC, datetime, timedelta

import pdfplumber
import requests

from email_pipeline.normalize import make_duplicate_key
from utils import extract_excerpt

logger = logging.getLogger(__name__)

API_BASE = "https://www.developmentaid.org/api/external"
SOURCE = "DevelopmentAid"

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

# 20 requests/minute -> one every 3s. Use 3.5s: exactly 3s sits on the
# limit with no headroom, and tripping it cost a >12h lockout, not just
# the rest of the minute. Enforced on every request (search pages, detail,
# each document), not just between items.
MIN_REQUEST_INTERVAL = 3.5
# On a 429, sit out a whole window before retrying.
RATE_LIMIT_COOLDOWN = 60

_next_allowed_time = 0.0


class RateLimited(RuntimeError):
    """DevelopmentAid kept returning 429 even after waiting out a full window."""


def _throttle():
    global _next_allowed_time
    wait = _next_allowed_time - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _next_allowed_time = time.monotonic() + MIN_REQUEST_INTERVAL


def _request(method, path, api_key, json_body=None, retries=3):
    url = f"{API_BASE}{path}"
    headers = {"X-API-KEY": api_key}
    last_exc = None
    for attempt in range(1, retries + 1):
        _throttle()
        try:
            r = requests.request(method, url, headers=headers, json=json_body, timeout=45)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep((2 ** attempt) + random.random())
            continue

        if r.status_code == 401:
            raise RuntimeError(f"DevelopmentAid API authentication failed (401): {r.text[:300]}")

        if r.status_code == 429:
            last_exc = RateLimited(f"429 on {method} {path}")
            if attempt < retries:
                logger.warning(
                    "DevelopmentAid: 429 on %s %s -- waiting %ds for the rate-limit window to reset",
                    method, path, RATE_LIMIT_COOLDOWN,
                )
                time.sleep(RATE_LIMIT_COOLDOWN + random.random())
            continue

        if r.status_code >= 500:
            last_exc = requests.HTTPError(f"{r.status_code} on {method} {path}", response=r)
            time.sleep((2 ** attempt) + random.random())
            continue

        r.raise_for_status()  # any other 4xx (e.g. 404 for a withdrawn tender): fail fast, no retry
        return r
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
    return items


def _fetch_detail(kind, api_key, item_id):
    return _request("GET", f"/{kind}/{item_id}", api_key).json()


def duplicate_key_for(kind: str, item_id) -> str:
    """The Pipeline-tab duplicateKey for a tender/grant, from its DevelopmentAid ID alone."""
    singular = "tender" if kind == "tenders" else "grant"
    return make_duplicate_key(SOURCE, "", "", "", stable_id=f"developmentaid-api:{singular}:{item_id}")


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
        except RateLimited:
            raise
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
        "duplicateKey": duplicate_key_for(kind, item_id),
    }


def fetch_opportunities(
    api_key: str,
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Fetch tenders + grants from DevelopmentAid's API for Latin America & the
    Caribbean + Mexico, posted in the last `days_back` days. Returns
    Pipeline-tab-compatible dicts including the duplicateKey.

    `skip_keys` -- duplicateKeys already in the Pipeline tab. Matching items
    are dropped after the (cheap, paginated) search and before the detail +
    attachment requests, which is where nearly all of the request budget goes.
    `limit` -- cap on how many *new* items to fully fetch; useful for a cheap
    live test against the 20 req/min limit.
    """
    skip_keys = skip_keys or set()
    posted_from = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    posted_till = datetime.now(UTC).strftime("%Y-%m-%d")

    candidates = []
    skipped = 0
    for kind in ("tenders", "grants"):
        try:
            items = _search(kind, api_key, posted_from, posted_till)
        except RateLimited as e:
            logger.error("DevelopmentAid: rate-limited during %s search (%s) -- giving up this run", kind, e)
            return []
        except Exception as e:
            logger.error("DevelopmentAid %s search failed: %s", kind, e)
            continue
        matched = 0
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            matched += 1
            if duplicate_key_for(kind, item_id) in skip_keys:
                skipped += 1
                continue
            candidates.append((kind, item_id))
        logger.info("DevelopmentAid: %d %s matched (LAC + Mexico, open/forecast)", matched, kind)

    logger.info(
        "DevelopmentAid: %d new to fetch, %d already in the sheet (skipped before any detail requests)",
        len(candidates), skipped,
    )
    if limit is not None:
        candidates = candidates[:limit]

    results = []
    for kind, item_id in candidates:
        try:
            detail = _fetch_detail(kind, api_key, item_id)
            results.append(_to_pipeline_row(kind, api_key, detail))
        except RateLimited as e:
            logger.error(
                "DevelopmentAid: still rate-limited after cooldown (%s) -- stopping with %d of %d fetched; "
                "the rest will be picked up on the next run",
                e, len(results), len(candidates),
            )
            break
        except Exception as e:
            logger.warning("DevelopmentAid: failed to fetch %s %s: %s", kind, item_id, e)

    logger.info("DevelopmentAid API: %d opportunities fetched", len(results))
    return results
