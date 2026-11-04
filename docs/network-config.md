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

```json
{
  "heartbeatIntervalSeconds": 5,
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
      "enabled": true
    }
  ],
  "wsClients": [
    {
      "name": "reverse",
      "url": "ws://127.0.0.1:8080/onebot/v11/ws",
      "accessToken": "reverse-token",
      "reconnectSeconds": 5,
      "enabled": false
    }
  ]
}
```

## Rules

- Endpoint names must be non-empty and unique within each list.
- Duplicate server `host:port` pairs are rejected across both server lists, including disabled entries. Wildcard binds and separate accounts can also conflict at runtime; different paths do not allow sharing a listener.
- Access tokens are independent per endpoint. An omitted token defaults to an empty string; an empty server token disables action authentication. Configure non-empty credentials before exposing an endpoint.
- HTTP client events use `Authorization: Bearer <accessToken>` when the token is non-empty. Reporting is outbound event delivery, not an action server or a durable retry queue.
- Reverse WS clients send `Authorization: Bearer <accessToken>` when non-empty during the handshake, plus `X-Self-ID` and `X-Client-Role: Universal`. HTTP and forward WS action endpoints accept a Bearer token or the `access_token` query parameter.
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
| `heartbeatIntervalSeconds` | 5 seconds when parsing; a newly created account inherits the main config value | 0.5–300 seconds |
| Endpoint lists | `[]` | Arrays |
| `enabled` | `true` | JSON boolean |
| Server `host` | `127.0.0.1` | Non-empty string |
| Server `port` | Required | Integer 1–65535 |
| HTTP / WS server `path` | `/` / `/ws` | Starts with `/`, contains no NUL |
| `accessToken` | Empty string | String |
| HTTP client `timeoutSeconds` | 10 seconds | 0.5–120 seconds |
| WS client `reconnectSeconds` | 5 seconds | 0.5–300 seconds; retries back off after connection failures |
| Client `url` | Required | HTTP(S) for HTTP clients, WS(S) for WS clients |

The example file contains placeholder tokens. Automatic main-config creation
uses random tokens, but neither case supplies tokens for new account endpoints.

## Media URLs

A forward or reverse WS connection does not serve media files. Account media URLs
use the first enabled HTTP server, falling back to the legacy main-config
`onebot.host:port` if none is enabled. Wildcard hosts are converted to loopback.
That fallback does not start a listener. Configure an HTTP server and verify that
the resulting URL is reachable from the consumer; loopback refers to the
consumer's own machine when it runs elsewhere. The HTTP adapter's `/healthz` and
`/media/{media_id}/{name}` routes do not use the action-token check.

Implementation references: [network config](../src/wechat_onebot/onebot/network/config.py),
[network manager](../src/wechat_onebot/onebot/network/manager.py),
[account runtime](../src/wechat_onebot/core/runtime.py), and
[network tests](https://github.com/PorkLittleHamburger/wechatbot/blob/main/tests/test_network_manager.py) (source checkout only).
