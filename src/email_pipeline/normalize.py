"""
Normalization and deduplication helpers for the email pipeline.
"""
import re
import unicodedata
from datetime import datetime, timezone


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_duplicate_key(source: str, title: str, donor: str, country: str) -> str:
    parts = [
        normalize_text(source),
        normalize_text(title),
        normalize_text(donor),
        normalize_text(country),
    ]
    return " | ".join(parts)


def infer_language(title: str) -> str:
    if not title:
        return ""
    # Common Spanish markers
    spanish_markers = re.compile(
        r"\b(de|para|en|del|los|las|con|por|una|hacia|servicios|proyecto|"
        r"consultoría|consultoria|evaluación|evaluacion|fortalecimiento|"
        r"implementación|implementacion|asistencia técnica|asistencia tecnica)\b",
        re.IGNORECASE,
    )
    if spanish_markers.search(title):
        return "Spanish"
    return "English"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_deadline_iso(deadline_str: str) -> str:
    if not deadline_str:
        return ""
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(deadline_str, dayfirst=True)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    return ""
