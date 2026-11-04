# 开发交接

## 接手前先读

新接手的 AI 必须先读根 [AGENTS.md](../AGENTS.md)、本文件和 [文档索引](README.md)，再核对工作区、分支、远端和实际运行版本。本文是 **2026-11-04** 对代码基线 `23f46eb`（v0.8.6）的维护快照；日期、测试数量及 Release 不代表未来状态。开发命令面向源码 checkout，便携包不包含完整测试及构建工具。

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
- 合并转发发送、撤回动作、群管理和原生引用回复仍不支持。`reply` 混合发送可省略引用效果；必须区分真实原生 @ 与普通文本标签。
- 某些媒体状态可能只有文本占位、原始路径或空 URL；发送历史也可为 `[sent]` 占位。不能把这些状态当成完整媒体接收或收件回执。
- 群消息未知角色可输出兼容默认值 `member`，不能证明真实群权限；观测成员资料可能省略角色。
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

2026-11-04 后续源码实现（尚未发布）：账号 `onebot.json` 新增布尔值 `groupMentionOnly`，WebUI 通用设置可热更新；仅过滤未原生 @ 到机器人的群消息上报，私聊正常，本地存储保留。新增 helper `replies.py` 解析 appmsg 57，`text_service.py` 在文件分流前交付引用正文。消息库新增 `native_message_ids` 和 `reference_snapshots`，按账号/会话保存原消息索引和未缓存引用的快照。OneBot `reply` 的 `get_msg` 可返回已缓存原图；未缓存媒体只给出不可用说明，快照不进入历史。`tests/test_inbound_replies.py` 覆盖解析、过滤、引用、隔离、重启和 WebUI 热更新；发布时复核并更新此未发布标记。

优先补充外部客户端端到端兼容、长期运行/重连和媒体消费者可达性验证；这些是待验证方向，不是已确认缺陷或交付承诺。尚无覆盖所有原生能力的无人值守真实环境验收。

2026-11-04 富引用扩展（当前源码、未发布）：`forwarded.py` 递归解析链接及嵌套记录，`ForwardedReceiver` 对整个树与引用内容共享原生图片接收流程；`protocol/rich_content.py` 生成 OneBot 内容与有序转发投影，`forward_messages` 表持久化账号隔离的 `get_forward_msg` 数据。未知原作者 ID 为 0，原时间文本不伪造时间戳；旧引用快照允许被更完整元数据补全。`tests/test_rich_quotes.py` 覆盖多个嵌套 XML 形式、共享额度、图片校验流程、引用与转发读取、缓存及重启。前一段“未缓存媒体只给出不可用说明”是第一阶段实现记录，现已扩展为在具有有效元数据时尝试原生接收；缺失/失败仍明确标注。完整契约见 [富消息与引用](rich-messages.md)。

每次功能、配置、部署或验证状态变化后，同步相关指南与本文件；交接时复核远端 README。记录具体行为、代码入口和可重复验证，不写个人部署路径、账号或代理；明确区分已完成、待验证和规划。此次文档审计范围见 [审计记录](documentation-audit.md)。

2026-11-04 v11 规范审计发现未修复差异：`get_forward_msg` 缺少标准 `message`/`node` 数组、CQ 与单对象输入不完整、HTTP/HTTP POST 行为有缺口、撤回 ID 未对应原消息、媒体索引只存内存等。当前不能宣称所有功能对任意严格 v11 应用通用；代码无 AstrBot 包依赖，但部分格式依赖宽松接入端。后续按 [v11 审计](onebot-v11-audit.md) 的优先级修复并新增标准消费者验收，本轮没有改动协议实现。
