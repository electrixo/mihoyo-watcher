# 交接说明（Agent Handoff）

> 面向接手本项目的 agent / 开发者。仓库公开，本文不含凭证、个人账号或本机路径。
> 有冲突时以仓库里的实际代码为准。

**接手先看**：[`docs/signin-protocol.md`](signin-protocol.md) —— 签到接口、凭证、错误码、变更时间线与差距表
（2026-11-23 核对；签到协议层已知落后，当前未启用）。

## TL;DR

- 只做米游社的 Python 工具：福利监测 + 兑换码提醒（只读），可选自动签到（需 Cookie）。
- 零第三方依赖，运行在 GitHub Actions；**94 个离线测试全绿**。
- 已验证：真实抓取、pushplus 送达、真实签到、Actions 定时运行。
- 当前设计（2026-11-27 起「密打 + 分槽」）：单条 `7,37 * * * *`（48 点/天，实际兑现 4~5 次），
  脚本按北京时间早/午/晚报分槽去重。详见 [`slots-dispatch.md`](slots-dispatch.md)。
- 签到默认关闭，需仓库 Variables `CHECKIN_ENABLED=true` 才执行。
- 已补 `LICENSE`（MIT）；推送去重由状态里的 `slots` 承担（见第 5 节）。

## 1. 项目边界

| 做 | 不做 |
|---|---|
| 抓米游社公开公告/活动/资讯 | 不抓微博、TapTap |
| 用搜索接口识别兑换码 | 不代替用户兑换 |
| 可选：用用户自己的 Cookie 签到 | 不识别验证码、不伪造指纹、不用代理池 |
| 单账号 | 不做多账号、Web UI、数据库 |

## 2. 仓库结构

```
docs/HANDOFF.md        本文
docs/signin-protocol.md 签到协议事实清单（接口 / 错误码 / 变更时间线）
docs/slots-dispatch.md  调度方案：密打 + 分槽（现行）
docs/schedule-drift.md  历史偏移实测
docs/digest-timing.md   已作废的反推方案（存档）
src/
  watch_mihoyo.py    入口：资讯 + 兑换码日报；--dry-run/--seed/--codes-only/--test-push
  mihoyo_codes.py    搜索采集 + 兑换码识别（字母数字评分、中文短语、书名号/顿号列举/隐藏码）
  mihoyo_comments.py 评论区取码（默认关闭，MIHOYO_COMMENTS=1 才启用；接口路径待实测）
  mihoyo_http.py     gzip/deflate、10s 超时、1 次重试
  checkin.py         签到：DS2、info 先行、失败分类、1/3/7 告警
tests/               94 个离线用例 + fixtures；tests/__init__.py 负责把 src/ 加入 sys.path
state/
  seen_news.json     posts + codes + slots（早/午/晚报最后推送日期）
  checkin_state.json last_run/last_ok/alerts
.github/workflows/
  watch.yml           主流程：密打 cron，每轮跑完整流程
LICENSE               MIT
```

## 3. 协议事实（2026-11 实测）

### 3.1 资讯（免登录）

```
GET https://bbs-api-static.miyoushe.com/painter/wapi/getNewsList
    ?client_type=4&gids=<2|6|8>&page_size=10&type=<1公告|2活动|3资讯>
```

字段：`data.list[].post.{post_id, subject, content, created_at}`、`text_summary`。

### 3.2 搜索（免登录）

```
GET https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=…&page_size=10
```

默认关键词（11 个）：兑换码、前瞻直播兑换码、礼包码、兑换中心、版本福利、
前瞻直播内容总结、前瞻总结、省流、直播总结、隐藏兑换码、兑换码汇总。

### 3.3 兑换码识别

- 字母数字：评分制（形态 +1、语境 +2、前缀 +2、混合 +1、长度 +1，阈值 4）
- 纯字母且无已知前缀直接丢弃（修复 `ENHANCEMENT` 误报）
- 中文/英文短语：取「国服/国际服：」标注之后的片段；中文 4–12 字、英文 3–32 位
- 另支持：书名号（`兑换码①：【码】`）、顿号/无冒号列举、隐藏汇总帖纯字母码（靠强语境闸门）
- 注意：无标注的纯文本中文码（如裸列在正文里、无任何引导词）仍抓不到；评论区码因接口未通也抓不到

### 3.4 签到

> ⚠ 2026-11-20 米游社变更了凭证与签到路径：下面的路径 / Cookie 说明只反映**当前代码行为**，
> 最新协议事实、错误码与差距见 [`signin-protocol.md`](signin-protocol.md)。

```
GET  https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie
GET  {host}{path}/info?act_id=&region=&uid=&lang=zh-cn   → data.is_sign
POST {host}{path}/sign  {"act_id","region","uid","lang":"zh-cn"}
```

| game_biz | 游戏 | act_id | signgame | host + path |
|---|---|---|---|---|
| hk4e_cn | 原神 | e202311201442471 | hk4e | api-takumi + /event/luna |
| hkrpg_cn | 星铁 | e202304121516551 | hkrpg | 同上 |
| nap_cn | 绝区零 | e202406242138391 | zzz | act-nap-api + /event/luna/zzz |

```
APP_VERSION = "2.117.0"
SALT        = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"
DS2 = f"{t},{r},{md5(f'salt={SALT}&t={t}&r={r}&b={body}&q={query}')}"
```

错误码：`0` 成功、`-5003` 已签（算成功）、`-100` 登录失效、`1034` 人机验证、`-1` 签名/参数错误。
Cookie：网页只下发 `_v2` 名，luna 只认 V1；`norm_cookie()` 负责补齐。

风控：必须带 `Accept-Encoding: gzip, deflate` 并解压；请求间隔 0.3s；签到前先 `--probe`。

## 4. 运行流程

### 4.1 watch

```
collect_news: 3 游戏 × 3 类型 = 9 请求
collect_codes: 5 个关键词 = 5 请求
  → 过滤（活动全收；公告/资讯按关键词）
  → 去重（posts 按 post_id；codes 按 code）
  → 生成日报 / 新码消息
  → pushplus
  → 推送成功后才写状态
```

- 首次运行只登记，不推送
- 无新内容不推送
- 推送失败不写状态，退出码 1
- 无 token 时只打印，状态照常记录
- `--codes-only`：跳过资讯，只搜码
- `--test-push`：只发测试消息，不抓取、不写状态

### 4.2 checkin

```
roles → 每个角色 info → 已签跳过 → sign → 复核 info 只取天数（不翻案）
```

失败分类：网络重试 2 次；`-100`/`1034`/`-1` 零重试并停止后续游戏；部分失败记 partial。
签到默认关闭：`CHECKIN_ENABLED=true` 才运行；无 Cookie 时 exit 0 跳过。

### 4.3 日志

- `[码] + CODE | 游戏 | 区域 | score=…`，并汇总新码/候选数
- `[码] 本轮未发现新兑换码（候选 N 个）`
- `[推送] 成功/未配置/失败`
- `[ERROR] PUSHPLUS_TOKEN 无效或已过期（code=903）`
- `[ERROR] 资讯抓取全部失败 / 兑换码搜索全部失败`
- 签到：`[告警] 推送成功`、`[ERROR] 告警未送达，本次不推进告警阶梯`

日志只进 Actions 日志和 Job Summary，不进 pushplus；pushplus 只收日报/告警正文。

## 5. 定时

| 项 | 值 |
|---|---|
| cron | `7,37 * * * *`（每 30 分钟一个点，一天 48 点） |
| 实际兑现 | 约 4~5 次/天（GitHub 只派发少数触发点，约 2%） |
| 每轮内容 | 抓资讯 + 抓码 + 签到（签到幂等；未启用则跳过） |
| 推送去重 | 早报 00–12 / 午报 12–18 / 晚报 18–24，每槽每天最多一条日报 |
| 新码 | 不受槽限制，随时推 |

- 实测派发延迟 4h07–11h46，且**派发本身随机**：24 小时内 19 个触发只兑现 1 次
- 所以不再反推触发时刻（前提不成立），改用密度换覆盖；被丢弃的触发点不产生请求
- 某槽当天无兑现时，资讯并入下一槽的日报，不丢数据
- 真要准点只能换外部触发（国内云主机 + `workflow_dispatch`）

## 6. 当前状态

| 项 | 状态 |
|---|---|
| 代码 | 完成 |
| 离线测试 | 94 个通过 |
| 真实抓取 | 通过 |
| pushplus 送达 | 通过 |
| 签到 | 11-20 真实运行通过（1m31s）；**当前未启用**（无 `CHECKIN_COOKIE`），协议层落后 |
| Actions 定时 | 密打 + 分槽去重（2026-11-27 起，观察中） |
| 推送去重 | 已实现（`slots` 早/午/晚三槽）|
| HTTP 调优 | 已做（TIMEOUT 10 / MAX_RETRY 1）|
| README | 已更新 |
| LICENSE | 已补（MIT）|

## 7. 待办

1. [x] 补 `LICENSE`（MIT）——已完成。
2. [x] 推送去重——已完成：密打下按早/午/晚三槽去重，同槽同日只推一条日报（替代原 `last_digest_date` 补抓门）。
3. [x] HTTP 调优——已完成：`mihoyo_http.py` 的 `TIMEOUT` 20→10、`MAX_RETRY` 2→1。
4. [x] 评论区取码——实测结论：加 DS2 签名后候选路径仍全部 404，接口路径/salt 未经真机验证，
   **保持默认关闭**（`MIHOYO_COMMENTS=1` 才启用）。模块代码完整、有降级，待接口确认后可启用。
5. [ ] 签到协议升级（唯一剩余）：原神/星铁改按游戏分段路径、V2 凭证优先、补 `-464`/`1002`/`-500001`
   错误码分类。当前签到未启用，详见 [`signin-protocol.md`](signin-protocol.md) 第 7 节。
6. [x] 覆盖 18:00 发布的资讯（约 24%）：密打后由晚报槽（18–24）覆盖，但依赖当天该时段有兑现。

## 8. 注意事项

1. cron 是 UTC；延迟 4~12 小时、派发率约 2% 是常态。按「能收到」设计，不按准点设计。
2. 整点最拥堵，用 `:07`、`:10`、`:30`、`:37`。
3. 资讯/搜索必须带 gzip 头，否则 1034。
4. 中文码主要靠「国服/国际服：」标注，也支持书名号/顿号/无冒号列举与强语境隐藏码；
   完全无标注的裸中文列举仍抓不到。
5. 搜索按相关度排序、单页 10 条，召回有限。
6. `APP_VERSION`/盐值/`act_id` 会轮换；`-1` 时先核对。
7. Cookie 静态、不自动续期。
8. 两个 job 都会 commit/push，必须保持 `needs: watch` 串行。
9. 公开仓库日志公开；不要打印凭证。
10. 不要使用 `pull_request_target` / `workflow_run` 注入 secrets。
11. 参考实现均无 LICENSE，只参考协议与设计，不复制代码。

## 9. 常用命令

```bash
python -m unittest discover -s tests -t .

python src/watch_mihoyo.py
python src/watch_mihoyo.py --dry-run
python src/watch_mihoyo.py --seed
python src/watch_mihoyo.py --codes-only
python src/watch_mihoyo.py --test-push

python src/checkin.py --probe
python src/checkin.py --dry-run
python src/checkin.py
```

## 10. 验收

- [x] 94 个离线测试通过
- [x] `--dry-run` 能预览且不写状态
- [x] 配置 token 后能收到 pushplus
- [x] `src/checkin.py --probe` 无 1034
- [x] Actions 手动/定时运行绿色，Job Summary 有内容
- [x] 仓库无凭证/个人/公司信息
- [x] README 与 LICENSE 完整

## 11. 参考实现

- `chichengzibu/game-checkin`：失败分类、幂等、stoken 续期、Cookie V2→V1 坑
- `qwe123456hh/mihoyo-checkin`：Actions 部署、当前可用常量、多账号 Secrets 命名
- `Marchen-orz/MiyoQian`：11-21 的 V1→V2 凭证切换、按游戏分段的签到路径、错误码（MIT）

前两者无 LICENSE，MiyoQian 为 MIT；本项目一律只参考协议事实与设计，不复制代码。
