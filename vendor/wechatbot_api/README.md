# wechatbot_api（vendored wheel）

本目录固定独立框架 `wechatbot-api`（导入名 `wechatbot_api`）的开发快照，供
`driver.type="sdk"` 使用。它是**上游 wheel 的原样副本**，不得在本仓库内修改。

| 项 | 值 |
| --- | --- |
| 版本 | `0.1.0.dev15` |
| 构建提交 | `7157c44f04b40fa51945b42e4d8486709be48422` |
| wheel | `wechatbot_api-0.1.0.dev15-py3-none-win_amd64.whl` |
| SHA-256 | `8dc2845866487138307e1bbf0840e98ae37c868b8a6feb43edee10ccd167b951` |
| 原生 bridge | `wca_bridge.dll`（bridge 10 / ABI 1），SHA-256 `5af4026cd8a5b8940c11b451df77ca78b2addaeb64e66e383322449be08a6c0b` |
| 许可 | helper 非商业使用许可协议 1.0（见 `LICENSE`） |
| 目标微信构建 | 4.2.0.52（`Weixin.dll` 204168240 字节，SHA-256 `d733489e…f8d84`） |

框架不依赖本仓库；网关只通过 `wechatbot_api` 的公开 API 接入。完整接口、
能力边界与验收范围见该框架自带的 `docs/API.md` 与 `docs/HANDOVER.md`；本仓库的
接入说明见 [微信版本与能力](../../docs/wechat-versions.md)。

## 使用方式

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps `
  .\vendor\wechatbot_api\wechatbot_api-0.1.0.dev15-py3-none-win_amd64.whl
```

便携包由 `scripts/build_portable.py` 自动安装该 wheel。安装时不要使用
`--no-deps` 之外的自我替换步骤，也不要单独替换包内 DLL：连接前 SDK 会校验
wheel 与 bridge 的版本、大小和哈希。

## 来源边界

- 该 wheel 由框架仓库的 `scripts/build_native.ps1` + `python -m build` 生成，
  清单（大小、SHA-256、工具链）以框架仓库 `docs/ARTIFACTS.json` 为准。
- 本目录的 `VENDORED.json` 记录本仓库固定使用的版本与哈希；升级时整份替换
  wheel、`LICENSE` 与本文件，并同步 `THIRD_PARTY_NOTICES.md` 与版本矩阵。
- wheel 内含 MinHook 1.3.4 的许可与声明，随包保留。
