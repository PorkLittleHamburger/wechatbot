"""OneBot inbound authorization header parsing.

Accepted forms (U-05a):

- ``Authorization: Bearer <token>`` (standard)
- ``Authorization: <token>`` (bare token, used by some QQ Lite clients)
- ``?access_token=<token>`` (handled by the adapters)

A header with whitespace that is neither ``Bearer`` nor a bare single token is
ignored, so ``Basic ...`` credentials fail closed instead of being compared as a
token.
"""
from __future__ import annotations

import hmac


def bearer_token(header: str | None) -> str:
    if not header:
        return ""
    scheme, separator, token = header.partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    value = header.strip()
    return value if value and " " not in value else ""


def token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
