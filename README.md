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

安装前请确认目标电脑微信版本在上述列表内。微信升级或重装为其他版本后，原生桥接会拒绝加载。

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

协议核心、账号级 Runtime、OneBot 网络适配器和 WebUI 已实现。项目测试记录包含私聊文本、图片、文件和群原生 `@` 的入站验证；不同电脑、微信构建和账号环境仍需各自验收。

## 便携包一键启动

适用于 Windows 11 x64。先安装 Python 3.11 或更新版本并加入 PATH，登录受支持版本的电脑微信，然后解压便携包并双击根目录的 `start.cmd`。入口优先使用 PATH 中的 PowerShell 7 (`pwsh.exe`)，其次使用标准安装目录中的 PowerShell 7；未安装时兼容 Windows PowerShell 5.1。首次启动会自动创建虚拟环境、安装依赖和生成带随机 WebUI / OneBot Token 的 `config.json`。启动窗口会显示 WebUI 地址和首次登录 Token；使用 Token 验证后必须设置管理密码，以后用密码登录。密码摘要保存在本地 `data/webui-auth.json`，不会明文保存。首次安装需要联网获取 Python 依赖，并会出现管理员权限提示。

启动后仅提供 WebUI 管理服务，不会自动启动已绑定微信账号，也不会自动创建 OneBot 服务端或客户端。进入 WebUI 的“进程与账号”，点击“扫描已登录账号”，选中目标账号后点击“设为机器人账号”。在“节点配置”中添加所需的 OneBot 端点并保存，再点击“启动”连接微信。保存绑定本身不会加载桥接。每次启动 Runtime 都会按账号重新识别当前 PID，以适应电脑重启后的 PID 变化。旧版本已经生成的账号级 `onebot.json` 保留原有端点，可在 WebUI 删除或禁用。

双击 `enable-autostart.cmd` 可创建当前 Windows 管理员用户登录时触发的高权限计划任务；双击 `disable-autostart.cmd` 可移除。计划任务使用安装时所选的 PowerShell，开机自启仅启动管理服务；账号需在 WebUI 手动启动。移动或删除解压目录前，请先移除任务；移动后需重新启用。查看任务状态可运行 `pwsh -File .\scripts\autostart.ps1 -Status`。

## 从源码安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
python -c "from wechat_onebot.config import ensure_config; ensure_config('config.json')"
```

修改 `config.json` 中至少以下配置：

- `onebot.access_token`：OneBot 客户端凭据
- `webui.access_token`：管理页首次登录凭据，设置管理密码后不可再用于登录
- `driver.type`：正式运行使用 `helper`，协议开发使用 `mock`
- `driver.wechat_data_directory`：可选。保持 `null` 时根据微信进程安装路径和当前账号自动发现数据目录；自定义安装位置时可显式填写当前账号目录

## 账号绑定

首选 WebUI 的“进程与账号”页面。扫描是只读操作，选择时会复查账号与 PID，再写入本地 `config.json`；该操作不会注入或发送消息。命令行 `bind` 仍供自动化与故障排查使用。

## 检查环境

在管理员 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot doctor
```

## 启动

正式 helper 模式需要管理员权限，并会加载原生桥接：

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\run_real.ps1
```

只验证 OneBot 协议、不连接微信：

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\run_mock.ps1
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
pwsh -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

脚本会运行打包器，在 `dist/` 下生成带固定 helper 提交、原生 DLL SHA-256 清单、一键启动和开机自启脚本的便携目录与 zip。便携包不包含任何安装者的 `config.json`、消息或登录数据。

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

启动服务后打开：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot --config .\config.json run --mock
```

浏览器访问 `http://127.0.0.1:5099/`。首次使用 `webui.access_token` 验证并设置管理密码，之后用管理密码登录；“系统设置 → 账号安全”可修改密码。管理页采用浅色蓝调界面，可切换深色主题并展开侧栏。六个主视图为总览、进程与账号、节点配置、日志、调试、系统设置；设置中包含存储管理。进程页提供只读扫描与账号绑定；保存绑定后可单独启动 Runtime。日志页显示受控诊断摘要及 OneBot 事件；调试页包含动作测试、消息编排、实时活动和接口目录，真实发送需要显式勾选确认。管理页通过独立的 HttpOnly 会话 Cookie 访问 Runtime API；OneBot access token 不会进入浏览器。

忘记管理密码时，关闭服务后删除本地 `data/webui-auth.json`，再使用 `config.json` 中的 `webui.access_token` 完成首次验证并设置新密码。自定义 `storage.data_dir` 时，密码文件位于该目录下。

## 原生桥接预检

项目保留了一次 Windows 11 测试环境的原生桥接与 OneBot 联调记录，涵盖账号识别、消息收发、媒体保存和群原生 `@`。这份记录仅说明该环境中的测试结果，不保证其他微信版本、账号或电脑上行为相同。详情见 `docs/live-preflight.md`；新安装应先运行 `doctor`，再按自身环境验证。
