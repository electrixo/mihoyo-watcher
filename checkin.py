import base64,json,os,random,hashlib,ssl,sys,time
import urllib.request,urllib.error,urllib.parse

APP_VERSION = "2.117.0"
UA = ("Mozilla/5.0 (Linux; Android 12; Unspecified Device) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Version/4.0 Chrome/103.0.5060.129 Mobile Safari/537.36 miHoYoBBS/" + APP_VERSION)
SALT_X6 = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"
MAX_ACCOUNTS = 5

GAMES = {
    "hk4e_cn": ("原神", "e202311201442471", "https://api-takumi.mihoyo.com", "/event/luna"),
    "hkrpg_cn": ("崩坏：星穹铁道", "e202304121516551", "https://api-takumi.mihoyo.com", "/event/luna"),
    "nap_cn": ("绝区零", "e202406242138391", "https://act-nap-api.mihoyo.com", "/event/luna/zzz"),
}

SIGNGAME = {"hk4e_cn": "hk4e", "hkrpg_cn": "hkrpg", "nap_cn": "zzz"}
BINDING_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
CTX = ssl.create_default_context()

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkin_state.json")
TIMEOUT = 15
MAX_RETRY = 2

def mask(s):
    s = str(s or "")
    return (s[0] + "***") if len(s) > 1 else s

def mask_uid(u):
    u = str(u or "")
    return (u[:3] + "****" + u[-2:]) if len(u) > 7 else u


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def norm_cookie(raw):
    """补齐 V1 cookie 别名：luna 的 info/sign 只认 cookie_token / account_id。"""
    pairs = [p.strip() for p in (raw or "").split(";") if "=" in p]
    d, order = {}, []
    for p in pairs:
        k, v = p.split("=", 1)
        k, v = k.strip(), v.strip()
        if k not in d:
            order.append(k)
        d[k] = v
    alias = {
        "cookie_token_v2": "cookie_token", "account_id_v2": "account_id",
        "ltuid_v2": "ltuid", "ltmid_v2": "ltmid", "account_mid_v2": "account_mid",
    }
    added = []
    for v2, v1 in alias.items():
        if v1 not in d and d.get(v2):
            d[v1] = d[v2]
            added.append(v1)
    if "account_id" not in d and d.get("ltuid"):
        d["account_id"] = d["ltuid"]
        added.append("account_id")
    if "ltuid" not in d and d.get("account_id"):
        d["ltuid"] = d["account_id"]
        added.append("ltuid")
    return "; ".join(f"{k}={d[k]}" for k in order + added)


def load_account():
    """从 Secrets 读 Cookie。支持 CHECKIN_COOKIE 原文，或 CHECKIN_COOKIE_B64（纯文本/JSON）。"""
    raw = os.environ.get("CHECKIN_COOKIE", "")
    device = os.environ.get("CHECKIN_DEVICE_ID", "")
    b64 = os.environ.get("CHECKIN_COOKIE_B64", "")
    if not raw and b64:
        try:
            text = base64.b64decode(b64).decode("utf-8")
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                raw = ";".join(f"{k}={v}" for k, v in (obj.get("cookies") or {}).items())
                device = device or str(obj.get("device") or "")
            else:
                raw = text
        except Exception as e:
            log(f"[WARN] CHECKIN_COOKIE_B64 解码失败: {e}")
    if raw and not device:
        device = "8" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:15]
    return norm_cookie(raw), device


def ds2(query="", body=""):
    t = str(int(time.time()))
    r = str(random.randint(100001, 200000))
    src = f"salt={SALT_X6}&t={t}&r={r}&b={body}&q={query}"
    return f"{t},{r},{hashlib.md5(src.encode('utf-8')).hexdigest()}"


def _headers(cookie, dev, query="", body="", signgame=None):
    h = {
        "Cookie": cookie,
        "DS": ds2(query, body),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": "5",
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_id": dev,
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://webstatic.mihoyo.com",
        "Referer": "https://webstatic.mihoyo.com/",
    }
    if signgame:
        h["x-rpc-signgame"] = signgame
    return h


def _once(url, cookie, dev, body=None, query="", signgame=None):
    data = body.encode("utf-8") if isinstance(body, str) else None
    req = urllib.request.Request(
        url, data=data,
        headers=_headers(cookie, dev, query, body or "", signgame),
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def call(url, cookie, dev, body=None, query="", signgame=None):
    """请求 + 仅对瞬时错误重试 MAX_RETRY 次（2s/4s + 抖动）。"""
    last = ""
    for i in range(MAX_RETRY + 1):
        try:
            return _once(url, cookie, dev, body, query, signgame)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:200]
            if e.code < 500:
                return {"retcode": e.code, "message": text}
            last = f"HTTP {e.code} {text}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i < MAX_RETRY:
            time.sleep(2 * (i + 1) + random.uniform(0, 1))
    return {"retcode": -9999, "message": last}


def classify(data):
    rc = data.get("retcode")
    if rc == 0:
        return "ok"
    if rc == -5003:
        return "already"
    if rc == -100:
        return "auth"
    if rc == 1034:
        return "captcha"
    if rc == -1:
        return "protocol"
    if rc == -9999:
        return "transient"
    return "failed"


def fetch_roles(cookie, dev):
    """不带 game_biz 一次取回全部游戏角色。"""
    q = urllib.parse.urlencode({"lang": "zh-cn"})
    data = call(BINDING_URL + "?" + q, cookie, dev, query=q, signgame="hk4e")
    return (data.get("data") or {}).get("list") or [], classify(data)


def query_info(cookie, dev, biz, region, uid):
    name, act_id, host, prefix = GAMES[biz]
    q = urllib.parse.urlencode(
        {"act_id": act_id, "region": region, "uid": uid, "lang": "zh-cn"}
    )
    return call(host + prefix + "/info?" + q, cookie, dev,
                query=q, signgame=SIGNGAME[biz])


def do_sign(cookie, dev, biz, region, uid):
    name, act_id, host, prefix = GAMES[biz]
    body = json.dumps(
        {"act_id": act_id, "region": region, "uid": uid, "lang": "zh-cn"},
        separators=(",", ":"),
    )
    return call(host + prefix + "/sign", cookie, dev, body=body, signgame=SIGNGAME[biz])


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"last_run": "", "last_ok": "", "alerts": {}}


def save_state(state):
    """无变化不写；先写 .tmp 再 replace。"""
    text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True)
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


def _date_ord(s):
    try:
        return int(time.mktime(time.strptime(s, "%Y-%m-%d")))
    except Exception:
        return 0


def should_alert(state, kind, today):
    """1/3/7 阶梯：第 1 天立即，之后第 3、7 天各一次，此后每 7 天一次。"""
    a = (state.get("alerts") or {}).get(kind) or {}
    count = int(a.get("count") or 0)
    if count == 0:
        return True
    if a.get("last") == today:
        return False
    days = (_date_ord(today) - _date_ord(a.get("first") or today)) // 86400
    return days in (2, 6) or (days > 6 and days % 7 == 6)


def record_alert(state, kind, today):
    alerts = state.setdefault("alerts", {})
    a = alerts.setdefault(kind, {"first": today, "last": "", "count": 0})
    if not a.get("first"):
        a["first"] = today
    a["last"] = today
    a["count"] = int(a.get("count") or 0) + 1


def notify(title, content):
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if not token:
        log("[WARN] 未配置 PUSHPLUS_TOKEN，跳过告警推送")
        return False
    payload = json.dumps(
        {"token": token, "title": title, "content": content, "template": "txt"},
        ensure_ascii=False,
    )
    try:
        req = urllib.request.Request(
            "https://www.pushplus.plus/send", data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        if resp.get("code") != 200:
            log(f"[WARN] 推送返回异常: {resp}")
            return False
        return True
    except Exception as e:
        log(f"[WARN] 推送失败: {e}")
        return False


ADVICE = {
    "auth": "Cookie 已失效，请重新登录米游社后更新 CHECKIN_COOKIE（或 CHECKIN_COOKIE_B64）",
    "captcha": "触发人机验证(1034)，已停止本轮；请到米游社 App 完成验证，不要尝试绕过",
    "protocol": "疑似协议参数失效(-1)，请核对 app_version / 盐值 / act_id（最后核对 2026-11-17）",
    "transient": "网络异常，重试后仍失败；请查看 Job 日志中的目标域名",
    "partial": "部分游戏签到失败，已成功的不再重签；请查看失败明细",
    "failed": "签到失败，请按 retcode 排查；若持续出现请重新登录",
}


def main(argv):
    dry = "--dry-run" in argv
    probe = "--probe" in argv
    today = time.strftime("%Y-%m-%d")

    cookie, dev = load_account()
    if not cookie:
        log("未配置 CHECKIN_COOKIE，跳过签到（exit 0）")
        return 0

    roles, st = fetch_roles(cookie, dev)
    if probe:
        log(f"[PROBE] roles 状态={st} 角色数={len(roles)}")
        for r in roles:
            log(f"  {r.get('game_biz')} {mask(r.get('nickname'))} {mask_uid(r.get('game_uid'))}")
        return 0 if st == "ok" else 1

    if st == "ok" and not roles:
        st = "auth"
    if st in ("auth", "captcha", "protocol", "transient", "failed"):
        log(f"[FAIL] 取角色失败：{st}")
        state = load_state()
        state["last_run"] = today
        if should_alert(state, st, today):
            record_alert(state, st, today)
            notify(f"米游社签到异常：{st}", ADVICE.get(st, ""))
        save_state(state)
        return 1

    results = []
    stopped = None
    for role in roles:
        biz = role.get("game_biz") or ""
        if biz not in GAMES:
            log(f"[SKIP] 未配置签到活动：{biz}")
            continue
        region = role.get("region") or ""
        uid = str(role.get("game_uid") or "")
        name = GAMES[biz][0]
        nick = mask(role.get("nickname"))
        if not region or not uid:
            results.append((name, nick, "failed", "缺少 region/uid"))
            continue

        info = query_info(cookie, dev, biz, region, uid)
        ist = classify(info)
        if ist == "ok" and (info.get("data") or {}).get("is_sign"):
            log(f"[OK] {name} {nick} 今日已签")
            results.append((name, nick, "already", "今日已签"))
            continue
        if ist in ("auth", "captcha", "protocol"):
            log(f"[FAIL] {name} {nick} 查询失败 {info.get('retcode')} {info.get('message')}")
            results.append((name, nick, ist, f"查询失败 {info.get('retcode')}"))
            stopped = ist
            break
        if dry:
            log(f"[DRY] {name} {nick} 未签到（dry-run）")
            results.append((name, nick, "ok", "dry-run 未签到"))
            continue

        resp = do_sign(cookie, dev, biz, region, uid)
        rst = classify(resp)
        if rst in ("ok", "already"):
            day = ""
            verify = query_info(cookie, dev, biz, region, uid)
            if classify(verify) == "ok":
                day = (verify.get("data") or {}).get("total_sign_day") or ""
            # 复核失败不翻案：sign 成功即成功
            log(f"[OK] {name} {nick} 签到成功" + (f" 第{day}天" if day else ""))
            results.append((name, nick, rst, f"签到成功 第{day}天" if day else "签到成功"))
            time.sleep(random.uniform(2, 4))
            continue

        log(f"[FAIL] {name} {nick} {resp.get('retcode')} {resp.get('message')}")
        results.append((name, nick, rst, f"{resp.get('retcode')} {resp.get('message')}"))
        if rst in ("auth", "captcha", "protocol"):
            stopped = rst
            break

    goods = [r for r in results if r[2] in ("ok", "already")]
    bads = [r for r in results if r[2] not in ("ok", "already")]
    if stopped:
        status = stopped
    elif not bads:
        status = "ok"
    elif goods:
        status = "partial"
    else:
        status = bads[0][2]

    log(f"本轮结果：{status}；成功/已签 {len(goods)}，失败 {len(bads)}")

    if dry:
        return 0

    state = load_state()
    state["last_run"] = today
    if status == "ok":
        state["last_ok"] = today
        state["alerts"] = {}
    elif should_alert(state, status, today):
        record_alert(state, status, today)
        detail = "\n".join(f"{n} {k}: {d}" for n, k, s, d in results) or "无明细"
        notify(f"米游社签到异常：{status}",
               f"{detail}\n\n时间：{time.strftime('%Y-%m-%d %H:%M')}\n建议：{ADVICE.get(status, '')}")
    save_state(state)

    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
