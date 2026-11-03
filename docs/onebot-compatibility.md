# OneBot v11 Compatibility

## Transport

- HTTP API: `POST /` with `{"action": "...", "params": {...}, "echo": "..."}`
- New accounts have no default OneBot listeners. HTTP and WS addresses are configured per account in `onebot.json` or WebUI.
- Convenience HTTP route: `POST /{action}` with a params object body
- Forward WebSocket: the configured endpoint path, for example `/ws`
- Reverse WebSocket: per-account `wsClients`; HTTP event reporting: `httpClients`
- Authentication: `Authorization: Bearer <token>` or `?access_token=<token>`
- WebUI authentication: first-login token and management password, followed by an independent HttpOnly session Cookie; OneBot access tokens cannot authenticate WebUI sessions
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
| `get_group_list` | Implemented | groups observed by the current native session |
| `get_group_info` | Implemented | observed group metadata |
| `get_group_member_list` | Implemented | requires a ready WeChat group-member cache |
| `get_group_member_info` | Implemented | based on group-member cache |
| `get_msg` | Implemented | durable per-account SQLite storage; signed 32-bit IDs |
| `get_friend_msg_history` | Implemented | local per-account store, optional `message_id` cursor |
| `get_group_msg_history` | Implemented | local per-account store, optional `message_id` cursor |
| `can_send_image` | Implemented | capability based |
| `can_send_record` | Implemented | capability based |
| `get_capabilities` | Extension | driver and action capability matrix |

## Explicitly unsupported

The following actions return `retcode=1404` and never report fake success:

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

## Inbound message segments

- Private and group text maps to `text`.
- Native WeChat `@` maps to an `at` segment and sets `to_me=true` when the bot is selected.
- A successfully downloaded image maps to `image` with a local `file` name and HTTP `url`.
- A successfully downloaded attachment maps to `file` with sanitized `name`, local `file`, and HTTP `url`.
- Unsupported downloads are explicit failure states; the adapter never fabricates a ready media path.

## Message segment rules

- A `reply` segment is accepted as metadata when the chain contains sendable content. The native bridge cannot create a quoted WeChat reply, so the quote is omitted; group `at` remains a real native mention. A reply-only chain fails without sending.
- `node`, `forward`, `json`, `xml`, `video`, `contact`, `music` are rejected as unsupported.
- `at` is group-only. `@全体成员` is unsupported.
- `face` requires a local file, URL, or `base64://` payload. QQ face IDs are not guessed.
- Remote media URLs are downloaded with a size limit before native sending.
- Every send segment is validated before the first side effect where possible.
- If a later segment fails after earlier segments have been sent, the failed response includes `sent_segments` in `data`.
