"""OneBot v11 message representations and CQ escaping (no transport policy)."""
from __future__ import annotations

import re
from typing import Any

_CQ = re.compile(r"\[CQ:([^\[\]]*)\]")
_NAME = re.compile(r"[^\s,=\[\]&]+\Z")


def _escape(value: str, *, parameter: bool = False) -> str:
    value = value.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")
    return value.replace(",", "&#44;") if parameter else value


def _unescape(value: str, *, parameter: bool = False) -> str:
    # Decode ampersands last: escaped entities must not be decoded twice.
    if parameter:
        value = value.replace("&#44;", ",")
    return value.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


def parse_cq(text: str) -> list[dict[str, Any]]:
    """Parse CQ tokens; malformed tokens remain literal text, never partial actions."""
    if not isinstance(text, str):
        raise ValueError("CQ message must be a string")
    result: list[dict[str, Any]] = []
    cursor = 0
    for match in _CQ.finditer(text):
        kind, *fields = match[1].split(",")
        data: dict[str, str] = {}
        valid = bool(_NAME.fullmatch(kind)) and kind != "text"
        for field in fields:
            key, separator, value = field.partition("=")
            if not separator or not _NAME.fullmatch(key) or key in data:
                valid = False
                break
            data[key] = _unescape(value, parameter=True)
        if not valid:
            continue
        if match.start() > cursor:
            result.append({"type": "text", "data": {"text": _unescape(text[cursor:match.start()])}})
        result.append({"type": kind, "data": data})
        cursor = match.end()
    if cursor < len(text):
        result.append({"type": "text", "data": {"text": _unescape(text[cursor:])}})
    return result


def _scalar(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError("message segment parameters must be scalar values")


def normalize_message(raw: Any, auto_escape: bool = False) -> list[dict[str, Any]]:
    """Accept a CQ string, segment array, or single segment; never mutate input.

    auto_escape applies only to string input. Invalid array/object structure raises
    ValueError for the caller to translate to an action parameter error.
    """
    if isinstance(raw, str):
        return [{"type": "text", "data": {"text": raw}}] if auto_escape else parse_cq(raw)
    rows = [raw] if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("message must be a string, segment array, or segment object")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("type"), str) or not _NAME.fullmatch(row["type"]):
            raise ValueError("message segment requires a valid type")
        source = row.get("data")
        if source is None:
            source = {}
        if not isinstance(source, dict):
            raise ValueError("message segment data must be an object or null")
        data: dict[str, Any] = {}
        for key, value in source.items():
            if not isinstance(key, str) or not _NAME.fullmatch(key):
                raise ValueError("invalid message segment parameter name")
            if row["type"] == "node" and key == "content":
                data[key] = normalize_message(value)
            else:
                data[key] = _scalar(value)
        if row["type"] == "text" and "text" not in data:
            raise ValueError("text segment requires text")
        result.append({"type": row["type"], "data": data})
    return result


def serialize_message(segments: Any) -> str:
    """Serialize segments to CQ, escaping literal text and parameter values once."""
    chunks = []
    for segment in normalize_message(segments):
        kind, data = segment["type"], segment["data"]
        if kind == "text":
            chunks.append(_escape(data["text"]))
            continue
        fields = []
        for key, value in data.items():
            if kind == "node" and key == "content":
                value = serialize_message(value)
            fields.append(f",{key}={_escape(value, parameter=True)}")
        chunks.append(f"[CQ:{kind}{''.join(fields)}]")
    return "".join(chunks)


serialize_cq = serialize_message
