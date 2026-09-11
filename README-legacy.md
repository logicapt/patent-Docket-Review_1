# Patent Case Docket & Status Tracker

This is the preserved legacy Flask workflow. It can save uploads and call third-party APIs. The default FastAPI workflow is documented in README.md.

A local Flask app that looks for matching patent cases on PacerMonitor, with CourtListener / RECAP as a fallback. Results include a source-linked docket excerpt, its docket date, and a separate case termination date when the source supplies one.

## Start

Install dependencies, then launch:

```powershell
python -m pip install -r requirements.txt
python legacy_app.py
```

Open http://127.0.0.1:5000. Stop the FastAPI server before starting this app on the same port. The default run.bat now starts FastAPI.

## Supply the case identity

Upload an XLSX, XLS, or CSV with:

| Column | Use |
| --- | --- |
| Case Number | Required. Both `1:25-cv-01337` and `1-25-cv-01337` are accepted; the office number is preserved. |
| Plaintiff | Required. Checked against the plaintiff role on the source page. |
| Defendant / Defendants / Defendents | Required. Checked against the defendant role. |
| Jurisdiction / Court | Recommended. If supplied, it must also match. |
| PacerMonitor URL / Docket Link | Optional direct HTTPS `/public/case/...` link. Skips search, but still checks the case identity. |
| Date of Filing | Preserved as input; never used to invent a docket event date. |

Punctuation and common corporate suffixes are normalized. Names are not approximately matched. List multiple parties with semicolons. Abbreviated captions and missing party lists can produce an unverified result; use names displayed by the source. Blank spreadsheet rows are skipped. Rows with a missing case number remain unverified; continuation/contact rows do not inherit another row's case identity.

Keep **Demo Simulation Mode off** for real cases. The bundled sample and demo docket events are fictional and are never source-verified.

## What verification means

- **Source matched** (`SOURCE_VERIFIED`): case number and both party roles match, as does the court if supplied, and at least one dated docket entry was retrieved.
- **Identity only** (`METADATA_ONLY`): case identity matched, but no dated entries were accessible. Available source metadata may still be shown.
- **Unverified**: access failed, identity did not match, required fields were missing, or results were ambiguous. No simulated replacement entries are generated.
- **Demo**: explicitly requested fictional data.
- **Imported - identity matched** (`IMPORTED_MATCHED`): a saved docket matches the selected case number and party roles. The file is user supplied; its authenticity and freshness have not been independently verified online.

“Source matched” describes the retrieved evidence. It is not certification by a court, proof that the docket is complete, or a guarantee of current legal status. The UI shows retrieval time, source update time when available, latest visible entry date, and coverage limitations. Source-specific access errors appear under **Source details**.

A search result or a working link is not treated as docket evidence. The scanner checks candidate case pages instead of choosing the first search hit. If PacerMonitor has no usable entries, the app tries CourtListener; results retain the actual source name and source link. Justia search summaries are not used to establish dates or dispositions.

## Docket dates and snippets

Each excerpt is the text extracted from one docket row (HTML whitespace normalized), kept with its entry number, source URL, and source-provided date. PacerMonitor date headings supply the displayed **docket date**. A signed date, response deadline, entry timestamp, or date mentioned in the narrative is not substituted for that heading. Entries without a usable date are excluded from the verified snippet selection.

The selected disposition excerpt is the most recent supported disposition event, not the first keyword hit. Otherwise, the app can show the latest dated entry as an excerpt while leaving case status unconfirmed. A metadata-only termination does not get an invented supporting quote.

**Termination Date** is copied only from source case metadata. It can legitimately differ from the snippet's docket date. The app does not infer a termination date from a complaint, settlement mention, or another entry.

Keyword labels describe observed events conservatively. A settlement conference is not a completed settlement; a settlement notice does not itself supply a termination date; a proposed dismissal or pending motion is not a dismissal order. Gemini cannot establish source verification or replace docket excerpts, dates, or dispositions. Its optional connection test is retained in Settings; batch results use retrieved evidence.

## Use your PacerMonitor website account when retrieval is blocked

A PacerMonitor website account and programmatic API access are different access routes. PacerMonitor lists its API as a [separate plan](https://www.pacermonitor.com/pricing). The app cannot turn a website subscription into API access or recover entries from an HTTP 403 response.

The **Use a docket from your PacerMonitor account** panel provides a working import route:

1. Load your case spreadsheet in the tracker.
2. Open the matching docket on PacerMonitor in your own browser and sign in there. Load the docket rows you need; the capture contains only content already loaded on that page.
3. Drag **Capture PacerMonitor docket** from the tracker to your bookmarks bar. On the PacerMonitor case page, click that bookmark to download an HTML snapshot. It reads the page DOM, removes scripts and forms, and downloads locally; it does not read cookies, send your password, or make a docket-refresh purchase.
4. Back in the tracker, select the case, choose the HTML file, and click **Import and match docket**. A normally saved HTML page is also accepted; provide its PacerMonitor case URL when available. PDF and MHTML files are not supported by this importer.
5. The snippet and date appear immediately, with the label **Imported - identity matched**. Wrong-case, wrong-party, login, and undated files are rejected. Excel and CSV exports include the imported evidence and its provenance.

Imports are stored as sanitized JSON under `uploads/docket_imports/`. The original HTML, scripts, login forms, and credentials are not served or stored by the importer. **Download imported evidence** saves the parsed case metadata and entries, original file hash, and import timestamp. The hash identifies the submitted file; it is not independent proof of authenticity.

By default, subsequent scans reuse snapshots for attached cases without requesting either website. Uncheck **Reuse imported snapshots during batch scans** to request live sources again. Reimport a newer page to update a snapshot. Import associations remain in the loaded case list during the current app/browser session; the saved evidence files persist on disk.

## Other source access options

1. Open the case in your normal browser and confirm its case number and parties.
2. If you can access PacerMonitor with your account, provide your authorized session cookie in Settings and, preferably, the direct case URL in the spreadsheet. A cookie may still be insufficient for a blocked or restricted page. The app does not solve challenges or bypass account restrictions.
3. Provide a CourtListener API token to enable the RECAP fallback. Access depends on your API permissions and the archive's coverage. See the [CourtListener PACER Data API documentation](https://wiki.free.law/c/courtlistener/help/api/rest/v4/pacer-data).
4. For missing or stale archive data, obtain a current docket through authorized PACER access. CourtListener documents an optional [PACER Fetch API](https://wiki.free.law/c/courtlistener/help/api/rest/v4/recap) that uses PACER credentials and can incur PACER charges. This app does not submit paid fetch requests.
5. Retry later after HTTP 429. HTTP 401/403 requires checking source access; increasing scrape volume does not verify the data.

A live test from this development environment for the uploaded case `1:25-cv-01337` returned **PacerMonitor HTTP 403** and **CourtListener HTTP 401** without credentials. No verified live entries were retrieved. Parser and pipeline tests use synthetic HTML/API fixtures. The account import route needs an actual captured docket to validate that account's page layout; it does not establish live verification.

Do not paste cookies or API tokens into spreadsheets or reports. PacerMonitor cookies are scoped to its domain on outgoing requests.

## Exports and checks

Excel and CSV include verification state, actual source, identity checks, snippet text, entry number, docket date, date basis, separate termination date, source link, retrieval/update times, coverage, access errors, and the PacerMonitor page hash when available. Original spreadsheet columns are preserved.

Run the offline regression and endpoint checks:

```powershell
python -m unittest discover -s tests -v
# Or:
python test_server.py
```

No live credentials, running web server, or paid requests are needed for these tests.

## Main files

- `app.py`: Flask routes and scan input validation.
- `core/pacer_monitor.py`: accessible case-page scraping and date extraction.
- `core/evidence.py`: identity matching and strict date parsing.
- `core/docket_imports.py`: saved-page validation, identity checks, and sanitized evidence storage.
- `static/js/capture-docket.js`: bookmarklet for capturing a page already accessible in the user's browser.
- `core/court_listener.py`: identity-checked, paginated RECAP fallback.
- `core/multi_source_scraper.py`: source selection and diagnostics.
- `core/disposition.py`: conservative event selection.
- `core/batch_processor.py`: live/demo separation and evidence records.
- `core/export_manager.py`: consistent Excel and CSV provenance.
- `tests/test_verification.py`: offline regression tests.
