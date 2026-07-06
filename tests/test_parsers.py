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


DEVEX_FIXTURE = """
<html><body>
<h2>Tenders &amp; Grants</h2>
<table>
  <tr>
    <td>
      <a href="https://www.devex.com/en/opportunity/consulting-services-for-governance-reform-12345">
        Consulting Services for Governance Reform in Ecuador
      </a>
      <br/>Donor: USAID | Country: Ecuador | Deadline: August 15, 2026
    </td>
  </tr>
  <tr>
    <td>
      <a href="https://www.devex.com/en/opportunity/technical-assistance-for-justice-sector-67890">
        Technical Assistance for Justice Sector Strengthening
      </a>
      <br/>Donor: State Department / INL | Country: Mexico
    </td>
  </tr>
</table>
<a href="https://www.devex.com/home">Home</a>
<a href="https://www.devex.com/account/unsubscribe">Unsubscribe</a>
<a href="https://twitter.com/devex">Twitter</a>
</body></html>
"""

DEVELOPMENTAID_FIXTURE = """
<html><body>
<h2>Tender Alert: LAC Contracts</h2>
<h3>Open</h3>
<table>
  <tr>
    <td>
      <a href="https://developmentaid.org/tenders/view/servicios-de-consultoria-para-seguridad-ciudadana-111">
        Servicios de Consultoría para Seguridad Ciudadana en Honduras
      </a>
      <br/>País: Honduras | Deadline: 2026-09-01 | Status: Open
    </td>
  </tr>
  <tr>
    <td>
      <a href="https://developmentaid.org/tenders/view/monitoring-evaluation-learning-melos-222">
        Monitoring, Evaluation, Learning and Sharing (MELoS) for LAC Region
      </a>
      <br/>Country: Latin America | Deadline: 2026-08-30
    </td>
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
