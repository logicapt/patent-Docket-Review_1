"""Read user-pasted page content in memory without requesting its source site."""
from datetime import datetime, timezone
from html import escape
import re

from .browser_session import access_check
from .browser_sources import parse_source


MAX_PASTE_BYTES = 2 * 1024 * 1024


def _plain_table_html(text):
    """Preserve labelled tab-separated columns; never guess dates from prose.

    Browser clipboard HTML is preferred. Plain text needs a tab-separated docket
    header and rows, since line-wrapped prose cannot establish reliable columns.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    header_index = None
    labels = []
    date_labels = {"filed", "date filed", "filing date", "entered", "date entered", "entry date", "date"}
    number_labels = {"dkt", "dkt no", "docket no", "docket number", "entry number", "entry", "document number", "document", "doc", "no", "number", ""}
    for index, line in enumerate(lines):
        labels = [re.sub(r"[^a-z0-9]+", " ", cell.lower()).strip() for cell in line.split("\t")]
        if any(label in {"description", "docket text", "docket entry"} for label in labels) and any(label in date_labels for label in labels) and any(label in number_labels for label in labels):
            header_index = index
            break
    meta_lines = lines[:header_index] if header_index is not None else lines
    caption = next((line.strip() for line in meta_lines if re.search(r"\s+v(?:s)?\.?\s+", line, re.I)), "")
    html = "<h1>" + escape(caption) + "</h1>"
    for line in meta_lines:
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\d+[:-])?\d{2}-[a-z]{2,4}-\d+(?:-[a-z]+)*", line, re.I):
            html += "<h2>" + escape(line) + "</h2>"
        elif re.match(r"^(?:Plaintiff|Defendant|Court|Citation|Case Number|Case #|Docket Number|Terminated):\s*\S", line, re.I):
            label, value = line.split(":", 1)
            html += "<table><tr><td>" + escape(label) + "</td><td>" + escape(value.strip()) + "</td></tr></table>"
        elif re.fullmatch(r"(?:.+ District Court|District of .+|[NSEWCM]\.\s*D\..+|D\.\s+.+)", line):
            html += "<dl><dt>Court</dt><dd>" + escape(line) + "</dd></dl>"
        else:
            html += "<p>" + escape(line) + "</p>"
    if header_index is None:
        return html
    html += "<table><tr>" + "".join("<th>" + escape(cell) + "</th>" for cell in lines[header_index].split("\t")) + "</tr>"
    for line in lines[header_index + 1:]:
        cells = line.split("\t")
        if len(cells) == len(labels):
            html += "<tr>" + "".join("<td>" + escape(cell) + "</td>" for cell in cells) + "</tr>"
    return html + "</table>"


def parse_pasted_docket(pasted_html, pasted_text, url):
    html = pasted_html.strip() or _plain_table_html(pasted_text)
    access_check(html, url)
    parsed = parse_source(html, url)
    parsed.update({"evidence_origin": "user_paste", "source_verified": False, "retrieved_at": "",
                   "provided_at": datetime.now(timezone.utc).isoformat(),
                   "coverage": "User-supplied copy of page content. The case identity is checked against the pasted content only; source authenticity, completeness and current freshness have not been independently verified online."})
    for entry in parsed["docket_entries"]:
        entry["evidence_origin"] = "user_paste"
    return parsed
