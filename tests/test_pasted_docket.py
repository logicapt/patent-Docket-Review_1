"""Synthetic clipboard evidence; these tests never read the user's clipboard."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from core.browser_session import BrowserAccessError
from core.evidence import verify_identity
from core.pasted_docket import MAX_PASTE_BYTES, parse_pasted_docket
from test_browser_review import CASE, URL, fixture


def plain_fixture():
    header = ("Alpha Labs LLC v. Beta Systems Inc\n1:25-cv-01337\n"
              "Court: D. Delaware\nCitation: 2026 Example 123 (synthetic)\n"
              "Checked at 09/09/26 3:43 PM\nEntered\tDkt\tFiled\tDescription\n")
    return header + "\n".join(
        f"09/{i:02d}/26\t{i}\t09/{i-1:02d}/2026\tInvalidity contentions due on 10/01/2026."
        for i in range(2, 9))


class PasteParserTests(unittest.TestCase):
    def test_rich_clipboard_preserves_rows_but_never_claims_online_verification(self):
        parsed = parse_pasted_docket(fixture(), "", URL)
        self.assertEqual(len(parsed["docket_entries"]), 9)
        self.assertEqual(parsed["evidence_origin"], "user_paste")
        self.assertFalse(parsed["source_verified"])
        self.assertEqual(parsed["retrieved_at"], "")
        self.assertTrue(parsed["provided_at"])
        self.assertEqual(parsed["source_last_updated"], "09/09/26 3:43 PM")
        self.assertIn("not been independently verified", parsed["coverage"])
        self.assertTrue(all(row["evidence_origin"] == "user_paste" for row in parsed["docket_entries"]))

    def test_plain_columns_preserve_date_roles_and_case_identity(self):
        parsed = parse_pasted_docket("", plain_fixture(), URL)
        match = verify_identity(CASE["case_number"], CASE["court"], CASE["plaintiff"], CASE["defendants"], parsed)
        self.assertTrue(match["verified"], match)
        self.assertEqual(len(parsed["docket_entries"]), 7)
        self.assertEqual(parsed["citation"], "2026 Example 123 (synthetic)")
        self.assertEqual(parsed["docket_entries"][-1]["date_entered"], "2026-09-08")
        self.assertEqual(parsed["docket_entries"][-1]["date_filed"], "2026-09-07")
        self.assertEqual(parsed["docket_entries"][-1]["docket_date"], "")

    def test_hash_number_and_docket_text_headers(self):
        text = plain_fixture().replace("Dkt\tFiled\tDescription", "#\tFiled\tDocket Text")
        self.assertEqual(len(parse_pasted_docket("", text, URL)["docket_entries"]), 7)

    def test_unsupported_plain_layout_does_not_guess_rows_from_prose_dates(self):
        text = plain_fixture().split("Entered\t")[0] + "Docket 8. Invalidity contentions due on 10/01/2026."
        parsed = parse_pasted_docket("", text, URL)
        self.assertEqual(parsed["docket_entries"], [])

    def test_invalid_column_dates_never_replaced_by_deadlines_in_description(self):
        text = plain_fixture().replace("09/08/26\t8\t09/07/2026", "02/30/26\t8\t02/30/2026")
        parsed = parse_pasted_docket("", text, URL)
        self.assertEqual(len(parsed["docket_entries"]), 6)
        self.assertEqual(parsed["undated_entries_excluded"], 1)

    def test_scripts_forms_and_other_active_markup_are_not_evidence(self):
        html = fixture().replace("</body>", '<script>SECRET_SCRIPT</script><form>SECRET_PASSWORD</form><iframe src="https://evil.example/">SECRET_FRAME</iframe></body>')
        self.assertNotIn("SECRET", str(parse_pasted_docket(html, "", URL)))

    def test_plain_text_is_escaped_before_parsing(self):
        text = plain_fixture().replace("Invalidity contentions", "<script>Literal text</script> Invalidity contentions")
        parsed = parse_pasted_docket("", text, URL)
        self.assertIn("<script>Literal text</script>", parsed["docket_entries"][0]["description"])


class PasteEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def post(self, **overrides):
        return self.client.post("/api/analyze-paste", json={**CASE, "url": URL, "pasted_html": fixture(), **overrides})

    def test_analysis_without_browser_network_or_file_storage(self):
        with patch("app.browser_session", side_effect=AssertionError("No source browser")), \
             patch("requests.Session.request", side_effect=AssertionError("No remote requests")), \
             patch("builtins.open", side_effect=AssertionError("No files")), \
             patch("pathlib.Path.write_text", side_effect=AssertionError("No files")):
            response = self.post(entry_limit=6)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "pasted_docket")
        self.assertEqual(data["visible_entry_count"], 9)
        self.assertEqual([e["entry_number"] for e in data["case"]["docket_entries"]], ["9", "8", "7", "6", "5", "4"])
        self.assertEqual(data["analysis"]["recommendation"], "BOTH_FOR_REVIEW")
        self.assertFalse(data["case"]["source_verified"])
        self.assertFalse(data["analysis"]["send_automatically"])
        self.assertIn("User-supplied", data["analysis"]["limitations"][0])
        self.assertNotIn("pasted_html", data)

    def test_wrong_case_is_rejected_even_if_plain_copy_or_url_matches(self):
        data = self.post(pasted_html=fixture().replace("Beta Systems", "Gamma Systems"), pasted_text=plain_fixture()).json()
        self.assertFalse(data["success"])
        self.assertEqual(data["status"], "identity_mismatch")
        self.assertNotIn("analysis", data)

    def test_metadata_only_returns_no_service_documents(self):
        data = self.post(pasted_html="", pasted_text=plain_fixture().split("Entered\t")[0]).json()
        self.assertTrue(data["success"], data)
        self.assertEqual(data["status"], "pasted_metadata_only")
        self.assertEqual(data["analysis"]["documents"], [])

    def test_access_challenge_or_login_copy_is_rejected(self):
        for html in ("<title>Just a moment</title>", "<h1>Verify you are human</h1>", "<title>Sign In</title>"):
            with self.subTest(html=html):
                data = self.post(pasted_html=html).json()
                self.assertEqual(data["status"], "pasted_access_page")
                self.assertFalse(data["success"])
                self.assertNotIn("analysis", data)

    def test_empty_content_and_unsupported_source_rejected(self):
        self.assertEqual(self.post(pasted_html="", pasted_text=" ").status_code, 422)
        self.assertEqual(self.post(url="https://unrelated.example/case/123").status_code, 422)

    def test_paste_limit_is_bounded_separately_from_small_url_requests(self):
        self.assertEqual(self.post(pasted_html=fixture() + " " * 20000).status_code, 200)
        response = self.client.post("/api/analyze-paste", content='{"pasted_html":"' + "x" * MAX_PASTE_BYTES + '"}', headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/analyze", json={**CASE, "url": URL, "pasted_html": "x" * 20000})
        self.assertEqual(response.status_code, 413)

    def test_validation_errors_do_not_echo_pasted_content(self):
        response = self.post(pasted_text="PRIVATE_PASTED_TEXT" * 30000)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE_PASTED_TEXT", response.text)

    def test_cross_site_paste_request_rejected(self):
        response = self.client.post("/api/analyze-paste", json={**CASE, "url": URL, "pasted_html": fixture()}, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)

    def test_source_and_google_blocks_report_stage_and_offer_recovery(self):
        with patch("app.browser_session", side_effect=BrowserAccessError("blocked", "The site refused the automated browser.", 403)):
            source = self.client.post("/api/analyze", json={**CASE, "url": URL}).json()
            search = self.client.post("/api/discover", json=CASE).json()
        self.assertEqual(source["failed_stage"], "case_page")
        self.assertEqual(source["http_status"], 403)
        self.assertEqual(source["open_url"], URL)
        self.assertIn("Paste page content", source["recovery_message"])
        self.assertEqual(search["failed_stage"], "google_search")
        self.assertEqual(search["blocked_source"], "Google")
        self.assertTrue(search["google_url"].startswith("https://www.google.com/search?"))
        self.assertEqual(search["candidates"], [])


if __name__ == "__main__":
    unittest.main()
