# Account Network Configuration

Each account has an isolated file under the configured data directory:

```text
data/accounts/<account>/onebot.json
```

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

- Server names must be unique within each list.
- HTTP and WS servers cannot share the same `host:port`.
- Access tokens are independent per endpoint. Use non-empty credentials, especially when exposing endpoints beyond localhost.
- HTTP client events use `Authorization: Bearer <accessToken>`.
- Reverse WS clients send `Authorization: Bearer <accessToken>` during the handshake.
- Network changes saved in WebUI apply immediately. Unchanged endpoints retain their connections; the WeChat bridge does not need to be started or restarted.
- Enabled saved endpoints resume with the management service. Stopping the WeChat bridge leaves the endpoints running and reports the account as offline.
- An unsupported or invalid network configuration is rejected rather than silently replaced.

## Addresses

- New installations do not listen on a OneBot HTTP or WS port until endpoints are configured.
- The standalone WebUI defaults to `http://127.0.0.1:5099/`; launcher-managed instances use their assigned WebUI ports.
- Every enabled server needs an available address and port. Separate accounts and instances must not share conflicting listeners.
