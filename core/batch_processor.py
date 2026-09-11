import time
import queue
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from .excel_parser import normalize_court_id, normalize_case_number
from .multi_source_scraper import MultiSourceScraper
from .keyword_engine import KeywordEngine, LEGAL_GLOSSARY

logger = logging.getLogger(__name__)

SIMULATED_DOCKET_TEMPLATES = [
    {
        "disposition": "settlement",
        "has_invalidity": True,
        "entries": [
            "NOTICE OF SERVICE of Defendant’s Preliminary Invalidity Contentions pursuant to Patent Local Rule 3-3 with prior art references.",
            "JOINT CLAIM CONSTRUCTION AND PREHEARING STATEMENT filed by parties.",
            "JOINT STIPULATION OF DISMISSAL WITH PREJUDICE pursuant to confidential settlement agreement and cross-license."
        ],
        "days_offset": 260
    },
    {
        "disposition": "settlement",
        "has_invalidity": True,
        "entries": [
            "SCHEDULING ORDER setting deadlines for invalidity contentions, Markman claim construction, and trial.",
            "CERTIFICATE OF SERVICE of Invalidity and Unenforceability Contentions with 35 U.S.C. 102/103 charts.",
            "NOTICE OF SETTLEMENT: The parties advise the Court that they have reached a global settlement resolving all claims."
        ],
        "days_offset": 190
    },
    {
        "disposition": "dismissed_with_prejudice",
        "has_invalidity": False,
        "entries": [
            "MOTION TO DISMISS for Failure to State a Claim under Fed. R. Civ. P. 12(b)(6) and 35 U.S.C. 101 patent eligibility.",
            "MEMORANDUM OPINION AND ORDER granting Motion to Dismiss. Action dismissed with prejudice."
        ],
        "days_offset": 95
    },
    {
        "disposition": "dismissed_without_prejudice",
        "has_invalidity": False,
        "entries": [
            "MOTION TO TRANSFER VENUE pursuant to 28 U.S.C. 1404(a).",
            "ORDER OF DISMISSAL WITHOUT PREJUDICE with leave to refile."
        ],
        "days_offset": 70
    },
    {
        "disposition": "stayed",
        "has_invalidity": True,
        "entries": [
            "DEFENDANT'S INVALIDITY CONTENTIONS served with 35 U.S.C. 103 obviousness combinations.",
            "ORDER STAYING CASE pending final written decision in Inter Partes Review (IPR2022-00418) before the PTAB."
        ],
        "days_offset": 130
    },
    {
        "disposition": "summary_judgment",
        "has_invalidity": True,
        "entries": [
            "SERVICE of Defendant's Invalidity Contentions under Patent Local Rule 3-3.",
            "MOTION FOR SUMMARY JUDGMENT of Patent Invalidity for lack of patentable subject matter under 35 U.S.C. 101.",
            "ORDER GRANTING IN PART Motion for Summary Judgment."
        ],
        "days_offset": 220
    },
    {
        "disposition": "invalidity_contentions",
        "has_invalidity": True,
        "entries": [
            "ANSWER AND COUNTERCLAIM for Declaratory Judgment of Patent Invalidity and Non-Infringement.",
            "SCHEDULING ORDER setting claim construction schedule.",
            "NOTICE OF SERVICE of Defendant’s Preliminary Invalidity Contentions and Accompanying Prior Art Production."
        ],
        "days_offset": 140
    },
    {
        "disposition": "active",
        "has_invalidity": False,
        "entries": [
            "SUMMONS Issued as to Defendants.",
            "ANSWER to Complaint with affirmative defenses.",
            "INITIAL CASE MANAGEMENT CONFERENCE held. Discovery ongoing."
        ],
        "days_offset": 45
    }
]

class BatchProcessor:
    def __init__(self, import_store=None):
        self.import_store = import_store
        self.is_running = False
        self.stop_requested = False
        self.event_queue = queue.Queue()
        self.results = []
        self.thread = None
        self.lock = threading.Lock()
        self.stats = self._empty_stats(0)

    @staticmethod
    def _empty_stats(total):
        return dict.fromkeys(("processed", "settled", "dismissed", "active", "invalidity_contentions",
                              "gemini_verified", "source_verified", "terminated_other", "unmatched", "demo", "imported"), 0) | {"total": total}

    def start_batch(self, cases, api_token="", gemini_api_key="", proxy_string="", pm_cookie="",
                    custom_keywords=None, delay_seconds=0.4, demo_mode=False):
        with self.lock:
            if self.is_running:
                return {"success": False, "error": "Batch processor is already running"}
            self.is_running, self.stop_requested = True, False
            self.results = []
            self.stats = self._empty_stats(len(cases))
            while not self.event_queue.empty():
                try:
                    self.event_queue.get_nowait()
                except queue.Empty:
                    break
            self.thread = threading.Thread(target=self._run_loop,
                args=(cases, api_token, gemini_api_key, proxy_string, pm_cookie, custom_keywords, delay_seconds, demo_mode),
                daemon=True)
            self.thread.start()
        return {"success": True, "total": len(cases)}

    def stop_batch(self):
        # Do not allow a second batch while the current request is still finishing.
        self.stop_requested = True
        return {"success": True}

    @staticmethod
    def _demo_data(case, idx):
        scenario = SIMULATED_DOCKET_TEMPLATES[idx % len(SIMULATED_DOCKET_TEMPLATES)]
        try:
            filed = datetime.strptime(case.get("date_filed_input", ""), "%Y-%m-%d")
        except ValueError:
            filed = datetime(2021, 5, 1)
        final_date = filed + timedelta(days=scenario["days_offset"])
        entries = [{"entry_number": 1, "date_filed": filed.strftime("%Y-%m-%d"),
                    "description": "COMPLAINT for Patent Infringement (simulated).", "source": "Demo simulation", "source_url": ""}]
        for offset, description in enumerate(scenario["entries"], 2):
            dt = final_date if offset == len(scenario["entries"]) + 1 else filed + timedelta(days=offset * 10)
            entries.append({"entry_number": offset, "date_filed": dt.strftime("%Y-%m-%d"),
                            "description": description, "source": "Demo simulation", "source_url": "", "date_basis": "simulated"})
        return {"found": True, "verified": False, "source_display": "Demo simulation",
                "date_filed": filed.strftime("%Y-%m-%d"),
                "date_terminated": final_date.strftime("%Y-%m-%d") if scenario["disposition"].startswith(("settlement", "dismissed")) else "",
                "docket_entries": entries, "coverage": "Simulated data; not a court record"}

    def _process_case(self, case, idx, scraper, engine, demo_mode):
        from .evidence import parse_date
        if case.get("docket_import_id") and not demo_mode:
            if self.import_store is None:
                raise ValueError("Docket import store is unavailable.")
            scraped = self.import_store.load(case["docket_import_id"], case)
        else:
            scraped = self._demo_data(case, idx) if demo_mode else scraper.fetch_case_data(
                case.get("case_number", ""), case.get("court_code") or normalize_court_id(case.get("jurisdiction_input", "")),
                case.get("plaintiff", ""), case.get("defendants", ""), case.get("pacermonitor_url", ""))
        imported = bool(scraped.get("imported"))
        identity_verified = bool(scraped.get("found") and (scraped.get("verified") or (imported and scraped.get("identity_match", {}).get("verified"))) and not demo_mode)
        entries = scraped.get("docket_entries", []) if identity_verified or demo_mode else []
        # An entry without a date or origin is never used as verified evidence.
        entries = [e for e in entries if parse_date(e.get("date_filed")) and e.get("description")
                   and (demo_mode or imported or e.get("source_url"))]
        source_verified = identity_verified and bool(entries) and not imported
        analysis = engine.evaluate_case_disposition(scraped.get("date_filed") if identity_verified or demo_mode else "",
            scraped.get("date_terminated") if identity_verified or demo_mode else "", entries)
        verification_status = "DEMO" if demo_mode else "IMPORTED_MATCHED" if imported and identity_verified else "SOURCE_VERIFIED" if source_verified else "METADATA_ONLY" if identity_verified else "UNVERIFIED"
        message = ("Simulated data; not verified." if demo_mode else
                   "Case number and parties match the imported docket. Source authenticity and freshness are not independently verified." if imported else
                   "Case number and both party roles match the source; dated entries retrieved." if source_verified else
                   "Case identity matched, but no dated docket entries were available." if identity_verified else
                   scraped.get("error", "No verified docket data retrieved."))
        if not identity_verified and not demo_mode:
            analysis["status_label"] = "Unverified / unavailable"
            analysis["disposition_category"] = "UNKNOWN"
        item = {key: case.get(key, "") for key in ("case_number", "original_case_number", "court_code", "plaintiff",
                                                   "defendants", "num_patents", "patent_numbers", "patent_title", "raw_row")}
        item.update({
            "id": case.get("id", idx + 1), "jurisdiction": case.get("jurisdiction_input", case.get("court_code", "")),
            "case_caption": scraped.get("case_name", ""), "date_filed_input": case.get("date_filed_input", ""),
            "status_category": analysis["disposition_category"],
            "status_display": ("DEMO: " if demo_mode else "") + analysis["status_label"],
            "litigation_stage": analysis["litigation_stage"],
            "has_invalidity_contentions": analysis["has_invalidity_contentions"],
            "invalidity_summary": (analysis.get("invalidity_contention_details") or {}).get("text", ""),
            "client_summary": message if not source_verified else analysis["status_label"] + ". " + scraped.get("coverage", ""),
            "gemini_verified": False, "confidence": verification_status, "verification_status": verification_status,
            "source_verified": source_verified, "identity_verified": identity_verified, "demo_mode": demo_mode,
            "docket_import_id": scraped.get("docket_import_id", ""), "imported_at": scraped.get("imported_at", ""),
            "import_filename": scraped.get("import_filename", ""), "imported": imported,
            "verification_message": message, "identity_match": scraped.get("identity_match", {}),
            "source_used": scraped.get("source_display", "No verified source"),
            "source_diagnostics": scraped.get("source_diagnostics", []),
            "retrieved_at": scraped.get("retrieved_at", ""), "coverage": scraped.get("coverage", ""),
            "source_last_updated": scraped.get("source_last_updated", ""),
            "page_sha256": scraped.get("page_sha256", ""),
            "docket_url": scraped.get("docket_url", "") if identity_verified else "",
            "pacermonitor_url": scraped.get("pacermonitor_url", "") if identity_verified else "",
            "search_url": scraped.get("search_url", ""), "docket_entries": entries,
            "latest_entry_date": max((e["date_filed"] for e in entries), default=""),
            "undated_entry_count": len(scraped.get("undated_entries", [])),
        })
        for key in ("badge_color", "date_filed", "date_terminated", "matched_keywords", "glossary_hits", "trigger_entry",
                    "trigger_date", "trigger_entry_number", "trigger_source_url", "trigger_source",
                    "trigger_date_basis", "all_trigger_entries"):
            item[key] = analysis.get(key, "")
        return item

    def _run_loop(self, cases, api_token, gemini_api_key, proxy_string, pm_cookie, custom_keywords, delay_seconds, demo_mode):
        try:
            scraper = MultiSourceScraper(api_token=api_token, pm_cookie=pm_cookie, proxy_string=proxy_string)
            engine = KeywordEngine(custom_keywords)
            self.event_queue.put({"type": "batch_started", "total": len(cases), "stats": dict(self.stats)})
            for idx, case in enumerate(cases):
                if self.stop_requested:
                    break
                self.event_queue.put({"type": "case_progress", "index": idx + 1, "total": len(cases),
                                      "case_number": case.get("case_number", ""), "court": case.get("court_code", "")})
                try:
                    item = self._process_case(case, idx, scraper, engine, demo_mode)
                except Exception:
                    logger.exception("Case processing failed at row %s", idx + 1)
                    item = {**case, "id": case.get("id", idx + 1), "status_display": "Unverified / processing error",
                            "status_category": "UNKNOWN", "verification_status": "UNVERIFIED",
                            "verification_message": "Processing failed; no verified evidence produced.",
                            "source_verified": False, "gemini_verified": False, "source_used": "No verified source",
                            "trigger_entry": "", "trigger_date": "", "date_terminated": "", "matched_keywords": [],
                            "docket_url": "", "pacermonitor_url": "", "client_summary": "Processing failed; retry this case."}
                if self.stop_requested:
                    break
                self.results.append(item)
                category = item["status_category"]
                if item.get("demo_mode"):
                    self.stats["demo"] += 1
                elif item.get("imported"):
                    self.stats["imported"] += 1
                elif item.get("source_verified"):
                    self.stats["source_verified"] += 1
                    if category == "SETTLED":
                        self.stats["settled"] += 1
                    elif category == "DISMISSED":
                        self.stats["dismissed"] += 1
                    elif category == "TERMINATED":
                        self.stats["terminated_other"] += 1
                    if item.get("has_invalidity_contentions"):
                        self.stats["invalidity_contentions"] += 1
                else:
                    self.stats["unmatched"] += 1
                self.stats["processed"] += 1
                self.event_queue.put({"type": "case_completed", "item": item, "stats": dict(self.stats),
                                      "index": idx + 1, "total": len(cases)})
                time.sleep(max(0, delay_seconds))
        finally:
            with self.lock:
                self.is_running = False
                self.event_queue.put({"type": "batch_cancelled" if self.stop_requested else "batch_finished",
                                      "stats": dict(self.stats), "total_results": len(self.results)})
