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

    def test_language_detection(self):
        assert infer_language("Servicios de consultoría para fortalecimiento") == "Spanish"
        assert infer_language("Governance Reform Technical Assistance") == "English"

    def test_normalize_removes_punctuation(self):
        assert normalize_text("Hello, World! Test.") == "hello world test"
