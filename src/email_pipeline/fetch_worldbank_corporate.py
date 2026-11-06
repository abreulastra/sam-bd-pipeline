"""
World Bank Group *corporate* procurement (Bank-executed work) -> Pipeline tab.

This is a different channel from fetch_worldbank.py. That one reads the
projects procurement API, i.e. notices issued by *borrowing governments* for
WB-financed projects. Studies, evaluations and analytics that the World Bank
Group (IBRD/IDA/IFC/CGAP...) contracts *itself* are advertised on WBG eProcure
("RFx Now") instead -- before 2026-11 those only reached us via DevelopmentAid
and Devex, which were then cancelled.

Public, no account (verified live 2026-11-06):
  - The listing page /rfxnow/public/advertisement/index.html is a JS app that
    loads every currently active advertisement from one JSON document,
    ACTIVE_URL below. A plain GET returns it; no cookie or token needed.
  - Each item: id, procurementNumber, procurementTitle, publicationDate,
    eoiDeadline (ISO 8601), and `text` -- the full advertisement as HTML.
    Public detail page: /rfxnow/public/advertisement/{id}/view.html
  - The list is short (~25 active worldwide), so there's no paging and no date
    window: an outage longer than a week still loses nothing that's open.
  - The JSON also carries WBG staff names/emails (createdBy...). Only the
    fields listed above are read -- don't add the rest to the sheet.

Geography: like fetch_worldbank.py this keeps Latin America & the Caribbean
(plus the USA, which C230 also targets). The country isn't a field -- it's
"Assignment Countries:" inside the HTML, in varying layouts, and sometimes
missing or "Not Country Related". Rows with no parseable country are KEPT
(countryRegion left blank) and left to sam-bd-agent, whose region gate treats
blank as unknown and scores it; only rows that explicitly name another
country are dropped here.
"""
import logging
import re
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from email_pipeline.fetch_worldbank import is_lac
from email_pipeline.normalize import infer_language, make_duplicate_key

logger = logging.getLogger(__name__)

SOURCE = "WBG Corporate"
DONOR = "World Bank Group"
BASE = "https://wbgeprocure-rfxnow.worldbank.org"
ACTIVE_URL = f"{BASE}/rfxnow/json/advertisement/activeAdvertisements.json"
DETAIL_URL = BASE + "/rfxnow/public/advertisement/{id}/view.html"
NOTICE_TYPE = "Request for Expression of Interest (Bank-executed)"

MAX_ROWS = 60          # ~25 ads are active worldwide at a time; more means something changed
REQUEST_TIMEOUT = 30
TOR_TEXT_CHARS = 1500

_COUNTRIES_RE = re.compile(
    r"Assignment\s+Countr(?:y|ies)\s*:\s*(.{0,200}?)\s*(?=ASSIGNMENT\s+DESCRIPTION)", re.I | re.S
)
# No "ASSIGNMENT DESCRIPTION" after it: take a short run up to the next sentence break.
_COUNTRIES_SHORT_RE = re.compile(r"Assignment\s+Countr(?:y|ies)\s*:\s*([^.:]{0,80})", re.I)
_NO_COUNTRY_RE = re.compile(r"not\s+country\s+related|global|worldwide|multiple", re.I)

# Many ads have no "Assignment Countries" line and name the place only in the
# title or description ("... Pilot in Uganda", "Montevideo Transport System").
# For those: keep if a LAC/USA place is mentioned (cities included -- the
# Montevideo ad never says "Uruguay"), drop if only another country is, and
# keep when nothing is named. Matched as whole words on accent-folded text.
LAC_EXTRA_TERMS = (
    "brasil", "united states", "usa", "puerto rico", "montevideo", "buenos aires", "sao paulo",
    "rio de janeiro", "brasilia", "belem", "manaus", "recife", "fortaleza", "porto alegre",
    "bogota", "medellin", "cali", "cartagena", "barranquilla", "quito", "guayaquil", "cusco",
    "arequipa", "la paz", "santa cruz de la sierra", "asuncion", "caracas", "tegucigalpa",
    "managua", "san salvador", "santo domingo", "port-au-prince", "port of spain", "paramaribo",
    "belmopan", "nassau", "bridgetown", "castries", "kingstown", "havana", "guadalajara",
    "monterrey", "valparaiso", "cordoba", "rosario", "amazon", "amazonia", "andean", "andes",
)
NON_LAC_COUNTRIES = (
    "afghanistan", "albania", "algeria", "andorra", "angola", "armenia", "australia", "austria",
    "azerbaijan", "bahrain", "bangladesh", "belarus", "belgium", "benin", "bhutan",
    "bosnia and herzegovina", "botswana", "brunei", "bulgaria", "burkina faso", "burundi",
    "cabo verde", "cape verde", "cambodia", "cameroon", "canada", "central african republic",
    "chad", "china", "comoros", "congo", "cote d'ivoire", "cote divoire", "ivory coast", "croatia",
    "cyprus", "czechia", "czech republic", "denmark", "djibouti", "egypt", "equatorial guinea",
    "eritrea", "estonia", "eswatini", "ethiopia", "fiji", "finland", "france", "gabon", "gambia",
    "germany", "ghana", "greece", "guinea", "guinea-bissau", "hungary", "iceland", "india",
    "indonesia", "iran", "iraq", "ireland", "israel", "italy", "japan", "jordan", "kazakhstan",
    "kenya", "kiribati", "korea", "kosovo", "kuwait", "kyrgyz republic", "kyrgyzstan", "lao pdr",
    "laos", "latvia", "lebanon", "lesotho", "liberia", "libya", "lithuania", "luxembourg",
    "madagascar", "malawi", "malaysia", "maldives", "mali", "malta", "marshall islands",
    "mauritania", "mauritius", "micronesia", "moldova", "mongolia", "montenegro", "morocco",
    "mozambique", "myanmar", "namibia", "nauru", "nepal", "netherlands", "new zealand", "niger",
    "nigeria", "north macedonia", "norway", "oman", "pakistan", "palau", "papua new guinea",
    "philippines", "poland", "portugal", "qatar", "romania", "russia", "rwanda", "samoa",
    "sao tome", "saudi arabia", "senegal", "serbia", "seychelles", "sierra leone", "singapore",
    "slovakia", "slovenia", "solomon islands", "somalia", "south africa", "south sudan", "spain",
    "sri lanka", "sudan", "sweden", "switzerland", "syria", "tajikistan", "tanzania", "thailand",
    "timor-leste", "togo", "tonga", "tunisia", "turkey", "turkiye", "turkmenistan", "tuvalu",
    "uganda", "ukraine", "united arab emirates", "united kingdom", "uzbekistan", "vanuatu",
    "vietnam", "viet nam", "west bank", "gaza", "yemen", "zambia", "zimbabwe",
    "africa", "asia", "europe", "middle east", "pacific islands",
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def _mentions(text: str, terms) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", text) for t in terms)


def duplicate_key_for(procurement_number) -> str:
    return make_duplicate_key(SOURCE, "", "", "", stable_id=f"wbg-corporate:{procurement_number}")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ")).strip()


def parse_countries(text: str) -> str:
    """'Brazil' / 'Moldova, Serbia' / '' when absent or not country-specific."""
    m = _COUNTRIES_RE.search(text) or _COUNTRIES_SHORT_RE.search(text)
    if not m:
        return ""
    value = m.group(1).strip(" -:;,")
    if not value or _NO_COUNTRY_RE.search(value):
        return ""
    parts = [p.strip(" -") for p in re.split(r"\s*[,;]\s*|\s+-\s+", value) if p.strip(" -")]
    return ", ".join(parts)[:200]


def in_scope(countries: str, title: str, text: str = "") -> bool:
    """
    LAC/USA, or no country named. False only when the ad names a place outside
    LAC/USA and none inside it.
    """
    if countries:
        probe = _fold(f"{countries} {title}")
        return is_lac(probe) or _mentions(probe, LAC_EXTRA_TERMS)
    probe = _fold(f"{title} {text}")
    if is_lac(probe) or _mentions(probe, LAC_EXTRA_TERMS):
        return True
    return not _mentions(probe, NON_LAC_COUNTRIES)


def _iso_date(value) -> str:
    try:
        return datetime.fromisoformat(str(value)[:19]).date().isoformat()
    except ValueError:
        return ""


def _to_pipeline_row(ad: dict, text: str, countries: str) -> dict:
    title = (ad.get("procurementTitle") or "").strip()
    number = str(ad.get("procurementNumber") or "").strip()
    deadline_iso = _iso_date(ad.get("eoiDeadline"))
    desc = re.split(r"ASSIGNMENT\s+DESCRIPTION", text, maxsplit=1, flags=re.I)
    return {
        "source": SOURCE,
        "alertName": "WBG eProcure (corporate procurement)",
        "opportunityTitle": title,
        "donorClient": DONOR,
        "countryRegion": countries,
        "opportunityType": NOTICE_TYPE,
        "status": number,
        "deadline": deadline_iso,
        "deadlineISO": deadline_iso,
        "url": DETAIL_URL.format(id=ad.get("id")),
        "torText": desc[-1].strip()[:TOR_TEXT_CHARS],
        "resourceLinks": "",
        "language": infer_language(title),
        "duplicateKey": duplicate_key_for(number),
    }


def _get_ads() -> list[dict]:
    resp = requests.get(ACTIVE_URL, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    ads = data.get("advertisementList") if isinstance(data, dict) else None
    if not isinstance(ads, list):
        raise ValueError("no advertisementList in response")
    return ads


def fetch_opportunities(skip_keys: set[str] | None = None, limit: int | None = None) -> list[dict]:
    """
    Active WBG corporate advertisements for LAC/USA (or with no country stated),
    deadline not past, not already in the sheet. Returns [] if the feed's shape
    changes rather than writing junk.
    """
    skip_keys = skip_keys or set()
    try:
        ads = _get_ads()
    except Exception as exc:
        logger.error("WBG Corporate: could not read the advertisement feed (%s) -- skipping", exc)
        return []

    # Tripwire: every item must look like an advertisement.
    if any(not isinstance(a, dict) or not a.get("procurementNumber") or not a.get("procurementTitle")
           for a in ads):
        logger.error("WBG Corporate: feed items no longer have procurementNumber/procurementTitle "
                     "-- format changed; writing nothing this run.")
        return []
    if len(ads) > MAX_ROWS * 3:
        logger.error("WBG Corporate: %d active ads (normally ~25) -- feed changed; writing nothing.", len(ads))
        return []

    today = datetime.now(timezone.utc).date().isoformat()
    cap = min(limit, MAX_ROWS) if limit is not None else MAX_ROWS
    counts = {"seen": len(ads), "not_lac": 0, "past_deadline": 0, "duplicate": 0}
    results = []
    for ad in ads:
        text = _plain(ad.get("text"))
        countries = parse_countries(text)
        if not in_scope(countries, ad["procurementTitle"], text):
            counts["not_lac"] += 1
            continue
        deadline = _iso_date(ad.get("eoiDeadline"))
        if deadline and deadline < today:
            counts["past_deadline"] += 1
            continue
        if duplicate_key_for(ad["procurementNumber"]) in skip_keys:
            counts["duplicate"] += 1
            continue
        results.append(_to_pipeline_row(ad, text, countries))
        if len(results) >= cap:
            break

    logger.info(
        "WBG Corporate: %d active | %d new | %d outside LAC/USA | %d past deadline | %d already in sheet",
        counts["seen"], len(results), counts["not_lac"], counts["past_deadline"], counts["duplicate"],
    )
    return results
