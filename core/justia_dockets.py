import re
import urllib.parse
import logging
from typing import Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

class JustiaDocketsClient:
    """
    Public lookup client for Justia Federal Dockets.
    """
    BASE_URL = "https://dockets.justia.com/search"

    def __init__(self, proxy_dict: Optional[Dict[str, str]] = None):
        self.proxies = proxy_dict
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        })
        if self.proxies:
            self.session.proxies.update(self.proxies)

    def search_docket(self, docket_number: str, court_id: str = "", plaintiff: str = "") -> Dict[str, Any]:
        """
        Searches Justia Dockets for a case using docket number and plaintiff.
        """
        query_str = docket_number
        if plaintiff:
            # Add plaintiff last name or first token for targeted match
            clean_plaintiff = re.sub(r"[,\.]", "", plaintiff).split()[0]
            query_str = f"{docket_number} {clean_plaintiff}"

        params = {
            "query": query_str,
            "nos": "830" # Nature of Suit 830 is Patent Litigation in federal courts
        }

        try:
            resp = self.session.get(self.BASE_URL, params=params, timeout=10)
            if resp.status_code != 200:
                return {"found": False, "error": f"Justia returned status {resp.status_code}"}

            html = resp.text
            # Check if cases were found in the HTML
            if "No cases found" in html or "did not match any cases" in html:
                return {"found": False, "error": f"Case not found in Justia for query: {query_str}"}

            # Parse basic case snippet using regex to avoid external parser dependency
            title_match = re.search(r'<span class="case-name">([^<]+)</span>', html)
            case_title = title_match.group(1).strip() if title_match else ""

            link_match = re.search(r'<a class="case-name" href="([^"]+)"', html)
            case_link = link_match.group(1) if link_match else ""
            if case_link and not case_link.startswith("http"):
                case_link = f"https://dockets.justia.com{case_link}"

            # Check status indicators in text
            is_terminated = False
            status_text = "Pending"
            if re.search(r'\b(Status:\s*Terminated|Case\s+Closed|Dismissed|Settled)\b', html, re.IGNORECASE):
                is_terminated = True
                status_text = "Terminated"

            # Check filing date
            filed_match = re.search(r'Filed:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})', html)
            date_filed = filed_match.group(1) if filed_match else ""

            if case_title or case_link:
                return {
                    "found": True,
                    "source": "Justia Dockets",
                    "case_name": case_title,
                    "status_text": status_text,
                    "is_terminated": is_terminated,
                    "date_filed": date_filed,
                    "docket_url": case_link or "https://dockets.justia.com",
                    "docket_entries": []
                }

        except Exception as e:
            logger.debug(f"Justia docket search error for {docket_number}: {e}")

        return {"found": False, "error": "Not found on Justia"}
