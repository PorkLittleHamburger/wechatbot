# WeChat OneBot

Windows 11 个人微信的 OneBot v11 协议端。设计目标：

- 个人微信账号，不使用微信官方 ClawBot。
- 对外提供 OneBot v11 HTTP API、正向 WebSocket、反向 WebSocket。
- 微信消息收发使用 `vendor/helper` 的原生 DLL 桥接，不使用 OCR、截图、坐标或键鼠模拟发送。
- 按 SnowLuma 的协议运行时思路，将账号 Bridge、协议转换、OneBot 网络适配和管理面分离。

## 当前支持的微信版本

`vendor/helper` 当前只验证以下 Windows x64 微信构建：

- `4.1.15.13`
- `4.1.15.56`

本机检测到的版本为 `4.1.15.56`。微信升级或重装为其他版本后，原生桥接会拒绝加载。

## 重要限制

`vendor/helper` 使用非商业许可：

- 允许个人学习、研究、测试、娱乐和非商业自用。
- 禁止商业使用、收费服务、SaaS、商业客户服务及集成到营利产品。
- 第三方组件继续遵循各自许可证。

详见 `vendor/helper/LICENSE`、`vendor/helper/THIRD_PARTY_NOTICES.md` 和根目录 `THIRD_PARTY_NOTICES.md`。

## 开发状态

当前已实现：

- 稳定 `wxid` / `chatroom` 与 OneBot 数字 ID 映射
- OneBot v11 HTTP 与 WebSocket 事件
- 文本、图片、文件、语音、表情和群 `@` 的动作映射
- Mock 驱动和自动化测试
- 原生 helper 驱动适配

协议核心、账号级 Runtime、OneBot 网络适配器、WebUI 和真实账号入站验证已完成。已实测私聊文本、图片、文件和群原生 `@`；下一阶段主要是第三方 Bot 框架端到端兼容、重连压力和便携包验收。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .\config.example.json .\config.json
```

修改 `config.json` 中至少以下配置：

- `onebot.access_token`：OneBot 客户端凭据
- `webui.access_token`：管理页独立凭据，不发送给 OneBot 客户端
- `driver.type`：正式运行使用 `helper`，协议开发使用 `mock`
- `driver.wechat_data_directory`：可选。保持 `null` 时根据微信进程安装路径和当前账号自动发现数据目录；自定义安装位置时可显式填写当前账号目录

## 账号绑定

先在管理员 PowerShell 中登录电脑微信，再执行：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot bind
```

该命令只读取当前已登录账号的资料并写入配置，不会注入微信。读取受保护微信进程需要管理员权限。

同时运行多个微信实例时，使用 `--account` 非交互选择已经绑定的账号：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot bind --account wxid_example
```

只存在一个已登录实例时可以省略该参数。

## 检查环境

在管理员 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot doctor
```

## 启动

正式 helper 模式需要管理员权限，并会加载原生桥接：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_real.ps1
```

只验证 OneBot 协议、不连接微信：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_mock.ps1
```

默认监听：

- WebUI：`http://127.0.0.1:5099/`
- OneBot HTTP API：`http://127.0.0.1:6700/`
- 正向 WebSocket：`ws://127.0.0.1:6701/ws`
- WebUI 健康检查：`http://127.0.0.1:5099/healthz`
- OneBot HTTP 健康检查：`http://127.0.0.1:6700/healthz`

每个账号的网络配置独立保存在 `data/accounts/<账号>/onebot.json`，包含 `httpServers`、`httpClients`、`wsServers`、`wsClients`。旧版全局 `onebot` / `reverse_ws` 配置仅用于首次迁移。WebUI 还提供实时状态、事件日志、存储统计和 JSON 动作调试/测试发送。

## 便携包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

脚本会运行打包器，在 `dist/` 下生成带固定 helper 提交、原生 DLL SHA-256 清单、启动脚本和 zip 的便携目录。便携包首次运行时会自动创建 `.venv`、安装依赖并复制 `config.example.json`。

## 协议测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Mock 模式不连接微信，可用于验证任意标准 OneBot v11 客户端（HTTP、正向 WebSocket、反向 WebSocket）。

## 目录

```text
src/wechat_onebot/bridge/    微信进程发现、账号级 Bridge 与原生桥接生命周期
src/wechat_onebot/protocol/  事件标准化与身份转换
src/wechat_onebot/onebot/    动作注册表、持久化消息/媒体、网络适配器
src/wechat_onebot/core/      账号 Runtime 与应用编排
webui/                        本地管理页
vendor/helper/                固定提交的微信原生桥接依赖
tests/                        离线协议测试
docs/                         架构、SnowLuma 对齐、预检和兼容性说明
```


## 管理页

启动 Mock 模式后打开：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot --config .\config.json run --mock
```

浏览器访问 `http://127.0.0.1:5099/`，使用 `webui.access_token` 登录。管理页采用 SnowLuma 风格的浅色蓝调界面，可切换深色主题并展开侧栏。六个主视图为总览、进程与账号、节点配置、日志、调试、系统设置；设置中包含存储管理。进程页默认只读取已有 Runtime 状态，只有手动点击“扫描已登录账号”才进行只读扫描。日志页显示受控诊断摘要及 OneBot 事件；调试页包含动作测试、消息编排、实时活动和接口目录，真实发送需要显式勾选确认。管理页通过独立的 HttpOnly 会话 Cookie 访问 Runtime API；OneBot access token 不会进入浏览器。

## 原生桥接预检

2026-11-01 至 2026-11-02 已在专用测试账号和第二个真实账号之间完成受限预检：

- 原生桥接成功加载并在结束时正常卸载；进程模块与 watchdog 检查均无残留
- 账号在线状态读取成功，好友列表读取成功（40 个）
- 向“文件传输助手”发送测试文本，原生接口返回接受
- 完整栈预检通过 `AccountRuntime → OneBot action registry → native bridge` 发送成功
- 真实账号下 HTTP `6700` 与 WS `6701` 适配器预检通过
- 完整 WebUI `5099`、Runtime、网络适配器联合预检通过
- 第二账号发来的私聊文本、真实 JPEG 图片和真实 XLSX 文件均生成正确 OneBot 段
- 群聊中使用微信原生 `@` 选择器时收到 `to_me=true` 和 `at` 段
- 图片和文件均完成内容校验并落入账号隔离媒体目录
- 同一媒体消息的临时 monitor 占位事件已抑制，避免先发 `[图片]` 再发 `image` 的重复上报

账号识别和原生桥接需要管理员权限。预检使用 `scripts/run_bind_and_preflight_elevated.ps1` 或现场采集脚本，会触发一次 UAC。详细结果见 `docs/live-preflight.md`。

本机已实际运行两个独立微信实例；绑定和采集均通过账号/PID 隔离选择目标实例，不依赖窗口标题或前台窗口。
