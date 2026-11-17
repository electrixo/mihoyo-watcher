"""米游社福利监测 + 兑换码提醒（P2）。

只读、免登录。一轮做三件事：
    1. 拉公告 / 活动 / 资讯（官方接口）
    2. 拉搜索结果并识别兑换码
    3. 合并成一条日报：有 PUSHPLUS_TOKEN 就推送，否则只打印

接口实测（2026-11-17）：
    GET /painter/wapi/getNewsList?client_type=4&gids=<2|6|8>&page_size=N&type=<1公告|2活动|3资讯>
    GET https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=…&page_size=10

风控要点：
    * 必须带 Accept-Encoding: gzip, deflate 并手动解压，否则会被判 1034
    * 请求之间留间隔；单源失败只降级

用法：
    python watch_mihoyo.py                 # 拉一轮，有新内容就打印/推送
    python watch_mihoyo.py --dry-run       # 只打印，不推送、不写状态
    python watch_mihoyo.py --seed          # 只登记历史，不推送
    python watch_mihoyo.py --codes-only    # 只搜兑换码（前瞻窗口用），不抓资讯
    python watch_mihoyo.py --test-push     # 只发一条测试推送，不抓取、不写状态
    python watch_mihoyo.py --page-size 20
"""

import html as htmllib
import json
import os
import re
import sys
import time
import urllib.request

import mihoyo_codes
from mihoyo_http import TIMEOUT, UA, decode_body, http_json

API_BASE = "https://bbs-api-static.miyoushe.com"
NEWS_URL = API_BASE + "/painter/wapi/getNewsList"

# gid -> (游戏名, 文章路径)。2/6/8 已实测；崩坏3 的 gid 待验证，先不纳入。
GAMES = {
    "2": ("原神", "ys"),
    "6": ("崩坏：星穹铁道", "sr"),
    "8": ("绝区零", "zzz"),
}
TYPES = {1: "公告", 2: "活动", 3: "资讯"}

# 公告 / 资讯按关键词过滤；活动（type=2）全收。
KEYWORDS = ("福利", "签到", "礼包", "兑换码", "前瞻", "领取", "奖励", "活动", "兑换")

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")
PAGE_SIZE = 10
KEEP_DAYS = 90
PAUSE_SECONDS = 0.3

TAG_RE = re.compile(r"<[^>]+>")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def strip_html(raw):
    text = TAG_RE.sub(" ", raw or "")
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(item, gid, typ):
    """把列表项压成扁平结构；缺 post_id 的直接丢弃。"""
    post = item.get("post") or {}
    post_id = str(post.get("post_id") or "")
    if not post_id:
        return None
    name, path = GAMES[gid]
    summary = strip_html(item.get("text_summary") or "") or strip_html(post.get("content") or "")
    return {
        "post_id": post_id,
        "title": str(post.get("subject") or "").strip(),
        "summary": summary[:300],
        "game": name,
        "type": TYPES[typ],
        "type_id": typ,
        "url": f"https://www.miyoushe.com/{path}/article/{post_id}",
        "created_at": int(post.get("created_at") or 0),
    }


def wanted(item):
    """活动全收；公告/资讯要求命中福利关键词，过滤封禁公示之类的噪音。"""
    if item.get("type_id") == 2:
        return True
    blob = f"{item.get('title', '')} {item.get('summary', '')}"
    return any(k in blob for k in KEYWORDS)


def fetch_list(gid, typ, page_size=PAGE_SIZE):
    query = "client_type=4&gids={}&page_size={}&type={}".format(gid, page_size, typ)
    data = http_json(NEWS_URL + "?" + query)
    if data.get("retcode") != 0:
        raise RuntimeError(
            f"getNewsList gid={gid} type={typ} retcode={data.get('retcode')} "
            f"{data.get('message')}"
        )
    items = []
    for raw in (data.get("data") or {}).get("list") or []:
        item = normalize(raw, gid, typ)
        if item:
            items.append(item)
    return items


def collect(page_size=PAGE_SIZE):
    """拉全部游戏 × 全部类型；单体失败只降级，不影响其它。"""
    items, failures, total = {}, 0, 0
    for gid in GAMES:
        for typ in TYPES:
            total += 1
            try:
                for item in fetch_list(gid, typ, page_size):
                    items.setdefault(item["post_id"], item)
            except Exception as e:
                failures += 1
                log(f"[WARN] {GAMES[gid][0]} {TYPES[typ]} 拉取失败：{e}")
            time.sleep(PAUSE_SECONDS)
    return list(items.values()), failures, total


def empty_seen():
    return {"posts": {}, "codes": {}}


def load_seen():
    """返回 (seen, 是否首次)。兼容旧格式（扁平 {post_id: 日期}）。"""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if "posts" in data or "codes" in data:
                posts = dict(data.get("posts") or {})
                raw_codes = dict(data.get("codes") or {})
                codes = {
                    code: (meta if isinstance(meta, dict) else {"first_seen": str(meta or "")})
                    for code, meta in raw_codes.items()
                }
                return {"posts": posts, "codes": codes}, False
            return {"posts": dict(data), "codes": {}}, False
    except Exception:
        pass
    return empty_seen(), True


def save_seen(seen):
    text = json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, STATE_PATH)
    return True


def prune_seen(seen, keep_days=KEEP_DAYS):
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - keep_days * 86400))
    old_posts = [pid for pid, day in seen["posts"].items()
                 if isinstance(day, str) and day < cutoff]
    for pid in old_posts:
        del seen["posts"][pid]
    old_codes = []
    for code, meta in seen["codes"].items():
        day = meta.get("first_seen") if isinstance(meta, dict) else meta
        if isinstance(day, str) and day < cutoff:
            old_codes.append(code)
    for code in old_codes:
        del seen["codes"][code]
    return len(old_posts) + len(old_codes)


def select_new(items, seen):
    """(本轮新帖, 其中需要推送的)。新帖无论是否命中关键词都记入 seen。"""
    new_items = [i for i in items if i["post_id"] not in seen["posts"]]
    push_items = [i for i in new_items if wanted(i)]
    return new_items, push_items


def select_new_codes(hits, seen):
    return [h for h in hits if h["code"] not in seen["codes"]]


def mark_seen(seen, new_items, new_hits, today):
    for item in new_items:
        seen["posts"][item["post_id"]] = today
    for hit in new_hits:
        seen["codes"][hit["code"]] = {
            "first_seen": today,
            "game": hit.get("game") or "",
            "region": hit.get("region") or "",
            "url": hit.get("url") or "",
            "score": hit.get("score") or 0,
        }


def render(push_items, code_hits, today):
    lines = [f"## 米游社福利日报 {today}", ""]

    if code_hits:
        lines.append(f"### 兑换码（{len(code_hits)}）")
        for hit in code_hits:
            lines.append(
                f"- `{hit['code']}` {hit.get('game') or '游戏未识别'} · "
                f"{hit.get('region') or '区域未标注'}"
            )
            if hit.get("title"):
                lines.append(f"  来源：[{hit['title'][:40]}]({hit.get('url') or ''})")
        lines.append("")
        lines.append("⚠️ 国服兑换码请在游戏内「设置 → 账户 → 兑换码」兑换，注意有效期。")
        lines.append("")

    groups = {}
    for item in push_items:
        groups.setdefault(item["game"], []).append(item)
    for game, rows in groups.items():
        rows.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        lines.append(f"### {game}")
        for item in rows:
            lines.append(f"- [{item['type']}] [{item['title']}]({item['url']})")
            if item["summary"]:
                lines.append(f"  {item['summary'][:120]}")
        lines.append("")

    lines.append("---")
    lines.append("本消息由米游社福利监测自动生成。")
    return "\n".join(lines)


def push(title, body):
    """返回 True 成功 / False 失败 / None 未配置（只写日志）。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if not token:
        log("[WARN] 未配置 PUSHPLUS_TOKEN，跳过推送")
        return None
    payload = json.dumps(
        {"token": token, "title": title, "content": body, "template": "markdown"},
        ensure_ascii=False,
    )
    try:
        req = urllib.request.Request(
            "https://www.pushplus.plus/send", data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.loads(decode_body(resp.read(), resp.headers.get("Content-Encoding")))
        if result.get("code") != 200:
            log(f"[WARN] 推送返回异常：{result}")
            return False
        return True
    except Exception as e:
        log(f"[WARN] 推送失败：{e}")
        return False


def main(argv):
    dry = "--dry-run" in argv
    force_seed = "--seed" in argv
    codes_only = "--codes-only" in argv
    test_push = "--test-push" in argv
    page_size = PAGE_SIZE
    if "--page-size" in argv:
        try:
            page_size = int(argv[argv.index("--page-size") + 1])
        except (IndexError, ValueError):
            log("[WARN] --page-size 需要整数，使用默认值")
    today = time.strftime("%Y-%m-%d")

    if test_push:
        body = (
            f"这是一条测试推送。\n\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"收到说明 PUSHPLUS_TOKEN 与网络都正常。"
        )
        log("发送测试推送……")
        result = push("米游社监控测试消息", body)
        if result is True:
            log("测试推送成功")
            return 0
        if result is None:
            log("未配置 PUSHPLUS_TOKEN，无法测试推送")
        return 1

    if codes_only:
        items, failures, total = [], 0, 0
    else:
        items, failures, total = collect(page_size)
    code_hits, code_failures, code_total = mihoyo_codes.collect_codes()
    failures += code_failures
    total += code_total
    if failures:
        log(f"[WARN] 本轮有 {failures}/{total} 个请求失败")

    seen, first_run = load_seen()
    new_items, push_items = select_new(items, seen)
    new_hits = select_new_codes(code_hits, seen)

    if first_run or force_seed:
        if dry:
            # 首次运行也要能看到“将要推送什么”，否则 dry-run 无从排查
            if push_items or new_hits:
                print(render(push_items, new_hits, today))
            log(f"dry-run 首次运行预览：{len(new_items)} 条内容 / {len(new_hits)} 个兑换码，"
                f"不推送、不写状态")
            return 0
        mark_seen(seen, new_items, new_hits, today)
        prune_seen(seen)
        save_seen(seen)
        log(f"首次/强制登记：记录 {len(new_items)} 条内容 / {len(new_hits)} 个兑换码，不推送")
        return 0

    if not new_items and not new_hits:
        log("没有新的兑换码" if codes_only else "没有新的福利内容或兑换码")
        return 0 if failures < total else 1

    if not push_items and not new_hits:
        # 新帖都不命中关键词：静默登记，不打扰
        mark_seen(seen, new_items, new_hits, today)
        if not dry:
            prune_seen(seen)
            save_seen(seen)
        log(f"新增 {len(new_items)} 条，未命中福利关键词且无兑换码，不推送")
        return 0 if failures < total else 1

    body = render(push_items, new_hits, today)
    if codes_only:
        title = f"米游社新兑换码 {today}（{len(new_hits)}）"
    else:
        title = f"米游社福利日报 {today}（{len(new_hits)} 码 / {len(push_items)} 条福利）"
    print(body)

    if dry:
        return 0

    result = push(title, body)
    if result is False:
        log("推送失败，本轮不记录状态，下轮重试")
        return 1

    mark_seen(seen, new_items, new_hits, today)
    prune_seen(seen)
    save_seen(seen)
    return 0 if failures < total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
