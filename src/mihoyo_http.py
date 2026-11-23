"""米游社公共 HTTP 层：只读、gzip、有限重试。

风控要点（实测）：必须带 Accept-Encoding: gzip, deflate 并手动解压，
否则 urllib 默认 identity 会被返回 1034。
"""

import gzip
import json
import time
import zlib
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.miyoushe.com/",
}

TIMEOUT = 20
MAX_RETRY = 2


def decode_body(raw, encoding):
    """按 Content-Encoding 解压。抽成独立函数便于离线测试。"""
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


def http_json(url, headers=None):
    last = None
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)
    for i in range(MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                text = decode_body(resp.read(), resp.headers.get("Content-Encoding"))
            return json.loads(text)
        except Exception as e:
            last = e
            if i < MAX_RETRY:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} 失败：{type(last).__name__}: {last}")
