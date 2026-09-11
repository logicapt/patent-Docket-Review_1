# Patent Docket Review

A local FastAPI web app for Google case discovery, browser-based docket extraction, and a **Case Status & Litigation Recommendation Report** generated with your Google Gemini API key. Case retrieval uses website pages rather than court-data APIs. Gemini mode sends extracted evidence to Google for analysis; local rules and optional on-device AI are also available. The app does not save docket files, API keys or a review history.

## Start

From this folder:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

Open http://127.0.0.1:5000, or double-click `run.bat` after installing dependencies. Restart an existing server after updating the code. The browser installation downloads software, not case data.

Keep the server terminal open while using the app; press **Ctrl+C** in that terminal to stop it. If another copy is already running, the launcher prints its address and exits cleanly. If another program occupies port 5000, select a different port:

```powershell
python app.py --port 5001
```

Then open http://127.0.0.1:5001. The batch launcher also accepts a port: `run.bat --port 5001`. It opens the browser after the server is ready. The launcher never terminates another process automatically.

You can use an installed Chrome or Edge instead of the bundled Chromium:

```powershell
$env:DOCKET_BROWSER_CHANNEL = 'chrome'  # or msedge
python app.py
```

## Workflow

1. Enter the federal district case number, plaintiff, and defendant. Court is recommended because docket numbers can repeat between courts. Use semicolons for multiple parties.
2. Select CourtListener, Ex Parte AI Lab, UniCourt, PacerMonitor, and/or Law360.
3. Enter your **Gemini API key**, or choose a different analysis mode under **Proxy & review settings**. Click **Search Google**. The discovery engine reads Google's result page in a temporary browser and extracts supported case links. Results are candidates, not verified cases. Your API key is not sent with search requests.
4. Click **Review bot** on a result. The browser opens that case page, reads its loaded HTML, and closes. The parser checks the case number, plaintiff/defendant roles, and court if supplied. In Gemini mode, extracted evidence is then analyzed automatically and the structured report appears on the page. No extra API call or separate report command is needed.
5. Review the source citation when provided, source update/check time, retrieval time, separate filed/entered/docket dates, entry numbers, descriptions, and source links.
6. The latest six or seven visible dated entries are sorted by entered date when present, otherwise filed date, otherwise the source's generic docket date, then by numeric docket number. At least six are required for a service suggestion. Duplicate rows do not inflate the count. Gemini reports also screen all readable dated entries on the loaded page for status events, including older transfer orders.
7. **Clear session** clears form fields, proxy input, candidates and displayed results. No results are restored after refresh.

**Open Google** opens a normal Google search without waiting for automation. If automated search is blocked, find a case yourself and paste its URL into **Already have a case link?** A direct link skips Google; it does not solve source-site access restrictions.

### When a case page blocks the Review bot

Google links and the case reader are separate steps. A working search link does not mean a case website accepts the automated browser. Access errors now identify the source and provide a normal-browser link plus a **Paste page content** option.

1. Open the matching case in your normal browser and sign in there if required. The docket must be visible to you.
2. Click the page outside any input field, then press **Ctrl+A**, **Ctrl+C**. Include the case caption, number, court, docket column headings and the newest rows you want reviewed.
3. In this app, expand **Paste page content**, enter the source case URL and paste with **Ctrl+V**. Normal webpage copying usually includes HTML table formatting along with the displayed text; the app reads it only from your explicit paste action.
4. Click **Analyze pasted docket**. The same identity check and six/seven-entry review apply. Results are labelled **User-supplied**, with a provided-at timestamp rather than an online retrieval timestamp. Authenticity, completeness and freshness are not independently verified.

This route makes no source-site request and saves no docket file. It sends your copy to the local backend for temporary parsing and analysis, then clears the paste input. **Gemini mode also sends extracted evidence from the copy to Google.** Re-enter your Gemini key if a previous Review bot submission cleared it. This option cannot retrieve content that is inaccessible in your normal browser. Copy only the case page, not passwords or account settings. The app does not read, clear or manage your operating-system clipboard history.

Formatted clipboard HTML uses the existing source parsers. Plain text supports labelled, tab-separated docket columns; line-wrapped prose is not treated as a reliable docket table. Editing the pasted text discards the hidden HTML and uses the displayed plain text. Unsupported layouts return metadata-only or an identity mismatch instead of inventing entries. Copies are limited to 500,000 text characters, 1,500,000 HTML characters and a 2 MB request body.

## Architecture

```mermaid
flowchart TD
    U[User] --> W[Web application]
    W --> F[FastAPI backend]
    F --> D[Case Discovery Engine]
    F --> X[Selected case reader]
    D --> P[IP Proxy Manager]
    X --> P
    P --> B[Temporary Playwright browser]
    B --> G[Google search page]
    B --> CL[CourtListener]
    B --> EX[Ex Parte AI Lab]
    B --> UC[UniCourt]
    B --> PM[PacerMonitor]
    B --> L[Law360]
    B --> V[Parse HTML and match case identity]
    U --> N[Normal browser: visible case page]
    N --> C[User copies and pastes page content]
    C --> W
    F --> T[Read pasted content in memory]
    T --> V
    V --> A[Local Analysis Engine]
    A --> R[Evidence and service suggestions]
    V --> S[Visible docket status screening]
    S --> GM[Google Gemini API with user-supplied key]
    GM --> Q[Validate quotations and required report fields]
    Q --> R
    R --> W
```

The web interface selects **Gemini litigation report** by default and requires a user-entered API key before a review. Choosing **Local rules** uses deterministic rules with no cloud analysis. **Local AI + rules** runs an existing GGUF model on this computer through `llama-cpp-python`. For compatibility, backend requests that omit `analysis_mode` still use local rules. No CourtListener REST API or other court-data API is used by these routes; websites may make their own JavaScript requests while rendering.

### Gemini report setup and output

1. Install/update `requirements.txt`, restart `python app.py`, and refresh the page.
2. Create a key in [Google AI Studio](https://aistudio.google.com/apikey), following [Google's API-key instructions](https://ai.google.dev/gemini-api/docs/api-key).
3. Enter it in **Gemini API key** inside the app. The default model is [`gemini-3.5-flash`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash); the model field accepts another Gemini model ID supported by your account and structured outputs.
4. Search for a case and click **Review bot**, or use **Analyze pasted docket** for a visible page copied from your normal browser. The key input is cleared on submission; re-enter it for another request. It is never saved in browser storage or a server configuration file.

The report contains **Case Status**, **Strategic Action**, **Conclusive Summary**, **Recommended Action Plan**, **Parties** (including counsel/type when supported), and **Case Facts** (case number, court, presiding judge, patents, filing/disposition dates, current stage, contentions status and the recent docket event). Expand **Supporting evidence** to see the quoted source text and docket/date references.

Status screening uses explicit procedural events across all readable dated entries on the loaded page, including entries outside the recent service window. A transfer order yields **TRANSFERRED / Analyze Transfer & Venue Defense** and takes precedence over ordinary service selection. Pending motions, proposals and denials are not completed transfers or dismissals. A later reopening or possible reversal requires status review. A restrictive source status label without a readable order requires review rather than a definitive classification. No decisive status evidence yields **NOT_ESTABLISHED**, not an assumed active case. This is a bounded rule-based screening, not a mandatory external audit or verification of the complete court record.

Gemini receives the selected recent entries, detected status events/uncertain procedural entries and labelled case metadata. Every narrative section must cite matching evidence IDs and verbatim quotations. Factual fields require a literal excerpt from cited evidence. Missing counsel, plaintiff type, judge, patent numbers and dates remain unknown. A year embedded in a case number is not accepted as a filing date. Transfer does not establish a merits disposition date. Unsupported statute references in the summary/action plan are rejected. These checks establish quotation correspondence, not the truth of an AI interpretation; human review is still required. The sample transfer report supplied during development is not evidence about a real case and is not hardcoded into reports.

Fewer than six entries may support a limited status report, but cannot support an infringement/invalidation service focus. No readable dated entries, a mismatched case or conflicting docket rows prevents the Gemini call. A link or search snippet alone is not analyzed as a docket. The Gemini key does not unblock a case website, transfer your website login or fetch missing attachments.

The integration uses the official [Google Gen AI SDK](https://googleapis.github.io/python-genai/) with JSON schema output, a fixed Google endpoint, one call per report, automatic retries disabled, a 120-second HTTP timeout and at most two concurrent calls. Input above 100,000 characters is rejected without sending a shortened version. The output allowance is 16,384 tokens with a 96,000-byte validation cap; incomplete output is not displayed as a report. No API-key test call, paid retry, fallback model, chat session, Files API upload, explicit prompt cache, grounding tool or automatic document send is used. Authentication, quota, timeout and invalid-output errors leave the extracted docket available.

**Google receives the extracted evidence in Gemini mode.** Google's applicable API data handling and billing govern that processing; the app's no-local-file behavior is not a promise of zero retention at Google. The app sends the key only to its local backend and Google's API, excludes it from discovery/AI prompt content, and redacts it from validation errors. The browser's optional source proxy is not applied to Gemini calls. Clearing the tab cannot retract a request already sent to Google.

Gemini request serialization, responses, validation and UI behavior are tested with synthetic fixtures and a mocked HTTP transport. A real paid/provider request has not been run because no user key was provided.

### Optional on-device AI

To enable the AI mode, install the optional inference library and configure the absolute path to an existing, instruction-tuned GGUF model compatible with llama.cpp:

```powershell
python -m pip install -r requirements-local-ai.txt
$env:DOCKET_LOCAL_MODEL_PATH = 'C:\models\your-instruct-model.gguf'
python app.py
```

On Windows, building the library may require a C/C++ toolchain. The project documents installation options, including prebuilt wheels, in its [official installation guide](https://llama-cpp-python.readthedocs.io/en/latest/#installation). This app never automatically downloads a model. The model and library are software stored on disk; docket text is passed through memory and pipes only.

After restarting the server, select **Proxy & review settings → Analysis mode → Local AI + rules**. Configuration enables the option; actual model compatibility and memory availability are checked during inference. With no configured model, the normal rule-based workflow continues to work.

The optional AI reads only the selected six/seven dated, identity-matched entries. It adds a proposed service focus, a summary, exact supporting quotations, and questions for human review. Every cited entry number and quote is checked against the supplied source entries; the backend supplies dates and URLs. Invalid references, malformed responses and truncated output are rejected. Interpretation can still be wrong even when a quote is exact. AI output is shown separately and cannot change case identity, docket metadata, source descriptions, or the original rule-based recommendation. Disagreement is displayed explicitly.

One AI process runs at a time. Each review loads its own model context and exits after completion; it has a hard two-minute limit including model loading. There is no shared conversation/KV cache, cloud server, prompt log, model download, or saved AI output. CPU inference may exceed this limit for large models. Inputs above 16,000 characters or the model token allowance are declined rather than silently shortened. Conflicting rows, fewer than six usable entries and a rule-based status hold skip AI review. Setup failures and timeouts retain the original docket and rule-based result.

No model file or inference library was available in the development environment when this integration was added. The process lifecycle, validation and UI integration are covered by synthetic/mocked tests; real-model inference remains unverified until a compatible model is configured.

### IP proxy

“IP proximity” is implemented as an optional IP proxy configuration:

- HTTP/HTTPS: `http://host:port`, `https://host:port`, or `http://user:password@host:port`.
- SOCKS5: `socks5://host:port` without username/password; Chromium does not support SOCKS5 authentication through this configuration.
- One proxy per request/browser session. Proxy credentials are separated from the server address and are not saved or echoed in error responses.
- No rotation on blocks, CAPTCHA solving, paywall bypass, or automatic account actions. No proxies are supplied or purchased.
- This controls the browser's network route; it does not choose geographically nearby IPs or calculate intellectual-property similarity.

## Source support and limits

| Source | Extraction path | Limit |
| --- | --- | --- |
| CourtListener / RECAP | Public legacy and current docket DOM, plus semantic tables; requests the website's descending sort | Archive pages may be incomplete, stale, or blocked; party tabs, document downloads, and extra pages are not fetched. |
| Ex Parte AI Lab | Case headings/labels and semantic docket tables or ARIA grids | Requires those structures to be available in the loaded page. Other layouts return metadata-only or unverified. |
| UniCourt | Case labels and semantic docket tables/ARIA grids | Accessible account content and site layouts vary; authenticated pages have not been validated. |
| PacerMonitor | Existing source-specific date-heading parser and semantic tables | A normal-browser login is not shared. Restricted pages return an access error. |
| Law360 | Discovery of case/article links; semantic case docket tables if present | Articles are context-only and never analyzed as docket entries. |

The configured AI Lab domain is [ai-lab.exparte.com](https://ai-lab.exparte.com/search).

The browser reads one loaded page per review. CourtListener links use the public website's `order_by=desc` control to request its newest page. Other sources use the selected page's default view. It does not paginate, click “show all”, expand hidden entries, refresh a paid docket, or open attachments. **Latest visible** does not establish the latest entries in the court's complete docket. Missing citation/update/date fields remain missing; retrieval time never substitutes for a source update time. A citation is only shown when the source labels one, not synthesized from the case number.

Automated requests are bounded to two concurrent browsers, a 30-second navigation timeout, an 8 MB HTML extraction limit, and a 16 KB local request body. The separate paste endpoint allows a 2 MB request body and does not launch a browser. Rendered pages have a short, bounded wait; slowly loaded or unsupported rows may be unavailable. Only the named source hosts and Google rendering hosts are allowed. Off-site resources can therefore prevent some sites from fully rendering.

A website login, subscription, access challenge, or rate limit may prevent scraping. The browser stops on access errors and reports them; it does not assume an ordinary website subscription includes automation rights. Use sites within the access and reuse permissions available to you. A crawler library does not grant access rights or guarantee current docket coverage.

**Live verification on September 10, 2026:** both Google discovery and an Ex Parte case-page read returned access blocks from this development machine using Playwright. No live docket was retrieved from those tests. Successful extraction/recommendation checks use synthetic fixtures. CourtListener selectors were checked against its public HTML templates; this does not prove live access. UniCourt and Law360 account layouts have not been verified.

## What the recommendation means

The engine can suggest:

- Prepare a **non-infringement strategy brief** for human review when selected entries mention claim construction, infringement contentions, accused products, or non-infringement.
- Prepare an **invalidation services / prior-art strategy brief** when selected entries mention invalidity, prior art, IPR, anticipation, or obviousness.
- Prepare both briefs when both sets of issues appear.
- Review current status before outreach when selected entries show a disposition, stay, settlement notice, reopening, or source termination metadata.
- Obtain more evidence when identity is unverified, fewer than six usable entries exist, duplicate entry numbers conflict, or the selected entries lack specific signals.

Each suggestion includes the triggering source entries and a short proposed outline. A topical mention does not establish that a motion was granted, a deadline remains open, or a contention was served. The engine does not determine patent validity, produce a merits opinion, draft a completed legal argument, calculate procedural deadlines, or send documents.

Infringement requires comparing asserted claims with the accused product/process; a docket excerpt cannot supply that comparison. See [USPTO: Managing a patent](https://www.uspto.gov/patents/basics/manage). Invalidity review needs the relevant claims and evidence, including pertinent prior art; see [USPTO: Citation of prior art](https://www.uspto.gov/web/offices/pac/mpep/s2202.html).

## Data handling

The application deliberately has no database, file upload/export endpoints, results cache, docket file writes, saved cookies, persistent browser profile, trace/HAR recording, screenshots, or analysis history in its default workflow. The Gemini API-key input is transient and cleared on submission. Responses use `Cache-Control: no-store`; the UI uses neither localStorage nor sessionStorage. Download acceptance and browser HTTP caching are disabled. The default launcher disables HTTP access logs, and handled browser/provider exceptions do not expose credentials or raw response content.

**Temporary processing is necessary.** Reading a page transfers bytes into browser/backend memory. Backend request objects and the response exist until their request lifecycle ends; displayed results remain in the user's tab until cleared. Clear cancels the UI request and removes its results. A browser read already running on the server finishes or times out and then closes its context. There is no application-level retention afterward, but this is not secure memory erasure.

A strict promise that nothing ever touches disk cannot be made: Chromium and the OS may create temporary files, page browser or model memory to disk, or retain diagnostic information outside application control. If docket text must never enter the backend at all, this architecture needs a browser extension with client-side extraction and analysis instead. Clearing an AI request removes its displayed result immediately; server computation finishes or is killed at the two-minute limit, then its process memory is released.

The browser software and dependencies are installed on disk. Offline tests use synthetic case data; the optional UI screenshot flag saves a synthetic test image, not retrieved docket data.

## Repository reuse

No specific scraper repository URL was supplied. This implementation uses the existing project's PacerMonitor parser plus [Microsoft Playwright for Python](https://github.com/microsoft/playwright-python), an Apache-2.0 browser-automation library, and BeautifulSoup. It does not copy an unspecified crawler repository or assume its license.

CourtListener DOM selectors were inspected against these public templates:

- [Legacy docket rows](https://github.com/freelawproject/courtlistener/blob/main/cl/opinion_page/templates/includes/de_list.html)
- [Current docket rows](https://github.com/freelawproject/courtlistener/blob/main/cl/opinion_page/templates/cotton/docket_entry_rows.html)

See also [Playwright browser contexts](https://playwright.dev/python/docs/api/class-browser#browser-new-context). Further crawler reuse should be evaluated against the exact repository, license, caching defaults, and source adapters.

## Verification

```powershell
python test_server.py
python tests/review_browser_smoke.py
```

The regression suite covers source matching, separate dates, table/DOM extraction, duplicate and conflicting entries, selection of the latest six/seven entries, evidence-linked recommendations, blocked/login/rate-limited states, no legacy API fallback, source URL validation, proxy redaction, request limits and browser cleanup. Paste tests check HTML/plain-text extraction, case mismatch rejection, explicit provenance, content limits, absence of source requests/file writes and rejection of pasted challenges. The Chromium UI smoke test runs against FastAPI with synthetic source pages and covers search → review → clear, local AI integration, blocked case → pasted docket recovery, mobile overflow, script-injection resistance and absence of browser storage. It simulates clipboard events without reading or changing the operating-system clipboard.

Gemini checks cover older transfer orders, pending versus completed events, reopening/vacatur, status priority, exact citations, unknown facts, filing-date safeguards, API-key redaction, no call on blocked/mismatched cases, real SDK serialization through mocked HTTP, quota/authentication/timeout handling and truncated responses. The browser smoke test also verifies the Gemini report sections, no key in Google discovery requests, key clearing, escaped model text and mobile layout.

## Previous spreadsheet workflow

The original Flask app is preserved as `legacy_app.py` and its instructions as [README-legacy.md](README-legacy.md). Run `python legacy_app.py` separately if you need the old spreadsheet import/export workflow. It uses the same port, so stop the other server first.

**The legacy app has different data handling:** it can call third-party APIs, save uploaded/imported data under `uploads/`, and export files. It is not used or imported by the new FastAPI app. Existing user files were left in place. Legacy tests remain separate and retain their previous behavior.

## Main files

- `app.py`: FastAPI routes, input limits, source verification and temporary results.
- `core/browser_session.py`: disposable browser, fixed proxy, host restrictions and access diagnostics.
- `core/browser_sources.py`: Google links, source allowlist, metadata and docket HTML parsing.
- `core/pasted_docket.py`: temporary parsing of user-pasted HTML or labelled tab-separated text, with explicit evidence provenance.
- `core/service_triage.py`: local six/seven-entry triage and source-linked review suggestions.
- `core/local_ai.py`, `core/local_ai_worker.py`: optional on-device inference, source citation validation and per-review process cleanup.
- `core/gemini_report.py`: one-request Gemini generation with transient credentials and safe provider errors.
- `core/litigation_report.py`: status screening, report schema, evidence bundle and citation/fact validation.
- `templates/review.html`, `static/review/`: no-storage search/review interface.
- `tests/test_browser_review.py`, `tests/test_pasted_docket.py`, `tests/review_browser_smoke.py`: offline and browser checks.
- `tests/test_gemini_report.py`: Gemini provider and report evidence checks without real API calls.
