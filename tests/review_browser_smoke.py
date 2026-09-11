"""Real Chromium + FastAPI UI smoke test; all remote case/search pages are synthetic."""
from contextlib import contextmanager
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch

import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_browser_review import CASE, URL, fixture
from test_gemini_report import sdk_response, report_json
from core.browser_session import BrowserAccessError
import app


class FixtureBrowser:
    def read(self, url):
        if "google.com/search?" in url:
            return '<a href="' + URL + '"><h3>Alpha v Beta — synthetic test case</h3></a>', url
        return fixture().replace("Administrative notice.", "Administrative notice. &lt;img src=x onerror=alert(1)&gt;"), URL


@contextmanager
def fake_browser(proxy=""):
    yield FixtureBrowser()


def main():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started, "Test server did not start"
        ai_fixture = {"status": "complete", "summary": "Synthetic AI review. <img src=x onerror=alert(1)>",
            "suggested_focus": "invalidation", "differs_from_rules": True, "message": "Synthetic AI interpretation for human review.",
            "observations": [{"entry_number": "8", "date": "2026-09-08", "source_url": URL,
                "quote": "Invalidity contentions due on 10/01/2026.", "interpretation": "Confirm the procedural context before outreach."}],
            "review_questions": ["Which claims need prior-art research?"]}
        with patch("app.browser_session", fake_browser), patch("app.analysis_config", return_value={"available": True, "status": "configured", "message": "Synthetic model configured"}), patch("app.local_ai_review", return_value=ai_fixture) as ai, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1365, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#sources input").nth(4).wait_for()
                assert page.locator("#analysis-mode").input_value() == "gemini"
                page.locator(".settings summary").click()
                page.locator("#analysis-mode").select_option("rules")
                for field, value in (("case-number", CASE["case_number"]), ("plaintiff", CASE["plaintiff"]),
                                     ("defendants", CASE["defendants"]), ("court", CASE["court"])):
                    page.locator("#" + field).fill(value)
                page.get_by_role("button", name="Search Google", exact=True).click()
                page.locator(".candidate button").wait_for()
                assert page.locator(".candidate").count() == 1
                page.locator(".candidate button").click()
                page.locator(".recommendation").wait_for()
                assert page.locator("tbody tr").count() == 7
                assert "both strategy briefs" in page.locator(".recommendation").inner_text()
                assert page.locator("#review-content img").count() == 0, "Unsafe source HTML rendered"
                assert page.locator(".document").count() == 2
                ai.assert_not_called()
                assert page.evaluate("localStorage.length + sessionStorage.length") == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
                if "--screenshot" in sys.argv:
                    destination = Path(__file__).resolve().parent / "review-smoke.png"
                    page.set_viewport_size({"width": 1365, "height": 1100})
                    page.screenshot(path=str(destination), full_page=True)
                    print("Synthetic UI screenshot:", destination)
                page.locator("#analysis-mode").select_option("local_ai")
                page.locator("#case-url").fill(URL)
                page.locator("#review-direct").click()
                page.get_by_role("heading", name="AI-assisted review", exact=True).wait_for()
                assert "AI and rule-based suggestions differ" in page.locator("#review-content").inner_text()
                assert page.locator("#review-content img").count() == 0
                ai.assert_called_once()
                assert ai.call_args.args[2] == 7
                assert page.locator("tbody tr").count() == 7
                page.locator("#analysis-mode").select_option("rules")
                with patch("app.browser_session", side_effect=BrowserAccessError("blocked", "The site refused the automated browser (HTTP 403).", 403)) as blocked:
                    page.locator("#review-direct").click()
                    page.get_by_role("button", name="Paste page content", exact=True).click()
                    assert page.locator("#paste-url").input_value() == URL
                    assert "HTTP 403" in page.locator("#review-status").inner_text()
                    # Synthetic clipboard event: never touches the operating-system clipboard.
                    page.locator("#paste-content").evaluate("""(element, html) => {
                        const data = new DataTransfer();
                        data.setData('text/plain', 'Synthetic copied page text');
                        data.setData('text/html', html);
                        element.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
                    }""", fixture().replace("Administrative notice.", "Administrative notice. &lt;img src=x onerror=alert(1)&gt;"))
                    page.locator("#review-paste").click()
                    page.locator(".recommendation").wait_for()
                    assert "User-supplied" in page.locator("#review-state").inner_text()
                    assert "not been independently verified" in page.locator("#review-status").inner_text()
                    assert "Provided at (UTC)" in page.locator(".metadata").inner_text()
                    assert "Retrieved at (UTC)" not in page.locator(".metadata").inner_text()
                    assert page.locator("tbody tr").count() == 7
                    assert page.locator("#review-content img").count() == 0
                    assert page.locator("#paste-content").input_value() == ""
                    blocked.assert_called_once()
                page.locator("#analysis-mode").select_option("gemini")
                page.locator("#gemini-key").fill("FAKE_BROWSER_TEST_KEY")
                bodies = []
                page.on("request", lambda request: bodies.append(request.post_data or "") if request.url.endswith("/api/discover") else None)
                page.get_by_role("button", name="Search Google", exact=True).click()
                page.locator(".candidate button").wait_for()
                assert all("FAKE_BROWSER_TEST_KEY" not in body for body in bodies), "API key leaked into discovery"
                with patch("core.gemini_report.genai.Client") as factory:
                    output = report_json(transfer=False)
                    output["conclusive_summary"][0]["text"] += " <img src=x onerror=alert(1)>"
                    factory.return_value.__enter__.return_value.models.generate_content.return_value = sdk_response(output)
                    page.locator(".candidate button").click()
                    page.locator(".report-status").wait_for()
                    assert "CASE STATUS & LITIGATION RECOMMENDATION REPORT" in page.locator(".litigation-report").inner_text()
                    assert "NOT_ESTABLISHED" in page.locator(".report-status").inner_text()
                    assert "CONCLUSIVE SUMMARY" in page.locator(".litigation-report").inner_text()
                    assert "RECOMMENDED ACTION PLAN" in page.locator(".litigation-report").inner_text()
                    assert "PARTIES" in page.locator(".litigation-report").inner_text()
                    assert "CASE FACTS" in page.locator(".litigation-report").inner_text()
                    assert "Not established by the supplied docket" in page.locator(".litigation-report").inner_text()
                    assert page.locator("#gemini-key").input_value() == ""
                    assert "FAKE_BROWSER_TEST_KEY" not in page.locator("body").inner_text()
                    assert page.locator(".litigation-report img").count() == 0
                    assert page.locator("tbody tr").count() == 7
                    factory.return_value.__enter__.return_value.models.generate_content.assert_called_once()
                assert page.evaluate("localStorage.length + sessionStorage.length") == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Paste UI overflows on mobile"
                page.locator("#clear").click()
                assert page.locator("#review-section").is_hidden()
                assert page.locator("#review-content").inner_text() == ""
                assert page.locator("#case-number").input_value() == ""
                assert page.locator("#paste-url").input_value() == ""
                assert not errors, errors
                print("PASS: Google -> review -> local AI; blocked case -> paste; Gemini structured report, API key handling, provenance, XSS, no browser storage, mobile layout, clear session.")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
