import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.batch_processor import BatchProcessor
from core.docket_imports import DocketImportStore, DocketImportError
from core.export_manager import generate_csv
from core.keyword_engine import KeywordEngine
from test_verification import CASE, HTML, URL


class ImportTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="docket-import-test-")
        self.directory = Path(self.temp.name).resolve()
        self.store = DocketImportStore(self.directory)

    def tearDown(self):
        # The generated cleanup target must stay directly below the system temp root.
        self.assertEqual(self.directory.parent, Path(tempfile.gettempdir()).resolve())
        self.temp.cleanup()


class ImportTests(ImportTestBase):
    def test_snapshot_matches_identity_and_preserves_entry_dates(self):
        content = HTML.replace('</head>', f'<meta name="docket-source-url" content="{URL}"></head>').encode()
        imported = self.store.import_html(content, "case.html", CASE)
        self.assertFalse(imported["verified"])
        self.assertTrue(imported["identity_match"]["verified"])
        self.assertEqual(imported["retrieved_at"], "")
        self.assertEqual(imported["docket_url"], URL)
        entries = {e["entry_number"]: e for e in imported["docket_entries"]}
        self.assertEqual(entries["37"]["date_filed"], "2026-03-24")
        self.assertEqual(entries["36"]["date_filed"], "2026-03-23")
        self.assertTrue(self.store.path_for(imported["docket_import_id"]).exists())

    def test_import_does_not_need_an_online_source_url(self):
        imported = self.store.import_html(HTML.encode(), "case.html", CASE)
        case = {**CASE, "docket_import_id": imported["docket_import_id"]}
        scraper = Mock()
        result = BatchProcessor(self.store)._process_case(case, 0, scraper, KeywordEngine(), False)
        scraper.fetch_case_data.assert_not_called()
        self.assertEqual(result["verification_status"], "IMPORTED_MATCHED")
        self.assertFalse(result["source_verified"])
        self.assertEqual(result["trigger_entry_number"], "37")
        self.assertEqual(result["trigger_date"], "2026-03-24")
        self.assertIn("IMPORTED_MATCHED", generate_csv([result]))

    def test_wrong_case_and_party_are_rejected_without_saving(self):
        for html in (HTML.replace("7:25-cv-00406", "7:25-cv-00999"), HTML.replace("Beta Systems", "Other Company")):
            with self.assertRaises(DocketImportError):
                self.store.import_html(html.encode(), "case.html", CASE, URL)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_identity_rechecked_when_reusing_import(self):
        imported = self.store.import_html(HTML.encode(), "case.html", CASE)
        with self.assertRaises(DocketImportError):
            self.store.load(imported["docket_import_id"], {**CASE, "defendants": "Wrong Defendant"})

    def test_script_and_form_contents_not_saved_or_used(self):
        html = HTML.replace('</body>', '<form>PRIVATE-PASSWORD<input value="SECRET"></form><script>COOKIE-SECRET</script></body>')
        imported = self.store.import_html(html.encode(), "case.html", CASE)
        stored = self.store.path_for(imported["docket_import_id"]).read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE-PASSWORD", stored)
        self.assertNotIn("COOKIE-SECRET", stored)

    def test_login_unsupported_and_undated_files_rejected(self):
        for content, name in ((b'<title>Sign in</title>', 'login.html'), (b'%PDF-file', 'docket.pdf'),
                              (b'', 'case.html'), (b'<h1>Alpha Labs LLC v. Beta Systems Inc.</h1>', 'case.html')):
            with self.assertRaises(DocketImportError):
                self.store.import_html(content, name, CASE)

    def test_no_docket_rows_is_actionable_error(self):
        empty = HTML[:HTML.index('<tr><td>40')]
        with self.assertRaisesRegex(DocketImportError, 'no readable dated docket rows'):
            self.store.import_html(empty.encode(), "empty.html", CASE)

    def test_path_traversal_and_external_origin_rejected(self):
        with self.assertRaises(DocketImportError):
            self.store.path_for("../../app.py")
        with self.assertRaises(DocketImportError):
            self.store.import_html(HTML.encode(), "case.html", CASE, "https://evil.example/case/123")


class ImportEndpointTests(ImportTestBase):
    def setUp(self):
        super().setUp()
        import legacy_app as app
        self.app_module = app
        self.old = app.batch_processor, app.current_cases, app.docket_imports
        app.current_cases = [copy.deepcopy(CASE)]
        app.docket_imports = self.store
        app.batch_processor = BatchProcessor(self.store)
        self.client = app.app.test_client()

    def tearDown(self):
        self.app_module.batch_processor, self.app_module.current_cases, self.app_module.docket_imports = self.old
        super().tearDown()

    def upload(self, html=HTML, case_id="1"):
        return self.client.post('/api/import-docket', data={"case_id": case_id, "source_url": URL,
            "file": (io.BytesIO(html.encode()), "saved.html")}, content_type="multipart/form-data")

    def test_import_updates_results_exports_and_reuses_without_network(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["dated_entries"], 4)
        self.assertEqual(result["item"]["verification_status"], "IMPORTED_MATCHED")
        stored = self.client.get('/api/docket-imports/' + result["docket_import_id"] + '/evidence')
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.mimetype, "application/json")
        stored.close()
        self.assertIn(b'IMPORTED_MATCHED', self.client.get('/api/export/csv').data)
        with patch('core.batch_processor.MultiSourceScraper') as scraper:
            self.client.post('/api/start-scan', json={"delay": 0})
            self.app_module.batch_processor.thread.join(5)
            scraper.return_value.fetch_case_data.assert_not_called()
        state = self.client.get('/api/results').get_json()
        self.assertEqual(state["stats"]["imported"], 1)
        self.assertEqual(state["stats"]["source_verified"], 0)

    def test_mismatch_missing_selection_and_busy_scan(self):
        self.assertEqual(self.upload(HTML.replace("Beta Systems", "Other Company")).status_code, 422)
        self.assertEqual(self.upload(case_id="999").status_code, 400)
        self.app_module.batch_processor.is_running = True
        self.assertEqual(self.upload().status_code, 409)
        self.app_module.batch_processor.is_running = False
        self.assertEqual(self.client.get('/api/docket-imports/unknown/evidence').status_code, 404)


if __name__ == '__main__':
    unittest.main()
