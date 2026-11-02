# OneBot 网关开发说明

本目录包含个人微信 OneBot v11 网关、账号 WebUI 与原生桥接适配。桌面启动器位于相邻的 [launcher 目录](../launcher/README.md)。

## 目录

| 路径 | 用途 |
| --- | --- |
| src/wechat_onebot | 协议、账号桥接、网络、WebUI 与 CLI；launcher 子包提供源码导入兼容入口 |
| tests | 网关、账号与 WebUI 测试 |
| vendor/helper | 固定版本的原生微信桥接与第三方声明 |
| webui | 账号和 OneBot 配置管理页 |
| scripts | 单服务启动、桥接预检和便携包构建 |
| docs | 架构、网络配置、协议兼容和验证说明 |
| config.example.json | 配置字段样例 |
| pyproject.toml | Python 包与测试配置 |
| requirements-runtime.lock | 离线运行时依赖锁文件 |

## 环境与测试

以下命令均在仓库根目录执行，建议使用 Python 3.13 x64：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r bot/requirements-runtime.lock
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"
.venv/Scripts/python.exe -m pytest -q bot/tests -c bot/pyproject.toml
```

启动 Mock 管理服务：

```powershell
.venv/Scripts/python.exe -m wechat_onebot --config bot/config.json run --mock
```

默认 WebUI 为 `http://127.0.0.1:5099/`。首次运行生成 `bot/config.json`，使用其中的 WebUI 首次登录凭据设置管理密码。全新账号没有 OneBot 端点，在 WebUI“节点配置”中添加后生效；Mock 模式不连接真实微信。

## 真实微信桥接

运行 `bot/start.cmd` 可启动 Helper 单服务，脚本会按需申请管理员权限。源码启动脚本在 `bot/.venv` 中创建独立环境；便携包使用包内 `python`。在 WebUI 中扫描已登录账号并绑定，配置端点后手动启动账号桥接。

桥接需要兼容的 Windows x64 微信构建，版本要求见 [项目首页](../README.md#系统要求)。环境检查可使用 `.venv/Scripts/python.exe -m wechat_onebot --config bot/config.json doctor`；真实收发验证步骤见 [原生桥接验证指南](docs/live-preflight.md)。

源码单服务配置位于 `bot/config.json`，默认数据位于 `bot/data`，这些运行文件不纳入 Git。每个账号的网络配置为 `data/accounts/<账号>/onebot.json`。备份或移动部署时应保留配置与数据，并检查自定义的组件及媒体路径。

## 构建便携包

在仓库根目录执行：

```powershell
pwsh -File bot/scripts/build_portable.ps1
```

产物位于根目录 `dist`，采用包含 `src`、`vendor`、`webui`、`assets` 和内置 Python 的平面目录。包内 README 来源为 [PORTABLE_README.md](PORTABLE_README.md)。桌面 EXE 使用另一套 [构建入口](../launcher/docs/building.md)。

原生桥接 DLL 位于 `vendor/helper/native/build_text` 与 `vendor/helper/native/build`，大小和 SHA-256 记录在 `vendor/helper/VENDORED_SHA256.json`。重新编译方法见 [原生组件说明](vendor/helper/native/README.md)。第三方使用限制见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
