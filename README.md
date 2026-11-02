# WeChat OneBot Launcher

Windows x64 桌面启动器，用于管理个人微信 OneBot v11 网关的多个独立实例。桌面应用采用 Tauri 2、React/TypeScript 与 Python 管理服务，提供实例启停、版本选择、升级、备份恢复、日志、托盘和开机自启。

## 系统要求

- Windows 11 x64。
- 使用真实微信桥接时，需安装并登录兼容的 PC 微信。目前支持的构建为 **4.1.15.13** 和 **4.1.15.56**；其他构建会被原生桥接拒绝加载。
- Mock 模式用于离线测试，不要求微信登录。

离线 EXE 安装包包含 Python、运行依赖、内置网关版本、WebView2 和 Visual C++ x64 运行库。安装后的正常使用无需开发工具或外部浏览器。

## 桌面版快速开始

1. 运行 `WeChat-OneBot-Launcher_<版本>_x64-setup.exe` 并安装。通过桌面或开始菜单快捷方式启动，按系统提示授予管理员权限。
2. 在“实例管理”创建实例，选择已安装的程序版本。可先使用默认的 Mock 模式检查配置。
3. 启动实例并点击“打开 WebUI”，使用首次登录凭据设置管理密码。
4. 使用 Helper 模式时，在实例 WebUI 中扫描并选择已登录的微信账号，在“节点配置”中添加所需 OneBot 端点，再手动启动账号桥接。

全新实例没有默认 OneBot 网络端点。端点配置保存后立即生效，微信桥接与网络端点分别控制。

默认工作空间为 `%LOCALAPPDATA%\WeChatOneBotLauncher`，与安装目录分离。关闭主窗口默认隐藏到托盘；选择托盘“退出”会停止实例并退出程序。卸载默认保留运行数据。

完整操作见 [桌面使用说明](launcher/docs/desktop-launcher.md)。EXE 不纳入 Git；可从维护者提供的安装包安装，也可按 [构建说明](launcher/docs/building.md) 自行构建。成功的 [Windows CI](https://github.com/PorkLittleHamburger/wechat-onebot-launcher/actions/workflows/desktop.yml) 会提供构建工件。

## OneBot 与微信桥接

网关提供 OneBot v11 HTTP API、HTTP 事件上报、正向 WebSocket 和反向 WebSocket，支持消息、账号及群信息等操作。消息能力和限制见 [协议兼容说明](bot/docs/onebot-compatibility.md)，端点配置见 [网络配置说明](bot/docs/network-config.md)。

真实微信消息通过 `bot/vendor/helper` 原生桥接收发。PC 微信登录需在微信客户端中完成；WebUI 不提供 PC 微信登录二维码。微信升级后应先确认桥接兼容性。不同实例应使用独立端口和账号，同一微信 PID 不能同时由多个实例桥接。

## 其他运行方式

| 方式 | 入口与用途 |
| --- | --- |
| 桌面应用 | 安装 EXE，提供版本管理、托盘、升级和恢复功能 |
| 网关便携包 | 解压后运行包内 `start.cmd`；详见 [便携包说明](bot/PORTABLE_README.md) |
| 源码单服务 | `bot/start.cmd` 或 Python CLI；详见 [机器人开发说明](bot/README.md) |
| 浏览器多实例管理 | `launcher/launcher.cmd`；共享源码与 Python 环境，详见 [兼容管理入口](launcher/docs/multi-instance-launcher.md) |

桌面应用、源码运行与便携包使用各自的数据目录，不会自动导入其他部署的账号数据。

## 从源码开发

以下命令在仓库根目录执行，建议使用 Python 3.13 x64：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r bot/requirements-runtime.lock
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"
.venv/Scripts/python.exe -m pytest -q bot/tests launcher/tests -c bot/pyproject.toml
```

只运行 Mock 单服务：

```powershell
.venv/Scripts/python.exe -m wechat_onebot --config bot/config.json run --mock
```

默认 WebUI 地址为 `http://127.0.0.1:5099/`。首次运行生成配置及随机登录凭据，登录后设置管理密码。真实桥接的前置检查见 [原生桥接验证指南](bot/docs/live-preflight.md)。

## 项目目录

| 路径 | 内容 |
| --- | --- |
| [launcher/](launcher/README.md) | 桌面工程、Python 管理后端、浏览器管理页、构建脚本与测试 |
| [bot/](bot/README.md) | OneBot 网关、微信桥接适配、账号 WebUI、配置及协议测试 |
| dist/ | 构建生成的安装器、运行时包和校验文件，不纳入 Git |

桌面安装器构建和依赖配置见 [构建说明](launcher/docs/building.md)，验证步骤见 [验收指南](launcher/docs/desktop-verification.md)。

## 第三方组件与使用限制

原生桥接组件 helper 使用非商业许可，允许个人学习、研究、测试、娱乐和非商业自用；商业使用、收费服务、SaaS 及营利产品集成不在其许可范围内。其他组件遵循各自许可证。详见 [第三方声明](bot/THIRD_PARTY_NOTICES.md) 与 [helper 许可证](bot/vendor/helper/LICENSE)。

配置、账号数据、密码记录和备份应保存在受控位置。提交问题时请附软件版本、复现步骤及已移除凭据和消息内容的日志。
