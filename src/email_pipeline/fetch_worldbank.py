"""
World Bank Procurement API client.

Pulls consulting-services and non-consulting-services solicitations from the
World Bank's public procurement REST API (no auth required):

    https://search.worldbank.org/api/v2/procnotices

Filters applied here (coarse pass; fine-grained fit judgement stays in the
agent):
  - procurement_category: "Consulting Services" or "Non-Consulting Services"
    only -- skip "Goods" and "Works" (physical procurement / construction)
  - notice_type: exclude "Contract Award" -- we only want open solicitations
  - Published within the last `days_back` days
  - Deadline not already in the past

No rate-limit complications: the endpoint is open and not quota-restricted.
We paginate with up to `rows_per_page` records per request until we've
consumed the full result set or exhausted the lookback window.

Deduplication is handled by the caller (run_email_pipeline.py) via the
Pipeline tab's existing duplicateKey mechanism -- the same pattern used by
all other pipeline sources.
"""
import logging
import time
from datetime import UTC, date, datetime, timedelta

import requests

from email_pipeline.normalize import infer_language, make_duplicate_key, parse_deadline_iso

logger = logging.getLogger(__name__)

SOURCE = "World Bank"
API_BASE = "https://search.worldbank.org/api/v2/procnotices"

# Categories to include (exact WB API strings)
INCLUDE_CATEGORIES = {"Consulting Services", "Non-Consulting Services"}

# Notice types to exclude
EXCLUDE_NOTICE_TYPES = {"Contract Award"}

ROWS_PER_PAGE = 500
REQUEST_TIMEOUT = 30

# Light throttle -- the API is open, but be polite
MIN_REQUEST_INTERVAL = 0.5
_next_allowed = 0.0


def _throttle():
    global _next_allowed
    wait = _next_allowed - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _next_allowed = time.monotonic() + MIN_REQUEST_INTERVAL


def _get_page(params: dict) -> dict:
    _throttle()
    resp = requests.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _parse_date(raw: str) -> date | None:
    """Parse a WB API date string (YYYY-MM-DD or similar) to a date object."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _notice_url(notice: dict) -> str:
    """Construct the canonical URL for a WB procurement notice."""
    # The API sometimes returns a direct URL; fall back to the standard pattern.
    direct = notice.get("url") or notice.get("link") or ""
    if direct and direct.startswith("http"):
        return direct
    notice_id = notice.get("id", "")
    if notice_id:
        return f"https://projects.worldbank.org/en/projects-operations/procurement/buyer?id={notice_id}"
    return ""


def _normalize(notice: dict) -> dict:
    """Map a WB API notice dict to a Pipeline-tab-compatible row."""
    notice_id = str(notice.get("id", ""))
    title = notice.get("bid_description") or notice.get("project_name") or ""
    category = notice.get("procurement_category") or ""
    notice_type = notice.get("notice_type") or ""
    country = notice.get("project_ctry_name") or ""
    # srce field: "WB" = World Bank, "IFC" = IFC, may be absent
    srce = (notice.get("srce") or "").upper()
    donor = "IFC" if srce == "IFC" else "World Bank"
    deadline_raw = notice.get("deadline") or notice.get("deadline_strdate") or ""
    deadline_iso = parse_deadline_iso(deadline_raw) or (deadline_raw[:10] if len(deadline_raw) >= 10 else "")

    return {
        "source": SOURCE,
        "alertName": f"World Bank – {category}" if category else SOURCE,
        "opportunityTitle": title,
        "donorClient": donor,
        "countryRegion": country,
        "opportunityType": f"{category} / {notice_type}".strip(" /"),
        "status": "",
        "deadline": deadline_raw,
        "deadlineISO": deadline_iso,
        "url": _notice_url(notice),
        "torText": "",
        "resourceLinks": "",
        "language": infer_language(title),
        "duplicateKey": make_duplicate_key(
            SOURCE, "", "", "", stable_id=f"world-bank:{notice_id}"
        ),
    }


def fetch_opportunities(
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
    srce: str = "both",
    rows_per_page: int = ROWS_PER_PAGE,
) -> list[dict]:
    """
    Fetch World Bank procurement notices published in the last `days_back` days.
    Returns Pipeline-tab-compatible dicts (without the envelope fields like
    source/processedAtUTC/pipelineStatus -- the caller adds those).

    `skip_keys`  -- duplicateKeys already in the Pipeline tab (pre-dedup).
    `limit`      -- hard cap on results; useful for testing.
    `srce`       -- "both" (WB + IFC), "WB", or "IFC".
    """
    skip_keys = skip_keys or set()
    today = date.today()
    published_from = (today - timedelta(days=days_back)).isoformat()

    results = []
    total_seen = 0
    total_skipped_dup = 0
    total_skipped_past = 0
    total_skipped_category = 0

    for category in INCLUDE_CATEGORIES:
        offset = 0
        while True:
            params = {
                "format": "json",
                "rows": rows_per_page,
                "os": offset,
                "srce": srce,
                "procurement_category": category,
                "srt": "publ_date",
                "order": "desc",
            }
            try:
                data = _get_page(params)
            except Exception as exc:
                logger.error("World Bank API error (category=%s, offset=%d): %s", category, offset, exc)
                break

            notices = data.get("procnotices") or []
            if not notices:
                break

            for notice in notices:
                total_seen += 1

                # Stop paging once we're past the lookback window
                pub_date = _parse_date(notice.get("publ_date") or "")
                if pub_date and pub_date < today - timedelta(days=days_back):
                    notices = []  # signal to break outer loop
                    break

                # Exclude contract awards
                notice_type = (notice.get("notice_type") or "").strip()
                if notice_type in EXCLUDE_NOTICE_TYPES:
                    total_skipped_category += 1
                    continue

                # Skip past-deadline notices
                deadline_iso = parse_deadline_iso(
                    notice.get("deadline") or notice.get("deadline_strdate") or ""
                )
                if deadline_iso:
                    dl_date = _parse_date(deadline_iso)
                    if dl_date and dl_date < today:
                        total_skipped_past += 1
                        continue

                row = _normalize(notice)

                # Dedup
                if row["duplicateKey"] in skip_keys:
                    total_skipped_dup += 1
                    continue

                results.append(row)

                if limit is not None and len(results) >= limit:
                    logger.info("World Bank: hit limit of %d, stopping", limit)
                    break

            if not notices or (limit is not None and len(results) >= limit):
                break

            offset += rows_per_page

    logger.info(
        "World Bank: %d seen | %d new | %d duplicates | %d past deadline | %d excluded type",
        total_seen, len(results), total_skipped_dup, total_skipped_past, total_skipped_category,
    )
    return results
