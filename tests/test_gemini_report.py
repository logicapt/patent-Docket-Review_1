"""Synthetic Gemini report tests: no real API key, billing or external case requests."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from google import genai
from google.genai import errors

import app
from core.browser_sources import parse_source
from core.evidence import verify_identity
from core.gemini_report import DEFAULT_GEMINI_MODEL, gemini_report
from core.litigation_report import FACT_NAMES, report_evidence, screen_status, validate_report
from test_browser_review import CASE, URL, evidence, fixture


TRANSFER = "ORDER transferring this case to the District of Delaware. Review the receiving docket."


def transfer_evidence():
    # The decisive transfer is outside the latest seven entries.
    return evidence([TRANSFER] + ["Administrative notice."] * 7 + ["Invalidity contentions discussed."])


def report_json(transfer=True):
    ref = {"evidence_id": "docket:1" if transfer else "docket:9",
           "quote": TRANSFER if transfer else "Claim construction briefing schedule."}
    return {"service_focus": "status_review" if transfer else "both",
            "conclusive_summary": [{"text": "The supplied entry records a transfer order. The receiving docket must be checked." if transfer else "The recent entries mention claim construction and invalidity issues.", "citations": [ref]}],
            "recommended_action_plan": [{"text": "Review the order and verify the receiving docket with counsel." if transfer else "Review the underlying filings before preparing strategy briefs.", "citations": [ref]}],
            "facts": {key: {"value": None, "citations": []} for key in FACT_NAMES},
            "current_stage": {"value": "Transfer recorded in the supplied docket; receiving-court status remains unverified." if transfer else "Claim construction scheduling is mentioned.", "citations": [ref]},
            "contentions_status": {"value": None, "citations": []}}


def sdk_response(data=None, finish="STOP"):
    return SimpleNamespace(text=json.dumps(data if data is not None else report_json()), candidates=[SimpleNamespace(finish_reason=finish)])


class StatusScreeningTests(unittest.TestCase):
    def test_old_transfer_has_priority_over_recent_motion_briefing(self):
        parsed = transfer_evidence()
        audit = screen_status(parsed)
        self.assertEqual(audit["case_status"], "TRANSFERRED")
        self.assertEqual(audit["entries_screened"], 9)
        payload, records, _, selected = report_evidence(parsed)
        self.assertEqual(len(selected), 7)
        self.assertNotIn("docket:1", payload["service_window"])
        self.assertIn("docket:1", records)

    def test_motion_proposal_denial_and_statute_alone_are_not_transfers(self):
        for text in ("Motion to transfer under 28 U.S.C. 1404(a).", "Proposed order transferring this case.",
                     "ORDER denying motion to transfer.", "ORDER granting motion to dismiss denied.",
                     "The case was not transferred."):
            with self.subTest(text=text):
                self.assertEqual(screen_status(evidence([text] * 7))["case_status"], "NOT_ESTABLISHED")

    def test_reopening_and_vacatur_supersede_old_disposition(self):
        for latest, status in (("ORDER reopening this case.", "REOPENED"), ("ORDER vacating the transfer order.", "REVIEW_REQUIRED")):
            parsed = evidence([TRANSFER] + ["Administrative notice."] * 7 + [latest])
            self.assertEqual(screen_status(parsed)["case_status"], status)

    def test_administrative_closure_in_transfer_entry_does_not_become_dismissal(self):
        parsed = evidence([TRANSFER + " The case is closed."] * 7)
        self.assertEqual(screen_status(parsed)["case_status"], "TRANSFERRED")

    def test_source_status_label_without_order_requires_review(self):
        parsed = evidence()
        parsed["report_metadata"] = [{"label": "Case Status", "value": "Transferred"}]
        self.assertEqual(screen_status(parsed)["case_status"], "REVIEW_REQUIRED")


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.parsed = transfer_evidence()
        _, self.records, self.audit, self.selected = report_evidence(self.parsed)

    def validate(self, data):
        return validate_report(json.dumps(data), self.parsed, self.records, self.audit, self.selected)

    def test_required_report_fields_provenance_and_unknown_facts(self):
        result = self.validate(report_json())
        self.assertEqual(result["case_status"], "TRANSFERRED")
        self.assertEqual(result["strategic_action"], "Analyze Transfer & Venue Defense")
        self.assertEqual(result["case_facts"]["case_number"], CASE["case_number"])
        self.assertIsNone(result["case_facts"]["filing_date"]["value"])
        self.assertIsNone(result["parties"]["plaintiff_type"]["value"])
        self.assertFalse(result["send_automatically"])
        ref = result["conclusive_summary"][0]["citations"][0]
        self.assertEqual(ref["source_url"], URL)
        self.assertEqual(ref["date"], "2026-09-01")

    def test_invented_quote_number_and_missing_fact_citations_rejected(self):
        edits = [lambda d: d["conclusive_summary"][0]["citations"][0].update(quote="This case is closed forever."),
                 lambda d: d["conclusive_summary"][0]["citations"][0].update(evidence_id="docket:999"),
                 lambda d: d["facts"]["presiding_judge"].update(value="Invented Judge"),
                 lambda d: d["conclusive_summary"][0]["citations"].clear(),
                 lambda d: d.update(case_status="ACTIVE"),
                 lambda d: d.update(service_focus="invalidation")]
        for edit in edits:
            data = report_json(); edit(data)
            with self.assertRaises(ValueError):
                self.validate(data)

    def test_literal_metadata_can_supply_judge_but_docket_dates_are_not_case_dates(self):
        html = fixture().replace("</dl>", "<dt>Presiding Judge</dt><dd>Example Judge</dd><dt>Filing Date</dt><dd>September 1, 2025</dd></dl>")
        parsed = parse_source(html, URL)
        self.assertIn({"label": "Presiding Judge", "value": "Example Judge"}, parsed["report_metadata"])
        self.assertIn({"label": "Filing Date", "value": "September 1, 2025"}, parsed["report_metadata"])
        self.assertFalse(parse_source(fixture(), URL)["report_metadata"], "Docket column dates leaked into case metadata")
        self.records["label:judge"] = {"evidence_id": "label:judge", "text": "Presiding Judge: Example Judge", "source_url": URL}
        data = report_json()
        data["facts"]["presiding_judge"] = {"value": "Example Judge", "citations": [{"evidence_id": "label:judge", "quote": "Presiding Judge: Example Judge"}]}
        self.assertEqual(self.validate(data)["case_facts"]["presiding_judge"]["value"], "Example Judge")

    def test_filing_date_cannot_be_an_approximate_case_number_year(self):
        self.records["meta:case_number"]["text"] = "Case number: 1:2025-cv-01337"
        data = report_json()
        data["facts"]["filing_date"] = {"value": "2025", "citations": [{"evidence_id": "meta:case_number", "quote": "Case number: 1:2025-cv-01337"}]}
        with self.assertRaises(ValueError):
            self.validate(data)

    def test_transfer_never_uses_originating_termination_date_as_merits_disposition(self):
        self.records["meta:date_terminated"] = {"evidence_id": "meta:date_terminated", "text": "2026-09-01", "source_url": URL}
        data = report_json()
        data["facts"]["disposition_date"] = {"value": "2026-09-01", "citations": [{"evidence_id": "meta:date_terminated", "quote": "2026-09-01"}]}
        self.assertIsNone(self.validate(data)["case_facts"]["disposition_date"]["value"])

    def test_transfer_statute_cannot_be_added_without_source_support(self):
        data = report_json()
        data["conclusive_summary"][0]["text"] = "The case was transferred under 28 U.S.C. § 1404."
        with self.assertRaises(ValueError):
            self.validate(data)


class GeminiRequestTests(unittest.TestCase):
    def test_single_sdk_call_structured_schema_secret_separation_and_cleanup(self):
        with patch("core.gemini_report.genai.Client") as factory:
            client = factory.return_value.__enter__.return_value
            client.models.generate_content.return_value = sdk_response()
            result = gemini_report(transfer_evidence(), "FAKE_TEST_KEY")
        self.assertEqual(result["status"], "complete", result)
        factory.return_value.__exit__.assert_called_once()
        call = client.models.generate_content.call_args
        self.assertEqual(call.kwargs["model"], DEFAULT_GEMINI_MODEL)
        self.assertNotIn("FAKE_TEST_KEY", call.kwargs["contents"])
        self.assertNotIn("FAKE_TEST_KEY", str(result))
        self.assertEqual(call.kwargs["config"].response_mime_type, "application/json")
        self.assertIsNone(call.kwargs["config"].tools)
        self.assertEqual(factory.call_args.kwargs["http_options"].retry_options.attempts, 1)

    def test_real_sdk_serializes_request_with_mock_http_transport(self):
        real_client = genai.Client
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(report_json())}]}, "finishReason": "STOP"}]})
        def client_factory(**kwargs):
            kwargs["http_options"].client_args["transport"] = httpx.MockTransport(transport)
            return real_client(**kwargs)
        with patch("core.gemini_report.genai.Client", side_effect=client_factory):
            result = gemini_report(transfer_evidence(), "FAKE_TEST_KEY")
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "generativelanguage.googleapis.com")
        self.assertNotIn("FAKE_TEST_KEY", str(requests[0].url))
        body = json.loads(requests[0].content)
        self.assertIn("responseJsonSchema", body["generationConfig"])
        self.assertNotIn("tools", body)

    def test_key_missing_mismatch_no_rows_conflict_and_oversize_make_no_call(self):
        with patch("core.gemini_report.genai.Client") as client:
            self.assertEqual(gemini_report(evidence(), "")["status"], "setup_required")
            parsed = evidence(); parsed["identity_match"]["verified"] = False
            self.assertEqual(gemini_report(parsed, "FAKE")["status"], "skipped")
            parsed = evidence(); parsed["docket_entries"] = []
            self.assertEqual(gemini_report(parsed, "FAKE")["status"], "skipped")
            parsed = evidence(); parsed["docket_entries"].append({**parsed["docket_entries"][-1], "description": "Different description"})
            self.assertEqual(gemini_report(parsed, "FAKE")["status"], "skipped")
            parsed = evidence(); parsed["docket_entries"][-1]["description"] = "X" * 100001
            self.assertEqual(gemini_report(parsed, "FAKE")["status"], "input_too_large")
        client.assert_not_called()

    def test_auth_quota_timeout_and_bad_output_do_not_echo_secrets(self):
        for exception, status in ((errors.ClientError(403, {"error": {"message": "SECRET_TOKEN"}}), "provider_error"),
                                  (errors.ClientError(429, {"error": {"message": "SECRET_TOKEN"}}), "provider_error"),
                                  (httpx.ReadTimeout("SECRET_TOKEN"), "timeout"), (ValueError("SECRET_TOKEN"), "invalid_report")):
            with self.subTest(status=status), patch("core.gemini_report.genai.Client") as factory:
                factory.return_value.__enter__.return_value.models.generate_content.side_effect = exception
                result = gemini_report(evidence(), "SECRET_TOKEN")
                self.assertEqual(result["status"], status)
                self.assertNotIn("SECRET_TOKEN", str(result))
                factory.return_value.__exit__.assert_called_once()

    def test_truncation_is_not_a_completed_report(self):
        with patch("core.gemini_report.genai.Client") as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = sdk_response(finish="MAX_TOKENS")
            self.assertEqual(gemini_report(transfer_evidence(), "FAKE")["status"], "incomplete")

    def test_status_report_can_use_fewer_than_six_entries_without_service_outreach(self):
        parsed = evidence([TRANSFER])
        with patch("core.gemini_report.genai.Client") as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = sdk_response()
            result = gemini_report(parsed, "FAKE")
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["service_focus"], "status_review")
        self.assertEqual(result["entries_reviewed"], 1)


class GeminiEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_paste_can_generate_report_without_browser_and_keeps_origin(self):
        html = fixture([TRANSFER] + ["Administrative notice."] * 8)
        with patch("app.browser_session", side_effect=AssertionError("No browser")), patch("core.gemini_report.genai.Client") as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = sdk_response()
            response = self.client.post("/api/analyze-paste", json={**CASE, "url": URL, "pasted_html": html, "analysis_mode": "gemini", "gemini_api_key": "FAKE_TEST_KEY"})
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()["analysis"]["litigation_report"]
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(report["evidence_origin"], "user_paste")
        self.assertNotIn("FAKE_TEST_KEY", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_rules_mode_and_blocked_case_never_send_to_gemini(self):
        from core.browser_session import BrowserAccessError
        with patch("app.gemini_report") as gemini, patch("app.browser_session") as browser:
            browser.return_value.__enter__.return_value.read.return_value = fixture(), URL
            self.client.post("/api/analyze", json={**CASE, "url": URL, "analysis_mode": "rules", "gemini_api_key": "FAKE"})
            browser.side_effect = BrowserAccessError("blocked", "Blocked")
            data = self.client.post("/api/analyze", json={**CASE, "url": URL, "analysis_mode": "gemini", "gemini_api_key": "FAKE"}).json()
        self.assertEqual(data["status"], "blocked")
        gemini.assert_not_called()

    def test_invalid_model_key_limits_and_repr_do_not_leak_key(self):
        data = app.AnalyzeRequest(**CASE, url=URL, gemini_api_key="SECRET_TOKEN")
        self.assertNotIn("SECRET_TOKEN", repr(data))
        self.assertEqual(set(data.identity()), set(CASE))
        for extra in ({"gemini_model": "https://evil.example"}, {"gemini_api_key": "SECRET_TOKEN" * 100}):
            response = self.client.post("/api/analyze", json={**CASE, "url": URL, **extra})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("SECRET_TOKEN", response.text)


if __name__ == "__main__":
    unittest.main()
