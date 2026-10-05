"""
Mexico City (CDMX) public procurement -> Pipeline tab.

Portal: https://concursodigital.finanzas.cdmx.gob.mx/convocatorias_publicas

Unlike the other sources this one is **keyword-filtered at ingestion**
(Raúl's decision, 2026-10-05). The portal is overwhelmingly goods, works and
maintenance: at the time of writing the 11 open tenders were uniforms, Metro
sanitation, tree trimming and musical instruments, and a year of closed
tenders held ~2 that C230 could bid on. Ingesting everything would add daily
noise for a couple of hits a year, so only titles that look like consulting,
research or evaluation work are written -- and medical/clinical "estudios"
and "consultas", which dominate the false positives, are excluded.

Mechanics (verified live 2026-10-05):
  - GET /convocatorias_publicas renders the open tenders as cards and carries
    a Laravel `_token`; POST to the same URL with that token filters by
    keyword, date range and vigencia (vigentes / cerradas).
  - Each card links to /proveedores/detalle_convocatoria/<base64 id>, a public
    page with the procedure details plus the bases and anexo_tecnico PDFs.
    The anexo tecnico is where the actual scope lives, so torText comes from
    it when present (falling back to the detail page text).
  - No pagination: the open list is a single page.
"""
import io
import logging
import re
import time
from datetime import date, datetime, timedelta

import pdfplumber
import requests
from bs4 import BeautifulSoup

from email_pipeline.normalize import infer_language, make_duplicate_key
from utils import extract_excerpt

logger = logging.getLogger(__name__)
# pdfminer logs a CropBox warning per page; CDMX bases run to hundreds of pages
# and would bury the real run output.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

SOURCE = "CDMX"
BASE = "https://concursodigital.finanzas.cdmx.gob.mx"
LIST_URL = f"{BASE}/convocatorias_publicas"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

MAX_NEW_ROWS = 25
REQUEST_TIMEOUT = 45
MIN_REQUEST_INTERVAL = 1.0
MAX_PDF_CHARS = 8000
TOR_TEXT_CHARS = 1500

# Titles worth ingesting: advisory / research / evaluation style work.
CONSULTING_RE = re.compile(
    r"consultor|asesor[íi]a|evaluaci[óo]n|diagn[óo]stic|estudio|investigaci[óo]n|"
    r"encuesta|levantamiento de informaci[óo]n|monitoreo|seguimiento y evaluaci[óo]n|"
    r"pol[íi]tica p[úu]blica|an[áa]lisis|sistematizaci[óo]n|capacitaci[óo]n|"
    r"fortalecimiento institucional|plan maestro|planeaci[óo]n|dise[ñn]o de programa",
    re.I,
)
# Clinical and lab services are the main false positives for "estudio",
# "consulta" and "diagnostico" -- they are medical care, not research.
MEDICAL_RE = re.compile(
    r"colposcopia|oftalmol[óo]gic|toxicol[óo]gic|hospitalari|m[ée]dic|cl[íi]nic|"
    r"laboratorio|radiolog|imagenolog|consulta externa|an[áa]lisis cl[íi]nic|"
    r"ambulancia|farmac",
    re.I,
)
# Goods purchases never carry consulting scope even when the title mentions a study.
GOODS_TYPE_RE = re.compile(r"adquisici[óo]n de bienes|arrendamiento", re.I)

_next_allowed = 0.0


def _throttle():
    global _next_allowed
    wait = _next_allowed - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _next_allowed = time.monotonic() + MIN_REQUEST_INTERVAL


def _open_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    _throttle()
    resp = session.get(LIST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    session.__dict__["_cdmx_list_html"] = resp.text
    return session


CARD_LABELS = (
    "Fecha de publicación", "Presentación de propuestas", "Tipo de contratación",
    "Carácter", "Método de contratación", "Entidad convocante", "Revisar",
)


def _field(body_text: str, label: str) -> str:
    """
    Card bodies read 'Label | value | Next label | value'. An empty field is
    rendered as two adjacent labels, so a captured value that is itself a
    label means the field was blank.
    """
    match = re.search(rf"{label}\s*\|\s*([^|]+)", body_text)
    if not match:
        return ""
    value = match.group(1).strip()
    return "" if value in CARD_LABELS else value


def _parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    notices = []
    for card in soup.select("div.card"):
        header = card.select_one(".card-header")
        body = card.select_one(".card-body")
        link = card.select_one("a[href*='detalle_convocatoria']")
        if not header or not body or not link:
            continue
        body_text = re.sub(r"\s+", " ", body.get_text(" | ", strip=True))
        url = link.get("href", "")
        notices.append({
            "id": url.rstrip("/").split("/")[-1],
            "title": re.sub(r"\s+", " ", header.get_text(" ", strip=True)),
            "url": url,
            "published": _field(body_text, "Fecha de publicación"),
            "deadline": _field(body_text, "Presentación de propuestas"),
            "contractType": _field(body_text, "Tipo de contratación"),
            "method": _field(body_text, "Método de contratación"),
            "entity": _field(body_text, "Entidad convocante"),
        })
    return notices


def _parse_date(value: str) -> date | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def looks_relevant(notice: dict) -> bool:
    """Keyword gate -- see the module docstring for why this source is filtered."""
    title = notice.get("title", "")
    if GOODS_TYPE_RE.search(notice.get("contractType", "")):
        return False
    if MEDICAL_RE.search(title):
        return False
    return bool(CONSULTING_RE.search(title))


def duplicate_key_for(notice_id) -> str:
    return make_duplicate_key(SOURCE, "", "", "", stable_id=f"cdmx:{notice_id}")


def _pdf_text(session: requests.Session, url: str) -> str:
    try:
        _throttle()
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            pages = [p.extract_text() for p in pdf.pages]
        return "\n".join(t for t in pages if t)[:MAX_PDF_CHARS]
    except Exception as exc:
        logger.warning("CDMX: could not read %s: %s", url, exc)
        return ""


def _fetch_detail(session: requests.Session, url: str) -> tuple[str, list[str]]:
    """Return (torText, document links). Scope lives in the anexo tecnico PDF."""
    try:
        _throttle()
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("CDMX: could not open %s: %s", url, exc)
        return "", []

    soup = BeautifulSoup(resp.text, "html.parser")
    docs = [a.get("href") for a in soup.select("a[href$='.pdf']") if a.get("href")]
    annex = next((d for d in docs if "anexo" in d.lower()), None)
    text = extract_excerpt(_pdf_text(session, annex), max_chars=TOR_TEXT_CHARS) if annex else ""
    if not text:
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:TOR_TEXT_CHARS]
    return text, docs


def _to_pipeline_row(notice: dict, tor_text: str, docs: list[str]) -> dict:
    deadline = _parse_date(notice["deadline"])
    title = notice["title"]
    return {
        "source": SOURCE,
        "alertName": f"CDMX ({notice['method']})" if notice["method"] else "CDMX",
        "opportunityTitle": title,
        "donorClient": notice["entity"] or "Gobierno de la Ciudad de México",
        "countryRegion": "Mexico",
        "opportunityType": notice["contractType"],
        "status": notice["method"],
        "deadline": notice["deadline"],
        "deadlineISO": deadline.isoformat() if deadline else "",
        "url": notice["url"],
        "torText": tor_text,
        "resourceLinks": "|".join(docs),
        "language": infer_language(title) or "Spanish",
        "duplicateKey": duplicate_key_for(notice["id"]),
    }


def fetch_opportunities(
    days_back: int = 7,
    skip_keys: set[str] | None = None,
    limit: int | None = None,
    fetch_details: bool = True,
) -> list[dict]:
    """
    Open CDMX tenders published in the last `days_back` days whose title looks
    like consulting/research/evaluation work (see looks_relevant), excluding
    anything already in the sheet. Returns [] if the page stops parsing.
    """
    skip_keys = skip_keys or set()
    cap = min(limit, MAX_NEW_ROWS) if limit is not None else MAX_NEW_ROWS
    today = date.today()
    cutoff = today - timedelta(days=days_back)
    counts = {"seen": 0, "off_topic": 0, "too_old": 0, "past_deadline": 0, "duplicate": 0}
    results = []

    try:
        session = _open_session()
        html = session.__dict__["_cdmx_list_html"]
    except Exception as exc:
        logger.error("CDMX: could not load the notice list: %s", exc)
        return []

    notices = _parse_cards(html)
    if not notices:
        logger.error(
            "CDMX: no parseable tender cards on %s -- the markup changed; "
            "writing nothing this run.", LIST_URL,
        )
        return []

    for notice in notices:
        counts["seen"] += 1
        published = _parse_date(notice["published"])
        if published and published < cutoff:
            counts["too_old"] += 1
            continue
        deadline = _parse_date(notice["deadline"])
        if deadline and deadline < today:
            counts["past_deadline"] += 1
            continue
        if not looks_relevant(notice):
            counts["off_topic"] += 1
            continue
        if duplicate_key_for(notice["id"]) in skip_keys:
            counts["duplicate"] += 1
            continue

        tor_text, docs = _fetch_detail(session, notice["url"]) if fetch_details else ("", [])
        results.append(_to_pipeline_row(notice, tor_text, docs))
        if len(results) >= cap:
            if limit is None:
                logger.warning("CDMX: hit MAX_NEW_ROWS=%d -- stopping early", MAX_NEW_ROWS)
            break

    logger.info(
        "CDMX: %d seen | %d new | %d off-topic | %d older than %d days | %d past deadline | %d already in sheet",
        counts["seen"], len(results), counts["off_topic"], counts["too_old"], days_back,
        counts["past_deadline"], counts["duplicate"],
    )
    return results
