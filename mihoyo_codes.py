"""米游社搜索 + 兑换码识别（P2）。

搜索接口（旧哨兵已验证）：
    GET https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=…&page_size=10
按相关度排序、单页最多 10 条，拿不到严格时间序；用 fresh_hours 做新鲜度闸门。
"""

import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Tuple

from mihoyo_http import http_json

SEARCH_API = "https://bbs-api.miyoushe.com/post/wapi/searchPosts"
DEFAULT_KEYWORDS = (
    "兑换码", "前瞻直播兑换码", "礼包码", "兑换中心", "版本福利",
    # 前瞻码常被整理进「总结/省流版」帖，正文里码最全，单独搜这一类
    "前瞻直播内容总结", "前瞻总结", "省流", "直播总结",
    # 「隐藏兑换码汇总」这类老版本码合集，码多为纯字母英文短语
    "隐藏兑换码", "兑换码汇总",
)
DEFAULT_EXCLUDE = ("崩坏3", "未定事件簿", "崩坏学园2")
# 搜索结果按相关度排序，前列常是 1-3 周前的帖子；7 天会漏掉大量前瞻码。
# 重复由 code 去重兜底，首轮 seed 也不会推送，所以放宽到 30 天。
FRESH_HOURS = 720
PAGE_SIZE = 10
PAUSE_SECONDS = 0.3

GAME_ID_MAP = {
    1: "崩坏3", 2: "原神", 3: "崩坏学园2", 4: "未定事件簿",
    6: "崩坏：星穹铁道", 8: "绝区零",
}
GAME_PATH = {1: "bh3", 2: "ys", 3: "bh2", 4: "bh3", 6: "sr", 8: "zzz"}

REGION_RULES = [
    ("国服", ("国服", "官服", "陆服", "米游社服", "cn_gf", "cn_qd")),
    ("国际服", ("国际服", "海外服", "亚服", "美服", "欧服", "台港澳", "os_", "asia", "usa", "euro", "cht")),
]

GAME_RULES = [
    ("原神", ("原神", "提瓦特", "旅行者", "原石", "空月之歌", "至冬", "祈愿")),
    ("崩坏：星穹铁道", ("星穹铁道", "星铁", "崩铁", "开拓者", "星琼", "星轨")),
    ("绝区零", ("绝区零", "绳匠", "菲林", "zzz", "零号空洞")),
    ("崩坏3", ("崩坏3", "崩坏三", "舰长", "水晶", "女武神")),
    ("未定事件簿", ("未定事件簿", "律师", "泪之约定")),
]

CONTEXT_KEYWORDS = (
    "兑换码", "兑换", "礼包码", "礼包", "礼品码", "cdkey", "cdk",
    "前瞻", "直播", "福利", "白嫖", "领取", "限时",
)

PREFIX_HINTS = (
    "GENSHIN", "STARRAIL", "ZZZ", "NAP", "HONKAI", "TOTHEMOON",
    "HSR", "GS", "SR", "MYS",
)

STOPWORDS = frozenset({
    "UID", "HTTP", "HTTPS", "WWW", "HTML", "GITHUB", "ANDROID", "IOS",
    "APK", "CPU", "GPU", "RAM", "USB", "PDF", "EXCEL", "WORD", "PPT",
    "VIP", "NPC", "DPS", "APP", "BUG", "PVP", "RPG", "FAQ", "API",
    "URL", "IDEA", "PNG", "JPEG", "MP4", "QPS", "SSD", "HDD", "JSON",
    "CDKEY", "CDKEYS", "LOGIN", "QQGROUP", "WECHAT", "TAPTAP", "BILIBILI",
    "MYSQL", "LINUX", "WINDOWS", "CHROME", "GMAIL", "GOOGLE", "APPLE",
    "STEAM", "EPIC", "PSN", "XBOX", "SONY", "MIHOYO", "HOYOVERSE",
})

CANDIDATE_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-Z]{8,32}(?![0-9A-Za-z])")

# 国服前瞻码是中文短句（实测：坏灭肇始之时 / 星神阿哈祝福你），
# 只出现在「国服兑换码：」「国服：」「国际服：」这类标注之后。
MARKER_RE = re.compile(
    r"(国服兑换码(?:（中文）|\(中文\))?|国际服兑换码|国服|国际服)\s*[:：]\s*"
    r"(.*?)(?=(?:国服兑换码|国际服兑换码|国服|国际服)\s*[:：]|【|=|\n|$)"
)
CN_TOKEN_RE = re.compile(r"^[\u4e00-\u9fff]{4,12}$")
ASCII_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{3,32}$")

# 「兑换码①：【永远不会抛弃你】」这类图文汇总帖的写法：序号 + 书名号包裹码。
# 码本身可以是中文短句或英文串，统一按国服/未标注处理（这类帖几乎都是国服码）。
BRACKET_CODE_RE = re.compile(
    r"兑换码\s*[$①-⑳⒊-⒛\d]?\s*[:：]?\s*【\s*([^】]{2,32}?)\s*】"
)
# 「兑换码：中文码A、中文码B、中文码C」这种顿号/空格列举，无任何括号标注。
# 用「兑换码：」引导 + 句内切片来定位，避免整段正文的普通中文被误判。
# 冒号可省略（帖子里常写「兑换码 永远不会抛弃你 …」）。用「。、，,」及
# 说明性连接词作为边界，避免把「兑换码有效期至…过期不候」整行吞进来。
CN_LIST_RE = re.compile(
    r"(?:兑换码|礼包码)\s*[:：]?\s*"
    r"([^\n。；;]{2,60}?)(?=不方便|请在|关注|更多|有效期|过期|使用|获取|领取|$)"
)
SEQ_MARK_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")
CN_BLOCK = ("兑换码", "兑换", "礼包", "前瞻", "有效期", "截止", "领取", "奖励",
            "角色", "武器", "版本", "直播", "获取", "官方", "设置", "关注",
            "点击", "游戏", "请", "账号", "入口", "方法", "步骤", "详情",
            "制作", "攻略", "支持", "三连", "一键", "如果", "帮助", "有所")

# 纯字母隐藏码（如 ifyouarereadingthis / creationnymph）：无任何形态规律，
# 只能靠「隐藏/汇总/兑换码」强语境作闸门，再排除常见英文单词。
HIDDEN_STRONG_CTX = ("隐藏兑换码", "兑换码汇总", "隐藏兑换")
HIDDEN_TOKEN_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]{5,24}(?![A-Za-z])")
ENGLISH_NOISE = frozenset("""
if you are reading this the a an and or of to in for is are be that welcome game games
player players guide guides new version versions event events reward rewards free get getall
all more about how what when where support like share please follow thanks thank very good
great best top up thisthat has have will can just now out here there them they their our
we you your my with from at by on as it its one two three first last next click copy use
using used make makes made open took take add addcode redeem code codes gift gifts bonus
normal sentence real very really also still only even much many some any such into over
after before because while which who whom whose what why how where when daily weekly event
""".upper().split())


@dataclass(frozen=True)
class CodeHit:
    code: str
    region: Optional[str]
    score: int
    reasons: Tuple[str, ...]


def detect_game(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for game, words in GAME_RULES:
        if any(w.lower() in blob for w in words):
            return game
    return None


def _nearest_region(text, pos, window=200):
    best, best_dist = None, window + 1
    start = max(0, pos - window)
    for region, words in REGION_RULES:
        for word in words:
            idx = text.rfind(word, start, pos)
            if idx >= 0 and pos - idx < best_dist:
                best_dist = pos - idx
                best = region
    return best


def _has_context(text, pos, radius=90):
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    window = text[start:end].lower()
    return any(k in window for k in CONTEXT_KEYWORDS)


def _looks_like_code(token):
    if token in STOPWORDS:
        return False
    if token.isdigit():
        return False
    letters = sum(c.isalpha() for c in token)
    if letters < 3:
        return False
    if len(set(token)) <= 2:
        return False
    return True


def extract(text, min_length=8, max_length=32, min_score=4):
    """评分制识别。宁可漏报也不误报。

    比旧哨兵多一条：纯字母且不以已知前缀开头的候选直接丢弃。
    原因：`礼包内含 Mystic Enhancement Ore` 会因「礼包」+长度加分，
    让 ENHANCEMENT 刚好达到阈值（旧哨兵已知误报）。
    """
    if not text:
        return []

    upper = text.upper()
    hits, seen = [], set()

    for match in CANDIDATE_RE.finditer(upper):
        token = match.group(0)
        if not (min_length <= len(token) <= max_length):
            continue
        if token in seen or not _looks_like_code(token):
            continue
        if token.isalpha() and not token.startswith(PREFIX_HINTS):
            continue

        pos = match.start()
        score, reasons = 1, ["形态符合"]
        if _has_context(text, pos):
            score += 2
            reasons.append("附近有兑换码语境")
        if token.startswith(PREFIX_HINTS):
            score += 2
            reasons.append("命中已知前缀")
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            score += 1
            reasons.append("字母数字混合")
        if 10 <= len(token) <= 16:
            score += 1
            reasons.append("长度典型")

        if score >= min_score:
            seen.add(token)
            hits.append(CodeHit(token, _nearest_region(text, pos), score, reasons))

    hits.sort(key=lambda h: (-h.score, h.code))
    return hits


def extract_marked(text):
    """抽取「国服/国际服：」标注后的中文短语或英文短语兑换码。

    只认标注之后的空白分隔片段，避免整段正文里的普通中文被误判。
    国际服码不要求已知前缀（如 nevergonnagive / aha）。
    """
    hits, seen = [], set()
    for match in MARKER_RE.finditer(text or ""):
        marker, segment = match.group(1), match.group(2)
        region = "国际服" if "国际" in marker else "国服"
        for token in re.split(r"[\s,，、;；/|]+", segment):
            token = token.strip()
            if not token:
                continue
            if CN_TOKEN_RE.match(token):
                if any(block in token for block in CN_BLOCK):
                    continue
                code = token
            elif ASCII_TOKEN_RE.match(token):
                code = token.upper()
                if code in STOPWORDS or code.isdigit():
                    continue
            else:
                continue
            if code in seen:
                continue
            seen.add(code)
            hits.append(CodeHit(code, region, 6, ("标注区域兑换码",)))
    return hits


def extract_bracketed(text):
    """抽取「兑换码①：【码】」书名号里的码（中文短句或英文串）。

    这类汇总帖很常见（尤其省流版），码形态完全无规律，靠标注结构识别最稳。
    """
    hits, seen = [], set()
    for match in BRACKET_CODE_RE.finditer(text or ""):
        token = match.group(1).strip()
        if CN_TOKEN_RE.match(token):
            if any(block in token for block in CN_BLOCK):
                continue
            code = token
        elif ASCII_TOKEN_RE.match(token):
            code = token.upper()
            if code in STOPWORDS or code.isdigit():
                continue
        else:
            continue
        if code in seen:
            continue
        seen.add(code)
        hits.append(CodeHit(code, "国服", 6, ("书名号标注兑换码",)))
    return hits


def extract_cn_list(text):
    """抽取「兑换码：中文码A、中文码B」顿号/空格列举的无标注中文码。

    只认「兑换码：」引导后的同一行片段，再按分隔符切片、逐片过中文形态与停用词，
    兼顾「前瞻直播兑换码：永远不会抛弃你、…」这类无括号写法。
    """
    hits, seen = [], set()
    for match in CN_LIST_RE.finditer(text or ""):
        for token in re.split(r"[\s、，,;；/|]+", match.group(1)):
            token = token.strip()
            if not CN_TOKEN_RE.match(token):
                continue
            if any(block in token for block in CN_BLOCK):
                continue
            if token in seen:
                continue
            seen.add(token)
            hits.append(CodeHit(token, "国服", 5, ("兑换码引导中文列举",)))
    return hits


def extract_hidden_ascii(text):
    """抽取「隐藏兑换码汇总」类帖里的纯字母英文码。

    这类码（ifyouarereadingthis / creationnymph 等）没有形态规律，也没有
    「兑换码：」引导，只能靠强语境词作闸门 + 常见英文单词黑名单来识别，
    避免把普通英文句子误当码。
    """
    if not text or not any(k in text for k in HIDDEN_STRONG_CTX):
        return []
    hits, seen = [], set()
    for match in HIDDEN_TOKEN_RE.finditer(text):
        token = match.group(0).upper()
        if token in ENGLISH_NOISE or token in seen:
            continue
        seen.add(token)
        hits.append(CodeHit(token, None, 5, ("强语境隐藏码",)))
    return hits


def find_codes(text):
    """字母数字启发式 + 标注/书名号/中文列举/隐藏英文码，合并去重。"""
    hits = list(extract(text))
    have = {h.code for h in hits}
    for hit in (extract_marked(text) + extract_bracketed(text)
                + extract_cn_list(text) + extract_hidden_ascii(text)):
        if hit.code not in have:
            have.add(hit.code)
            hits.append(hit)
    return hits


def collect_codes(now=None, keywords=None, page_size=PAGE_SIZE,
                  fresh_hours=FRESH_HOURS, exclude=None):
    """返回 (hits, 失败请求数, 总请求数)。单个关键词失败只降级。"""
    now = time.time() if now is None else now
    cutoff = now - fresh_hours * 3600
    excluded = {str(g).strip() for g in (exclude or DEFAULT_EXCLUDE)}

    hits, failures, total = [], 0, 0
    seen_pid, seen_code = set(), set()

    for keyword in keywords or DEFAULT_KEYWORDS:
        total += 1
        query = urllib.parse.urlencode({"keyword": keyword, "page_size": page_size})
        try:
            data = http_json(SEARCH_API + "?" + query)
        except Exception as e:
            failures += 1
            print(time.strftime("%H:%M:%S"), f"[WARN] 搜索 {keyword!r} 失败：{e}", flush=True)
            time.sleep(PAUSE_SECONDS)
            continue

        if data.get("retcode") != 0:
            failures += 1
            print(time.strftime("%H:%M:%S"),
                  f"[WARN] 搜索 {keyword!r} retcode={data.get('retcode')} {data.get('message')}",
                  flush=True)
            time.sleep(PAUSE_SECONDS)
            continue

        for wrapper in (data.get("data") or {}).get("posts") or []:
            post = wrapper.get("post") or {}
            post_id = str(post.get("post_id") or "")
            if not post_id or post_id in seen_pid:
                continue
            created = int(post.get("created_at") or 0)
            if created and created < cutoff:
                continue
            seen_pid.add(post_id)

            title = str(post.get("subject") or "")
            body = str(post.get("content") or "")
            game = detect_game(title, body) or GAME_ID_MAP.get(post.get("game_id"))
            if game and game in excluded:
                continue

            path = GAME_PATH.get(post.get("game_id"), "ys")
            url = f"https://www.miyoushe.com/{path}/article/{post_id}"
            for hit in find_codes(f"{title}\n{body}"):
                if hit.code in seen_code:
                    continue
                seen_code.add(hit.code)
                hits.append({
                    "code": hit.code, "region": hit.region, "score": hit.score,
                    "game": game, "title": title, "url": url,
                    "source": f"米游社搜索({keyword})",
                })
        time.sleep(PAUSE_SECONDS)

    hits.sort(key=lambda h: (h["game"] or "", h["code"]))
    return hits, failures, total
