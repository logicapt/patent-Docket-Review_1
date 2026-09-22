"""Structured docket extraction using Crawl4AI.

Two extraction modes:
  1. Schema-based  (JsonCssExtractionStrategy) -- fast, no LLM cost.
     Preferred for sources with stable DOM layouts (CourtListener, Ex Parte).
  2. LLM-based     (LLMExtractionStrategy)     -- for irregular layouts.
     Only activated when schema extraction yields no entries AND a Gemini key
     is available.  Uses the same key already supplied by the user.

XHR / fetch interception is handled separately by `browser_session.py`; this
module only processes the captured payloads once they have been collected.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

try:
    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy, LLMExtractionStrategy
except ImportError:
    JsonCssExtractionStrategy = None
    LLMExtractionStrategy = None

# ---------------------------------------------------------------------------
# Per-source CSS schemas for JsonCssExtractionStrategy
# ---------------------------------------------------------------------------

#: Selectors checked against CourtListener public de_list.html and
#: cotton/docket_entry_rows.html templates (legacy + current layout).
_COURTLISTENER_SCHEMA: dict[str, Any] = {
    "name": "DocketEntries",
    "baseSelector": (
        "#docket-entry-table > .row[id^='entry-'], "
        "ol[aria-label='Docket entries'] > li[id^='entry-']"
    ),
    "fields": [
        {
            "name": "entry_number",
            "selector": "[id]",
            "type": "attribute",
            "attribute": "id",
        },
        {
            "name": "date_filed",
            "selector": "div p, dt + dd",
            "type": "text",
        },
        {
            "name": "description",
            "selector": "div:nth-child(3) p, dl dd:last-of-type",
            "type": "text",
        },
    ],
}

#: Ex Parte AI Lab uses semantic ARIA grid / table structures.
_EXPARTE_SCHEMA: dict[str, Any] = {
    "name": "DocketEntries",
    "baseSelector": "[role='row']:not([role='columnheader'])",
    "fields": [
        {"name": "entry_number", "selector": "[role='cell']:nth-child(1)", "type": "text"},
        {"name": "date_filed",   "selector": "[role='cell']:nth-child(2)", "type": "text"},
        {"name": "description",  "selector": "[role='cell']:last-child",   "type": "text"},
    ],
}

#: Generic schema for UniCourt, PacerMonitor, Law360 standard HTML tables.
_GENERIC_TABLE_SCHEMA: dict[str, Any] = {
    "name": "DocketEntries",
    "baseSelector": "table tr:not(:first-child)",
    "fields": [
        {"name": "entry_number", "selector": "td:nth-child(1)", "type": "text"},
        {"name": "date_filed",   "selector": "td:nth-child(2)", "type": "text"},
        {"name": "description",  "selector": "td:last-child",   "type": "text"},
    ],
}

#: Lookup from source key to extraction schema.
SOURCE_SCHEMAS: dict[str, dict[str, Any]] = {
    "courtlistener": _COURTLISTENER_SCHEMA,
    "exparte":       _EXPARTE_SCHEMA,
    "unicourt":      _GENERIC_TABLE_SCHEMA,
    "pacermonitor":  _GENERIC_TABLE_SCHEMA,
    "law360":        _GENERIC_TABLE_SCHEMA,
}


# ---------------------------------------------------------------------------
# Schema-based extraction (no LLM, no network call)
# ---------------------------------------------------------------------------

def extract_with_schema(html: str, source_key: str) -> list[dict[str, str]]:
    """Extract docket entries from already-fetched HTML using CSS selectors.

    Uses Crawl4AI JsonCssExtractionStrategy in offline mode (passing html
    directly) so it never opens a new network connection.

    Returns a list of raw entry dicts with keys: entry_number, date_filed,
    description (all str, possibly empty). The caller normalises dates.
    """
    if JsonCssExtractionStrategy is None:
        logger.warning("crawl4ai not installed - falling back to BeautifulSoup parser")
        return []

    schema = SOURCE_SCHEMAS.get(source_key, _GENERIC_TABLE_SCHEMA)
    strategy = JsonCssExtractionStrategy(schema, verbose=False)

    try:
        raw = strategy.extract(url="", html=html)
        items: list[dict] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception as exc:
        logger.debug("Crawl4AI schema extraction failed for %s: %s", source_key, exc)
        return []

    entries = []
    for item in items:
        entry_number = _clean(item.get("entry_number", ""))
        # Strip the "entry-" prefix that CourtListener puts on element IDs.
        entry_number = re.sub(r"^entry-", "", entry_number)
        description = _clean(item.get("description", ""))
        date_filed = _clean(item.get("date_filed", ""))
        if description:
            entries.append(
                {"entry_number": entry_number, "date_filed": date_filed,
                 "date_entered": "", "description": description,
                 "date_basis": f"crawl4ai/{source_key} schema"}
            )
    return entries


def _clean(value: object) -> str:
    """Collapse whitespace and strip a value to a plain string."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


# ---------------------------------------------------------------------------
# XHR / fetch JSON interception helpers
# ---------------------------------------------------------------------------

def find_json_docket_entries(payloads: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Scan intercepted XHR/fetch JSON payloads for docket entries.

    Court sites like UniCourt and Ex Parte AI Lab load their docket data via
    internal REST calls. browser_session.py collects these payloads; this
    function finds the one that looks like a docket entry list and normalises
    it into the format expected by parse_source().

    Returns an empty list if nothing recognisable is found.
    """
    docket_keys = {
        "docketEntries", "docket_entries", "entries", "results",
        "data", "items", "rows",
    }
    date_keys = {"dateFiled", "date_filed", "filedDate", "date", "enteredDate", "dateEntered"}
    desc_keys = {"description", "docketText", "docket_text", "text", "content", "entry"}
    num_keys  = {"entryNumber", "entry_number", "dktNo", "documentNumber", "number", "seq"}

    for payload in payloads:
        entry_list: list | None = None
        for key in docket_keys:
            candidate = payload.get(key)
            if isinstance(candidate, list) and candidate:
                entry_list = candidate
                break
        # Handle {"data": {"docketEntries": [...]}} nesting (UniCourt pattern).
        if entry_list is None:
            data = payload.get("data")
            if isinstance(data, dict):
                for key in docket_keys:
                    candidate = data.get(key)
                    if isinstance(candidate, list) and candidate:
                        entry_list = candidate
                        break

        if not entry_list:
            continue

        first = entry_list[0] if entry_list else {}
        if not isinstance(first, dict):
            continue
        if not any(k in first for k in desc_keys):
            continue

        entries: list[dict[str, str]] = []
        for row in entry_list:
            if not isinstance(row, dict):
                continue
            description = _clean(_pick(row, desc_keys))
            if not description:
                continue
            entry_number = _clean(_pick(row, num_keys))
            date_filed   = _clean(_pick(row, date_keys))
            entries.append(
                {"entry_number": entry_number, "date_filed": date_filed,
                 "date_entered": "", "description": description,
                 "date_basis": "intercepted XHR/fetch JSON"}
            )
        if entries:
            return entries

    return []


def _pick(row: dict, keys: set[str]) -> str:
    """Return the first matching key value from row, or an empty string."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            return str(v)
    return ""


# ---------------------------------------------------------------------------
# LLM-based extraction (optional, uses the user Gemini key)
# ---------------------------------------------------------------------------

def extract_with_llm(html: str, gemini_api_key: str, source_key: str) -> list[dict[str, str]]:
    """Use Crawl4AI LLMExtractionStrategy with the user Gemini key.

    Only called when schema extraction produces no entries AND a key is
    available. Sends a portion of the page HTML to Gemini.

    Returns normalised entry dicts or an empty list on any failure.
    """
    if not gemini_api_key or LLMExtractionStrategy is None:
        return []

    try:
        from pydantic import BaseModel

        class DocketEntry(BaseModel):
            entry_number: str = ""
            date_filed: str = ""
            description: str

        strategy = LLMExtractionStrategy(
            provider="gemini/gemini-2.0-flash",
            api_token=gemini_api_key,
            schema=DocketEntry.model_json_schema(),
            extraction_type="schema",
            instruction=(
                "Extract all court docket entries from this page. "
                "Each entry has an entry_number (e.g. '42'), a date_filed "
                "(ISO format preferred), and a description of the docket event. "
                "Return only entries that have a non-empty description."
            ),
            verbose=False,
        )
        raw = strategy.extract(url="", html=html[:80_000])
        items: list[dict] = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return [
            {"entry_number": _clean(r.get("entry_number", "")),
             "date_filed":   _clean(r.get("date_filed", "")),
             "date_entered": "",
             "description":  _clean(r.get("description", "")),
             "date_basis":   "crawl4ai/llm"}
            for r in items if _clean(r.get("description", ""))
        ]
    except Exception as exc:
        logger.debug("Crawl4AI LLM extraction failed for %s: %s", source_key, exc)
        return []
