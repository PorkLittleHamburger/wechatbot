# SnowLuma 能力比对与未覆盖功能分析

> **比对对象（SnowLuma）**：仓库 TomyJan/SnowLuma，版本 tag `v2.4.2`，commit `f2ea3354f8bbe8672cbb4ae6a2b4e1fbeff44faf`（2026-11-09 发布）；以该 tag 的源码为唯一上游事实来源，抓取/比对日期 2026-11-15。
>
> **本仓库基线（WechatBot）**：v0.11.10，HEAD `9f689fa`（Windows x64 个人微信 OneBot v11 网关）。
>
> **B 批基线变化（2026-11-15，已提交 `5a3a360`）**：A 批（`1b19502`/`04ab900`）之后的工作区新增 U-07 动作目录/元素四向单一真源、U-06 文件日志与级别热切换、U-05a 暴露面加固、U-04 `bot_offline`、U-08a 好友申请只读列表（新增可选扩展动作 `get_friend_requests`）；注册动作 23→24、测试 557→645、测试文件 43→49。正文的现状判断与优先级仍以 A 批为基线，不据 B 批改写；B 批落地与验证入口见 `docs/HANDOVER.md`，引用行号已按当前工作区核对。
>
> **C 批 U-02（2026-11-15，未提交）**：出站 `video` 发送已按驱动/构建映射（SDK 4.2.0.52 H.264 静音 MP4：Q-3 私聊短样本已验证，其他编码/带音轨 pending；helper 4.2.0.12：网关已映射但私聊/本机待 Q-4，上游仅声明 filehelper/群已验证；其他构建与转发发送扩展链仍 1404）。C 批当时 `pytest --collect-only` 为 669 项 / 50 个测试文件，动作仍 24、WebUI 路由 33；U-02 状态按本批更新，其他条目与 A/B 历史注记不变。
>
> **dev18 批次（2026-11-20，未提交）**：vendored `wechatbot-api` 升级到 dev18（bridge 11 / ABI 2），新增群通知自动显示名并移除手动绑定入口；**当前** 30 条 WebUI 路由、51 个测试文件 / 750 项（`pytest --collect-only`）、注册动作 24。正文结论与优先级不变，计数与受本批影响的 `server.py`、`webui/index.html` 行号按当前工作区刷新；本批未重审其余既有分析行号。
>
> **方法**：只读比对。SnowLuma 侧逐文件阅读镜像源码与文档；本仓库侧逐文件阅读 `src/`、`tests/`、`docs/` 与 `vendor/` 的固定依赖，结论以代码为准、文档为辅。证据格式：本仓库写 `路径:行号`；SnowLuma 镜像写 `SnowLuma v2.4.2: 镜像相对路径:行号`。所有引用在成稿前重新核对到具体行内容（不只校验行号存在）。
>
> **声明**：本文是**分析快照**，不表示任何能力已实现、正在开发或承诺交付；不复制上游源码；不构成对 SnowLuma 的评价。双方均**未做真实 QQ/微信联调**，凡"原生是否真能用"的结论以第七节待确认项为准。SnowLuma 的 9 个显式占位动作与 2 个语义降级动作已单列（见 §3.2），不计为我方缺口。
>
> **术语**：本文"可迁移"指可在本仓库现有原生桥接边界内实现；"受限"指需要原生新增、或仅特定微信构建可用、或需实机确认。

---

## 一、结论摘要（10 条）

1. **差距的主因是原生桥接边界，不是网关代码量。** 事实：本仓库能力 = 原生后端已有能力 ∩ 网关已映射动作（`src/wechat_onebot/driver/helper.py:99-117`、`src/wechat_onebot/driver/base.py:29-135`）；SnowLuma 在 QQ 协议层自行实现 222 个声明动作（`SnowLuma v2.4.2: packages/onebot/src/actions/index.ts:288-313`）。判断：多数"缺动作"要落成"原生得先开门"，而不是协议层补字段。
2. **唯一 P0：出站引用（`reply` 段）当前整链失败。** 事实：任何出站 `reply` 直接 `1404`（`src/wechat_onebot/service.py:882-883`），驱动契约无引用发送方法；原生侧仅微信 **4.2.0.12** 的 helper 后端声明 `text.quote.send`（`vendor/helper/wxbot_framework/backend.py:35-43`、`:132-133`），且 helper 发送不回传 native ID（`src/wechat_onebot/driver/base.py:32-34`），因此只能引用**入站**消息。判断：属"受限可迁移 + 产品决策"（`docs/HANDOVER.md:134` 记录为有意未映射），不是回归或遗漏。
3. **视频发送已由 C 批按驱动/构建映射（SDK Q-3 已验证；helper 4.2.0.12 映射、Q-4 待验；其他构建/转发链 1404）。** 事实：`video` 段不再一律 1404；SDK 4.2.0.52 映射 H.264 静音 MP4（Q-3 私聊短样本实测可播放；其他编码/带音轨 pending），helper 4.2.0.12 映射但私聊/本机待 Q-4（上游仅声明 filehelper/群已验证），其他构建与转发发送扩展链仍 1404（`src/wechat_onebot/service.py:812-815`、`:884-888`、`:909-914`；`src/wechat_onebot/driver/base.py:101-104`；`src/wechat_onebot/driver/sdk.py:45,726-729`；`src/wechat_onebot/driver/helper.py:101,652-662`；`src/wechat_onebot/protocol/element_manifest.py:52-54`）。判断：U-02 从“未映射”转为“按能力门控”；原“框架能力≠网关能力”口径修正注记保留。
4. **官方 OneBot v11 的 38 个标准动作，本仓库注册 18 个、未注册 20 个。** 事实：注册表 24 个动作（`src/wechat_onebot/onebot/actions.py:86-221`）；20 个未注册中 **可迁移 5 个**（撤回、好友申请审批、踢人、退群、改群名[待确认]）、**平台不可迁移 13 个**、**明确排除 2 个**（`set_restart`、`clean_cache`）。
5. **传输层已对齐，缺 6 项协议增强。** 已覆盖：HTTP API、HTTP 上报（含签名/快速操作）、正向/反向 WS、三角色、`echo`、`_async`/`_rate_limited`、心跳与生命周期。缺：SSE 事件流、单端口复用、出站 CQ 字符串模式、自身消息事件上报、离线事件、`Authorization: <token>` 裸 token 形态（均协议层可补，见第四节）。
6. **管理面闭环完整，缺运维增强而非核心功能。** 事实：30 条 WebUI 路由（`@app` 装饰器；`src/wechat_onebot/webui/server.py:179-667`）覆盖登录、状态、调试台、日志、端点、账号/桥接；相对 SnowLuma 缺文件日志与级别热切换、管理密码强度、TOTP、TLS、通知渠道、多账号批量操作。
7. **上游有"虚标"，不能按名字算缺口。** 事实：222 个声明动作中 9 个显式占位（其中 4 个是标准名：`set_group_anonymous`、`set_group_anonymous_ban`、`set_restart`、`clean_cache`）、2 个语义降级（`check_url_safely`、`clean_cache`），实际有效约 211 个（`SnowLuma v2.4.2: packages/mcp/src/generated/catalog.json` 的 summary/notes）。
8. **平台不可迁移集中在 QQ 专有能力。** QQ 空间（29）、群文件（12）、群相册（9）、群精华、闪传、系统表情、匿名/禁言/管理员/群名片/头衔等；另有一批"微信有产品但**无公开接口/原生未提供**"（头像、资料、小程序卡片、输入状态），本文统一写作"无接口"，不写成"微信无对应"。
9. **微信侧原生已有但未映射的能力，不属于 SnowLuma 差距。** 群邀请/建群（4.1.15.x）、主动好友申请（4.2.0.12）、公众号、本机防撤回（4.2.0.12）属产品决策项，不应混入"相对 SnowLuma 缺什么"；表情导出（U-21）与朋友圈（U-20）虽同样来自微信侧，但因其可对齐 OneBot 语义，仍列入第四节并标注"上游无对应/高风险"。
10. **定位差异不应计入缺口。** SDK、MCP、UI 组件包、插件运行器、多平台发行、发布自动化源于 SnowLuma 的"平台"定位；本仓库是"网关 + 管理面"，业务外置。判断：先做协议与管理面补强；SDK/MCP 若做，建议外置进程并复用 WebUI 动作代理（`src/wechat_onebot/webui/server.py:633-643`），不进内核。

---

## 二、架构与定位差异

### 2.1 结构对比

| 维度 | WechatBot（本仓库） | SnowLuma v2.4.2 |
| --- | --- | --- |
| 语言/运行时 | Python 3.11+；便携包内嵌 CPython 3.13.9（`scripts/build_portable.py:34-48`） | TypeScript / Node（`SnowLuma v2.4.2: AGENTS.md:54-58`） |
| 形态 | 单仓库：`src/wechat_onebot/` + `webui/` + vendored helper/sdk | pnpm monorepo 14 包（core/bridge/protocol/onebot/common/sqlite/proto-defs/proton/websocket/runtime/webui/sdk/mcp/ui）（`SnowLuma v2.4.2: AGENTS.md:19-32`） |
| 接入对象 | Windows 微信（helper 注入 / wechatbot-api SDK） | QQ NT 桌面客户端（原生 hook 注入）（`SnowLuma v2.4.2: packages/bridge/src/injector.ts:112-124`） |
| 动作来源 | 原生后端已有能力 ∩ 网关映射（`src/wechat_onebot/driver/helper.py:99-117`） | QQ 协议层自研动作（`SnowLuma v2.4.2: packages/onebot/src/actions/index.ts:288-313`） |
| 对外入口 | OneBot v11（HTTP/上报/双向 WS）+ WebUI + CLI | OneBot v11（HTTP/WS/SSE）+ WebUI + TypeScript SDK + MCP |
| 管理面 | FastAPI + 单文件前端，30 路由 | Vite/React，66 处路由注册 |
| 平台 | Windows x64 便携包 | win-x64 / linux-x64 / linux-arm64（`SnowLuma v2.4.2: .github/workflows/release.yml:42-57`） |
| 发布 | 人工发布 + Launcher 实例复验 | GitHub Actions 三平台矩阵 + 自动发布说明（`SnowLuma v2.4.2: .github/workflows/release.yml:119-192`） |
| 许可 | 本仓库许可 + 第三方声明 | 源码可见非商业许可 + 专有原生组件（`SnowLuma v2.4.2: LICENSE:32-35`） |

### 2.2 差异归因：定位差异 vs 实现差异

**源于定位（不建议为"对齐"而改变）**

1. **不做 SDK/MCP/UI 组件包**：SnowLuma 面向"平台"提供 TypeScript SDK（`SnowLuma v2.4.2: packages/sdk/README.md:3`）、MCP（`SnowLuma v2.4.2: packages/mcp/README.md:3-11`）；本仓库的对外契约就是 OneBot v11，业务由 AstrBot/NoneBot 等客户端承担（`docs/roadmap.md:16`）。把 SDK/MCP 引入内核会扩大攻击面，并与 "vendored helper 禁用插件运行器" 的既有决策冲突（`docs/vendor-helper.md:5`）。
2. **不做 QQ 专有动作**：上游 222 个动作包含 QQ 空间、群文件、闪传等协议层实现；本仓库只暴露"原生桥接已有能力"。拉齐动作数等价于重做原生桥接，不是网关层能补的。
3. **单一平台**：微信桌面端与 helper DLL/wheel 绑定 Windows x64（`vendor/wechatbot_api/VENDORED.json:6`）；上游三平台因其 hook 产物覆盖对应平台。
4. **管理面不含更新器/备份/外观/EULA**：这些由外部 Launcher 的实例模型承担（`docs/HANDOVER.md:7`）；网关重复实现会产生"双生命周期"冲突。
5. **发布自动化/PR 治理**：单人维护 + Launcher 契约，自动化发布放大风险。

**真实实现差异（可补，见第四节）**

1. **已有原生能力未映射**：出站引用（仅 4.2.0.12）、好友申请（列表 + 审批）、群邀请/建群、主动好友申请（4.2.0.12）、表情导出（4.1.15.x）、朋友圈/防撤回——`docs/HANDOVER.md:134` 已记录"未新增 OneBot 映射"。视频发送已由 C 批 U-02 按驱动/构建映射（见 §4.2），不再列为本项。
2. **协议层缺项**：SSE、单端口复用、出站 CQ 字符串、自身消息上报、离线事件、裸 token。
3. **管理面缺项**：文件日志/轮转/级别热切换、密码强度、TOTP、TLS、通知渠道、多账号批量。
4. **工程质量**：动作目录与元素四向清单没有"单一真源 + 防漂移"；CI 无 lint/类型/覆盖率。
5. **可观测性（A 批基线）**：仅 stderr + WebUI 内存 200 条（`src/wechat_onebot/logging_setup.py:1-29`；B 批已实现文件日志/轮转/级别热切换，见 HANDOVER）。

**相对上游的反向优势（对齐时不要丢）**

- HTTP 上报 HMAC-SHA1 签名与快速操作（`src/wechat_onebot/onebot/network/http_client.py:47-65`）；上游 httpClients 只有超时（`SnowLuma v2.4.2: packages/onebot/src/types.ts:72-75`）。
- 引用解析"不猜 ID"策略 + 引用快照（`src/wechat_onebot/service.py:313-359`）。
- 原生组件完整性 CI 强校验（`.github/workflows/bot.yml:26-35`）。
- 语音 SILK 统一转码与 60 秒分段（`src/wechat_onebot/audio.py:78-138`），上游无等价分段逻辑。
- 驱动 × 构建能力交集，不虚报能力（`src/wechat_onebot/driver/helper.py:106-117`）。

### 2.3 一句话口径

上游"声明支持"不等于本仓库"网关支持"：SnowLuma 的动作是其协议层自研，本仓库的动作取决于原生桥接是否开门。后文所有"已覆盖/部分覆盖/未覆盖"均按本仓库代码判定，不按任何一方的文档宣称判定。

---

## 三、逐域差异矩阵

> 判定口径（互斥五选一）：**已覆盖**（语义等价，允许实现路径不同）/ **部分覆盖**（有实现但范围更窄）/ **未覆盖-可迁移**（有可落地路径；"受限"= 需原生新增、仅特定构建可用或需实机确认）/ **未覆盖-平台不可迁移**（QQ 专有，或微信有产品但无公开接口/原生未提供）/ **明确排除**（文档已声明不支持，或与"只做网关"定位冲突）。
> 矩阵按能力域计行；同一能力不跨域重复。第三列"清单"给出第四节对应条目，28 个可迁移条目与矩阵一一对应。
> "微信侧"取值：有 / 无 / 无接口 / 待确认。

### 3.1 协议入口、事件与传输

| # | 能力 | 判定 | 清单 | 微信侧 | 证据（本仓库 / SnowLuma v2.4.2） |
| --- | --- | --- | --- | --- | --- |
| 1 | HTTP API 服务端与 HTTP 事件上报（路径式+信封式、签名、快速操作） | 已覆盖 | — | 有 | `src/wechat_onebot/onebot/network/http_server.py:78-97`；`src/wechat_onebot/onebot/network/http_client.py:47-65` / `SnowLuma v2.4.2: packages/onebot/src/types.ts:62-75`、`SnowLuma v2.4.2: packages/onebot/src/network/http-post-adapter.ts:104-113` |
| 2 | WebSocket：正向三角色 / 反向客户端 / 可配帧上限 | 已覆盖 | — | 有 | `src/wechat_onebot/onebot/network/ws_server.py:74-83`、`src/wechat_onebot/onebot/network/ws_client.py:38-76`、`src/wechat_onebot/onebot/network/config.py:13-24` / `SnowLuma v2.4.2: packages/onebot/src/types.ts:41-60,77-88` |
| 3 | 单端口复用 HTTP + WS（+SSE） | 未覆盖-可迁移 | U-10 | 有 | 同 host:port 被拒绝：`src/wechat_onebot/onebot/network/config.py:219-226` / `SnowLuma v2.4.2: packages/onebot/src/types.ts:47-70` |
| 4 | SSE 事件流（含 Last-Event-ID 重放） | 未覆盖-可迁移 | U-09 | 有 | 本仓库 SSE 仅管理面：`src/wechat_onebot/webui/server.py:494-508` / `SnowLuma v2.4.2: packages/onebot/HTTP_SSE.md:51-84` |
| 5 | 出站消息格式双模（`array` / CQ 字符串） | 部分覆盖 | U-11 | 有 | 入站两模均接受、出站固定段数组：`src/wechat_onebot/protocol/codec.py:60-108` / `SnowLuma v2.4.2: packages/onebot/src/config.ts:841-846` |
| 6 | 自身消息事件上报（`reportSelfMessage`） | 未覆盖-可迁移 | U-03 | 有 | 发送快照只落库不广播：`src/wechat_onebot/service.py:843-859`（`:845` 注释） / `SnowLuma v2.4.2: packages/onebot/src/self-sent-event.ts:3-41`、`SnowLuma v2.4.2: packages/onebot/src/types.ts:26` |
| 7 | 桥接离线事件（`bot_offline`） | 未覆盖-可迁移 | U-04 | 有 | 仅状态快照，无事件：`src/wechat_onebot/core/runtime.py:124-135` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:212-219` |
| 8 | 群成员增减事件（`group_increase/decrease`） | 未覆盖-可迁移（受限） | U-15 | 有 | 转换器需标准 `sub_type`，驱动未提供即抑制：`src/wechat_onebot/protocol/converters.py:153-166` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:29-61` |
| 9 | 好友撤回、好友申请事件 | 已覆盖（仅 helper 驱动） | — | 有 | `src/wechat_onebot/driver/helper.py:350-389`；sdk 不产生（`docs/onebot-compatibility.md:57`） / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:102`、`.../to-request.ts:11` |
| 10 | 群撤回事件 | 部分覆盖 | — | 有 | 路径存在但缺操作者/原消息即抑制：`src/wechat_onebot/protocol/converters.py:149-174`；文档声明：`docs/onebot-compatibility.md:57` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:120-125` |

**小结**：四个 OneBot 传输入口、角色语义、心跳/生命周期与错误映射（`src/wechat_onebot/service.py:416-429`）已与上游对齐，我方在 HTTP 上报侧还多签名与快速操作。缺口是 6 项协议增强（SSE、单端口、CQ 字符串、自身消息、离线事件、裸 token），除自身消息外均为纯协议层工作。
> 其余通知事件（群头衔/群名/输入状态/资料点赞/群精华/消息表情回应）在微信侧无对应或无接口，统一抑制：`src/wechat_onebot/protocol/converters.py:175-176`；上游对应实现见 `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:166,229,244,256,271,291`。

### 3.2 动作面

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 11 | 动作注册表（参数规格/返回 schema/示例/只读/公开目录） | 已覆盖 | — | 有 | `src/wechat_onebot/onebot/actions.py:11-83`、`:220-225` / `SnowLuma v2.4.2: packages/onebot/src/actions/index.ts:288-313` |
| 12 | 动作规模与标准覆盖（24 vs 222；18/38 vs 38/38） | 部分覆盖 | — | 有 | `src/wechat_onebot/onebot/actions.py:86-221` / `SnowLuma v2.4.2: packages/mcp/src/generated/catalog.json`（actions/categories） |
| 13 | 20 个未注册标准动作的分类 | 部分覆盖 | — | 混合 | 下表 3.2.1；本仓库统一失败语义：`src/wechat_onebot/service.py:393-395`、`src/wechat_onebot/onebot/network/http_server.py:93-96` |
| 14 | 撤回 `delete_msg` | 未覆盖-可迁移（受限） | U-14 | 有 | 仅"本机防撤回"、无主动撤回：`vendor/helper/native/local_recall_transport.py:18-34`；文档声明：`docs/roadmap.md:14` / `SnowLuma v2.4.2: packages/onebot/src/actions/message.ts:251` |
| 15 | 好友申请：列表 + 审批 | 未覆盖-可迁移（受限） | U-08 | 有 | helper 已有 `friend_requests()/accept_friend()`（`vendor/helper/wxbot_framework/backend.py:150-157`；`vendor/helper/native/text_service.py:720-745`），网关仅映射入站事件（`src/wechat_onebot/driver/helper.py:350-361`） / `SnowLuma v2.4.2: packages/onebot/src/actions/request.ts:6`、`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3215-3276` |
| 16 | 群管理可迁移子集：踢人/退群/改群名 | 未覆盖-可迁移（受限） | U-16 | 有（群名待确认） | 网关无动作、驱动无方法（`src/wechat_onebot/driver/base.py:29-135`）；文档声明：`docs/roadmap.md:14` / `SnowLuma v2.4.2: packages/onebot/src/actions/group-admin.ts:6,614,638` |
| 17 | 群公告读写 | 未覆盖-可迁移（受限） | U-17 | 有 | 同上 / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1209` |
| 18 | 群管理平台项：禁言/全员禁言/管理员/匿名/群名片/群头衔/加群设置等 | 未覆盖-平台不可迁移 | — | 无（群名片仅 self 昵称） | `docs/onebot-compatibility.md:36` / `SnowLuma v2.4.2: packages/onebot/src/actions/group-admin.ts:67,92,445,589,660,690,707` |
| 19 | QQ 专有动作簇：空间/群文件/群相册/群精华/群资料扩展+标记已读/群待办签到/闪传收藏/系统表情 | 未覆盖-平台不可迁移 | — | 无 / 无接口 | 本仓库无同类动作：`src/wechat_onebot/onebot/actions.py:86-221` / `SnowLuma v2.4.2: packages/onebot/src/actions/qzone.ts:357`、`SnowLuma v2.4.2: packages/onebot/src/actions/group-file.ts:22`、`SnowLuma v2.4.2: packages/onebot/src/actions/group-album.ts:153`、`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:670,3544,3571,2211,4967,5034,3334`、`SnowLuma v2.4.2: packages/onebot/src/actions/system-face.ts:64` |
| 20 | 收藏/商城表情（只读本地缓存） | 部分覆盖 | U-21 | 无接口（4.1.15.x 可只读本地缓存） | helper 有 `emoticon.extract`/`export_emoticons`（仅 4.1.15.x）：`vendor/helper/wxbot_framework/backend.py:23,187-188`、`vendor/helper/wxbot_framework/emoticons.py:483-489`；网关未映射 / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:2402`、`SnowLuma v2.4.2: packages/onebot/src/actions/message.ts:30` |
| 21 | 协议直通/凭据/密钥（`send_packet`、`request_decrypt_key`、rkey/clientkey） | 未覆盖-平台不可迁移 | — | 无 | 无 SSO/凭据概念：`docs/architecture.md:43-50` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4808,3915` |
| 22 | 机器人卡片（Ark/内联按钮） | 未覆盖-平台不可迁移 | — | 无 | 出站拒绝未知/卡片段：`src/wechat_onebot/service.py:884-888` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3057,4725` |
| 23 | 小程序/链接卡片发送 | 明确排除 | — | 无接口 | 文档声明不支持：`docs/onebot-compatibility.md:46`；`src/wechat_onebot/service.py:884-888` |
| 24 | 消息表情回应（reaction） | 未覆盖-平台不可迁移 | — | 无 | 未知段拒绝：`src/wechat_onebot/service.py:976-977` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1874` |
| 25 | 状态侧信道：已读/输入状态/在线设备 | 未覆盖-平台不可迁移 | — | 已读、在线设备无；输入状态有产品无接口 | 无同类动作或事件：`src/wechat_onebot/protocol/converters.py:175-176` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1164,2762,3515` |
| 26 | 资料/装扮/在线状态/好友分组 | 未覆盖-平台不可迁移 | — | 无（QQ 装扮/分组）；微信资料有产品无接口 | 无同类动作：`src/wechat_onebot/onebot/actions.py:86-221` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1990,5397` |
| 27 | 修改资料/头像/签名 | 明确排除 | — | 无接口 | 安全红线与定位：`docs/architecture.md:43-50` / 上游同类：`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1990` |
| 28 | OCR/翻译/AI 语音/语音转文字 | 明确排除 | — | — | 内容处理不属网关：`docs/architecture.md:43-50` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4076` |
| 29 | 拍一拍（动作 + 通知事件） | 未覆盖-可迁移（受限） | U-18 | 有 | 未知段拒绝、无通知分支：`src/wechat_onebot/service.py:976-977`、`src/wechat_onebot/protocol/converters.py:175-176` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:637`、`SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:128-152` |
| 30 | 历史查询与检索（`get_*_msg_history`） | 已覆盖 | — | 有 | `src/wechat_onebot/onebot/actions.py:185-206` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:971`；双方均无全文检索 |
| 31 | 文件信息/媒体下载动作 | 部分覆盖 | — | 有 | 已有 `get_image/get_record` + `/media` 发布，无"按 URL 落盘"动作：`src/wechat_onebot/onebot/actions.py:209-219`、`src/wechat_onebot/onebot/network/http_server.py:112-117` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4509`（`download_file`）；`get_file` 与其为两个动作 |
| 32 | 好友/群信息查询 | 已覆盖 | — | 有 | `src/wechat_onebot/onebot/actions.py:152-179`；字段更窄处用 `unavailable_fields` 标注（`docs/onebot-compatibility.md:59`） / `SnowLuma v2.4.2: packages/onebot/src/actions/index.ts:292-293`。注：`delete_friend` 原生无接口、价值低，未单列（§4.4）；`get_group_share_link`/`get_group_system_msg` 见 #33 |
| 33 | 群分享链接/群系统消息 | 未覆盖-平台不可迁移 | — | 无接口 | 无动作：`src/wechat_onebot/onebot/actions.py:86-221` / `SnowLuma v2.4.2: packages/onebot/src/actions/group-info.ts:35,566` |
| 34 | 系统类 `bot_exit` | 明确排除 | — | — | 进程生命周期归 Launcher/WebUI：`docs/HANDOVER.md:7` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3687` |
| 35 | 朋友圈发布/读取（微信侧原生已有） | 未覆盖-可迁移（受限，高风险） | U-20 | 有 | 网关无动作/事件；helper 原生代码存在但受门禁：`vendor/helper/wxbot_framework/backend.py:172-188`、`vendor/helper/native/text_service.py:850-906`、`vendor/helper/native/moments_transport.py:106-137`；`docs/HANDOVER.md:134` 记录未映射 / SnowLuma **无朋友圈能力**（QQ 空间仅作社类类比：`SnowLuma v2.4.2: packages/onebot/src/actions/qzone.ts:357`） |
| 36 | 上游占位/语义降级动作 | 不计缺口 | — | — | `SnowLuma v2.4.2: packages/mcp/src/generated/catalog.json`：9 个占位（含 4 个标准名）+ 2 个降级，实际有效约 211/222 |

#### 3.2.1 20 个未注册标准动作的去向

| 分类 | 动作 | 清单 / 说明 |
| --- | --- | --- |
| 可迁移（5） | `delete_msg`、`set_friend_add_request`、`set_group_kick`、`set_group_leave`、`set_group_name`（待确认） | U-14、U-08、U-16；`set_group_name` 的权限/可见性待实机 |
| 平台不可迁移（13） | `send_like`、`set_group_ban`、`set_group_anonymous_ban`、`set_group_whole_ban`、`set_group_admin`、`set_group_anonymous`、`set_group_card`（仅 self 昵称）、`set_group_special_title`、`get_group_honor_info`、`set_group_add_request`（语义不成立，待确认）、`get_cookies`、`get_csrf_token`、`get_credentials` | 见 §3.2 行 18、19、33 |
| 明确排除（2） | `set_restart`、`clean_cache` | 进程/缓存由 Launcher 与 WebUI 管理（`docs/HANDOVER.md:7`）；上游两者本身也是占位/降级 |

**小结**：动作层差距分三层——① 协议层可补（历史/媒体读取已覆盖；缺流式与少量段类型）；② 原生受限（撤回、好友申请、群管理、群公告、拍一拍、表情导出）；③ 平台不可迁移（QQ 专有动作簇）。微信侧**原生已有但未映射**的还有群邀请/建群（`vendor/helper/wxbot_framework/backend.py:21,159-163`）、主动好友申请（`backend.py:156-157`，4.2.0.12）、表情导出、公众号、朋友圈/防撤回——这些不是 SnowLuma 差距，见 §5.2。

### 3.3 消息段

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 37 | `text` / `at` 基础段 | 已覆盖 | — | 有 | `src/wechat_onebot/protocol/converters.py:34-40`、`src/wechat_onebot/service.py:897-908` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:134-144` |
| 38 | 群 @全体成员（`at qq="all"`） | 未覆盖-可迁移（受限） | U-19 | 有（群主/管理员） | 本仓库显式拒绝：`src/wechat_onebot/service.py:900-902`；文档声明：`docs/onebot-compatibility.md:41` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/element-codecs.ts:254-263`、`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:2905` |
| 39 | `image` / `record` | 部分覆盖 | — | 有 | 字段更窄（无闪照/哈希/时长等原生不提供的字段）：`src/wechat_onebot/protocol/converters.py:60-72`、`src/wechat_onebot/driver/helper.py:413-424` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:185-223` |
| 40 | `video` 入站 | 部分覆盖 | — | 有 | 仅 helper 下载完成时出段；sdk 依赖框架 extra：`src/wechat_onebot/driver/helper.py:419-424`、`src/wechat_onebot/driver/sdk.py:485-501` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:224-244` |
| 41 | `video` 出站 | 已覆盖（C 批；按驱动/构建） | U-02 | 有（SDK Q-3 已验；helper Q-4 待验） | SDK 4.2.0.52 映射 H.264 静音 MP4（Q-3 私聊短样本可播放；其他编码/带音轨 pending）；helper 4.2.0.12 映射但私聊/本机待 Q-4；其他构建与转发链仍 1404：`src/wechat_onebot/service.py:812-815`、`src/wechat_onebot/service.py:884-888`、`src/wechat_onebot/service.py:909-914`、`src/wechat_onebot/driver/base.py:101-104`、`src/wechat_onebot/driver/helper.py:101,652-662`、`src/wechat_onebot/driver/sdk.py:45,726-729`、`src/wechat_onebot/protocol/element_manifest.py:52-54`、`docs/onebot-compatibility.md:41` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:224-244` |
| 42 | `face` | 部分覆盖 | — | 无接口（QQ 表情 ID） | 只支持 `face.file`：`src/wechat_onebot/protocol/converters.py:41-46`、`src/wechat_onebot/service.py:909-921` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:145-149` |
| 43 | `reply` 入站 | 已覆盖 | — | 有 | 优先级匹配 + 引用快照、不猜 ID：`src/wechat_onebot/service.py:313-359` / `SnowLuma v2.4.2: packages/onebot/REPLIES.md:3-28` |
| 44 | `reply` 出站 | 未覆盖-可迁移（受限：仅 4.2.0.12 声明） | U-01 | 有 | 整链 1404：`src/wechat_onebot/service.py:882-883`；原生：`vendor/helper/wxbot_framework/backend.py:35-43,132-133`、`vendor/helper/native/text_service.py:827-848`；helper 发送无 native ID：`src/wechat_onebot/driver/base.py:32-34`（sdk 有：`src/wechat_onebot/driver/sdk.py:734-737`）；测试固化不宣称：`tests/test_helper42.py:49-50` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:150-161` |
| 45 | `node` / `forward` 段（含 `get_forward_msg`） | 部分覆盖 | — | 有 | 入站解析已覆盖、出站仅降级：`src/wechat_onebot/protocol/rich_content.py:41-112`、`src/wechat_onebot/service.py:365-372` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:172-184` |
| 46 | 卡片类出站：`json`/`xml`/`share`/`location`/`contact` | 明确排除 | — | 无接口 | 文档声明不支持：`docs/onebot-compatibility.md:46`；代码拒绝：`src/wechat_onebot/service.py:884-888` / `SnowLuma v2.4.2: packages/onebot/src/message-parser.ts:427-440` |
| 47 | `music` | 已覆盖（降级语义明确） | — | 有 | `audio` → 语音条、`url` → 文本：`src/wechat_onebot/service.py:889-891,964-976` / `SnowLuma v2.4.2: packages/onebot/src/message-parser.ts:442-524` |
| 48 | `file` 段 | 部分覆盖 | — | 有 | 出站支持、入站转文本链接（文档已声明）：`src/wechat_onebot/protocol/converters.py:47-59`、`src/wechat_onebot/service.py:802-806` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:251-264` |
| 49 | `poke` 段（好友私聊窗口抖动） | 未覆盖-平台不可迁移 | — | 无（与"拍一拍"不同） | 未知段拒绝：`src/wechat_onebot/service.py:976-977` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:265-270` |
| 50 | `mface`/`markdown`/`inline_keyboard`/`flash_file`/`rps`/`dice` | 未覆盖-平台不可迁移 | — | 无 | 未知段拒绝：`src/wechat_onebot/service.py:976-977` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:245-287` |
| 51 | CQ 编解码与 `raw_message` | 已覆盖 | — | 有 | `src/wechat_onebot/protocol/codec.py:11-108` / `SnowLuma v2.4.2: packages/onebot/src/helper/cq.ts` |
| 52 | 发送策略硬校验（发前拒绝而非静默降级） | 已覆盖 | — | 有 | `src/wechat_onebot/service.py:909-923` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:44-96` |

**小结**：入站段（文本、引用、转发、媒体降级）与上游相当；**出站 `reply` 仍会打断真实客户端；`video` 已由 C 批按驱动/构建映射（SDK Q-3 已验；helper 4.2.0.12 映射、Q-4 待验；转发链内仍 1404）**，其余差异是"微信原生不提供的字段"或平台的媒体形态。`poke` 段（窗口抖动）与"拍一拍"是两件事，本文分开判定。

### 3.4 富消息与文件传输

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 53 | 合并转发发送动作族 | 部分覆盖 | U-13 | 有 | 仅 2 个扩展动作且**降级为普通消息链**（`forward_mode=normal_messages`、`native_forward=false`、不伪造 `forward_id`）：`src/wechat_onebot/onebot/actions.py:87-96`、`src/wechat_onebot/service.py:570-642`、`src/wechat_onebot/protocol/outbound_forward.py:10-16` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4185` |
| 54 | 转发节点内联引用 | 部分覆盖 | —（依赖 4.1 出站引用） | 有 | 节点内 `reply` 同样被拒：`docs/onebot-compatibility.md:89`、`src/wechat_onebot/protocol/outbound_forward.py:10-16` / `SnowLuma v2.4.2: packages/sdk/README.md:330-356` |
| 55 | 引用解析（有状态匹配） | 已覆盖 | — | 有 | `src/wechat_onebot/service.py:313-359` / `SnowLuma v2.4.2: packages/onebot/REPLIES.md:3-28` |
| 56 | 图片引用刷新与历史修复 | 已覆盖 | — | 有 | 只做本地索引重算、不回源：`src/wechat_onebot/media.py:197-302`、`src/wechat_onebot/service.py:1216-1227` / `SnowLuma v2.4.2: packages/onebot/REPLIES.md:30-42` |
| 57 | 媒体来源与体积限制 | 部分覆盖 | — | 有 | 多来源 + 三层限制；无"单文件总预算/节点选择"：`src/wechat_onebot/media.py:304-352`、`src/wechat_onebot/media.py:401`（下载默认 30 s）、`src/wechat_onebot/config.py:64-68`、`src/wechat_onebot/audio.py:23` / `SnowLuma v2.4.2: packages/protocol/HIGHWAY_UPLOADS.md:89-110` |
| 58 | Highway 上传并发/节点选择 | 未覆盖-平台不可迁移 | — | 无（QQ 专有） | 上传在原生内部完成：`src/wechat_onebot/media.py:304-352` / `SnowLuma v2.4.2: packages/protocol/HIGHWAY_UPLOADS.md:3-87` |
| 59 | 流式文件传输动作族 | 未覆盖-可迁移 | U-12 | 有 | 只有 WS 帧上限、无分块协议：`src/wechat_onebot/onebot/network/config.py:13-24` / `SnowLuma v2.4.2: packages/onebot/src/actions/stream-file.ts:1-55`、`SnowLuma v2.4.2: packages/onebot/src/actions/stream-download.ts:1-29` |
| 60 | 群文件永久/临时空间回退 | 未覆盖-平台不可迁移 | — | 无 | 无群文件动作：`src/wechat_onebot/onebot/actions.py:86-221` / `SnowLuma v2.4.2: packages/onebot/GROUP_FILES.md:3-26` |

**小结**：入站富消息与引用能力与上游持平；**出站富语义（引用、原生转发卡片）是主要差距**，且都依赖原生桥接。文件传输层我方走"大帧 + 上限"路线，上游走"分块协议"路线，属工程取舍。

### 3.5 存储与历史

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 61 | 每账号独立 SQLite（消息/ID/媒体） | 已覆盖 | — | 有 | `src/wechat_onebot/core/runtime.py:56-65`、`src/wechat_onebot/media.py:68-87` / `SnowLuma v2.4.2: packages/onebot/src/instance.ts:145-147` |
| 62 | 消息表结构与撤回墓碑 | 已覆盖（等价） | — | 有 | 无历史回填即无"撤回复活"路径：`src/wechat_onebot/onebot/message_store.py:24-70`；撤回处理见 `docs/onebot-compatibility.md:57` / `SnowLuma v2.4.2: packages/onebot/src/message-store.ts:956-997` |
| 63 | 消息 ID 稳定性 | 部分覆盖 | — | 有 | 持久化映射（库是唯一真源）vs 可重算哈希：`src/wechat_onebot/onebot/message_id.py:8-17`、`src/wechat_onebot/onebot/message_store.py:177-226` / `SnowLuma v2.4.2: packages/onebot/src/message-id.ts:18-31` |
| 64 | 媒体多键索引与 URL 刷新 | 已覆盖 | — | 有 | `src/wechat_onebot/media.py:68-87,197-302` / `SnowLuma v2.4.2: packages/onebot/src/media-store.ts:73` |
| 65 | 外部 ID ↔ 数字 ID 映射 | 已覆盖 | — | 有 | `src/wechat_onebot/ids.py:23-77`、`src/wechat_onebot/onebot/message_store.py:73-118` / `SnowLuma v2.4.2: packages/protocol/src/identity-service.ts:129-217` |
| 66 | 迁移与保留治理 | 部分覆盖 | — | 有 | `CREATE TABLE IF NOT EXISTS` 自愈，无版本化迁移/配额/清理：`src/wechat_onebot/onebot/message_store.py:26-90` / `SnowLuma v2.4.2: packages/onebot/src/message-store-migration.ts:1-20` |
| 67 | 历史分页与检索 | 已覆盖 | — | 有 | `count` 1–100 + 游标 + 反向：`src/wechat_onebot/onebot/actions.py:185-206` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:971`；双方均无全文检索 |
| 68 | 登录历史回填 | 明确排除 | — | 无接口（读微信加密本地库越界） | 文档声明"历史只读运行期观测"：`docs/onebot-compatibility.md:34`；安全红线：`docs/architecture.md:43-50` / `SnowLuma v2.4.2: packages/onebot/src/history-sync.ts:14-74`、`SnowLuma v2.4.2: packages/onebot/src/config.ts:81` |

**小结**：存储架构与账号隔离等价；差异是"ID 稳定性策略"与"迁移/保留治理"，不影响协议语义。

### 3.6 多账号

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 69 | 每账号 Runtime / 端点 / PID 独占 | 已覆盖 | — | 有 | `src/wechat_onebot/core/runtime.py:47-75`、`src/wechat_onebot/bridge/process_lock.py:18-57` / `SnowLuma v2.4.2: packages/onebot/src/manager.ts:126-184` |
| 70 | 管理面多账号批量操作 | 未覆盖-可迁移 | U-28 | 有 | 一次一个账号：`webui/index.html:705-713` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1941-2070` |
| 71 | 临时会话 | 未覆盖-平台不可迁移 | — | 无 | 无临时会话分支：`src/wechat_onebot/protocol/converters.py:86-121` / `SnowLuma v2.4.2: packages/onebot/src/temp-session-store.ts:12` |

**小结**：后端多账号模型等价（每账号 Runtime/DB/端点/PID 锁），差在管理面批量操作与展示。

### 3.7 WebUI 管理面

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 72 | 管理面规模（30 vs 66 路由） | 部分覆盖 | — | 有 | `src/wechat_onebot/webui/server.py:179-667` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1611-1751`、`SnowLuma v2.4.2: packages/core/src/webui/storage-routes.ts:76-164`、`SnowLuma v2.4.2: packages/core/src/webui/update-routes.ts:12-70` |
| 73 | 登录/首设/改密/状态/实时流 | 已覆盖 | — | 有 | `src/wechat_onebot/webui/server.py:210-295`、`:137-152`、`:494-508` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1783` |
| 74 | 调试台与消息编排 | 部分覆盖 | — | 有 | 有 JSON 段编辑 + 发送确认，无逐段表单/文件源：`webui/index.html:1569-1646`（消息输入在 `:1605`） / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1611` |
| 75 | 日志查看/导出与级别热切换 | 部分覆盖 | U-06 | 有 | 仅内存 200 条 + 前端导出，无文件日志/级别路由：`src/wechat_onebot/webui/server.py:427-431`、`src/wechat_onebot/logging_setup.py:1-29` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1836-1873` |
| 76 | 存储管理与统计 | 部分覆盖 | — | 有 | 只有媒体上限 + 统计，无配额/保留/清理：`src/wechat_onebot/webui/server.py:596-631` / `SnowLuma v2.4.2: packages/core/src/webui/storage-routes.ts:76-164` |
| 77 | 通知渠道（Webhook/SMTP） | 未覆盖-可迁移 | U-24 | 有 | 本仓库无通知实现 / `SnowLuma v2.4.2: packages/core/src/notifications/config.ts:40-70` |
| 78 | TOTP 两步验证 | 未覆盖-可迁移 | U-22 | 有 | 本仓库无 TOTP / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:978`、`SnowLuma v2.4.2: packages/core/src/webui/totp.ts:1-40` |
| 79 | 管理面 TLS | 未覆盖-可迁移 | U-23 | 有 | 默认 loopback、无 TLS/证书管理：`docs/network-config.md:97` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1469-1536` |
| 80 | 管理密码强度策略 | 未覆盖-可迁移 | U-27 | 有 | 仅 8–1024 长度：`src/wechat_onebot/webui/auth.py:44-46` / `SnowLuma v2.4.2: packages/core/src/webui/auth.ts:51-73`、`SnowLuma v2.4.2: packages/core/src/webui/server.ts:913-947` |
| 81 | 真实账号头像 | 部分覆盖 | — | 有（无接口） | 字母占位头像，无真实头像：`webui/index.html:70-72,494,1080` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1214` |
| 82 | 内置更新器 / 备份 / 外观宿主 / EULA 同意 | 明确排除 | — | — | 由外部 Launcher 承担（推断：`docs/HANDOVER.md:7`、`docs/troubleshooting.md:27`）；外观为单文件 UI：`webui/index.html:1-2217` / `SnowLuma v2.4.2: packages/core/src/webui/update-routes.ts:12-70`、`SnowLuma v2.4.2: packages/core/src/webui/server.ts:1537-1610`、`SnowLuma v2.4.2: packages/webui/INTEGRATIONS.md:3-75` |

**小结**：核心闭环（登录/状态/调试/日志/端点/诊断）完备且部分更严格；缺口都在运维增强与远程访问。更新器/备份/外观属"上游平台化"与"本仓库 Launcher 分工"的差异，不计缺口。

### 3.8 鉴权与安全

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 83 | 凭据随机生成与密码哈希 | 已覆盖 | — | 有 | PBKDF2-SHA256 310k：`src/wechat_onebot/webui/auth.py:14-54`；随机 token：`src/wechat_onebot/config.py:42,50` / `SnowLuma v2.4.2: packages/onebot/src/config.ts:25,86-88`、`SnowLuma v2.4.2: packages/core/src/webui/auth.ts:2,76` |
| 84 | OneBot token 策略与暴露面加固（裸 token/安全头/登录限速/体上限） | 部分覆盖 | U-05 | 有 | 空 token = 关闭鉴权且无强度校验：`docs/network-config.md:82`、`src/wechat_onebot/onebot/network/http_server.py:24-28`；登录无限速/无安全头：`src/wechat_onebot/webui/server.py:210-242` / `SnowLuma v2.4.2: packages/onebot/HTTP_SSE.md:22-26`、`SnowLuma v2.4.2: packages/core/src/webui/onebot-token-policy.ts:63-113`、`SnowLuma v2.4.2: packages/common/src/access-token.ts:5-20`、`SnowLuma v2.4.2: packages/core/src/webui/request-security.ts:5-42` |
| 85 | 管理会话与 OneBot 凭据分离 | 已覆盖 | — | 有 | HttpOnly Cookie（`SameSite=Strict`）：`src/wechat_onebot/webui/server.py:240-241`；设计说明：`docs/snowluma-alignment.md:47-48` / `SnowLuma v2.4.2: packages/webui/README.md:72-77` |
| 86 | CSRF 缓解 | 部分覆盖 | — | 有 | 无 CSRF token，靠 SameSite + 仅 JSON：`src/wechat_onebot/webui/server.py:210-242` / `SnowLuma v2.4.2: packages/core/src/webui/request-security.ts:44-77` |
| 87 | 凭据轮换撤销既有会话 | 部分覆盖 | — | 有 | 改密 `revoke_all`：`src/wechat_onebot/webui/server.py:260,284` / `SnowLuma v2.4.2: packages/onebot/HTTP_SSE.md:67-69` |
| 88 | 无遥测 / 数据外发可控 | 已覆盖 | — | — | 本仓库无遥测代码；仅向用户配置端点上报（`docs/network-config.md:86`） / `SnowLuma v2.4.2: PRIVACY.md:35-61` |

**小结**：密码学与会话隔离已对齐；缺口集中在"暴露面加固"（U-05）与远程访问增强（TOTP/TLS）。高风险点：空 token 会关闭端点鉴权，当前无机制阻止其暴露到非本机。

### 3.9 部署与工程生态

| # | 能力 | 判定 | 清单 | 微信侧 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 89 | 发行形态与平台矩阵 | 部分覆盖 | — | — | Windows x64 便携包（内嵌 CPython）：`.github/workflows/bot.yml:10-11`、`scripts/build_portable.py:34-48`、`vendor/wechatbot_api/VENDORED.json:6` / `SnowLuma v2.4.2: .github/workflows/release.yml:42-57,91-109`、`SnowLuma v2.4.2: README.md:53`（macOS 有代码分支、无发行包） |
| 90 | 容器/服务化 | 未覆盖-平台不可迁移 | — | — | 依赖 Windows 微信 GUI + 注入：`scripts/autostart.ps1:9-13` / `SnowLuma v2.4.2: .github/workflows/release.yml:194-248` |
| 91 | 发布流程 | 明确排除 | — | — | 人工发布 + Launcher 复验：`docs/HANDOVER.md:66` / `SnowLuma v2.4.2: .github/workflows/release.yml:119-192` |
| 92 | CI 治理（lint/类型/覆盖率/矩阵） | 部分覆盖 | — | — | 单作业 windows-x64：`.github/workflows/bot.yml:10-53` / `SnowLuma v2.4.2: .github/workflows/ci.yml:17-106` |
| 93 | 依赖固定与 vendored 校验 | 已覆盖 | — | — | `.github/workflows/bot.yml:26-35`、`vendor/helper/VENDORED_SHA256.json` / `SnowLuma v2.4.2: LICENSE:32-35` |
| 94 | 测试规模与分层 | 部分覆盖 | — | — | 51 个测试文件 / 750 项（`pytest --collect-only`）；无真实客户端 e2e：`docs/live-preflight.md:3` / `SnowLuma v2.4.2: AGENTS.md:54-90`；测试文件 518（含 scripts 生成器测试为 519） |
| 95 | 动作目录 / 元素四向清单单一真源 | 部分覆盖 | U-07 | — | 运行期目录（`src/wechat_onebot/onebot/actions.py:220-225`），无生成快照/漂移校验 / `SnowLuma v2.4.2: .github/workflows/ci.yml:39-43`、`SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:1-27,134-288` |
| 96 | 客户端 SDK | 未覆盖-可迁移 | U-25 | — | 对外契约只有 OneBot 协议：`docs/onebot-compatibility.md:3` / `SnowLuma v2.4.2: packages/sdk/README.md:3`、`SnowLuma v2.4.2: packages/sdk/src/actions.ts:5-240` |
| 97 | MCP 服务器 | 未覆盖-可迁移 | U-26 | — | 本仓库无 MCP / `SnowLuma v2.4.2: packages/mcp/README.md:3-11`、`SnowLuma v2.4.2: packages/mcp/src/tools.ts:57-162` |
| 98 | 插件系统 | 已覆盖（双方均不做） | — | — | 上游 helper 插件运行器被显式禁用：`docs/vendor-helper.md:5` / `SnowLuma v2.4.2: AGENTS.md:3` |
| 99 | 文档体系与诊断工具 | 已覆盖 | — | — | 16 篇 docs + 交接制度（`docs/README.md:1-35`；SnowLuma 比对索引在 :29）；`doctor` 与预检脚本（`src/wechat_onebot/cli.py:81-139`） / `SnowLuma v2.4.2: AGENTS.md:1-52` |

**小结**：工程质量"够用但不齐"——测试规模可观、依赖固定更成文；缺 lint/类型/覆盖率与 OS 矩阵。发布自动化、PR 治理、容器化属定位/平台约束，不建议对齐。

---

## 四、未覆盖功能清单（可迁移，28 项）

> **口径**：28 个**唯一能力条目**（P0 1 / P1 7 / P2 20），与第三节矩阵行一一对应：其中 **22 个在矩阵中判"未覆盖-可迁移"**，**6 个是"部分覆盖"的补强项**（U-05 暴露面加固、U-06 文件日志、U-07 目录/元素单一真源、U-11 CQ 字符串、U-13 合并转发卡片、U-21 表情导出）——即"现状部分可用、补齐后达到上游等价"。条目是"可实施单元"，不等于上游动作数：有的条目合并了多个标准动作（U-16），有的只对应一个段类型（U-01）。按能力域计行会因 5 组跨域重复而高估，去重后为 28 个唯一能力；本节已合并重复项，并补入核验发现的 4 项（好友申请列表并入 U-08、@全体 U-19、密码强度 U-27、表情导出 U-21）。
>
> **排序维度**：V = 外部 OneBot 客户端价值；F = 微信侧可行性（高 = 纯协议/管理面可做；中 = 原生已有/需实机；低 = 需新写原生或依赖未验证协议）；P = 与"只做网关"定位的契合度。**P0** = V 高 ∧ F 中高 ∧ P 高且当前会使真实客户端失败；**P1** = 至少两项为高且无一项为低，或 P0 条件成立但需产品决策/实机先行；**P2** = 其余。
>
> **B 批提示**：B 批（已提交 `5a3a360`）已落地 U-07/U-06/U-05a/U-04/U-08a 的部分或全部内容（U-08b 审批/备注仍未实现）；以下各条的状态与优先级保留 A 批基线判断，B 批验收见 `docs/HANDOVER.md`。

### 4.1 P0（1 项）

#### U-01 出站引用（`reply` 段映射原生引用发送）

| 项 | 内容 |
| --- | --- |
| 是什么 | 让标准 `reply` 段能映射为原生"引用回复"发送，而不是拒绝整条消息链 |
| 差异在哪 | SnowLuma 的 `reply` 元素四向全支持（`SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:150-161`）；本仓库任何出站 `reply` 都会让整链失败并返回 `1404`（`src/wechat_onebot/service.py:882-883`），文档已声明该行为（`docs/onebot-compatibility.md:43`） |
| 微信侧可行性（F=中） | **受限：仅微信 4.2.0.12 的 helper 后端声明该能力**——`text.quote.send` 只在 `TEXT_42012_CAPABILITIES`（`vendor/helper/wxbot_framework/backend.py:35-43`），后端方法 `send_quote_text`（`backend.py:132-133`）与原生实现（`vendor/helper/native/text_service.py:827-848`）齐备，helper 文档也写明"4.2.0.12 原生后端提供 `text.quote.send`"（`vendor/helper/docs/messages/text.md:18-28`）；4.1.15.x 与 4.2.0.11 的能力集合均不含该名称（`backend.py:10-31`），sdk 驱动无引用发送 |
| 新增限制 | **helper 发送不回传 native message ID**（默认实现返回空：`src/wechat_onebot/driver/base.py:32-34`；helper 未覆写），因此可引用**入站消息**（入站 native ID 已登记到 `native_message_ids`），但**不能引用机器人自己刚发的消息**；sdk 发送才有 native ID（`src/wechat_onebot/driver/sdk.py:734-737`） |
| 前置条件 | ① 驱动契约新增引用发送方法（仅 4.2.0.12 暴露）；② `reply.data.id` → native ID 反向查询（当前表只有 native→message 方向：`src/wechat_onebot/onebot/message_store.py:58-60`）；③ 目标为引用快照/跨账号/已删除时的明确策略（可见文本降级或显式失败，不伪造）；④ 按构建声明能力，避免 4.2.0.11 误报可用；⑤ 实机验证 |
| 风险 | 非 4.2.0.12 构建可能静默失败；引用自己发的消息在 helper 不可行；群聊/私聊的原生参数差异未知；与"本机防撤回"开关交互；**属产品决策**：`docs/HANDOVER.md:134` 记录"原生引用没有新增 OneBot 映射"，`tests/test_helper42.py:49-50` 把"不宣称"固化成测试 |
| 工作量 | M（1 个段语义 + 驱动映射 + 能力声明 + ID 反查 + Mock 测试 + 文档） |
| 证据 | 本仓库：`src/wechat_onebot/service.py:882-883`、`src/wechat_onebot/driver/base.py:32-34`、`vendor/helper/wxbot_framework/backend.py:35-43,132-133`、`vendor/helper/native/text_service.py:827-848`、`tests/test_helper42.py:49-50`、`docs/HANDOVER.md:134`；SnowLuma：`SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:150-161`、`SnowLuma v2.4.2: packages/onebot/REPLIES.md:3-28` |

### 4.2 P1（7 项）

| ID | 能力 | 差异与影响（V） | 前置条件（F） | 风险 | 证据 |
| --- | --- | --- | --- | --- | --- |
| U-02 | 出站视频发送 | C 批已按驱动/构建映射：SDK 4.2.0.52 映射 H.264 静音 MP4（Q-3 私聊短样本实测可播放；其他编码/带音轨 pending）；helper 4.2.0.12 已映射但私聊/本机待 Q-4（上游仅声明 filehelper/群已验证）；其他构建与转发发送扩展链仍 1404（V 高，能力不再一律缺失） | `Driver.send_video` 可选（默认 `DriverUnsupported`）；SDK 固定 wheel `video.send`；helper 4.2.0.12 能力门控 + 本地 `inspect_video`；发送前媒体校验，转发链明确拒绝（F 中高） | Q-3 证据为用户/B 方确认，非本轮 agent 实发；Q-4 未做；SDK 仅 H.264 静音 MP4，其他编码/带音轨 pending；转发链 1404；helper 4.2.0.12 上游 filehelper/群已验证、私聊待 Q-4 | `src/wechat_onebot/service.py:812-815`、`src/wechat_onebot/service.py:884-888`、`src/wechat_onebot/service.py:909-914`、`src/wechat_onebot/driver/base.py:101-104`、`src/wechat_onebot/driver/helper.py:101,652-662`、`src/wechat_onebot/driver/sdk.py:45,726-729`、`src/wechat_onebot/protocol/element_manifest.py:52-54`、`tests/test_video_send.py`、`docs/onebot-compatibility.md:41`、`docs/wechat-versions.md:33-38` / `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:224-244` |
| U-03 | 自身消息事件上报（`message_sent`） | 多客户端/日志/自检看不到机器人发出的消息（V 中）；现只有本地历史 | 数据已在本地快照（`src/wechat_onebot/service.py:843-859`），加适配器级开关与去重窗口即可（F 高）；上游 120 秒去重窗口（`SnowLuma v2.4.2: packages/onebot/src/self-sent-event.ts:3-41`） | 重复/回环；需与 `reportSelfMessage` 语义对齐（`SnowLuma v2.4.2: packages/onebot/src/types.ts:26`） | `src/wechat_onebot/service.py:850` / `SnowLuma v2.4.2: packages/onebot/src/self-sent-event.ts:3-41` |
| U-04 | 桥接离线事件（`bot_offline`） | A 批基线：客户端只能靠沉默推断掉线，无人值守排障困难（V 中） | 纯协议层：停止/异常时广播 notice 或扩展 `meta_event`，定义与 `lifecycle.disable` 的边界（F 高） | 格式需与 NapCat/上游习惯一致；避免与重连重复 | `src/wechat_onebot/core/runtime.py:124-135` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:212-219` |
| U-05 | 暴露面加固（token 策略 + 安全头 + 登录限速 + 裸 token） | A 批基线：空 token 会关闭端点鉴权且无护栏；WebUI 无安全头/限速（V 中） | 纯管理面/协议层：非 loopback + 空 token 拒绝保存、token 最短长度/易猜校验、CSP/X-Frame-Options/nosniff、登录限速与请求体上限、`Authorization: <token>` 兼容（F 高） | 不要破坏既有 Cookie/JSON 调用；错误信息避免泄露 | `docs/network-config.md:82`、`src/wechat_onebot/onebot/network/http_server.py:24-28`、`src/wechat_onebot/webui/server.py:210-242` / `SnowLuma v2.4.2: packages/core/src/webui/onebot-token-policy.ts:63-113`、`SnowLuma v2.4.2: packages/common/src/access-token.ts:5-20`、`SnowLuma v2.4.2: packages/core/src/webui/request-security.ts:5-42`、`SnowLuma v2.4.2: packages/onebot/HTTP_SSE.md:22-26` |
| U-06 | 文件日志 + 级别热切换 | A 批基线：网关只写 stderr，便携包排障只能复现；WebUI 仅内存 200 条（V 中） | 纯工程：rotating file handler（大小/份数可配）、WebUI 改级别端点，保持现有脱敏白名单（F 高） | 磁盘占用/路径权限；脱敏不能回退 | `src/wechat_onebot/logging_setup.py:1-29`、`src/wechat_onebot/webui/server.py:72-90,427-431` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1836-1873` |
| U-07 | 动作目录 / 元素四向清单单一真源 | A 批基线：文档与客户端拿到的能力可能不一致；新段类型易漏方向（V 中） | 纯工程：从注册表生成 catalog（含 returns/examples/read_only），新增元素×四向清单并加断言，CI 防漂移（F 高） | 生成物入库/更新流程需约定 | `src/wechat_onebot/onebot/actions.py:220-225` / `SnowLuma v2.4.2: .github/workflows/ci.yml:39-43`、`SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:1-27,134-288` |
| U-08 | 好友申请：列表 + 审批 | A 批基线：网关只有入站申请事件，无法列出/通过申请；客户端做"自动通过"需此能力（V 中） | **原生已有（按构建）**：helper `friend_requests()/accept_friend()`（`vendor/helper/wxbot_framework/backend.py:150-157`、`vendor/helper/native/text_service.py:720-745`），能力名 `friend.request.accept` 在 4.1.15.x / 4.2.0.11 / 4.2.0.12 三个构建均声明（`vendor/helper/wxbot_framework/backend.py:19,26,36`），主动发申请 `friend.request.send` 仅 4.2.0.12（`:41`）；上游有列表+处理动作（`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3215-3276`、`SnowLuma v2.4.2: packages/onebot/src/actions/request.ts:6`） | 申请列表/flag 语义与入站事件字段需对齐；写操作需显式确认；实机记录见 helper 文档（`vendor/helper/README.md:40`） | `src/wechat_onebot/driver/helper.py:350-361`、`vendor/helper/wxbot_framework/backend.py:150-157` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3215-3276`、`SnowLuma v2.4.2: packages/onebot/src/actions/request.ts:6` |

### 4.3 P2（20 项）

| ID | 能力 | 差异与影响 | 前置条件 | 风险 | 证据 |
| --- | --- | --- | --- | --- | --- |
| U-09 | SSE 事件流（OneBot 端口，含重放） | 少数浏览器/SSE 客户端可省一条 WS；常客户端无感 | 复用管理面 SSE（`src/wechat_onebot/webui/server.py:494-508`）+ 游标/背压设计 | 慢读者/内存窗口 | `src/wechat_onebot/webui/server.py:494-508`（仅管理面 SSE） / `SnowLuma v2.4.2: packages/onebot/HTTP_SSE.md:51-84` |
| U-10 | 单端口复用 HTTP+WS | 客户端配置更省一个端口 | 需 ASGI 多路复用或允许同端口挂载（当前冲突校验：`src/wechat_onebot/onebot/network/config.py:219-226`） | 监听冲突与安全边界 | `src/wechat_onebot/onebot/network/config.py:219-226` / `SnowLuma v2.4.2: packages/onebot/src/types.ts:47-70` |
| U-11 | 出站 CQ 字符串模式（`messageFormat`） | 只认字符串形态的旧客户端 | 适配器级开关 + 序列化（已有 `src/wechat_onebot/protocol/codec.py:94-108`） | 与数组模式行为一致性 | `SnowLuma v2.4.2: packages/onebot/src/config.ts:841-846` |
| U-12 | 流式文件传输动作族 | 发超大文件不必整包进内存或受 WS 帧限制 | 分块协议 + 临时目录 + 限额；不需要原生（落盘后走现有发送） | 临时文件清理/并发 | `src/wechat_onebot/onebot/network/config.py:13-24`（仅帧上限） / `SnowLuma v2.4.2: packages/onebot/src/actions/stream-file.ts:1-55` |
| U-13 | 原生合并转发卡片发送 | 真"聊天记录"卡片而非普通消息链 | **需原生新增**（helper 无转发卡片方法）；上游也是动作层自研 | 卡片协议复杂、风控 | `src/wechat_onebot/service.py:570-642` / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4185` |
| U-14 | 撤回动作 `delete_msg` | 客户端无法撤回已发消息 | **需原生新增**：helper 只有"本机防撤回"（`vendor/helper/native/local_recall_transport.py:18-34`），无主动撤回 | 2 分钟窗口/权限；文档已声明不支持（`docs/roadmap.md:14`） | `vendor/helper/native/local_recall_transport.py:18-34`、`docs/roadmap.md:14` / `SnowLuma v2.4.2: packages/onebot/src/actions/message.ts:251` |
| U-15 | 群成员增减事件 | 欢迎/退群播报类客户端 | **需原生补 `sub_type`/operator**：helper 已有 `group.events`（`vendor/helper/wxbot_framework/backend.py:21`），转换器缺标准字段即抑制（`src/wechat_onebot/protocol/converters.py:153-166`） | 字段可靠性；不伪造事件 | `src/wechat_onebot/protocol/converters.py:153-166` / `SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:29-61` |
| U-16 | 群管理：踢人/退群/改群名 | 基础群治理缺失 | **需原生新增**（驱动无方法：`src/wechat_onebot/driver/base.py:29-135`）；改群名的权限/可见性待确认 | 高风险写操作，需二次确认 | `src/wechat_onebot/driver/base.py:29-135` / `SnowLuma v2.4.2: packages/onebot/src/actions/group-admin.ts:6,614,638` |
| U-17 | 群公告读写 | 群公告同步 | **需原生新增**；权限模型待确认 | 同上 | `docs/roadmap.md:14`（群管理按不支持处理） / `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1209` |
| U-18 | 拍一拍（动作 + 事件） | 互动玩法；事件侧也缺 | **需原生新增**（网关未知段拒绝：`src/wechat_onebot/service.py:976-977`；无 poke 通知分支：`src/wechat_onebot/protocol/converters.py:175-176`） | 与 `poke` 段（窗口抖动）不同，勿混 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:637`、`SnowLuma v2.4.2: packages/onebot/src/event-converter/to-notice.ts:128-152` |
| U-19 | 群 @全体成员（`at qq="all"`） | 客户端无法 @所有人 | **需原生新增**：helper `validate_mentions` 仅接受 1–8 个成员 ID、无 @all（`vendor/helper/wxbot_framework/text.py:26-36`）；本仓库显式 1404（`src/wechat_onebot/service.py:900-902`），文档已声明（`docs/onebot-compatibility.md:41`） | 权限（群主/管理员）与风控 | `SnowLuma v2.4.2: packages/onebot/src/event-converter/element-codecs.ts:254-263`、`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:2905` |
| U-20 | 朋友圈发布/读取 | 社交动态；上游**无**朋友圈能力，属微信侧机会 | **原生代码已有但受 DLL 门禁**（`vendor/helper/wxbot_framework/backend.py:172-188`、`vendor/helper/native/text_service.py:850-906`、`vendor/helper/native/moments_transport.py:106-137`）；`docs/HANDOVER.md:134` 记录未映射 | 高风控、非消息语义；建议不暴露 | `vendor/helper/wxbot_framework/backend.py:172-188`、`docs/HANDOVER.md:134` / 上游仅 QQ 空间可类比（`SnowLuma v2.4.2: packages/onebot/src/actions/qzone.ts:357`） |
| U-21 | 收藏表情导出（只读本地缓存） | 客户端可读取账号已缓存的自定义表情；无完整 CRUD | **原生已有（仅 4.1.15.x）**：`emoticon.extract`/`export_emoticons`（`vendor/helper/wxbot_framework/backend.py:23,187-188`、`vendor/helper/wxbot_framework/emoticons.py:483-489`）；非权威收藏列表（`vendor/helper/docs/messages/emoticons.md:13-22`） | 受构建限制、数据非权威；写操作不可迁移 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:2402` |
| U-22 | TOTP 两步验证 | 远程管理 WebUI 时提升安全 | 纯管理面（密钥/恢复码/校验） | 恢复码丢失风险 | `src/wechat_onebot/webui/server.py:210-295`（登录流程无 TOTP） / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:978`、`SnowLuma v2.4.2: packages/core/src/webui/totp.ts:1-40` |
| U-23 | 管理面 TLS | 反代/远程访问 | 纯管理面（证书导入/删除、失败不静默降级） | 证书管理复杂度 | `docs/network-config.md:97` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1469-1536` |
| U-24 | 通知渠道（Webhook/SMTP） | 掉线/异常告警 | 纯管理面 | 密钥/模板注入 | `src/wechat_onebot/webui/server.py:179-667`（无通知路由） / `SnowLuma v2.4.2: packages/core/src/notifications/config.ts:40-70` |
| U-25 | 客户端 SDK | 降低接入成本；上游有 230 动作名与流式/反向 WS 服务端 | 生态层：建议外置并先做机器可读契约（U-07） | 维护多语言包成本 | `docs/onebot-compatibility.md:3` / `SnowLuma v2.4.2: packages/sdk/README.md:3`、`SnowLuma v2.4.2: packages/sdk/src/actions.ts:5-240` |
| U-26 | MCP 服务器 | AI 客户端操作网关；上游 14 工具/三模式 | 可外置进程复用 `POST /api/accounts/{account}/action`（`src/wechat_onebot/webui/server.py:633-643`） | 写模式安全门控 | `docs/onebot-compatibility.md:3`（对外只有 OneBot 协议） / `SnowLuma v2.4.2: packages/mcp/src/tools.ts:57-162` |
| U-27 | 管理密码强度策略 | 弱密码无护栏 | 纯管理面：长度/大小写/符号/空格规则 + 评估接口（对齐 `SnowLuma v2.4.2: packages/core/src/webui/auth.ts:51-73`） | 与既有 8–1024 长度兼容 | `src/wechat_onebot/webui/auth.py:44-46` |
| U-28 | 管理面多账号批量操作 | 多账号用户需逐账号切换 | 纯管理面（批量启停/状态） | 并发桥接未实测 | `webui/index.html:705-713` / `SnowLuma v2.4.2: packages/core/src/webui/server.ts:1941-2070` |

**未单列的低价值项**：状态文本命令（`#sl` 类）、性能基准、存储配额/清理、消息段可视化构造器、真实账号头像、`download_file` 落盘——矩阵中已判定（部分覆盖或平台不可迁移），因价值低/影响面小不进入排期清单。

### 4.4 优先级计数

| 优先级 | 数量 | 条目 |
| --- | ---: | --- |
| P0 | 1 | U-01 出站引用 |
| P1 | 7 | U-02 视频、U-03 自身消息、U-04 离线事件、U-05 暴露面加固、U-06 文件日志、U-07 目录/元素单一真源、U-08 好友申请 |
| P2 | 20 | U-09 ~ U-28 |

> 说明：上表 28 条中，22 条对应矩阵的"未覆盖-可迁移"行，6 条对应"部分覆盖"行（见本节开头），合计覆盖矩阵全部 28 个带清单编号的行；矩阵其余 71 行为已覆盖/平台不可迁移/明确排除/不计缺口，不进入清单。

---

## 五、平台不可迁移与明确排除

> 本节回答"为什么不做"，避免把下列条目误读为遗漏。判定依据：微信侧**产品不存在**（无），或**产品存在但无公开接口/原生未提供**（无接口），或与"只做网关"的定位冲突。

### 5.1 平台不可迁移（QQ 专有或微信无接口）

| # | 能力簇（上游） | 微信侧 | 为什么不可迁移 | 上游证据 |
| --- | --- | --- | --- | --- |
| N-1 | QQ 空间（29）：说说/评论/点赞/推荐流/主页 | 无 | QQ 空间是 QQ 独有社交产品；微信"朋友圈"是不同产品，且 SnowLuma 本身**没有**朋友圈能力 | `SnowLuma v2.4.2: packages/onebot/src/actions/qzone.ts:357`、`SnowLuma v2.4.2: packages/onebot/src/actions/qzone-public.ts:174` |
| N-2 | 群文件库（12）：目录树/上传/转存/重命名 | 无 | 个人微信群没有文件库/目录/永久空间，文件只是聊天消息 | `SnowLuma v2.4.2: packages/onebot/src/actions/group-file.ts:22` |
| N-3 | 群相册（9） | 无 | 微信无群相册产品 | `SnowLuma v2.4.2: packages/onebot/src/actions/group-album.ts:153` |
| N-4 | 群精华/群资料扩展/标记已读/群分享 | 无 / 无接口 | 微信无群精华；群资料扩展与"标记已读"无公开接口（微信不向发送方暴露已读） | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:670,3544,3571,2211,1164` |
| N-5 | 群待办/群签到/群荣誉 | 无接口 | 微信纵有类似入口（群打卡小程序）也无第三方接口 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:4967`、`SnowLuma v2.4.2: packages/onebot/src/actions/group-info.ts:375` |
| N-6 | QQ 系统表情（4）/商城表情 | 无 | 微信无"QQ 表情 ID/目录"体系；贴图发送已由 `image`/`face.file` 覆盖 | `SnowLuma v2.4.2: packages/onebot/src/actions/system-face.ts:64` |
| N-7 | 闪传文件集 / QQ 收藏 / 文件集 | 无接口 | 闪传是 QQ 专有协议；微信有"收藏"产品但无可用原生写接口 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:5034,3334` |
| N-8 | 群管理平台项：禁言/全员禁言/管理员/匿名/群头衔/加群设置/搜索/头像/标签 | 无 | 个人微信群没有匿名、禁言、管理员角色位与这些设置项（企业微信才有） | `SnowLuma v2.4.2: packages/onebot/src/actions/group-admin.ts:67,92,445,660,690` |
| N-9 | 资料卡点赞/个性装扮/自定义在线状态/好友分组/单向好友 | 无（QQ 专有）；微信资料有产品无接口 | QQ 资料卡与好友分组在微信无对应；微信资料/头像的修改见 §5.3 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:561,5397` |
| N-10 | 已读回执/在线设备/输入状态 | 无 / 无接口 | 微信不向发送方暴露已读、无在线设备列表；"对方正在输入"有产品、无接口 | `SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:1164,2762,3515` |
| N-11 | 机器人 Ark/内联键盘/markdown 消息 | 无 | QQ 机器人消息形态；微信无对应渲染 | `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:271-281`、`SnowLuma v2.4.2: packages/onebot/src/actions/extended.ts:3057,4725` |
| N-12 | `poke` 段（好友私聊窗口抖动） | 无（与"拍一拍"不同） | QQ 元素语义；微信拍一拍走动作/通知，见 U-18 | `SnowLuma v2.4.2: packages/protocol/src/element-manifest.ts:265-270` |
| N-13 | 临时会话 | 无 | 微信无"临时会话"产品语义 | `SnowLuma v2.4.2: packages/onebot/src/temp-session-store.ts:12` |
| N-14 | Highway 上传通道（并发/节点选择） | 无 | QQ 专有文件通道；微信上传由原生桥接内部完成 | `SnowLuma v2.4.2: packages/protocol/HIGHWAY_UPLOADS.md:3-87` |
| N-15 | Linux/arm64、macOS 发行、容器 | 无 | 原生桥接是 Windows x64 的 DLL + win_amd64 wheel；微信桌面端没有公开的 Linux 构建（待确认），macOS 架构与注入模型不同且无原生产物 | `SnowLuma v2.4.2: .github/workflows/release.yml:42-57,194-248` |
| N-16 | 群请求审批（`set_group_add_request` 标准语义） | 待确认 | 微信是邀请/邀请确认模型，无"管理员审批加群申请"的标准语义 | `SnowLuma v2.4.2: packages/onebot/src/actions/request.ts:33` |

### 5.2 微信侧原生已有但未映射（不是 SnowLuma 差距，属产品决策项）

| # | 能力 | 现状 | 证据 |
| --- | --- | --- | --- |
| P-1 | 群邀请/建群 | helper 4.1.15.x 后端声明 `group.member.invite`/`group.create` 并有方法，网关未映射；不属于 OneBot v11 标准动作 | `vendor/helper/wxbot_framework/backend.py:21,159-163` |
| P-2 | 主动好友申请 | 仅 4.2.0.12：`friend.request.send` 能力与 `send_friend_request` 方法；网关只映射入站申请 | `vendor/helper/wxbot_framework/backend.py:41,156-157` |
| P-3 | 本机防撤回（保留原消息） | 4.2.0.12 原生开关；网关未暴露（与本报告 U-14"主动撤回"是相反方向） | `vendor/helper/native/local_recall_transport.py:18-34` |
| P-4 | 公众号目录/文章读取 | helper 独立应用已验证（4.2.0.12）；网关后端未见对应方法，故 OneBot 不可用 | `vendor/helper/README.md:21,40` |

> 判断：P-1～P-4 不是"相对 SnowLuma 缺什么"，而是"本仓库可以决定是否开放"的额外能力。建议默认不开放（P-1/P-2/P-4 风控高、P-3 与防撤回语义冲突）。

### 5.3 明确排除（文档已声明或定位冲突）

| # | 能力 | 排除依据 | 说明 |
| --- | --- | --- | --- |
| X-1 | `set_restart` / `clean_cache` | `docs/HANDOVER.md:7` | 进程生命周期归 Launcher；无流式临时文件缓存体系；上游两者本身也是占位/降级 |
| X-2 | 出站 `json`/`xml`/`share`/`location`/`contact`、小程序/链接卡片 | `docs/onebot-compatibility.md:46` | 文档已声明不支持；微信卡片需原生 appmsg 注入，当前原生层无发送接口 |
| X-3 | OCR/翻译/AI 语音/语音转文字 | `docs/architecture.md:43-50` | 内容理解属客户端/外部服务；网关只交付原始媒体 |
| X-4 | 修改账号资料/头像/签名 | `docs/architecture.md:43-50` | 账号敏感写操作，非网关目标，且无公开接口 |
| X-5 | 登录历史回填（读取微信本地历史） | `docs/onebot-compatibility.md:34`、`docs/architecture.md:43-50` | 读微信加密本地库违反安全边界；"历史只读运行期观测"已声明 |
| X-6 | 内置更新器 / 自更新 | `docs/HANDOVER.md:7`（推断） | 版本切换由外部 Launcher 的实例模型负责 |
| X-7 | 备份/恢复 API | `docs/troubleshooting.md:27`（推断） | 外部 Launcher 承担实例备份/恢复；网关只要求停服务后复制数据目录 |
| X-8 | 外观宿主协议 / EULA 同意 | `webui/index.html:1-2217` | 单文件 UI，无 MaiBot/Electron 宿主场景；无对应法务需求 |
| X-9 | 插件运行器 | `docs/vendor-helper.md:5` | 上游 helper 插件运行器被显式禁用（`plugins={}`）；业务逻辑外置 |
| X-10 | npm 包生态 / 文档站 / Docker 镜像分发 | 定位差异 | Python 网关 + Windows 便携包 + Launcher；容器化已列 N-15 |

---

## 六、对照现有文档的边界说明

本报告**不重复**现有文档已经声明的能力限制，只在其上给出"可迁移性/优先级"判断；两者冲突时以代码为准。

| 现有文档 | 已声明的限制（不重复抄写） | 本报告的处理 |
| --- | --- | --- |
| [OneBot 兼容说明](onebot-compatibility.md) | 出站 `reply` 整链 1404（`docs/onebot-compatibility.md:43`）；`video` 已按驱动/构建映射（`docs/onebot-compatibility.md:41`：SDK Q-3、helper 4.2.0.12/Q-4 待验，转发链 1404），`json`/`xml`/`share`/`contact` 等仍不支持（`docs/onebot-compatibility.md:46`）；未注册动作 1404/404（`docs/onebot-compatibility.md:36`）；历史只读本地观测（`docs/onebot-compatibility.md:34`）；通知/请求抑制规则（`docs/onebot-compatibility.md:57`） | 出站引用判 **U-01（受限可迁移）**，只在"产品决策"层面建议重估；`video` 判 **U-02**（C 批已实现，Q-3/Q-4 分列）；卡片类判 **X-2**；未注册动作给出 5/13/2 分类；抑制规则属已声明限制，不计遗漏 |
| [微信版本与能力](wechat-versions.md) | 指纹锁定 4 个 helper 构建 + 2 个 sdk 构建（4.2.0.52/4.2.0.53，`docs/wechat-versions.md:12-25`）；驱动能力按构建不同（`docs/wechat-versions.md:27-65`） | U-01 限定"仅 4.2.0.12 声明"；U-02 分 helper 4.2.0.12 / sdk 4.2.0.52/4.2.0.53 两条路径；U-08 按构建可用 |
| [后续方向](roadmap.md) | 撤回、原生合并转发卡片、群管理"按原生接口限制，按不支持处理"（`docs/roadmap.md:14`） | 尊重该声明：U-13/U-14/U-16/U-17 均标注"需原生新增"，并注明是**产品决策**而非遗漏 |

**口径提示**：上游"框架/协议层支持"不等于本仓库"网关支持"。视频即典型——SnowLuma 的 `video` 四向支持、helper/SDK 原生有 `send_video`；本仓库 C 批已按驱动/构建映射（SDK Q-3 私聊短样本已验证；helper 4.2.0.12 映射但 Q-4 待验；其他构建/转发链 1404），不再按"未覆盖"表述。原 7 项纠偏中的第 1、5 项口径修正注记保留；本报告其他结论不变。

---

## 七、待确认问题与建议的验证方式

> 以下均需在装有对应微信构建的机器上实机验证；已执行项按实测/用户确认记录，未执行项不写入"已支持"。SnowLuma 侧未做真实 QQ 联调，其结论仅来自 v2.4.2 源码静态阅读。

| # | 待确认问题 | 影响 | 建议验证方式 |
| --- | --- | --- | --- |
| Q-1 | helper 4.2.0.12 的 `send_quote_text` 在真实微信上是否成功（群聊/私聊、引用他人消息） | U-01 | 管理员终端最小脚本：启动 helper 后端 → 用已缓存的原生消息 ID 调 `send_quote_text` → 观察对端渲染；记录失败码与 `report.stage`（`vendor/helper/native/text_service.py:827-848` 已有阶段判断） |
| Q-2 | 4.1.15.x / 4.2.0.11 是否也能引用（能力未声明） | 能力矩阵按构建表达 | 逐构建重复 Q-1；原生 DLL 不认 `quote_id` 时记录为"仅 4.2.0.12" |
| Q-3 | SDK 视频发送的真实行为（编码/时长/风控） | U-02 的 sdk 分支 | **已执行（2026-11-15，用户/B 方确认）**：SDK 4.2.0.52 私聊短静音 H.264 MP4 实测可播放；群聊/其他编码/带音轨/风控仍未覆盖。记录 `SendResult`。 |
| Q-4 | helper 4.2.0.12 `send_video` 的真实可用性 | U-02 | **未执行**：上游仅声明 filehelper/群已验证；私聊/本机待 Q-4。短 MP4（`inspect_video` 可识别）实发；记录成功/失败与 `report`。 |
| Q-5 | `accept_friend` 三个构建的实测差异 | U-08 | 测试小号发好友申请 → 入站 request → `accept_friend`；对照 `backend.capabilities`；helper 文档称 4.2.0.11/4.2.0.12 已实测通过（`vendor/helper/README.md:40`、`vendor/helper/native/README.md:7`） |
| Q-6 | 微信群成员增减的 `sub_type`/operator 是否可得 | U-15 | 测试群拉入/移出小号，抓 `group.events` 原始载荷 |
| Q-7 | 群公告/群名/踢人/退群是否有可用原生接口 | U-16/U-17 | 先检索 `vendor/helper/native` 与 vendored SDK API；无则确认"需新写原生"，不进入排期 |
| Q-8 | 微信是否有可用的"主动撤回"客户端接口（区别于 `WxSetLocalRecall`） | U-14 | 检索原生导出与上游 issue；无则长期 P2 |
| Q-9 | 朋友圈 `moments_capabilities` 在本机是否 `supported=true`（helper 文档称 4.2.0.12 已实测"仅自己可见"发布） | U-20 | 调 `moments_capabilities()` 读 flags（`vendor/helper/native/text_service.py:850-861`），不发布 |
| Q-10 | 群 @全体成员的原生路径与权限 | U-19 | 检索 helper 原生 at/mention 接口；确认是否需要新写原生；用群主/管理员小号验证 |
| Q-11 | 多账号并发桥接是否可用（当前未实测） | U-28 | 两个已登录微信进程 + 两个账号 Runtime，分别启停与发消息；观察 PID 锁与端点冲突 |
| Q-12 | 空 token 端点暴露到非 loopback 的实际风险面；FastAPI 默认 `/docs`、`/openapi.json` 是否暴露 | U-05 | 隔离网络内用 `webui.host=0.0.0.0` + 空 OneBot token 调用 `send_group_msg`；直接访问三个默认路径；验证后回滚 |

---

## 附：数据口径与自检

**固定数据（本轮复核）**

| 项 | 数值 | 口径 |
| --- | --- | --- |
| SnowLuma 声明动作 | 222（12 类）+ 6 别名 + 2 raw = 230 可执行名 | `SnowLuma v2.4.2: packages/mcp/src/generated/catalog.json` |
| SnowLuma v11 标准动作 | 38/38 全部存在 | 名称集合核对 |
| 上游占位/降级 | 9 个显式占位（含 4 个标准名）+ 2 个语义降级，实际有效约 211 | catalog 的 summary/notes |
| SnowLuma 入站事件/消息元素/WebUI/MCP | 23 类事件 / 16 类元素 / 66 处路由 / 14 个 MCP 工具 | 镜像源码统计 |
| 本仓库注册动作 | 24（18 标准 + 6 扩展） | `src/wechat_onebot/onebot/actions.py:86-221`；AST 复核 |
| 本仓库 WebUI/测试 | 30 路由；51 个测试文件 / 750 项 | `src/wechat_onebot/webui/server.py:179-667`；`pytest --collect-only` |
| 本报告矩阵/清单 | 99 行矩阵；28 个可迁移条目（P0 1 / P1 7 / P2 20） | 本文第三、四节 |

**自检**

- 所有 `路径:行号` 与 `SnowLuma v2.4.2: 路径:行号` 均重新核对到具体行内容（不只校验行号存在），并修正了 3 处行内容不匹配（拍一拍通知行号、媒体下载超时位置、消息编排输入位置）；B 批复核（已提交 `5a3a360`）刷新了受影响的 `src/`、`docs/` 行号与计数，报告结论未据此改写。
- 矩阵每个"未覆盖"行同时给出本仓库与 SnowLuma 两侧证据；28 个可迁移条目与矩阵第三列一一对应，无跨域重复、无同一能力两处不同优先级。
- `docs/onebot-v11-audit.md:146` 的"本项目注册 19 个动作"是该文档文首声明的 v0.9.0 代码基线（`7b62bdd`）历史快照，不代表当前注册表；当前注册表为 24 个动作（见上表与 `src/wechat_onebot/onebot/actions.py:86-221`），本报告保留该历史基线且不改写该审计文档。
- 未引用个人路径、账号、代理或任何本地分析目录；未复制上游大段源码。
- 本轮只改文档：新增本文件与 `docs/README.md` 索引一行；未改代码、未提交/推送/发布。**批次说明**：其后的独立纠偏批次（HEAD `1b19502`）提交了本报告复核出的 7 项一致性修正（5 项文档口径 + 2 项低风险代码文案/参数描述，P1/P0 均未实施）；本报告结论不变，测试计数与文档索引已按该批次刷新。**B 批说明（2026-11-15，已提交 `5a3a360`）**：工作区新增 U-07/U-06/U-05a/U-04/U-08a 的落地内容；本报告已按 B 批工作区刷新计数与引用行号（动作 24、测试 49 文件/645 项、新增 `get_friend_requests` 计为可选扩展），结论与优先级不变，B 批验收见 `docs/HANDOVER.md`。**C 批说明（2026-11-15，未提交）**：U-02 出站视频已按驱动/构建映射（SDK Q-3 私聊短样本已验证；helper 4.2.0.12 映射但 Q-4 待验；其他构建/转发链 1404）；测试计数更新为 50 文件 / 669 项，动作 24、WebUI 路由 33 不变，其他结论与优先级不变，验收口径见 `docs/HANDOVER.md` C 批节与 Q-3/Q-4。**SDK 升级说明（2026-11-18，未提交）**：vendored `wechatbot-api` 升级到 dev17，sdk 支持 4.2.0.52/4.2.0.53 双构建；helper `driver.supported_versions` 不变，测试计数同步为 669；4.2.0.53 仅做只读发现与离线群 @ 准备验证，未做真实群 @。**dev18 批次说明（2026-11-20，未提交）**：vendored `wechatbot-api` 升级到 dev18（bridge 11 / ABI 2），群通知自动显示名（experimental 出站使用）与手动绑定移除；计数刷新为 30 条 WebUI 路由 / 51 个测试文件 / 750 项，注册动作 24；受本批影响的 `server.py`、`webui/index.html` 行号已按当前工作区核对，报告结论与优先级不变。
