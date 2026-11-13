# 开发交接

## 接手前先读

新接手的 AI 必须先读根 [AGENTS.md](../AGENTS.md)、本文件和 [文档索引](README.md)，再核对工作区、分支、远端和实际运行版本。本文是 **2026-11-13** 的维护快照，早期修复基线为 `23f46eb`（v0.8.6），当前发布基线为 v0.11.4（Launcher 配套 v0.8.12、框架 dev14）；日期、测试数量及 Release 不代表未来状态。开发命令面向源码 checkout，便携包不包含完整测试及构建工具。

本仓库只维护 Windows 个人微信 OneBot v11 网关。外部 Launcher、AstrBot 与其插件是独立项目。历史上的 `bot/` 与 `launcher/` 布局已在 `2a16bc3` 收敛为根目录 Bot 工程，不能按旧文档恢复已移出的启动器。

## 代码与数据入口

| 入口 | 责任 |
| --- | --- |
| `cli.py`、`config.py` | CLI、首次配置、路径解析与环境检查 |
| `core/runtime.py` | 账号 Runtime、端点恢复、桥接生命周期 |
| `driver/helper.py`、`driver/sdk.py` | 固定 helper 原生服务；`wechatbot_api` 公开 SDK 驱动（4.2.0.52） |
| `bridge/` | 账号发现、PID 所有权和兼容会话包装 |
| `service.py`、`onebot/actions.py` | 动作编排、参数校验和结果 |
| `protocol/converters.py` | 用户/群 ID 和消息事件转换 |
| `ids.py`、`onebot/message_store.py` | 用户/群 ID、消息 ID、历史、资料和成员观测 |
| `onebot/network/`、`webui/` | 账号端点与管理会话，鉴权互相独立 |
| `media.py`、`vendor/helper/` | 网关媒体与固定上游原生实现 |

以上 Python 路径均相对 `src/wechat_onebot/`。当前 `HelperBridgeSession` 继承 `HelperDriver`，`OneBotInstance` 是 `OneBotService` 的别名；不要把设计分层误认为已经完成了彻底解耦。

默认 `data/accounts/<safe-account-id>/` 中的 `onebot.json`、`ids.sqlite3`、`messages.sqlite3` 分别保存网络、用户/群 ID 和消息/资料。媒体默认在 `data/media/<safe-account-id>/`。路径由实际配置决定；迁移前停止服务并保留完整 SQLite 数据，不能重新生成映射来解决显示问题。

## 已完成的关键修复

| 提交 | 内容与约束 |
| --- | --- |
| `ccc2192` | embeddable Python 的守护导入修复及启动日志诊断 |
| `a7e937e` | DLL 完整路径比较兼容 Windows 扩展路径，保持模块归属检查 |
| `4209c5b`、`9b325e2` | 本地媒体扩展路径、发送快照读取权限及原生文件/语音路径；不泛化为支持 UNC/网络盘 |
| `9ca025f` | 首次事件上报前持久化资料，昵称与群名片分离；成员离群不被旧观测恢复；实现 `get_stranger_info` |
| `87fe123` | 昵称从好友缓存查不到时，读取已验证的 4.1.15.56 非好友 LRU；原始名称先校验再去空白，非好友不进入好友列表 |
| `23f46eb` | v0.8.6 版本与发布基线 |

固定 helper 来源为 `119acdf9d74654c6ebb8cded2c6841b85af75867`（v0.9.4 更新；此前为 `a81c495`），其上叠加本地补丁。来源标记不等于未经修改的工作树；读取 [边界说明](vendor-helper.md)、[第三方声明](../THIRD_PARTY_NOTICES.md) 和 `VENDORED_SHA256.json` 后再修改原生部分。

## 当前限制与诊断重点

- 原生适配只承诺指定构建及指纹，不因版本号相同就放行。非好友昵称 LRU 仅用于分别验证的 4.1.15.56、4.2.0.11 和 4.2.0.12；没有真实资料时可能缺名。
- `get_stranger_info` 使用已映射、已观测资料，`no_cache` 只尝试刷新好友资料，不是任意陌生人联网查询。
- 原生合并转发卡片发送、撤回动作、群管理和原生引用回复仍不支持；v0.9.2 的转发发送扩展转换为普通消息，见下文。出站 `reply` 整条失败，不再静默省略引用；必须区分真实原生 @ 与普通文本标签。
- 不可用媒体在标准事件中使用明确的文本占位；底层原始诊断可能保留路径或空 URL。旧版发送历史可为 `[sent]` 占位；v0.9.0 新发送保存实际内容。不能把这些状态当成完整媒体接收或收件回执。
- 群消息未知角色可输出兼容默认值 `member`，不能证明真实群权限；观测成员资料将未知角色标在 `unavailable_fields`，标准 `role` 使用保守的 `member`。
- Helper 成员行缺少群昵称时，适配器的 `card` 可退回显示昵称；分开存储字段不等于每份 `card` 都是独立的真实群名片。
- AstrBot UMO 可由用户和群组合。应核对消息实际路由配置中的 `unique_session`，而不是改 Bot 的群 ID。诊断须区分网关事件、客户端正文和微信 UI。
- 日志位置以实例 `driver.helper_root` 为准；Release、源码和正在运行的实例可能是三个不同状态。详见 [故障排查](troubleshooting.md)。

## 验证与发布

在源码工作副本中按 [源码 README](https://github.com/PorkLittleHamburger/wechatbot/blob/main/README.md#快速开始) 安装锁定运行依赖与开发依赖，再执行；便携包根 README 为运行说明，不能代替此开发入口：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
pwsh -File .\scripts\build_portable.ps1
```

涉及 helper 联系人缓存时，还运行相应 helper 回归；例如 PowerShell 中设置 `$env:PYTHONPATH = Join-Path $PWD 'vendor/helper'`，再通过 pytest 执行其 `test_contacts.py`、`test_group_members.py` 和 `test_native_profiles.py`。涉及原生路径时执行 `scripts/test_native_paths.ps1`。真实注入和发送须按 [预检指南](live-preflight.md) 明确安排，Mock/CI 不替代真实收件验证。

v0.8.6 发布时记录：141 项 Bot、27 项相关 helper 回归通过；15 项 vendor 哈希、编译、ZIP 完整性和包内导入通过。[Windows CI](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/41658219077) 成功。非好友昵称另有只读探测命中证据；本记录不宣称所有账号的真实发送均经验证。

验证后提交并推送到 **PorkLittleHamburger/wechatbot**，使用已核实指向它的 remote；不要依赖 remote 名称推断仓库归属。发布前必须获得用户对 Release 的明确确认，核对两个版本字段、目标提交、CI、ZIP、helper/原生哈希和包内版本。上传后复核标签提交、附件大小/摘要、正式发布状态及 latest。当前 CI 只验证，不自动发布。

## 后续工作与交接更新

2026-11-04 v0.9.0 功能：账号 `onebot.json` 新增布尔值 `groupMentionOnly`，WebUI 通用设置可热更新；仅过滤未原生 @ 到机器人的群消息上报，私聊正常，本地存储保留。新增 helper `replies.py` 解析 appmsg 57，`text_service.py` 在文件分流前交付引用正文。消息库新增 `native_message_ids` 和 `reference_snapshots`，按账号/会话保存原消息索引和未缓存引用的快照。OneBot `reply` 的 `get_msg` 可返回已缓存原图；未缓存媒体只给出不可用说明，快照不进入历史。`tests/test_inbound_replies.py` 覆盖解析、过滤、引用、隔离、重启和 WebUI 热更新；这些功能已纳入 v0.9.0 源码与构建。

优先补充外部客户端端到端兼容、长期运行/重连和媒体消费者可达性验证；这些是待验证方向，不是已确认缺陷或交付承诺。尚无覆盖所有原生能力的无人值守真实环境验收。

2026-11-04 v0.9.0 富引用扩展：`forwarded.py` 递归解析链接及嵌套记录，`ForwardedReceiver` 对整个树与引用内容共享原生图片接收流程；`protocol/rich_content.py` 生成 OneBot 内容与有序转发投影，`forward_messages` 表持久化账号隔离的 `get_forward_msg` 数据。未知原作者 ID 为 0，原时间文本不伪造时间戳；旧引用快照允许被更完整元数据补全。`tests/test_rich_quotes.py` 覆盖多个嵌套 XML 形式、共享额度、图片校验流程、引用与转发读取、缓存及重启。前一段“未缓存媒体只给出不可用说明”是第一阶段实现记录，现已扩展为在具有有效元数据时尝试原生接收；缺失/失败仍明确标注。完整契约见 [富消息与引用](rich-messages.md)。

每次功能、配置、部署或验证状态变化后，同步相关指南与本文件；交接时复核远端 README。记录具体行为、代码入口和可重复验证，不写个人部署路径、账号或代理；明确区分已完成、待验证和规划。此次文档审计范围见 [审计记录](documentation-audit.md)。

2026-11-04 v0.9.0 修复 v11 核心通用性：标准转发节点、CQ 三种输入和 raw_message、HTTP GET/POST/form/JSON、HTTP POST 签名/快速回复、WS 三角色及 echo、异步/限速、生命周期、原消息撤回 ID、持久化媒体索引。标准测试不依赖 AstrBot。原始证据与修复映射见 [v11 审计](onebot-v11-audit.md)，当前能力与原生限制见 [兼容说明](onebot-compatibility.md)。

接手要点：`protocol/codec.py` 统一消息表示；旧转发用 `canonical_forward` 读取投影。`service.handle_quick_operation` 复用动作发送路径，关闭时取消排队限速工作，已进入原生发送的异步动作必须排空后才关闭 helper，不能取消 `to_thread` 等待就误判原生调用已结束。`onebot/network/requests.py` 保留 echo 的缺省/显式 null 差异。媒体新增账号目录 `media.sqlite3`；迁移需保留媒体目录和原文件，旧版本未索引的外部媒体需重新注册。网络新增 `rateLimitIntervalMs`、HTTP 客户端 `secret` 和 WS 客户端 `role`，默认行为见网络配置。

不要恢复静默丢弃出站 `reply` 的旧行为。微信原生引用、群管理、@全体、原生转发卡片发送等仍明确不支持；未知通知操作者/子类型或无法关联原消息时，只记录诊断，不伪造标准事件。资料未知默认值与 `unavailable_fields` 不能用来判断真实权限。群成员变动仍更新本地成员观测。

CR 修复重点：HTTP 生命周期 enable 不等待远端响应，停机/移除端点仅给予两秒排空宽限，随后取消未完成 HTTP 请求；普通 timeout=0 上报仍按配置等待，但不能锁死管理恢复。标准媒体 `file` 使用唯一内容 ID，避免不同图片同名后无法按收到的段直接回发。对应回归在 `test_standard_actions.py` 和 `test_transport_conformance.py`。

v0.9.0 发布验证（2026-11-04）：277 项 Bot 回归及 45 项相关 helper 回归通过，源码 compileall、WebUI JavaScript 语法、文档链接及 27 项 vendor 哈希通过。两路独立 CR 的原生发送关闭竞态、HTTP 停机阻塞及同名媒体回发问题已修复并新增回归。原生 C++ 路径编译测试由 Windows CI 验证；本机未安装 MSVC/vswhere，未进行真实微信注入或发送。CI、正式附件和标签状态以发布页面为准，发布不会自动替换实例。

HTTP 快速回复也必须创建服务所有的受保护任务；关闭上报连接只取消等待，服务仍在释放 helper 前等待真实发送结束。不要只为 `_async` / `_rate_limited` 追踪原生任务。

## v0.9.1 引用故障修复（2026-11-04，实机复验仍待进行）

实机证据：仅反向 WS、无 HTTP API 节点时，v0.9.0 将未监听的 legacy OneBot 端口写入图片 URL；本地图片已存在，接收端连接拒绝。当前源码 `_network_public_base_url` 改为真实 HTTP 节点或主 HTTP 应用的账号隔离路由，`webui/server.py` 提供无管理 Cookie 的只读媒体下载。`media.refresh_payload` 对账号内经过索引和文件哈希验证的旧链接更新地址，服务层重建 raw_message。新增真实 socket/HTTP 测试，禁止仅凭配置或 healthz 认定媒体恢复。

转发解析补充明确 XML 容器形式，并将缺记录、XML/结构错误和超限分开诊断。`rich_content.py` 保留脱敏失败类别；已完整的引用快照不被后续缺正文卡片覆盖。旧实机占位未保留原始 XML，无法据此确认具体失败类别。无缓存且无嵌入记录的历史转发仍无已验证的原生补取路径，不得声称所有旧引用已修复。必要时使用新诊断复现，不提交原始聊天 XML、CDN 凭据或账号路径。

运行实例的临时恢复可启用实际 loopback HTTP API 端点，热更新不要求重新注入。源码修复必须构建并切换实例版本后生效；未经用户明确请求不发布 Release，不把 Mock/HTTP 通过写成真实引用联调通过。

本轮源码验证：328 项 Bot 回归通过，1 项符号链接隔离测试因 Windows 权限跳过；67 项相关 helper 回归、源码编译、文档链接及 27 项 vendor 哈希通过。独立媒体 CR 发现同步哈希阻塞事件循环，已改为读接口工作线程刷新及整批历史共享校验。正式发布版本仍需核对远端，不要把本记录当成已部署或已发布证明。

用户确认暂不修改运行实例配置。后续加强 `test_media_delivery.py`：真实反向 WS 接收引用事件并调用 get_msg，再用返回 URL 经真实 HTTP 下载原图片；同时开启仅 @ 过滤、保留未触发原图，并验证旧 6700 URL 刷新。不需要配置 OneBot HTTP 节点。此验证使用 Mock 微信输入，仍不是实机微信/AstrBot 联调。

## v0.9.2 转发发送转换（2026-11-04 发布基线）

用户明确要求在 Bot 侧将 send_group_forward_msg 转成普通消息发送，不再依赖 AstrBot 的长文阈值规避。当前新增群聊/私聊扩展处理，protocol/outbound_forward.py 有界展开自定义、嵌套、消息 ID 及已存转发 ID 节点。service.py 在所有节点校验/媒体准备后串行发送，返回 normal_messages/native_forward=false、实际普通消息 IDs 和已接受分段数，不伪造原生 forward_id；不支持内容与超限不静默丢弃。见兼容说明中的具体额度和参数。

所有普通发送和转发转换都以受保护的服务任务运行，共享账号发送锁；关闭时排空原生工作。DriverError/DriverUnsupported 和发送后存储失败保留已发送进度，避免后端误以为整条未发送。输入预检失败没有原生发送。原生引用和原生转发卡片仍不支持；历史审计与 v0.9.1 的“转发发送不支持”指当时实现，不可覆盖当前明确转换模式。

本轮仅修改 Bot 网关，不修改 AstrBot/Launcher 配置、不做真实微信发送。验证后推送，发布仍需用户明确确认。

CR 追加修复：Driver.validate_media 提供发送前检查钩子，Helper 复用纯文件格式校验（含 WAV 编码依赖可用性），无原生调用；媒体数量在解析前检查、字节额度逐项传入 MediaStore.resolve_input，不修改共享实例的 max_bytes。空文本计入访问额度；发送过程中的非 Driver 异常同样保留进度。

v0.9.2 验证记录：新增转发相关 89 项回归及 5 项独立媒体额度回归；全量 422 项通过，1 项符号链接权限相关测试跳过。CR 的四项 P2 已修复并复审无剩余发现；覆盖 Helper 纯格式检查、同链/跨链剩余额度、额外异常进度及空段工作预算。所有发送测试均为离线 Mock/纯文件校验，没有真实微信发送。

2026-11-04 用户已明确授权发布 v0.9.2。功能提交为 `a901fcf`，对应 [Windows CI](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/41804657088) 已通过。发布包由最终发布提交构建，包含此次文档状态更新；正式状态以 [v0.9.2 Release](https://github.com/PorkLittleHamburger/wechatbot/releases/tag/v0.9.2)、标签目标及附件摘要为准。发布不会替换运行实例或修改外部后端配置，真实微信分段发送仍待升级后验证。

## v0.9.3 桥接恢复控制器（2026-11-04 发布基线）

2026-11-04 与独立 Launcher 项目核对：旧 worker 固定 `start_runtime=False`，实例恢复只启动管理服务；所选版本内的 worker 也不会随 Launcher 本体升级自动更新。修复需两侧配合，不能在 Bot 猜测所有管理启动都要求原生注入。

`webui/bridge_resume.py` 统一启动恢复与显式启停，`app.state.bridge_resume` 暴露能力版本 1 和状态快照；WebUI `/api/status`/状态流附带 `bridgeResume`。目标已登录立即恢复，未登录持续只读等待；`RuntimeManager.start_account` 重新扫描 PID，并用 `AccountNotLoggedInError` 表示启动前目标离线，其他异常明确失败而非无限重试。`startup.json` 新增 `desired`，兼容旧 `last_account`；手动停止持久化 stopped，正常关闭只排空任务不清意图。控制器自有任务保护 HTTP 等待取消后的原生初始化，停止/关闭必须排空，不得恢复旧的取消 await 即释放 helper 的方式。

Launcher 负责明确恢复设置/启动参数、所选 worker 能力握手及状态显示，不新增账号扫描。旧 worker 缺能力须提示升级/重新导入，不能只追加被忽略的参数或误报已恢复。集成契约、错误策略与验证边界见 [桥接自动恢复](bridge-resume.md)。本轮没有部署实际实例、修改运行配置或进行真实微信注入/发送；发布状态以远端标签和 Release 为准。

本轮验证：456 项 Bot 测试通过，1 项 Windows 符号链接权限测试跳过；编译、WebUI JavaScript 语法及 5 项前端恢复状态/目标选择场景通过，58 项 Markdown 相对链接与 27 项 vendor 哈希通过。独立 CR 的两项 P2 已修复并复审无剩余发现：失败切换账号后停止原持久化目标仍保存 stopped；关闭等待被重复取消时仍排空线程初始化再释放 runtime。Launcher 侧已用临时导入的验证包与实际嵌入 Python 运行 Mock 子进程，验证 running/stopped/no_target，并模拟未登录到登录和等待期间停止。此为离线跨仓库集成证据，不等同真实微信或 Windows 注销/登录验收；最终远端 CI 与提交状态需按当前分支核对。

2026-11-04 用户已明确授权发布新 Release。v0.9.3 功能提交为 `b908d4c`，[Windows CI](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/41818859960) 全部通过；最终发布提交另包含 README 和本文的发布状态更新，发布包由最终提交构建。正式状态、目标和附件摘要以 [v0.9.3 Release](https://github.com/PorkLittleHamburger/wechatbot/releases/tag/v0.9.3) 为准。配套 Launcher 需具备恢复协议 v1 的 worker，更新后重新导入兼容 Bot 包并切换实例，开启三项自启/恢复设置；仅更新 Launcher 本体不会改写旧版本目录。发布不替换运行实例，真实微信和 Windows 登录自启仍需升级后安排验收。

## v0.9.4 微信 4.2 适配（2026-11-11）

用户确认此次先发布上游已支持的 4.2.0.11 / 4.2.0.12 指定构建，4.2.0.52 不在本次适配范围。helper 基线更新至 `119acdf9d74654c6ebb8cded2c6841b85af75867`，消息 DLL 从合并后的源码以 MSVC x64 重编译；其他两个 DLL 使用固定上游二进制。`native/compatibility.json`、`native_profiles.py` 与 `core_version.h` 保持对应的精确指纹及独立地址映射。不要把 4.2 系列版本字符串当作通配支持。

三方合并保留守护导入/日志、完整 DLL 路径检查、发送快照权限、本地扩展路径、@ 标签空格、富引用及递归转发。引用解析必须先走 `replies.py`；上游 `quoted.py` 仅补充显示元数据，不能提前返回吞掉图片和转发正文。4.2 非好友名称沿用上游独立映射，保留 4.1.15.56 布局和先校验原始名称再去空白的规则。

`HelperDriver.capabilities` 与实际后端取交集，并映射网关特有的好友申请/本地转发解析名称；4.2 不宣称全量联系人扫描、表情发送。语音/视频 pending 监控不重复上报；最终仍按现有网关占位规则交付。新增上游视频、原生引用、朋友圈、公众号及本机防撤回没有新增 OneBot 映射，不启动独立应用插件。

文件快照保留完整文件名；当生成的路径超过 Windows UTF-16 MAX_PATH 时补充本地扩展前缀，清理仍先核对拥有的目录边界。便携构建排除 `.build`、obj/lib/exp/pdb，并记录全部三个原生 DLL。vendor 清单扩展为完整分发快照的 341 项文件（不含清单本身）；文本固定 LF，更新后必须重算。

验证记录：466 项 Bot 回归通过、1 项 Windows 符号链接权限测试跳过；1367 项 helper 离线回归通过、2 项平台/环境测试跳过（完整独立 helper 测试需另装其 `qrcode==8.2` 开发依赖，网关运行无需该依赖）。完整 helper 回归后，对最终长路径 UTF-16 阈值调整又运行 10 项文件回归通过。MSVC 消息 DLL 重编译、原生路径及 V3–V7 接收队列边界测试通过；compileall、64 份 Markdown 相对链接和完整 vendor 哈希通过。CI 增加网关实际相关 helper 测试。最初探索回归中发现的引用优先级、临时状态重复、长文件名路径和旧测试分隔符预期均已处理。

本次只更新 Bot，不替换本机实例、不改 Launcher/外部后端、不注入当前微信或发送真实消息。用户已明确授权发布；正式提交、CI、标签、附件和 Latest 状态以 [v0.9.4 Release](https://github.com/PorkLittleHamburger/wechatbot/releases/tag/v0.9.4) 为准。匹配指纹构建上的实际启停、群 @、图片及引用图片仍待升级后验收。版本、指纹和升级步骤见 [微信版本与能力](wechat-versions.md)。

## 发布包审计与 WebView2 故障交接（2026-11-12）

用户反馈 v0.9.4 无法在 Launcher 内下载，要求发布替代包并撤回旧 Release。本轮对比 v0.9.3/v0.9.4：附件命名均符合旧规则，两个 ZIP 均通过实际 Launcher 的路径检查；v0.9.4 通过公开目录识别、实际下载、解压、导入及 1514 项运行文件校验。本轮环境未复现用户侧失败，不能据此断言原故障由文件名、ZIP 损坏或兼容清单缺失造成。

本轮将既有发布约定固化到 scripts/package_portable.py：自动生成标准版本附件名、同名单层根目录、正斜杠 ZIP 路径及无 BOM 的同名 SHA-256 文件；PowerShell 构建直接调用该入口。补充路径、重复项和摘要回归。仍为 Launcher 支持的 legacy portable 输入，由 Launcher 注入自身 worker 并生成 VERSION_MANIFEST.json，不在 Bot 仓库混入 Launcher 源码。微信适配和网关业务代码保持 v0.9.4 范围。

用户随后澄清下载已恢复，之前可能是网络问题；实际问题为打开 WebUI 时 Launcher 报 WebView2 0x80010108。当前 Bot 实例的首页和 healthz 均返回 HTTP 200。已将窗口缺陷交由独立 Launcher 会话诊断修复；不再按包损坏处理，不发布无关的 Bot 替代版、不撤下有效 v0.9.4。打包规则改进另行验证推送，不改变 Bot 版本或运行实例。467 项 Bot 回归通过、1 项权限测试跳过。

## v0.10.0 wechatbot_api 驱动（2026-11-13，发布基线）

背景：用户的微信已升级到 **4.2.0.52**，上游 helper 没有该构建的适配（修改版本号不会放行）。独立框架仓库 `wechatbot-api` 已完成 `0.1.0.dev14` 快照，仅适配这一精确构建；本轮在 Bot 侧接入其公开 SDK，不修改框架代码，也不改动 Launcher/AstrBot/运行实例。

代码入口：

- `src/wechat_onebot/driver/sdk.py`：`SdkDriver(Driver)`。`start` 用框架只读 `discover()` 选账号并 `Session.connect(receive=True)`；独立消费线程把不可变 `Event` 转成 `IncomingMessage`（thaw 冻结结构后复用 `protocol/rich_content.py`）；入站 pending 媒体经 `request_media` 下载、从 `MediaHandle` 导出到账号 `media/inbound/` 并 `release()`；`stop` 排空（`SHUTDOWN_PENDING` → `wait_closed`），不强制卸载。
- 发送只映射文本、原生群 `@`、图片、文件、语音与视频；`completed` 才返回成功，`accepted`/`unknown` 一律抛“结果未知、不要重发”，绝不伪造成功。能力集合不含表情、撤回、好友申请与群管理，对应动作返回 1404。
- 配置：`driver.type` 新增 `sdk`；`sdk_data_root`（默认账号目录 `sdk/`）、`sdk_allow_unverified`（默认 false）、`sdk_media_timeout`（默认 30 秒）。`bridge.discovery` 新增 `discover_sdk_accounts`/`discover_accounts`，CLI、Runtime、WebUI、桥接恢复控制器按 `driver.type` 分派；helper 路径与既有测试契约不变。
- 依赖：固定 `vendor/wechatbot_api/wechatbot_api-0.1.0.dev14-py3-none-win_amd64.whl`（sha256 `408a0430…b523`，bridge 10/ABI 1），`VENDORED.json` 记录来源与指纹；`build_portable.py` 安装该 wheel 并导入校验。SDK 要求 Pillow 13.0.0，运行锁与 `pyproject` 已同步到 `Pillow>=13,<14`。

能力边界（来自框架公开契约，不是网关猜测）：支持 4.2.0.52 的文本收发、1–2 人真实缓存名称群 `@`、PNG/普通文件/语音/视频发送、四类私聊媒体与群 PNG/文件/视频/群 CDN 语音接收、私聊引用文本/引用原图/两层嵌套转发、只读联系人/群/成员缓存；不支持表情发送、撤回、好友申请、群管理、原生合并转发卡片与出站原生引用。群富图片在框架内仍为开发 opt-in，网关默认不承诺；入站语音是解码后的 WAV。

验证：496 项 Bot 回归通过、1 项 Windows 符号链接权限测试跳过；`compileall`、便携包构建（含包内 `wechatbot_api` 导入）通过。新增 `tests/test_sdk_driver.py` 覆盖事件/引用/富内容映射、媒体导出与句柄释放、异常媒体占位、发送结果四态、只读查询、目标选择、启停与配置校验；测试用假 SDK，不注入微信。另用打包后的 Python 执行了只读 `discover()`：识别到本机两个已登录的 4.2.0.52 进程（核心指纹 `d733489e…`，`bridge_supported=true`），Bot 的 `discover_sdk_accounts` 也正确映射；该步骤不注入、不发送、不创建会话。

2026-11-13 实机验收（用户授权，仅连接/接收/查询，不发送）：使用**临时配置与临时数据目录**，未读取或修改运行实例的 `config.json`。直接驱动连接耗时 0.87 秒，`get_status` 报 `connected` / `4.2.0.52` / watchdog `ready`，读回好友 87、群 4、首个群成员 9，20 秒窗口内无入站事件，停止 0.50 秒；守护日志为 `WATCHDOG_READY` → `NORMAL_STOP`，微信进程保持存活。随后用完整 Bot 服务（临时 WebUI/OneBot 端口）做端到端：账号扫描命中已配置账号、`/start` 返回 `{"ok":true}`、`/api/status` 显示 `driver=sdk` 且 `online=true`、`/stop` 正常，再次确认 `NORMAL_STOP` 且无残留进程/端口。入站事件与发送链路仍未验证。

未完成/未验证：**入站消息与发送链路尚未用真实微信消息验证**（验收窗口内没有新消息，且未获发送授权）。待实机验收：群 `@`、图片/文件/语音/视频收发、私聊引用（含引用图片）、转发接收；`sdk` 驱动逐条消息串行等待媒体（受 `sdk_media_timeout` 限制），高并发长媒体会延迟后续事件，暂未改为并发流水线；通知类系统消息与撤回尚无 OneBot 映射；`get_stranger_info` 仍依赖事件/缓存观测，非任意陌生人查询。

## v0.10.0 发布结果（2026-11-13）

该版本已因启动失败与昵称问题被 v0.10.2 取代，Release 已按用户要求撤下；标签 `v0.10.0` 保留供历史引用。

- 标注标签 `v0.10.0` 指向发布提交 `347f519`；`main` 与标签一致，发布包由该提交构建。
- [Bot checks #43369866824](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43369866824) 成功；CI 新增 vendored wheel 的哈希核对与安装步骤。
- 附件：`wechat-onebot-0.10.0-windows-x64.zip`（60,042,666 字节，SHA-256 `d3820a3dc3a89177c23719ed6d4a33f69c9994dc67ce3b4de5bfbb59973f5158`）与同名 `.sha256`；正式发布并设为 Latest，公开重新下载的摘要与本地一致。
- 用独立 Launcher 的 `VersionStore.import_archive` 在临时根目录执行真实导入：识别版本 `0.10.0`，生成并校验 1,567 个文件，注入 worker、`wechatbot_api` 包与 `wca_bridge.dll` 均存在，状态 compatible；未使用或修改任何实例目录。
- 未在 Launcher 内实际启动该版本；发布不会替换运行实例，入站/发送实机验收仍待真实消息。
- 版本命名约定（自下一版本起）：Release 标题与文档中的版本称呼统一为 `WeChatBot vX.Y.Z`；Git 标签保持 `vX.Y.Z`、资产名保持 `wechat-onebot-<version>-windows-x64.zip`，不添加 `WeChatBot ` 前缀，以免破坏 Launcher 的版本发现与下载。

## v0.10.1 驱动自动回退（2026-11-13，发布基线）

用户反馈：Launcher 实例升级到 v0.10.0 后，微信 4.2.0.52 已登录，启动桥接仍提示“目标账号未登录，请在 PC 微信中登录”。只读核对实例配置确认根因：`driver.type` 仍是 `helper`（旧配置），`wechat_pid` 已过期（28904）；当前微信进程 31004 的账号与 4.2.0.52 构建可被 SDK 正确识别。v0.10.0 的 helper 分支只做 helper 发现，找不到就把账号报告为未登录，不会尝试 sdk。

修复：`RuntimeManager.start_account` 先按配置驱动发现，找不到目标账号时自动尝试另一真实驱动；成功后用原子写入把 `driver.type`、账号与 PID 写回配置（失败保留原值），并更新 manager 配置。WebUI 账号扫描在配置驱动返回空时回退到另一驱动，账号选择会持久化实际驱动；开机自启恢复在等待登录前也检查 SDK。SDK 启动错误按 `SESSION_BUSY` / `ACCESS_DENIED` / `UNSUPPORTED_BUILD` / `ABI_MISMATCH` 给出可操作提示。框架会话确认：切换前必须确保旧 helper bridge 已卸载（同一进程只允许一个 SDK owner），提权 worker 可用，但需与微信同用户/同会话；仅在连接成功后持久化驱动类型。

验证：新增 6 项回归（回退成功并持久化、配置驱动优先、反向回退、失败错误含构建信息、自启回退、WebUI 选择回退），全量 **502 项通过、1 项 Windows 符号链接权限测试跳过**。用临时配置复现实例场景（helper + 过期 PID + 4.2.0.52）：日志“已按微信构建自动切换驱动: helper -> sdk”，连接成功（`SdkDriver`、`4.2.0.52`、watchdog `ready`，PID 修正为 31004），配置文件写回 `type=sdk`/`pid=31004`；停止后微信进程存活。未发送消息，未修改用户实例配置或运行进程。

边界：自动回退只在“目标账号已被另一驱动验证”时发生；两驱动都找不到时仍失败，并列出检测到的构建与错误码。正在运行的 v0.10.0 服务不会热切换，需导入 v0.10.1 并切换实例运行版本后重启。2026-11-13 用户明确授权发布 **WeChatBot v0.10.1**（标签 `v0.10.1`，资产 `wechat-onebot-0.10.1-windows-x64.zip` + 同名 `.sha256`）；正式状态以 Release 页面为准，发布不会替换运行实例。

发布结果（2026-11-13）：标注标签 `v0.10.1` 指向 `e059776`；[Bot checks #43373899369](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43373899369) 成功；附件 `wechat-onebot-0.10.1-windows-x64.zip`（60,046,846 字节，SHA-256 `331e45a53296cc9a6a40c8e97418d67751210dad2171f63a8a0e77f03040e55b`）与同名 `.sha256`；正式发布并设为 Latest，标题按新约定为 `WeChatBot v0.10.1`，公开重新下载的摘要一致。Launcher `VersionStore.import_archive` 在临时根目录导入成功：识别 0.10.1、校验 1,567 个文件、worker/SDK/DLL 齐全、状态 compatible。未在 Launcher 内启动该版本，未替换运行实例。该版本因启动失败回退与昵称问题已由 v0.10.2 取代，Release 已按用户要求撤下；标签 `v0.10.1` 保留供历史引用。

## v0.10.2 启动失败回退与昵称修复（2026-11-13，发布基线）

v0.10.1 实机反馈两个问题：启动桥接仍可能报“helper 启动失败: 目标进程登录的不是绑定的机器人账号，等待正确账号登录”；账号信息显示 wxid/数字 ID 而不是昵称“墨染青花”。

根因一：v0.10.1 的自动回退只覆盖“配置驱动发现不到账号”。若 helper 的发现阶段碰巧命中（或运行时复用了早前创建的 helper runtime），原生启动仍会执行 helper 的账号校验；该账号校验对 4.2.0.52 不适用，直接拒绝。启动失败后没有清理 runtime，也没有换用 SDK。

根因二：SDK 的 `Account.wechat_name` 在 discovery 中为 `None`，而 `discover_sdk_accounts`、WebUI 扫描/选择与持久化直接使用空字符串，覆盖了配置里已有的“墨染青花”；`get_login_info` 同样只读该字段，导致 OneBot/WebUI 只能显示 wxid 或数字 ID。

修复：`start_account` 在选定驱动启动失败时关闭并移除该 runtime，用另一真实驱动重新发现并重试一次，成功后只持久化实际可用的驱动；昵称改为从 SDK 只读联系人缓存 `Client.contact(wxid)` 补全，`get_login_info` 的回退链为“discovery 名称 → 联系人缓存 → 配置昵称”；扫描与选择只在拿到非空昵称时更新，切换账号时不会把旧昵称带到新账号。开机自启恢复的 SDK 探测改为等待 2 秒后、每 5 秒最多一次，避免恢复循环每毫秒扫描进程。

验证：新增 5 项回归；全量 **507 项通过、1 项 Windows 符号链接权限测试跳过**。临时配置复现用户场景（helper + 过期 PID + 空昵称 + 4.2.0.52）：自动切换 `helper -> sdk`，`get_login_info` 返回昵称“墨染青花”，好友 87、群 6，配置写回 `type=sdk`、当前 PID 与昵称；停止后微信进程存活。未发送消息，未修改用户实例配置。

2026-11-13 用户明确授权发布 **WeChatBot v0.10.2**，并要求撤下 v0.10.0 与 v0.10.1 两个不可用的 Release：删除两个 Release 及其附件，保留 Git 标签供历史引用；v0.10.2 为唯一受支持的下载版本。发布不替换运行实例。

发布结果（2026-11-13）：标注标签 `v0.10.2` 指向 `0851dfa`；[Bot checks #43376900152](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43376900152) 成功；附件 `wechat-onebot-0.10.2-windows-x64.zip`（60,049,409 字节，SHA-256 `2d7cbd7109b940502e629b62e8fe8fb2d014640d503c3b2254e80f46e9014feb`）与同名 `.sha256`；正式发布并设为 Latest，标题为 `WeChatBot v0.10.2`，公开重新下载的摘要一致。Launcher `VersionStore.import_archive` 在临时根目录导入成功：识别 0.10.2、校验 1,567 个文件、worker/SDK/DLL 齐全、状态 compatible。v0.10.0 与 v0.10.1 的 Release 及附件已删除（`releases/tags/*` 返回 404），对应标签仍保留。未在 Launcher 内启动该版本，未替换运行实例。

## v0.10.3 Launcher 驱动契约（2026-11-13，发布基线）

用户升级到 0.10.2 后，启动实例报「实例驱动与清单不一致」和「备份缺少有效的实例配置」。

根因：Launcher 0.8.11 的 `manager.check_config` 要求 `config.json` 的 `driver.type` 等于 `instance.json` 的 `mode`（`launcher/manager.py:188`）。Bot 0.10.1/0.10.2 的自动回退把 `driver.type` 持久化成 `sdk`，而实例 `mode` 仍是 `helper`，启动前即被拒绝；`manager.py:67/134` 的实例元数据只接受 `mock`/`helper`，`restore_service.py:42` 的恢复校验同样只接受这两种，且恢复时用 Launcher 侧的 `AppConfig.load` 解析备份 config，遇到 `driver.type=sdk` 失败，表现为「备份缺少有效的实例配置」（即便解析成功也会以「备份驱动无效」拒绝）。

修复（Bot 侧）：自动回退只保留在本次运行时的内存中，`_remember_driver` 不再写 `driver.type`，只持久化账号、PID 与昵称；WebUI 账号选择也不再改类型。`config.driver.type` 始终等于 Launcher 的 mode；`helper` 配置下 4.2.0.52 仍由 sdk 驱动运行。已与 `bot-launcher` 会话沟通 Launcher 侧方案。

兼容：0.10.1/0.10.2 已改写过的实例（`config.driver.type=sdk`、`mode=helper`）无法由 Bot 自行修复——Launcher 在启动 Bot 之前就会拒绝。需要 Launcher 增加 `sdk` mode 并把 mode 迁移为 sdk，或把 `config.driver.type` 改回 `helper`（0.10.3 起不会再被改写）。备份恢复同理。

验证：507 项回归通过、1 项 Windows 符号链接权限测试跳过；临时配置验证 `helper` 配置下自动使用 sdk、`driver.type` 保持 `helper`、PID 与昵称正确写回。未修改用户实例配置或运行进程。

2026-11-13 用户明确确认发布顺序：**Launcher v0.8.12 先发**（`bot-launcher` 仓：窄视图读取实例配置、helper 实例兼容 `driver.type ∈ {helper, sdk}`、恢复时归一为 mode；不改 UI/Rust/数据迁移），**WeChatBot v0.10.3 随后**。Launcher 修复在 Bot 启动前解除「实例驱动与清单不一致」与备份恢复失败；Bot 0.10.3 防止未来再次改写 `driver.type`。两个 Release 均不替换运行实例。

发布结果（2026-11-13）：Launcher **v0.8.12** 先发布（annotated tag `8f52d27` → `eb73581`，[CI #43379930218](https://github.com/PorkLittleHamburger/wechatbot-launcher/actions/runs/43379930218) 成功；安装器 286,406,076 B SHA-256 `7d257f91c25b06ba472025a333ded6d529f59b9fb4e5277e169b764cd5ba31e7`，运行包 59,275,291 B SHA-256 `c0d200d74382a1e60e7f1118181179e99428f015db989a08adb6c25ff8e44012`；Latest；隔离包内验证 helper 实例接受 sdk 配置、含 sdk 的备份恢复归一为 helper、公网回下载与 0.8.11 更新模块实测通过）。随后 **WeChatBot v0.10.3** 发布：annotated tag `v0.10.3` → `ffaab3f`，[Bot checks #43380091740](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43380091740) 成功；附件 `wechat-onebot-0.10.3-windows-x64.zip` 60,050,817 B，SHA-256 `15794169197a9fc056f802bc369fc29e8c41461bb206490a59d450a27cd01c75`，与同名 `.sha256` 一同设为 Latest；公网重新下载摘要一致，Launcher `VersionStore.import_archive` 导入 1,568 个文件、状态 compatible。当前实例 `mode=helper` + `config.driver.type=sdk` 可直接启动，无需手改配置。

## v0.11.0 语音格式兼容（2026-11-13，发布基线）

用户先询问原生语音发送能力，随后要求完整兼容。原实现只接受 SILK v3 与 WAV（WAV 还需 `silk-python`，且发布包未内置），MP3/AMR/AAC 等被明确拒绝。微信语音条固定为腾讯 SILK v3，其他编码不能直接透传。

实现：新增 `src/wechat_onebot/audio.py`，在 `service._prepare_segments` 的 `record` 分支统一归一化——SILK（腾讯头/标准头）校验后透传并归一为腾讯封装；其他格式用内置 `imageio-ffmpeg` 解码为 24 kHz 单声道 PCM16，再用 `silk-python==0.2.9` 编码为腾讯 SILK v3。按输入内容哈希缓存，重复发送不重复转码。限制：60 秒 / 20 MiB、单声道、采样率 24 kHz；解码/编码/超限失败均返回 1400 并给出中文原因，不退化为文件消息。`silk-python` 已加入 `requirements-runtime.lock` 与 `pyproject.toml`，便携包构建会自动安装；ffmpeg 原本已随 `imageio-ffmpeg` 分发。两个驱动都只接收最终 SILK，因此框架与 Launcher 无需改动。

支持格式：SILK v3、WAV、MP3、AAC/M4A、OGG/Opus、AMR-NB/WB、FLAC（以内置 ffmpeg 解码能力为准）。

验证：新增 `tests/test_voice_compat.py`（8 项：SILK 透传/标准头归一、WAV/MP3 转 SILK、损坏/超限/超时长/缺解码器拒绝）与 `tests/test_service.py` 的端到端 `record` 用例；移除已失效的“helper WAV 编码器缺失”用例。全量 **515 项通过、1 项 Windows 符号链接权限测试跳过**；机器上 MP3/WAV 均成功转出腾讯 SILK。未向真实微信发送语音，仍需实机收件验收（听感、时长、群/私聊一致性）。

安全与边界：ffmpeg 只读取已由 `media.resolve_input` 落到本地的文件，`-nostdin`、单线程、45 秒超时、输出字节与时长上限；不解析网络协议。转码有 CPU 延迟（通常几百毫秒），首发明显、缓存后无感；SILK 重编码不可避免会改变音质。

2026-11-13 用户明确确认发布 **WeChatBot v0.11.0**（标签 `v0.11.0`，资产 `wechat-onebot-0.11.0-windows-x64.zip` + 同名 `.sha256`）。本版本只改 Bot，框架与 Launcher 无需配合或重新发布；发布不替换运行实例。真实微信语音收件（听感、时长、私聊/群）仍需升级后验收。

发布结果（2026-11-13）：annotated tag `v0.11.0` → `949b21d`；[Bot checks #43392184037](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43392184037) 成功；附件 `wechat-onebot-0.11.0-windows-x64.zip` 60,395,830 B，SHA-256 `9fb4f7d7dbcb8dc4cefea12a47298b592a092d9000a30a406b5ac20fa58f0465`，与同名 `.sha256` 一同设为 Latest；公网重新下载摘要一致；Launcher `VersionStore.import_archive` 导入 1,586 个文件、状态 compatible。包内 Python 实测 MP3→Tencent SILK（300 ms）。

## v0.11.1 OneBot v11 消息流一致性修复（2026-11-13，发布基线）

用户要求按官方 OneBot v11 检查对外 API（尤其消息流），确认后要求修复发现的问题。本轮只改 Bot，框架与 Launcher 无需配合。

1. **事件顺序**：`OneBotService.start` 新增每账号 `asyncio.Queue` + 单 worker（`service.py`），驱动只入队；`_on_driver_event` 串行执行，避免成员/好友补全的 `await` 让后到消息先入库/上报。`_enqueue_event` 等待本账号队列处理完成，Mock 测试与真实驱动均保持原语义。
2. **快速操作**：`_handle_quick_operation` 逐字段处理；同响应中的 `reply` 不再因 `delete`/`kick`/`ban`/`approve` 被整条拒绝，不支持项记录 warning。
3. **helper 入站语音/视频**：helper 下载完成（`voice_status=ready`/`video_status=ready` 且路径存在）时映射标准 `record`/`video` 段，不再固定为 `[语音]`/`[视频]` 文本；pending/failed 仍显式占位。
4. **私聊撤回**：`friend_recall` 在私聊对端或原消息可用时复用原 `message_id`；helper 的 recall 载荷没有 sender 时用私聊对端补上。群撤回因无可靠 `operator_id` 继续抑制并记录。
5. **媒体读取**：新增标准 `get_image`/`get_record`（`onebot/actions.py`、`media.received_path`、`audio.export_format`）。`get_record` 可把收到的 SILK/WAV 等转为 mp3/amr/wma/m4a/spx/ogg/wav/flac，按内容哈希缓存；`file` 只接受当前账号已注册的媒体 ID/文件名，不接受任意路径或 URL。
6. **自发消息引用**：驱动 `take_native_message_ids()` 返回 native ID（sdk 使用 `SendResult.native_message_id`；helper 原生发送只返回成功布尔值，因此保持引用快照降级），发送快照登记后，后续引用该消息解析到真实发送 ID，而不新建 time=0 引用快照。

验证：全量 **523 passed / 1 skipped**；新增事件顺序、快速操作独立、helper 语音/视频与撤回转换、`get_image`/`get_record`（含 SILK→ogg、WAV→mp3/amr）、native ID 引用测试。仍为能力子集：群撤回、群成员进退、群管理、出站 `reply`、`@全体`、原生合并转发卡片保持显式失败/抑制；helper 原生发送只返回成功布尔值，没有 native ID 时仍走引用快照降级。未在真实微信上逐项验收，发布需用户确认。

发布结果（2026-11-13）：用户要求先做 CR 再发布。CR 移除了 helper 端猜测性的 native-ID 兜底、重复的私聊撤回兜底，统一了 audio 导入与收到媒体校验；随后发布 **WeChatBot v0.11.1**。annotated tag `v0.11.1` → `bad397b`；[Bot checks #43397712069](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43397712069) 成功；附件 `wechat-onebot-0.11.1-windows-x64.zip` 60,400,590 B，SHA-256 `25b515c452c46d6f0e206b174dde96a08fbd43040006c4965ba94b150916b83d`，与同名 `.sha256` 一同设为 Latest；公网重新下载摘要一致；Launcher `VersionStore.import_archive` 导入 1,586 个文件、状态 compatible；包内 Python 实测 SILK→MP3 与 WAV→AMR。框架与 Launcher 无需改动。

## v0.11.2 长音频与 music 段发送（2026-11-13，发布基线）

用户报告 AstrBot `astrbot_plugin_music` 发歌时出现三个错误：本地语音 `record` 被 60 秒上限拒绝（1400）、`music` 段不支持（1404）、录音链接同样超 60 秒被拒绝。微信原生语音条上限确实为 60 秒，不能发超长语音。

修复：
1. `audio` 新增 `VoiceTooLong`，区分“超过 60 秒”和真正的解码/编码失败。
2. `service._prepare_segments` 捕获 `VoiceTooLong` 后把 `record` 降级为普通 `file` 消息（保留原音频文件），不再让整条消息失败；日志记录 `语音超过 60 秒，已降级为文件消息`。
3. 新增标准 `music` 段发送（`_prepare_music`）：`custom` 且有 `audio` 时复用 `record` 管线（≤60 秒语音、更长文件）；只有 `url` 时发送“标题 + 链接”文本；只有平台 ID、无可播放地址时明确 1404；封面图忽略。
4. 移除 `music` 的“不支持”判定；转发中遇到只有平台 ID 的 `music` 仍在准备阶段失败。

验证：新增 `tests/test_music_send.py` 4 项（本地超长 WAV、AstrBot 风格 base64 超长 WAV、custom music 短/长音频、url-only 与无地址失败）；全量 **526 passed / 1 skipped**。未在真实 AstrBot + 微信上跑通点歌全流程，仍需实机验收文件消息接收与播放。

边界：超长音频降级为文件是明确的功能降级（不伪造 60 秒语音条、不截断），文件大小仍受媒体上限与驱动文件校验约束；AstrBot 若只发平台 ID 的 `music` 卡片且无可播放 `url/audio`，网关仍会失败。

发布结果（2026-11-13）：用户确认发布 **WeChatBot v0.11.2**。因本机代理中断，提交通过 GitHub Git Data API 写入并重建为远程 commit `a2d71f1`（内容与本地一致），随后正常打 annotated tag 并推送。annotated tag `v0.11.2` → `a2d71f1`；[Bot checks #43407712748](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43407712748) 成功；附件 `wechat-onebot-0.11.2-windows-x64.zip` 60,402,667 B，SHA-256 `46fdab1154b74be45c834721854ff6f44b65ee160aeab412ddd29b92a8c3431c`，与同名 `.sha256` 一同设为 Latest；公网重新下载摘要一致；Launcher `VersionStore.import_archive` 导入 1,586 个文件、状态 compatible；包内 Python 验证 `VoiceTooLong` 可识别 61 秒音频。框架与 Launcher 无需改动。

## v0.11.3 WebSocket 大帧上限（2026-11-13，并入 v0.11.4）

AstrBot 点歌仍报 `Local voice send failed: WebSocket API call timeout`；bot 日志显示反向 WS 收到 **37,986,087 字节** 的单帧，超过网关硬编码的 16 MiB，`websockets` 以 `1009 (message too big)` 关闭连接。原因是 AstrBot 的 aiocqhttp 适配器把本地 `Record` 转成 `base64://` WAV 内联在 `send_private_msg` 请求中，长歌曲的 JSON 帧可达数十 MB；请求在进入 `_prepare_segments` 前就被传输层拒绝。

修复：
1. `WsServerEndpoint` / `WsClientEndpoint` 新增 `maxMessageBytes`，默认 **128 MiB**，允许 1–512 MiB；旧配置省略时使用默认值。
2. 反向 WS 客户端把该值传给 `websockets.connect(max_size=...)`；正向 WS 服务端传给 `uvicorn.Config(ws_max_size=...)`。
3. WebUI 网络端点编辑增加“最大 WS 消息（字节）”，保存/读取与 `to_dict/from_dict` 同步；新建 WebSocket 端点默认 134217728。
4. 文档补充大帧来源、内存风险与调整建议。

验证：新增配置默认/往返/边界测试、反向连接 `max_size` 断言、正向 `ws_max_size` 断言；全量 **527 passed / 1 skipped**。未用真实 AstrBot 复测；即使帧被接受，AstrBot 的 API 超时仍可能受原生上传耗时影响，必要时再压缩长音频或改插件发送模式。

边界：128 MiB 是单帧内存上限，不是可靠性保证；不可信端点不应调高。微信实际文件上传耗时不受此字段控制。

## v0.11.4 长语音分段与降码率（2026-11-13，发布基线）

用户明确要求“怎么来的就怎么出去”：长语音仍要以微信原生语音条发送，码率高就降码率、时间长就分段，不改发文件、不截断。此前 v0.11.2 的逻辑是 `VoiceTooLong → 文件消息`，且 `ensure_silk` 在 20 MiB 输入上限处会提前失败，AstrBot 的 28 MB base64 WAV 根本到不了 60 秒判断。

实现：
1. `audio.py` 新增 `ensure_silk_chunks()`：非 SILK 输入用 ffmpeg 解码为 24 kHz 单声道 PCM（统一降采样/降码率），按 60 秒切 PCM 后用 `pysilk` 编码；SILK 输入按 3000 包（60 秒）切包，不再重编码。单条输出始终 ≤60 秒、腾讯 SILK v3。
2. 分段结果按输入内容哈希缓存在 `media/cache/voice/<hash>.NNN.silk`，重复发送不重复转码。
3. `service._prepare_segments` 的 `record` 分支改为把每个分段 append 成独立 `PreparedSegment("record")`，由 `_send_prepared_message` 依次调用 `driver.send_voice`；一条 OneBot `record` 对应多条微信语音条，API 仍返回一个逻辑 `message_id`，发送快照包含多个 record 段。
4. `music` 的 `custom.audio` 复用同一 `record` 管线，因此长歌曲同样分段语音；只有 URL 的卡片仍发文本链接，平台 ID 卡片仍 1404。
5. 总时长上限 30 分钟；输入文件大小受 `storage.max_media_bytes`（默认 50 MiB）约束；无法解码、超过总时长或媒体上限仍明确 1400。

验证：新增/更新 `tests/test_music_send.py` 5 项（61 秒本地 WAV、125 秒 base64 WAV、61 秒 44.1 kHz 立体声高码率 WAV、music 短/长音频、url-only 与无地址失败），逐段断言腾讯 SILK 头、单段 ≤60 秒、总时长正确；全量 **528 passed / 1 skipped**。未在真实 AstrBot + 微信上验收多段语音的到达顺序与播放体验。

边界：一条请求发送多段语音，AstrBot 若在发送过程中超时，bot 仍会继续把剩余分段发完；客户端可能看到“超时”但微信收到多段语音，重试可能重复。分段上限 30 分钟是保护值。

发布结果（2026-11-13）：用户确认发布 **WeChatBot v0.11.4**（v0.11.3 的 WS 大帧改动并入本版，未单独发布）。annotated tag `v0.11.4` → `5ad3f6c`；[Bot checks #43416923179](https://github.com/PorkLittleHamburger/wechatbot/actions/runs/43416923179) 成功；附件 `wechat-onebot-0.11.4-windows-x64.zip` 60,406,474 B，SHA-256 `4c1fac928fe4d4c6b0447b003703b2396b50dd53476960eecb5884c833b1a7c5`，与同名 `.sha256` 一同设为 Latest；公网重新下载摘要一致；Launcher `VersionStore.import_archive` 导入 1,586 个文件、状态 compatible；包内实测 61 秒 WAV 分为 60s+1s 两条 SILK 语音。框架与 Launcher 无需改动。
