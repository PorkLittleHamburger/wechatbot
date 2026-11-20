"""Fail-closed extraction of the sender display name from SDK notification text."""

from __future__ import annotations

import pytest

from wechat_onebot.notification_names import (
    MAX_NOTIFICATION_NAME_LENGTH,
    extract_notification_name,
    validate_display_name,
)


@pytest.mark.parametrize(("content_type", "notification_text", "expected"), [
    # "name : preview" templates
    ("text", "张三 : 你好", "张三"),
    ("text", "李 四 : 你好 : 世界", ""),
    ("reference", "张三 : 原始引用文本", "张三"),
    ("reference", "张三 : 引用一\n第二行", "张三"),
    ("file", "张三 : [文件]季度报告.pdf", "张三"),
    ("app", "张三 : [文件]季度报告.pdf", "张三"),
    ("forwarded", "张三 : [聊天记录]", "张三"),
    # suffix templates
    ("image", "张三在群聊中发了一张图片", "张三"),
    ("voice", "张三在群聊中发了一段语音", "张三"),
    ("video", "张三在群聊中发了一段视频", "张三"),
    # trimming and non-CJK names
    ("text", "  Alice : hi  ", "Alice"),
    ("text", "🙂小明 : 你好", "🙂小明"),
    ("text", "A·B-1_2 : hi", "A·B-1_2"),
])
def test_supported_templates_extract_the_sender_name(content_type, notification_text, expected):
    assert extract_notification_name(content_type, notification_text, "wxid_a") == expected


@pytest.mark.parametrize(("content_type", "notification_text"), [
    # Not a supported template
    ("link", "张三 : [链接]标题"),
    ("emoticon", "张三在群聊中发了一个表情"),
    ("system", "张三 : 系统消息"),
    ("unknown", "张三 : 你好"),
    # Separator ambiguity / missing body
    ("text", "张三 : "),
    ("text", " : 你好"),
    ("text", "张三 : 你好 : 世界"),
    ("text", "张三\u2005: 你好"),
    ("text", "张三: 你好"),
    # File cards only
    ("file", "张三 : [链接]标题"),
    ("app", "张三 : [链接]标题"),
    ("app", "张三 : 一张图片"),
    ("file", "张三 : [文件]"),
    ("file", "张三 : [文件]   "),
    # Forwarded body must be exact
    ("forwarded", "张三 : [聊天记录]x"),
    # Suffix templates must be exact and unambiguous
    ("image", "张三发了一张图片"),
    ("image", "在群聊中发了一张图片在群聊中发了一张图片"),
    ("voice", "张三在群聊中发了一段语音在群聊中发了一段语音"),
    ("video", "张三在群聊中发了一段视频，请查收"),
    # Empty/None/non-string
    ("text", ""),
    ("text", "   "),
    ("text", None),
    ("image", None),
])
def test_unsupported_or_ambiguous_notifications_learn_nothing(content_type, notification_text):
    assert extract_notification_name(content_type, notification_text, "wxid_a") == ""


@pytest.mark.parametrize("name", [
    "张三",                                                        # normal
    "a" * MAX_NOTIFICATION_NAME_LENGTH,                            # upper bound
])
def test_valid_display_names_are_kept(name):
    assert validate_display_name(name) == name


@pytest.mark.parametrize(("value", "sender_id"), [
    ("", "wxid_a"),
    ("   ", "wxid_a"),
    ("a" * (MAX_NOTIFICATION_NAME_LENGTH + 1), "wxid_a"),
    ("张\n三", "wxid_a"),           # internal C0 control
    ("张三\u0000", "wxid_a"),
    ("\x7f张三", "wxid_a"),
    ("\ud800张三", "wxid_a"),        # broken UTF-16 decoding
    ("wxid_a", "wxid_a"),           # a wxid is never a display name
    (None, "wxid_a"),
    (123, "wxid_a"),
])
def test_invalid_display_names_are_rejected(value, sender_id):
    assert validate_display_name(value, sender_id=sender_id) == ""


def test_surrounding_whitespace_is_trimmed_not_rejected():
    assert validate_display_name("  张三  ") == "张三"
    assert validate_display_name("张三\n") == "张三"


def test_name_that_equals_the_sender_id_is_rejected_only_for_that_sender():
    assert extract_notification_name("text", "wxid_a : hi", "wxid_a") == ""
    assert extract_notification_name("text", "wxid_a : hi", "wxid_b") == "wxid_a"


def test_notification_bodies_are_never_trimmed_into_a_name():
    # A preview whose body is only the separator must not yield a name.
    assert extract_notification_name("text", "李四 :  ", "wxid_a") == ""
    assert extract_notification_name("reference", "李四 :  ", "wxid_a") == ""
