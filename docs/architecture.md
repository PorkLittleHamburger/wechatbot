# Architecture

## Layers

```text
WeChat 4.1.15.13 / 4.1.15.56 / 4.2.0.11 / 4.2.0.12 (fingerprint-locked Windows x64 builds)
        |
        v
Driver implementation + bridge helpers
  driver/helper.py: native service lifecycle and event conversion
  bridge/: discovery, process ownership and compatibility wrappers
        |
        v
Protocol package
  event normalization
  identity resolution
  conversion rules
        |
        v
OneBot package
  service.py: OneBotService orchestration and action handlers
  onebot/: action definitions and parameter validation
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

`AccountRuntime` composes account-owned state and lifecycle. These packages are organizational boundaries, not fully decoupled interfaces: the runtime calls `driver.create_driver`; `HelperBridgeSession` subclasses `HelperDriver`, and `OneBotInstance` aliases `OneBotService`. External bot applications connect through OneBot endpoints. The root [handover](HANDOVER.md) lists implementation entry points.

## Non-negotiable rules

1. Message sending must use the native helper service. No OCR, screenshots, coordinate clicks, clipboard, or keyboard simulation.
2. Unsupported OneBot actions must fail explicitly. They must never return a fake success.
3. OneBot IDs are persisted. Reconnecting or restarting must not silently move the same `wxid` or `chatroom` to a different numeric ID.
4. A stale driver session must cancel old send tasks instead of sending through a reconnected account.
5. Every send result must be based on the native service result, not merely on request submission.

## ID mapping

- `wxid` and `chatroom` IDs are external identifiers.
- OneBot `user_id` and `group_id` are stable positive integers; message IDs are non-zero signed 32-bit integers.
- User/group mappings are stored per account in `data/accounts/<account>/ids.sqlite3`; deterministic hashes are used first, then sequential collision probing.
- Message IDs, message history, user profiles and observed group membership are stored in `messages.sqlite3` beside that file. Message identity allocation is implemented separately in `onebot/message_id.py` and `onebot/message_store.py`.
- These are default paths; `storage.data_dir` and `storage.media_dir` are configurable relative to the configuration file's directory.

## Driver contract

`service.py` uses the `Driver` contract from `driver/base.py`; `core/runtime.py` creates helper or mock implementations. The `bridge` package provides related lifecycle/discovery helpers and compatibility names, rather than a separate transport abstraction used by every caller.

Current helper capabilities include:

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
   final standard OneBot `image` segment or text download link for a generic file.

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
- `get_stranger_info`
- `get_group_list`
- `get_group_info`
- `get_group_member_list`
- `get_group_member_info`
- `get_msg`
- `get_forward_msg` (persisted parsed inbound forwards)
- `get_friend_msg_history`
- `get_group_msg_history`
- `get_capabilities` (extension)

The authoritative action list and segment limits are in [OneBot compatibility](onebot-compatibility.md). Unsupported methods return `retcode=1404`.

## User profiles and client sessions

Inbound observations are stored before OneBot delivery; known nonempty names survive blank observations and restarts. Global nicknames and per-group cards are separate. A verified complete native member list replaces observed membership, while leave notices remove membership without erasing historical identity. Nickname lookup tries the friend cache, then the separately verified 4.1.15.56 / 4.2.0.11 / 4.2.0.12 non-friend LRU layouts. Missing or unstable native records remain unavailable.

The gateway emits separate `user_id` and `group_id` fields. External clients such as AstrBot may combine these into per-member session identifiers according to their own configuration; this does not change the gateway's group identity.

## Standard message boundary

`protocol/codec.py` normalizes CQ/string/array/object input and serializes complete `raw_message`. Parsed forwards are stored with standard `message`/`node` output plus optional compatibility metadata; old forward rows are projected at read time. The action layer rejects unavailable native semantics before sending. Protocol consumers never need a brand-specific SDK or history/capability extension to receive messages.

Media registrations and download-cache entries are stored in `media.sqlite3` within each account media directory. Native source files must remain available; content IDs are validated against changed or missing files before serving. Account data and media directories must be migrated together.
