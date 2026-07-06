"""
Entry point for the email opportunity pipeline.

Usage:
    python -m src.email_pipeline.run_email_pipeline [--days 7] [--dry-run] [--limit N] [--source devex|developmentaid|all]
"""
import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# Allow relative imports when run as __main__
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from email_pipeline.fetch_gmail import fetch_emails
from email_pipeline.normalize import (
    infer_language,
    make_duplicate_key,
    now_utc_iso,
    parse_deadline_iso,
)
from email_pipeline.parse_devex import parse_opportunities as parse_devex
from email_pipeline.parse_developmentaid import parse_opportunities as parse_developmentaid
from email_pipeline.write_pipeline_sheet import append_opportunities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Email opportunity ingestion pipeline")
    p.add_argument("--days", type=int, default=7, help="Days back to search (default: 7)")
    p.add_argument("--dry-run", action="store_true", help="Parse and print without writing to Sheets")
    p.add_argument("--limit", type=int, default=None, help="Max emails to process per source")
    p.add_argument(
        "--source",
        choices=["devex", "developmentaid", "all"],
        default="all",
        help="Filter by source (default: all)",
    )
    return p.parse_args()


def process_email(email: dict) -> list[dict]:
    source = email["source"]
    html = email["html_body"]
    subject = email["subject"]

    if source == "devex":
        raw_opps = parse_devex(html, subject)
    elif source == "developmentaid":
        raw_opps = parse_developmentaid(html, subject)
    else:
        return []

    processed = now_utc_iso()
    results = []

    for opp in raw_opps:
        title = opp.get("opportunityTitle", "")
        donor = opp.get("donorClient", "")
        country = opp.get("countryRegion", "")
        deadline = opp.get("deadline", "")
        deadline_iso = opp.get("deadlineISO", "") or parse_deadline_iso(deadline)

        source_label = "Devex" if source == "devex" else "DevelopmentAid"

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
            "duplicateKey": make_duplicate_key(source_label, title, donor, country),
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
    account = os.environ.get("GMAIL_ACCOUNT_EMAIL", "rabreu@c-230.com")

    logger.info("Email pipeline starting")
    logger.info("Gmail account: %s", account)
    logger.info("Days back: %d | Source: %s | Dry run: %s", args.days, args.source, args.dry_run)

    emails = fetch_emails(days=args.days, limit=args.limit, source_filter=args.source)
    logger.info("Total emails fetched: %d", len(emails))

    all_opportunities = []
    for email in emails:
        opps = process_email(email)
        all_opportunities.extend(opps)

    logger.info("Total opportunities extracted: %d", len(all_opportunities))

    if args.dry_run:
        print(f"\n=== DRY RUN RESULTS ===")
        print(f"Gmail account:            {account}")
        print(f"Emails found:             {len(emails)}")
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
