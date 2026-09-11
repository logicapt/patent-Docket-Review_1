"""Offline regression tests: synthetic fixtures, no live credentials or charges."""
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

import requests
from openpyxl import load_workbook

from core.batch_processor import BatchProcessor
from core.court_listener import CourtListenerClient
from core.evidence import verify_identity, parse_date
from core.excel_parser import normalize_case_number, normalize_court_id, map_column_names
from core.export_manager import generate_csv, generate_enriched_excel
from core.keyword_engine import KeywordEngine
from core.multi_source_scraper import MultiSourceScraper
from core.pacer_monitor import PacerMonitorClient

URL = "https://www.pacermonitor.com/public/case/12345/Alpha_v_Beta"
CASE = {"case_number": "7:25-cv-00406", "court_code": "txwd", "plaintiff": "Alpha Labs LLC",
        "defendants": "Beta Systems Inc.", "date_filed_input": "2025-09-04", "id": 1}
HTML = """
<html><head><title>Alpha Labs v. Beta Systems</title></head><body>
<h1>Alpha Labs LLC v. Beta Systems, Inc.</h1><h2>Texas Western District Court</h2>
<table><tr><td>Case #:</td><td>7:25-cv-00406</td></tr>
<tr><td>Case Filed:</td><td>Sep 04, 2025</td></tr>
<tr><td>Terminated:</td><td>Mar 24, 2026</td></tr></table>
<table><tr><td>Defendant <strong>Beta Systems, Inc.</strong></td><td>Represented By Alpha Labs LLC</td></tr>
<tr><td>Plaintiff <strong>Alpha Labs LLC</strong></td><td>Represented By Another Lawyer</td></tr></table>
<table>
<tr><td>40</td><td></td><td>Undated order signed on 04/01/2026.</td></tr>
<tr><td colspan="3">Tuesday, March 24, 2026</td></tr>
<tr><td>38</td><td></td><td><span>Report on Patent/Trademark</span><br>Report sent.</td></tr>
<tr><td>37</td><td></td><td><span class="docket-text">ORDER DISMISSING CASE. Signed on 3/23/2026. Response due 4/20/2026.</span></td></tr>
<tr><td colspan="3">Monday, March 23, 2026</td></tr>
<tr><td>36</td><td></td><td>STIPULATION of Dismissal by Alpha Labs LLC.</td></tr>
<tr><td colspan="3">Thursday, September 04, 2025</td></tr>
<tr><td>1</td><td></td><td>COMPLAINT for Patent Infringement.</td></tr>
</table></body></html>
"""


def response(text="", status=200, url=URL, data=None):
    result = Mock(status_code=status, text=text, url=url, headers={})
    result.json.return_value = data or {}
    return result


def verified_result(source="PacerMonitor", url=URL):
    parsed = PacerMonitorClient.parse_case_page(HTML, url)
    parsed.update({"found": True, "verified": True, "source_display": source,
                   "identity_match": verify_identity(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"], parsed)})
    if source != "PacerMonitor":
        parsed["pacermonitor_url"] = ""
    for entry in parsed["docket_entries"]:
        entry["source"] = source
        entry["source_url"] = url
    return parsed


class IdentityTests(unittest.TestCase):
    def identity(self, **changes):
        meta = PacerMonitorClient.parse_case_page(HTML, URL)
        meta.update(changes)
        return verify_identity(CASE["case_number"], CASE["court_code"], CASE["plaintiff"], CASE["defendants"], meta)

    def test_number_parties_and_court_match(self):
        self.assertTrue(self.identity()["verified"])

    def test_wrong_defendant_rejected_even_when_plaintiff_is_attorney(self):
        self.assertFalse(self.identity(defendants=["Other LLC"])["verified"])

    def test_wrong_office_year_serial_or_court_rejected(self):
        for num in ("1:25-cv-00406", "7:24-cv-00406", "7:25-cv-00407"):
            self.assertFalse(self.identity(case_number=num)["verified"])
        self.assertFalse(self.identity(court="Texas Eastern District Court")["verified"])

    def test_reversed_parties_rejected(self):
        self.assertFalse(self.identity(plaintiffs=["Beta Systems Inc."], defendants=["Alpha Labs LLC"])["verified"])

    def test_missing_party_rejected(self):
        meta = PacerMonitorClient.parse_case_page(HTML, URL)
        self.assertFalse(verify_identity(CASE["case_number"], "txwd", "", CASE["defendants"], meta)["verified"])

    def test_leading_zero_and_corporate_punctuation_variations(self):
        self.assertTrue(self.identity(case_number="7:25-cv-406-JUDGE", defendants=["Beta Systems Incorporated"])["verified"])

    def test_courts_and_url_mapping(self):
        self.assertEqual(normalize_case_number("1-25-cv-01337")["base"], "1:25-cv-01337")
        self.assertEqual(normalize_court_id("California Northern District Court"), "cand")
        self.assertEqual(normalize_court_id("Western District of Virginia"), "vawd")
        mapping = map_column_names(["Docket Link", "Case Number", "Plaintiff", "Defendents"])
        self.assertEqual(mapping["pacermonitor_url"], "Docket Link")
        self.assertEqual(mapping["case_number"], "Case Number")


class PacerMonitorTests(unittest.TestCase):
    def test_dates_stay_with_rows(self):
        parsed = PacerMonitorClient.parse_case_page(HTML, URL)
        entries = {str(e["entry_number"]): e for e in parsed["docket_entries"]}
        self.assertEqual(entries["37"]["date_filed"], "2026-03-24")
        self.assertEqual(entries["36"]["date_filed"], "2026-03-23")
        self.assertIn("Response due 4/20/2026.", entries["37"]["description"])
        self.assertEqual(parsed["date_terminated"], "2026-03-24")
        self.assertEqual(len(parsed["undated_entries"]), 1)
        self.assertEqual(parsed["undated_entries"][0]["entry_number"], "40")
        self.assertEqual(len(parsed["page_sha256"]), 64)

    def test_invalid_date_not_guessed(self):
        parsed = PacerMonitorClient.parse_case_page(HTML.replace("Tuesday, March 24, 2026", "Tuesday, February 30, 2026"), URL)
        self.assertNotIn("37", [str(e["entry_number"]) for e in parsed["docket_entries"]])
        self.assertEqual(parse_date("Terminated"), "")

    def test_metadata_date_and_other_tables_cannot_date_an_entry(self):
        html = HTML.replace("Sep 04, 2025", "Thursday, September 04, 2025")
        html = html.replace("</body>", '<table><tr><td>99</td><td>ORDER DISMISSING CASE.</td></tr></table></body>')
        parsed = PacerMonitorClient.parse_case_page(html, URL)
        undated_numbers = [str(e["entry_number"]) for e in parsed["undated_entries"]]
        self.assertIn("40", undated_numbers)
        self.assertIn("99", undated_numbers)

    def test_blocked_search_does_not_prevent_direct_case_access(self):
        client = PacerMonitorClient()
        client.session.request = Mock(side_effect=[response("Denied", status=403), response(HTML)])
        self.assertFalse(client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])["found"])
        self.assertTrue(client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"], URL)["verified"])

    def test_search_checks_all_candidates_and_both_parties(self):
        client = PacerMonitorClient()
        wrong_url = URL.replace("12345", "99999")
        client._request = Mock(side_effect=[
            response('<input name="_csrf" value="test">'),
            response(f'<a href="{wrong_url}">Decoy</a><a href="{URL}">Correct</a>'),
            response(HTML.replace("Beta Systems", "Wrong Company"), url=wrong_url),
            response(HTML),
        ])
        result = client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["docket_url"], URL)
        self.assertEqual(len(result["docket_entries"]), 4)

    def test_ambiguous_candidates_rejected(self):
        client = PacerMonitorClient()
        client._request = Mock(side_effect=[response(""), response(
            f'<a href="{URL}">One</a><a href="{URL.replace("12345", "99999")}">Two</a>'), response(HTML), response(HTML)])
        self.assertFalse(client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])["found"])

    def test_no_results_does_not_become_found(self):
        client = PacerMonitorClient()
        client._request = Mock(return_value=response("<p>No results</p>"))
        result = client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])
        self.assertFalse(result["found"])
        self.assertEqual(result["docket_entries"], [])

    def test_direct_url_still_checks_identity(self):
        client = PacerMonitorClient()
        client._request = Mock(return_value=response(HTML.replace("Beta Systems", "Wrong Company")))
        self.assertFalse(client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"], URL)["found"])
        client._request.assert_called_once()

    def test_blocked_and_rate_limited_requests_not_retried_for_every_case(self):
        for code in (403, 429):
            client = PacerMonitorClient()
            client.session.request = Mock(return_value=response("Denied", status=code))
            first = client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])
            second = client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])
            self.assertFalse(first["found"])
            self.assertIn(str(code), second["error"])
            client.session.request.assert_called_once()

    def test_login_page_is_not_a_case(self):
        client = PacerMonitorClient()
        client.session.request = Mock(return_value=response('<title>Sign in</title><input type="password">'))
        self.assertFalse(client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"], URL)["found"])

    def test_external_urls_rejected_before_fetch(self):
        client = PacerMonitorClient()
        client._request = Mock()
        for url in ("http://127.0.0.1/", "https://evil.example/public/case/1/Case",
                    "https://www.pacermonitor.com@evil.example/public/case/1/Case"):
            result = client.search_case(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"], url)
            self.assertFalse(result["found"])
        client._request.assert_not_called()

    def test_cookie_is_scoped_and_not_raw_header(self):
        client = PacerMonitorClient(session_cookie="pm_session=secret")
        self.assertNotIn("Cookie", client.session.headers)
        self.assertEqual(next(iter(client.session.cookies)).domain, "www.pacermonitor.com")


class EvidenceTests(unittest.TestCase):
    def test_disposition_quote_not_first_entry_and_not_ai(self):
        processor = BatchProcessor()
        scraper = Mock()
        scraper.fetch_case_data.return_value = verified_result()
        item = processor._process_case(CASE, 0, scraper, KeywordEngine(), False)
        self.assertTrue(item["source_verified"])
        self.assertEqual(item["trigger_entry_number"], "37")
        self.assertEqual(item["trigger_date"], "2026-03-24")
        self.assertTrue(item["trigger_entry"].startswith("ORDER DISMISSING CASE"))
        self.assertFalse(item["gemini_verified"])

    def test_termination_date_is_separate_from_trigger(self):
        evidence = verified_result()
        evidence["date_terminated"] = "2026-03-25"
        scraper = Mock(fetch_case_data=Mock(return_value=evidence))
        item = BatchProcessor()._process_case(CASE, 0, scraper, KeywordEngine(), False)
        self.assertEqual(item["date_terminated"], "2026-03-25")
        self.assertEqual(item["trigger_date"], "2026-03-24")

    def test_live_failure_never_fabricates_data(self):
        scraper = Mock(fetch_case_data=Mock(return_value={"found": False, "error": "HTTP 403"}))
        item = BatchProcessor()._process_case(CASE, 0, scraper, KeywordEngine(), False)
        self.assertEqual(item["verification_status"], "UNVERIFIED")
        for field in ("trigger_entry", "trigger_date", "date_terminated"):
            self.assertEqual(item[field], "")
        self.assertEqual(item["docket_entries"], [])
        self.assertEqual(item["status_category"], "UNKNOWN")
        scraper.fetch_case_data.assert_called_once()

    def test_metadata_only_is_not_verified_docket(self):
        evidence = verified_result()
        evidence["docket_entries"] = []
        item = BatchProcessor()._process_case(CASE, 0, Mock(fetch_case_data=Mock(return_value=evidence)), KeywordEngine(), False)
        self.assertEqual(item["verification_status"], "METADATA_ONLY")
        self.assertEqual(item["trigger_entry"], "")
        self.assertFalse(item["source_verified"])

    def test_demo_is_explicit_and_never_verified(self):
        scraper = Mock()
        item = BatchProcessor()._process_case(CASE, 0, scraper, KeywordEngine(), True)
        self.assertEqual(item["verification_status"], "DEMO")
        self.assertFalse(item["source_verified"])
        self.assertFalse(item["gemini_verified"])
        self.assertTrue(item["status_display"].startswith("DEMO:"))
        scraper.fetch_case_data.assert_not_called()

    def test_settlement_conference_and_proposed_dismissal_not_dispositions(self):
        for text in ("Settlement conference scheduled on 4/20/2026.",
                     "PROPOSED ORDER DISMISSING CASE.", "Case is not dismissed.",
                     "If the parties do not respond, this case will be dismissed.",
                     "NOTICE OF SETTLEMENT: no settlement reached.",
                     "ORDER denying motion to dismiss."):
            result = KeywordEngine().evaluate_case_disposition("", "", [{"date_filed": "2026-03-01", "description": text}])
            self.assertEqual(result["disposition_category"], "UNKNOWN", text)
            self.assertEqual(result["date_terminated"], "")

    def test_settlement_notice_does_not_invent_closure_date(self):
        result = KeywordEngine().evaluate_case_disposition("", "", [{"date_filed": "2026-03-01", "description": "NOTICE OF SETTLEMENT."}])
        self.assertEqual(result["disposition_category"], "SETTLEMENT_REPORTED")
        self.assertEqual(result["date_terminated"], "")

    def test_latest_decisive_event_and_date_sort(self):
        entries = [{"date_filed": "2026-03-01", "description": "ORDER DISMISSING CASE."},
                   {"date_filed": "2026-04-01", "description": "ORDER REOPENING CASE."},
                   {"date_filed": "2025-01-01", "description": "COMPLAINT."}]
        result = KeywordEngine().evaluate_case_disposition("", "2026-03-01", entries)
        self.assertEqual(result["disposition_category"], "REOPENED")
        self.assertEqual(result["trigger_date"], "2026-04-01")

    def test_invalidity_schedule_is_not_service(self):
        result = KeywordEngine().evaluate_case_disposition("", "", [{
            "date_filed": "2026-03-01", "description": "SCHEDULING ORDER: invalidity contentions must be served by April 20."}])
        self.assertFalse(result["has_invalidity_contentions"])


class FallbackAndExportTests(unittest.TestCase):
    def test_fallback_keeps_its_own_source_and_url(self):
        scraper = MultiSourceScraper()
        scraper.pacer_monitor.search_case = Mock(return_value={"found": False, "error": "HTTP 403"})
        cl_url = "https://www.courtlistener.com/docket/12/"
        scraper.court_listener.search_docket = Mock(return_value=verified_result("CourtListener / RECAP", cl_url))
        item = BatchProcessor()._process_case(CASE, 0, scraper, KeywordEngine(), False)
        self.assertEqual(item["source_used"], "CourtListener / RECAP")
        self.assertEqual(item["trigger_source_url"], cl_url)
        self.assertEqual(item["pacermonitor_url"], "")
        self.assertIn("403", item["source_diagnostics"][0]["error"])

    def test_courtlistener_paginates_entries(self):
        client = CourtListenerClient()
        docket = {"id": 12, "docket_number": CASE["case_number"], "case_name": "Alpha Labs LLC v. Beta Systems Inc.",
                  "court": "https://www.courtlistener.com/api/rest/v4/courts/txwd/", "date_terminated": "2026-03-24"}
        client.session.get = Mock(side_effect=[
            response(data={"results": [docket], "next": None}),
            response(data={"results": [{"entry_number": 2, "date_filed": "2026-03-24", "description": "ORDER DISMISSING CASE."}],
                           "next": client.BASE_URL + "/docket-entries/?page=2"}),
            response(data={"results": [{"entry_number": 1, "date_filed": "2025-01-01", "description": "COMPLAINT."}], "next": None}),
        ])
        result = client.search_docket(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])
        self.assertTrue(result["verified"])
        self.assertEqual(len(result["docket_entries"]), 2)

    def test_courtlistener_wrong_defendant_is_rejected(self):
        client = CourtListenerClient()
        client.session.get = Mock(return_value=response(data={"results": [{
            "id": 12, "docket_number": CASE["case_number"], "case_name": "Alpha Labs LLC v. Wrong Company",
            "court": "txwd"}]}))
        self.assertFalse(client.search_docket(CASE["case_number"], "txwd", CASE["plaintiff"], CASE["defendants"])["found"])

    def test_exports_preserve_verification_and_dates(self):
        item = BatchProcessor()._process_case(CASE, 0, Mock(fetch_case_data=Mock(return_value=verified_result())), KeywordEngine(), False)
        csv = generate_csv([item])
        self.assertIn("SOURCE_VERIFIED", csv)
        self.assertNotIn("PacerMonitor Verified", csv)
        workbook = load_workbook(generate_enriched_excel([item]))
        rows = list(workbook.active.values)
        exported = dict(zip(rows[0], rows[1]))
        self.assertEqual(exported["Trigger Date"], "2026-03-24")
        self.assertEqual(exported["Trigger Entry Number"], "37")
        self.assertEqual(exported["Docket Source Link"], URL)
        unavailable = BatchProcessor()._process_case(CASE, 0, Mock(fetch_case_data=Mock(return_value={"found": False})), KeywordEngine(), False)
        self.assertIn("UNVERIFIED", generate_csv([unavailable]))


class ServerTests(unittest.TestCase):
    def setUp(self):
        import legacy_app as app
        self.module = app
        self.client = app.app.test_client()
        self.original_processor = app.batch_processor
        app.batch_processor = BatchProcessor()

    def tearDown(self):
        self.module.batch_processor = self.original_processor

    def test_home_defaults_to_live(self):
        result = self.client.get("/")
        self.assertEqual(result.status_code, 200)
        html = result.get_data(as_text=True)
        self.assertNotIn('id="chk-demo-mode" class="sr-only peer" checked', html)

    def test_invalid_boolean_and_delay_rejected(self):
        for extra in ({"demo_mode": "false"}, {"delay": -1}, {"delay": "nan"}, {"delay": "bad"}):
            result = self.client.post("/api/start-scan", json={"cases": [CASE], **extra})
            self.assertEqual(result.status_code, 400)

    def test_demo_and_exports(self):
        result = self.client.post("/api/start-scan", json={"cases": [CASE], "demo_mode": True, "delay": 0})
        self.assertEqual(result.status_code, 200)
        self.module.batch_processor.thread.join(5)
        items = self.client.get("/api/results").get_json()["results"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["verification_status"], "DEMO")
        self.assertEqual(self.client.get("/api/export/excel").status_code, 200)
        self.assertEqual(self.client.get("/api/export/csv").status_code, 200)

    def test_live_scan_without_credentials_still_fetches_and_failure_not_active(self):
        with patch("core.batch_processor.MultiSourceScraper") as factory:
            factory.return_value.fetch_case_data.return_value = {"found": False, "error": "blocked"}
            self.client.post("/api/start-scan", json={"cases": [CASE], "demo_mode": False, "delay": 0})
            self.module.batch_processor.thread.join(5)
            factory.return_value.fetch_case_data.assert_called_once()
        results = self.client.get("/api/results").get_json()
        self.assertEqual(results["results"][0]["verification_status"], "UNVERIFIED")
        self.assertEqual(results["stats"]["active"], 0)
        self.assertEqual(results["stats"]["unmatched"], 1)


if __name__ == "__main__":
    unittest.main()
