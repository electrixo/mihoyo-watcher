# 米游社国服签到 · 协议事实清单

> 本文只记录**接口事实、错误码语义、变更时间线、我方现状**，不含任何参考实现的代码。
> 这是和米哈游的猫鼠游戏：协议随时会变，所以只维护这张表，代码照表实现。
>
> **最后核对：2026-11-23**（下次协议异常时先更新这一行）

## 0. 摘要

- 2026-11-20 起，米游社**灰度下线 V1 凭证与旧签到路径**。旧 `cookie_token` 被签到接口拒绝（`-100`），
  但查角色接口可能照常放行；旧的不带游戏段的 `/event/luna/...` 对部分账号返回 `-500001`。
- 新签到路径**按游戏分段**：`/event/luna/<signgame>/...`，并且**必须带** `x-rpc-signgame` 头。
- 我方 `src/checkin.py` 的协议层停留在 11-20 之前（原神/星铁仍走旧路径、只维护 V1 别名、无凭证刷新）。
- **当前签到未启用**（未配置 `CHECKIN_COOKIE`），所以上述落后尚未在任何运行里暴露。

## 1. 接口

### 1.1 角色列表

| 项 | 值 |
|---|---|
| URL | `https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie` |
| 方法 | GET |
| 参数 | `lang=zh-cn`；可带 `game_biz=<biz>` 过滤，不带则一次返回全部 |
| 返回 | `data.list[]`：`game_biz` / `region` / `game_uid` / `nickname` |

> ⚠ 11-20 之后：这接口在凭证失效时**可能仍然放行**。不能把它当成"凭证有效"的判据。
> 参考实现因此改为"每轮签到前主动刷新一次凭证"。

### 1.2 签到三件套（新，按游戏分段）

| 用途 | 路径 | 方法 |
|---|---|---|
| 奖励列表 | `/event/luna/<signgame>/home?lang=zh-cn` | GET |
| 签到状态 | `/event/luna/<signgame>/info?lang=zh-cn` | GET |
| 签到 | `/event/luna/<signgame>/sign` | POST（JSON） |

- **host**：原神/星铁/崩坏3/未定/崩坏2 → `https://api-takumi.mihoyo.com`；绝区零 → `https://act-nap-api.mihoyo.com`
- **参数**：`act_id`、`region`、`uid`、`lang=zh-cn`
- **必带头**：`x-rpc-signgame: <signgame>`

### 1.3 旧路径（不要在新增代码里使用）

`/event/luna/home|info|sign`（不带游戏段）正在灰度下线。部分账号返回 `-500001`。
我方原神/星铁目前仍用这一条，属于"尚未暴露的旧协议"。

## 2. 游戏标识（两边实现一致，视为稳定值）

| 游戏 | `game_biz` | `signgame` | `act_id` | host |
|---|---|---|---|---|
| 原神 | `hk4e_cn` | `hk4e` | `e202311201442471` | api-takumi |
| 崩坏：星穹铁道 | `hkrpg_cn` | `hkrpg` | `e202304121516551` | api-takumi |
| 绝区零 | `nap_cn` | `zzz` | `e202406242138391` | act-nap-api |
| 崩坏3 | `bh3_cn` | `bh3` | `e202306201626331` | api-takumi |
| 未定事件簿 | `nxx_cn` | `nxx` | `e202202251749321` | api-takumi |
| 崩坏学园2 | `bh2_cn` | `bh2` | `e202203291431091` | api-takumi |

> 我们只启用前三款。`act_id` 在多个独立实现里完全一致，可继续当常量。

## 3. 凭证

### 3.1 V2 字段

浏览器登录后 Cookie 里的 V2 键：

```
account_id_v2 / account_mid_v2 / cookie_token_v2 / ltmid_v2 / ltoken_v2 / ltuid_v2
```

V1 键（`cookie_token` / `account_id` / `ltuid` / `ltmid`）是旧的，11-20 起部分接口不再接受。

> 我方现状：`norm_cookie()` 把 V2 值**补成 V1 别名**，并优先以 V1 键发请求。
> 这在 V1 尚未完全下线时可能还能用，但方向是反的——应改为"只发 V2 字段"。

### 3.2 有效期（来源：参考实现注释，未独立验证）

- `ltoken_v2`：约 365 天
- `cookie_token_v2`：约 48 小时

### 3.3 刷新

| 项 | 值 |
|---|---|
| URL | `https://passport-api.mihoyo.com/account/ma-cn-session/app/exchange` |
| 方法 | POST（JSON） |
| 前置条件 | **v2 stoken（`v2_` 前缀）+ `mid` + `uid`** |
| 返回 | `data.token.token`（分别为 `ltoken_v2`、`cookie_token_v2`） |
| 签名 | `salt=<exchange盐>&t&r&b&q`，`x-rpc-client_type: 2` |

- 旧的 `getLTokenBySToken` / `getCookieAccountInfoBySToken` 已下线。
- **结论：只保存 Cookie 不保存 stoken，就无法自愈。** 凭证过期只能人工重新登录。

## 4. 签名

两种 DS 都被观察到可用，区别在盐和声明：

| 方式 | 计算 | 用途 |
|---|---|---|
| DS2（X6 盐） | `md5("salt=<x6盐>&t=<t>&r=<r>&b=<body>&q=<query>")` | 我方现用，`x-rpc-client_type: 5` |
| 网页 DS | `md5("salt=<web盐>&t=<t>&r=<r>")` | 参考实现的签到请求 |

- X6 盐：`t0qEgfub6cvueAPgR5m9aQWWVciEer7v`
- 网页盐：`G1ktdwFL4IyGkHuuWSmz0wUe9Db9scyK`
- 我方 `x-rpc-app_version = 2.117.0`（参考实现用 `2.106.2`，两者都在用，**版本号不是强校验**）。

## 5. 错误码

| retcode | 语义 | 我方当前分类 | 建议分类 | 处置 |
|---|---|---|---|---|
| `0` | 成功 | ok | ok | 复核 `total_sign_day` |
| `-5003` | 今日已签到 | already | already | 按成功处理 |
| `-100` | 登录失效 / 凭证被拒 | auth | auth | 提示换 Cookie；有 stoken 时可自动刷新 |
| `-464` | 刷新接口在部分环境返回（参考实现记录） | failed | auth | 同上，并提示"刷新接口不可用" |
| `1002` | 与上面同一批失效表现（参考实现记录，未给单独语义） | failed | auth | 同上 |
| `-500001` | 旧签到路径灰度下线 | failed | deprecated | **不要提示重新登录**，应提示"接口路径已变更" |
| `1034` | 风控 / 人机验证 | captcha | captcha | 停手告警，不做绕过 |
| `-1` | 协议参数失效（版本/盐/act_id） | protocol | protocol | 核对版本、盐、act_id |
| `-9999` | 网络异常（客户端本地码） | transient | transient | 重试后仍失败才告警 |

> 我方 `classify()` 缺 `-464` / `1002` / `-500001` 三档，后两者会被归进 `failed`，
> 告警文案还会误导成"请重新登录"。

## 6. 变更时间线

| 日期 | 事件 | 来源 |
|---|---|---|
| 2026-11-17 | 我方核对 `app_version` / 盐值 / `act_id` | 本项目记录 |
| 2026-11-20 | 米游社灰度下线 V1 凭证与旧签到路径；签到/查角色/刷新 token 出现 `-100` / `-464` / `1002` / `-500001` | 公开实现的提交说明（MIT） |
| 2026-11-21 15:18 | 参考实现改为每轮签到前刷新 `cookie_token` | 同上 |
| 2026-11-21 15:33 | 参考实现让商店类接口也走失效刷新 | 同上 |
| 2026-11-21 20:44 | 参考实现全面切 V2 凭证 + 按游戏分段的新签到路径 | 同上 |
| 2026-11-23 | 我方核对：协议层落后；决定暂不启用签到，先沉淀本文档 | 本项目记录 |

## 7. 我方现状与差距

| 能力 | 我方 | 结论 |
|---|---|---|
| 角色列表 | ✅ | 可用，但不能当凭证判据 |
| 签到路径 | ⚠ 原神/星铁旧路径，绝区零已分段 | **需改** |
| `x-rpc-signgame` | ✅ 三款都带 | 保持 |
| 凭证 | ⚠ 主用 V1 键 + 补 V1 别名 | **建议改 V2 优先** |
| 凭证自愈 | ❌ 无 stoken 刷新 | 启用前决定是否引入 |
| `-5003` 已签 | ✅ | 保持 |
| `first_bind` 首次绑定 | ❌ 未跳过 | 建议跳过并提示手动签一次 |
| 奖励名称/数量 | ❌ 只记"第 N 天" | 可选 |
| 多账号 / 黑名单 | ❌ 单账号 | 不做 |
| 验证码 | ✅ 分类后停手，不绕过 | 保持（产品边界） |
| 国际服 / 云游戏 / 米游币 / 商品兑换 | ❌ | 不做（超出范围） |

## 8. 启用前自检清单

1. `python src/checkin.py --probe` —— 只查角色，不签到，确认 Cookie 还能用。
2. `python src/checkin.py --dry-run` —— 走完 info 查询，不提交签到。
3. 确认三个配置就位：`CHECKIN_ENABLED=true`（Variable）、`CHECKIN_COOKIE`、`PUSHPLUS_TOKEN`（Secrets）。
4. 首轮看 Job Summary 的 `[OK]` / `[FAIL]` 明细，再决定是否长期开启。

## 9. 明确不做

- 自动过验证码 / 接打码平台（涉及绕过人机验证）。
- 国际服（HoYoLAB）、云游戏签到、米游币社区任务、商品兑换。
- 多账号与 Web 控制台。

---

**维护规则**：米游社一变，先改第 1/5/6 节，再动代码；代码里所有协议常量都应与本文一致。
