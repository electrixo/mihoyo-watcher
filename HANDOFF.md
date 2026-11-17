# 交接说明（Agent Handoff）

> 本文面向接手本项目的 agent / 开发者。仓库是公开的，因此文中不含任何凭证、个人账号或本机路径。
> 如有冲突，以仓库里的实际代码为准。

## TL;DR

- 这是一个**只做米游社**的 Python 工具：福利监测 + 兑换码提醒（只读），外加可选自动签到（需 Cookie）。
- 零第三方依赖，运行在 GitHub Actions 上。
- **代码已完成，45 个离线测试全绿**；但**尚未完成真实推送、真实签到、Actions 运行**这三项端到端验证。
- 下一步：按第 8 节的验收清单逐项执行；第 9 节是剩余工作。

---

## 1. 项目边界

| 做 | 不做 |
|---|---|
| 抓米游社公开公告/活动/资讯 | 不抓微博、TapTap（已明确放弃） |
| 用搜索接口识别兑换码 | 不代替用户兑换 |
| 可选：用用户自己的 Cookie 自动签到 | 不识别验证码、不伪造设备指纹、不用代理池 |
| 单账号 | 不做多账号、Web UI、数据库、插件系统 |

只读部分（监测/兑换码）**不需要登录**；签到部分只在配置了 Cookie 时启用。

---

## 2. 仓库结构

```
watch_mihoyo.py      入口：资讯 + 兑换码日报；支持 --dry-run / --seed / --codes-only
mihoyo_codes.py      搜索采集 + 兑换码识别（字母数字评分 + 中文短语）
mihoyo_http.py       公共 HTTP 层：gzip/deflate、15s 超时、2 次重试
checkin.py           米游社签到：DS2、info 先行、失败分类、告警节流
tests/
  __init__.py
  test_watch_mihoyo.py      日报/去重/seed/dry-run/push 失败
  test_mihoyo_codes.py      识别、区域、中文码、新鲜度、失败降级
  fixtures/news_type1.json  公告 fixture
  fixtures/news_type2.json  活动 fixture
  fixtures/news_type3.json  资讯 fixture
.github/workflows/watch.yml 单 workflow，watch + checkin 两个 job
.gitignore
README.md            （已有基础内容，尚缺快速开始/定时表/已知限制）
```

**不要上传/创建**：`tests/test_config.py`、`tests/test_models.py`（属于另一个早期 prototype，会 `import app` 失败）、`app/`、`__pycache__/`、`cookie.json`、`*.log`。

运行后生成的、需要提交回仓库的状态文件：

- `seen_news.json`：已见帖子与兑换码
- `checkin_state.json`：签到结果与告警阶梯

---

## 3. 已验证的数据源与协议

以下常量于 **2026-11-17** 在真实接口上验证可用，但会随客户端版本轮换。

### 3.1 资讯列表（免登录）

```
GET https://bbs-api-static.miyoushe.com/painter/wapi/getNewsList
    ?client_type=4&gids=<2|6|8>&page_size=10&type=<1|2|3>
```

- `gid`：2=原神，6=崩坏：星穹铁道，8=绝区零
- `type`：1=公告，2=活动，3=资讯
- 字段：`data.list[].post.{post_id, subject, content, created_at}`、`data.list[].text_summary`
- 实测一轮 9 个请求返回 90 条

### 3.2 搜索（免登录）

```
GET https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=<关键词>&page_size=10
```

默认关键词：`兑换码`、`前瞻直播兑换码`、`礼包码`、`兑换中心`、`版本福利`。

**已知限制**：按相关度排序、单页最多 10 条，可能漏新帖；`fresh_hours` 已放宽到 720 小时（30 天）以覆盖前列的老帖。

### 3.3 兑换码识别（重要）

实测发现国服前瞻码是**中文短句**，例如：

- `坏灭肇始之时`
- `天才少年米提亚登场`
- `永远不会抛弃你`
- `星神阿哈祝福你`

它们只出现在「国服兑换码：」「国服：」「国际服：」这类标注之后。识别规则：

1. 字母数字候选：评分制（形态 +1、语境 +2、前缀 +2、混合 +1、长度 +1，阈值 4）
2. **纯字母且无已知前缀的候选直接丢弃**（修复 `ENHANCEMENT` 误报）
3. 中文/英文短语：只取标注之后的空白分隔片段，中文 4–12 字，英文 3–32 位，过滤「兑换码/有效期/奖励/角色/武器/版本」等表头词

### 3.4 签到协议

```
GET  https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie
     （不带 game_biz → 一次返回全部游戏角色）
GET  {host}{path}/info?act_id=&region=&uid=&lang=zh-cn   → data.is_sign
POST {host}{path}/sign  {"act_id","region","uid","lang":"zh-cn"}
```

| game_biz | 游戏 | act_id | signgame | host + path |
|---|---|---|---|---|
| hk4e_cn | 原神 | e202311201442471 | hk4e | api-takumi.mihoyo.com + /event/luna |
| hkrpg_cn | 崩坏：星穹铁道 | e202304121516551 | hkrpg | 同上 |
| nap_cn | 绝区零 | e202406242138391 | zzz | act-nap-api.mihoyo.com + /event/luna/zzz |

请求头常量：

```
APP_VERSION = "2.117.0"
SALT        = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"
DS2 = f"{t},{r},{md5(f'salt={SALT}&t={t}&r={r}&b={body}&q={query}')}"
headers: Cookie / DS / x-rpc-app_version / x-rpc-client_type=5 /
         x-rpc-channel=miyousheluodi / x-rpc-device_id /
         x-rpc-signgame / User-Agent(miHoYoBBS) /
         Origin+Referer=https://webstatic.mihoyo.com
```

错误码：`0` 成功、`-5003` 今日已签（算成功）、`-100` 登录失效、`1034` 人机验证、`-1` 签名/参数错误。

**Cookie 坑**：网页只下发 `cookie_token_v2 / account_id_v2 / ltuid_v2`，而 `luna` 接口只认 V1 名；`norm_cookie()` 会在请求前补齐别名（幂等）。

### 3.5 风控要点

- 必须带 `Accept-Encoding: gzip, deflate` 并手动解压；urllib 默认 `identity` 会被判 1034。
- 请求之间留 0.3s 间隔；不要并发。
- 签到前先跑只读探针（`checkin.py --probe`），确认当前网络/IP 不会被 1034。

---

## 4. 运行流程

### 4.1 福利 + 兑换码（watch）

```
collect_news: 3 游戏 × 3 类型 = 9 请求
collect_codes: 5 个关键词 = 5 请求
  → 过滤（活动全收；公告/资讯按关键词）
  → 去重（posts 按 post_id；codes 按 code）
  → 渲染日报（兑换码区 + 按游戏分组）
  → pushplus 推送
  → 成功后才写状态
```

- 首次运行（没有 `seen_news.json`）只登记，不推送。
- 没有新内容时不推送。
- 推送失败 **不写状态**，退出码 1，下一轮重试。
- 没有 `PUSHPLUS_TOKEN` 时只打印日志，状态照常记录。
- `--codes-only`：跳过 9 个资讯请求，只搜码、只推码（前瞻窗口用）。

### 4.2 签到（checkin）

```
getUserGameRolesByCookie → 每个角色：
  info.is_sign == true  → 跳过
  否则 POST sign（幂等）
  再查一次 info 只取 total_sign_day，失败不翻案
```

失败分类：

| 类别 | 重试 | 是否继续其它游戏 | 退出码 |
|---|---|---|---|
| 网络错误 | 2 次（2s/4s + 抖动） | 是 | 1（最终失败） |
| -100 登录失效 | 否 | 停全部 | 1 |
| 1034 人机验证 | 否 | 停全部 | 1 |
| -1 签名/参数 | 否 | 停全部 | 1 |
| 部分游戏失败 | 否 | 是 | 1 |
| 全部成功/已签 | — | — | 0 |
| 未配置 CHECKIN_COOKIE | — | 跳过 | 0 |

### 4.3 推送

```
POST https://www.pushplus.plus/send
{"token": "...", "title": "...", "content": "...", "template": "markdown"}
返回 code == 200 才算成功
```

- watch 有新内容才推；签到成功不推，失败才推。
- 签到告警按第 1 / 3 / 7 天阶梯发送。

### 4.4 状态结构

`seen_news.json`：

```json
{
  "posts": {"<post_id>": "YYYY-MM-DD"},
  "codes": {"<code>": {"first_seen": "YYYY-MM-DD", "game": "...", "region": "...", "url": "...", "score": 6}}
}
```

`checkin_state.json`：

```json
{
  "last_run": "YYYY-MM-DD",
  "last_ok": "YYYY-MM-DD",
  "alerts": {"auth": {"first": "YYYY-MM-DD", "last": "YYYY-MM-DD", "count": 2}}
}
```

---

## 5. 定时与 Workflow

`watch.yml` 是**单 workflow、两个 job**，GitHub cron 用 UTC：

| 北京时间 | UTC cron | 运行内容 |
|---|---|---|
| 11:10 | `10 3 * * *` | watch 日报 → checkin 主跑（串行） |
| 14:10 | `10 6 * * *` | checkin 兜底 |
| 周三/五/六 22:07、22:37 | `7,37 14 * * 3,5,6` | watch `--codes-only` |

关键设计：

- 用 `github.event.schedule` 分流：日报 cron 不触发只抓码逻辑，反之亦然。
- `checkin` 使用 `needs: watch` + `if: always()`：watch 被跳过或失败时签到仍运行；两个 job 串行，避免并发 push。
- 两个 job 都有 `timeout-minutes: 10`、0–3 分钟随机延迟（仅 schedule 触发）。
- 全局 `TZ: Asia/Shanghai`，保证脚本里的「今天」是北京时间。
- `set -o pipefail`，`tee` 不吞退出码。
- 状态提交与 Job Summary 都用 `if: always()`。
- `permissions: contents: write` 仅用于提交状态文件；**不要**加 `pull_request_target` 或 `workflow_run`。

额度估算：约 116 个 job/月，公开仓库免费；私有仓库平均约 350 分钟/月（2000 分钟额度内）。

---

## 6. 已完成的工作

1. 新建米游社一体机项目，只保留米游社数据源（放弃微博/TapTap）。
2. 实现福利监测：3 游戏 × 3 类型，关键词过滤，首轮 seed，原子写状态。
3. 实现兑换码提醒：搜索 + 评分识别；发现并支持**中文短语码**；30 天新鲜度窗口。
4. 实现签到：DS2、info 先行、幂等、失败分类、1/3/7 告警阶梯、退出码。
5. 增加 `--codes-only`，用于前瞻窗口只抓码。
6. 实现单 workflow 双 job，按 cron 分流、串行提交、Job Summary、随机延迟。
7. 编写离线测试：45 个用例全绿，不联网。
8. 真实接口验证：资讯 90 条、兑换码 10 个；确认 gzip/1034 行为与中文码格式。
9. README 已有基础内容（功能 / 不做什么 / 安全与 Fork / 免责）。
10. 仓库已清理：没有 `test_config.py` / `test_models.py` / `__pycache__` / 日志 / 凭证。

---

## 7. 当前状态

| 项 | 状态 |
|---|---|
| 代码 | 完成 |
| 离线测试 | 45 个通过 |
| 真实抓取 | 通过（本机） |
| pushplus 真实送达 | **未验证**（没有用真 token 收过微信） |
| 签到真实可用 | **未验证**（没有用真 Cookie 跑过） |
| Actions 运行 | **未验证**（还没手动 Run workflow） |
| README | 基础版，缺少快速开始/定时表/已知限制 |
| LICENSE | 缺失 |

---

## 8. 下一步（按优先级）

### P0：端到端验证（必须先做）

1. 本地配置 `PUSHPLUS_TOKEN`，运行 `python watch_mihoyo.py`，确认微信收到日报。
2. 配置 `CHECKIN_COOKIE`（可选 `CHECKIN_DEVICE_ID`），运行 `python checkin.py --probe`：
   - 能列出角色、retcode=0 → 网络/IP 没问题
   - 返回 1034 → Actions 路线不可行，改本机或云主机
3. GitHub Actions → Run workflow，确认：
   - watch 先跑、checkin 后跑
   - Job Summary 有内容
   - 状态文件被提交
   - 收到推送

### P1：文档与许可

- README 补齐：快速开始、Secrets 表、定时任务表、本地运行、已知限制。
- 添加 `LICENSE`（建议 MIT）。
- 可选：加 `SECURITY.md` 说明不要提交 Cookie。

### P2：观察与调优

- 连续观察 2–3 天的输出，记录：
  - 每天新内容条数（活动类是否噪音过大）
  - 识别出的码是否有效（抽查 1–2 个）
  - 是否出现 1034
- 如活动噪音大：给 `type=2` 也加关键词过滤。
- 如请求量担心：把随机延迟从 0–180s 降到 0–60s。

### P3：合并推送（可选）

当前 watch 与 checkin 是两条独立消息；签到成功不发消息。若希望早上只收一条：

- 日报 job 先跑签到（或读取签到结果）
- 渲染时增加「今日签到」段落
- 签到成功也纳入日报
- 22 点的只抓码轮次不受影响

### P4：长期可选

- 多账号（Secrets `_2.._5`，状态按账号分区）
- stoken 自动续期（需先验证兑换是否影响其它会话）
- 静态看板（history.json + GitHub Pages）

---

## 9. 注意事项与已知坑

1. **cron 是 UTC**，且 GitHub 可能延迟或丢弃定时任务；签到有 06:10/14:10 两轮兜底，且签到幂等。
2. **整点最拥堵**：cron 用 `:07`、`:10`、`:30`、`:37`，不要用 `:00`。
3. **60 天无活动会禁用 schedule**：状态文件每天提交，正好保持活跃。
4. **gzip/1034**：资讯与搜索请求必须带 `Accept-Encoding: gzip, deflate` 并解压。
5. **中文码依赖标注**：没有「国服/国际服：」标记的帖子无法识别中文码。
6. **搜索召回低**：相关度排序 + 单页 10 条，结构性限制；可考虑分页或更多关键词。
7. **常量会轮换**：`APP_VERSION`、`SALT`、`act_id` 失效时，先查是否返回 `-1`，再按第 3 节更新。
8. **Cookie 是静态的**：不自动续期；失效时只能告警，需要用户重新登录更新 Secret。
9. **watch 推送失败不记录状态**；checkin 的告警状态目前无论推送是否成功都会推进（已知小问题，可按需修复）。
10. **两个 job 都会 commit/push**：必须保持 `needs: watch` 串行，否则会 non-fast-forward。
11. **公开仓库日志公开**：不要打印 Cookie；脚本已使用掩码，但新增代码时要注意。
12. **不要使用 `pull_request_target` / `workflow_run`** 注入 secrets；fork 的 PR 不应读取到凭证。
13. **状态文件会每天产生一次提交**（`last_run/last_ok` 变化），这是预期行为。
14. **测试必须离线**：不要在网络不可用时跑端到端；`python -m unittest` 只跑离线用例。
15. **不要复制第三方代码**：参考实现均为公开仓库但**没有 LICENSE**，只可参考协议与设计，不可整段复制。

---

## 10. 常用命令

```bash
# 测试（离线）
python -m unittest tests.test_watch_mihoyo tests.test_mihoyo_codes

# 本地：福利 + 兑换码
python watch_mihoyo.py
python watch_mihoyo.py --dry-run
python watch_mihoyo.py --seed
python watch_mihoyo.py --codes-only

# 本地：签到
python checkin.py --probe
python checkin.py --dry-run
python checkin.py

# 环境变量
PUSHPLUS_TOKEN=...
CHECKIN_COOKIE=...
CHECKIN_DEVICE_ID=...
# 或 CHECKIN_COOKIE_B64（Cookie 串 / {"cookies": {...}, "device": "..."} 的 base64）
```

---

## 11. 验收标准

- [ ] 45 个离线测试通过
- [ ] `watch_mihoyo.py --dry-run` 能拉到资讯与兑换码，且不写状态
- [ ] 配置 token 后能收到 pushplus 消息
- [ ] `checkin.py --probe` 返回角色列表，无 1034
- [ ] Actions 手动运行成功，Job Summary 有内容，状态文件被提交
- [ ] 仓库内没有任何 Cookie/token/个人或公司信息
- [ ] README 与 LICENSE 完整

---

## 12. 参考实现（只参考协议与设计，不复制代码）

- `chichengzibu/game-checkin`：失败分类、幂等、stoken 续期、Cookie V2→V1 坑
- `qwe123456hh/mihoyo-checkin`：GitHub Actions 部署、当前可用常量、多账号 Secrets 命名

两者均**没有 LICENSE**，代码版权归作者；本项目为独立实现。
