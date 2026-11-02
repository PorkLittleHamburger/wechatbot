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

适用于 Windows 11 x64。便携包已内置 Python 与运行依赖，无需安装系统 Python，也无需在首次启动时联网下载依赖。登录受支持版本的电脑微信后，解压并运行根目录的 start.cmd。首次启动会生成带随机 WebUI / OneBot Token 的 config.json，随后在 WebUI 设置管理密码。移动整个目录后，内置环境仍可直接使用；旧版虚拟环境会在启动时自动修复。

全新安装只启动 WebUI 管理服务，没有预设 OneBot 服务端或客户端，也不会自动启动微信桥接。进入 WebUI 的“进程与账号”，点击“扫描已登录账号”，选中目标账号后点击“设为机器人账号”。在“节点配置”中添加所需的 OneBot 端点并保存，端点会立即运行；需要连接微信时再点击账号“启动”。下次启动管理服务时，已保存且启用的端点会独立恢复，微信桥接仍由用户手动启动。每次启动账号都会重新识别当前 PID，以适应电脑重启后的 PID 变化。旧版本已经生成的账号级 `onebot.json` 保留原有端点，可在 WebUI 删除或禁用。

在 WebUI 的“系统设置 → 服务”中启用或关闭开机自启，也可在 Windows“任务管理器 → 启动应用”中开关 WeChat OneBot。启动应用快捷方式调用按需计划任务，以便在当前用户登录时启动管理服务；识别到上次桥接账号登录 PC 微信后会立即连接。移动目录后，请重新启用开机自启，以更新任务和快捷方式路径。原有 enable-autostart.cmd 和 disable-autostart.cmd 仍可使用。

当前原生桥接没有 PC 微信登录二维码及扫码状态接口。WebUI 无法生成与本机 PC 微信实例绑定的二维码，也不会使用截图或 Web 微信登录来冒充此功能；未登录时需先通过 PC 微信完成登录。

## 多实例启动器

桌面版 **WeChat OneBot Launcher 0.8.0** 提供独立窗口、托盘、版本管理、升级、备份恢复和开机自启。构建后的离线 EXE 安装程序位于 `dist/WeChat-OneBot-Launcher_0.8.0_x64-setup.exe`。使用方法、全新工作空间与构建环境位置见 [桌面启动器说明](docs/desktop-launcher.md)。以下 `launcher.cmd` 是保留的浏览器管理入口，供源码开发和兼容使用；桌面版请运行 EXE 安装程序。

运行根目录 `launcher.cmd` 可打开多实例管理面板（默认本机 5088 端口），统一创建、编辑、启动、停止、重启、查看日志、备份和归档多个独立服务实例。每个实例有独立配置、数据、管理密码与 WebUI 端口。新建默认使用 Mock；Helper 模式需要以管理员身份运行启动器，账号绑定和桥接启动在各自 WebUI 中操作。

源码命令为 `.\.venv\Scripts\python.exe -m wechat_onebot launcher`。此版本的实例共享当前安装目录的代码及 Python；尚不提供按实例切换软件版本、托盘或启动器开机自启。完整设计、参考源码、目录与使用方式见 [多实例启动器](docs/multi-instance-launcher.md)。

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

管理服务默认地址与可选的 OneBot 端点：

- WebUI：`http://127.0.0.1:5099/`
- WebUI 健康检查：`http://127.0.0.1:5099/healthz`
- OneBot HTTP API、正向 WebSocket 和反向 WebSocket 均需在 WebUI 的“节点配置”中手动添加；全新安装没有默认 OneBot 端点。

每个账号的网络配置独立保存在 `data/accounts/<账号>/onebot.json`，包含 `httpServers`、`httpClients`、`wsServers`、`wsClients`。在 WebUI 保存后端点立即生效，即使微信账号 Runtime 尚未启动也能监听或连接；未改动的端点保持连接。启动或停止微信桥接不会停止端点，账号离线状态会通过 OneBot 状态接口报告。管理服务关闭时才统一释放端点。WebUI 还提供实时状态、事件日志、存储统计和 JSON 动作调试/测试发送。

AstrBot 等 OneBot 客户端可能在回复链中同时发送 `reply`、`at` 和文本。当前原生桥接没有微信引用回复发送入口，因此发送时会省略引用关系，继续发送正文及真实群 `@`；单独只有 `reply`、没有可发送内容的消息会报错。

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
