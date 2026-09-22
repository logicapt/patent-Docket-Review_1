"""Source allowlist and HTML-only extraction; no network calls or storage."""
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .evidence import parse_date
from .pacer_monitor import PacerMonitorClient
from .crawl4ai_extractor import extract_with_schema, find_json_docket_entries


SOURCES = {
    "courtlistener": {"name": "CourtListener / RECAP", "domain": "www.courtlistener.com", "path": r"/docket/\d+(?:/[^/]+)?/?"},
    "exparte": {"name": "Ex Parte AI Lab", "domain": "ai-lab.exparte.com", "path": r"/case/[^?#]+"},
    "unicourt": {"name": "UniCourt", "domain": "unicourt.com", "path": r"/(?:case|case-detail)/[^?#]+"},
    "pacermonitor": {"name": "PacerMonitor", "domain": "www.pacermonitor.com", "path": r"/(?:public/)?case/\d+(?:/[^/]+)?/?"},
    "law360": {"name": "Law360", "domain": "www.law360.com", "path": r"/(?:[^/]+/)?(?:articles|cases)/[^?#]+"},
}


def source_url(value):
    """Return a canonical source link or reject URLs that could fetch arbitrary hosts."""
    try:
        parts = urlsplit(value.strip())
        path = unquote(parts.path)
        if (parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443)
                or "\\" in value or "\\" in path or re.search(r"[\x00-\x20]", value)
                or re.search(r"[\x00-\x1f]", path) or any(segment in {".", ".."} for segment in path.split("/"))):
            raise ValueError()
        for key, source in SOURCES.items():
            domain = source["domain"].removeprefix("www.")
            if parts.hostname in {domain, "www." + domain} and re.fullmatch(source["path"], parts.path):
                # CourtListener publishes an order_by=desc website control. Request its
                # newest page so a large docket's default ascending page is not selected.
                query = "order_by=desc" if key == "courtlistener" else ""
                return key, urlunsplit(("https", source["domain"], quote(path, safe="/"), query, ""))
    except (ValueError, AttributeError):
        pass
    raise ValueError("Use an HTTPS case link from CourtListener, Ex Parte AI Lab, UniCourt, PacerMonitor, or Law360.")


def google_url(case, source_ids=None):
    ids = source_ids or list(SOURCES)
    terms = [case.get(k, "").replace('"', " ").strip() for k in ("case_number", "plaintiff", "defendants", "court")]
    query = " ".join('"' + term + '"' for term in terms if term)
    query += " (" + " OR ".join("site:" + SOURCES[k]["domain"] for k in ids) + ")"
    return "https://www.google.com/search?" + urlencode({"q": query})


def search_candidates(html, selected):
    soup = BeautifulSoup(html, "html.parser")
    candidates, seen = [], set()
    for anchor in soup.select("a[href]"):
        value = urljoin("https://www.google.com", anchor["href"])
        parts = urlsplit(value)
        if parts.hostname in {"google.com", "www.google.com"} and parts.path == "/url":
            query = parse_qs(parts.query)
            value = (query.get("q") or query.get("url") or [""])[0]
        try:
            key, url = source_url(value)
        except ValueError:
            continue
        title = anchor.select_one("h3") or anchor
        title = title.get_text(" ", strip=True)
        if key not in selected or url in seen or not title:
            continue
        seen.add(url)
        candidates.append({"source_id": key, "source": SOURCES[key]["name"], "url": url,
                           "title": title[:350], "verified": False})
        if len(candidates) == 12:
            break
    return candidates


def docket_date(value):
    """Dates come from labelled cells/attributes, never a description or deadline."""
    value = re.sub(r"\s+", " ", str(value or "")).strip().replace("Sept.", "Sep").replace("Sep.", "Sep")
    parsed = parse_date(value)
    if parsed:
        return parsed
    for fmt in ("%m/%d/%y", "%b. %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _text(node):
    return node.get_text(" ", strip=True) if node else ""


def _metadata(soup):
    # Docket column labels/dates are not case filing dates or other case metadata.
    soup = BeautifulSoup(str(soup), "html.parser")
    for node in list(soup.select('#docket-entry-table, [aria-label="Docket entries"], [data-entry-number]')):
        node.decompose()
    for table in list(soup.select('table, [role="table"], [role="grid"]')):
        if table.parent is None:
            continue
        header = table.select('th, [role="columnheader"]')
        if not header:
            row = table.select_one('tr, [role="row"]')
            header = row.select('td, [role="cell"], [role="gridcell"]') if row else []
        if any(_text(cell).lower() in {"description", "docket text", "docket entry"} for cell in header):
            table.decompose()
    meta = {"case_name": _text(soup.find("h1")), "case_number": "", "court": "",
            "citation": "", "source_last_updated": "", "source_update_label": "",
            "date_terminated": "", "plaintiffs": [], "defendants": []}
    labels = {"case #": "case_number", "case number": "case_number", "case no.": "case_number",
              "docket number": "case_number", "court": "court", "jurisdiction": "court",
              "citation": "citation", "citations": "citation", "last updated": "source_last_updated",
              "docket last updated": "source_last_updated", "checked at": "source_last_updated",
              "date terminated": "date_terminated", "terminated": "date_terminated"}
    report_labels = {"presiding judge", "judge", "assigned to", "patent in suit", "patents in suit",
                     "patent number", "patent numbers", "date filed", "filing date", "filed",
                     "disposition date", "plaintiff counsel", "plaintiff's counsel", "counsel for plaintiff",
                     "defendant counsel", "defendant's counsel", "counsel for defendant", "plaintiff type",
                     "case status", "status", "current stage", "contentions status"}
    pairs = []
    for term in soup.select("dt"):
        pairs.append((_text(term), _text(term.find_next_sibling("dd"))))
    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) == 2:
            pairs.append((_text(cells[0]), _text(cells[1])))
    for label, value in pairs:
        role = label.strip().rstrip(":").lower()
        if role in {"plaintiff", "plaintiffs", "defendant", "defendants"} and value:
            meta["plaintiffs" if role.startswith("plaintiff") else "defendants"].extend(
                name.strip() for name in value.split(";") if name.strip())
    # Visible labels rendered in div-based layouts, including Ex Parte.
    lines = list(soup.stripped_strings)
    for index, line in enumerate(lines):
        name, sep, value = line.partition(": ")
        label = name.rstrip(":").lower()
        if label in labels or label in report_labels:
            pairs.append((name, value if sep else (lines[index + 1] if index + 1 < len(lines) else "")))
        match = re.match(r"^(Checked at|Docket last updated|Last updated):?\s+(.+)$", line, re.I)
        if match:
            pairs.append((match[1], match[2]))
    for label, value in pairs:
        key = labels.get(label.strip().rstrip(":").lower())
        if key and value and not meta[key]:
            meta[key] = value[:500]
            if key == "source_last_updated":
                meta["source_update_label"] = label
    for name, key in (("citation_docket_number", "case_number"), ("citation_case_name", "case_name"),
                      ("citation_court", "court"), ("citation_reference", "citation")):
        node = soup.select_one('meta[name="' + name + '"]')
        if node and node.get("content"):
            meta[key] = node["content"][:500]
    # A standalone case-number heading is evidence; the URL alone is not.
    if not meta["case_number"]:
        for heading in soup.select("h1, h2, h3"):
            match = re.search(r"(?<!\d)(?:\d+:)?\d{2}-[a-z]{2,4}-\d+(?:-[A-Z]+)?", _text(heading), re.I)
            if match:
                meta["case_number"] = match[0]
                break
    if meta["case_number"]:
        meta["case_name"] = re.sub(r"\s*\(\s*" + re.escape(meta["case_number"]) + r"\s*\)\s*$", "", meta["case_name"])
    meta["case_name"] = re.split(r",?\s+(?:No\.?\s+)?(?:\d+:)?\d{2}-[a-z]{2,4}-\d+", meta["case_name"], maxsplit=1, flags=re.I)[0]
    docket_heading = soup.select_one('h1[data-type="search.Docket"]')
    if docket_heading and not meta["court"]:
        hgroup = docket_heading.find_parent("hgroup")
        meta["court"] = _text(hgroup.find("p") if hgroup else docket_heading.find_next_sibling("h2"))
    meta["date_terminated"] = docket_date(meta["date_terminated"])
    meta["report_metadata"] = []
    seen_labels = set()
    for label, value in pairs:
        role = label.strip().rstrip(":").lower()
        if role in report_labels and value and (role, value) not in seen_labels:
            seen_labels.add((role, value))
            meta["report_metadata"].append({"label": label, "value": value})
    return meta


def _table_entries(soup):
    entries = []
    for table in soup.select('table, [role="table"], [role="grid"]'):
        mapping = {}
        for row in table.select('tr, [role="row"]'):
            cells = row.select('th, td, [role="columnheader"], [role="cell"], [role="gridcell"]')
            values = [_text(cell) for cell in cells]
            headers = [re.sub(r"[^a-z0-9]+", " ", v.lower()).strip() for v in values]
            if any(v in {"description", "docket text", "docket entry"} for v in headers):
                mapping = {}
                for index, label in enumerate(headers):
                    if label in {"description", "docket text", "docket entry"}:
                        mapping["description"] = index
                    elif label in {"dkt", "dkt no", "docket no", "docket number", "entry number", "entry", "document number", "document", "doc", "no", "number", ""}:
                        mapping.setdefault("entry_number", index)
                    elif label in {"filed", "date filed", "filing date"}:
                        mapping["date_filed"] = index
                    elif label in {"entered", "date entered", "entry date"}:
                        mapping["date_entered"] = index
                    elif label == "date":
                        mapping["docket_date"] = index
                continue
            if not mapping or max(mapping.values()) >= len(values):
                continue
            entry = {key: values[index] for key, index in mapping.items()}
            if not re.fullmatch(r"\d+(?:-\d+)?", entry.get("entry_number", "")):
                continue
            entry["date_basis"] = " / ".join(label for key, label in
                (("date_filed", "source Filed column"), ("date_entered", "source Entered column"),
                 ("docket_date", "source Date column")) if key in mapping)
            entries.append(entry)
    # Explicit attributes are useful on rendered docket components.
    for row in soup.select('[data-entry-number]'):
        desc = row.select_one(".docket-text, .docket-description, [data-description]")
        if desc:
            entries.append({"entry_number": row["data-entry-number"], "description": _text(desc),
                            "date_filed": row.get("data-date-filed", ""), "date_entered": row.get("data-date-entered", ""),
                            "date_basis": "source row date attributes"})
    return entries


def _courtlistener_entries(soup):
    """Read the public v1/v2 docket DOM, excluding document/attachment subrows.

    Selectors checked against CourtListener's public de_list.html and
    cotton/docket_entry_rows.html templates, not against its REST API.
    """
    entries = []
    for row in soup.select('#docket-entry-table > .row[id^="entry-"]'):
        cells = row.find_all("div", recursive=False)
        if len(cells) >= 3:
            entries.append({"entry_number": row["id"].removeprefix("entry-"),
                "date_filed": _text(cells[1].find("p", recursive=False)),
                "description": _text(cells[2].find("p", recursive=False)), "date_basis": "CourtListener Date Filed column"})
    for row in soup.select('ol[aria-label="Docket entries"] > li[id^="entry-"]'):
        fields = {_text(dt).lower(): _text(dt.find_next_sibling("dd")) for dt in row.select("dt")}
        entries.append({"entry_number": fields.get("document number", ""), "date_filed": fields.get("date filed", ""),
                        "description": fields.get("description", ""), "date_basis": "CourtListener Date Filed label"})
    return entries


def parse_source(html, url, intercepted_json=None):
    """Parse a docket page into a structured dict.

    Extraction priority (highest first):
      1. XHR/fetch JSON payloads captured by BrowserSession during page load.
         These are the raw API responses many court sites use internally —
         no HTML parsing required, highest fidelity.
      2. Crawl4AI schema-based CSS extraction — fast, no LLM, good for
         sources with known/stable DOM layouts.
      3. Original BeautifulSoup table + CourtListener DOM selectors — kept
         as a reliable fallback and to supply entries missed by the above.

    All three result sets are merged and deduplicated by (entry_number, date, desc).
    ``intercepted_json`` is the list collected by BrowserSession.intercepted_json;
    pass None (or omit) when called from the paste-docket path which has no browser.
    """
    key, url = source_url(url)
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, noscript, form, input, textarea, iframe, object, embed"):
        node.decompose()
    meta = _metadata(soup)

    # --- Tier 1: XHR/fetch JSON captured during browser navigation ----------
    xhr_entries: list[dict] = []
    if intercepted_json:
        xhr_entries = find_json_docket_entries(intercepted_json)

    # --- Tier 2: Crawl4AI CSS schema extraction (offline, on cached HTML) ---
    schema_entries: list[dict] = []
    if key not in ("pacermonitor",):  # PacerMonitor has its own dedicated parser.
        schema_entries = extract_with_schema(html, key)

    # --- Tier 3: Original BeautifulSoup extraction (existing behaviour) ------
    bs4_entries = _table_entries(soup)
    if key == "courtlistener":
        bs4_entries.extend(_courtlistener_entries(soup))
    if key == "pacermonitor":
        parsed = PacerMonitorClient.parse_case_page(str(soup), url)
        for field in ("case_number", "case_name", "court", "plaintiffs", "defendants", "date_terminated", "source_last_updated"):
            if parsed.get(field):
                meta[field] = parsed[field]
        # PacerMonitor headings are docket dates; they do not separately establish entered/filed dates.
        for entry in parsed["docket_entries"]:
            bs4_entries.append({**entry, "docket_date": entry["date_filed"], "date_filed": "", "date_entered": ""})
        if meta["source_last_updated"]:
            meta["source_update_label"] = "Docket last updated"

    # Merge all entry sources; XHR data takes precedence by appearing first.
    all_raw_entries = xhr_entries + schema_entries + bs4_entries

    timestamp = datetime.now(timezone.utc).isoformat()
    dated, seen, undated = [], set(), 0
    for raw in all_raw_entries:
        filed, entered, displayed = [docket_date(raw.get(field)) for field in ("date_filed", "date_entered", "docket_date")]
        dt = entered or filed or displayed
        desc = raw.get("description", "").strip()
        number = str(raw.get("entry_number", ""))
        if not dt or not desc:
            undated += 1
            continue
        identity = (number, dt, desc)
        if identity in seen:
            continue
        seen.add(identity)
        dated.append({"entry_number": number, "date_filed": filed, "date_entered": entered,
                      "docket_date": displayed, "date": dt, "date_basis": raw.get("date_basis", ""),
                      "description": desc, "source": SOURCES[key]["name"],
                      "source_url": url + "#entry-" + number if key == "courtlistener" and number.isdigit() else url})
    return {**meta, "source_id": key, "source": SOURCES[key]["name"], "source_url": url,
            "retrieved_at": timestamp, "docket_entries": dated, "undated_entries_excluded": undated,
            "coverage": "Loaded page only. Pagination, hidden entries and source freshness are not verified; these are the latest visible entries, not necessarily the latest court entries."}

