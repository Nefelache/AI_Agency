from __future__ import annotations

import logging
import random
import string
import time
from datetime import date, datetime
from typing import Dict, Iterable, Optional

import requests

from app.core.config import get_settings


class BilibiliAPIError(RuntimeError):
    def __init__(self, message: str, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message or ""
    
    def __str__(self) -> str:
        if self.code is not None:
            return f"Bilibili API error: code={self.code}, message={self.message}"
        return self.message


class BilibiliClient:
    """
    Robust wrapper over Bilibili history API, based on BilibiliHistoryFetcher logic.
    Handles cookie generation (buvid3/4), headers, and retries.
    """

    logger = logging.getLogger(__name__)

    def __init__(self, cookie: str, session: Optional[requests.Session] = None) -> None:
        if not cookie:
            raise ValueError("BILIBILI_COOKIE is empty")

        self.sessdata = cookie
        # Clean up SESSDATA if it contains "SESSDATA=" prefix
        if "SESSDATA=" in self.sessdata:
            self.sessdata = self.sessdata.split("SESSDATA=")[1].split(";")[0]
        
        self.logger.info("Bilibili cookie prefix: %r", self.sessdata[:20])
        self.session = session or requests.Session()
        self.base_url = "https://api.bilibili.com"
        
        # Generate browser-like cookies/headers
        self.headers = self._generate_headers()

    def _generate_headers(self) -> Dict[str, str]:
        """Generate robust headers mimicking a real browser"""
        buvid3 = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        buvid4 = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        b_nut = str(int(time.time() * 1000))
        _uuid = f"D{buvid3}-{b_nut}-{buvid4}"

        cookie_str = (
            f"SESSDATA={self.sessdata}; "
            f"buvid3={buvid3}; "
            f"buvid4={buvid4}; "
            f"b_nut={b_nut}; "
            f"bsource=search_google; "
            f"_uuid={_uuid}"
        )

        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36',
        ]

        return {
            'User-Agent': random.choice(user_agents),
            'Referer': 'https://www.bilibili.com',
            'Origin': 'https://www.bilibili.com',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Site': 'same-site',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Cookie': cookie_str
        }

    def _get(self, path: str, params: Dict) -> Dict:
        url = self.base_url + path
        max_retries = 3
        
        for retry in range(max_retries):
            try:
                # Add random delay before request to act like human
                time.sleep(0.5 + random.random() * 0.5)
                
                resp = self.session.get(url, params=params, headers=self.headers, timeout=20)
                
                if resp.status_code == 412:
                    self.logger.warning("Bilibili 412 (Banned), retrying in %ss...", (retry + 1) * 2)
                    time.sleep((retry + 1) * 2)
                    continue
                
                if resp.status_code != 200:
                    body = resp.text[:500] if resp.text else ""
                    self.logger.warning("Bilibili HTTP error %s %s body=%r", resp.status_code, path, body)
                    raise BilibiliAPIError(f"HTTP {resp.status_code}")
                
                data = resp.json()
                data_code = data.get("code")
                data_msg = data.get("message")
                
                if data_code == -101:
                    raise BilibiliAPIError("账号未登录或 Cookie 失效", code=-101)
                
                if data_code != 0:
                    self.logger.warning("Bilibili API %s code=%s message=%r", path, data_code, data_msg)
                    raise BilibiliAPIError(message=data_msg or "", code=data_code)
                
                return data.get("data") or {}

            except requests.exceptions.RequestException as e:
                self.logger.warning("Request error: %s, retrying...", e)
                if retry == max_retries - 1:
                    raise BilibiliAPIError(f"Network error after {max_retries} retries: {e}")
                time.sleep(1)
                
        raise BilibiliAPIError("Failed to fetch data after retries")

    def get_history_page(self, max_: int = 0, view_at: int = 0, business: str = "archive") -> Dict:
        return self._get(
            "/x/web-interface/history/cursor",
            {"max": max_, "view_at": view_at, "business": business, "ps": 30},
        )

    def iter_history_for_day(self, day: date, business: str = "archive") -> Iterable[Dict]:
        """
        Iterate history until the start of the given day.
        """
        max_ = 0
        view_at = 0
        # Target is the END of the previous day (so we stop when we hit data older than `day`)
        # Logic: history is desc order. 
        # If item.dt > day: continue (future/today) - wait, we want history FOR 'day'.
        # Actually, the original logic was: 
        # dt > target: continue (too new? no, target is `day`).
        # If we want specific day's history, we scan until we find it.
        #
        # Let's keep original logic:
        # "stop when view_at date < target day" -> means we passed the target day.
        
        target_date = day
        
        while True:
            data = self.get_history_page(max_, view_at, business)
            items = data.get("list") or []
            
            if not items:
                break

            for item in items:
                ts = item.get("view_at") or (item.get("history") or {}).get("view_at")
                if not ts:
                    continue
                    
                dt = datetime.fromtimestamp(ts).date()
                
                if dt > target_date:
                    # Item is newer than target day (e.g. today is 2024-01-02, target is 2024-01-01)
                    continue
                
                if dt < target_date:
                    # Item is older than target day, we are done
                    return
                
                # dt == target_date
                yield item

            cursor = data.get("cursor") or {}
            next_max = cursor.get("max") or 0
            next_view_at = cursor.get("view_at") or 0
            
            # Stop if cursor resets or loops
            if not next_max or (view_at > 0 and next_view_at >= view_at):
                break
                
            max_, view_at = next_max, next_view_at


def get_bilibili_client() -> BilibiliClient:
    settings = get_settings()
    return BilibiliClient(cookie=settings.bilibili_cookie)
