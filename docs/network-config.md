# Account Network Configuration

Each account has an isolated file under the configured data directory:

```text
<storage.data_dir>/accounts/<safe-account-id>/onebot.json
```

With the default storage settings this is `data/accounts/<safe-account-id>/onebot.json`.
The account directory name is sanitized by `safe_account_id`; relative storage
paths are resolved against the main configuration file directory.

New accounts start with empty endpoint lists. Add endpoints in WebUI or configure
this file while the service is stopped. The example below illustrates optional
endpoints; these addresses and tokens are not installation defaults. Replace
example tokens with independent credentials.

## Structure

`groupMentionOnly` is a strict JSON boolean, default `false`. WebUI exposes it under
Node Configuration / General Settings. Saving applies it to the selected account
without restarting its bridge: when enabled, only group messages with a native
mention of the bot are reported downstream. Private messages and non-message
events remain enabled. Filtered group messages stay in local storage for quote
resolution; outbound action calls are not restricted. This field is included in v0.9.0.

```json
{
  "heartbeatIntervalSeconds": 5,
  "rateLimitIntervalMs": 500,
  "groupMentionOnly": false,
  "httpServers": [
    {
      "name": "http",
      "host": "127.0.0.1",
      "port": 6700,
      "path": "/",
      "accessToken": "6mZq2Vt9Lk4Rw8Pc",
      "enabled": true,
      "maxBodyBytes": 104857600
    }
  ],
  "httpClients": [
    {
      "name": "reporter",
      "url": "http://127.0.0.1:8080/onebot/event",
      "accessToken": "reporter-token",
      "timeoutSeconds": 10,
      "secret": "",
      "enabled": false
    }
  ],
  "wsServers": [
    {
      "name": "ws",
      "host": "127.0.0.1",
      "port": 6701,
      "path": "/ws",
      "accessToken": "6mZq2Vt9Lk4Rw8Pc",
      "enabled": true,
      "maxMessageBytes": 134217728
    }
  ],
  "wsClients": [
    {
      "name": "reverse",
      "url": "ws://127.0.0.1:8080/onebot/v11/ws",
      "accessToken": "reverse-token",
      "reconnectSeconds": 5,
      "role": "Universal",
      "enabled": false,
      "maxMessageBytes": 134217728
    }
  ]
}
```

## Rules

- Endpoint names must be non-empty and unique within each list.
- Duplicate server `host:port` pairs are rejected across both server lists, including disabled entries. Wildcard binds and separate accounts can also conflict at runtime; different paths do not allow sharing a listener.
- Access tokens are independent per endpoint. An omitted token defaults to an empty string. For compatibility an already-saved empty token keeps working (loading never fails), but the WebUI refuses to **save a changed empty token** — or to move an existing empty-token endpoint to a non-loopback bind — unless both the management client and the listening address are loopback; a non-loopback bind must use a non-empty token. Changed tokens shorter than 16 characters, common placeholder/weak values, sequences, all-digit strings, or tokens containing the account name, endpoint name, host or port are rejected; an unchanged weak token is allowed and only logged as a warning. Manual edits of `onebot.json` are not rejected. HTTP and forward WS action endpoints accept `Authorization: Bearer <token>`, a bare `Authorization: <token>`, or the `access_token` query parameter.
- `maxBodyBytes` limits one OneBot HTTP POST body (default **100 MiB**, 1–512 MiB) and is enforced by counting actual bytes, not only Content-Length. The default stays above the base64 envelope of a 50 MiB decoded media item (about 70 MB plus JSON overhead); raise it for larger inline media or use a URL/file input instead. An oversized request returns HTTP 413 with OneBot retcode `1413`. The WebUI applies fixed limits: login/setup/change-password **16 KiB**, other JSON APIs **1 MiB**, and `/api/accounts/{account}/action` **16 MiB** so large inline media can still be sent. These limits are not configurable in this batch.
- WebUI failed logins are rate limited per client address: 5 failures within 60 seconds return `429` with `Retry-After`; a successful login clears the window. State is in memory and resets when the process restarts. The gateway does not trust `X-Forwarded-For`; behind a reverse proxy the limit applies to the proxy address, so restrict proxy exposure separately.
- HTTP responses from the WebUI and the OneBot HTTP server adapter carry `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a CSP limited to `frame-ancestors 'none'; base-uri 'self'; object-src 'none'`. WebSocket accept handshakes and WS frames are not HTTP responses and do not carry these headers. No CORS headers are added. FastAPI `/docs`, `/redoc` and `/openapi.json` are disabled on the WebUI, OneBot HTTP and OneBot WS apps.
- HTTP client events include `X-Self-ID`, plus `Authorization: Bearer <accessToken>` when non-empty. Optional `secret` adds HMAC-SHA1 `X-Signature`. Response JSON can request standard quick replies; unsupported native operations are logged. Reports are not a durable retry queue. HTTP POST and quick operations finish before WS broadcast.
- Reverse WS clients send `Authorization: Bearer <accessToken>` when non-empty during the handshake, plus `X-Self-ID` and the configured `X-Client-Role` (`Universal`, `API`, or `Event`). HTTP and forward WS action endpoints accept a Bearer token or the `access_token` query parameter.
- `maxMessageBytes` limits the raw size of one received WS frame/message: forward WS servers use it as uvicorn `ws_max_size`, reverse WS clients pass it to `websockets.connect` (`max_size`). The default is **128 MiB**, configurable 1–512 MiB. It exists for clients that inline long audio as `base64://` (AstrBot's `Record` adapter can produce 30 MB+ frames); frames over the limit close with `1009 (message too big)` and the client's API call times out. Raise it only for trusted local/private endpoints because each frame can consume comparable memory. The WebUI exposes the field for both WS endpoint types.
- This WS limit is separate from `storage.max_media_bytes` in `config.json` (default 50 MiB, 1 KiB–1 GiB). `maxMessageBytes` checks the raw WS frame (including the base64 text), while `storage.max_media_bytes` checks the **decoded media bytes** after JSON parsing; both must pass. AstrBot can therefore fit the WS frame but still be rejected with `base64 媒体数据超过大小限制`. The WebUI exposes `storage.max_media_bytes` under “系统设置 → 存储管理”; saving it writes `config.json` and updates running account media stores without restarting.
- Network changes saved in WebUI apply immediately. Unchanged endpoints retain their connections; the WeChat bridge does not need to be started or restarted.
- Enabled saved endpoints resume with the management service. Stopping the WeChat bridge leaves the endpoints running and reports the account as offline.
- Invalid JSON, a non-object root, and invalid validated fields are rejected rather than replaced with defaults. Unknown fields are ignored, and non-object entries in endpoint arrays are skipped; check spelling and saved output instead of relying on strict schema rejection.

## Addresses

- New accounts have no OneBot HTTP/WS listeners or outbound clients until their endpoints are configured. The legacy top-level `onebot.host`, `onebot.port`, `onebot.access_token` and `reverse_ws` entries in `config.example.json` do not populate these lists; only the initial heartbeat interval is inherited from `onebot.heartbeat_interval_seconds`.
- The WebUI defaults to `http://127.0.0.1:5099/`.
- Every enabled server needs an available address and port. Separate accounts and instances must not share conflicting listeners.

## Defaults and validation

| Field | Default when omitted | Validation |
|---|---|---|
| `rateLimitIntervalMs` | 500 milliseconds | Nonnegative integer; queued `_rate_limited` action interval |
| `httpClients[].secret` | Empty string | String; enables signing when nonempty |
| `wsClients[].role` | `Universal` | `Universal`, `API`, or `Event` |
| `heartbeatIntervalSeconds` | 5 seconds when parsing; a newly created account inherits the main config value | 0.5–300 seconds |
| Endpoint lists | `[]` | Arrays |
| `enabled` | `true` | JSON boolean |
| Server `host` | `127.0.0.1` | Non-empty string |
| Server `port` | Required | Integer 1–65535 |
| HTTP / WS server `path` | `/` / `/ws` | Starts with `/`, contains no NUL |
| `accessToken` | Empty string | String |
| HTTP client `timeoutSeconds` | 10 seconds | 0 for no timeout, otherwise 0.5–120 seconds |
| WS client `reconnectSeconds` | 5 seconds | 0.5–300 seconds; every retry waits this fixed interval after a failed connection or a clean close (no exponential backoff; a backoff strategy would be a separate planned change) |
| WS server/client `maxMessageBytes` | 134217728 (128 MiB) | Integer 1–512 MiB; received raw WS frame limit for large base64 media |
| HTTP server `maxBodyBytes` | 104857600 (100 MiB) | Integer 1–512 MiB; OneBot HTTP POST body limit; oversized body returns 413/retcode 1413 |
| `storage.max_media_bytes` (main config) | 52428800 (50 MiB) | Integer 1 KiB–1 GiB; decoded media limit, independent of WS frames |
| Client `url` | Required | HTTP(S) for HTTP clients, WS(S) for WS clients |

The example file contains placeholder tokens. Automatic main-config creation
uses random tokens, but neither case supplies tokens for new account endpoints.

## Media URLs

WS-only accounts still need HTTP image downloads. From v0.9.1, media URLs use the first enabled OneBot HTTP server when available; otherwise the gateway's existing main HTTP application serves `/accounts/<opaque-account-key>/media/<content-id>/<name>` on its configured WebUI host/port. The legacy top-level OneBot port is not advertised unless it has a real listener. No additional OneBot HTTP node or management login cookie is required for these read-only media URLs.

Wildcard hosts become loopback, and IPv6 addresses are bracketed. The advertised host must be reachable from the consumer; loopback only works on the same machine. Management APIs still require their normal authentication. Media routes only serve already registered resources in the selected account, validate content identity, and never create a runtime from an unknown URL.

Stored `get_msg`, `get_forward_msg` and history results refresh valid old media URLs using the current account index and publication address. Refreshing never downloads URLs and cannot recreate missing or changed files. Keep the complete media directory during upgrades.

For an existing v0.9.0 reverse-WS-only installation that reports connection refusal at its legacy OneBot port, enabling an actual loopback HTTP server on that port restores those old URLs without reinjecting WeChat. This is an operational repair for that release, not a requirement for the corrected source. Verify an actual image GET returns image bytes, rather than only checking configuration or `/healthz`.

Implementation references: [network config](../src/wechat_onebot/onebot/network/config.py),
[network manager](../src/wechat_onebot/onebot/network/manager.py),
[account runtime](../src/wechat_onebot/core/runtime.py), and
[network tests](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_network_manager.py) (source checkout only).

## Standard role and lifecycle paths

HTTP actions are available at `/{action}` with GET/POST and query, URL-encoded form or JSON parameters. The configured envelope endpoint remains an optional extension. Forward WS offers `/`, `/api`, `/event`; a custom path such as `/ws` additionally offers `/ws/api` and `/ws/event`. API connections do not receive events, and Event connections do not execute actions. HTTP POST receives lifecycle `enable`/`disable`; event-capable WS connections receive `connect`. No client brand selects behavior.

Media registrations persist in the account media directory as `media.sqlite3`. Keep that directory and source media during upgrades; a missing or changed file is unavailable even if its ID exists. Files previously registered by an older release outside the media directory need re-registration or explicit paths.

Lifecycle enable is queued without waiting for the remote backend. Disabling/removing a reporter permits a two-second drain and then cancels unfinished HTTP work. `timeoutSeconds=0` still permits an ordinary report to wait indefinitely before WS broadcast; an administrator can remove the reporter or shut down the network to recover.
