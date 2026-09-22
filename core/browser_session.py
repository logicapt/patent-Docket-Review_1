"""A bounded, disposable browser. Docket content is never written by the application.

Browser engine: Camoufox (stealth Firefox via Playwright API).
Falls back gracefully to standard Playwright/Chromium when Camoufox is not
installed so that existing setups are not broken.

XHR / fetch JSON interception: every JSON response received while loading a
case page is captured in `BrowserSession.intercepted_json`.  The caller
(browser_sources.py) passes this list to crawl4ai_extractor.find_json_docket_entries()
so that sites which deliver docket data through internal API calls can be read
without any HTML parsing.
"""
from contextlib import contextmanager
import os
import re
import threading
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .browser_sources import SOURCES
from .proxy_manager import ProxyManager


class BrowserAccessError(Exception):
    def __init__(self, status, message, http_status=None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


_slots = threading.BoundedSemaphore(2)
MAX_HTML = 8 * 1024 * 1024


def access_check(html, url, status=200):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    text = soup.get_text(" ", strip=True).lower()
    if status == 429:
        raise BrowserAccessError("rate_limited", "The site rate-limited this request (HTTP 429). Retry later.", status)
    if status == 401 or any(word in title for word in ("sign in", "log in", "login")) or re.search(r"/(?:login|signin|sign-in)(?:/|$)", urlsplit(url).path):
        raise BrowserAccessError("login_required", "An account sign-in is required. The automated browser does not share your normal browser login.", status)
    if status == 403:
        raise BrowserAccessError("blocked", "The site refused access to the automated browser (HTTP 403).", status)
    if any(word in title for word in ("just a moment", "access denied", "captcha", "attention required")) or any(word in text[:500] for word in ("blocked by waf", "unusual traffic from your computer network", "verify you are human")):
        raise BrowserAccessError("blocked", "The automated browser received an access challenge or block page.", status)
    if status >= 400:
        raise BrowserAccessError("unavailable", f"The site returned HTTP {status}.", status)


class BrowserSession:
    def __init__(self, page):
        self.page = page
        self.intercepted_json: list[dict] = []

    def _attach_json_interceptor(self):
        """Attach a response listener that captures XHR/fetch JSON payloads.

        This gives us raw structured data from sites like UniCourt and Ex Parte
        AI Lab that deliver docket entries through internal API calls before
        rendering them into HTML.  We collect every JSON response; the caller
        filters for docket-relevant payloads.
        """
        intercepted = self.intercepted_json

        def _on_response(response):
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" not in ct:
                    return
                # Only capture responses from the allowed source hosts.
                parts = urlsplit(response.url)
                allowed = {s["domain"].removeprefix("www.") for s in SOURCES.values()}
                allowed |= {"www." + d for d in allowed}
                if parts.hostname not in allowed:
                    return
                data = response.json()
                if isinstance(data, (dict, list)):
                    intercepted.append(data if isinstance(data, dict) else {"_list": data})
            except Exception:
                pass  # Never raise inside an event handler.

        self.page.on("response", _on_response)

    def read(self, url):
        self._attach_json_interceptor()
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else 0
        if response and "text/html" not in response.headers.get("content-type", "").lower():
            raise BrowserAccessError("unsupported_content", "Only HTML case pages are read. Documents and downloads are disabled.")
        # Bounded rendering wait; also allows XHR/fetch calls to complete.
        self.page.wait_for_timeout(1200)
        html = self.page.content()
        if len(html.encode("utf-8")) > MAX_HTML:
            raise BrowserAccessError("page_too_large", "This page exceeds the 8 MB processing limit.")
        access_check(html, self.page.url, status)
        return html, self.page.url


def _build_proxy_config(proxy_string: str):
    return ProxyManager(proxy_string).get_playwright_proxy()


def _allowed_hosts():
    hosts = {"www.google.com", "google.com", "consent.google.com", "www.gstatic.com", "www.googleadservices.com"}
    for source in SOURCES.values():
        domain = source["domain"].removeprefix("www.")
        hosts.update({domain, "www." + domain})
    return hosts


def _make_route_handler(hosts):
    def route_request(route):
        request = route.request
        parts = urlsplit(request.url)
        if (parts.scheme != "https" or parts.hostname not in hosts or parts.port not in (None, 443)
                or parts.username or parts.password or request.resource_type in {"image", "media", "font"}
                or re.search(r"\.(?:pdf|zip|xlsx?|csv|docx?)(?:$|/)", parts.path, re.I)):
            route.abort()
        else:
            route.continue_()
    return route_request


@contextmanager
def browser_session(proxy_string=""):
    """Disposable browser context yielding a BrowserSession.

    Tries Camoufox (stealth Firefox) first for better anti-bot bypass.
    Falls back to standard Playwright/Chromium when Camoufox is unavailable
    (e.g. `python -m camoufox fetch` has not been run yet) so existing setups
    continue working without any additional steps.

    The DOCKET_BROWSER_ENGINE environment variable can force a specific engine:
      - ``camoufox``  -- always use Camoufox (error if not installed)
      - ``chromium``  -- always use standard Playwright/Chromium
      - (unset)       -- try Camoufox, fall back to Chromium
    """
    if not _slots.acquire(blocking=False):
        raise BrowserAccessError("busy", "Two browser requests are already running. Try again when one finishes.")
    try:
        engine = os.environ.get("DOCKET_BROWSER_ENGINE", "").lower()
        use_camoufox = engine != "chromium"

        if use_camoufox:
            try:
                yield from _camoufox_session(proxy_string)
                return
            except BrowserAccessError:
                raise  # Real access errors propagate as-is.
            except Exception:
                if engine == "camoufox":
                    raise BrowserAccessError(
                        "setup_required",
                        "Camoufox is not ready. Run: python -m camoufox fetch\n"
                        "Or set DOCKET_BROWSER_ENGINE=chromium to use the standard browser."
                    ) from None
                # Otherwise fall through to Chromium.

        yield from _chromium_session(proxy_string)
    finally:
        _slots.release()


def _camoufox_session(proxy_string: str):
    """Camoufox stealth Firefox session (same Playwright page API)."""
    try:
        from camoufox.sync_api import SyncCamoufox
    except ImportError:
        raise RuntimeError("camoufox not installed") from None

    proxy_config = _build_proxy_config(proxy_string)
    hosts = _allowed_hosts()

    launch_kwargs: dict = {
        "headless": True,
        "os": ("windows",),   # Spoof Windows OS fingerprint.
        "geoip": True,        # Auto-match timezone/locale to proxy IP.
        "humanize": True,     # Realistic mouse movement timing.
    }
    if proxy_config:
        launch_kwargs["proxy"] = proxy_config

    try:
        with SyncCamoufox(**launch_kwargs) as browser:
            context = browser.new_context(accept_downloads=False, service_workers="block")
            context.route("**/*", _make_route_handler(hosts))
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            session = BrowserSession(page)
            yield session
            context.close()
    except BrowserAccessError:
        raise
    except Exception as exc:
        if "Executable" in str(exc) or "camoufox" in str(exc).lower():
            raise RuntimeError("camoufox not ready") from exc
        raise BrowserAccessError(
            "browser_error",
            "Browser navigation failed. Check site access, browser installation, and proxy settings."
        ) from None


def _chromium_session(proxy_string: str):
    """Standard Playwright Chromium session (original behaviour, kept as fallback)."""
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError as BrowserTimeout
    except ImportError:
        raise BrowserAccessError("setup_required", "Install dependencies and run: python -m playwright install chromium") from None

    proxy_config = _build_proxy_config(proxy_string)
    hosts = _allowed_hosts()

    with sync_playwright() as playwright:
        browser = context = None
        try:
            launch: dict = {"headless": True, "timeout": 20000}
            if proxy_config:
                launch["proxy"] = proxy_config
            channel = os.environ.get("DOCKET_BROWSER_CHANNEL", "")
            if channel in {"chrome", "msedge"}:
                launch["channel"] = channel
            browser = playwright.chromium.launch(**launch)
            context = browser.new_context(accept_downloads=False, service_workers="block")
            context.route("**/*", _make_route_handler(hosts))
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            yield BrowserSession(page)
        except BrowserTimeout:
            raise BrowserAccessError("timeout", "The site did not finish loading within the browser timeout.") from None
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                raise BrowserAccessError("setup_required", "Install the browser: python -m playwright install chromium") from None
            raise BrowserAccessError("browser_error", "Browser navigation failed. Check site access, browser installation, and proxy settings.") from None
        finally:
            try:
                if context:
                    context.close()
            finally:
                if browser:
                    browser.close()
