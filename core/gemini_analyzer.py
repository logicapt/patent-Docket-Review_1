import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_INSTRUCTION = """
You are a Senior Federal Patent Litigation Docket Analyst & Legal Tech Specialist.
Your task is to analyze raw federal court docket entries for patent infringement cases and produce a rigorous, verified analysis.

CRITICAL FOCUS:
1. Disposition Verification:
   - Check if the case is Terminated via Settlement ("joint stipulation of dismissal with prejudice pursuant to settlement agreement", "notice of settlement", etc.).
   - Check if dismissed with prejudice vs without prejudice.
   - Check if active (e.g. scheduling order, discovery, Markman hearing, pending motion to dismiss).
2. Patent Invalidity Contentions:
   - Carefully identify if the defendant served or filed "Invalidity Contentions", "Preliminary Invalidity Contentions", or disclosures under Patent Local Rules (e.g. P.L.R. 3-3, 35 U.S.C. 102/103/112 prior art charts).
3. Plain-English Client Summary:
   - Provide a clear, professional 1-2 sentence executive summary explaining the exact status of the lawsuit for client calls.

You MUST return valid JSON matching this exact structure:
{
  "verified_status": "Terminated (Settled) | Terminated (Dismissed with Prejudice) | Terminated (Dismissed without Prejudice) | Active (Invalidity Contentions Served) | Active (Pending) | Active (Stayed / IPR)",
  "disposition_category": "SETTLED | DISMISSED | ACTIVE | STAYED | MOTION_TO_DISMISS",
  "is_terminated": true | false,
  "termination_date": "YYYY-MM-DD or empty string",
  "has_invalidity_contentions": true | false,
  "invalidity_summary": "Explanation of invalidity contentions status or empty string",
  "matched_milestones": ["Invalidity Contentions", "Settlement", "Complaint", etc.],
  "client_executive_summary": "Plain-English summary of status for client calls",
  "confidence": "HIGH | MEDIUM | LOW",
  "key_quote": "Most decisive docket excerpt"
}
"""

class GeminiDocketAnalyzer:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key.strip() if api_key else ""
        self.client = None
        if self.api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Initialized Google GenAI client for Gemini docket analysis.")
            except Exception as e:
                logger.error(f"Failed to initialize google-genai client: {e}")

    def is_available(self) -> bool:
        return self.client is not None

    def analyze_docket(
        self,
        case_info: Dict[str, Any],
        docket_entries: List[Dict[str, Any]],
        fallback_analysis: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sends the case details and docket history to Gemini for AI-verified disposition and invalidity contention analysis.
        """
        if not self.is_available():
            return fallback_analysis or {}

        try:
            from google.genai import types

            entries_text = ""
            for idx, entry in enumerate(docket_entries[:20], 1):
                dt = entry.get("date_filed") or entry.get("date", "Unknown Date")
                desc = entry.get("description") or entry.get("text", "")
                entries_text += f"[{dt}] #{idx}: {desc}\n"

            prompt = f"""
Analyze the following patent litigation docket:
CASE NUMBER: {case_info.get('case_number')}
COURT: {case_info.get('jurisdiction')} ({case_info.get('court_code')})
PLAINTIFF: {case_info.get('plaintiff')}
DEFENDANT: {case_info.get('defendants')}
PATENT(S): {case_info.get('patent_numbers')} - {case_info.get('patent_title')}
DATE FILED: {case_info.get('date_filed')}
DATE TERMINATED: {case_info.get('date_terminated')}

DOCKET ENTRIES:
{entries_text or "No detailed docket entries available."}
"""

            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=ANALYSIS_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )

            raw_text = response.text
            parsed = json.loads(raw_text)
            # Model-generated text cannot establish source verification.
            parsed["ai_verified"] = False
            parsed["ai_analyzed"] = True
            return parsed

        except Exception as e:
            logger.warning(f"Gemini docket analysis failed or timed out: {e}")
            if fallback_analysis:
                fallback_copy = dict(fallback_analysis)
                fallback_copy["ai_verified"] = False
                fallback_copy["ai_error"] = str(e)
                return fallback_copy
            return {}
