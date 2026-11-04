# OneBot v11 Compatibility

## Transport

- HTTP API: `POST <configured path>` (default `/`) with `{"action": "...", "params": {...}, "echo": "..."}`
- New accounts have no default OneBot listeners. HTTP and WS addresses are configured per account in `onebot.json` or WebUI.
- Convenience HTTP route: `POST /{action}` with a params object body; it stays at the root even when the envelope route uses a custom path
- Forward WebSocket: the configured endpoint path, for example `/ws`
- Reverse WebSocket: per-account `wsClients`; HTTP event reporting: `httpClients`
- HTTP / forward WS action authentication: `Authorization: Bearer <token>` or `?access_token=<token>`; an empty configured token disables this check. Outbound HTTP / reverse WS send the configured Bearer token when non-empty.
- WebUI authentication: the WebUI token grants a first-login setup session for setting a management password; later logins use that password. Sessions use an independent HttpOnly Cookie. OneBot tokens are not WebUI credentials.
- Heartbeat: `meta_event.heartbeat`
- Lifecycle: `meta_event.lifecycle`

## Implemented actions

| Action | Status | Notes |
|---|---|---|
| `send_private_msg` | Implemented | text, image, file, record, face-file |
| `send_group_msg` | Implemented | text, image, file, record, face-file, native `at` |
| `send_msg` | Implemented | routes by `message_type` or ID field |
| `get_login_info` | Implemented | persisted numeric self ID |
| `get_status` | Implemented | reports native driver connection state |
| `get_version_info` | Implemented | protocol v11 |
| `get_friend_list` | Implemented | native contact cache; may be incomplete |
| `get_stranger_info` | Implemented | account-scoped observed identity; see lookup limits below |
| `get_group_list` | Implemented | groups observed by the current native session |
| `get_group_info` | Implemented | observed group metadata; a mapped group absent from the current list returns empty name and zero counts |
| `get_group_member_list` | Implemented | requires a ready WeChat group-member cache |
| `get_group_member_info` | Implemented | native member list, with observed membership fallback only when that list cannot be read |
| `get_msg` | Implemented | durable per-account SQLite storage; signed 32-bit IDs |
| `get_friend_msg_history` | Implemented | local per-account store, optional `message_id` cursor |
| `get_group_msg_history` | Implemented | local per-account store, optional `message_id` cursor |
| `can_send_image` | Implemented | `yes` requires an online driver and `image.send` |
| `can_send_record` | Implemented | `yes` requires an online driver and `voice.send` |
| `get_capabilities` | Extension | driver and action capability matrix |

## Explicitly unsupported

The following actions are absent from the action registry. With valid request parameters they return `status=failed`, `retcode=1404`; they do not report fake success. This list is illustrative: every unregistered action fails the same way (a non-object `params` is rejected first with `1400`).

- `delete_msg`
- `get_forward_msg`
- `send_group_forward_msg`
- `send_private_forward_msg`
- `set_group_kick`
- `set_group_ban`
- `set_group_whole_ban`
- `set_group_admin`
- `set_group_card`
- `set_group_name`
- `set_group_leave`
- `set_essence_msg`
- `set_msg_emoji_like`
- `get_cookies`
- `get_csrf_token`
- `get_credentials`

## User lookup and role limits

`get_stranger_info` takes a positive OneBot numeric `user_id` already mapped in
this account. An unmapped ID returns `1400`; a mapped user with no observed
profile after the refresh attempt returns `1404`. A known profile can succeed
with an empty `nickname`. It returns only `user_id` and `nickname`.
`no_cache` must be a JSON boolean. If true, or if the cached nickname is missing,
the service attempts `get_friend_list` and reuses the resulting stored profile;
a driver failure during that refresh does not erase a previous observation.
This is not arbitrary stranger lookup or a forced network refresh of non-friends.

Native group member queries can expose `owner`, `admin`, `member`, or `unknown`
according to the native cache. A missing role in the Helper row defaults to
`member`. Separately, inbound group message events convert an unknown sender
role to `member` for compatibility. That event value alone is not proof of the
sender's actual permissions. A member-info response based only on observed
membership omits `role`; a successfully read native list is authoritative for
whether the user is currently included. Do not infer membership from a global
contact profile or invent permissions from a nickname/card.

## Inbound message segments

User observations are persisted in the account message database before event delivery. WeChat nicknames and per-group cards are stored separately; empty observations do not erase known names. Group-member queries and native mention display names share these records. A message-only cached member response omits an unobserved role; an available native member list remains authoritative for membership. The cache is partial and cannot supply names that have never been observed or read from native contacts/members.

The Helper member adapter falls back to the row's display nickname when its group nickname is absent. A stored `card` can therefore be that fallback label rather than an independently observed group card.

On the verified WeChat 4.1.15.56 build, nickname lookup checks the friend cache first, then the non-friend contact LRU by exact user ID. This allows cached non-friend group members to supply sender nicknames without being added as friends. It performs no network refresh and does not add these users to `get_friend_list`; unknown or unstable records remain unavailable. Other builds do not use this LRU layout.

- Private and group text maps to `text`.
- Native WeChat `@` maps to an `at` segment and sets `to_me=true` when the bot is selected.
- An image or attachment with an available path maps to `image` or `file`; successful media registration supplies a local `file` name and HTTP `url`.
- Without a path the Helper adapter may emit text placeholders such as `[图片]` or `[文件或卡片]`; inbound voice, video and emoticons are text placeholders, not native `record`, `video` or `face` media segments.
- A path is not proof of a ready download: registration errors leave the original `file` value and an empty `url`. Inspect download state and actual file content; do not count placeholders or empty URLs as successful media receipt. HTTP URL reachability also requires a suitable HTTP endpoint; see [network configuration](network-config.md#media-urls).

## Message segment rules

- A `reply` segment is accepted as metadata when the chain contains sendable content. The native bridge cannot create a quoted WeChat reply, so the quote is omitted; group `at` remains a real native mention. A reply-only chain fails without sending.
- `node`, `forward`, `json`, `xml`, `video`, `contact`, `music` are rejected as unsupported.
- `at` is group-only, uses a known numeric OneBot user ID in `data.qq`, and requires non-empty text in the chain. `qq="all"` is unsupported (`1404`); private `at` or a chain without sendable content fails with `1400`.
- Group sends pass resolved native user IDs to `send_at_text`; a visible `@name` string alone does not create a mention. Display names prefer explicit `data.name`, then stored group card/nickname, then the native ID. Inbound mention metadata identifies targets and does not itself contain their nicknames.
- String messages are sent as plain text; use segment arrays for `at`, media and reply metadata. CQ-code strings are not parsed into segments.
- `face` requires a local file, URL, or `base64://` payload. QQ face IDs are not guessed.
- Remote media URLs are downloaded with a size limit before native sending.
- Every send segment is validated before the first side effect where possible.
- If a later segment fails after earlier segments have been sent, the failed response includes `sent_segments` in `data`.

## Storage and result limits

Message history reads only the gateway's local per-account message store; it does
not fetch PC WeChat history. `count` defaults to 20 and accepts 1–100;
`message_id` is an optional nonzero signed 32-bit cursor and `reverse_order=true`
selects newer records. Outbound message records currently contain a `[sent]`
placeholder rather than the original message chain. A returned message ID is a
local gateway ID, not a recipient delivery receipt.

`get_capabilities.actions` lists registered handlers, not a promise that every
action will succeed while offline or with an unavailable native cache. Driver
errors normally return `1500`, unsupported driver operations `1404`, and invalid
parameters `1400`. Inspect the response and receive-side result for sends.

Implementation references: [action registry](../src/wechat_onebot/onebot/actions.py),
[service](../src/wechat_onebot/service.py),
[event converter](../src/wechat_onebot/protocol/converters.py),
[Helper adapter](../src/wechat_onebot/driver/helper.py),
[user profile tests](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_user_profiles.py),
[send and action tests](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_service.py), and
[non-friend cache tests](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_nonfriend_names.py). Test sources are not included in the portable package.
