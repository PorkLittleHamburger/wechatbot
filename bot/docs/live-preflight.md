# Native Bridge Live Preflight

Date: 2026-11-01 through 2026-11-02

Environment: Windows 11 x64, WeChat 4.1.15.56, Python 3.13.9 x64

## Result

The live stack has now been validated in both outbound and inbound directions on
real WeChat accounts. No OCR, screenshot, coordinate click, clipboard, or
keyboard/mouse message sending was used.

### Bridge and outbound preflight

- Bridge state: online / good
- Account binding: exact account/PID match confirmed
- Friend list: 40 records returned
- File Transfer Helper text send: accepted by the native bridge
- Full path exercised: `AccountRuntime -> OneBot action registry -> native helper -> WeChat`
- Returned `message_id`: non-zero signed 32-bit value
- Message persisted to the per-account SQLite store
- Bridge cleanup: completed normally
- Post-run process audit: no `text_bridge`, `selftest_bridge`, MinHook, watchdog, or OneBot process remained

### Network and WebUI preflight

- OneBot HTTP listened on `127.0.0.1:6700`
- Forward WebSocket listened on `127.0.0.1:6701/ws`
- Lifecycle and `get_login_info` succeeded over HTTP/WS
- WebUI listened on `127.0.0.1:5099` with an independent HttpOnly session login
- Shutdown released all ports and unloaded the native bridge

### Inbound live messages

A second real WeChat account sent messages to the bound account.

Text:

- private text produced a `message_type=private` event with a `text` segment
- the native sender identity and stable numeric `user_id` were retained

Image:

- a real JPEG message first produced a provisional monitor event
- the asynchronous native download completed and produced a real `image` segment
- validated file: JPEG, 1280 x 2781, 256,834 bytes
- the final OneBot segment includes the local media filename and HTTP media URL
- the provisional media placeholder is now suppressed before final delivery

File:

- a real XLSX attachment produced a `file` segment with filename and HTTP media URL
- validated file: 201,647 bytes, valid ZIP/XLSX container with 30 entries
- the sender-provided filename is preserved after path sanitization

Group native mention:

- using WeChat's native `@` selector produced a group message with `to_me=true`
- the final message contains an `at` segment followed by the text segment
- manually typing `@name` as plain text is not treated as a native mention

### Media handoff fix

The elevated helper client and the normal-integrity WeChat process cannot
reliably let the native task copy directly into the project media workspace.
The verified flow is:

```text
WeChat native download
  -> current account's own temp directory
  -> elevated Python validates and copies
  -> account-isolated project media directory
  -> OneBot image/file event
```

The WeChat account data directory is auto-detected from the running process and
account ID. `driver.wechat_data_directory` can override it for a custom install.
Temporary native files are removed after the copy.

### Multi-instance validation

Two independent WeChat instances were present during the final test. The
binding and capture scripts selected the target using:

```text
wxid -> matching logged-in process -> exact PID
```

No foreground-window or UI-coordinate selection was used.

## Prerequisites

- WeChat must be logged in before starting.
- The helper client must run elevated because it inspects the process and loads
  the native bridge.
- A dedicated test account is recommended.
- When multiple instances are running, bind with `--account <wxid>`.

## Reproduce

Outbound/network preflight:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_bind_and_preflight_elevated.ps1
```

Inbound live capture:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_inbound_capture_elevated.ps1
```

The second account then sends private text, a real image, a real file, and a
group message using WeChat's native `@` selector. The capture status and events
are written under `data/`.

## Remaining validation

- Standard OneBot v11 clients over HTTP, forward WebSocket and reverse WebSocket
- long-running reconnect/failover tests
- port and bridge cleanup after abnormal termination
- portable package acceptance on a clean Windows profile
