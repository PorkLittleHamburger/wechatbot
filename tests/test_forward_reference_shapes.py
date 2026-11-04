"""Explicit synthetic XML shapes; no native calls, chat data or network access."""
import json
import logging
from xml.sax.saxutils import escape

import pytest

from test_inbound_replies import helper_path
from test_rich_quotes import native_context, native_message


RECORD = ('<recordinfo><datalist count="1"><dataitem datatype="1">'
          '<datadesc>synthetic record</datadesc></dataitem></datalist></recordinfo>')


def forward(container):
    return '<msg><appmsg><type>19</type>' + container + '</appmsg></msg>'


def quote(body, *, inline=False, sender='wxid_fixture'):
    return ('<msg><appmsg><type>57</type><title>synthetic reply</title><refermsg>'
            '<type>49</type><svrid>9001</svrid><chatusr>' + sender + '</chatusr>'
            '<content>' + (body if inline else escape(body)) + '</content>'
            '</refermsg></appmsg></msg>')


@pytest.mark.parametrize('container', [
    RECORD,
    '<recorditem><recordxml>' + RECORD + '</recordxml></recorditem>',
    '<recorditem>' + escape(RECORD) + '</recorditem>',
    '<recorditem><![CDATA[' + RECORD + ']]></recorditem>',
    '<recordxml>' + escape('<recordxml>' + RECORD + '</recordxml>') + '</recordxml>',
])
def test_explicit_forward_container_shapes(helper_path, container):
    from wxbot_framework.forwarded import parse_forwarded
    result = parse_forwarded(forward(container))
    assert len(result.items) == 1
    assert result.items[0].kind == 'text'
    assert result.items[0].text == 'synthetic record'
    assert not result.truncated


@pytest.mark.parametrize('bare_app', [False, True])
def test_inline_reference_xml_reaches_native_mapping(helper_path, bare_app):
    from text_service import NativeTextService
    from wxbot_framework.replies import parse_reply
    source = forward(RECORD)
    if bare_app:
        source = source.removeprefix('<msg>').removesuffix('</msg>')
    parsed = parse_reply(quote(source, inline=True))
    assert parsed['reply']['forwarded_availability'] == 'embedded'
    assert 'forwarded_failure_reason' not in parsed['reply']
    assert parsed['reply']['content'] == source
    data = NativeTextService._map_message(native_context(), native_message(quote(source, inline=True)))
    assert data['reply']['content'] == ''
    assert data['reply']['forwarded_availability'] == 'embedded'
    assert data['reply']['records'][0]['text'] == 'synthetic record'


@pytest.mark.parametrize(('container', 'reason'), [
    ('<appattach><attachid>private-attachment</attachid></appattach>', 'missing_container'),
    ('<wrapper>' + RECORD + '</wrapper>', 'missing_container'),
    ('<recorditem>&lt;broken</recorditem>', 'invalid_xml'),
    ('<recorditem/>', 'invalid_xml'),
    ('<recorditem>' + escape('<!DOCTYPE recordinfo><recordinfo/>') + '</recorditem>', 'invalid_xml'),
    ('<recordinfo/>', 'invalid_structure'),
    ('<recordinfo><datalist/><datalist/></recordinfo>', 'invalid_structure'),
    (RECORD + RECORD, 'invalid_structure'),
    ('<recorditem>' + RECORD + RECORD + '</recorditem>', 'invalid_structure'),
    ('<recorditem><wrapper>' + RECORD + '</wrapper></recorditem>', 'invalid_structure'),
    ('<recorditem>mixed' + RECORD + '</recorditem>', 'invalid_structure'),
    ('<recorditem>' + RECORD + 'mixed</recorditem>', 'invalid_structure'),
    ('<recorditem>' + RECORD + '</recorditem><recordxml>' + RECORD + '</recordxml>', 'invalid_structure'),
])
def test_failure_reason_and_unavailable_reference_metadata(helper_path, container, reason, caplog):
    from text_service import NativeTextService
    from wxbot_framework.forwarded import ForwardedError, parse_forwarded, parse_reference_content
    source = forward(container)
    with pytest.raises(ForwardedError) as caught:
        parse_forwarded(source)
    assert caught.value.forwarded and caught.value.reason == reason
    with caplog.at_level(logging.WARNING, logger='wxbot_framework.forwarded'):
        result = parse_reference_content(49, source)
    assert caplog.messages == ['Quoted forwarded record unavailable category=' + reason]
    row = result.public()['forwarded_records'][0]
    assert row['availability'] == 'unavailable' and row['failure_reason'] == reason
    assert row['children'] == []
    if reason == 'missing_container':
        assert row['text'] == '引用只包含聊天记录卡片，原记录未缓存'
    else:
        assert row['text'] == '转发未携带可展开记录或内容无效'
    data = NativeTextService._map_message(native_context(), native_message(quote(source)))
    assert data['reply']['forwarded_availability'] == 'unavailable'
    assert data['reply']['forwarded_failure_reason'] == reason
    assert data['reply']['records'][0]['availability'] == 'unavailable'


def test_diagnostic_has_only_fixed_reason_no_private_values(helper_path, caplog):
    from wxbot_framework.forwarded import ForwardedError, parse_reference_content
    secret = 'private-body-private-aes-https://private.invalid/path'
    source = forward('<appattach><aeskey>' + secret + '</aeskey></appattach>')
    with caplog.at_level(logging.WARNING, logger='wxbot_framework.forwarded'):
        result = parse_reference_content(49, source)
    assert caplog.messages == ['Quoted forwarded record unavailable category=missing_container']
    assert secret not in caplog.text + json.dumps(result.public()) + repr(result)
    assert ForwardedError(secret, category=secret).reason == 'invalid_structure'


@pytest.mark.parametrize('body', [
    'mixed' + forward(RECORD),
    forward(RECORD) + 'mixed',
    forward(RECORD) + forward(RECORD),
    '<wrapper>' + forward(RECORD) + '</wrapper>',
])
def test_ambiguous_inline_reference_is_rejected(helper_path, body):
    from wxbot_framework.replies import parse_reply
    assert parse_reply(quote(body, inline=True)) is None


@pytest.mark.parametrize('sender', ['../wrong', '123@chatroom', 'filehelper'])
def test_inline_reference_keeps_native_sender_validation(helper_path, sender):
    from wxbot_framework.replies import parse_reply
    assert parse_reply(quote(forward(RECORD), inline=True, sender=sender)) is None


def test_forward_limits_and_explicit_failure_are_preserved(helper_path):
    from wxbot_framework.forwarded import ForwardedError, MAX_FORWARD_XML_BYTES, parse_forwarded
    oversized = forward('<recorditem>' + 'x' * MAX_FORWARD_XML_BYTES + '</recorditem>')
    with pytest.raises(ForwardedError) as caught:
        parse_forwarded(oversized)
    assert caught.value.reason == 'limit_exceeded'
    deep = '<recorditem>' + '<wrapper>' * 33 + RECORD + '</wrapper>' * 33 + '</recorditem>'
    with pytest.raises(ForwardedError) as caught:
        parse_forwarded(forward(deep))
    assert caught.value.reason == 'limit_exceeded'
    item = '<dataitem datatype="1"><datadesc>x</datadesc></dataitem>'
    result = parse_forwarded(forward('<recordinfo><datalist>' + item * 201 + '</datalist></recordinfo>'))
    assert len(result.items) == 200 and result.truncated


def test_identified_forward_with_excessive_inner_nodes_reports_limit(helper_path, caplog):
    from wxbot_framework.forwarded import parse_reference_content
    from wxbot_framework.replies import parse_reply
    inner = '<recordinfo><datalist>' + '<x/>' * 20001 + '</datalist></recordinfo>'
    source = forward('<recorditem>' + escape(inner) + '</recorditem>')
    parsed = parse_reply(quote(source))
    assert parsed['reply']['forwarded_availability'] == 'unavailable'
    assert parsed['reply']['forwarded_failure_reason'] == 'limit_exceeded'
    with caplog.at_level(logging.WARNING, logger='wxbot_framework.forwarded'):
        result = parse_reference_content(49, source)
    assert result.public()['forwarded_records'][0]['failure_reason'] == 'limit_exceeded'
    assert caplog.messages == ['Quoted forwarded record unavailable category=limit_exceeded']


def test_quote_byte_depth_node_and_declaration_limits(helper_path):
    from wxbot_framework.replies import MAX_REPLY_XML_BYTES, parse_reply
    assert parse_reply(quote('汉' * (MAX_REPLY_XML_BYTES // 3 + 1))) is None
    deep = '<wrapper>' * 33 + RECORD + '</wrapper>' * 33
    assert parse_reply(quote(deep, inline=True)) is None
    assert parse_reply(quote('<msg>' + '<x/>' * 20001 + '</msg>', inline=True)) is None
    assert parse_reply('<!DOCTYPE msg>' + quote(forward(RECORD), inline=True)) is None
    assert parse_reply(quote(forward(RECORD), inline=True).replace('<content>', '<content/> <content>', 1)) is None


def test_non_forward_card_does_not_claim_forward_availability(helper_path):
    from wxbot_framework.replies import parse_reply
    result = parse_reply(quote('<msg><appmsg><type>5</type></appmsg></msg>'))
    assert 'forwarded_availability' not in result['reply']
