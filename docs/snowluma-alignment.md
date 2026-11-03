# Runtime Boundaries and Design Reference

The account-runtime boundaries use architectural principles described by
SnowLuma, with an independent Python implementation for the helper WeChat
bridge. Reference and licensing information is in
[Third-Party Notices](../THIRD_PARTY_NOTICES.md).

## Layer ownership

| Package | Responsibility |
| --- | --- |
| bridge | Process discovery, account identity, native DLL lifecycle and normalized bridge events |
| protocol | Event conversion and stable external-to-OneBot identity mapping |
| onebot | Action validation, message/media persistence and network adapters |
| core | Account runtime composition, configuration and lifecycle |
| webui | Account selection, bridge controls, network configuration and diagnostics |

External bot applications connect through OneBot v11. Reply logic, AI behavior
and business workflows belong to those external applications.

## Account isolation

Each account owns its bridge session, message store, identity mapping, media
directory and network configuration. A WeChat PID must not be bridged by more
than one session. Account mismatches and stale sessions must fail before sending.

External account and group identifiers map to persistent numeric OneBot IDs.
Message IDs are non-zero signed 32-bit integers; negative values are valid.

## Actions and transport

Supported actions define parameter validation and result metadata. Unsupported
actions return explicit errors. A successful send response must reflect the
native bridge result rather than request submission alone.

HTTP API, HTTP reporting, forward WebSocket and reverse WebSocket are separate
adapters over the same account runtime. Enabled saved endpoints start with the
management service, while the WeChat bridge is controlled separately. Saving a
network configuration applies it without restarting the bridge.

## Management and diagnostics

The WebUI provides process/account selection, bridge start/stop, storage
statistics, event logs and an action console. WebUI session authentication is
separate from OneBot credentials. Diagnostics should omit credentials and
private message content.

Supported protocol actions and current limits are listed in
[OneBot compatibility](onebot-compatibility.md).
