"""CourtListener fallback with case identity checks and paginated dated entries."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from .evidence import parse_date, verify_identity


class CourtListenerClient:
    BASE_URL = "https://www.courtlistener.com/api/rest/v4"

    def __init__(self, api_token=None, proxy_dict=None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "PatentDocketTracker/2.0"
        if api_token:
            self.session.headers["Authorization"] = "Token " + api_token.strip()
        if proxy_dict:
            self.session.proxies.update(proxy_dict)
        self.blocked_reason = ""

    def _pages(self, url, params, limit):
        items, seen = [], set()
        for _ in range(limit):
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.netloc != "www.courtlistener.com"
                    or not parts.path.startswith("/api/rest/v4/") or url in seen):
                return items, False, "Invalid or repeated pagination URL."
            seen.add(url)
            resp = self.session.get(url, params=params, timeout=20, allow_redirects=False)
            if resp.status_code != 200:
                error = f"CourtListener returned HTTP {resp.status_code}; check API access or retry later."
                if resp.status_code in (401, 403, 429):
                    self.blocked_reason = error
                return items, False, error
            data = resp.json()
            items.extend(data.get("results", []))
            url, params = data.get("next"), None
            if not url:
                return items, True, ""
        return items, False, "Page limit reached; retrieved docket is partial."

    def search_docket(self, docket_number, court_id="", plaintiff="", defendants=""):
        failure = {"found": False, "verified": False, "source": "CourtListener / RECAP", "docket_entries": []}
        if self.blocked_reason:
            return {**failure, "error": self.blocked_reason}
        if not all((docket_number, plaintiff, defendants)):
            return {**failure, "error": "Case number and both parties are required."}
        try:
            params = {"docket_number": docket_number}
            if court_id:
                params["court"] = court_id
            dockets, complete, error = self._pages(self.BASE_URL + "/dockets/", params, 10)
            if error or not complete:
                return {**failure, "error": error}
            matches = []
            for docket in dockets:
                court = docket.get("court_id") or docket.get("court") or ""
                if isinstance(court, dict):
                    court = court.get("id", "")
                if str(court).startswith("https://"):
                    court = urlsplit(court).path.rstrip("/").rsplit("/", 1)[-1]
                meta = {"case_number": docket.get("docket_number", ""),
                        "case_name": docket.get("case_name_full") or docket.get("case_name", ""),
                        "court": court}
                identity = verify_identity(docket_number, court_id, plaintiff, defendants, meta)
                if identity["verified"] and docket.get("id"):
                    matches.append((docket, meta, identity))
            if len(matches) != 1:
                return {**failure, "error": "CourtListener case identity is missing, mismatched, or ambiguous."}
            docket, meta, identity = matches[0]
            docket_url = f"https://www.courtlistener.com/docket/{docket['id']}/"
            raw, complete, error = self._pages(self.BASE_URL + "/docket-entries/",
                                               {"docket": docket["id"], "order_by": "-date_filed,-id"}, 20)
            timestamp = datetime.now(timezone.utc).isoformat()
            entries, seen = [], set()
            for entry in raw:
                dt = parse_date(entry.get("date_filed"))
                description = entry.get("description", "")
                key = (entry.get("entry_number"), dt, description)
                if dt and description and key not in seen:
                    seen.add(key)
                    entries.append({"entry_number": entry.get("entry_number", ""),
                                    "date_filed": dt, "description": description, "source": "CourtListener / RECAP",
                                    "source_url": docket_url, "retrieved_at": timestamp,
                                    "date_basis": "CourtListener date_filed"})
            return {**meta, "found": True, "verified": True, "identity_match": identity,
                    "source": "CourtListener / RECAP", "docket_url": docket_url, "docket_entries": entries,
                    "date_filed": parse_date(docket.get("date_filed")),
                    "date_terminated": parse_date(docket.get("date_terminated")), "retrieved_at": timestamp,
                    "source_last_updated": docket.get("date_modified", ""),
                    "coverage": "All available RECAP entry pages retrieved; archive may be incomplete or outdated" if complete
                    else "Partial RECAP docket: " + error, "entry_error": error}
        except (requests.RequestException, ValueError, TypeError):
            return {**failure, "error": "CourtListener request failed; check connectivity, API access, and proxy settings."}
