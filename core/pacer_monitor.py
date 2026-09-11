"""Read accessible PacerMonitor pages and bind each docket row to its date."""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from .evidence import parse_date, verify_identity


class SourceAccessError(Exception):
    pass


class PacerMonitorClient:
    BASE_URL = "https://www.pacermonitor.com"

    def __init__(self, session_cookie=None, proxy_dict=None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PatentDocketTracker/2.0", "Accept": "text/html"})
        for pair in (session_cookie or "").split(";"):
            name, sep, value = pair.strip().partition("=")
            if sep and name:
                self.session.cookies.set(name, value, domain="www.pacermonitor.com", path="/", secure=True)
        if proxy_dict:
            self.session.proxies.update(proxy_dict)
        self.blocked_reason = ""
        self.blocked_scope = ""

    def get_pacermonitor_url(self, case_number, court_code="", plaintiff="", defendants=""):
        query = " ".join(v for v in (case_number, plaintiff, defendants, court_code) if v)
        return self.BASE_URL + "/search?" + urlencode({"querystring": query, "querytype": "caseQuery"})

    @staticmethod
    def valid_case_url(url):
        try:
            parts = urlsplit(str(url or ""))
            return (parts.scheme == "https" and parts.hostname in {"www.pacermonitor.com", "pacermonitor.com"}
                    and not parts.username and not parts.password and parts.port in (None, 443)
                    and bool(re.fullmatch(r"/public/case/\d+/[^/?#]+/?", parts.path)))
        except ValueError:
            return False

    def _request(self, method, url, **kwargs):
        response = self.session.request(method, url, timeout=20, allow_redirects=False, **kwargs)
        for _ in range(4):
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            target = urljoin(url, response.headers.get("Location", ""))
            parts = urlsplit(target)
            if (parts.scheme != "https" or parts.hostname not in {"pacermonitor.com", "www.pacermonitor.com"}
                    or parts.username or parts.password or parts.port not in (None, 443)):
                raise SourceAccessError("PacerMonitor redirected outside its site; access unavailable.")
            url = target
            response = self.session.get(url, timeout=20, allow_redirects=False)
        if response.status_code in (401, 403, 429):
            self.blocked_reason = f"PacerMonitor access unavailable (HTTP {response.status_code}). Check account access or retry later."
            self.blocked_scope = "search" if urlsplit(url).path == "/search" and response.status_code != 429 else "all"
            raise SourceAccessError(self.blocked_reason)
        if response.status_code != 200:
            raise SourceAccessError(f"PacerMonitor returned HTTP {response.status_code}.")
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        if (urlsplit(response.url or url).path in ("/login", "/signin") or
                any(v in title for v in ("just a moment", "access denied", "captcha", "sign in", "log in"))):
            raise SourceAccessError("PacerMonitor requires login or an access challenge; no docket retrieved.")
        return response

    @staticmethod
    def parse_case_page(html, url):
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select("script, style, noscript"):
            node.decompose()
        heading = soup.find("h1")
        metadata = {"case_name": heading.get_text(" ", strip=True) if heading else "",
                    "case_number": "", "court": "", "date_filed": "", "date_terminated": "",
                    "plaintiffs": [], "defendants": []}
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) >= 2:
                label = cells[0].get_text(" ", strip=True).rstrip(":").lower()
                value = cells[1].get_text(" ", strip=True)
                if label in ("case #", "case number", "case no."):
                    metadata["case_number"] = value
                elif label in ("case filed", "filed", "terminated"):
                    metadata["date_terminated" if label == "terminated" else "date_filed"] = parse_date(value)
                elif label in ("court", "jurisdiction"):
                    metadata["court"] = value
            if cells:
                party = cells[0].get_text(" ", strip=True)
                match = re.match(r"^(Plaintiff|Defendant)\s+(.+)", party, re.I)
                if match:
                    name = re.split(r"\s+Represented By\b", match[2], flags=re.I)[0].strip()
                    metadata["plaintiffs" if match[1].lower() == "plaintiff" else "defendants"].append(name)
        lines = list(soup.stripped_strings)
        for index, line in enumerate(lines):
            if line.startswith("Docket last updated:"):
                metadata["source_last_updated"] = line.partition(":")[2].strip() or (lines[index + 1] if index + 1 < len(lines) else "")
            if not metadata["court"] and re.fullmatch(r"[A-Za-z .]+ District Court", line):
                metadata["court"] = line
            match = re.fullmatch(r"(Case #|Case Filed|Terminated):?\s*(.*)", line, re.I)
            if match:
                value = match[2] or (lines[index + 1] if index + 1 < len(lines) else "")
                key = {"case #": "case_number", "case filed": "date_filed", "terminated": "date_terminated"}[match[1].lower()]
                if not metadata[key]:
                    metadata[key] = value if key == "case_number" else parse_date(value)

        retrieved_at = datetime.now(timezone.utc).isoformat()
        entries, undated, current_date, seen = [], [], "", set()
        date_table = None
        full_date = re.compile(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}$")
        for node in soup.find_all(True):
            # Never extract a filing date from signed dates or deadlines in the narrative.
            if node.name in ("tr", "div", "h2", "h3", "h4", "td", "span", "b", "strong"):
                text = node.get_text(" ", strip=True)
                parent_row = node.find_parent("tr")
                parent_cells = parent_row.find_all("td", recursive=False) if parent_row else []
                inside_entry = len(parent_cells) > 1
                if full_date.fullmatch(text) and not inside_entry:
                    current_date = parse_date(text)
                    date_table = node.find_parent("table")
                    continue
            if node.name != "tr":
                continue
            cells = node.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            first = cells[0].get_text(" ", strip=True)
            number = node.get("data-entry-number") or (first if re.fullmatch(r"\d+", first) else "")
            classes = " ".join(node.get("class", [])).lower()
            if not number and "docket" not in classes:
                continue
            desc_node = node.select_one(".docket-text, .docketText, .docket-description, .docketDescription")
            desc = (desc_node or cells[-1]).get_text(" ", strip=True)
            if not desc or desc.startswith("Att:"):
                continue
            explicit_date = node.get("data-date-filed")
            heading_date = current_date if date_table is None or date_table is node.find_parent("table") else ""
            entry_date = parse_date(explicit_date) if explicit_date is not None else heading_date
            key = (number, entry_date, desc)
            if key in seen:
                continue
            seen.add(key)
            entry = {"entry_number": number, "date_filed": entry_date, "description": desc,
                     "source": "PacerMonitor", "source_url": url,
                     "date_basis": "docket date heading" if explicit_date is None else "entry date attribute",
                     "retrieved_at": retrieved_at}
            (entries if entry_date else undated).append(entry)
        metadata.update({"docket_entries": entries, "undated_entries": undated,
                         "docket_url": url, "pacermonitor_url": url, "source": "PacerMonitor",
                         "retrieved_at": retrieved_at, "page_sha256": hashlib.sha256(html.encode()).hexdigest(),
                         "coverage": "Accessible page only; docket may be partial or outdated"})
        return metadata

    def search_case(self, case_number, court_code="", plaintiff="", defendants="", case_url=""):
        search_url = self.get_pacermonitor_url(case_number, court_code, plaintiff, defendants)
        failure = {"found": False, "verified": False, "source": "PacerMonitor", "docket_entries": [],
                   "search_url": search_url, "pacermonitor_url": ""}
        if not all(str(v or "").strip() for v in (case_number, plaintiff, defendants)):
            return {**failure, "error": "Case number, plaintiff, and defendant are required for verification."}
        if self.blocked_reason and (not case_url or self.blocked_scope == "all"):
            return {**failure, "error": self.blocked_reason}
        try:
            if case_url:
                if not self.valid_case_url(case_url):
                    return {**failure, "error": "Provide an HTTPS PacerMonitor /public/case/ URL."}
                links = [case_url]
            else:
                response = self._request("GET", self.BASE_URL + "/search")
                soup = BeautifulSoup(response.text, "html.parser")
                csrf = soup.select_one('input[name="_csrf"]')
                form = {"querystring": case_number, "querytype": "caseQuery"}
                if csrf:
                    form["_csrf"] = csrf.get("value", "")
                response = self._request("POST", self.BASE_URL + "/search", data=form,
                                         headers={"Referer": self.BASE_URL + "/search"})
                soup = BeautifulSoup(response.text, "html.parser")
                links = list(dict.fromkeys(urljoin(self.BASE_URL, a["href"]) for a in soup.select("a[href]")
                                           if self.valid_case_url(urljoin(self.BASE_URL, a["href"]))))
            matches, rejections = [], []
            for url in links[:10]:
                response = self._request("GET", url)
                parsed = self.parse_case_page(response.text, response.url or url)
                if not self.valid_case_url(parsed["docket_url"]):
                    continue
                identity = verify_identity(case_number, court_code, plaintiff, defendants, parsed)
                if identity["verified"]:
                    matches.append({**parsed, "found": True, "verified": True, "identity_match": identity})
                else:
                    rejections.append(identity)
            if len(matches) == 1 and len(links) <= 10:
                return {**matches[0], "search_url": search_url}
            return {**failure, "identity_rejections": rejections,
                    "error": "Ambiguous or too many case results; supply a direct PacerMonitor URL." if len(matches) > 1 or len(links) > 10
                    else "No PacerMonitor case page matched the case number, plaintiff, defendant, and supplied court."}
        except (requests.RequestException, SourceAccessError, ValueError) as exc:
            message = str(exc) if isinstance(exc, SourceAccessError) else "PacerMonitor request failed; check connectivity and proxy settings."
            return {**failure, "error": message}
