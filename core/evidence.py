"""Conservative identity checks and dates shared by source adapters."""
import re
import unicodedata
from datetime import datetime

from .excel_parser import normalize_court_id


def parse_date(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y",
                "%A, %B %d, %Y", "%A %B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def case_key(value):
    value = re.sub(r"\b(\d+)-(\d{2}-[a-zA-Z]{2,4}-)", r"\1:\2", str(value or ""))
    match = re.search(r"(?<!\d)(?:(\d+):)?(\d{2}|\d{4})-([a-z]{2,4})-0*(\d+)(?!\d)",
                      str(value or "").lower())
    if not match:
        return None
    office, year, kind, number = match.groups()
    return (office, year[-2:], kind, int(number))


def party_key(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\bet\s+al\.?", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = text.split()
    while tokens and tokens[-1] in {"llc", "inc", "incorporated", "corp", "corporation", "ltd", "limited", "llp", "co"}:
        tokens.pop()
    return " ".join(tokens)


def party_matches(expected, observed):
    expected_names = [party_key(v) for v in re.split(r";|\n|\s+and\s+", str(expected or ""), flags=re.I)]
    actual_names = [party_key(v) for v in observed if party_key(v)]
    return bool(expected_names and all(expected_names)) and all(v in actual_names for v in expected_names)


def verify_identity(case_number, court_code, plaintiff, defendants, metadata):
    wanted, actual = case_key(case_number), case_key(metadata.get("case_number"))
    number_match = bool(wanted and actual and wanted[1:] == actual[1:]
                        and (wanted[0] is None or wanted[0] == actual[0]))
    caption_parts = re.split(r"\s+v(?:s)?\.?\s+", metadata.get("case_name", ""), maxsplit=1, flags=re.I)
    plaintiffs = metadata.get("plaintiffs") or (caption_parts[:1] if len(caption_parts) == 2 else [])
    defendant_names = metadata.get("defendants") or (caption_parts[1:] if len(caption_parts) == 2 else [])
    checks = {
        "case_number": number_match,
        "plaintiff": party_matches(plaintiff, plaintiffs),
        "defendants": party_matches(defendants, defendant_names),
    }
    if court_code:
        checks["court"] = bool(metadata.get("court")) and normalize_court_id(court_code) == normalize_court_id(metadata["court"])
    return {"verified": all(checks.values()), "checks": checks,
            "reason": "Case number and party roles match" if all(checks.values()) else
            "Missing or mismatched " + ", ".join(k for k, v in checks.items() if not v),
            "observed": {"case_number": metadata.get("case_number", ""),
                         "case_name": metadata.get("case_name", ""), "court": metadata.get("court", ""),
                         "plaintiffs": plaintiffs, "defendants": defendant_names}}
