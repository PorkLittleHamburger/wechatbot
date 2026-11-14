# OneBot v11 Compatibility

The core interface targets the official OneBot v11 contract and does not require AstrBot, NoneBot, or a proprietary client SDK. This is a supported capability subset, not an implementation of every QQ API. Native operations unavailable in WeChat fail explicitly. Local administration and optional extensions remain outside the standard.

## Communication

- HTTP API: `GET` or `POST /{action}` and `/{action}/`. Parameters accept query strings, URL-encoded forms, or JSON objects. String booleans such as `"false"` are converted by the action schema.
- The configured HTTP endpoint also retains the gateway envelope extension: `{"action":"get_status","params":{},"echo":null}`. Standard clients do not need it.
- HTTP authentication: missing token is 401, wrong token 403; unsupported POST media type 406, malformed body 400, unknown action 404. Execution failures from registered actions retain HTTP 200 and carry a failed OneBot response.
- HTTP event POST includes `X-Self-ID`. Optional `httpClients[].secret` enables HMAC-SHA1 `X-Signature` over the actual UTF-8 body. Empty/204 responses do nothing; JSON quick replies use the validated send path. Group replies default to a real mention of the sender. Quick-operation fields are independent: a supported `reply` is still sent when the same body also contains unsupported `delete`/`kick`/`ban`/`approve`, and unsupported fields are logged instead of discarding the reply.
- HTTP POST processing completes before WS event broadcast. HTTP reports receive lifecycle `enable`/`disable`; each WS event-capable connection receives `connect`. Enable is queued without blocking startup; shutdown/removal grants reporting a two-second drain, then cancels pending HTTP work. An explicit zero report timeout still waits for the backend during ordinary event ordering, but endpoint removal remains available.
- Forward WS offers standard `/` (Universal), `/api` (API only), `/event` (Event only), with optional trailing slashes on role paths. A custom Universal path such as `/ws` also offers `/ws/api` and `/ws/event`.
- Reverse WS supports `role: "Universal"`, `"API"`, or `"Event"` with matching `X-Client-Role` and `X-Self-ID`. Clean closes wait the reconnect interval.
- Forward and reverse WS accept frames up to `maxMessageBytes` (default 128 MiB, configurable 1–512 MiB). This accommodates clients such as AstrBot that inline long audio as `base64://` WAV in a single API call; a frame over the limit is closed with `1009 (message too big)` before the action can run, which the client reports as an API timeout.
- WS `echo` is returned unchanged, including explicit `null`, false, arrays and objects. Invalid requests return failed results without closing a healthy connection.
- All registered actions accept `_async` and `_rate_limited` suffixes. Accepted work returns `status=async`, `retcode=1`, `data=null`. Rate-limited work runs in order at `rateLimitIntervalMs` (default 500); queued rate-limited work is cancelled on shutdown, while in-flight native sends are drained before helper closure. Failures are logged; asynchronous acceptance is not a send receipt.

New accounts have no network endpoints until configured. Tokens are per endpoint; WebUI password/cookie authentication is separate. See [network configuration](network-config.md).

## Supported actions

| Standard actions | Scope |
| --- | --- |
| `send_private_msg`, `send_group_msg`, `send_msg` | Supported segments below; native sends may fail offline |
| `get_msg` | Account-scoped stored messages and quote snapshots; signed int32 IDs |
| `get_forward_msg` | Standard `data.message` node array, including old stored forwards |
| `get_login_info`, `get_status`, `get_version_info` | Current account, native connectivity, app/protocol version |
| `get_friend_list`, `get_stranger_info` | Native contacts / observed per-account profiles |
| `get_group_list`, `get_group_info` | Currently readable groups; missing group fails rather than returning fabricated metadata |
| `get_group_member_list`, `get_group_member_info` | Native roster, or observed membership when the roster is unreadable |
| `get_image`, `get_record` | Resolve a received media content ID/filename; `get_record` converts stored SILK/WAV/other audio to `mp3`, `amr`, `wma`, `m4a`, `spx`, `ogg`, `wav` or `flac` |
| `can_send_image`, `can_send_record` | Online state and actual driver capability |

Optional extensions: `get_friend_msg_history`, `get_group_msg_history`, `get_capabilities`, `send_group_forward_msg`, `send_private_forward_msg`. They are not prerequisites for standard message/event delivery. Histories only read local observations, not arbitrary PC WeChat history.

Unregistered actions fail explicitly (`1404` via WS; HTTP 404). These include native recall, QQ group management, QQ credentials, friend-request approval and native outbound merged-forward cards. `send_group_forward_msg` and `send_private_forward_msg` are community extensions, not mandatory official v11 public APIs. In v0.9.2 source they are registered and explicitly downgrade to ordinary messages as described below.

## Messages and media

- Sending accepts CQ strings, arrays, or one segment object. CQ text and parameters are escaped separately; `auto_escape=true` sends a string literally. Incoming `raw_message` preserves the full CQ representation, including mentions, media and quotes.
- Standard `text`, `image`, `record` sending and group `at` are supported. `record` accepts SILK v3 as-is and transcodes WAV, MP3, AAC/M4A, OGG/Opus, AMR-NB/WB and FLAC to Tencent SILK v3 via the bundled ffmpeg and `silk-python`. WeChat voice bubbles are capped at 60 seconds, so longer audio is decoded once to 24 kHz mono (dropping a high source bitrate) and split into consecutive native voice bubbles of at most 60 seconds; undecodable or over-size input still fails explicitly. Mention-only chains use native mentions; `qq="all"` remains unsupported. Unknown targets fail; visible `@name` text never substitutes for a native mention.
- Standard `music` sending is accepted as a degraded media request. A `custom` music segment with `audio` is resolved through the normal media pipeline: up to 60 seconds it becomes one native voice message, longer audio becomes consecutive native voice bubbles. Without `audio`, a `url` becomes text with the title and link. Platform-ID-only cards (`qq`/`163`/`xiami` without a playable URL) fail explicitly because WeChat cannot render them; cover images are ignored.
- Native quoted sending is unavailable: any outbound `reply` rejects the complete chain with `1404`, before sending. Clients requesting ordinary replies must explicitly omit the segment. Inbound `reply` plus `get_msg` remains supported; when the driver reports a native message ID for a sent message, a later quote of that message resolves to the real stored sent message ID instead of a reference snapshot.
- Inbound forwarded records use `forward` and standard `get_forward_msg.message` nodes with `data.user_id`, `nickname`, and `content`. Optional `messages`, `records`, and availability metadata are supplementary. Unknown original user IDs are `"0"`; original time text is not fabricated into timestamps. See [rich messages](rich-messages.md).
- Link titles, URLs and descriptions use standard text. Received generic files use a text download link; WeChat file stickers use `image`. No incoming `file` segment or private `text.data.link` is required.
- Sending generic `file` and `face.file` remains optional legacy functionality. Standard QQ `face.id`, `share`, `video`, `contact`, `node`, `forward`, `json` and `xml` sending through ordinary send actions is explicitly unsupported (music is handled as the media degradation above). Node/forward expansion is available only through the forward-send extensions. Flash images and voice-changing options are rejected rather than ignored.
- Received media publishes a unique content ID in the standard `file` field, so different files sharing a basename still round-trip unchanged. `get_image` and `get_record` resolve that ID or the received filename; `get_record` uses the bundled ffmpeg to transcode to the requested standard format and caches the result by content hash. Media supports received filenames, media IDs, local paths/file URIs, HTTP(S), and base64. HTTP input honors `cache`, `proxy`, and positive finite `timeout`; the default download timeout is 30 seconds. Size limits always apply.
- Media registrations and URL-cache entries persist in account-local `media.sqlite3`. Ambiguous filenames fail; changed or missing files cannot be returned through old content IDs. Old unindexed account-local filenames can be recovered lazily; old external files require re-registration. Preserve the complete media directory during upgrades.
- Every segment is prepared before native sends. A later native failure reports partial `sent_segments` where available. Stored outgoing messages contain the accepted content instead of a `[sent]` placeholder. A local message ID is not a recipient receipt.

Media URLs require a reachable HTTP listener. v0.9.1 automatically uses the main gateway HTTP application for WS-only accounts, or an enabled OneBot HTTP endpoint; it no longer advertises an unbound legacy port. Missing/failed native media becomes explicit standard text, not a successful image with an empty URL.

## Events and identity

User/group mappings, message IDs, profiles, native-ID aliases and quote snapshots are account-scoped and durable. WeChat nickname and per-group card observations stay separate; empty values do not erase known names. Member lookup never infers membership from a global contact alone. Non-friend nickname reads remain restricted to the verified native cache layouts.

Group messages use `sub_type=normal`; `at.data.qq` is a string. Driver events are serialized per account in arrival order before storage and reporting, so a later message cannot overtake an earlier one while member/friend lookups are pending. `friend_recall` notices reuse the original MessageStore ID when the original private message is cached (the private peer is the author even when the helper payload omits it). `group_recall`, group member increase/decrease, group admin/ban and similar notices are not emitted: WeChat does not expose the required operator or subtype reliably, so they are logged and suppressed instead of emitting invalid events or inventing actors. Membership removal observed through other paths is still recorded locally. Unresolvable recalls, unknown notice operators/subtypes, and incomplete group requests are suppressed.

Profile APIs provide standard fields with unknown defaults (`sex=unknown`, age/timestamps 0, empty strings, conservative false values). Additional `unavailable_fields` identifies unobserved data. Unknown member role is represented as `member` for schema compatibility and marked unavailable; this is not authority to manage a group. Native cache rows may themselves use display-name fallbacks for missing cards.

`groupMentionOnly` filters only outer group message reporting. Private messages remain enabled; untriggered group messages are stored for quote resolution. Forwarded inner messages are never replayed as live events. Local WebUI/bridge/account management need not implement OneBot APIs.

## Native build and driver differences

v0.10.0 adds a second real driver. `helper` supports fingerprint-locked 4.1.15.13, 4.1.15.56, 4.2.0.11 and 4.2.0.12; `sdk` supports fingerprint-locked 4.2.0.52 through `wechatbot-api 0.1.0.dev15`. The active `driver.type` is exclusive; a build is never accepted because its version string matches.

Capabilities are queried from the running driver, not assumed:

- `helper`: text, native group `at`, image, ordinary file, voice send, emoticon send, contacts/members, inbound rich quotes and forwarded records. Ready inbound voice/video downloaded by the helper are reported as standard `record`/`video` segments; pending or failed downloads remain explicit text placeholders. Private message recall is emitted from a cached original; group recall, native forward cards and native outbound quotes remain unavailable; forward sending still converts to ordinary messages.
- `sdk`: text, native group `at` (1–2 real cached names by default; dev15 per-call `labels_verified=False` for manually bound real names), PNG image, ordinary file, WAV/SILK voice and H.264 silent video send; image/file/voice/video receive; private quoted text/image and nested forwarded media; read-only contacts, groups and members from the WeChat cache. **No** emoticon send, recall, friend request, group management, native outbound quote or native forward card. Group rich images stay behind the framework's development opt-in and are not a gateway-promised capability.
- `sdk` group mention labels: the instance WebUI can bind `(group_id, user_id)` to a real group display name. The gateway sends bound names with per-call `labels_verified=False`; cached/profiled names stay verified. Arbitrary labels can complete natively but are **not** a real @, so without a known real name the gateway degrades to visible text instead of claiming a native mention.
- `sdk` inbound voice is delivered as decoded WAV, not original SILK. Video/voice reception requires the framework's optional `video`/`voice` extras; `DEPENDENCY_MISSING` is reported, never silently downgraded.
- `sdk` directory reads are `source=wechat_cache`, `complete=False`; `LAYOUT_NOT_READY` means the cache was not readable at that moment, not that the account has no contacts.

Account discovery also follows the driver: `helper` scans its own logged-in accounts; `sdk` uses the framework's read-only `discover()`. Unsupported builds are reported with their error code instead of being treated as logged out. See [version scope](wechat-versions.md).

## Verification and limits

The [audit record](onebot-v11-audit.md) retains the original findings and resolution mapping. Regression tests include consumers that read only standard fields, CQ round trips, four transports, role isolation, signatures, quick operations, recall IDs, account isolation and media restarts. Passing them does not certify every external framework or substitute for native receive-side verification. Test sources are in the source checkout, not the portable package.

v0.9.1 repairs quoted media delivery and accepts additional unambiguous forward XML container forms. Compact quotes without embedded records and without a cached original still cannot recover historical forward contents; see [rich-message diagnostics](rich-messages.md#quote-delivery-fix).

## Forward-send conversion (v0.9.2)

`send_group_forward_msg` takes `group_id` and a nonempty `messages` node array; `send_private_forward_msg` takes `user_id` and the same array. The gateway expands nodes into ordered ordinary message chains. This is an explicit content conversion, not native WeChat merged-forward support, and requires no particular framework or threshold change.

Custom nodes use `data.content` (CQ string, segment array, or one segment) with optional `nickname` (`name` alias). Labels are rendered as `[转发内容 · author]`, never a forged native sender or native mention. Nested nodes, nested `{"messages":[...]}` containers and locally stored `forward` IDs are expanded in place. Reference nodes with `data.id` read actual stored messages from the current account; missing IDs, cycles and invalid structure fail before native sends.

Text preserves ordering, whitespace and code content and is split through the existing native UTF-8 limits (1023 bytes, or 900 with mentions). Supported media is sent as ordinary media. Unsupported segments, including outbound `reply`, are rejected rather than discarded. All structure, target mapping, media preparation and driver capability checks finish before the first native send. The Helper driver also runs its pure native-format file validators at this stage; the gateway has already transcoded common audio formats to SILK before validation. Mock input validation does not imply a live native send succeeded.

Shared limits: 200 nodes, 8 levels, 2000 visited leaf segments (including empty text), 256 KiB text including author labels, 16 media items, 64 MiB prepared media and 512 estimated native sends. Media-count overflow is rejected before resolution; downloads/base64/local/cache inputs honor the remaining byte budget per item. Exceeding a limit fails without silently truncating. A failure during sending reports accepted `sent_segments` and IDs for previously completed ordinary chains; these are not recipient delivery receipts. Do not blindly retry the full request after a partial failure.

Successful `data` contains `message_id` (the first actual stored ordinary-chain ID), `message_ids`, `forward_mode="normal_messages"`, `native_forward=false`, `node_count` and `sent_segments`. No native `forward_id` is invented. `get_msg` returns the corresponding ordinary content. `get_capabilities.forward_send` advertises the conversion and its limits. Registered actions also support the existing asynchronous/rate-limited suffixes and echo behavior.

Per-account sends are serialized so ordinary replies cannot interleave a forward conversion's chains. All sends are service-owned tasks; cancelling the caller does not cancel an already executing native thread, and shutdown drains owned work before closing helper. As with any send action, network loss or cancellation is not proof that nothing was sent.
