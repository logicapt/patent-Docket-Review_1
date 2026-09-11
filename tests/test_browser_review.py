"""Offline fixtures only; no case-page downloads, credentials or paid requests."""
import copy
from contextlib import contextmanager
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import app
from core.browser_session import BrowserAccessError, access_check, browser_session
from core.browser_sources import SOURCES, docket_date, google_url, parse_source, search_candidates, source_url
from core.evidence import verify_identity
from core.proxy_manager import ProxyManager
from core.service_triage import analyze_docket, latest_entries

CASE = {"case_number": "1:25-cv-01337", "plaintiff": "Alpha Labs LLC", "defendants": "Beta Systems Inc", "court": "ded"}
URL = "https://ai-lab.exparte.com/case/dct/ded/1%3A25-cv-01337/alpha-v-beta"


def fixture(descriptions=None):
    descriptions = descriptions or ["Administrative notice."] * 7 + ["Invalidity contentions due on 10/01/2026.", "Claim construction briefing schedule."]
    rows = "".join(f'<tr><td>09/{i:02d}/26</td><td>{i}</td><td>09/{i:02d}/2026</td><td>{description}</td></tr>' for i, description in enumerate(descriptions, 1))
    return ('<html><head><title>Alpha v Beta</title></head><body><h2>1:25-cv-01337</h2>'
        '<h1>Alpha Labs LLC v. Beta Systems Inc</h1><dl><dt>Court</dt><dd>D. Delaware</dd>'
        '<dt>Citation</dt><dd>2026 Example 123 (synthetic)</dd></dl><p>Checked at 09/09/26 3:43 PM</p>'
        '<table><thead><tr><th>Entered</th><th>Dkt</th><th>Filed</th><th>Description</th></tr></thead>'
        '<tbody>' + rows + '</tbody></table></body></html>')


def evidence(descriptions=None):
    parsed = parse_source(fixture(descriptions), URL)
    parsed["identity_match"] = verify_identity(CASE["case_number"], CASE["court"], CASE["plaintiff"], CASE["defendants"], parsed)
    return parsed


class ParserTests(unittest.TestCase):
    def test_dates_number_update_citation_and_caption_preserved(self):
        parsed = evidence()
        self.assertTrue(parsed["identity_match"]["verified"])
        self.assertEqual(parsed["source_last_updated"], "09/09/26 3:43 PM")
        self.assertEqual(parsed["source_update_label"], "Checked at")
        self.assertEqual(parsed["citation"], "2026 Example 123 (synthetic)")
        self.assertEqual(parsed["docket_entries"][-1]["date_entered"], "2026-09-09")
        self.assertEqual(parsed["docket_entries"][-1]["date_filed"], "2026-09-09")
        self.assertIn("10/01/2026", parsed["docket_entries"][-2]["description"])

    def test_filing_entered_and_unlabelled_date_are_separate(self):
        html = fixture().replace("<td>09/09/2026</td>", "<td>09/08/2026</td>")
        last = parse_source(html, URL)["docket_entries"][-1]
        self.assertEqual(last["date_filed"], "2026-09-08")
        self.assertEqual(last["date_entered"], "2026-09-09")
        self.assertEqual(last["docket_date"], "")
        self.assertEqual(last["date"], "2026-09-09")

    def test_generic_layout_supported_on_all_source_hosts(self):
        urls = [URL, "https://www.courtlistener.com/docket/12345/alpha-v-beta/",
                "https://unicourt.com/case/pc-db5-alpha-beta-12345",
                "https://www.law360.com/cases/12345"]
        for url in urls:
            self.assertEqual(len(parse_source(fixture(), url)["docket_entries"]), 9)

    def test_role_tables_override_caption(self):
        html = fixture().replace("</body>", '<table><tr><td>Plaintiff</td><td>Beta Systems Inc</td></tr><tr><td>Defendant</td><td>Alpha Labs LLC</td></tr></table></body>')
        parsed = parse_source(html, URL)
        self.assertFalse(verify_identity(CASE["case_number"], "ded", CASE["plaintiff"], CASE["defendants"], parsed)["verified"])

    def test_description_dates_cannot_replace_invalid_row_dates(self):
        html = fixture(["Order signed 09/09/2026. Response due 10/01/2026."] * 7)
        html = html.replace("<td>09/07/26</td>", "<td>02/30/26</td>").replace("<td>09/07/2026</td>", "<td></td>")
        parsed = parse_source(html, URL)
        self.assertEqual(len(parsed["docket_entries"]), 6)
        self.assertEqual(parsed["undated_entries_excluded"], 1)
        self.assertEqual(docket_date("Order signed 09/09/2026"), "")

    def test_no_case_identity_is_inferred_from_url(self):
        parsed = parse_source('<h1>Alpha Labs LLC v. Beta Systems Inc</h1>', URL)
        self.assertEqual(parsed["case_number"], "")

    def test_pacermonitor_headings_remain_docket_dates(self):
        from test_verification import HTML as PM_HTML, URL as PM_URL
        parsed = parse_source(PM_HTML, PM_URL)
        self.assertTrue(parsed["docket_entries"])
        entry = next(e for e in parsed["docket_entries"] if e["entry_number"] == "37")
        self.assertEqual(entry["docket_date"], "2026-03-24")
        self.assertEqual(entry["date_filed"], "")
        self.assertEqual(entry["date_entered"], "")

    def test_scripts_and_forms_are_not_evidence(self):
        parsed = parse_source(fixture().replace("</body>", '<script>SECRET_TOKEN</script><form>SECRET_PASSWORD</form></body>'), URL)
        self.assertNotIn("SECRET", str(parsed))

    def test_courtlistener_v1_and_v2_rows_exclude_attachment_text(self):
        v1 = '<h1 data-type="search.Docket">Alpha Labs LLC v. Beta Systems Inc (1:25-cv-01337)</h1><h2>District of Delaware</h2><div id="docket-entry-table"><div class="row" id="entry-20"><div><p>20</p></div><div><p>Sept. 9, 2026</p></div><div><p>Claim construction order.</p><div class="recap-documents">Attachment signed 10/01/2026</div></div></div></div>'
        v2 = '<hgroup><h1 data-type="search.Docket">Alpha Labs LLC v. Beta Systems Inc (1:25-cv-01337)</h1><p>District of Delaware</p></hgroup><ol aria-label="Docket entries"><li id="entry-20"><dl><div><dt>Document Number</dt><dd>20</dd></div><div><dt>Date Filed</dt><dd><time datetime="2026-09-09">Sept. 9, 2026</time></dd></div><div><dt>Description</dt><dd><p>Claim construction order.</p></dd></div></dl><ul><li>Attachment signed 10/01/2026</li></ul></li></ol>'
        for html in (v1, v2):
            parsed = parse_source(html, "https://www.courtlistener.com/docket/123/alpha-beta/")
            self.assertEqual(parsed["case_name"], "Alpha Labs LLC v. Beta Systems Inc")
            self.assertEqual(parsed["court"], "District of Delaware")
            self.assertEqual(len(parsed["docket_entries"]), 1)
            entry = parsed["docket_entries"][0]
            self.assertEqual(entry["date_filed"], "2026-09-09")
            self.assertEqual(entry["description"], "Claim construction order.")
            self.assertTrue(entry["source_url"].endswith("#entry-20"))


class DiscoveryTests(unittest.TestCase):
    def test_search_uses_all_case_fields_and_selected_sources(self):
        q = parse_qs(urlsplit(google_url(CASE, ["exparte"])).query)["q"][0]
        for term in (CASE["case_number"], CASE["plaintiff"], CASE["defendants"], "ded", "site:ai-lab.exparte.com"):
            self.assertIn(term, q)

    def test_links_unwrapped_deduplicated_allowlisted_and_unverified(self):
        html = f'<a href="/url?q={URL}"><h3>Alpha v Beta</h3></a><a href="{URL}">Duplicate</a><a href="https://evil.example/case/123">Wrong host</a>'
        found = search_candidates(html, ["exparte"])
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["verified"])
        self.assertEqual(search_candidates(html, ["courtlistener"]), [])

    def test_external_urls_ports_credentials_and_files_rejected(self):
        for url in ("http://127.0.0.1/", "https://www.courtlistener.com.evil.test/docket/123/", "https://user:password@unicourt.com/case/123",
                    "https://unicourt.com:8000/case/123", "https://www.courtlistener.com/api/rest/v4/dockets/", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                source_url(url)
        self.assertEqual(source_url(URL + "?session=SECRET#document")[1], URL)
        self.assertEqual(source_url("https://www.courtlistener.com/docket/123/alpha-beta/?order_by=asc&session=SECRET")[1],
                         "https://www.courtlistener.com/docket/123/alpha-beta/?order_by=desc")


class TriageTests(unittest.TestCase):
    def test_selects_latest_seven_with_numeric_same_day_tiebreak(self):
        parsed = evidence()
        latest = latest_entries(parsed["docket_entries"])
        self.assertEqual([e["entry_number"] for e in latest], [str(i) for i in range(9, 2, -1)])
        entries = [dict(latest[0], entry_number=n) for n in ["2", "10", "9"]]
        self.assertEqual([e["entry_number"] for e in latest_entries(entries)], ["10", "9", "2"])

    def test_recommendation_uses_only_selected_window(self):
        parsed = evidence(["Invalidity contentions."] + ["Administrative notice."] * 8)
        self.assertEqual(analyze_docket(parsed)["recommendation"], "INSUFFICIENT_EVIDENCE")

    def test_both_signals_include_evidence_and_never_send(self):
        result = analyze_docket(evidence())
        self.assertEqual(result["recommendation"], "BOTH_FOR_REVIEW")
        self.assertEqual(len(result["documents"]), 2)
        self.assertTrue(all(e["source_url"] == URL for e in result["evidence"]))
        self.assertFalse(result["send_automatically"])
        self.assertTrue(result["human_review_required"])

    def test_insufficient_entries_and_mismatched_case_abstain(self):
        self.assertEqual(analyze_docket(evidence(["Invalidity contentions."] * 5))["documents"], [])
        parsed = evidence(); parsed["identity_match"]["verified"] = False
        self.assertEqual(analyze_docket(parsed)["documents"], [])

    def test_disposition_and_reopening_require_review(self):
        for text in ("ORDER DISMISSING CASE.", "Notice of Settlement", "ORDER staying the case.", "Order reopening case."):
            result = analyze_docket(evidence(["Invalidity contentions."] * 6 + [text]))
            self.assertEqual(result["recommendation"], "REVIEW_STATUS", text)
            self.assertFalse(result["documents"])

    def test_proposals_denials_and_settlement_conference_not_closures(self):
        for text in ("Proposed order dismissing case.", "Motion denied; case not dismissed.", "Notice of settlement conference"):
            self.assertEqual(analyze_docket(evidence(["Invalidity contentions."] * 6 + [text]))["recommendation"], "INVALIDATION_REVIEW")

    def test_conflicting_rows_abstain_and_duplicates_do_not_count(self):
        parsed = evidence()
        parsed["docket_entries"].append(dict(parsed["docket_entries"][-1], description="Conflicting description"))
        self.assertEqual(analyze_docket(parsed)["documents"], [])
        one = evidence(["Invalidity contentions."])["docket_entries"][0]
        parsed["docket_entries"] = [one] * 9
        self.assertEqual(analyze_docket(parsed)["entries_reviewed"], 1)


class BrowserTests(unittest.TestCase):
    def test_block_login_rate_limit_and_waf_are_explicit(self):
        for html, code, status in [("Blocked By WAF", 200, "blocked"), ("<title>Sign In</title>", 200, "login_required"),
                ("", 429, "rate_limited"), ("", 403, "blocked"), ("<title>Just a moment</title>", 200, "blocked")]:
            with self.assertRaises(BrowserAccessError) as caught:
                access_check(html, URL, code)
            self.assertEqual(caught.exception.status, status)

    def test_proxy_credentials_are_separate_and_invalid_config_rejected(self):
        proxy = ProxyManager("http://alice:p%40ss@proxy.example:8080").get_playwright_proxy()
        self.assertEqual(proxy, {"server": "http://proxy.example:8080", "username": "alice", "password": "p@ss"})
        for value in ("http://bad", "http://bad:8080/path", "http://a:80,http://b:80", "socks5://user:pass@host:1080"):
            with self.assertRaises(ValueError):
                ProxyManager(value).get_playwright_proxy()

    def test_context_closed_even_when_parser_fails_and_downloads_disabled(self):
        with patch("playwright.sync_api.sync_playwright") as factory:
            playwright = factory.return_value.__enter__.return_value
            browser = playwright.chromium.launch.return_value
            context = browser.new_context.return_value
            with self.assertRaisesRegex(RuntimeError, "parser failure"):
                with browser_session("http://user:secret@proxy.example:8080"):
                    raise RuntimeError("parser failure")
            browser.new_context.assert_called_once_with(accept_downloads=False, service_workers="block")
            context.close.assert_called_once()
            browser.close.assert_called_once()
            self.assertNotIn("secret", playwright.chromium.launch.call_args.kwargs["proxy"]["server"])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_home_has_new_workflow_and_no_store(self):
        result = self.client.get("/")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIn("Review bot", result.text)
        self.assertNotIn("input-api-token", result.text)
        self.assertEqual(self.client.get("/api/results").status_code, 404)
        self.assertEqual(self.client.get("/api/export/csv").status_code, 404)

    def test_analyze_bounded_entries_no_files_and_no_api_fallback(self):
        with patch("app.browser_session") as session, patch("pathlib.Path.write_text", side_effect=AssertionError("Cannot store data")), patch("requests.Session.request", side_effect=AssertionError("No API or legacy fallback")):
            session.return_value.__enter__.return_value.read.return_value = fixture(), URL
            response = self.client.post("/api/analyze", json={**CASE, "url": URL, "entry_limit": 6})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["case"]["docket_entries"]), 6)
        self.assertEqual(data["visible_entry_count"], 9)
        self.assertEqual(data["analysis"]["recommendation"], "BOTH_FOR_REVIEW")

    def test_mismatch_does_not_return_docket_analysis(self):
        with patch("app.browser_session") as session:
            session.return_value.__enter__.return_value.read.return_value = fixture().replace("Beta Systems", "Gamma Systems"), URL
            data = self.client.post("/api/analyze", json={**CASE, "url": URL}).json()
        self.assertEqual(data["status"], "identity_mismatch")
        self.assertNotIn("analysis", data)

    def test_metadata_only_abstains(self):
        with patch("app.browser_session") as session:
            session.return_value.__enter__.return_value.read.return_value = fixture([]).split("<table>")[0], URL
            data = self.client.post("/api/analyze", json={**CASE, "url": URL}).json()
        self.assertEqual(data["status"], "metadata_only")
        self.assertFalse(data["analysis"]["documents"])

    def test_redirect_and_news_are_not_docket_evidence(self):
        for requested, final, expected in ((URL, "https://evil.example/case/123", "redirected"),
            ("https://www.law360.com/articles/123/alpha-beta", "https://www.law360.com/articles/123/alpha-beta", "context_only")):
            with patch("app.browser_session") as session:
                session.return_value.__enter__.return_value.read.return_value = fixture(), final
                data = self.client.post("/api/analyze", json={**CASE, "url": requested}).json()
            self.assertEqual(data["status"], expected)

    def test_discovery_block_retains_manual_google_links(self):
        with patch("app.browser_session", side_effect=BrowserAccessError("blocked", "Blocked")):
            data = self.client.post("/api/discover", json=CASE).json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(len(data["source_searches"]), 5)
        self.assertTrue(data["google_url"].startswith("https://www.google.com/search?"))
        self.assertFalse(data["candidates"])

    def test_input_limits_proxy_redaction_and_no_external_fetch(self):
        with patch("app.browser_session") as browser:
            for changes in ({"case_number": ""}, {"entry_limit": 8}, {"url": "https://127.0.0.1/"},
                            {"proxy_string": "http://alice:SUPERSECRET@host:badport"}):
                response = self.client.post("/api/analyze", json={**CASE, "url": URL, **changes})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("SUPERSECRET", response.text)
            browser.assert_not_called()
        self.assertEqual(self.client.post("/api/discover", json={**CASE, "plaintiff": "X" * 17000}).status_code, 413)

    def test_cross_site_and_untrusted_host_rejected(self):
        self.assertEqual(self.client.post("/api/discover", json=CASE, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
