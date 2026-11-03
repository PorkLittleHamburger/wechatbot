# WeChatBot

Windows 11 x64 个人微信 OneBot v11 网关。本项目包含协议转换、账号与原生桥接会话、HTTP/WebSocket 网络端点、账号 WebUI、持久化 ID 与消息存储，以及 Windows 便携包构建。

仓库仅包含 Bot 网关开发内容。机器人回复逻辑、AI 行为和业务流程由外部 OneBot v11 客户端实现。

## 功能

- OneBot v11 HTTP API、HTTP 事件上报、正向 WebSocket 与反向 WebSocket。
- 私聊、群聊、图片、文件、语音、表情和微信原生群 `@`。
- 稳定且持久化的 OneBot 用户、群与消息 ID。
- 本地 WebUI 管理账号扫描、桥接、OneBot 端点、日志和媒体存储。
- Mock 驱动用于离线测试；Helper 驱动通过固定版本原生桥接读写真实微信消息。
- 可构建包含 Python、依赖、原生组件和 WebUI 的 Windows 便携包。

## 系统要求

- Windows 11 x64。
- 源码开发建议使用 Python 3.13 x64。
- 真实桥接需要安装并登录兼容的 PC 微信，目前验证版本为 **4.1.15.13** 和 **4.1.15.56**。
- 原生桥接需要管理员权限；Mock 模式不需要微信登录。

PC 微信登录必须由用户在微信客户端中完成。WebUI 不提供扫码登录接口。

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

默认 WebUI 为 `http://127.0.0.1:5099/`。首次运行生成 `config.json` 和随机登录凭据；打开 WebUI 后设置管理密码。全新配置没有 OneBot 网络端点，在“节点配置”中添加并保存。

启动 Helper 单服务：

```powershell
.\start.cmd
```

脚本会按需申请管理员权限。在 WebUI 中选择已登录账号、保存 OneBot 端点，再手动启动微信桥接。真实收发验证步骤见 [原生桥接验证指南](docs/live-preflight.md)。

## 配置与数据

`config.example.json` 是配置字段样例；实际 `config.json` 和 `data/` 仅保留在本机，不纳入 Git。每个账号的网络配置位于 `data/accounts/<账号>/onebot.json`，ID 映射和媒体文件也按账号隔离。

备份或迁移时应保留配置、账号数据和媒体目录，并核对自定义组件路径。不要提交密码、Token、账号标识、消息内容或运行日志。

端点、心跳、访问令牌和监听地址说明见 [网络配置](docs/network-config.md)。公开 API 与已知限制见 [OneBot 兼容说明](docs/onebot-compatibility.md)。

## 测试与验证

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q src
```

自动化测试默认使用 Mock 和隔离临时目录，不代表真实微信、管理员权限或原生 DLL 已完成联调。Windows CI 执行 Python 测试、编译检查和便携包构建：[Bot checks](https://github.com/PorkLittleHamburger/wechatbot/actions/workflows/bot.yml)。

## 构建便携包

```powershell
pwsh -File .\scripts\build_portable.ps1
```

产物位于 `dist/`，包含 `src`、`vendor`、`webui`、`assets`、内置 Python、启动脚本和文件校验清单。包内说明见 [PORTABLE_README.md](PORTABLE_README.md)。

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

原生桥接 DLL 位于 `vendor/helper/native/build_text` 与 `vendor/helper/native/build`，大小和 SHA-256 记录在 `vendor/helper/VENDORED_SHA256.json`。重新编译方法见 [原生组件说明](vendor/helper/native/README.md)。

helper 使用非商业许可，允许个人学习、研究、测试、娱乐和非商业自用；商业使用、收费服务、SaaS 及营利产品集成不在其许可范围内。详见 [第三方声明](THIRD_PARTY_NOTICES.md) 与 [helper 许可证](vendor/helper/LICENSE)。