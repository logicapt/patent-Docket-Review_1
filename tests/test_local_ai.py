"""Synthetic AI responses exercise orchestration without downloading a model."""
import copy
import io
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app
from core.local_ai import analysis_config, local_ai_review, _run_worker, _model_slot
from core.local_ai_worker import main as run_local_worker
from core.service_triage import analyze_docket
from test_browser_review import CASE, URL, evidence, fixture


def response(**changes):
    return {"summary": "Claim construction and validity topics warrant review.", "suggested_focus": "both",
            "observations": [{"entry_number": "9", "quote": "Claim construction briefing schedule.",
                              "interpretation": "Confirm the claim construction posture before selecting a strategy."}],
            "review_questions": ["Which claims are asserted?"], **changes}


class LocalAIConfigTests(unittest.TestCase):
    def test_no_model_and_bad_path_do_not_expose_configuration(self):
        for path in ("", "https://example.test/SECRET.gguf", "\\\\server\\share\\SECRET.gguf", "relative/SECRET.gguf"):
            with patch.dict(os.environ, {"DOCKET_LOCAL_MODEL_PATH": path}):
                config = analysis_config()
            self.assertFalse(config["available"])
            self.assertNotIn("SECRET", str(config))

    def test_library_required_and_model_not_loaded_on_status_check(self):
        path = os.path.abspath("synthetic-model.gguf")
        with patch.dict(os.environ, {"DOCKET_LOCAL_MODEL_PATH": path}), patch("core.local_ai.Path.is_file", return_value=True), patch("core.local_ai.importlib.util.find_spec", return_value=None):
            self.assertFalse(analysis_config()["available"])
        with patch.dict(os.environ, {"DOCKET_LOCAL_MODEL_PATH": path}), patch("core.local_ai.Path.is_file", return_value=True), patch("core.local_ai.importlib.util.find_spec", return_value=Mock()), patch("core.local_ai.subprocess.Popen") as process:
            self.assertTrue(analysis_config()["available"])
            process.assert_not_called()


class LocalAIReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = evidence()
        self.rules = analyze_docket(self.data)
        self.config = patch("core.local_ai.analysis_config", return_value={"available": True})
        self.config.start()
        self.addCleanup(self.config.stop)

    def review(self, output):
        with patch("core.local_ai._run_worker", return_value=json.dumps(output)):
            return local_ai_review(self.data, self.rules)

    def test_uses_selected_entries_checks_quote_and_attaches_source_reference(self):
        original = copy.deepcopy(self.data)
        original_rules = copy.deepcopy(self.rules)
        with patch("core.local_ai._run_worker", return_value=json.dumps(response())) as worker:
            reviewed = local_ai_review(self.data, self.rules, 6)
        payload = worker.call_args.args[0]
        selected = json.loads(payload["messages"][1]["content"])["entries"]
        self.assertEqual([entry["entry_number"] for entry in selected], ["9", "8", "7", "6", "5", "4"])
        self.assertIn("untrusted", payload["messages"][0]["content"])
        self.assertEqual(reviewed["status"], "complete")
        self.assertEqual(reviewed["observations"][0]["source_url"], URL)
        self.assertEqual(reviewed["observations"][0]["date"], "2026-09-09")
        self.assertFalse(reviewed["differs_from_rules"])
        self.assertEqual(self.data, original)
        self.assertEqual(self.rules, original_rules)

    def test_invented_number_quote_urls_and_wrong_entry_rejected(self):
        original = response()["observations"][0]
        for change in ({"entry_number": "999"}, {"entry_number": "8"}, {"quote": "A fabricated court ruling."},
                       {"source_url": "https://evil.test"}, {"quote": " " * 20}, {"entry_number": "1", "quote": "Administrative notice."}):
            reviewed = self.review(response(observations=[{**original, **change}]))
            self.assertEqual(reviewed["status"], "unavailable")
            self.assertNotIn("summary", reviewed)

    def test_malformed_response_does_not_change_rules_or_echo_case_text(self):
        with patch("core.local_ai._run_worker", return_value="SECRET CASE TEXT"):
            reviewed = local_ai_review(self.data, self.rules)
        self.assertEqual(reviewed["status"], "unavailable")
        self.assertNotIn("SECRET", str(reviewed))
        self.assertEqual(self.rules["recommendation"], "BOTH_FOR_REVIEW")

    def test_ai_disagreement_is_explicit(self):
        reviewed = self.review(response(suggested_focus="insufficient_evidence"))
        self.assertTrue(reviewed["differs_from_rules"])
        self.assertEqual(self.rules["recommendation"], "BOTH_FOR_REVIEW")

    def test_bad_questions_and_invented_decision_rejected(self):
        for change in ({"review_questions": ["X" * 401]}, {"review_questions": [" "]},
                       {"suggested_focus": "send_now"}, {"observations": []}):
            self.assertEqual(self.review(response(**change))["status"], "unavailable")

    def test_unverified_insufficient_conflicting_and_held_cases_skip_model(self):
        cases = [evidence(["Invalidity contentions."] * 5), evidence(["Invalidity contentions."] * 6 + ["Order dismissing case."])]
        wrong = evidence(); wrong["identity_match"]["verified"] = False; cases.append(wrong)
        conflict = evidence(); conflict["docket_entries"].append(dict(conflict["docket_entries"][-1], description="A conflict")); cases.append(conflict)
        with patch("core.local_ai._run_worker") as worker:
            for data in cases:
                self.assertEqual(local_ai_review(data, analyze_docket(data))["status"], "skipped")
            worker.assert_not_called()

    def test_input_is_not_silently_truncated(self):
        self.data["docket_entries"][-1]["description"] = "X" * 17000
        with patch("core.local_ai._run_worker") as worker:
            self.assertEqual(local_ai_review(self.data, self.rules)["status"], "input_too_large")
            worker.assert_not_called()

    def test_setup_missing_timeout_and_busy_are_explicit(self):
        with patch("core.local_ai.analysis_config", return_value={"available": False, "status": "setup_required", "message": "No model"}), patch("core.local_ai._run_worker") as worker:
            self.assertEqual(local_ai_review(self.data, self.rules)["status"], "setup_required")
            worker.assert_not_called()
        with patch("core.local_ai._run_worker", side_effect=subprocess.TimeoutExpired("worker", 120)):
            self.assertEqual(local_ai_review(self.data, self.rules)["status"], "timeout")
        self.assertTrue(_model_slot.acquire(blocking=False))
        try:
            with patch("core.local_ai._run_worker") as worker:
                self.assertEqual(local_ai_review(self.data, self.rules)["status"], "busy")
                worker.assert_not_called()
        finally:
            _model_slot.release()

    def test_worker_uses_pipes_no_shell_and_always_closes_streams(self):
        process = Mock(returncode=0)
        process.communicate.return_value = b'{"summary":"synthetic"}', None
        process.poll.return_value = 0
        with patch("core.local_ai.subprocess.Popen", return_value=process) as start:
            self.assertIn("synthetic", _run_worker({"messages": []}))
        self.assertNotIn("shell", start.call_args.kwargs)
        self.assertNotIn("synthetic", str(start.call_args))
        self.assertEqual(start.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(start.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(start.call_args.kwargs["stdout"], subprocess.PIPE)
        if os.name == "nt":
            self.assertEqual(start.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_worker_killed_on_timeout(self):
        process = Mock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 120), (b"", None)]
        process.poll.return_value = None
        with patch("core.local_ai.subprocess.Popen", return_value=process), self.assertRaises(subprocess.TimeoutExpired):
            _run_worker({})
        process.kill.assert_called_once()
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()


class LocalAIEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_default_review_never_starts_ai_and_mode_validated(self):
        with patch("app.browser_session") as session, patch("app.local_ai_review") as ai:
            session.return_value.__enter__.return_value.read.return_value = fixture(), URL
            response = self.client.post("/api/analyze", json={**CASE, "url": URL})
            self.assertEqual(response.status_code, 200)
            ai.assert_not_called()
            self.assertNotIn("ai_review", response.json()["analysis"])
        self.assertEqual(self.client.post("/api/analyze", json={**CASE, "url": URL, "analysis_mode": "cloud"}).status_code, 422)

    def test_selected_ai_mode_retains_verified_source_and_rule_result(self):
        with patch("app.browser_session") as session, patch("app.local_ai_review", return_value={"status": "setup_required", "message": "No model"}) as ai:
            session.return_value.__enter__.return_value.read.return_value = fixture(), URL
            result = self.client.post("/api/analyze", json={**CASE, "url": URL, "analysis_mode": "local_ai", "entry_limit": 6}).json()
        self.assertTrue(result["success"])
        self.assertEqual(len(result["case"]["docket_entries"]), 6)
        self.assertEqual(result["analysis"]["recommendation"], "BOTH_FOR_REVIEW")
        self.assertEqual(result["analysis"]["ai_review"]["status"], "setup_required")
        self.assertEqual(ai.call_args.args[2], 6)

    def test_config_endpoint_is_not_cached_and_reveals_no_paths(self):
        with patch.dict(os.environ, {"DOCKET_LOCAL_MODEL_PATH": "https://example.test/SECRET.gguf"}):
            result = self.client.get("/api/analysis-config")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertFalse(result.json()["local_ai"]["available"])
        self.assertNotIn("SECRET", result.text)


class LocalAIWorkerTests(unittest.TestCase):
    def run_worker(self, finish_reason="stop", tokens=20):
        library = Mock()
        model = library.Llama.return_value
        model.tokenize.return_value = [1] * tokens
        output = json.dumps(response())
        model.create_chat_completion.return_value = {"choices": [{"finish_reason": finish_reason, "message": {"content": output}}]}
        payload = {"messages": [{"role": "user", "content": "Synthetic case text"}], "schema": {"type": "object"}}
        stdin = Mock(buffer=io.BytesIO(json.dumps(payload).encode()))
        stdout = Mock(buffer=io.BytesIO())
        with patch.dict("sys.modules", {"llama_cpp": library}), patch.dict(os.environ, {"DOCKET_LOCAL_MODEL_PATH": "synthetic.gguf"}), patch("core.local_ai_worker.sys.stdin", stdin), patch("core.local_ai_worker.sys.stdout", stdout):
            status = run_local_worker()
        return status, stdout.buffer.getvalue(), model, library

    def test_library_runs_locally_with_json_schema_and_releases_context(self):
        status, output, model, library = self.run_worker()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["suggested_focus"], "both")
        self.assertEqual(library.Llama.call_args.kwargs["model_path"], "synthetic.gguf")
        self.assertFalse(library.Llama.call_args.kwargs["verbose"])
        self.assertEqual(model.create_chat_completion.call_args.kwargs["response_format"]["type"], "json_object")
        self.assertEqual(model.create_chat_completion.call_args.kwargs["max_tokens"], 1000)
        model.close.assert_called_once()

    def test_truncated_and_oversized_inference_does_not_return_partial_output(self):
        for kwargs in ({"finish_reason": "length"}, {"tokens": 6001}):
            status, output, model, _ = self.run_worker(**kwargs)
            self.assertEqual(status, 1)
            self.assertEqual(output, b"")
            model.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
