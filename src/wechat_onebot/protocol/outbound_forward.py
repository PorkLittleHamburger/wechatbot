"""Bounded expansion of forward-send extensions to ordinary message chains."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .codec import normalize_message, parse_cq

MAX_NODES = 200
MAX_DEPTH = 8
MAX_SEGMENTS = 2000
MAX_TEXT_BYTES = 256 * 1024
MAX_MEDIA = 16
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_NATIVE_SENDS = 512


class ForwardSendError(ValueError):
    def __init__(self, message: str, retcode: int = 1400):
        super().__init__(message)
        self.retcode = retcode


@dataclass(frozen=True)
class ForwardPlan:
    chains: list[list[dict[str, Any]]]
    node_count: int


def expand_forward(
    messages: Any, *, lookup_message: Callable[[int], dict | None],
    lookup_forward: Callable[[str], dict | None],
) -> ForwardPlan:
    """Resolve only caller-authorized account-local IDs, never execute sends.

    Nodes may contain CQ text, arrays, nested nodes, forward references, or a
    nested ``messages`` wrapper. All wrappers and references share one budget.
    Author labels describe quoted content; they never create native mentions.
    """
    chains: list[list[dict[str, Any]]] = []
    nodes = segments = text_bytes = visited_segments = 0

    def fail(message):
        raise ForwardSendError(message)

    def rows(value):
        if isinstance(value, str):
            if len(value) > MAX_TEXT_BYTES or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
                fail("转发正文超过 256 KiB 上限")
            return parse_cq(value)
        if isinstance(value, dict):
            return [value]
        if not isinstance(value, list):
            fail("转发内容必须是 CQ 字符串、消息段对象或数组")
        return value

    def append(chain, segment):
        nonlocal segments, text_bytes
        segments += 1
        if segments > MAX_SEGMENTS:
            fail("转发消息段超过上限")
        if segment["type"] == "text":
            text_bytes += len(segment["data"]["text"].encode("utf-8"))
            if text_bytes > MAX_TEXT_BYTES:
                fail("转发正文超过 256 KiB 上限")
        chain.append(segment)

    def content(value, depth, path, label=""):
        nonlocal visited_segments
        if depth > MAX_DEPTH:
            fail("转发嵌套超过 8 层上限")
        chain: list[dict[str, Any]] = []
        labeled = False

        def flush():
            nonlocal chain, labeled
            if chain:
                chains.append(chain)
                chain = []
                labeled = False

        for row in rows(value):
            if isinstance(row, dict) and set(row) == {"messages"}:
                flush()
                nested_nodes(row["messages"], depth + 1, path)
                continue
            if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
                fail("转发消息段必须包含 type 和 data 对象")
            kind, data = row.get("type"), row["data"]
            if kind == "node":
                flush()
                node(row, depth + 1, path)
                continue
            if kind == "forward":
                flush()
                forward_id = data.get("id")
                if not isinstance(forward_id, str) or not 0 < len(forward_id) <= 128:
                    fail("转发引用需要有效 id")
                key = ("forward", forward_id)
                if key in path:
                    fail("转发引用存在循环")
                stored = lookup_forward(forward_id)
                if stored is None:
                    raise ForwardSendError("转发引用不在当前账号存储中", 1404)
                nested_nodes(stored["message"], depth + 1, (*path, key))
                continue
            visited_segments += 1
            if visited_segments > MAX_SEGMENTS:
                fail("转发消息段超过上限")
            normalized = normalize_message(row)
            if all(segment["type"] == "text" and segment["data"]["text"] == "" for segment in normalized):
                continue
            if label and not labeled:
                append(chain, {"type": "text", "data": {"text": f"[转发内容 · {label}]\n"}})
                labeled = True
            for segment in normalized:
                append(chain, segment)
        flush()

    def node(row, depth, path):
        nonlocal nodes
        if depth > MAX_DEPTH:
            fail("转发嵌套超过 8 层上限")
        if not isinstance(row, dict) or row.get("type") != "node" or not isinstance(row.get("data"), dict):
            fail("messages 必须由 node 消息段组成")
        nodes += 1
        if nodes > MAX_NODES:
            fail("转发节点超过 200 条上限")
        data = row["data"]
        if "content" in data:
            if data.get("id") not in (None, "", 0, "0"):
                fail("节点不能同时指定消息 id 与 content")
            label = data.get("nickname", data.get("name", ""))
            if label is None:
                label = ""
            if not isinstance(label, str) or len(label) > 128:
                fail("节点作者名称必须是不超过 128 字符的字符串")
            label = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in label).strip()
            before = len(chains)
            content(data["content"], depth, path, label)
            if len(chains) == before:
                fail("转发节点没有可发送内容")
            return
        value = data.get("id")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            fail("引用节点需要非零 signed int32 消息 id")
        spelling = str(value)
        if not spelling.isascii() or not spelling.removeprefix("-").isdecimal() or len(spelling) > 11:
            fail("引用节点需要非零 signed int32 消息 id")
        message_id = int(spelling)
        if not -(2**31) <= message_id <= 2**31 - 1 or message_id == 0:
            fail("引用节点需要非零 signed int32 消息 id")
        key = ("message", message_id)
        if key in path:
            fail("消息引用存在循环")
        stored = lookup_message(message_id)
        if stored is None:
            raise ForwardSendError("节点原消息不在当前账号存储中", 1404)
        sender = stored.get("sender") or {}
        label = sender.get("nickname", "")
        content(stored["message"], depth, (*path, key), str(label)[:128])

    def nested_nodes(value, depth, path):
        if not isinstance(value, list) or not value:
            fail("messages 必须是非空 node 数组")
        for row in value:
            node(row, depth, path)

    nested_nodes(messages, 1, ())
    if not chains:
        fail("转发没有可发送内容")
    return ForwardPlan(chains, nodes)
