"""Tests for core.crawl4ai_extractor — all offline, no browser or network needed."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the project root is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table_html(rows: list[tuple[str, str, str]]) -> str:
    """Build a minimal HTML docket table for testing."""
    header = "<tr><th>No.</th><th>Date Filed</th><th>Description</th></tr>"
    body = "".join(
        f"<tr><td>{n}</td><td>{d}</td><td>{desc}</td></tr>"
        for n, d, desc in rows
    )
    return f"<html><body><table>{header}{body}</table></body></html>"


# ---------------------------------------------------------------------------
# extract_with_schema — mocked Crawl4AI
# ---------------------------------------------------------------------------

class TestExtractWithSchema:
    """extract_with_schema wraps Crawl4AI JsonCssExtractionStrategy.extract()."""

    def test_returns_entries_on_success(self):
        fake_items = [
            {"entry_number": "1", "date_filed": "2024-01-15", "description": "Complaint filed"},
            {"entry_number": "2", "date_filed": "2024-02-01", "description": "Answer filed"},
        ]
        mock_strategy = MagicMock()
        mock_strategy.extract.return_value = json.dumps(fake_items)

        with patch("core.crawl4ai_extractor.JsonCssExtractionStrategy", return_value=mock_strategy):
            from core.crawl4ai_extractor import extract_with_schema
            result = extract_with_schema("<html>...</html>", "courtlistener")

        assert len(result) == 2
        assert result[0]["description"] == "Complaint filed"
        assert result[0]["date_basis"] == "crawl4ai/courtlistener schema"

    def test_strips_entry_prefix(self):
        """CourtListener puts 'entry-42' on element IDs; we strip the prefix."""
        fake_items = [{"entry_number": "entry-42", "date_filed": "2024-03-01", "description": "Motion filed"}]
        mock_strategy = MagicMock()
        mock_strategy.extract.return_value = json.dumps(fake_items)

        with patch("core.crawl4ai_extractor.JsonCssExtractionStrategy", return_value=mock_strategy):
            from core.crawl4ai_extractor import extract_with_schema
            result = extract_with_schema("<html/>", "courtlistener")

        assert result[0]["entry_number"] == "42"

    def test_skips_entries_with_no_description(self):
        fake_items = [
            {"entry_number": "1", "date_filed": "2024-01-01", "description": ""},
            {"entry_number": "2", "date_filed": "2024-01-02", "description": "Real entry"},
        ]
        mock_strategy = MagicMock()
        mock_strategy.extract.return_value = json.dumps(fake_items)

        with patch("core.crawl4ai_extractor.JsonCssExtractionStrategy", return_value=mock_strategy):
            from core.crawl4ai_extractor import extract_with_schema
            result = extract_with_schema("<html/>", "unicourt")

        assert len(result) == 1
        assert result[0]["description"] == "Real entry"

    def test_returns_empty_on_crawl4ai_import_error(self):
        with patch("core.crawl4ai_extractor.JsonCssExtractionStrategy", None):
            from core.crawl4ai_extractor import extract_with_schema
            result = extract_with_schema("<html/>", "courtlistener")
        assert result == []


    def test_returns_empty_on_extraction_exception(self):
        mock_strategy = MagicMock()
        mock_strategy.extract.side_effect = RuntimeError("boom")

        with patch("core.crawl4ai_extractor.JsonCssExtractionStrategy", return_value=mock_strategy):
            from core.crawl4ai_extractor import extract_with_schema
            result = extract_with_schema("<html/>", "exparte")

        assert result == []


# ---------------------------------------------------------------------------
# find_json_docket_entries — no mocking needed
# ---------------------------------------------------------------------------

class TestFindJsonDocketEntries:
    """find_json_docket_entries scans intercepted XHR payloads."""

    def test_finds_top_level_entries(self):
        from core.crawl4ai_extractor import find_json_docket_entries

        payload = {"docketEntries": [
            {"entryNumber": "10", "dateFiled": "2024-05-01", "description": "Order granting motion"},
            {"entryNumber": "11", "dateFiled": "2024-05-10", "description": "Notice of appeal"},
        ]}
        result = find_json_docket_entries([payload])
        assert len(result) == 2
        assert result[0]["entry_number"] == "10"
        assert result[1]["description"] == "Notice of appeal"
        assert result[0]["date_basis"] == "intercepted XHR/fetch JSON"

    def test_finds_nested_unicourt_pattern(self):
        from core.crawl4ai_extractor import find_json_docket_entries

        payload = {"data": {"docketEntries": [
            {"number": "5", "date_filed": "2024-04-01", "docketText": "Summons issued"},
        ]}}
        result = find_json_docket_entries([payload])
        assert len(result) == 1
        assert result[0]["description"] == "Summons issued"

    def test_skips_irrelevant_payloads(self):
        from core.crawl4ai_extractor import find_json_docket_entries

        payloads = [
            {"user": {"id": 1, "name": "Alice"}},
            {"analytics": [{"event": "pageview"}]},
        ]
        result = find_json_docket_entries(payloads)
        assert result == []

    def test_empty_input(self):
        from core.crawl4ai_extractor import find_json_docket_entries
        assert find_json_docket_entries([]) == []

    def test_skips_rows_with_no_description(self):
        from core.crawl4ai_extractor import find_json_docket_entries

        payload = {"entries": [
            {"entryNumber": "1", "description": ""},
            {"entryNumber": "2", "description": "Valid entry"},
        ]}
        result = find_json_docket_entries([payload])
        assert len(result) == 1
        assert result[0]["description"] == "Valid entry"

    def test_first_matching_payload_wins(self):
        """When multiple payloads match, the first one is used."""
        from core.crawl4ai_extractor import find_json_docket_entries

        p1 = {"entries": [{"entryNumber": "1", "description": "First payload entry"}]}
        p2 = {"entries": [{"entryNumber": "2", "description": "Second payload entry"}]}
        result = find_json_docket_entries([p1, p2])
        assert result[0]["description"] == "First payload entry"


# ---------------------------------------------------------------------------
# Integration: parse_source honours intercepted_json
# ---------------------------------------------------------------------------

class TestParseSourceWithInterceptedJson:
    """parse_source should use XHR entries when intercepted_json is provided."""

    def test_xhr_entries_appear_in_result(self):
        from core.browser_sources import parse_source

        # Minimal HTML with no docket table.
        html = (
            '<html><head><title>Case 1:24-cv-00001</title></head>'
            '<body><h1>Docket 1:24-cv-00001</h1></body></html>'
        )
        url = "https://www.courtlistener.com/docket/12345/test-case/"
        intercepted = [{"docketEntries": [
            {"entryNumber": "1", "dateFiled": "2024-01-10", "description": "Complaint filed"},
            {"entryNumber": "2", "dateFiled": "2024-02-15", "description": "Answer filed"},
        ]}]

        result = parse_source(html, url, intercepted_json=intercepted)
        descs = [e["description"] for e in result["docket_entries"]]
        assert "Complaint filed" in descs
        assert "Answer filed" in descs

    def test_no_intercepted_json_still_works(self):
        """Backward-compatible: omitting intercepted_json must not break anything."""
        from core.browser_sources import parse_source

        html = '<html><body><h1>Docket 1:24-cv-99999</h1></body></html>'
        url = "https://www.courtlistener.com/docket/99999/no-entries/"
        result = parse_source(html, url)  # no intercepted_json kwarg
        assert "docket_entries" in result
