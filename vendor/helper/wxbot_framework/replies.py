"""Bounded metadata extraction for WeChat appmsg type 57; no media requests."""
import re
import xml.etree.ElementTree as ET

from .account import validate_account


def parse_reply(content):
    if not isinstance(content, str) or len(content) > 1024 * 1024 or '\0' in content:
        return None
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', content, re.I):
        return None
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        return None
    app = root if root.tag == 'appmsg' else root.find('appmsg')
    if app is None or app.findtext('type') != '57':
        return None
    refer = app.find('refermsg')
    if refer is None:
        return None
    native_id = (refer.findtext('svrid') or '').strip()
    sender = (refer.findtext('chatusr') or refer.findtext('fromusr') or '').strip()
    if not re.fullmatch(r'[1-9][0-9]{0,19}', native_id):
        return None
    try:
        validate_account(sender)
        kind = int(refer.findtext('type') or '0')
    except ValueError:
        return None
    return {'text': app.findtext('title') or '', 'reply': {
        'native_message_id': native_id, 'sender_id': sender,
        'sender_name': (refer.findtext('displayname') or '')[:256],
        'type': kind, 'content': (refer.findtext('content') or '')[:65536],
    }}
