# -*- coding: utf-8 -*-
"""
公式サイト(boatrace.jp)からHTMLを取得する薄いラッパ。
- リトライ
- リクエスト間に必ず間隔を空ける(サイトへの配慮)
- 普通のブラウザに近いUA
"""
import time
import logging
import requests

import config

log = logging.getLogger("fetcher")

BASE = "https://www.boatrace.jp/owpc/pc/race"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

_session = requests.Session()
_session.headers.update(_HEADERS)
_last_request_ts = 0.0


def _throttle():
    global _last_request_ts
    wait = config.REQUEST_DELAY_SEC - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def get(path, **params):
    """例: get("raceindex", jcd=24, hd="20260629") → HTML文字列 or None"""
    url = f"{BASE}/{path}"
    for attempt in range(1, config.REQUEST_RETRY + 1):
        _throttle()
        try:
            r = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT_SEC)
            if r.status_code == 200 and r.text:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            log.warning("GET %s %s -> HTTP %s (try %s)", path, params, r.status_code, attempt)
        except requests.RequestException as e:
            log.warning("GET %s %s -> %s (try %s)", path, params, e, attempt)
        time.sleep(1.5 * attempt)
    log.error("GET failed: %s %s", path, params)
    return None
