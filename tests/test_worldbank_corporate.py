"""Tests for the WBG corporate procurement (WBG eProcure) fetcher -- no network."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from email_pipeline import fetch_worldbank_corporate as wc

FUTURE = (date.today() + timedelta(days=10)).isoformat() + "T04:59:00.000+0000"
PAST = (date.today() - timedelta(days=2)).isoformat() + "T04:59:00.000+0000"


def ad(num, title, text, deadline=FUTURE, ad_id=None):
    return {
        "id": ad_id or int(num[-4:]),
        "procurementNumber": num,
        "procurementTitle": title,
        "publicationDate": "2026-11-01T05:00:00.000+0000",
        "eoiDeadline": deadline,
        "text": text,
        # present in the real feed; must never reach the sheet
        "createdBy": {"userProfile": {"email": "staff@worldbank.org", "fullName": "Some Staff"}},
    }


INLINE = ("<p>Assignment Title: X</p><p>Assignment\r\nCountries: <b>Brazil</b></p>"
          "<p><b>ASSIGNMENT DESCRIPTION</b></p><p>IFC seeks a consulting firm to assess SME finance.</p>")
LISTED = ("<p>Assignment Countries: </p><ul><li>Kenya</li></ul>"
          "<p><b>ASSIGNMENT DESCRIPTION</b></p><p>Training for women in business.</p>")
GLOBAL = ("<p>Assignment Countries: </p><p>- Not Country Related</p>"
          "<p><b>ASSIGNMENT DESCRIPTION</b></p><p>Peer review of a flooding methodology.</p>")
NO_FIELD_LAC = "<p>Design alternatives for the BRT stations of the Montevideo Metropolitan Transport System.</p>"
NO_FIELD_OTHER = "<p>The Department of Transportation of the Republic of the Philippines is undertaking a PPP.</p>"
NO_FIELD_UNKNOWN = "<p>Development of a regulatory framework for independent power transmission.</p>"


def _run(monkeypatch, ads, **kw):
    monkeypatch.setattr(wc, "_get_ads", lambda: ads)
    return wc.fetch_opportunities(**kw)


def test_parse_countries_layouts():
    assert wc.parse_countries(wc._plain(INLINE)) == "Brazil"
    assert wc.parse_countries(wc._plain(LISTED)) == "Kenya"
    assert wc.parse_countries(wc._plain(GLOBAL)) == ""
    assert wc.parse_countries(wc._plain(NO_FIELD_LAC)) == ""


def test_geography_rules(monkeypatch):
    rows = _run(monkeypatch, [
        ad("0000000001", "SME Financing Diagnostic", INLINE),
        ad("0000000002", "Business training - Kenya", LISTED),
        ad("0000000003", "Flooding methodology peer review", GLOBAL),
        ad("0000000004", "Design of BRT Pre-Boarding Stations", NO_FIELD_LAC),
        ad("0000000005", "Legal Consultant - Airport PPP", NO_FIELD_OTHER),
        ad("0000000006", "Framework for independent power transmission", NO_FIELD_UNKNOWN),
    ])
    kept = {r["status"] for r in rows}
    # Brazil, global, Montevideo (city only), and unknown are kept; Kenya and Philippines are not
    assert kept == {"0000000001", "0000000003", "0000000004", "0000000006"}


def test_row_shape_and_no_staff_data(monkeypatch):
    (row,) = _run(monkeypatch, [ad("0002024264", "Brazil SME Financing Diagnostic", INLINE, ad_id=7491)])
    assert row["source"] == "WBG Corporate"
    assert row["donorClient"] == "World Bank Group"
    assert row["countryRegion"] == "Brazil"
    assert row["url"] == "https://wbgeprocure-rfxnow.worldbank.org/rfxnow/public/advertisement/7491/view.html"
    assert row["deadlineISO"] == FUTURE[:10]
    assert row["torText"].startswith("IFC seeks a consulting firm")
    assert row["duplicateKey"] == wc.duplicate_key_for("0002024264")
    assert "staff@worldbank.org" not in repr(row) and "Some Staff" not in repr(row)


def test_past_deadline_and_already_ingested_are_skipped(monkeypatch):
    rows = _run(monkeypatch, [
        ad("0000000010", "Brazil study", INLINE, deadline=PAST),
        ad("0000000011", "Brazil study 2", INLINE),
        ad("0000000012", "Brazil study 3", INLINE),
    ], skip_keys={wc.duplicate_key_for("0000000012")})
    assert [r["status"] for r in rows] == ["0000000011"]


def test_tripwire_on_format_change(monkeypatch):
    assert _run(monkeypatch, [{"id": 1, "title": "renamed fields"}]) == []


def test_feed_error_returns_nothing(monkeypatch):
    def boom():
        raise ValueError("no advertisementList in response")
    monkeypatch.setattr(wc, "_get_ads", boom)
    assert wc.fetch_opportunities() == []
