"""Tests for the IDB BEO row parser and the Rejected-tab dedupe helper."""
import os
import sys

import gspread

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from email_pipeline.fetch_idb_beo import _parse_rows
from email_pipeline.write_pipeline_sheet import get_rejected_duplicate_keys


# ── _parse_rows ──────────────────────────────────────────────────────────────

BEO_HTML = """
<table>
  <tr>
    <td><a href="/document.cfm?id=EZIDB1-1">B260001</a></td>
    <td>UPDATE Consultoría para evaluar el programa X</td>
    <td>19-Oct-2026 5:00 PM (Washington DC Time)</td>
  </tr>
  <tr><td colspan="3">Sub-Sector:|Education|Operation Country:|Peru</td></tr>
  <tr>
    <td><a href="https://www.iadb.org/document.cfm?id=EZIDB2-2">B260002</a></td>
    <td>Second opportunity</td>
    <td>21-Oct-2026</td>
  </tr>
  <tr>
    <td><a href="/document.cfm?id=EZIDB1-1">B260001</a></td>
    <td>duplicate row, same id</td>
    <td>01-Jan-2026</td>
  </tr>
</table>
"""


def test_parse_rows_extracts_fields_and_dedupes_ids():
    rows = _parse_rows(BEO_HTML)
    assert [r["selectionId"] for r in rows] == ["B260001", "B260002"]

    first = rows[0]
    assert first["opportunityTitle"] == "Consultoría para evaluar el programa X"  # UPDATE prefix stripped
    assert first["url"] == "https://www.iadb.org/document.cfm?id=EZIDB1-1"  # relative -> absolute
    assert first["deadline"] == "19-Oct-2026"
    assert first["deadlineISO"] == "2026-10-19"
    assert first["countryRegion"] == "Peru"  # read from the following metadata row


def test_parse_rows_empty_page():
    assert _parse_rows("<html><body>nothing here</body></html>") == []


# ── get_rejected_duplicate_keys ──────────────────────────────────────────────

class FakeWorksheet:
    def __init__(self, values):
        self._values = values

    def get_all_values(self):
        return self._values


class FakeSheet:
    def __init__(self, tabs=None, error=None):
        self._tabs = tabs or {}
        self._error = error

    def worksheet(self, name):
        if self._error:
            raise self._error
        if name not in self._tabs:
            raise gspread.WorksheetNotFound(name)
        return FakeWorksheet(self._tabs[name])


HEADER = ["rejectedAtUTC", "tab", "source", "title", "url", "fitScore", "reason",
          "agencyOrDonor", "country", "duplicateKey"]


def _row(tab, key):
    return ["2026-10-07T00:00:00+00:00", tab, "IDB BEO", "t", "u", "0", "quick screen", "d", "c", key]


def test_rejected_keys_only_include_pipeline_rows():
    sh = FakeSheet({"Rejected": [HEADER, _row("Pipeline", "idb beo | a"), _row("Opportunities", "noticeid-1"),
                                 _row("Pipeline", "")]})
    assert get_rejected_duplicate_keys(sh) == {"idb beo | a"}


def test_rejected_keys_missing_tab_or_empty_is_empty_set():
    assert get_rejected_duplicate_keys(FakeSheet({})) == set()
    assert get_rejected_duplicate_keys(FakeSheet({"Rejected": [HEADER]})) == set()
    assert get_rejected_duplicate_keys(FakeSheet({"Rejected": [["a", "b"], ["1", "2"]]})) == set()


def test_rejected_keys_read_error_degrades_instead_of_raising():
    assert get_rejected_duplicate_keys(FakeSheet(error=RuntimeError("quota"))) == set()


# ── ToR document handling ────────────────────────────────────────────────────

import io as _io
import zipfile as _zipfile

from email_pipeline.fetch_idb_beo import _document_kind, _extract_docx_text, _is_login_page


def _docx(paragraphs):
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
           + "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
           + "</w:body></w:document>")
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_text_is_extracted():
    body = _docx(["TÉRMINOS DE REFERENCIA", "Objetivo: evaluar el programa &amp; su impacto"])
    assert _extract_docx_text(body) == "TÉRMINOS DE REFERENCIA\nObjetivo: evaluar el programa & su impacto"
    assert _extract_docx_text(b"not a zip") == ""


def test_document_kind_by_bytes_then_header():
    assert _document_kind(b"%PDF-1.7 ...", "application/octet-stream") == "pdf"
    assert _document_kind(_docx(["x"]), "application/octet-stream") == "docx"
    assert _document_kind(b"<html>", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "docx"
    assert _document_kind(b"<!DOCTYPE html>", "text/html; charset=utf-8") == ""


def test_login_redirect_is_recognised():
    assert _is_login_page("https://login.microsoftonline.com/abc/oauth2/authorize?client_id=x")
    assert not _is_login_page("https://ezws.iadb.org/getdocument?docnum=EZIDB000820")
