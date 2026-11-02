# Account Network Configuration

Each account has an isolated file:

```text
data/accounts/<account>/onebot.json
```

Legacy global `onebot` and `reverse_ws` settings are used only when this file is
created for the first time.

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

- Server names must be unique within each list.
- HTTP and WS servers cannot share the same `host:port`.
- Access tokens are independent per endpoint and may be empty only on trusted localhost setups.
- HTTP client events use `Authorization: Bearer <accessToken>`.
- Reverse WS clients send `Authorization: Bearer <accessToken>` during the handshake.
- Network changes from WebUI are saved first and applied after the account Runtime restarts.
- An unsupported or invalid network configuration is rejected rather than silently replaced.

## Defaults

- OneBot HTTP: `http://127.0.0.1:6700/`
- OneBot WS: `ws://127.0.0.1:6701/ws`
- WebUI: `http://127.0.0.1:5099/`
- Additional accounts get deterministic, non-default ports unless their `onebot.json` is edited.
