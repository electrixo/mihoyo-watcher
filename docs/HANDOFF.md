# 交接说明（Agent Handoff）

> 面向接手本项目的 agent / 开发者。仓库公开，本文不含凭证、个人账号或本机路径。
> 有冲突时以仓库里的实际代码为准。

## TL;DR

- 只做米游社的 Python 工具：福利监测 + 兑换码提醒（只读），可选自动签到（需 Cookie）。
- 零第三方依赖，运行在 GitHub Actions；**88 个离线测试全绿**。
- 已验证：真实抓取、pushplus 送达、真实签到、Actions 定时运行。
- 当前设计：**职责拆分**——02:45 只抓码、04:15 完整日报 + 签到、14:10 签到兜底、周三/五/六晚只抓码。
- 签到默认关闭，需仓库 Variables `CHECKIN_ENABLED=true` 才执行。

## 1. 项目边界

| 做 | 不做 |
|---|---|
| 抓米游社公开公告/活动/资讯 | 不抓微博、TapTap |
| 用搜索接口识别兑换码 | 不代替用户兑换 |
| 可选：用用户自己的 Cookie 签到 | 不识别验证码、不伪造指纹、不用代理池 |
| 单账号 | 不做多账号、Web UI、数据库 |

## 2. 仓库结构

```
docs/HANDOFF.md      本文
src/
  watch_mihoyo.py    入口：资讯 + 兑换码日报；--dry-run/--seed/--codes-only/--test-push
  mihoyo_codes.py    搜索采集 + 兑换码识别（字母数字评分、中文短语、书名号/顿号列举）
  mihoyo_comments.py 评论区取码（默认关闭，MIHOYO_COMMENTS=1 才启用）
  mihoyo_http.py     gzip/deflate、15s 超时、2 次重试
  checkin.py         签到：DS2、info 先行、失败分类、1/3/7 告警
tests/               88 个离线用例 + fixtures；tests/__init__.py 负责把 src/ 加入 sys.path
state/
  seen_news.json     posts（post_id→日期）+ codes（code→meta）
  checkin_state.json last_run/last_ok/alerts
.github/workflows/watch.yml
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

默认关键词：兑换码、前瞻直播兑换码、礼包码、兑换中心、版本福利。

### 3.3 兑换码识别

- 字母数字：评分制（形态 +1、语境 +2、前缀 +2、混合 +1、长度 +1，阈值 4）
- 纯字母且无已知前缀直接丢弃（修复 `ENHANCEMENT` 误报）
- 中文/英文短语：只取「国服/国际服：」标注之后的片段；中文 4–12 字、英文 3–32 位
- 另支持书名号、顿号列举、隐藏汇总帖纯字母码

### 3.4 签到

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

| 声明 BJT | UTC cron | 内容 | 推送 |
|---|---|---|---|
| 02:45 | `45 18 * * *` | 只抓码，不抓资讯、不签到 | 新码 |
| 04:15 | `15 20 * * *` | 完整日报 + 签到 | 日报 / 签到失败告警 |
| 14:10 | `10 6 * * *` | 签到兜底 | 失败告警 |
| 周三/五/六 22:07、22:37 | `7,37 14 * * 3,5,6` | 只抓码 | 新码 |

- GitHub cron 是 UTC；实测会延迟数小时（4.7–9.5h）才创建运行，因此 cron 提前触发
- 送达时间会漂移，用户已接受；不要为了对齐时间继续调偏移
- 已知弱点：04:15 是当天唯一资讯抓取；被丢弃则当天无日报，次日补上

## 6. 当前状态

| 项 | 状态 |
|---|---|
| 代码 | 完成 |
| 离线测试 | 88 个通过 |
| 真实抓取 | 通过 |
| pushplus 送达 | 通过 |
| 签到 | 通过（真实运行 1m31s，绿色） |
| Actions 定时 | 通过（有偏移） |
| README | 已更新 |
| LICENSE | 暂未声明（保留所有权利） |

## 7. 待办

1. 许可证暂不处理；如果以后要开源分发，再补 `LICENSE`（MIT）。
2. 可选：给 14:10 增加「当天未出日报则补一次」的状态门（`last_digest_date`），解决 04:15 被丢弃时当天无资讯。
3. watch 单轮约 3 分钟，偏慢；可把 `mihoyo_http.py` 的 `TIMEOUT` 20→10、`MAX_RETRY` 2→1。
4. 可选：评论区取码实测后再决定是否默认开启（当前 `MIHOYO_COMMENTS=1` 才启用）。

## 8. 注意事项

1. cron 是 UTC，且可能延迟/丢弃；不要依赖准点。
2. 整点最拥堵，用 `:07`、`:10`、`:30`、`:37`。
3. 资讯/搜索必须带 gzip 头，否则 1034。
4. 中文码依赖「国服/国际服：」标注。
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

- [ ] 88 个离线测试通过
- [ ] `--dry-run` 能预览且不写状态
- [ ] 配置 token 后能收到 pushplus
- [ ] `src/checkin.py --probe` 无 1034
- [ ] Actions 手动/定时运行绿色，Job Summary 有内容
- [ ] 仓库无凭证/个人/公司信息
- [ ] README 与 LICENSE 完整

## 11. 参考实现

- `chichengzibu/game-checkin`：失败分类、幂等、stoken 续期、Cookie V2→V1 坑
- `qwe123456hh/mihoyo-checkin`：Actions 部署、当前可用常量、多账号 Secrets 命名

两者均无 LICENSE，本项目为独立实现。
