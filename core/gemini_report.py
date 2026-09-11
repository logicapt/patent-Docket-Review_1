"""Opt-in Gemini generation: one request, no saved keys, chats, files or reports."""
import json
import re
import threading

import httpx
from google import genai
from google.genai import errors, types

from .litigation_report import (GeminiReport, MAX_REPORT_INPUT, MAX_REPORT_OUTPUT,
                                SYSTEM_PROMPT, report_evidence, validate_report)


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
_slots = threading.BoundedSemaphore(2)


def gemini_report(parsed, api_key, model=DEFAULT_GEMINI_MODEL, limit=7):
    base = {"status": "unavailable", "engine": "Google Gemini", "human_review_required": True}
    if not api_key or not api_key.strip():
        return {**base, "status": "setup_required", "message": "Enter your Gemini API key to generate the litigation report. The docket and local review remain available."}
    if not re.fullmatch(r"gemini-[a-z0-9][a-z0-9.-]{0,90}", model):
        return {**base, "message": "Enter a valid Gemini model ID."}
    if not parsed.get("identity_match", {}).get("verified"):
        return {**base, "status": "skipped", "message": "Match the case identity before sending docket evidence to Gemini."}
    payload, records, audit, selected = report_evidence(parsed, limit)
    if audit["conflicting_entries"] or not selected:
        return {**base, "status": "skipped", "message": "Resolve conflicting docket rows or provide dated docket entries before generating a report."}
    content = json.dumps(payload, ensure_ascii=False)
    if len(content) > MAX_REPORT_INPUT:
        return {**base, "status": "input_too_large", "message": "The extracted evidence exceeds the report input limit. Use a smaller visible docket excerpt with its case header; no partial evidence was sent."}
    if not _slots.acquire(blocking=False):
        return {**base, "status": "busy", "message": "Two Gemini reports are already running. Try again when one finishes."}
    try:
        # Fixed provider endpoint; no source proxy, ambient API key or saved session.
        options = types.HttpOptions(base_url="https://generativelanguage.googleapis.com", api_version="v1beta",
                                    timeout=120000, retry_options=types.HttpRetryOptions(attempts=1),
                                    client_args={"trust_env": False})
        with genai.Client(api_key=api_key.strip(), vertexai=False, enterprise=False, http_options=options) as client:
            response = client.models.generate_content(model=model, contents=content,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json", response_json_schema=GeminiReport.model_json_schema(),
                    max_output_tokens=16384, candidate_count=1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            if not response.candidates or str(response.candidates[0].finish_reason).split(".")[-1] != "STOP":
                return {**base, "status": "incomplete", "message": "Gemini did not finish a complete report. The docket remains available; no partial report is shown."}
            output = response.text
            if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_REPORT_OUTPUT:
                raise ValueError("Invalid report size")
        return {**base, **validate_report(output, parsed, records, audit, selected), "model": model}
    except errors.APIError as exc:
        code = exc.code
        if code in (400, 401, 403):
            message = "Gemini rejected the request. Check your API key, API access, model and billing settings."
        elif code == 404:
            message = "This Gemini model is unavailable for your key. Check the model ID in review settings."
        elif code == 429:
            message = "Gemini quota or rate limit reached. Check your Google quota/billing or retry later."
        else:
            message = "Gemini is temporarily unavailable. The docket and local review remain available."
        return {**base, "status": "provider_error", "message": message}
    except (httpx.TimeoutException, TimeoutError):
        return {**base, "status": "timeout", "message": "The Gemini request timed out. The docket remains available."}
    except (ValueError, TypeError, OSError, httpx.HTTPError):
        # Never expose raw SDK exceptions, prompts, response text or secrets.
        return {**base, "status": "invalid_report", "message": "Gemini could not return a complete report with matching source quotations and the required status safeguards. The docket and local review remain available."}
    finally:
        _slots.release()
