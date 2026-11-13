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
      "accessToken": "onebot-token",
      "enabled": true
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
      "accessToken": "onebot-token",
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
- Access tokens are independent per endpoint. An omitted token defaults to an empty string; an empty server token disables action authentication. Configure non-empty credentials before exposing an endpoint.
- HTTP client events include `X-Self-ID`, plus `Authorization: Bearer <accessToken>` when non-empty. Optional `secret` adds HMAC-SHA1 `X-Signature`. Response JSON can request standard quick replies; unsupported native operations are logged. Reports are not a durable retry queue. HTTP POST and quick operations finish before WS broadcast.
- Reverse WS clients send `Authorization: Bearer <accessToken>` when non-empty during the handshake, plus `X-Self-ID` and the configured `X-Client-Role` (`Universal`, `API`, or `Event`). HTTP and forward WS action endpoints accept a Bearer token or the `access_token` query parameter.
- `maxMessageBytes` limits one WS frame/message in both directions: forward WS servers use it as uvicorn `ws_max_size`, reverse WS clients pass it to `websockets.connect`. The default is **128 MiB**, configurable 1–512 MiB. It exists for clients that inline long audio as `base64://` (AstrBot's `Record` adapter can produce 30 MB+ frames); frames over the limit close with `1009 (message too big)` and the client's API call times out. Raise it only for trusted local/private endpoints because each frame can consume comparable memory. The WebUI exposes the field for both WS endpoint types.
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
| WS client `reconnectSeconds` | 5 seconds | 0.5–300 seconds; retries back off after connection failures |
| WS server/client `maxMessageBytes` | 134217728 (128 MiB) | Integer 1–512 MiB; per-message frame limit for large base64 media |
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
