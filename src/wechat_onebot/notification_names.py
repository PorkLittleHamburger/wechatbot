"""Fail-closed sender display-name extraction from SDK notification text.

``wechatbot_api`` 0.1.0.dev18 (bridge 11 / ABI 2) attaches two read-only fields
to group message events:

* ``sender_display_name`` — filled only when the SDK's strict parser matched
  ``<name>在群聊中@了你`` for *this* account (group text message, attributable
  author, this account in ``atuserlist``); it is already trimmed, control-free
  and at most 128 characters.
* ``notification_text`` — the raw group-notification preview.  For messages
  that do not mention this account it is a client-rendered preview (for
  example ``<name> : <body>``); on WeChat 4.2.0.52 it is always empty.

Neither value is an identity: the extracted name is only the *display* name of
the message author in that group.  It is used for the inbound ``sender.card``
and, at most, as an **unverified** outbound group-``@`` label for that same
sender in that same group.

Privacy: ``notification_text`` may contain message previews, so the raw string
must stay inside the driver conversion stack.  It is never logged, stored or
copied into an event payload; only the extracted name leaves this module.

Recognition is deliberately narrow: only the fixed templates below are
accepted.  Unknown clients/languages, extra separators, empty bodies and
self-identifying names yield ``""`` (fail closed) instead of guessing.
"""
from __future__ import annotations

MAX_NOTIFICATION_NAME_LENGTH = 64

#: Client-rendered separator of the "name : preview" notification formats.
_SEPARATOR = " : "
#: ``<name> : [文件]<file name>`` (group file card; also the ``app`` twin event).
_FILE_BODY_PREFIX = "[文件]"
#: ``<name> : [聊天记录]`` (forwarded chat history).
_FORWARD_BODY = "[聊天记录]"
#: ``<name>在群聊中发了一张图片`` style suffixes (no separator).
_SUFFIX_TEMPLATES = {
    "image": "在群聊中发了一张图片",
    "voice": "在群聊中发了一段语音",
    "video": "在群聊中发了一段视频",
}
#: ``content_type`` values that use the "name : body" preview format.
_PREFIX_TYPES = frozenset({"text", "reference", "file", "app", "forwarded"})


def _has_forbidden_characters(value: str) -> bool:
    """Reject C0 controls, DEL and lone UTF-16 surrogates (broken decoding)."""
    for character in value:
        code = ord(character)
        if code < 32 or code == 127 or 0xD800 <= code <= 0xDFFF:
            return True
    return False


def validate_display_name(value: object, *, sender_id: str = "") -> str:
    """Return a usable display name, or ``""`` when the value is not safe.

    Applied to both the SDK's strict ``sender_display_name`` and to names
    extracted from a notification template, so callers get one contract:
    trimmed, 1..64 characters, no control characters, and never the raw wxid.
    """
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or len(name) > MAX_NOTIFICATION_NAME_LENGTH:
        return ""
    if _has_forbidden_characters(name):
        return ""
    if sender_id and name == sender_id:
        return ""
    return name


def _extract_prefix_name(content_type: str, notification_text: str, sender_id: str) -> str:
    if notification_text.count(_SEPARATOR) != 1:
        # Ambiguous: a multi-separator preview cannot be split reliably.
        return ""
    name, _, body = notification_text.partition(_SEPARATOR)
    if not name or not body:
        return ""
    if content_type in ("file", "app"):
        # ``app`` events also exist for links/mini-programs; only the file card
        # preview is a stable sender-name carrier.  The file card's ``app`` and
        # ``file`` twins carry the same text, so re-learning is a no-op.
        if not body.startswith(_FILE_BODY_PREFIX):
            return ""
        if not body[len(_FILE_BODY_PREFIX):].strip():
            return ""
    elif content_type == "forwarded":
        if body != _FORWARD_BODY:
            return ""
    return validate_display_name(name, sender_id=sender_id)


def _extract_suffix_name(suffix: str, notification_text: str, sender_id: str) -> str:
    if not notification_text.endswith(suffix):
        return ""
    name = notification_text[:-len(suffix)]
    if not name or suffix in name:
        # A name that still contains the suffix phrase is a rendering echo,
        # not a display name.
        return ""
    return validate_display_name(name, sender_id=sender_id)


def extract_notification_name(content_type: str, notification_text: object,
                              sender_id: str) -> str:
    """Return the sender display name carried by a group notification preview.

    ``notification_text`` is only read here; callers must not persist or log it.
    """
    if not isinstance(notification_text, str) or not notification_text.strip():
        return ""
    text = notification_text.strip()
    if content_type in _PREFIX_TYPES:
        return _extract_prefix_name(content_type, text, sender_id)
    suffix = _SUFFIX_TEMPLATES.get(content_type)
    if suffix is None:
        return ""
    return _extract_suffix_name(suffix, text, sender_id)
