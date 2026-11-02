# Architecture

## Layers

```text
WeChat 4.1.15.x (Windows x64)
        |
        v
Bridge package
  process/account discovery
  per-account HelperBridgeSession
  native DLL lifecycle + normalized bridge events
  (`driver` is the low-level helper/mock transport implementation)
        |
        v
Protocol package
  event normalization
  identity resolution
  conversion rules
        |
        v
OneBot package
  action registry + validation
  durable message/media stores
  per-account network manager
        |
        +--> HTTP API server
        +--> HTTP event client
        +--> Forward WebSocket server
        +--> Reverse WebSocket client

Core
  RuntimeManager + per-account AccountRuntime

WebUI
  session-cookie login + SSE state stream + account action proxy
```

Each layer owns its account state and lifecycle. External bot applications connect through OneBot endpoints; bridge and management details remain inside the gateway.

## Non-negotiable rules

1. Message sending must use the native helper service. No OCR, screenshots, coordinate clicks, clipboard, or keyboard simulation.
2. Unsupported OneBot actions must fail explicitly. They must never return a fake success.
3. OneBot IDs are persisted. Reconnecting or restarting must not silently move the same `wxid` or `chatroom` to a different numeric ID.
4. A stale driver session must cancel old send tasks instead of sending through a reconnected account.
5. Every send result must be based on the native service result, not merely on request submission.

## ID mapping

- `wxid` and `chatroom` IDs are external identifiers.
- OneBot `user_id` and `group_id` are stable positive integers; message IDs are non-zero signed 32-bit integers.
- Mappings are stored per account in `data/accounts/<account>/ids.sqlite3`.
- Deterministic hashes are used first; collisions are resolved by sequential probing and stored permanently.

## Driver contract

The OneBot layer depends only on the Bridge session interface; the current native implementation is the helper adapter.

Required message capabilities:

- private/group text receive and send
- native group mention
- image receive/send
- file receive/send
- voice send
- emoticon send
- friend and group metadata
- recall notification where the backend provides it

Unavailable capabilities remain visible in the capability matrix.

## Inbound media handoff

The native download task runs inside the non-elevated WeChat process, while the
helper client must be elevated. Direct cross-integrity writes into the project
workspace are therefore not part of the contract. Image and file downloads use
a two-stage handoff:

1. the native task writes into a fresh file under the current WeChat account's
   own `temp` directory;
2. the elevated helper validates the result, copies it into the account-isolated
   media workspace, removes the native temporary file, and only then emits the
   final OneBot `image` or `file` segment.

The account data directory is detected from the running WeChat process and
account ID. `driver.wechat_data_directory` is an optional explicit override.

## Protocol

Core action surface:

- `send_private_msg`
- `send_group_msg`
- `send_msg`
- `get_login_info`
- `get_status`
- `get_version_info`
- `can_send_image`
- `can_send_record`
- `get_friend_list`
- `get_group_list`
- `get_group_info`
- `get_group_member_list`
- `get_group_member_info`
- `get_msg`

Unsupported methods return `retcode=1404`.
