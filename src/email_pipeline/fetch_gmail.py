"""
Gmail API client for fetching Devex and DevelopmentAid alert emails.
Authenticates using OAuth2 credentials stored in environment variables.
"""
import base64
import logging
import os
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def _build_queries() -> dict:
    da_sender = os.environ.get("DEVELOPMENTAID_SENDER", "")
    da_query = f'from:{da_sender} "DevelopmentAid"' if da_sender else '"DevelopmentAid"'
    return {
        "devex": "from:alerts@devex.com",
        "developmentaid": da_query,
    }


def build_gmail_client():
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    account = os.environ.get("GMAIL_ACCOUNT_EMAIL", "")

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError(
            "Missing Gmail credentials. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
            "and GMAIL_REFRESH_TOKEN environment variables."
        )

    logger.info("Authenticating Gmail for account: %s", account)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_messages(service, query: str, days: int, limit: int | None = None) -> list[dict]:
    full_query = f"{query} newer_than:{days}d"
    logger.info("Gmail search: %s", full_query)

    messages = []
    page_token = None

    while True:
        kwargs = {"userId": "me", "q": full_query, "maxResults": 100}
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.users().messages().list(**kwargs).execute()
        batch = result.get("messages", [])
        messages.extend(batch)

        if limit and len(messages) >= limit:
            messages = messages[:limit]
            break

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return messages


def fetch_message(service, message_id: str) -> dict:
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    return msg


def parse_message_metadata(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    subject = headers.get("subject", "")
    date_str = headers.get("date", "")
    sender = headers.get("from", "")

    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        email_date = dt.date().isoformat()
    except Exception:
        email_date = ""

    return {
        "message_id": msg["id"],
        "subject": subject,
        "email_date": email_date,
        "sender": sender,
    }


def get_html_body(msg: dict) -> str:
    payload = msg.get("payload", {})
    return _extract_html(payload)


def _extract_html(part: dict) -> str:
    mime = part.get("mimeType", "")
    if mime == "text/html":
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    for sub in part.get("parts", []):
        result = _extract_html(sub)
        if result:
            return result

    return ""


def fetch_emails(days: int = 7, limit: int | None = None, source_filter: str = "all") -> list[dict]:
    """
    Returns list of dicts with keys: source, message_id, subject, email_date, sender, html_body
    """
    service = build_gmail_client()
    results = []

    queries = _build_queries()
    sources = ["devex", "developmentaid"] if source_filter == "all" else [source_filter]

    for source in sources:
        query = queries[source]
        messages = search_messages(service, query, days, limit)
        logger.info("Found %d %s messages", len(messages), source)

        for m in messages:
            msg = fetch_message(service, m["id"])
            meta = parse_message_metadata(msg)
            html = get_html_body(msg)

            results.append({
                "source": source,
                **meta,
                "html_body": html,
            })

    return results
