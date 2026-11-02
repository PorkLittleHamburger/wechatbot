# SnowLuma-Aligned Architecture

SnowLuma is a TypeScript QQ protocol runtime. This WeChat project uses similar
architectural principles but is an independent Python implementation built on
the vendored helper native bridge for WeChat 4.1.15.x.

## Adopted principles

1. **Protocol runtime only**
   - This project exposes normalized OneBot v11 events and actions.
   - Bot personalities, AI replies, command handlers, and business workflows stay
     in external OneBot v11 clients. This repository ships no framework-specific integration.

2. **Ownership boundaries**
   - `bridge`: WeChat process discovery, account identity, native DLL lifecycle,
     transport health, and normalized bridge events.
   - `protocol`: conversion from raw helper/WeChat data into typed events and
     stable identities. Parsing does not mutate live account state.
   - `onebot`: action registry, validation, result schemas, event conversion,
     durable message/media stores, and network adapters.
   - `core`: application composition, account runtime manager, per-account
     configuration, notifications, and WebUI backend.
   - `webui`: browser management surface, state stream, logs, connections, and
     storage controls.
   - External applications connect only through OneBot v11.

3. **Per-account isolation**
   - Each account has its own Bridge session, OneBot instance, identity store,
     message store, media directory, and network configuration.
   - A WeChat PID must be bound to exactly one account/session.
   - Account mismatch or a stale session must refuse to send rather than fall
     through to a reconnected account.

4. **Durable identities and messages**
   - External `wxid`/`chatroom` identifiers are mapped to stable OneBot numeric
     IDs and persisted per account.
   - OneBot `message_id` is a non-zero signed 32-bit integer; negative values are
     valid.
   - Message events, message metadata, and media references are persisted rather
     than kept only in an in-memory ring buffer.

5. **Actions own validation**
   - Every supported action is described in a registry with parameter rules,
     success result schema, examples, and read-only/write classification.
   - Invalid parameters fail before side effects.
   - Unsupported actions return an explicit unsupported error and never fake
     success.

6. **Network adapters are replaceable**
   - HTTP server, HTTP client, WebSocket server, and WebSocket client are
     separate adapters over the same OneBot instance.
   - Credentials and bind addresses are validated and preserved.
   - A failed transport must not silently downgrade security or switch modes.

7. **Event-driven lifecycle**
   - Do not continuously poll processes for new logins.
   - Account detection is triggered by initial/manual refresh, process changes,
     login database changes, or an explicit WebUI action.
   - Process facts are diagnostics; only a confirmed native login marks a bridge
     online.

8. **Safe diagnostics**
   - Tokens, cookies, private account data, QR data, raw XML, CDN keys, and
     message secrets are redacted from logs.
   - WebUI authentication is separate from the OneBot access token.
   - Destructive actions require explicit confirmation.

## Deliberate differences from SnowLuma

- SnowLuma targets QQ/NTQQ and uses proprietary native addons.
- This project targets WeChat's Windows client and uses the vendored helper
  native bridge, whose non-commercial license also applies.
- The SnowLuma license is source-available but restricts commercial use and
  public derivative distribution. This project studies only its architecture and
  does not copy SnowLuma source code.

## Migration status

Completed:

- live helper bridge preflight
- normalized bridge events
- OneBot v11 HTTP/forward WS/reverse WS prototype
- stable external identity mapping
- native group mention support
- local management page
- offline tests

Completed in the current migration slice:

- account-level Bridge session manager with PID/account isolation
- per-account Runtime and isolated identity/message/media directories
- durable per-account SQLite message store
- non-zero signed 32-bit message IDs, including negative values
- action registry with parameter validation, result metadata and examples
- `protocol` and `onebot.network` package boundaries
- separate WebUI token from the OneBot access token
- explicit account discovery with no periodic polling loop

Completed in the network/WebUI slice:

- per-account network configuration and server/client adapter lifecycle
- HTTP client event reporting
- WebUI session authentication and SSE state stream
- multi-account RuntimeManager exposed through account-scoped API routes

Completed in the live inbound slice:

- second-account private text, image and file validation
- real native group mention validation
- account-specific selection when two WeChat instances are running
- two-stage media handoff from WeChat temp into the isolated project workspace
- provisional monitor event suppression before final media delivery

Completed in the management slice:

- SnowLuma-style light blue theme, optional dark theme, expandable sidebar, and
  six main views: overview, processes, configuration, logs, debug, settings
- account-scoped configuration tabs for general settings and four OneBot network
  transports; saved settings are shown even before the Runtime restarts
- read-only process snapshots, explicitly requested login scan, account
  selection, friend/group/capability views, and Runtime start/stop controls
- bounded, redacted runtime diagnostic summaries, recent OneBot events, storage
  statistics, action tester, message composer, activity view, and API catalog
- write-action confirmation before a real send

The interface follows SnowLuma's layout and information architecture, while
every actionable feature here maps to an existing WeChat or OneBot v11 API.
Several settings panels intentionally display protocol facts or link to the
corresponding configuration page; there is no QQ plugin, model, or framework
management layer in this protocol-only project.

Still deferred:

- backup/restore UI
- full action catalog parity and group administration
