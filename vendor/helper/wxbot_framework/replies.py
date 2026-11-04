"""Bounded metadata extraction for WeChat appmsg type 57; no media requests."""
import re
import xml.etree.ElementTree as ET

from .account import validate_account

MAX_REPLY_XML_BYTES = 1024 * 1024


def parse_reply(content):
    if not isinstance(content, str) or len(content) > MAX_REPLY_XML_BYTES or '\0' in content:
        return None
    try:
        if len(content.encode('utf-8')) > MAX_REPLY_XML_BYTES:
            return None
    except UnicodeError:
        return None
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', content, re.I):
        return None
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        return None
    pending, count = [(root, 1)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 20000 or depth > 32:
            return None
        pending.extend((child, depth + 1) for child in node)
    apps = [root] if root.tag == 'appmsg' else root.findall('appmsg') if root.tag == 'msg' else []
    if len(apps) != 1 or (apps[0].findtext('type') or '').strip() != '57':
        return None
    app = apps[0]
    references = app.findall('refermsg')
    if len(references) != 1:
        return None
    refer = references[0]
    native_id = (refer.findtext('svrid') or '').strip()
    sender = (refer.findtext('chatusr') or refer.findtext('fromusr') or '').strip()
    if not re.fullmatch(r'[1-9][0-9]{0,19}', native_id):
        return None
    try:
        validate_account(sender)
        kind = int(refer.findtext('type') or '0')
    except ValueError:
        return None
    contents = refer.findall('content')
    if len(contents) > 1:
        return None
    body = refer.findtext('content') or ''
    if contents and len(contents[0]):
        element = contents[0]
        if (kind != 49 or len(element) != 1 or element[0].tag not in ('msg', 'appmsg') or
                (element.text or '').strip() or (element[0].tail or '').strip()):
            return None
        body = ET.tostring(element[0], encoding='unicode')
    reply = {
        'native_message_id': native_id, 'sender_id': sender,
        'sender_name': (refer.findtext('displayname') or '')[:256],
        'type': kind, 'content': body,
    }
    if kind == 49:
        from .forwarded import ForwardedError, parse_forwarded
        try:
            forwarded = parse_forwarded(body)
        except ForwardedError as error:
            if error.forwarded:
                reply.update(forwarded_availability='unavailable', forwarded_failure_reason=error.category)
        else:
            if forwarded is not None:
                reply['forwarded_availability'] = 'embedded'
    return {'text': app.findtext('title') or '', 'reply': reply}
