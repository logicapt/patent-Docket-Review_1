"""Import a user-accessible docket snapshot without claiming live verification."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from .evidence import verify_identity
from .excel_parser import normalize_court_id
from .pacer_monitor import PacerMonitorClient


class DocketImportError(ValueError):
    def __init__(self, message, identity=None):
        super().__init__(message)
        self.identity = identity or {}


def clean_source_url(value):
    if not value:
        return ""
    try:
        parts = urlsplit(value.strip())
        if (parts.scheme == "https" and parts.hostname in {"pacermonitor.com", "www.pacermonitor.com"}
                and not parts.username and not parts.password and parts.port in (None, 443)
                and re.match(r"/(?:public/)?case/\d+(?:/|$)", parts.path)):
            # Do not store session-like query strings or fragments from a saved page.
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        pass
    raise DocketImportError("The source URL must be an HTTPS PacerMonitor case link.")


class DocketImportStore:
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, import_id):
        if not re.fullmatch(r"[0-9a-f]{32}", str(import_id)):
            raise DocketImportError("Invalid docket import identifier.")
        return self.directory / (import_id + ".json")

    @staticmethod
    def check_identity(case, metadata):
        identity = verify_identity(case.get("case_number", ""),
            case.get("court_code") or normalize_court_id(case.get("jurisdiction_input", "")),
            case.get("plaintiff", ""), case.get("defendants", ""), metadata)
        if not identity["verified"]:
            raise DocketImportError("The saved docket does not match the selected case: " + identity["reason"], identity)
        return identity

    def import_html(self, content, filename, case, source_url=""):
        if Path(filename).suffix.lower() not in (".html", ".htm"):
            raise DocketImportError("Upload a captured or saved HTML docket (.html or .htm).")
        if not content or len(content) > self.MAX_BYTES:
            raise DocketImportError("The HTML file must be nonempty and no larger than 8 MB.")
        soup = BeautifulSoup(content, "html.parser")
        source_meta = soup.select_one('meta[name="docket-source-url"], link[rel="canonical"]')
        if not source_url and source_meta:
            source_url = source_meta.get("content") or source_meta.get("href", "")
        source_url = clean_source_url(source_url)
        title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        if any(value in title for value in ("sign in", "login", "log in", "access denied", "just a moment")):
            raise DocketImportError("This file is a login or blocked page. Open the actual docket while signed in and capture it again.")
        # Forms and executable content are never retained or executed.
        for node in soup.select("script, style, form, input, textarea, iframe, object, embed, link"):
            node.decompose()
        parsed = PacerMonitorClient.parse_case_page(str(soup), source_url)
        identity = self.check_identity(case, parsed)
        if not parsed["docket_entries"]:
            raise DocketImportError("The case matches, but the file contains no readable dated docket rows. Expand the docket on PacerMonitor and capture the loaded page again.", identity)
        imported_at = datetime.now(timezone.utc).isoformat()
        import_id = uuid4().hex
        parsed.update({"found": True, "verified": False, "imported": True,
            "identity_match": identity, "docket_import_id": import_id,
            "source": "PacerMonitor (imported page)", "source_display": "PacerMonitor (imported page)",
            "imported_at": imported_at, "retrieved_at": "", "import_filename": Path(filename).name,
            "page_sha256": hashlib.sha256(content).hexdigest(),
            "coverage": "User-supplied snapshot of loaded docket rows; source authenticity and current completeness are not independently verified."})
        for entry in parsed["docket_entries"] + parsed.get("undated_entries", []):
            entry.update({"source": parsed["source"], "retrieved_at": "", "imported_at": imported_at})
        self.path_for(import_id).write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        return parsed

    def load(self, import_id, case):
        try:
            parsed = json.loads(self.path_for(import_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raise DocketImportError("Saved docket import not found; import the docket again.")
        self.check_identity(case, parsed)
        # Imported data cannot promote itself to a live verified source.
        parsed.update({"verified": False, "imported": True, "docket_import_id": import_id})
        return copy.deepcopy(parsed)
