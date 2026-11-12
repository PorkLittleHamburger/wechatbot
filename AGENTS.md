# Repository Guidelines

## Required Handover & Documentation Maintenance

新接手本项目的 AI 必须先阅读本文件、[开发交接](docs/HANDOVER.md) 和 [文档索引](docs/README.md)，再开展工作；交接中的版本、验证记录和待办是带日期的快照，必须核对当前代码与 Git 状态。

- 每次改动后核对受影响的 README、配置、命令、能力限制及交接说明，保证与代码一致；阶段性交接或发布前清查全部项目文档及历史遗留描述。
- 检查远端默认分支的 README，不能仅凭本地文档宣称远端已更新。使用通用路径、占位值和可配置条件，区分默认值、已实现能力、规划、上游 helper 功能和本机验证结果。
- 发布包必须沿用 `wechat-onebot-<version>-windows-x64.zip` 与同名 `.sha256`，通过构建脚本生成；发布后用实际 Launcher 的版本目录、下载及导入流程复验。公开 URL 可下载不等同启动器完整兼容。
- 更新交接中的架构入口、关键决策、已知限制、验证证据及未完成事项。不得把个人账号、实例 ID、绝对部署路径、代理地址或一次性测试结果写成通用要求。
- 保留第三方原文和许可的来源边界；修改 vendored 文件时更新差异声明及相应哈希。验证后提交并推送；发布 Release 仍需用户明确确认。

## Project Structure & Module Organization

This repository is the standalone Python OneBot v11 gateway for a personal Windows WeChat account. Run commands from the repository root.

- `src/wechat_onebot/`: `bridge/` handles WeChat and helper lifecycle; `protocol/` normalizes events; `onebot/` implements actions and transports; `core/` owns account runtimes; `webui/` serves the management UI.
- `tests/`: pytest coverage for protocol conversion, IDs, networking, runtime, bridge management, storage, and WebUI APIs.
- `webui/`, `assets/`, `docs/`, and `scripts/`: frontend files, icons, design notes, and launch/preflight/build utilities.
- `vendor/helper/`: pinned native bridge. Do not modify vendored files without following its README and updating `VENDORED_SHA256.json`.
- `config.example.json`: configuration template. Never commit local `config.json`, `data/`, credentials, or logs.

Do not add unrelated application code to this repository.

## Build, Test, and Development Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
.\scripts\run_mock.ps1
.\start.cmd
pwsh -File .\scripts\build_portable.ps1
```

The first three commands create the development environment. `run_mock.ps1` starts an offline service; `start.cmd` launches the Helper service; the build command writes a portable package to `dist/`.

## Coding Style & Naming Conventions

Use Python 3.11+ syntax, four-space indentation, type hints, and `pathlib.Path` for filesystem work. Name modules and functions `snake_case`, classes `PascalCase`, and constants `UPPER_CASE`. Keep public OneBot JSON fields and existing configuration keys unchanged. No linter or formatter is configured; preserve the surrounding style and keep errors explicit.

## Testing Guidelines

Use pytest with `pytest-asyncio` (`asyncio_mode = "auto"`). Add focused `test_<behavior>` functions in `tests/test_<area>.py`; use `tmp_path` for isolated state and `TestClient` for WebUI APIs. Add regression tests for protocol, runtime, ID mapping, storage, or transport changes. No coverage threshold is configured, but new behavior needs a passing test. Live preflight scripts do not replace the mocked suite.

## Commit & Pull Request Guidelines

Use concise imperative commit subjects; focused Conventional Commits such as `fix: reject unsupported actions` match the history. Pull requests should describe behavior changes, list executed tests, link issues when available, and include screenshots for WebUI changes. Scrub tokens, account IDs, and message data. Push verified updates; publish a release only after explicit confirmation.

## Security & Native Bridge Rules

Never return fake success for unsupported OneBot actions. Preserve durable ID mappings and account isolation. Native sends must use the helper bridge, not OCR, clicks, clipboard, or keyboard simulation. Reserve elevated/live WeChat tests for deliberate verification and confirm before real sends.
