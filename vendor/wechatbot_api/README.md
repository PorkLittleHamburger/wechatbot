# wechatbot_api（vendored wheel）

本目录固定独立框架 `wechatbot-api`（导入名 `wechatbot_api`）的开发快照，供
`driver.type="sdk"` 使用。它是**上游 wheel 的原样副本**，不得在本仓库内修改。

| 项 | 值 |
| --- | --- |
| 版本 | `0.1.0.dev18` |
| 构建提交 | `11fd38697b473a0fb5834cd598e62f7e66a160a4` |
| wheel | `wechatbot_api-0.1.0.dev18-py3-none-win_amd64.whl` |
| SHA-256 | `23b25ff38f5e28d3edddcfe5acc99adbb1179d91425b838ded38234751c8e7df` |
| 原生 bridge | `wca_bridge.dll`（bridge 11 / ABI 2），SHA-256 `fec9c75f747ccf269f8bca5dfb783cdb94df78fbd131cf9074a92952c693db06` |
| 许可 | helper 非商业使用许可协议 1.0（见 `LICENSE`） |
| 目标微信构建 | 4.2.0.52（`Weixin.dll` 204168240 字节，SHA-256 `d733489e…f8d84`）；4.2.0.53（204154920 字节，SHA-256 `ac6b174d…104825`） |

框架不依赖本仓库；网关只通过 `wechatbot_api` 的公开 API 接入。完整接口、
能力边界与验收范围见该框架自带的 `docs/API.md` 与 `docs/HANDOVER.md`；本仓库的
接入说明见 [微信版本与能力](../../docs/wechat-versions.md)。

## 使用方式

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps `
  .\vendor\wechatbot_api\wechatbot_api-0.1.0.dev18-py3-none-win_amd64.whl
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
