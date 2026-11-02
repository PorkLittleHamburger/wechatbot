# 机器人开发目录

这里是个人微信 OneBot v11 协议端的 Python 项目。启动器的全部开发文件集中在相邻的 [launcher 目录](../launcher/README.md)，包括 React/Tauri 桌面工程、Python 管理后端、构建脚本、测试与文档。

## 目录

| 路径 | 用途 |
| --- | --- |
| src/wechat_onebot | 协议、账号桥接、OneBot 网络、WebUI 服务；launcher 子包仅保留源码兼容入口 |
| tests | 机器人协议、账号与 WebUI 自动化测试；启动器测试位于 ../launcher/tests |
| vendor/helper | 固定版本的原生微信桥接依赖 |
| webui | 账号与 OneBot 配置管理页面 |
| assets/icons | 项目图标 |
| scripts | 单服务启动、预检和便携包构建 |
| docs | 协议、网络配置及原生桥接开发说明 |
| config.example.json | 配置样例；实际配置保存在本地 config.json |
| pyproject.toml | Python 项目和测试配置 |
| requirements-runtime.lock | 离线运行时依赖锁定文件 |

## 开发

在仓库根目录使用 Python 3.13 创建虚拟环境，并执行：

    .venv\Scripts\python.exe -m pip install -e "./bot[dev]"
    .venv\Scripts\python.exe -m pytest -q bot/tests -c bot/pyproject.toml

这套根目录虚拟环境用于开发测试和构建。直接运行 bot/start.cmd 时，单服务脚本使用 bot/python（便携包）或 bot/.venv（源码环境），首次运行会按需安装项目依赖。

从机器人目录运行单服务启动脚本：

    bot\start.cmd

构建便携包：

    pwsh -File bot\scripts\build_portable.ps1

便携包继续使用原有的平面布局，包含 src、vendor、webui、assets 和内置 Python；目录整理不会改变既有便携包的运行时文件结构。构建产物位于仓库根目录 dist。

便携包根目录的 README.md 由本目录的 PORTABLE_README.md 生成，介绍最终用户的启动和配置步骤。

旧开发环境的账号配置和运行数据已迁入 bot/config.json 与 bot/data。两个路径均不纳入 Git。旧联调记录归档在 bot/.development/legacy-public-area，也不纳入 Git。

原生消息桥接所需的两个预编译 DLL 位于 vendor/helper/native/build_text 与 vendor/helper/native/build；对应文件的大小与 SHA-256 记录在 vendor/helper/VENDORED_SHA256.json。重新编译可使用 vendor/helper/native/build_bridges.ps1，编译器和构建步骤见 vendor/helper/native/README.md。

桌面启动器的源码、构建和安装说明见[启动器开发说明](../launcher/README.md) 与 [桌面使用说明](../launcher/docs/desktop-launcher.md)。
