# 开发交接

## 接手前先读

新接手的 AI 必须先读根 [AGENTS.md](../AGENTS.md)、本文件和 [文档索引](README.md)，再核对工作区、分支、远端和实际运行版本。本文是 **2026-11-04** 的维护快照，早期修复基线为 `23f46eb`（v0.8.6），本次发布基线为 v0.9.2（实际发布状态以远端标签和 Release 为准）；日期、测试数量及 Release 不代表未来状态。开发命令面向源码 checkout，便携包不包含完整测试及构建工具。

本仓库只维护 Windows 个人微信 OneBot v11 网关。外部 Launcher、AstrBot 与其插件是独立项目。历史上的 `bot/` 与 `launcher/` 布局已在 `2a16bc3` 收敛为根目录 Bot 工程，不能按旧文档恢复已移出的启动器。

## 代码与数据入口

| 入口 | 责任 |
| --- | --- |
| `cli.py`、`config.py` | CLI、首次配置、路径解析与环境检查 |
| `core/runtime.py` | 账号 Runtime、端点恢复、桥接生命周期 |
| `driver/helper.py`、`driver/mock.py` | 真实原生服务与离线替身 |
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

固定 helper 来源为 `a81c495770b397646ba0661118927c83589c41fc`，其上叠加本地补丁。来源标记不等于未经修改的工作树；读取 [边界说明](vendor-helper.md)、[第三方声明](../THIRD_PARTY_NOTICES.md) 和 `VENDORED_SHA256.json` 后再修改原生部分。

## 当前限制与诊断重点

- 原生适配只承诺指定构建及指纹，不因版本号相同就放行。非好友昵称 LRU 只验证了 4.1.15.56；没有真实资料时可能缺名。
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

## v0.9.3 桥接恢复控制器（源码，待发布）

2026-11-04 与独立 Launcher 项目核对：旧 worker 固定 `start_runtime=False`，实例恢复只启动管理服务；所选版本内的 worker 也不会随 Launcher 本体升级自动更新。修复需两侧配合，不能在 Bot 猜测所有管理启动都要求原生注入。

`webui/bridge_resume.py` 统一启动恢复与显式启停，`app.state.bridge_resume` 暴露能力版本 1 和状态快照；WebUI `/api/status`/状态流附带 `bridgeResume`。目标已登录立即恢复，未登录持续只读等待；`RuntimeManager.start_account` 重新扫描 PID，并用 `AccountNotLoggedInError` 表示启动前目标离线，其他异常明确失败而非无限重试。`startup.json` 新增 `desired`，兼容旧 `last_account`；手动停止持久化 stopped，正常关闭只排空任务不清意图。控制器自有任务保护 HTTP 等待取消后的原生初始化，停止/关闭必须排空，不得恢复旧的取消 await 即释放 helper 的方式。

Launcher 负责明确恢复设置/启动参数、所选 worker 能力握手及状态显示，不新增账号扫描。旧 worker 缺能力须提示升级/重新导入，不能只追加被忽略的参数或误报已恢复。集成契约、错误策略与验证边界见 [桥接自动恢复](bridge-resume.md)。本轮没有部署实际实例、修改运行配置或进行真实微信注入/发送；尚未发布新 Release。

本轮验证：456 项 Bot 测试通过，1 项 Windows 符号链接权限测试跳过；编译、WebUI JavaScript 语法及 5 项前端恢复状态/目标选择场景通过，58 项 Markdown 相对链接与 27 项 vendor 哈希通过。独立 CR 的两项 P2 已修复并复审无剩余发现：失败切换账号后停止原持久化目标仍保存 stopped；关闭等待被重复取消时仍排空线程初始化再释放 runtime。Launcher 侧已用临时导入的验证包与实际嵌入 Python 运行 Mock 子进程，验证 running/stopped/no_target，并模拟未登录到登录和等待期间停止。此为离线跨仓库集成证据，不等同真实微信或 Windows 注销/登录验收；最终远端 CI 与提交状态需按当前分支核对。
