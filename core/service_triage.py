"""Local, explainable service triage. No cloud model or external API is called."""
import re


def latest_entries(entries, limit=7):
    def order(entry):
        number = re.match(r"\d+", str(entry.get("entry_number", "")))
        return entry["date"], int(number[0]) if number else -1
    seen, unique = set(), []
    for entry in sorted(entries, key=order, reverse=True):
        key = (entry["entry_number"], entry["date"], entry["description"])
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    return unique[:limit]


def analyze_docket(parsed, limit=7):
    entries = latest_entries(parsed.get("docket_entries", []), limit)
    result = {
        "engine": "Local rules (no language model)", "recommendation": "INSUFFICIENT_EVIDENCE",
        "title": "More evidence needed before choosing a service document",
        "reason": "At least six dated, identity-matched docket entries are required for this review.",
        "entries_reviewed": len(entries), "requested_entries": limit, "evidence": [], "documents": [],
        "human_review_required": True, "send_automatically": False,
        "limitations": [parsed.get("coverage", "Only visible docket entries were reviewed."),
                         "Docket descriptions do not establish infringement or patent validity. Review the claims, accused products, prior art and underlying filings with counsel.",
                         "Source update time is reported as displayed, not independently verified. No procedural deadline is calculated."],
    }
    if not parsed.get("identity_match", {}).get("verified"):
        result["reason"] = "The case number and party roles must match the source before analysis."
        return result
    if len(entries) < 6:
        return result
    by_number = {}
    for entry in parsed.get("docket_entries", []):
        previous = by_number.setdefault(entry["entry_number"], entry)
        if (previous["date"], previous["description"]) != (entry["date"], entry["description"]):
            result.update(title="Resolve conflicting docket rows first", reason="The page contains conflicting dates or descriptions for the same docket number.")
            return result
    ni, invalidity, hold = [], [], []
    for entry in entries:
        text = entry["description"].lower()
        reference = {k: entry[k] for k in ("entry_number", "date", "description", "source_url")}
        conditional = re.search(r"\b(?:proposed|denied|denying|not dismissed|not settled|not stayed|not reopened|shall|should|would|will|unless|if)\b", text)
        if not conditional and re.search(r"\border reopening\b|\b(?:case|action) (?:is |is hereby )?reopened\b|\border lifting (?:the )?stay\b", text):
            hold.append({**reference, "signal": "Reopening or lifted stay: confirm the current posture"})
        if not conditional and re.search(
            r"\border (?:of dismissal|dismissing (?:this |the )?(?:case|action)|staying (?:this |the )?(?:case|action))\b|"
            r"\b(?:case|action) (?:is |is hereby )?(?:dismissed|stayed|closed|terminated)\b|\bnotice of settlement\b(?!\s+conference)", text):
            hold.append({**reference, "signal": "Disposition or stay requiring current-status review"})
        # Topical mentions, not findings that a contention was served or a motion granted.
        if re.search(r"\b(?:non[- ]?infringement|infringement contentions|claim construction|markman|accused products?)\b", text):
            ni.append({**reference, "signal": "Infringement / claim-scope issue mentioned"})
        if re.search(r"\b(?:invalidity|invalidation|prior art|inter partes review|ipr|anticipation|obviousness)\b", text):
            invalidity.append({**reference, "signal": "Validity / prior-art issue mentioned"})
    if hold or parsed.get("date_terminated"):
        result.update(recommendation="REVIEW_STATUS", title="Review case status before outreach",
                      reason="A disposition, stay, settlement notice or termination date appears in the retrieved evidence. Confirm the current posture with counsel.", evidence=hold)
        return result
    result["evidence"] = ni + invalidity
    if ni:
        result["documents"].append({"title": "Non-infringement strategy brief for review",
            "outline": ["Identify asserted claims and the accused products or processes.",
                        "Map each asserted claim element to product evidence and flag missing elements.",
                        "Review claim construction, infringement theories and the underlying cited filings."]})
    if invalidity:
        result["documents"].append({"title": "Invalidation services / prior-art strategy brief for review",
            "outline": ["Confirm the asserted patents, claims and relevant priority dates.",
                        "Scope a patent and non-patent literature search with a claim-by-claim evidence chart.",
                        "Have counsel evaluate procedural options, timing, prior positions and underlying cited filings."]})
    if ni or invalidity:
        category = "BOTH_FOR_REVIEW" if ni and invalidity else "NON_INFRINGEMENT_REVIEW" if ni else "INVALIDATION_REVIEW"
        result.update(recommendation=category, title="Prepare " + ("both strategy briefs" if ni and invalidity else "a non-infringement brief" if ni else "an invalidation services brief") + " for human review",
                      reason="The latest visible entries mention the issues listed below. This supports reviewing a service proposal, not a conclusion on the merits or an instruction to send it.")
    else:
        result.update(title="No clear service signal in the selected entries",
                      reason="These entries do not provide a specific infringement or validity signal. Obtain the relevant filings before selecting a strategy document.")
    return result
