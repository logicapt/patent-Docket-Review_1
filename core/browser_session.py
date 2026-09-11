"""A bounded, disposable browser. Docket content is never written by the application."""
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

    def read(self, url):
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else 0
        if response and "text/html" not in response.headers.get("content-type", "").lower():
            raise BrowserAccessError("unsupported_content", "Only HTML case pages are read. Documents and downloads are disabled.")
        # Bounded rendering wait; no document links, refresh purchases, or account actions are clicked.
        self.page.wait_for_timeout(1200)
        html = self.page.content()
        if len(html.encode("utf-8")) > MAX_HTML:
            raise BrowserAccessError("page_too_large", "This page exceeds the 8 MB processing limit.")
        access_check(html, self.page.url, status)
        return html, self.page.url


@contextmanager
def browser_session(proxy_string=""):
    if not _slots.acquire(blocking=False):
        raise BrowserAccessError("busy", "Two browser requests are already running. Try again when one finishes.")
    try:
        try:
            from playwright.sync_api import sync_playwright, Error, TimeoutError as BrowserTimeout
        except ImportError:
            raise BrowserAccessError("setup_required", "Install dependencies and run: python -m playwright install chromium") from None
        config = ProxyManager(proxy_string).get_playwright_proxy()
        with sync_playwright() as playwright:
            browser = context = None
            try:
                launch = {"headless": True, "timeout": 20000}
                if config:
                    launch["proxy"] = config
                channel = os.environ.get("DOCKET_BROWSER_CHANNEL", "")
                if channel in {"chrome", "msedge"}:
                    launch["channel"] = channel
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(accept_downloads=False, service_workers="block")
                hosts = {"www.google.com", "google.com", "consent.google.com", "www.gstatic.com", "www.googleadservices.com"}
                for source in SOURCES.values():
                    domain = source["domain"].removeprefix("www.")
                    hosts.update({domain, "www." + domain})

                def route_request(route):
                    request = route.request
                    parts = urlsplit(request.url)
                    # Strict host routing also applies to redirects and page subrequests.
                    if (parts.scheme != "https" or parts.hostname not in hosts or parts.port not in (None, 443)
                            or parts.username or parts.password or request.resource_type in {"image", "media", "font"}
                            or re.search(r"\.(?:pdf|zip|xlsx?|csv|docx?)(?:$|/)", parts.path, re.I)):
                        route.abort()
                    else:
                        route.continue_()

                # Routing disables the browser HTTP cache. No HAR, trace, screenshots or storage_state.
                context.route("**/*", route_request)
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.dismiss())
                yield BrowserSession(page)
            except BrowserTimeout:
                raise BrowserAccessError("timeout", "The site did not finish loading within the browser timeout.") from None
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    raise BrowserAccessError("setup_required", "Install the browser: python -m playwright install chromium") from None
                # Do not return Playwright exception text: it can contain URLs and proxy credentials.
                raise BrowserAccessError("browser_error", "Browser navigation failed. Check site access, browser installation, and proxy settings.") from None
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
    finally:
        _slots.release()
