import random
import logging
from urllib.parse import urlsplit, unquote
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class ProxyManager:
    """
    Manages proxy configurations (HTTP, HTTPS, SOCKS5) for scraping and API requests.
    Supports single proxy string, list of rotating proxies, and proxy health checks.
    """
    def __init__(self, proxy_input: Optional[str] = None):
        self.proxies: List[str] = []
        self._current_index = 0
        if proxy_input:
            self.set_proxies(proxy_input)

    def set_proxies(self, proxy_input: str):
        """Parse comma or newline-separated proxy strings."""
        if not proxy_input:
            self.proxies = []
            return
        
        raw_list = [p.strip() for p in proxy_input.replace("\r", "\n").split("\n")]
        parsed = []
        for item in raw_list:
            if not item:
                continue
            for sub in item.split(","):
                p = sub.strip()
                if p:
                    if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://")):
                        p = "http://" + p
                    parsed.append(p)
        self.proxies = parsed
        self._current_index = 0
        logger.info(f"Loaded {len(self.proxies)} proxies into ProxyManager.")

    def get_proxy(self) -> Optional[str]:
        """Returns the next proxy in round-robin fashion, or None if no proxies configured."""
        if not self.proxies:
            return None
        proxy = self.proxies[self._current_index % len(self.proxies)]
        self._current_index += 1
        return proxy

    def get_requests_proxies(self) -> Optional[Dict[str, str]]:
        """Returns proxy dict formatted for `requests` or `httpx`."""
        proxy = self.get_proxy()
        if not proxy:
            return None
        return {
            "http": proxy,
            "https": proxy
        }

    def has_proxies(self) -> bool:
        return len(self.proxies) > 0

    def get_playwright_proxy(self):
        """Use one proxy for a browser's lifetime; never rotate after access failures."""
        if len(self.proxies) > 1:
            raise ValueError("Enter one proxy per browser session.")
        proxy = self.get_proxy()
        if not proxy:
            return None
        try:
            parts = urlsplit(proxy)
            if (parts.scheme not in {"http", "https", "socks5"} or not parts.hostname or not parts.port
                    or parts.path not in {"", "/"} or parts.query or parts.fragment):
                raise ValueError()
            if parts.scheme == "socks5" and (parts.username or parts.password):
                raise ValueError("Browser SOCKS5 proxies do not support username/password authentication. Use an HTTP proxy.")
            host = "[" + parts.hostname + "]" if ":" in parts.hostname else parts.hostname
            result = {"server": f"{parts.scheme}://{host}:{parts.port}"}
            if parts.username:
                result.update(username=unquote(parts.username), password=unquote(parts.password or ""))
            return result
        except ValueError as exc:
            if "SOCKS5" in str(exc):
                raise
            raise ValueError("Use http://[user:password@]host:port, https://host:port, or socks5://host:port.") from None

    def test_proxy(self, proxy_url: Optional[str] = None) -> Dict[str, any]:
        """Tests a proxy connection by requesting an IP reflection endpoint."""
        import requests
        target_proxy = proxy_url or (self.proxies[0] if self.proxies else None)
        if not target_proxy:
            return {"success": False, "error": "No proxy provided to test"}
        
        proxies = {"http": target_proxy, "https": target_proxy}
        try:
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=8)
            if resp.status_code == 200:
                ip_info = resp.json().get("origin", "Unknown")
                return {"success": True, "ip": ip_info, "proxy": target_proxy}
            return {"success": False, "error": f"HTTP {resp.status_code}", "proxy": target_proxy}
        except Exception as e:
            return {"success": False, "error": str(e), "proxy": target_proxy}
