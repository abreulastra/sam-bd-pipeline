import re
from datetime import datetime


def mmddyyyy(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y")


def normalize_date(value) -> str:
    return (value or "")[:10]


def opp_url_from_notice(notice_id: str) -> str:
    return f"https://sam.gov/opp/{notice_id}/view" if notice_id else ""


# Common scope-defining section headings in tender/grant/ToR documents.
# Spanish/Portuguese matter as much as English here: the DevelopmentAid and
# IDB BEO feeds are Latin America-focused, so most documents aren't in
# English, and an English-only list silently falls back to document-start on
# the majority of them. Accented and unaccented variants are both listed
# because PDF text extraction doesn't always preserve accents.
_EXCERPT_HEADINGS = (
    # English
    "scope of work",
    "terms of reference",
    "objective",
    "objectives",
    "background",
    "purpose",
    "description of services",
    # Spanish
    "terminos de referencia",
    "términos de referencia",
    "alcance del servicio",
    "alcance de los servicios",
    "alcance del trabajo",
    "objetivo general",
    "objetivo",
    "objetivos",
    "antecedentes",
    "objeto de la contratacion",
    "objeto de la contratación",
    # Portuguese
    "termos de referencia",
    "termos de referência",
    "escopo do trabalho",
    "objetivo geral",
    "contexto",
)


# A heading match followed shortly by a run of dots (dot leaders, e.g.
# "....... 4") is almost always a Table of Contents entry, not the actual
# section body -- skip those and keep looking.
_TOC_DOTS_RE = re.compile(r"\.{3,}")


def extract_excerpt(text: str, max_chars: int = 800) -> str:
    """
    Heuristic (non-LLM) excerpt of a document's most relevant section: text
    starting at the first scope-defining heading found (case-insensitive),
    or the opening paragraph if none match. Keeps attachment content in the
    sheet short and on-topic without adding an LLM dependency to ingestion.
    """
    if not text:
        return ""

    lowered = text.lower()
    start = None
    for heading in _EXCERPT_HEADINGS:
        search_from = 0
        while True:
            idx = lowered.find(heading, search_from)
            if idx == -1:
                break
            if _TOC_DOTS_RE.search(text[idx:idx + 100]):
                search_from = idx + len(heading)
                continue
            if start is None or idx < start:
                start = idx
            break

    if start is None:
        start = 0

    excerpt = text[start:start + max_chars].strip()
    return excerpt
