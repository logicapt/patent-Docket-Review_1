"""Choose an identity-matched source without mixing source labels or dates."""
from .court_listener import CourtListenerClient
from .pacer_monitor import PacerMonitorClient
from .proxy_manager import ProxyManager


class MultiSourceScraper:
    def __init__(self, api_token="", pm_cookie="", proxy_string=""):
        proxies = ProxyManager(proxy_string).get_requests_proxies()
        self.pacer_monitor = PacerMonitorClient(session_cookie=pm_cookie, proxy_dict=proxies)
        self.court_listener = CourtListenerClient(api_token=api_token, proxy_dict=proxies)

    def fetch_case_data(self, case_number, court_code="", plaintiff="", defendants="", pacermonitor_url=""):
        diagnostics, metadata_match = [], None
        search_url = self.pacer_monitor.get_pacermonitor_url(case_number, court_code, plaintiff, defendants)
        for name, fetch in (
            ("PacerMonitor", lambda: self.pacer_monitor.search_case(case_number, court_code, plaintiff, defendants, pacermonitor_url)),
            ("CourtListener / RECAP", lambda: self.court_listener.search_docket(case_number, court_code, plaintiff, defendants)),
        ):
            result = fetch()
            matched = bool(result.get("found") and result.get("verified"))
            diagnostics.append({"source": name, "matched": matched,
                                "error": result.get("error") or result.get("entry_error", ""),
                                "dated_entries": len(result.get("docket_entries", [])),
                                "identity_rejections": result.get("identity_rejections", [])})
            if matched:
                result.update({"source_display": name, "source_diagnostics": list(diagnostics), "search_url": search_url})
                if result.get("docket_entries"):
                    return result
                if metadata_match is None:
                    metadata_match = result
        if metadata_match:
            metadata_match["source_diagnostics"] = diagnostics
            return metadata_match
        return {"found": False, "verified": False, "docket_entries": [], "source_display": "No verified source",
                "docket_url": "", "pacermonitor_url": "", "search_url": search_url,
                "source_diagnostics": diagnostics, "error": "No source passed case identity and access checks."}
