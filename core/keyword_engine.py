import re
from typing import List, Dict, Any, Optional

LEGAL_GLOSSARY = {
    # 1. Starting the Case
    "complaint": {
        "term": "Complaint",
        "phase": "Case Initiation",
        "patterns": [r"\bcomplaint\b", r"\boriginal\s+complaint\b"],
        "definition": "The document that starts the lawsuit. It explains what the plaintiff says happened and what relief they want."
    },
    "summons": {
        "term": "Summons",
        "phase": "Case Initiation",
        "patterns": [r"\bsummons\b", r"\bsummons\s+issued\b"],
        "definition": "A formal court notice telling the defendant that a lawsuit has been filed and that they must respond."
    },
    "service_of_process": {
        "term": "Service / Service of process",
        "phase": "Case Initiation",
        "patterns": [r"\bservice\s+of\s+process\b", r"\bexecuted\s+service\b", r"\baffidavit\s+of\s+service\b"],
        "definition": "Official delivery of the complaint and summons to the defendant."
    },
    "proof_of_service": {
        "term": "Proof of service",
        "phase": "Case Initiation",
        "patterns": [r"\bproof\s+of\s+service\b", r"\bsummon(s)?\s+returned\s+executed\b"],
        "definition": "A filing showing when and how the legal papers were delivered."
    },
    "answer": {
        "term": "Answer",
        "phase": "Case Initiation",
        "patterns": [r"\banswer\s+to\s+complaint\b", r"\banswer\s+and\s+counterclaim(s)?\b", r"\bdefendant('s)?\s+answer\b"],
        "definition": "The defendant’s formal written response to the complaint."
    },
    "counterclaim": {
        "term": "Counterclaim",
        "phase": "Case Initiation",
        "patterns": [r"\bcounterclaim(s)?\b", r"\bcounter-claim\b"],
        "definition": "A claim the defendant makes back against the plaintiff in the same case (often for declaratory judgment of patent invalidity or non-infringement)."
    },
    "amended_complaint": {
        "term": "Amended complaint / amended pleading",
        "phase": "Case Initiation",
        "patterns": [r"\bamended\s+complaint\b", r"\bfirst\s+amended\s+complaint\b", r"\bamended\s+pleading\b"],
        "definition": "A revised version of a filing that corrects or adds allegations, patents, or claims."
    },

    # 2. Early Motions and Case Management
    "motion_to_dismiss": {
        "term": "Motion to dismiss",
        "phase": "Early Motions",
        "patterns": [r"\bmotion\s+to\s+dismiss\b", r"\brule\s+12\(b\)\(6\)\b", r"\bmotion\s+to\s+dismiss\s+granted\b", r"\bgranting\s+motion\s+to\s+dismiss\b"],
        "definition": "A request to throw out some or all of the case because of a legal defect (e.g. patent eligibility under 35 U.S.C. § 101 or failure to state a claim)."
    },
    "motion_to_transfer": {
        "term": "Motion to transfer venue",
        "phase": "Early Motions",
        "patterns": [r"\bmotion\s+to\s+transfer\b", r"\btransfer\s+venue\b", r"\b28\s+u\.?s\.?c\.?\s+1404\b", r"\bmotion\s+to\s+transfer\s+venue\b"],
        "definition": "A request to move the case to a different court or district (e.g. transfer from EDTX/WDTX to NDCA/DED)."
    },
    "motion_to_extend_time": {
        "term": "Motion to extend time",
        "phase": "Early Motions",
        "patterns": [r"\bmotion\s+to\s+extend\s+time\b", r"\bextension\s+of\s+time\b", r"\bunopposed\s+motion\s+for\s+extension\b"],
        "definition": "A request for more time to meet a deadline."
    },
    "scheduling_order": {
        "term": "Scheduling order",
        "phase": "Early Motions",
        "patterns": [r"\bscheduling\s+order\b", r"\bdocket\s+control\s+order\b", r"\bcase\s+management\s+order\b"],
        "definition": "The court’s timetable for the case, setting firm deadlines for invalidity contentions, claim construction, discovery, motions, and trial."
    },
    "status_report": {
        "term": "Status report",
        "phase": "Early Motions",
        "patterns": [r"\bstatus\s+report\b", r"\bjoint\s+status\s+report\b"],
        "definition": "A filing updating the court on where the case stands."
    },
    "pro_hac_vice": {
        "term": "Pro hac vice",
        "phase": "Early Motions",
        "patterns": [r"\bpro\s+hac\s+vice\b", r"\bappearance\s+pro\s+hac\s+vice\b"],
        "definition": "Permission for an out-of-state patent lawyer to appear in this specific federal district court."
    },

    # 3. Patent Contentions & Patent Local Rules (CRITICAL FOR PATENT DOCKET ANALYSIS)
    "invalidity_contentions": {
        "term": "Invalidity Contentions",
        "phase": "Patent Contentions",
        "patterns": [
            r"\binvalidity\s+contention(s)?\b",
            r"\bpreliminary\s+invalidity\s+contention(s)?\b",
            r"\bpatent\s+local\s+rule\s+3-3\b",
            r"\bp\.?l\.?r\.?\s+3-3\b",
            r"\bserved\s+invalidity\s+contentions\b",
            r"\bnotice\s+of\s+service\s+of\s+invalidity\s+contentions\b",
            r"\binvalidity\s+and\s+unenforceability\s+contentions\b"
        ],
        "definition": "The formal patent disclosure where defendant reveals their prior art references, claim charts, obviousness combinations (103), anticipation (102), and § 101 / § 112 invalidity positions."
    },
    "infringement_contentions": {
        "term": "Infringement Contentions",
        "phase": "Patent Contentions",
        "patterns": [
            r"\binfringement\s+contention(s)?\b",
            r"\bpreliminary\s+infringement\s+contention(s)?\b",
            r"\bpatent\s+local\s+rule\s+3-1\b",
            r"\bp\.?l\.?r\.?\s+3-1\b",
            r"\bdisclosure\s+of\s+asserted\s+claims\b"
        ],
        "definition": "The plaintiff's detailed claim charts mapping each patent claim limitation to the defendant's accused products."
    },
    "claim_construction_markman": {
        "term": "Claim Construction / Markman",
        "phase": "Patent Contentions",
        "patterns": [
            r"\bmarkman\b",
            r"\bclaim\s+construction\b",
            r"\bjoint\s+claim\s+construction\b",
            r"\bmarkman\s+hearing\b",
            r"\bclaim\s+construction\s+order\b"
        ],
        "definition": "The court proceeding where the judge defines the technical meaning and scope of disputed patent claim terms."
    },
    "ipr_ptab_stay": {
        "term": "Inter Partes Review (IPR) / PTAB Stay",
        "phase": "Patent Contentions",
        "patterns": [
            r"\binter\s+partes\s+review\b",
            r"\bipr\b",
            r"\bptab\b",
            r"\bstayed\s+pending\s+ipr\b",
            r"\bpetition\s+for\s+inter\s+partes\s+review\b"
        ],
        "definition": "Parallel administrative patent validity challenge filed at the USPTO Patent Trial and Appeal Board (PTAB), frequently pausing the court lawsuit."
    },

    # 4. Discovery Phase
    "interrogatories": {
        "term": "Interrogatories",
        "phase": "Discovery",
        "patterns": [r"\binterrogator(y|ies)\b", r"\bresponses\s+to\s+interrogatories\b"],
        "definition": "Written questions that the opposing party must answer in writing under oath."
    },
    "request_for_production": {
        "term": "Request for production",
        "phase": "Discovery",
        "patterns": [r"\brequest\s+for\s+production\b", r"\brfp\b", r"\brequests\s+for\s+production\s+of\s+documents\b"],
        "definition": "A demand for source code, technical schematics, sales data, licenses, or emails relevant to the case."
    },
    "deposition": {
        "term": "Deposition",
        "phase": "Discovery",
        "patterns": [r"\bdeposition\b", r"\bnotice\s+of\s+deposition\b", r"\brule\s+30\(b\)\(6\)\b"],
        "definition": "Sworn out-of-court oral testimony where inventors, corporate reps, or technical experts answer questions recorded by a court reporter."
    },
    "subpoena": {
        "term": "Subpoena",
        "phase": "Discovery",
        "patterns": [r"\bsubpoena\b", r"\bsubpoena\s+returned\s+executed\b"],
        "definition": "A court order requiring a third-party witness (e.g. chip foundry, standard setting body) to testify or produce documents."
    },
    "motion_to_compel": {
        "term": "Motion to compel",
        "phase": "Discovery",
        "patterns": [r"\bmotion\s+to\s+compel\b", r"\bcompel\s+discovery\b", r"\bmotion\s+to\s+compel\s+production\b"],
        "definition": "A formal request asking the judge to force the other side to produce withheld source code, documents, or answers."
    },
    "protective_order": {
        "term": "Motion for protective order",
        "phase": "Discovery",
        "patterns": [r"\bprotective\s+order\b", r"\bmotion\s+for\s+protective\s+order\b", r"\bstipulated\s+protective\s+order\b"],
        "definition": "A court order protecting confidential technical trade secrets, source code, and pricing data from public disclosure."
    },

    # 5. Pretrial Motions
    "summary_judgment": {
        "term": "Motion for summary judgment",
        "phase": "Pretrial Motions",
        "patterns": [
            r"\bmotion\s+for\s+summary\s+judgment\b",
            r"\bsummary\s+judgment\s+of\s+invalidity\b",
            r"\bsummary\s+judgment\s+of\s+non-infringement\b",
            r"\brule\s+56\b"
        ],
        "definition": "A request asking the court to rule that patents are invalid or not infringed as a matter of law without needing a jury trial."
    },
    "motion_in_limine": {
        "term": "Motion in limine",
        "phase": "Pretrial Motions",
        "patterns": [r"\bmotion\s+in\s+limine\b", r"\bmotions\s+in\s+limine\b"],
        "definition": "A request asking the judge before trial to exclude prejudicial or improper testimony (e.g. damages opinions or foreign litigation results)."
    },
    "settlement_conference": {
        "term": "Settlement conference",
        "phase": "Pretrial Motions",
        "patterns": [r"\bsettlement\s+conference\b", r"\bmediation\b", r"\bmediator('s)?\s+report\b"],
        "definition": "A court-supervised negotiation session (often with a Magistrate Judge or mediator) to settle the dispute without trial."
    },

    # 6. Trial Terms
    "trial_hearing": {
        "term": "Hearing / Trial",
        "phase": "Trial",
        "patterns": [r"\bbench\s+trial\b", r"\bjury\s+trial\b", r"\bpretrial\s+conference\b", r"\btrial\s+minutes\b"],
        "definition": "Formal court trial proceedings before the judge or jury."
    },
    "verdict": {
        "term": "Verdict",
        "phase": "Trial",
        "patterns": [r"\bverdict\b", r"\bjury\s+verdict\b", r"\bspecial\s+verdict\b"],
        "definition": "The jury’s final factual decision finding infringement, willful infringement, patent validity, and damages."
    },
    "judgment": {
        "term": "Judgment",
        "phase": "Trial",
        "patterns": [r"\bfinal\s+judgment\b", r"\bjudgment\s+in\s+favor\s+of\b", r"\bclerk('s)?\s+judgment\b"],
        "definition": "The court's official final decree resolving the dispute."
    },
    "injunction": {
        "term": "Injunction",
        "phase": "Trial",
        "patterns": [r"\bpermanent\s+injunction\b", r"\bpreliminary\s+injunction\b", r"\binjunction\b"],
        "definition": "A court order prohibiting the defendant from making, using, selling, or importing the infringing technology."
    },

    # 7. Post-Judgment & Final Disposition
    "settlement": {
        "term": "Settlement",
        "phase": "Post-Judgment & Disposition",
        "patterns": [
            r"\bsettlement\b",
            r"\bsettled\b",
            r"\bsettlement\s+agreement\b",
            r"\bcourt\s+settlement\b",
            r"\bjoint\s+stipulation\s+of\s+dismissal\b",
            r"\bstipulation\s+of\s+dismissal\s+with\s+prejudice\b",
            r"\bclosed\s+pursuant\s+to\s+settlement\b",
            r"\bterms\s+of\s+settlement\b"
        ],
        "definition": "Voluntary agreement between plaintiff and defendant resolving all claims (usually involving a patent license or release) and ending the lawsuit."
    },
    "dismissed_with_prejudice": {
        "term": "Dismissed with prejudice",
        "phase": "Post-Judgment & Disposition",
        "patterns": [r"\bdismissed\s+with\s+prejudice\b", r"\border\s+of\s+dismissal\s+with\s+prejudice\b"],
        "definition": "The case is permanently closed for good and the plaintiff cannot file a lawsuit again on these patent claims."
    },
    "dismissed_without_prejudice": {
        "term": "Dismissed without prejudice",
        "phase": "Post-Judgment & Disposition",
        "patterns": [r"\bdismissed\s+without\s+prejudice\b", r"\border\s+of\s+dismissal\s+without\s+prejudice\b"],
        "definition": "The lawsuit is dismissed, but the plaintiff retains the legal right to refile against the defendant."
    },
    "appeal": {
        "term": "Appeal",
        "phase": "Post-Judgment & Disposition",
        "patterns": [r"\bnotice\s+of\s+appeal\b", r"\bappeal\s+to\s+cafc\b", r"\bfederal\s+circuit\s+appeal\b"],
        "definition": "A petition asking the U.S. Court of Appeals for the Federal Circuit (CAFC) to overturn the district judge’s decision."
    },
    "stay": {
        "term": "Stay",
        "phase": "Post-Judgment & Disposition",
        "patterns": [r"\bcase\s+stayed\b", r"\bmotion\s+to\s+stay\s+granted\b", r"\border\s+staying\s+case\b", r"\bstayed\b"],
        "definition": "A formal suspension or temporary pause in all court proceedings."
    },
    "remand": {
        "term": "Remand",
        "phase": "Post-Judgment & Disposition",
        "patterns": [r"\bremand\b", r"\bremanded\b", r"\bremand\s+from\s+cafc\b"],
        "definition": "When the appellate court (CAFC) sends the case back down to the district court for further trial or proceedings."
    }
}

class KeywordEngine:
    def __init__(self, custom_glossary: Optional[Dict[str, Any]] = None):
        self.glossary = custom_glossary or LEGAL_GLOSSARY

    def analyze_text(self, text: str) -> Dict[str, Any]:
        """
        Analyzes a single docket text entry against the comprehensive legal glossary.
        """
        if not text:
            return {"matches": {}, "has_match": False}

        matches = {}
        for key, item in self.glossary.items():
            matched_substrings = []
            for pat in item["patterns"]:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    matched_substrings.append(m.group(0))
            if matched_substrings:
                matches[key] = {
                    "term": item["term"],
                    "phase": item["phase"],
                    "definition": item["definition"],
                    "found_tokens": list(set(matched_substrings))
                }

        return {
            "matches": matches,
            "has_match": len(matches) > 0
        }

    def evaluate_case_disposition(self, date_filed, date_terminated, docket_entries):
        from .disposition import evaluate
        return evaluate(self, date_filed, date_terminated, docket_entries)
