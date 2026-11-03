# WeChat OneBot 便携包

本目录包含 Windows 11 x64 个人微信 OneBot v11 网关、WebUI、原生桥接、Python 解释器和运行依赖。解压整个目录后即可使用，无需另行安装 Python；移动目录时请连同子目录一起移动。

## 启动

1. 安装并登录受支持的 PC 微信版本。当前验证的 Windows x64 版本是 4.1.15.13 和 4.1.15.56。
2. 双击 `start.cmd`。首次运行会在本目录生成 `config.json`，终端会显示首次登录 WebUI 的 Token。
3. 打开 `http://127.0.0.1:5099/`，使用 Token 设置管理密码。
4. 在“进程与账号”扫描已登录账号并设为机器人账号；在“节点配置”添加 OneBot HTTP、正向 WebSocket 或反向 WebSocket 端点并保存。
5. 需要收发微信消息时，在账号页启动微信桥接。端点与桥接独立运行。

正式桥接需要管理员权限，启动脚本会按需请求提升。微信消息通过 `vendor/helper` 的原生接口收发；不使用 OCR 或模拟点击。WebUI 没有 PC 微信扫码登录接口，请在 PC 微信中完成登录。

## 开机自启与再次登录

在 WebUI 的“系统设置 → 服务”里设置开机自启，也可以在 Windows“任务管理器 → 启动应用”中开关。自启后管理服务会等待上次桥接的微信账号登录，并在识别到账号后恢复桥接。移动整个目录后，重新启用一次开机自启以更新路径。

## 文件说明

| 路径 | 内容 |
| --- | --- |
| `start.cmd` | 单服务启动入口 |
| `python/` | 内置 Python 与锁定的运行依赖 |
| `src/`、`vendor/`、`webui/` | 网关代码、原生桥接和本地管理页面 |
| `config.example.json` | 配置字段样例；首次启动会另行生成带随机凭据的 `config.json` |
| `data/` | 本地账号配置、消息和媒体数据；首次运行后生成 |
| `BUILD_MANIFEST.json` | 包版本和原生 DLL 校验值 |

`config.json` 和 `data/` 只留在本机。请勿将它们随日志或报错截图公开。查看运行状态和报错请使用 WebUI“日志”页面。原生桥接的使用限制见 `THIRD_PARTY_NOTICES.md` 和 `vendor/helper/LICENSE`。

桥接守护启动失败时，报错会给出日志绝对路径。启动输出（包括导入错误）在 `vendor/helper/logs/bridge_watchdog_startup.log`，运行日志在同目录的 `bridge_watchdog.log`；提前退出可能尚未生成运行日志。通过外部启动器运行时，日志位于实例所配置的 `driver.helper_root` 下的 `logs/`，读取时需具备对应权限。
