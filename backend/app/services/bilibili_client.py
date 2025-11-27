from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, Iterable, Optional

import requests

from app.core.config import get_settings


class BilibiliAPIError(RuntimeError):
    pass


class BilibiliClient:
    """
    Thin wrapper over Bilibili history API, mirroring the behavior of:

    curl 'https://api.bilibili.com/x/web-interface/history/cursor?max=0&view_at=0&business=archive' \
      -H "Cookie: ${BILIBILI_COOKIE}" \
      -H 'User-Agent: Mozilla/5.0'
    """

    logger = logging.getLogger(__name__)

    def __init__(self, cookie: str, session: Optional[requests.Session] = None) -> None:
        if not cookie:
            raise ValueError("BILIBILI_COOKIE is empty")

        # Log a short prefix so we can confirm env wiring without leaking the full cookie
        self.logger.info(f"Bilibili cookie prefix: {cookie[:40]}...")

        self.cookie = cookie
        self.session = session or requests.Session()
        self.base_url = "https://api.bilibili.com"
        self.base_headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
        }

    def _get(self, path: str, params: Dict) -> Dict:
        url = self.base_url + path
        resp = self.session.get(url, params=params, headers=self.base_headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        code = payload.get("code")
        msg = payload.get("message", "")
        # log for debugging
        self.logger.info("Bilibili API %s code=%s message=%s", path, code, msg)

        if code == -101:
            raise BilibiliAPIError("账号未登录或 Cookie 失效")
        if code != 0:
            raise BilibiliAPIError(f"Bilibili error: {code} {msg}")
        return payload.get("data") or {}

    def get_history_page(self, max_: int = 0, view_at: int = 0, business: str = "archive") -> Dict:
        return self._get(
            "/x/web-interface/history/cursor",
            {"max": max_, "view_at": view_at, "business": business},
        )

    def iter_history_for_day(self, day: date, business: str = "archive") -> Iterable[Dict]:
        # use cursor-based pagination, stop when view_at date < target day
        max_ = 0
        view_at = 0
        target = day
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
                if dt > target:
                    continue
                if dt < target:
                    return
                yield item

            cursor = data.get("cursor") or {}
            next_max = cursor.get("max") or 0
            next_view_at = cursor.get("view_at") or 0
            if not next_max:
                break
            max_, view_at = next_max, next_view_at


def get_bilibili_client() -> BilibiliClient:
    settings = get_settings()
    return BilibiliClient(cookie=settings.bilibili_cookie)
