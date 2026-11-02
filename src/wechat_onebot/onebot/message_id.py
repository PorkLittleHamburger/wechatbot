from __future__ import annotations

import hashlib
import time
from typing import Any


def hash_message_id_int32(sequence: int | str, session_id: int | str, event_name: str, salt: int = 0) -> int:
    """Return a deterministic non-zero signed 32-bit OneBot message ID.

    Negative values are intentional and valid. This follows the same contract
    observed in SnowLuma's message identity design without copying its code.
    """
    key = f"{sequence}:{session_id}:{event_name}:{salt}"
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], byteorder="big", signed=True)
    return value or 1


def message_event_name(message: Any) -> str:
    if getattr(message, "is_group", False):
        return "group_message"
    return "private_message_sent" if getattr(message, "is_self", False) else "private_message"


def message_sequence(message: Any) -> str:
    sequence = getattr(message, "sequence", 0) or 0
    if sequence:
        return str(sequence)
    external = getattr(message, "external_message_id", "") or ""
    if external:
        return str(external)
    timestamp = getattr(message, "timestamp", 0) or int(time.time())
    text = "".join(segment.data.get("text", "") for segment in getattr(message, "segments", ()) if segment.type == "text")
    return f"{timestamp}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def sent_message_event_name() -> str:
    return "private_message_sent"


def sent_message_sequence(nonce: str) -> str:
    return f"sent:{nonce}"
