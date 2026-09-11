"""Optional Windows Chrome smoke test using only synthetic, offline data."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from test_verification import CASE, URL, verified_result
from core.batch_processor import BatchProcessor
from core.keyword_engine import KeywordEngine
from unittest.mock import Mock


def main():
    root = Path(__file__).resolve().parents[1]
    browser = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not browser.exists():
        raise SystemExit("Chrome is required for this optional browser test.")
    item = BatchProcessor()._process_case(CASE, 0, Mock(fetch_case_data=Mock(return_value=verified_result())), KeywordEngine(), False)
    item["trigger_entry"] += ' <img src=x onerror="window.injected=true">'
    imported = {**item, "imported": True, "source_verified": False, "verification_status": "IMPORTED_MATCHED",
                "docket_import_id": "a" * 32, "source_used": "PacerMonitor (imported page)",
                "imported_at": "2026-09-08T00:00:00Z", "import_filename": "saved.html", "retrieved_at": ""}
    html = (root / "templates/index.html").read_text(encoding="utf-8")
    html = re.sub(r'<script\b[^>]*src=[^>]*>.*?</script>', '', html, flags=re.S)
    html = re.sub(r'<link\b[^>]*>', '', html)
    capture_js = (root / "static/js/capture-docket.js").read_text(encoding="utf-8")
    payload = json.dumps({"item": item, "case": CASE, "imported": imported, "capture": capture_js}).replace("</", "<\\/")
    harness = """
    const fixture = PAYLOAD;
    window.onerror = message => { document.body.dataset.error = message; };
    window.alert = message => { document.body.dataset.error = message; };
    window.fetch = async url => ({text: async () => fixture.capture, json: async () => url === '/api/load-sample'
        ? {success:true, all_cases:[fixture.case], total:1, columns:[], column_mapping:{}, filename:'fixture.xlsx'}
        : url === '/api/import-docket' ? {success:true, item:fixture.imported, docket_import_id:fixture.imported.docket_import_id,
            dated_entries:4, stats:{processed:1, imported:1, source_verified:0, dismissed:0}}
        : url === '/api/glossary' ? {success:true, glossary:{}} : {success:true, total:1}});
    window.EventSource = class {
        addEventListener(name, callback) {
            if (name === 'case_completed') setTimeout(() => callback({data:JSON.stringify({item:fixture.item,
                stats:{processed:1, settled:0, dismissed:1, active:0, source_verified:1}})}), 20);
            if (name === 'batch_finished') setTimeout(() => callback({data:JSON.stringify({total_results:1})}), 50);
        }
        close() {}
    };
    document.addEventListener('DOMContentLoaded', () => {
        setTimeout(() => document.getElementById('btn-load-sample').click(), 10);
        setTimeout(() => document.getElementById('btn-start-scan').click(), 50);
        setTimeout(() => {
            const table = document.getElementById('results-table-body');
            const text = table.textContent;
            window.livePassed = text.includes('Docket date: 2026-03-24') && text.includes('Entry #37')
                && text.includes('Source matched') && text.includes('Termination date (source): 2026-03-24')
                && !window.injected && !table.querySelector('img')
                && !document.body.dataset.error;
        }, 125);
        setTimeout(() => {
            const files = new DataTransfer();
            files.items.add(new File(['<html>synthetic</html>'], 'saved.html', {type:'text/html'}));
            document.getElementById('docket-html-file').files = files.files;
            document.getElementById('import-case-id').value = '1';
            document.getElementById('docket-import-form').requestSubmit();
        }, 150);
        setTimeout(() => {
            const table = document.getElementById('results-table-body');
            const text = table.textContent;
            const passed = window.livePassed && text.includes('Imported - identity matched')
                && text.includes('Docket date: 2026-03-24') && text.includes('Download imported evidence')
                && document.getElementById('stat-imported').textContent === '1'
                && document.getElementById('stat-gemini-verified').textContent === '0'
                && document.getElementById('capture-docket-bookmark').href.startsWith('javascript:')
                && !document.body.dataset.error;
            document.body.dataset.smoke = passed ? 'passed' : 'failed';
            const form = document.createElement('form');
            form.textContent = 'CAPTURE_FORM_SECRET';
            document.body.appendChild(form);
            const oldCreate = URL.createObjectURL;
            URL.createObjectURL = blob => {
                blob.text().then(content => {
                    document.body.dataset.capture = !content.includes('CAPTURE_FORM_SECRET')
                        && !content.includes('<script') && !content.includes('onclick=')
                        && content.includes('docket-source-url') ? 'passed' : 'failed';
                });
                return oldCreate(blob);
            };
            HTMLAnchorElement.prototype.click = function () {};
            new Function('location', fixture.capture)({protocol:'https:',hostname:'www.pacermonitor.com',
                origin:'https://www.pacermonitor.com',pathname:'/public/case/12345/Alpha_v_Beta'});
        }, 300);
    });
    """.replace("PAYLOAD", payload)
    app_js = (root / "static/js/app.js").read_text(encoding="utf-8")
    html = html.replace("</body>", "<script>" + harness + "</script><script>" + app_js + "</script></body>")
    with tempfile.TemporaryDirectory(prefix="browser-smoke-", dir=root) as directory:
        work = Path(directory).resolve()
        # Verify the exact cleanup target before TemporaryDirectory removes it.
        if not work.is_relative_to(root) or work == root:
            raise RuntimeError("Browser test directory is outside the workspace.")
        page = work / "smoke.html"
        page.write_text(html, encoding="utf-8")
        result = subprocess.run([str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
                                 "--disable-background-networking", "--no-default-browser-check",
                                 "--user-data-dir=" + str(work / "profile"), "--virtual-time-budget=1000",
                                 "--dump-dom", page.as_uri()], capture_output=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        dom = result.stdout.decode("utf-8", errors="replace")
        if 'data-smoke="passed"' not in dom or 'data-capture="passed"' not in dom:
            errors = re.findall(r'data-(?:error|smoke|capture)="[^"]*"', dom)
            raise AssertionError("Browser smoke failed: " + str(errors))
        print("Browser smoke passed: live and imported labels, dates, import form, evidence link, source text escaping, capture sanitization.")


if __name__ == "__main__":
    main()
