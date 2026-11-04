from copy import deepcopy

import pytest

from wechat_onebot.protocol.codec import normalize_message, parse_cq, serialize_message


def test_cq_roundtrip_escapes_text_and_parameters_once():
    message = [
        {"type": "text", "data": {"text": "[CQ:at,qq=evil] & &#91; ,"}},
        {"type": "share", "data": {"title": "a,[]=b & &#44;\nnext", "url": "https://example.com/?x=1&y=2"}},
        {"type": "at", "data": {"qq": "all"}},
        {"type": "dice", "data": {}},
    ]
    encoded = serialize_message(message)
    assert encoded.startswith("&#91;CQ:at,qq=evil&#93; &amp; &amp;#91; ,")
    assert "title=a&#44;&#91;&#93;=b &amp; &amp;#44;\nnext" in encoded
    assert parse_cq(encoded) == message


def test_normalize_all_message_representations_and_auto_escape():
    at = {"type": "at", "data": {"qq": 123}}
    original = deepcopy(at)
    expected = [{"type": "at", "data": {"qq": "123"}}]
    assert normalize_message(at) == expected
    assert normalize_message([at]) == expected
    assert normalize_message("[CQ:at,qq=123]") == expected
    assert normalize_message("[CQ:at,qq=123]&amp;", auto_escape=True) == [
        {"type": "text", "data": {"text": "[CQ:at,qq=123]&amp;"}}
    ]
    assert normalize_message(at, auto_escape=True) == expected
    assert at == original
    assert normalize_message({"type": "dice", "data": None}) == [{"type": "dice", "data": {}}]
    assert normalize_message("") == []


@pytest.mark.parametrize("value", [None, 1, True, {}, [None], ["text"],
    {"type": "at", "data": []}, {"type": "text", "data": None},
    {"type": "at", "data": {"qq": {}}}, {"type": "bad,type"},
    {"type": "at", "data": {"bad,key": 3}}])
def test_normalize_rejects_invalid_structures(value):
    with pytest.raises(ValueError):
        normalize_message(value)


@pytest.mark.parametrize("text", ["[CQ:at,qq]", "[CQ:at, qq=123]", "[CQ:at,qq=1,qq=2]", "[CQ:at,qq=123", "[CQ:]", "[CQ:text,text=literal]"])
def test_malformed_cq_remains_literal(text):
    assert parse_cq(text) == [{"type": "text", "data": {"text": text}}]


def test_cq_node_nested_content_roundtrip():
    nodes = [{"type": "node", "data": {"user_id": "0", "nickname": "name,[]",
        "content": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "&,[]"}}]}}]
    assert normalize_message(parse_cq(serialize_message(nodes))) == nodes
    assert parse_cq("&amp;#91;CQ:at,qq=1&amp;#93;") == [
        {"type": "text", "data": {"text": "&#91;CQ:at,qq=1&#93;"}}
    ]
