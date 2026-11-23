"""米游社评论区取码：补「正文为空、码只在置顶评论」的帖子。

评论区接口需要 DS2 动态签名，且米游社可能随时改协议 / 换 salt。
所有易变项集中在顶部常量，接口失效时优先改这里；任何失败只记 WARN，
不影响正文抓码主流程。

用法：
    import mihoyo_comments
    hits = mihoyo_comments.collect_comment_codes()   # 返回与 collect_codes 同结构的 dict 列表
"""

import hashlib
import random
import time
from urllib.parse import urlencode

import mihoyo_codes
from mihoyo_http import http_json

# ── 易变常量（接口失效时优先改这里）────────────────────────────
API_HOST = "https://bbs-api.miyoushe.com"
# 候选路径按顺序尝试，第一个 retcode=0 的即为可用路径
COMMENT_PATHS = (
    "/post/wapi/getCommentList",
    "/post/api/getCommentList",
    "/apihub/wapi/getCommentList",
)
APP_VERSION = "2.50.1"
DS2_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"  # 4X K2 salt，版本绑定，失效需换
PAUSE_SECONDS = 0.3
SEARCH_SIZE = 10
MAX_POSTS = 30
MAX_CONSECUTIVE_FAIL = 2

DS2_HEADERS = {
    "x-rpc-app_version": APP_VERSION,
    "x-rpc-client_type": "2",  # 安卓端
    "X-Requested-With": "com.mihoyo.hyperion",
    "Referer": "https://app.mihoyo.com",
    "Origin": "https://app.mihoyo.com",
}


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def gen_ds2(query=""):
    """DS2 动态签名（client_type=2 安卓）。返回 "t,r,md5" 三段串。"""
    t = int(time.time())
    r = random.randint(100001, 200000)
    q = "&".join(sorted(query.split("&"))) if query else ""
    main = f"salt={DS2_SALT}&t={t}&r={r}&b=&q={q}"
    return f"{t},{r},{hashlib.md5(main.encode('utf-8')).hexdigest()}"


def search_post_ids(keywords=None, size=SEARCH_SIZE):
    """搜索命中的帖子 id（与正文抓码复用同一批关键词）。"""
    ids, seen = [], set()
    for kw in keywords or mihoyo_codes.DEFAULT_KEYWORDS:
        query = urlencode({"keyword": kw, "size": size})
        try:
            data = http_json(mihoyo_codes.SEARCH_API + "?" + query)
        except Exception as e:
            _log(f"[WARN] 评论源搜索 {kw!r} 失败：{e}")
            continue
        for w in (data.get("data") or {}).get("list") or []:
            pid = str((w.get("post") or {}).get("post_id") or "")
            if pid and pid not in seen:
                seen.add(pid)
                ids.append(pid)
        time.sleep(PAUSE_SECONDS)
    return ids[:MAX_POSTS]


def fetch_comment_list(post_id):
    """拉单个帖子的评论列表；所有候选路径都失败时返回 None。"""
    params = {"post_id": post_id, "size": 20, "mode": 3, "order": 2}
    query = urlencode(params)
    headers = dict(DS2_HEADERS)
    headers["DS"] = gen_ds2(query)
    for path in COMMENT_PATHS:
        try:
            data = http_json(f"{API_HOST}{path}?{query}", headers=headers)
        except Exception as e:
            _log(f"[WARN] 评论接口 {path} 失败：{type(e).__name__}: {e}")
            continue
        if data.get("retcode") == 0:
            return data.get("data") or {}
        _log(f"[WARN] 评论接口 {path} retcode={data.get('retcode')} {data.get('message')}")
    return None


def extract_comment_texts(data):
    """从评论接口 data 里递归抽出所有评论正文字段。"""
    texts = []
    keys = ("content", "content_raw", "text")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and isinstance(v, str):
                    texts.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(data)
    return texts


def collect_comment_codes(keywords=None, post_ids=None):
    """返回评论里识别到的码，结构与 mihoyo_codes.collect_codes 的 hit 一致。

    连续 MAX_CONSECUTIVE_FAIL 个帖子都拉不到评论时判定接口不可用，提前退出，
    避免协议失配时对每个帖子都发 N 个无效请求。
    """
    pids = post_ids if post_ids is not None else search_post_ids(keywords)
    hits, seen = [], set()
    consecutive_fail = 0
    for pid in pids:
        data = fetch_comment_list(pid)
        if not data:
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                _log(f"[WARN] 评论接口连续 {consecutive_fail} 次不可用，本轮跳过评论区取码")
                break
            continue
        consecutive_fail = 0
        for text in extract_comment_texts(data):
            for h in mihoyo_codes.find_codes(text):
                if h.code in seen:
                    continue
                seen.add(h.code)
                hits.append({
                    "code": h.code, "region": h.region, "score": h.score,
                    "game": None, "title": "评论区", "url": "",
                    "source": "米游社评论区",
                })
        time.sleep(PAUSE_SECONDS)
    return hits
