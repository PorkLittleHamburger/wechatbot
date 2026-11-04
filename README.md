# WeChatBot

Windows 11 x64 个人微信 OneBot v11 网关。本项目包含协议转换、账号与原生桥接会话、HTTP/WebSocket 网络端点、账号 WebUI、持久化 ID 与消息存储，以及 Windows 便携包构建。

仓库仅包含 Bot 网关开发内容。机器人回复逻辑、AI 行为和业务流程由外部 OneBot v11 客户端实现。

核心消息、通信和事件按 OneBot v11 标准交付，不依赖指定机器人框架。微信不具备的原生能力明确失败；历史查询、能力查询和本地管理是可选扩展，不要求接入端实现。支持范围及标准回归见 [协议兼容说明](docs/onebot-compatibility.md) 和 [v11 审计及修复记录](docs/onebot-v11-audit.md)。

## 功能

- OneBot v11 HTTP API、HTTP 事件上报、正向 WebSocket 与反向 WebSocket。
- 私聊与群聊文本、图片、微信原生群 `@`；收到的文件以标准文本下载链接交付，微信贴图以图片交付。语音、文件及贴图发送能力与可选扩展的区别见协议兼容说明。
- 稳定且持久化的 OneBot 用户、群与消息 ID。
- 本地 WebUI 管理账号扫描、桥接、OneBot 端点、日志和媒体存储。
- Mock 驱动用于离线测试；Helper 驱动通过固定版本原生桥接读写真实微信消息。
- 可构建包含 Python、依赖、原生组件和 WebUI 的 Windows 便携包。

## 系统要求

- 当前验证平台为 Windows 11 x64。
- 源码要求 Python 3.11+ x64，建议使用与 CI 一致的 Python 3.13；便携包内置解释器。
- 真实桥接适配 PC 微信 **4.1.15.13 / 4.1.15.56** 的指定构建，还会核对核心 DLL 指纹；相同版本号不保证兼容。
- 原生桥接需要管理员权限；Mock 模式不需要微信登录。

PC 微信登录必须由用户在微信客户端中完成。WebUI 不提供扫码登录接口。

## 群聊触发与入站引用

v0.9.0 包含以下能力（升级后才应用于运行实例）：

- 在“节点配置 → 通用设置”开启“群聊仅被 @ 时互动”，保存后对当前账号立即生效；默认关闭。只上报原生 @ 到机器人的群消息，私聊保持正常，其他群消息仍在本地保存供引用使用。它不限制外部客户端主动调用发送 API。
- 微信引用聊天（appmsg type 57）转换为 OneBot `reply` 段。`get_msg` 优先返回同账号、同会话的已缓存原消息；引用携带完整内容时，也会解析文本、链接和合并转发，并通过原生接口接收可用图片。
- 合并转发保留作者、原时间文本、消息顺序和嵌套结构；标准客户端可通过 `get_forward_msg` 的 `message` / `node` 数组取得文字、链接与图片。解析有共享的深度、条数和媒体额度，缺失、失败或截断均明确标注。出站微信原生引用回复、原生合并转发卡片发送仍不支持。详见 [富消息与引用](docs/rich-messages.md)。

v0.9.1 修复反向 WS 单独启用时的引用图片 URL，补充转发容器解析与脱敏诊断。原消息未缓存且引用不含记录正文时，仍不能自动拉取历史转发；详见 [引用故障说明](docs/rich-messages.md)。

v0.9.2 支持将 `send_group_forward_msg` / `send_private_forward_msg` 的节点按顺序转成普通消息发送，避免长回复因合并转发能力缺失而整条失败。不会生成原生聊天记录卡片；详见 [兼容说明](docs/onebot-compatibility.md#forward-send-conversion-v092)。

## 下载与升级

从 [最新 Release](https://github.com/PorkLittleHamburger/wechatbot/releases/latest) 下载完整 Windows x64 便携包及同名 `.sha256` 文件，按 [便携包说明](PORTABLE_README.md) 启动。

使用外部 Launcher 时，先停止目标实例，导入新的 Bot 包并切换实例运行版本，再启动验证。发布 Release 不会自动替换本机实例；保留配置、账号数据和媒体目录。Launcher 的安装、更新和界面以其自身文档为准。

v0.9.3 源码（待发布）新增明确的桥接恢复状态与手动停止持久化。自动恢复时，目标账号已登录则启动，未登录则等待；Launcher 还需通过兼容 worker 显式请求恢复，管理进程启动成功不等于桥接恢复。详见 [桥接自动恢复](docs/bridge-resume.md)。

## 快速开始

以下命令从仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-runtime.lock
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
```

启动不连接真实微信的 Mock 服务：

```powershell
.venv\Scripts\python.exe -m wechat_onebot --config config.json run --mock
```

默认 WebUI 为 `http://127.0.0.1:5099/`，实际地址以配置为准。配置不存在时会生成随机 Token；直接使用上述 Python 命令时，从 `config.json` 的 `webui.access_token` 取得首次登录 Token，登录后设置管理密码。全新账号没有 OneBot 网络端点，在“节点配置”中添加并保存。

启动 Helper 单服务：

```powershell
.\start.cmd
```

脚本会按需申请管理员权限。在 WebUI 中选择已登录账号、保存 OneBot 端点，再手动启动微信桥接。真实收发验证步骤见 [原生桥接验证指南](docs/live-preflight.md)。

## 配置与数据

接收消息时会先保存账号隔离的用户资料，补全后再上报 OneBot；微信昵称与各群名片分开记录，空值不覆盖已知名称。`get_stranger_info` 和出站原生 `@` 复用资料缓存，群成员实时资料可用时优先刷新。缓存位于账号的消息数据库内，重启后保留；没有真实资料的用户不会被伪造昵称。`@` 显示标签与正文默认以空格连接，正文自身的换行保持不变。

微信 4.1.15.56 的昵称读取先查好友缓存，再按用户 ID 查询已验证的非好友联系人 LRU 缓存，因此群成员不必先添加好友。该读取不触发网络刷新；微信尚未缓存资料时仍可能缺名，后续收到带昵称的消息或读取到成员资料后会补全。其他版本不套用这一内存布局。

`config.example.json` 是带占位 Token 的字段样例，不应原样复制用于运行。实际 `config.json` 和运行数据仅保留在本机。默认账号网络配置为 `data/accounts/<账号>/onebot.json`，用户/群 ID 映射为同目录的 `ids.sqlite3`，消息 ID、消息及用户资料为 `messages.sqlite3`，媒体为 `data/media/<账号>/`。路径可由 `storage` 配置修改，相对路径按配置文件目录解析。

备份或迁移时应先停止服务，再复制配置、完整账号数据和媒体目录，避免遗漏 SQLite WAL，并核对自定义组件路径。不要提交密码、Token、账号标识、消息内容或运行日志。

端点、心跳、访问令牌和监听地址说明见 [网络配置](docs/network-config.md)。公开 API 与已知限制见 [OneBot 兼容说明](docs/onebot-compatibility.md)。

### 故障排查

启动失败、日志定位、媒体路径与权限的完整步骤见 [故障排查](docs/troubleshooting.md)。下列路径均以当前实例的配置为准。

守护日志在当前 `driver.helper_root` 下的 `logs/bridge_watchdog_startup.log` 和 `logs/bridge_watchdog.log`；提前退出时后者可能尚未生成。DLL 完整路径比较、本地媒体路径校验与微信核心指纹是不同检查，不应通过放宽其中一项绕过其他检查。

## 测试与验证

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q src
```

安装 MSVC x64 开发工具后，可运行 `pwsh -File scripts/test_native_paths.ps1`，编译并执行原生媒体路径校验回归；该检查也在 Windows CI 中执行，不加载微信桥接。

自动化测试默认使用 Mock 和隔离临时目录，不代表真实微信、管理员权限或原生 DLL 已完成联调。Windows CI 执行 Python 测试、编译检查和便携包构建：[Bot checks](https://github.com/PorkLittleHamburger/wechatbot/actions/workflows/bot.yml)。

## 构建便携包

```powershell
pwsh -File .\scripts\build_portable.ps1
```

产物位于 `dist/`，包含 `src`、`vendor`、`webui`、`assets`、内置 Python、启动脚本和文件校验清单。包内说明见 [PORTABLE_README.md](PORTABLE_README.md)。

构建会下载或复用缓存的 CPython，并安装锁定依赖，需要相应网络或缓存。CI 验证构建，但不自动发布 Release。

## 文档与交接

全部指南见 [文档索引](docs/README.md)。新维护者及 AI 接手前须阅读 [AGENTS.md](AGENTS.md) 和 [开发交接](docs/HANDOVER.md)。`vendor/helper` 的插件、控制台和网页说明属于上游独立应用参考，不能视作本网关能力；适用范围见 [helper 边界说明](docs/vendor-helper.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/wechat_onebot/` | 协议、账号桥接、OneBot 网络、运行时与 WebUI 服务 |
| `tests/` | 协议、账号、网络、WebUI、存储与运行时测试 |
| `vendor/helper/` | 固定版本的原生微信桥接与第三方声明 |
| `webui/` | 账号和 OneBot 配置管理页面 |
| `scripts/` | 单服务启动、原生桥接预检和便携包构建 |
| `docs/` | 架构、网络配置、协议兼容及验证说明 |
| `assets/` | 网关图标 |

## 原生组件与使用限制

原生桥接 DLL 位于 `vendor/helper/native/build_text` 与 `vendor/helper/native/build`。DLL 与本地修改的 helper Python 文件的大小和 SHA-256 记录在 `vendor/helper/VENDORED_SHA256.json`；本地修改说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。重新编译方法见 [原生组件说明](vendor/helper/native/README.md)。

helper 使用非商业许可，允许个人学习、研究、测试、娱乐和非商业自用；商业使用、收费服务、SaaS 及营利产品集成不在其许可范围内。详见 [第三方声明](THIRD_PARTY_NOTICES.md) 与 [helper 许可证](vendor/helper/LICENSE)。
