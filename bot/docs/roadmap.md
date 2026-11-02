# Roadmap

## Milestone 1: Protocol core (complete)

- HTTP API, forward WebSocket, reverse WebSocket
- stable persisted numeric IDs
- mock driver
- text, image, file, voice, emoticon and native group mention send paths
- basic local management page
- offline tests

## Milestone 2: Native bridge preflight (complete)

- confirmed a dedicated test WeChat account in the development environment
- verified DLL load/unload lifecycle
- verified `get_login_info`, `get_friend_list`, `get_group_list`
- sent one text message to File Transfer Helper
- verified account/PID mismatch refusal
- verified the full AccountRuntime -> action registry -> native bridge path
- verified a negative signed 32-bit message ID is durable in SQLite
- inbound text, image, file and native group mention verified with a second live account

## Milestone 3: SnowLuma-aligned runtime boundaries (complete)

Completed:

- account-level Bridge lifecycle manager
- per-account runtime and isolated data/media directories
- durable per-account message store
- non-zero signed 32-bit message IDs
- action registry with validation metadata and examples
- explicit account discovery; no periodic process polling
- separate per-account `httpServers`, `httpClients`, `wsServers`, and `wsClients`
- HTTP client event reporting
- WebUI session Cookie authentication independent from OneBot tokens
- WebUI SSE runtime state stream
- multi-account RuntimeManager and account-scoped WebUI action/start/stop APIs

## Milestone 4: OneBot compatibility hardening (in progress)

- Standard OneBot v11 client end-to-end tests
- full message deduplication and reconnect tests (provisional media placeholders are now suppressed)
- media URL base configuration
- group role/card mapping improvements
- reverse WebSocket reconnection and token tests against external clients

## Milestone 5: Runtime management console (core complete)

- account status and bridge diagnostics
- connection/token editing
- friend/group/member browser
- test-send dialog with explicit confirmation
- recent event log and storage statistics
- capability matrix and unsupported-action visibility
- JSON action console with write confirmation

Completed: storage statistics, event log, network configuration editor and JSON action console.
Deferred: backup/restore and notification-channel UI.

## Milestone 6: Packaging (portable directory complete; single-exe deferred)

- portable directory and zip build: `scripts/build_portable.ps1` (complete)
- single executable
- pinned helper commit and native hash verification
- startup/tray lifecycle
- clean shutdown and DLL unload verification
- upgrade guard for unsupported WeChat builds

## Explicitly deferred

- recall send (`delete_msg`)
- merged forward send
- arbitrary group administration
- QQ face ID conversion
- any OCR, screenshot, coordinate click, clipboard, or keyboard/mouse message sending
