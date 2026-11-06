"""
Entry point for the email opportunity pipeline.

Sources:
  devex           Gmail alert emails from alerts@devex.com          [RETIRED]
  developmentaid  DevelopmentAid's external API directly (not email --
                  see fetch_developmentaid_api.py)                  [RETIRED]
  idbbeo          IDB BEO procurement web scrape
  worldbank       World Bank procurement API (LAC firm consulting REOIs)
  ungm            UN Global Marketplace public notice search (LAC + USA)
  cdmx            Mexico City procurement portal (consulting-type tenders only)

Usage:
    python -m src.email_pipeline.run_email_pipeline [--days 7] [--dry-run] [--limit N] [--source <name>|all]

`all` runs every source except RETIRED_SOURCES; a retired source still runs
when named explicitly (e.g. --source developmentaid), for tests or reactivation.
"""
import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# Allow relative imports when run as __main__
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from email_pipeline.fetch_developmentaid_api import fetch_opportunities as fetch_developmentaid_api
from email_pipeline.fetch_gmail import fetch_emails
from email_pipeline.fetch_cdmx import fetch_opportunities as fetch_cdmx
from email_pipeline.fetch_idb_beo import fetch_opportunities as fetch_idb_beo
from email_pipeline.fetch_ungm import fetch_opportunities as fetch_ungm
from email_pipeline.fetch_worldbank import fetch_opportunities as fetch_worldbank
from email_pipeline.normalize import (
    infer_language,
    make_duplicate_key,
    now_utc_iso,
    parse_deadline_iso,
)
from email_pipeline.parse_devex import parse_opportunities as parse_devex
from email_pipeline.write_pipeline_sheet import append_opportunities, load_pipeline_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Subscriptions cancelled (Raúl, 2026-11-06). Coverage analysis over
# 2026-10-07..11-06: ~1/3 of their LatAm high/medium items also arrived via
# IDB BEO / UNGM / World Bank / SAM.gov, and C230 had not acted on any of them.
# The code stays so a source can be re-enabled by deleting it from this set.
RETIRED_SOURCES = {"devex", "developmentaid"}


def source_wanted(requested: str, name: str) -> bool:
    """True if this run should fetch `name` (`all` skips retired sources)."""
    if requested == name:
        return True
    return requested == "all" and name not in RETIRED_SOURCES


def parse_args():
    p = argparse.ArgumentParser(description="Email opportunity ingestion pipeline")
    p.add_argument("--days", type=int, default=7, help="Days back to search (default: 7)")
    p.add_argument("--dry-run", action="store_true", help="Parse and print without writing to Sheets")
    p.add_argument("--limit", type=int, default=None, help="Max emails to process per source")
    p.add_argument(
        "--source",
        choices=["devex", "developmentaid", "idbbeo", "worldbank", "ungm", "cdmx", "all"],
        default="all",
        help="Filter by source (default: all)",
    )
    return p.parse_args()


def process_email(email: dict) -> list[dict]:
    source = email["source"]
    html = email["html_body"]
    subject = email["subject"]

    if source != "devex":
        return []
    raw_opps = parse_devex(html, subject)

    processed = now_utc_iso()
    results = []

    for opp in raw_opps:
        title = opp.get("opportunityTitle", "")
        donor = opp.get("donorClient", "")
        country = opp.get("countryRegion", "")
        deadline = opp.get("deadline", "")
        deadline_iso = opp.get("deadlineISO", "") or parse_deadline_iso(deadline)

        source_label = "Devex"

        results.append({
            "source": source_label,
            "emailDate": email["email_date"],
            "emailSubject": subject,
            "alertName": opp.get("alertName", ""),
            "opportunityTitle": title,
            "donorClient": donor,
            "countryRegion": country,
            "opportunityType": opp.get("opportunityType", ""),
            "status": opp.get("status", ""),
            "deadline": deadline,
            "deadlineISO": deadline_iso,
            "url": opp.get("url", ""),
            "language": infer_language(title),
            "fitScore": "",
            "fitLabel": "",
            "reviewSummary": "",
            "duplicateKey": make_duplicate_key(source_label, title, donor, country, url=opp.get("url", "")),
            "processedAtUTC": processed,
            "owner": "",
            "pipelineStatus": "New",
        })

    return results


def print_preview(opportunities: list[dict]):
    print(f"\n{'─'*100}")
    print(f"{'SOURCE':<15} {'DATE':<12} {'ALERT':<20} {'TITLE':<40} {'URL':<30}")
    print(f"{'─'*100}")
    for opp in opportunities[:30]:
        title = opp["opportunityTitle"][:38]
        url = opp["url"][:28]
        alert = opp["alertName"][:18]
        print(f"{opp['source']:<15} {opp['emailDate']:<12} {alert:<20} {title:<40} {url:<30}")
    if len(opportunities) > 30:
        print(f"... and {len(opportunities) - 30} more")
    print(f"{'─'*100}\n")


def main():
    load_dotenv()

    args = parse_args()
    account = os.environ.get("GMAIL_ACCOUNT_EMAIL", "")

    logger.info("Email pipeline starting")
    if source_wanted(args.source, "devex"):
        logger.info("Gmail account: %s", account)
    if args.source == "all" and RETIRED_SOURCES:
        logger.info("Retired sources (not fetched by `all`): %s", ", ".join(sorted(RETIRED_SOURCES)))
    logger.info("Days back: %d | Source: %s | Dry run: %s", args.days, args.source, args.dry_run)

    all_opportunities = []

    # ── Devex (Gmail alert emails) ───────────────────────────────────────────
    if source_wanted(args.source, "devex"):
        emails = fetch_emails(days=args.days, limit=args.limit, source_filter="devex")
        logger.info("Total emails fetched: %d", len(emails))
        for email in emails:
            opps = process_email(email)
            all_opportunities.extend(opps)

    # ── DevelopmentAid (external API) ────────────────────────────────────────
    if source_wanted(args.source, "developmentaid"):
        da_api_key = os.environ.get("DEVELOPMENTAID_API_KEY")
        if not da_api_key:
            logger.warning("DEVELOPMENTAID_API_KEY not set — skipping DevelopmentAid")
        else:
            # Check the sheet first: already-ingested items then cost no
            # detail/attachment requests, and rows written earlier today tell
            # us how much of DevelopmentAid's 100-tenders/24h quota is left.
            try:
                skip_keys, used_today = load_pipeline_state(
                    "DevelopmentAid", sheet_url=os.environ.get("SHEET_URL")
                )
            except Exception as e:
                logger.warning("Could not read Pipeline state (%s) -- fetching without a skip list", e)
                skip_keys, used_today = set(), 0
            logger.info("Fetching DevelopmentAid opportunities via API...")
            da_raw = fetch_developmentaid_api(
                api_key=da_api_key,
                days_back=args.days,
                skip_keys=skip_keys,
                limit=args.limit,
                budget_used_today=used_today,
            )
            processed = now_utc_iso()
            today = processed[:10]
            for opp in da_raw:
                title = opp["opportunityTitle"]
                donor = opp["donorClient"]
                country = opp["countryRegion"]
                all_opportunities.append({
                    "source": "DevelopmentAid",
                    "emailDate": today,
                    "emailSubject": "",
                    "alertName": f"DevelopmentAid API ({opp['opportunityType']})",
                    "opportunityTitle": title,
                    "donorClient": donor,
                    "countryRegion": country,
                    "opportunityType": opp.get("opportunityType", ""),
                    "status": opp.get("status", ""),
                    "deadline": opp.get("deadline", ""),
                    "deadlineISO": opp.get("deadlineISO", ""),
                    "url": opp.get("url", ""),
                    "torText": opp.get("torText", ""),
                    "resourceLinks": opp.get("resourceLinks", ""),
                    "language": infer_language(title),
                    "fitScore": "",
                    "fitLabel": "",
                    "reviewSummary": "",
                    "duplicateKey": opp["duplicateKey"],
                    "processedAtUTC": processed,
                    "owner": "",
                    "pipelineStatus": "New",
                })
            logger.info("DevelopmentAid opportunities extracted: %d", len(da_raw))

    # ── IDB BEO web scrape ───────────────────────────────────────────────────
    if source_wanted(args.source, "idbbeo"):
        logger.info("Fetching IDB BEO opportunities...")
        beo_raw = fetch_idb_beo()
        processed = now_utc_iso()
        today = processed[:10]
        for opp in beo_raw:
            title = opp["opportunityTitle"]
            donor = opp["donorClient"]
            country = opp["countryRegion"]
            deadline = opp["deadline"]
            deadline_iso = opp["deadlineISO"]
            all_opportunities.append({
                "source": "IDB BEO",
                "emailDate": today,
                "emailSubject": "",
                "alertName": "IDB BEO",
                "opportunityTitle": title,
                "donorClient": donor,
                "countryRegion": country,
                "opportunityType": opp.get("opportunityType", ""),
                "status": opp.get("selectionId", ""),
                "deadline": deadline,
                "deadlineISO": deadline_iso,
                "url": opp["url"],
                "torText": opp.get("torText", ""),
                "language": infer_language(title),
                "fitScore": "",
                "fitLabel": "",
                "reviewSummary": "",
                "duplicateKey": make_duplicate_key("IDB BEO", title, donor, country),
                "processedAtUTC": processed,
                "owner": "",
                "pipelineStatus": "New",
            })
        logger.info("IDB BEO opportunities extracted: %d", len(beo_raw))

    # ── World Bank Procurement API (LAC firm consulting notices) ─────────────
    if source_wanted(args.source, "worldbank"):
        logger.info("Fetching World Bank procurement opportunities...")
        try:
            skip_keys, _ = load_pipeline_state("World Bank", sheet_url=os.environ.get("SHEET_URL"))
        except Exception as e:
            logger.warning("Could not read Pipeline state (%s) -- fetching without a skip list", e)
            skip_keys = set()
        wb_raw = fetch_worldbank(
            days_back=args.days,
            skip_keys=skip_keys,
            limit=args.limit,
        )
        processed = now_utc_iso()
        today = processed[:10]
        for opp in wb_raw:
            all_opportunities.append({
                "source": "World Bank",
                "emailDate": today,
                "emailSubject": "",
                "alertName": opp["alertName"],
                "opportunityTitle": opp["opportunityTitle"],
                "donorClient": opp["donorClient"],
                "countryRegion": opp["countryRegion"],
                "opportunityType": opp["opportunityType"],
                "status": opp.get("status", ""),
                "deadline": opp["deadline"],
                "deadlineISO": opp["deadlineISO"],
                "url": opp["url"],
                "torText": opp.get("torText", ""),
                "resourceLinks": opp.get("resourceLinks", ""),
                "language": opp["language"],
                "fitScore": "",
                "fitLabel": "",
                "reviewSummary": "",
                "duplicateKey": opp["duplicateKey"],
                "processedAtUTC": processed,
                "owner": "",
                "pipelineStatus": "New",
            })
        logger.info("World Bank opportunities extracted: %d", len(wb_raw))

    # ── UNGM (UN Global Marketplace public notice search) ────────────────────
    if source_wanted(args.source, "ungm"):
        logger.info("Fetching UNGM opportunities...")
        try:
            skip_keys, _ = load_pipeline_state("UNGM", sheet_url=os.environ.get("SHEET_URL"))
        except Exception as e:
            logger.warning("Could not read Pipeline state (%s) -- fetching without a skip list", e)
            skip_keys = set()
        ungm_raw = fetch_ungm(days_back=args.days, skip_keys=skip_keys, limit=args.limit)
        processed = now_utc_iso()
        today = processed[:10]
        for opp in ungm_raw:
            all_opportunities.append({
                "source": "UNGM",
                "emailDate": today,
                "emailSubject": "",
                "alertName": opp["alertName"],
                "opportunityTitle": opp["opportunityTitle"],
                "donorClient": opp["donorClient"],
                "countryRegion": opp["countryRegion"],
                "opportunityType": opp["opportunityType"],
                "status": opp.get("status", ""),
                "deadline": opp["deadline"],
                "deadlineISO": opp["deadlineISO"],
                "url": opp["url"],
                "torText": opp.get("torText", ""),
                "resourceLinks": "",
                "language": opp["language"],
                "fitScore": "",
                "fitLabel": "",
                "reviewSummary": "",
                "duplicateKey": opp["duplicateKey"],
                "processedAtUTC": processed,
                "owner": "",
                "pipelineStatus": "New",
            })
        logger.info("UNGM opportunities extracted: %d", len(ungm_raw))

    # ── CDMX (Mexico City procurement portal) ────────────────────────────────
    if source_wanted(args.source, "cdmx"):
        logger.info("Fetching CDMX opportunities...")
        try:
            skip_keys, _ = load_pipeline_state("CDMX", sheet_url=os.environ.get("SHEET_URL"))
        except Exception as e:
            logger.warning("Could not read Pipeline state (%s) -- fetching without a skip list", e)
            skip_keys = set()
        cdmx_raw = fetch_cdmx(days_back=args.days, skip_keys=skip_keys, limit=args.limit)
        processed = now_utc_iso()
        today = processed[:10]
        for opp in cdmx_raw:
            all_opportunities.append({
                "source": "CDMX",
                "emailDate": today,
                "emailSubject": "",
                "alertName": opp["alertName"],
                "opportunityTitle": opp["opportunityTitle"],
                "donorClient": opp["donorClient"],
                "countryRegion": opp["countryRegion"],
                "opportunityType": opp["opportunityType"],
                "status": opp.get("status", ""),
                "deadline": opp["deadline"],
                "deadlineISO": opp["deadlineISO"],
                "url": opp["url"],
                "torText": opp.get("torText", ""),
                "resourceLinks": opp.get("resourceLinks", ""),
                "language": opp["language"],
                "fitScore": "",
                "fitLabel": "",
                "reviewSummary": "",
                "duplicateKey": opp["duplicateKey"],
                "processedAtUTC": processed,
                "owner": "",
                "pipelineStatus": "New",
            })
        logger.info("CDMX opportunities extracted: %d", len(cdmx_raw))

    logger.info("Total opportunities extracted: %d", len(all_opportunities))

    if args.dry_run:
        print(f"\n=== DRY RUN RESULTS ===")
        if source_wanted(args.source, "devex"):
            print(f"Gmail account:            {account}")
        print(f"Opportunities extracted:  {len(all_opportunities)}")
        print_preview(all_opportunities)
        print("Dry run complete — nothing written to Google Sheets.")
        return

    sheet_url = os.environ.get("SHEET_URL")
    result = append_opportunities(all_opportunities, sheet_url=sheet_url)

    logger.info(
        "Done. Appended: %d | Skipped (duplicates): %d | Total processed: %d",
        result["appended"],
        result["skipped"],
        result["total_processed"],
    )


if __name__ == "__main__":
    main()
