"""Describe observed docket events without inventing a current case status."""
import re
from .evidence import parse_date


def evaluate(engine, date_filed, date_terminated, docket_entries):
    dated = []
    glossary_hits = {}
    for entry in docket_entries:
        dt = parse_date(entry.get("date_filed") or entry.get("date"))
        desc = entry.get("description") or entry.get("entry_text", "")
        if not dt or not desc:
            continue
        hits = engine.analyze_text(desc)["matches"]
        glossary_hits.update(hits)
        dated.append({**entry, "date": dt, "text": desc, "matches": [v["term"] for v in hits.values()]})
    dated.sort(key=lambda e: (e["date"], int(e.get("entry_number") or 0) if str(e.get("entry_number") or "").isdigit() else 0))
    terminated = parse_date(date_terminated)
    category, label, stage = "UNKNOWN", "Status unconfirmed (docket excerpt)", "Unconfirmed"
    trigger = None
    has_invalidity = False
    invalidity_details = None
    events = []
    for entry in dated:
        text = entry["text"]
        lower = text.lower()
        # Glossary mentions are navigation aids; they do not establish case disposition.
        settlement = bool(re.search(r"\bnotice of settlement\b|\b(?:parties|all parties) (?:have |advise .*?have )?(?:reached|reported) (?:a |an |global )?settlement\b|\bpursuant to (?:a |the |confidential )?settlement agreement\b", lower))
        dismissal = bool(re.search(r"\border (?:of dismissal|dismissing (?:this |the )?(?:case|action))\b|\b(?:case|action) (?:is |is hereby |hereby )?dismissed\b", lower))
        # Proposed orders, conditional language, and negation cannot prove a disposition.
        uncertain = bool(re.search(r"\b(?:proposed|denied|denying|not dismissed|not settled|not reached|no settlement|should|would|may be|shall be|will be|unless|if)\b", lower))
        dismissal = dismissal and not uncertain
        settlement = settlement and not uncertain
        served = bool(re.search(r"\b(?:notice|certificate) of service\b|\b(?:served|filed)\b", lower))
        invalidity = "invalidity contentions" in lower and served and not re.search(r"\b(?:deadline|due|shall|must|not served|not filed)\b", lower)
        if invalidity:
            has_invalidity = True
            invalidity_details = {"date": entry["date"], "text": text}
        if re.search(r"\b(?:case|action) (?:is |is hereby )?reopened\b|\border reopening\b", lower) and not uncertain:
            events.append((entry, "REOPENED", "Reopening recorded; current status needs review"))
        elif dismissal:
            events.append((entry, "DISMISSED", "Dismissal recorded" + (" (with prejudice)" if "with prejudice" in lower else " (without prejudice)" if "without prejudice" in lower else "")))
        elif settlement:
            events.append((entry, "SETTLED" if terminated else "SETTLEMENT_REPORTED",
                           "Settlement reported (case terminated)" if terminated else "Settlement reported; closure unconfirmed"))
        elif re.search(r"\border (?:staying (?:the )?case|granting .*?motion to stay)\b|\bcase (?:is )?stayed\b", lower) and not uncertain:
            events.append((entry, "STAYED", "Stay recorded; current status unconfirmed"))
    if events:
        trigger, category, label = events[-1]
        stage = "Observed docket event"
    elif terminated:
        category, label, stage = "TERMINATED", "Terminated (source metadata)", "Terminated / Resolved"
        # A metadata termination date has no invented supporting quote.
    elif dated:
        trigger = dated[-1]
    if terminated and category == "REOPENED":
        label = "Reopening and termination metadata conflict; review source"
    elif terminated and category not in ("DISMISSED", "SETTLED", "TERMINATED"):
        category, label, stage = "TERMINATED", "Terminated (source metadata)", "Terminated / Resolved"
        trigger = None
    if trigger is None and dated and not terminated:
        trigger = dated[-1]
    trigger = trigger or {}
    return {
        "disposition_category": category, "status_label": label,
        "is_terminated": bool(terminated) and category != "REOPENED",
        "litigation_stage": stage, "has_invalidity_contentions": has_invalidity,
        "invalidity_contention_details": invalidity_details,
        "date_filed": parse_date(date_filed), "date_terminated": terminated,
        "badge_color": "gray", "matched_keywords": sorted({v["term"] for v in glossary_hits.values()}),
        "glossary_hits": glossary_hits, "trigger_entry": trigger.get("text", ""),
        "trigger_date": trigger.get("date", ""), "trigger_entry_number": trigger.get("entry_number", ""),
        "trigger_source_url": trigger.get("source_url", ""), "trigger_source": trigger.get("source", ""),
        "trigger_date_basis": trigger.get("date_basis", ""),
        "all_trigger_entries": list(reversed(dated))[:8],
    }
