# Repository Guidelines

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