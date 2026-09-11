"""Stateless website discovery, local triage and optional Gemini litigation reports."""
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from core.browser_session import BrowserAccessError, browser_session
from core.browser_sources import SOURCES, google_url, parse_source, search_candidates, source_url
from core.evidence import case_key, verify_identity
from core.proxy_manager import ProxyManager
from core.service_triage import analyze_docket, latest_entries
from core.local_ai import analysis_config, local_ai_review
from core.pasted_docket import MAX_PASTE_BYTES, parse_pasted_docket
from core.gemini_report import DEFAULT_GEMINI_MODEL, gemini_report

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Patent Docket Review", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=ROOT / "static" / "review"), name="assets")


@app.middleware("http")
async def private_responses(request: Request, call_next):
    # Local app: reject cross-site requests and DNS rebinding hostnames.
    host = urlsplit("http://" + request.headers.get("host", "")).hostname
    if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        return JSONResponse({"error": "This application accepts local requests only."}, status_code=403)
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Cross-site requests are not allowed."}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "Send an application/json request."}, status_code=415)
        body_limit = MAX_PASTE_BYTES if request.url.path == "/api/analyze-paste" else 16384
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > body_limit:
                return JSONResponse({"error": "Pasted content exceeds the 2 MB limit." if body_limit == MAX_PASTE_BYTES else "Request exceeds the 16 KB limit."}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # FastAPI normally echoes invalid input; do not echo proxy credentials.
    errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse({"success": False, "error": "Check the case fields and settings.", "details": errors}, status_code=422)


class CaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    case_number: str = Field(min_length=1, max_length=100)
    plaintiff: str = Field(min_length=1, max_length=300)
    defendants: str = Field(min_length=1, max_length=300)
    court: str = Field(default="", max_length=200)
    proxy_string: str = Field(default="", max_length=1000, repr=False)

    @field_validator("case_number")
    @classmethod
    def valid_case(cls, value):
        if not case_key(value):
            raise ValueError("Enter a federal district case number, for example 1:25-cv-01337.")
        return value

    @field_validator("proxy_string")
    @classmethod
    def valid_proxy(cls, value):
        ProxyManager(value).get_playwright_proxy()
        return value

    def identity(self):
        return self.model_dump(include={"case_number", "plaintiff", "defendants", "court"})


class DiscoverRequest(CaseRequest):
    sources: list[Literal["courtlistener", "exparte", "unicourt", "pacermonitor", "law360"]] = Field(default_factory=lambda: list(SOURCES), min_length=1, max_length=5)


class AnalyzeRequest(CaseRequest):
    url: str = Field(min_length=1, max_length=2000)
    entry_limit: Literal[6, 7] = 7
    analysis_mode: Literal["rules", "local_ai", "gemini"] = "rules"
    gemini_api_key: SecretStr = Field(default=SecretStr(""), max_length=500, repr=False)
    gemini_model: str = Field(default=DEFAULT_GEMINI_MODEL, pattern=r"^gemini-[a-z0-9][a-z0-9.-]{0,90}$", max_length=100)

    @field_validator("url")
    @classmethod
    def valid_source(cls, value):
        return source_url(value)[1]


class PasteRequest(AnalyzeRequest):
    pasted_text: str = Field(default="", max_length=500000, repr=False)
    pasted_html: str = Field(default="", max_length=1500000, repr=False)


def access_failure(exc, stage, url):
    name = "Google" if stage == "google_search" else SOURCES[source_url(url)[0]]["name"]
    guidance = ("Use Open Google to search in your normal browser, then open a matching case. If the Review bot is also blocked, use Paste page content below."
                if stage == "google_search" else
                "Open this case in your normal browser and sign in there if needed. If its docket is visible, copy the page and use Paste page content below. A direct link does not transfer your login to the bot.")
    return {"status": exc.status, "failed_stage": stage, "blocked_source": name,
            "http_status": exc.http_status, "message": name + ": " + str(exc),
            "recovery_message": guidance, "open_url": url}


@app.get("/")
def index():
    return FileResponse(ROOT / "templates" / "review.html", media_type="text/html")


@app.get("/api/sources")
def sources():
    return {"sources": [{"id": key, "name": value["name"], "domain": value["domain"]} for key, value in SOURCES.items()]}


@app.get("/api/health")
def health():
    return {"service": "patent-docket-review", "status": "ok"}


@app.post("/api/discover")
def discover(data: DiscoverRequest):
    query_url = google_url(data.identity(), data.sources)
    result = {"success": True, "google_url": query_url, "candidates": [],
              "source_searches": [{"source": SOURCES[key]["name"], "url": google_url(data.identity(), [key])} for key in dict.fromkeys(data.sources)]}
    try:
        with browser_session(data.proxy_string) as browser:
            html, final_url = browser.read(query_url)
            if urlsplit(final_url).hostname not in {"google.com", "www.google.com"}:
                raise BrowserAccessError("search_unavailable", "Google requires an interactive step. Use Open Google and paste a case link below.")
            result["candidates"] = search_candidates(html, data.sources)
        result["status"] = "candidates_found" if result["candidates"] else "no_candidates"
        result["message"] = "Select a result to check the case identity and read its docket." if result["candidates"] else "No supported case links were found on this search page. Open Google or paste a direct case link."
    except BrowserAccessError as exc:
        result.update(access_failure(exc, "google_search", query_url))
    return result


@app.get("/api/analysis-config")
def get_analysis_config():
    return {"default_mode": "gemini", "local_ai": analysis_config(),
            "gemini": {"model": DEFAULT_GEMINI_MODEL, "requires_key": True}}


@app.post("/api/analyze")
def analyze(data: AnalyzeRequest):
    try:
        with browser_session(data.proxy_string) as browser:
            html, final_url = browser.read(data.url)
            try:
                source, final_url = source_url(final_url)
                if source != source_url(data.url)[0]:
                    raise ValueError()
            except ValueError:
                raise BrowserAccessError("redirected", "The site redirected away from the selected source's case page.") from None
            parsed = parse_source(html, final_url)
        return review_parsed(data, parsed)
    except BrowserAccessError as exc:
        return {"success": False, "source_url": data.url, **access_failure(exc, "case_page", data.url)}


def review_parsed(data, parsed):
    """Both routes use the same identity check; pasted evidence keeps its origin."""
    identity = verify_identity(data.case_number, data.court, data.plaintiff, data.defendants, parsed)
    parsed["identity_match"] = identity
    final_url = parsed["source_url"]
    if not identity["verified"]:
        return {"success": False, "status": "identity_mismatch", "message": identity["reason"], "identity_match": identity, "source_url": final_url}
    if parsed["source_id"] == "law360" and "/articles/" in urlsplit(final_url).path:
        return {"success": False, "status": "context_only", "message": "This Law360 result is an article. Use a docket page for dated docket-entry analysis.", "source_url": final_url}
    analysis = analyze_docket(parsed, data.entry_limit)
    if data.analysis_mode == "local_ai":
        analysis["ai_review"] = local_ai_review(parsed, analysis, data.entry_limit)
    elif data.analysis_mode == "gemini":
        analysis["litigation_report"] = gemini_report(parsed, data.gemini_api_key.get_secret_value(), data.gemini_model, data.entry_limit)
    visible_count = len(parsed["docket_entries"])
    parsed["docket_entries"] = latest_entries(parsed["docket_entries"], data.entry_limit)
    status = "docket_read" if visible_count else "metadata_only"
    if parsed.get("evidence_origin") == "user_paste":
        status = "pasted_docket" if visible_count else "pasted_metadata_only"
    return {"success": True, "status": status, "case": parsed, "visible_entry_count": visible_count, "analysis": analysis}


@app.post("/api/analyze-paste")
def analyze_paste(data: PasteRequest):
    if not data.pasted_html and not data.pasted_text:
        return JSONResponse({"success": False, "error": "Paste the visible case page, including its caption, number and docket rows."}, status_code=422)
    try:
        # No browser, source request, import store or file write is used on this path.
        parsed = parse_pasted_docket(data.pasted_html, data.pasted_text, data.url)
        return review_parsed(data, parsed)
    except BrowserAccessError as exc:
        return {"success": False, "status": "pasted_access_page", "message": "The pasted content is a login or block page. Copy the actual docket only after it is visible in your normal browser."}


if __name__ == "__main__":
    from core.server_launcher import launch
    raise SystemExit(launch(app))
