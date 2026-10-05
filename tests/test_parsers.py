"""
Parser unit tests using minimal HTML fixtures.
Run with: python -m pytest tests/ -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from email_pipeline.parse_devex import parse_opportunities as parse_devex, parse_alert_name
from email_pipeline.parse_developmentaid import parse_opportunities as parse_developmentaid
from email_pipeline.normalize import make_duplicate_key, infer_language, normalize_text
from utils import extract_excerpt


# Devex lays out each opportunity as a title <tr> (with a status badge in a
# second <td>) followed by three plain-text sibling <tr> rows for
# donor/country/deadline (no field labels in the real markup), then two blank
# spacer rows before the next opportunity. Verified against live alert emails.
DEVEX_FIXTURE = """
<html><body>
<h2>Tenders &amp; Grants</h2>
<table>
  <tr>
    <td><div><a href="https://www.devex.com/en/opportunity/consulting-services-for-governance-reform-12345">
      Consulting Services for Governance Reform in Ecuador
    </a></div></td>
    <td><div>OPEN</div></td>
  </tr>
  <tr><td>USAID</td></tr>
  <tr><td>Ecuador</td></tr>
  <tr><td>August 15, 2026</td></tr>
  <tr><td></td></tr>
  <tr><td></td></tr>
  <tr>
    <td><div><a href="https://www.devex.com/en/opportunity/technical-assistance-for-justice-sector-67890">
      Technical Assistance for Justice Sector Strengthening
    </a></div></td>
    <td><div>FORECAST</div></td>
  </tr>
  <tr><td>State Department / INL</td></tr>
  <tr><td>Mexico</td></tr>
  <tr><td>September 1, 2026</td></tr>
</table>
<a href="https://www.devex.com/home">Home</a>
<a href="https://www.devex.com/account/unsubscribe">Unsubscribe</a>
<a href="https://twitter.com/devex">Twitter</a>
</body></html>
"""

# DevelopmentAid lays out each opportunity as an outer <tr> containing a
# nested title table, followed by a sibling <tr> containing a nested
# label/value table (Funding agency / Location / Deadline / ...).
# Verified against live alert emails.
DEVELOPMENTAID_FIXTURE = """
<html><body>
<h2>Tender Alert: LAC Contracts</h2>
<h3>Open</h3>
<table>
  <tr>
    <td><table><tr>
      <td><a href="https://developmentaid.org/tenders/view/servicios-de-consultoria-para-seguridad-ciudadana-111">
        Servicios de Consultoría para Seguridad Ciudadana en Honduras
      </a></td>
      <td>new</td>
    </tr></table></td>
  </tr>
  <tr>
    <td><table>
      <tr><td>Funding agency:</td><td>UNDP</td></tr>
      <tr><td>Location:</td><td>Honduras</td></tr>
      <tr><td>Deadline:</td><td>01 Sep, 2026</td></tr>
    </table></td>
  </tr>
  <tr>
    <td><table><tr>
      <td><a href="https://developmentaid.org/tenders/view/monitoring-evaluation-learning-melos-222">
        Monitoring, Evaluation, Learning and Sharing (MELoS) for LAC Region
      </a></td>
      <td>new</td>
    </tr></table></td>
  </tr>
  <tr>
    <td><table>
      <tr><td>Funding agency:</td><td>World Bank Group</td></tr>
      <tr><td>Location:</td><td>Latin America</td></tr>
      <tr><td>Deadline:</td><td>30 Aug, 2026</td></tr>
    </table></td>
  </tr>
</table>
<a href="https://developmentaid.org/login">Login</a>
<a href="mailto:info@developmentaid.info">Contact</a>
</body></html>
"""


class TestDevexParser:
    def test_alert_name_parsing(self):
        assert parse_alert_name("3 new reports for your Business Alert: DOS") == "DOS"
        assert parse_alert_name("Business Alert: Consulting LAC, MEX, USA") == "Consulting LAC, MEX, USA"
        assert parse_alert_name("No match here") == ""

    def test_extracts_opportunities(self):
        results = parse_devex(DEVEX_FIXTURE, "3 new reports for your Business Alert: DOS")
        assert len(results) == 2

    def test_skips_nav_links(self):
        results = parse_devex(DEVEX_FIXTURE, "Business Alert: DOS")
        urls = [r["url"] for r in results]
        assert not any("unsubscribe" in u for u in urls)
        assert not any("twitter" in u for u in urls)
        assert not any("/home" in u for u in urls)

    def test_opportunity_type(self):
        results = parse_devex(DEVEX_FIXTURE, "Business Alert: DOS")
        assert all(r["opportunityType"] == "Tenders & Grants" for r in results)

    def test_titles_non_empty(self):
        results = parse_devex(DEVEX_FIXTURE, "Business Alert: DOS")
        for r in results:
            assert len(r["opportunityTitle"]) >= 15

    def test_extracts_donor_country_deadline_status(self):
        results = parse_devex(DEVEX_FIXTURE, "Business Alert: DOS")
        first = next(r for r in results if "Governance Reform" in r["opportunityTitle"])
        assert first["donorClient"] == "USAID"
        assert first["countryRegion"] == "Ecuador"
        assert first["deadline"] == "August 15, 2026"
        assert first["status"] == "OPEN"

        second = next(r for r in results if "Justice Sector" in r["opportunityTitle"])
        assert second["donorClient"] == "State Department / INL"
        assert second["countryRegion"] == "Mexico"
        assert second["deadline"] == "September 1, 2026"
        assert second["status"] == "FORECAST"


class TestDevelopmentAidParser:
    def test_alert_name_parsing(self):
        from email_pipeline.parse_developmentaid import parse_alert_name as da_alert
        assert da_alert("1 Funding opportunities from DevelopmentAid: LAC Contracts") == "LAC Contracts"

    def test_extracts_opportunities(self):
        results = parse_developmentaid(
            DEVELOPMENTAID_FIXTURE,
            "1 Funding opportunities from DevelopmentAid: LAC Contracts"
        )
        assert len(results) == 2

    def test_opportunity_type_tender(self):
        results = parse_developmentaid(
            DEVELOPMENTAID_FIXTURE,
            "1 Funding opportunities from DevelopmentAid: LAC Contracts"
        )
        assert all(r["opportunityType"] == "Tender" for r in results)

    def test_skips_nav_links(self):
        results = parse_developmentaid(
            DEVELOPMENTAID_FIXTURE,
            "1 Funding opportunities from DevelopmentAid: LAC Contracts"
        )
        urls = [r["url"] for r in results]
        assert not any("login" in u for u in urls)
        assert not any("mailto" in u for u in urls)

    def test_spanish_title(self):
        results = parse_developmentaid(
            DEVELOPMENTAID_FIXTURE,
            "1 Funding opportunities from DevelopmentAid: LAC Contracts"
        )
        spanish_opp = next(r for r in results if "Seguridad" in r["opportunityTitle"])
        assert spanish_opp is not None

    def test_extracts_donor_country_deadline(self):
        results = parse_developmentaid(
            DEVELOPMENTAID_FIXTURE,
            "1 Funding opportunities from DevelopmentAid: LAC Contracts"
        )
        first = next(r for r in results if "Seguridad" in r["opportunityTitle"])
        assert first["donorClient"] == "UNDP"
        assert first["countryRegion"] == "Honduras"
        assert first["deadline"] == "01 Sep, 2026"
        assert first["deadlineISO"] == "2026-09-01"

        second = next(r for r in results if "MELoS" in r["opportunityTitle"])
        assert second["donorClient"] == "World Bank Group"
        assert second["countryRegion"] == "Latin America"
        assert second["deadline"] == "30 Aug, 2026"


class TestNormalize:
    def test_duplicate_key_deterministic(self):
        k1 = make_duplicate_key("Devex", "  Consulting  Services ", "USAID", "Ecuador")
        k2 = make_duplicate_key("Devex", "Consulting Services", "USAID", "Ecuador")
        assert k1 == k2

    def test_duplicate_key_with_blanks(self):
        key = make_duplicate_key("DevelopmentAid", "Some Title", "", "")
        assert "some title" in key
        assert key.count("|") == 3

    def test_duplicate_key_prefers_stable_id(self):
        # A passed-in stable_id (e.g. DevelopmentAid's own numeric tender ID)
        # takes precedence over both the URL-regex path and the text-based
        # fallback -- two calls with different title/donor/country but the
        # same stable_id must dedupe to the same key.
        key1 = make_duplicate_key(
            "DevelopmentAid", "Title A", "Donor A", "Country A",
            stable_id="developmentaid-api:tender:12345",
        )
        key2 = make_duplicate_key(
            "DevelopmentAid", "Title B", "Donor B", "Country B",
            stable_id="developmentaid-api:tender:12345",
        )
        assert key1 == key2
        assert "developmentaid-api:tender:12345" in key1

    def test_language_detection(self):
        assert infer_language("Servicios de consultoría para fortalecimiento") == "Spanish"
        assert infer_language("Governance Reform Technical Assistance") == "English"

    def test_normalize_removes_punctuation(self):
        assert normalize_text("Hello, World! Test.") == "hello world test"


class TestExtractExcerpt:
    def test_finds_clean_heading(self):
        text = (
            "Some cover page text here.\n\n"
            "SCOPE OF WORK\n"
            "The contractor shall provide consulting services for the "
            "modernization of the national health information system."
        )
        excerpt = extract_excerpt(text, max_chars=200)
        assert excerpt.startswith("SCOPE OF WORK")
        assert "modernization" in excerpt

    def test_skips_table_of_contents_entry(self):
        # "Purpose" appears twice: once as a dot-leader TOC line (must be
        # skipped), once as the real section header further down.
        text = (
            "CONTENTS\n"
            "1. Purpose ....................................... 4\n"
            "2. Background ................................... 6\n\n"
            "1. PURPOSE\n"
            "This document sets out the terms of reference for the assignment."
        )
        excerpt = extract_excerpt(text, max_chars=200)
        assert "......." not in excerpt
        assert "terms of reference for the assignment" in excerpt

    def test_finds_spanish_heading(self):
        # LAC-focused feeds are mostly non-English; an English-only heading
        # list silently falls back to document-start on these.
        text = (
            "Fundación Vida\nLicitación PR804C 2025/10\n\n"
            "TÉRMINOS DE REFERENCIA\n"
            "Contratación de servicios para la legalización de grupos comunitarios."
        )
        excerpt = extract_excerpt(text, max_chars=200)
        assert excerpt.startswith("TÉRMINOS DE REFERENCIA")
        assert "legalización de grupos comunitarios" in excerpt

    def test_finds_unaccented_spanish_heading(self):
        # PDF extraction doesn't always preserve accents.
        text = "Portada\n\nOBJETIVO GENERAL\nFortalecer las capacidades locales de monitoreo."
        excerpt = extract_excerpt(text, max_chars=200)
        assert excerpt.startswith("OBJETIVO GENERAL")

    def test_falls_back_to_start_when_no_heading_matches(self):
        text = "This document does not contain any of the tracked section headings at all."
        excerpt = extract_excerpt(text, max_chars=20)
        assert excerpt == text[:20]

    def test_respects_max_chars(self):
        text = "Objective: " + ("x" * 5000)
        excerpt = extract_excerpt(text, max_chars=100)
        assert len(excerpt) <= 100

    def test_empty_text(self):
        assert extract_excerpt("", max_chars=100) == ""
        assert extract_excerpt(None, max_chars=100) == ""


class TestDevelopmentAidApiFetch:
    """
    fetch_opportunities must drop already-ingested items *before* the detail
    request -- that's where the 20 req/min budget goes -- and honor `limit`.
    Network is stubbed out; nothing here touches the real API.
    """

    def _stub(self, monkeypatch):
        from email_pipeline import fetch_developmentaid_api as da

        monkeypatch.setattr(da, "_throttle", lambda: None)
        # Search returns newest-first, as the real API does.
        monkeypatch.setattr(
            da, "_search",
            lambda kind, api_key, pf, pt: (
                [{"id": 1, "postedDate": "2026-09-03"}, {"id": 2, "postedDate": "2026-09-01"}]
                if kind == "tenders"
                else [{"id": 3, "postedDate": "2026-09-02"}]
            ),
        )
        fetched = []

        def fake_detail(kind, api_key, item_id):
            fetched.append((kind, item_id))
            return {"id": item_id, "name": f"{kind} {item_id}", "documents": []}

        monkeypatch.setattr(da, "_fetch_detail", fake_detail)
        return da, fetched

    def test_skips_items_already_in_sheet_before_fetching_detail(self, monkeypatch):
        da, fetched = self._stub(monkeypatch)
        already = {da.duplicate_key_for("tenders", 1), da.duplicate_key_for("grants", 3)}

        rows = da.fetch_opportunities("key", days_back=1, skip_keys=already)

        assert fetched == [("tenders", 2)]
        assert [r["duplicateKey"] for r in rows] == [da.duplicate_key_for("tenders", 2)]

    def test_limit_caps_new_items_fetched(self, monkeypatch):
        da, fetched = self._stub(monkeypatch)

        rows = da.fetch_opportunities("key", days_back=1, skip_keys=set(), limit=1)

        assert len(fetched) == 1
        assert len(rows) == 1

    def test_fetches_oldest_first_so_deferred_items_dont_age_out(self, monkeypatch):
        # Search returns newest-first; under a budget we must spend it on the
        # oldest, since those are the ones about to fall out of the search
        # window. Newest deferred items will still be there next run.
        da, fetched = self._stub(monkeypatch)

        da.fetch_opportunities("key", days_back=1, skip_keys=set(), limit=2)

        assert fetched == [("tenders", 2), ("grants", 3)]  # 2026-09-01, then 09-02

    def test_daily_tender_budget_caps_fetches(self, monkeypatch):
        # The 100 unique tenders/24h membership quota binds before the
        # per-minute throttle does.
        da, fetched = self._stub(monkeypatch)
        monkeypatch.setattr(da, "DAILY_TENDER_BUDGET", 2)

        rows = da.fetch_opportunities("key", days_back=1, skip_keys=set())

        assert len(fetched) == 2
        assert len(rows) == 2

    def test_budget_already_spent_today_fetches_nothing(self, monkeypatch):
        da, fetched = self._stub(monkeypatch)
        monkeypatch.setattr(da, "DAILY_TENDER_BUDGET", 2)

        rows = da.fetch_opportunities("key", days_back=1, skip_keys=set(), budget_used_today=2)

        assert fetched == []
        assert rows == []


class TestWorldBankFetch:
    """
    The World Bank API silently ignores most query params, so the fetcher
    must stop at the date cutoff itself, refuse to write anything if the
    filters stop being honored, and keep only LAC firm-level notices.
    Network is stubbed out; nothing here touches the real API.
    """

    def _notice(self, nid, days_ago, country="Colombia", method="QCBS",
                deadline_in_days=10, group="CS", ntype="Request for Expression of Interest"):
        from datetime import date, timedelta
        today = date.today()
        return {
            "id": nid,
            "noticedate": (today - timedelta(days=days_ago)).strftime("%d-%b-%Y"),
            "procurement_group": group,
            "notice_type": ntype,
            "procurement_method_code": method,
            "procurement_method_name": "Quality And Cost-Based Selection",
            "project_ctry_name": country,
            "submission_deadline_date": f"{(today + timedelta(days=deadline_in_days)).isoformat()}T00:00:00Z",
            "bid_description": f"Evaluation services {nid}",
            "notice_text": "<p>Background.</p><p><strong>Scope of Work</strong></p><p>Conduct a mid-term evaluation.</p>",
        }

    def _stub(self, monkeypatch, pages):
        from email_pipeline import fetch_worldbank as wb

        monkeypatch.setattr(wb, "_throttle", lambda: None)
        calls = []

        def fake_page(offset):
            calls.append(offset)
            i = offset // wb.PAGE_SIZE
            return pages[i] if i < len(pages) else []

        monkeypatch.setattr(wb, "_get_page", fake_page)
        return wb, calls

    def test_stops_paging_at_days_back_cutoff(self, monkeypatch):
        wb, calls = self._stub(monkeypatch, [
            [self._notice("A", 1), self._notice("B", 9)],
            [self._notice("C", 10)],
        ])

        rows = wb.fetch_opportunities(days_back=7)

        assert [r["duplicateKey"] for r in rows] == [wb.duplicate_key_for("A")]
        assert calls == [0]  # never asked for page 2

    def test_keeps_only_lac_firm_open_new_notices(self, monkeypatch):
        wb, _ = self._stub(monkeypatch, [[
            self._notice("keep", 1, country="St. Lucia"),
            self._notice("indv", 1, method="INDV"),
            self._notice("africa", 1, country="Kenya"),
            self._notice("expired", 1, deadline_in_days=-2),
            self._notice("seen", 1),
        ]])

        rows = wb.fetch_opportunities(days_back=7, skip_keys={wb.duplicate_key_for("seen")})

        assert [r["duplicateKey"] for r in rows] == [wb.duplicate_key_for("keep")]

    def test_tripwire_writes_nothing_if_filters_ignored(self, monkeypatch):
        wb, _ = self._stub(monkeypatch, [[
            self._notice("ok", 1),
            self._notice("goods", 1, group="GO", ntype="Contract Award"),
        ]])

        assert wb.fetch_opportunities(days_back=7) == []

    def test_limit_and_max_new_rows_cap_output(self, monkeypatch):
        wb, _ = self._stub(monkeypatch, [[self._notice(str(i), 1) for i in range(10)]])
        monkeypatch.setattr(wb, "MAX_NEW_ROWS", 3)

        assert len(wb.fetch_opportunities(days_back=7, limit=2)) == 2
        assert len(wb.fetch_opportunities(days_back=7)) == 3

    def test_row_mapping(self, monkeypatch):
        wb, _ = self._stub(monkeypatch, [[self._notice("OP123", 1, deadline_in_days=5)]])

        row = wb.fetch_opportunities(days_back=7)[0]

        assert row["url"] == "https://projects.worldbank.org/en/projects-operations/procurement-detail/OP123"
        assert len(row["deadlineISO"]) == 10 and row["deadline"] == row["deadlineISO"]
        assert row["torText"].startswith("Background. Scope of Work Conduct a mid-term evaluation.")
        assert row["source"] == "World Bank" and row["countryRegion"] == "Colombia"


# UNGM search responses are HTML table rows, not JSON. This mirrors the live
# markup (verified 2026-10-05): the first tableCell holds action buttons, then
# title, deadline, published date, agency, notice type, reference, country.
UNGM_ROW_TEMPLATE = """
<div role="row" data-noticeid="{id}" class="tableRow dataRow notice-table">
  <div role="cell" class="tableCell editable resultOptions">
    <input type="button" value='Express Interest' data-noticeid="{id}" />
  </div>
  <div role="cell" class="tableCell resultTitle">
    <span class="ungm-title ungm-title--small">{title}</span>
    <a target='_blank' href='/Public/Notice/{id}'></a>
  </div>
  <div role="cell" class="tableCell resultInfo1 deadline" data-description="Deadline">
    <span>{deadline} 12:00 (GMT -4.00)</span>
    <span class="remainingDaysToDeadline" style="display:none">5.5</span>
  </div>
  <div role="cell" class="tableCell"><span>{published}</span></div>
  <div role="cell" class="tableCell resultAgency"><span>{agency}</span></div>
  <div role="cell" class="tableCell"><span><label for='{ntype}'>{ntype}</label></span></div>
  <div role="cell" class="tableCell resultInfo1" data-description="Reference"><span>{ref}</span></div>
  <div role="cell" class="tableCell"><span>{country}</span></div>
</div>
"""


def _ungm_html(notices):
    return "".join(UNGM_ROW_TEMPLATE.format(**n) for n in notices)


def _ungm_notice(nid, published_days_ago=1, deadline_in_days=20,
                 ntype="Request for proposal", country="Colombia", agency="UNDP"):
    from datetime import date, timedelta
    today = date.today()
    return {
        "id": nid,
        "title": f"Evaluation services {nid}",
        "deadline": (today + timedelta(days=deadline_in_days)).strftime("%d-%b-%Y"),
        "published": (today - timedelta(days=published_days_ago)).strftime("%d-%b-%Y"),
        "agency": agency,
        "ntype": ntype,
        "ref": f"REF-{nid}",
        "country": country,
    }


class TestUngmFetch:
    """
    The UNGM search is paged HTML behind an anti-forgery token, so the fetcher
    must parse rows defensively, stop at the date cutoff itself, and refuse to
    write anything if the markup stops parsing. Network is stubbed out.
    """

    def _stub(self, monkeypatch, pages, descriptions=True):
        from email_pipeline import fetch_ungm as ungm

        monkeypatch.setattr(ungm, "_throttle", lambda: None)
        monkeypatch.setattr(ungm, "_open_session", lambda: (None, "token"))
        asked = []

        def fake_search(session, token, page, published_from):
            asked.append(page)
            return _ungm_html(pages[page]) if page < len(pages) else ""

        monkeypatch.setattr(ungm, "_search_page", fake_search)
        monkeypatch.setattr(ungm, "_fetch_description",
                            lambda session, nid: f"Description of {nid}" if descriptions else "")
        return ungm, asked

    def test_parses_a_search_row(self, monkeypatch):
        ungm, _ = self._stub(monkeypatch, [[_ungm_notice("316942", country="Panama", agency="FAO")]])

        rows = ungm.fetch_opportunities(days_back=7)

        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "UNGM"
        assert row["url"] == "https://www.ungm.org/Public/Notice/316942"
        assert row["countryRegion"] == "Panama"
        assert row["donorClient"] == "FAO" and row["alertName"] == "UNGM (FAO)"
        assert row["opportunityType"] == "Request for proposal"
        assert len(row["deadlineISO"]) == 10
        assert row["torText"] == "Description of 316942"
        assert row["duplicateKey"] == ungm.duplicate_key_for("316942")

    def test_skips_individual_consultant_past_deadline_and_known_keys(self, monkeypatch):
        ungm, _ = self._stub(monkeypatch, [[
            _ungm_notice("keep"),
            _ungm_notice("indiv", ntype="Call for individual consultant"),
            _ungm_notice("expired", deadline_in_days=-3),
            _ungm_notice("seen"),
        ]])

        rows = ungm.fetch_opportunities(days_back=7, skip_keys={ungm.duplicate_key_for("seen")})

        assert [r["duplicateKey"] for r in rows] == [ungm.duplicate_key_for("keep")]

    def test_stops_at_cutoff_without_asking_for_more_pages(self, monkeypatch):
        page0 = [_ungm_notice(str(i)) for i in range(14)] + [_ungm_notice("old", published_days_ago=40)]
        ungm, asked = self._stub(monkeypatch, [page0, [_ungm_notice("newer")]])

        rows = ungm.fetch_opportunities(days_back=7)

        assert len(rows) == 14 and asked == [0]

    def test_pages_until_a_short_page(self, monkeypatch):
        full = [_ungm_notice(f"a{i}") for i in range(15)]
        ungm, asked = self._stub(monkeypatch, [full, [_ungm_notice("b1")]])

        rows = ungm.fetch_opportunities(days_back=7)

        assert len(rows) == 16 and asked == [0, 1]

    def test_tripwire_writes_nothing_when_markup_changes(self, monkeypatch):
        from email_pipeline import fetch_ungm as ungm

        monkeypatch.setattr(ungm, "_throttle", lambda: None)
        monkeypatch.setattr(ungm, "_open_session", lambda: (None, "token"))
        monkeypatch.setattr(ungm, "_search_page",
                            lambda s, t, p, f: "<div class='somethingElse'>no rows here</div>")

        assert ungm.fetch_opportunities(days_back=7) == []

    def test_limit_and_max_new_rows_cap_output(self, monkeypatch):
        ungm, _ = self._stub(monkeypatch, [[_ungm_notice(f"n{i}") for i in range(15)]])
        monkeypatch.setattr(ungm, "MAX_NEW_ROWS", 3)

        assert len(ungm.fetch_opportunities(days_back=7, limit=2)) == 2
        assert len(ungm.fetch_opportunities(days_back=7)) == 3

    def test_session_failure_returns_nothing(self, monkeypatch):
        from email_pipeline import fetch_ungm as ungm

        def boom():
            raise RuntimeError("no token")

        monkeypatch.setattr(ungm, "_open_session", boom)
        assert ungm.fetch_opportunities(days_back=7) == []


# CDMX renders each tender as a bootstrap card; an empty field shows up as two
# adjacent labels (verified live 2026-10-05).
CDMX_CARD_TEMPLATE = """
<div class="card">
  <div class="card-header text-white gray-07 fs-14 fw-bold">{title}</div>
  <div class="card-body d-flex flex-column">
    <span>Fecha de publicación</span><span>{published}</span>
    <span>Presentación de propuestas</span><span>{deadline} 10:00</span>
    <span>Tipo de contratación</span><span>{ctype}</span>
    <span>Carácter</span><span>Nacional</span>
    <span>Método de contratación</span><span>LP - Licitación Pública</span>
    <span>Entidad convocante</span><span>{entity}</span>
    <a href="https://concursodigital.finanzas.cdmx.gob.mx/proveedores/detalle_convocatoria/{id}">Revisar</a>
  </div>
</div>
"""


def _cdmx_html(cards):
    return "<div class='convocatorias_publicas'>" + "".join(
        CDMX_CARD_TEMPLATE.format(**c) for c in cards
    ) + "</div>"


def _cdmx_card(cid, title, published_days_ago=1, deadline_in_days=10,
               ctype="Prestación de Servicios", entity="SECRETARÍA DE GOBIERNO"):
    from datetime import date, timedelta
    today = date.today()
    return {
        "id": cid,
        "title": title,
        "published": (today - timedelta(days=published_days_ago)).isoformat(),
        "deadline": (today + timedelta(days=deadline_in_days)).isoformat(),
        "ctype": ctype,
        "entity": entity,
    }


class TestCdmxFetch:
    """
    CDMX is the one keyword-filtered source: the portal is mostly goods and
    maintenance, so only consulting/research/evaluation titles are ingested.
    Network is stubbed out.
    """

    def _stub(self, monkeypatch, cards):
        from email_pipeline import fetch_cdmx as cdmx

        monkeypatch.setattr(cdmx, "_throttle", lambda: None)

        class FakeSession(dict):
            pass

        def fake_open():
            s = FakeSession()
            s.__dict__["_cdmx_list_html"] = _cdmx_html(cards)
            return s

        monkeypatch.setattr(cdmx, "_open_session", fake_open)
        monkeypatch.setattr(cdmx, "_fetch_detail",
                            lambda session, url: ("Objeto de la contratación: ...", ["a.pdf", "anexo_tecnico.pdf"]))
        return cdmx

    def test_keeps_consulting_titles_and_drops_the_rest(self, monkeypatch):
        cdmx = self._stub(monkeypatch, [
            _cdmx_card("c1", "Servicios de consultoría estratégica para estudios de política pública"),
            _cdmx_card("c2", "SERVICIO DE MANTENIMIENTO DE ARBOLADO Y ÁREAS VERDES"),
            _cdmx_card("c3", "ADQUISICIÓN DE UNIFORMES Y MATERIAL DIDACTICO", ctype="Adquisición de Bienes"),
            _cdmx_card("c4", "SERVICIO INTEGRAL DE ESTUDIOS DE COLPOSCOPIA"),
            _cdmx_card("c5", "Servicio para realizar el diagnóstico del perfil del visitante"),
        ])

        rows = cdmx.fetch_opportunities(days_back=7)

        assert [r["duplicateKey"] for r in rows] == [
            cdmx.duplicate_key_for("c1"), cdmx.duplicate_key_for("c5"),
        ]

    def test_row_mapping(self, monkeypatch):
        cdmx = self._stub(monkeypatch, [_cdmx_card("NjMwOA==", "Consultoría para evaluación de programas")])

        row = cdmx.fetch_opportunities(days_back=7)[0]

        assert row["source"] == "CDMX" and row["countryRegion"] == "Mexico"
        assert row["url"].endswith("/detalle_convocatoria/NjMwOA==")
        assert row["donorClient"] == "SECRETARÍA DE GOBIERNO"
        assert len(row["deadlineISO"]) == 10
        assert row["resourceLinks"] == "a.pdf|anexo_tecnico.pdf"
        assert row["torText"].startswith("Objeto de la contratación")

    def test_skips_old_expired_and_known_rows(self, monkeypatch):
        cdmx = self._stub(monkeypatch, [
            _cdmx_card("keep", "Consultoría para evaluación de programas"),
            _cdmx_card("old", "Consultoría para evaluación de programas", published_days_ago=30),
            _cdmx_card("expired", "Consultoría para evaluación de programas", deadline_in_days=-2),
            _cdmx_card("seen", "Consultoría para evaluación de programas"),
        ])

        rows = cdmx.fetch_opportunities(days_back=7, skip_keys={cdmx.duplicate_key_for("seen")})

        assert [r["duplicateKey"] for r in rows] == [cdmx.duplicate_key_for("keep")]

    def test_blank_publication_date_is_not_read_as_the_next_label(self, monkeypatch):
        from email_pipeline import fetch_cdmx as cdmx

        card = _cdmx_card("c1", "Consultoría para evaluación")
        card["published"] = ""
        parsed = cdmx._parse_cards(_cdmx_html([card]))

        assert parsed[0]["published"] == ""
        assert parsed[0]["deadline"].startswith("20")

    def test_tripwire_writes_nothing_when_markup_changes(self, monkeypatch):
        from email_pipeline import fetch_cdmx as cdmx

        monkeypatch.setattr(cdmx, "_throttle", lambda: None)

        class FakeSession(dict):
            pass

        def fake_open():
            s = FakeSession()
            s.__dict__["_cdmx_list_html"] = "<div class='nothing-here'></div>"
            return s

        monkeypatch.setattr(cdmx, "_open_session", fake_open)
        assert cdmx.fetch_opportunities(days_back=7) == []

    def test_limit_caps_output(self, monkeypatch):
        cdmx = self._stub(monkeypatch, [
            _cdmx_card(f"c{i}", "Consultoría para evaluación de programas") for i in range(6)
        ])

        assert len(cdmx.fetch_opportunities(days_back=7, limit=2)) == 2
