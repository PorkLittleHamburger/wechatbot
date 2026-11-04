from copy import deepcopy

import pytest

from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.models import IncomingMessage, NoticeEvent, RequestEvent, Segment
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.protocol.codec import parse_cq, serialize_message
from wechat_onebot.protocol.converters import message_event, notice_event, request_event
from wechat_onebot.protocol.rich_content import canonical_forward, materialize_forwards, rich_segments


def test_inbound_standard_projection_and_raw_message(tmp_path):
    media = MediaStore(tmp_path / 'media', max_bytes=1024, public_base_url='http://example.com')
    sticker = tmp_path / 'media' / 'sticker.gif'
    sticker.write_bytes(b'GIF89a')
    with IdMapper(tmp_path / 'ids.db') as mapper:
        incoming = IncomingMessage('g@chatroom', 'alice', is_group=True, segments=(
            Segment('text', {'text': '[literal]&', 'link': {'url': 'private'}}),
            Segment('at', {'qq': 'bob'}), Segment('at', {'qq': 'all'}),
            Segment('file', {'file': 'document.pdf'}), Segment('face', {'file': str(sticker)}),
            Segment('reply', {'id': 23}), Segment('image', {'file': 'missing.png'}),
        ))
        event = message_event(incoming, mapper, media, 1, message_id=-42)
        assert event['sub_type'] == 'normal'
        rows = event['message']
        assert rows[0]['data'] == {'text': '[literal]&'}
        assert rows[1]['data']['qq'] == str(mapper.get('user', 'bob'))
        assert rows[2]['data']['qq'] == 'all'
        assert rows[3] == {'type': 'text', 'data': {'text': '[文件] document.pdf\n[文件内容不可用]'}}
        assert rows[4]['type'] == 'image' and rows[4]['data']['url']
        assert rows[5]['data']['id'] == '23'
        assert rows[6]['type'] == 'text'
        # Adjacent text segments can merge in CQ; serialization must be stable.
        assert serialize_message(parse_cq(event['raw_message'])) == event['raw_message']
        assert '[CQ:at,qq=' in event['raw_message'] and '[CQ:image,' in event['raw_message']


def test_standard_only_forward_consumer_and_legacy_projection(tmp_path):
    media = MediaStore(tmp_path / 'media', max_bytes=1024, public_base_url='http://example.com')
    path = tmp_path / 'messages.db'
    with IdMapper(tmp_path / 'ids.db') as mapper, MessageStore(path) as store:
        records = [{'kind': 'text', 'text': 'first', 'sender_name': 'Alice'},
            {'kind': 'forwarded', 'title': 'nested', 'children': [
                {'kind': 'link', 'title': 'Title', 'url': 'https://example.com', 'text': 'Summary'}]},
            {'kind': 'image', 'image_status': 'ready', 'image_path': 'missing.png'}]
        incoming = IncomingMessage('g@chatroom', 'alice', segments=rich_segments(records, is_forward=True))
        payload = message_event(incoming, mapper, media, 1, message_id=8)
        materialize_forwards(payload, store=store, mapper=mapper, media=media, account='a', message_id=8)
        fid = payload['message'][0]['data']['id']
    with MessageStore(path) as store:
        forward = store.get_forward('a', fid)
        assert store.get_forward('b', fid) is None
        assert forward['status'] == 'partial'
        texts = []
        for node in forward['message']:  # Consumer knows no aliases.
            assert node['type'] == 'node'
            assert set(node['data']) == {'user_id', 'nickname', 'content'}
            for row in node['data']['content']:
                assert row['type'] == 'text' and set(row['data']) == {'text'}
                texts.append(row['data']['text'])
        assert texts[0] == 'first'
        assert any('Title' in text and 'Summary' in text and 'https://example.com' in text for text in texts)
        legacy = {key: value for key, value in forward.items() if key != 'message'}
        original = deepcopy(legacy)
        assert canonical_forward(legacy)['message'] == forward['message']
        assert legacy == original
        assert canonical_forward(forward) == forward


def test_legacy_private_segments_project_to_canonical_standard_nodes():
    legacy = {'messages': [{'sender': {'user_id': 0, 'nickname': 'Author'}, 'content': [
        {'type': 'text', 'data': {'text': 'link text', 'link': {'url': 'private'}}},
        {'type': 'face', 'data': {'file': 'sticker.gif', 'url': 'https://example.com/sticker'}},
        {'type': 'file', 'data': {'file': 'x.pdf'}}, {'type': 'at', 'data': {'qq': 9}},
    ]}], 'title': 'Archive'}
    original = deepcopy(legacy)
    standard = canonical_forward(legacy)
    assert standard['messages'] == original['messages'] and legacy == original
    content = standard['message'][0]['data']['content']
    assert content[0]['data'] == {'text': 'link text'}
    assert content[1]['type'] == 'image'
    assert content[2]['type'] == 'text'
    assert content[3]['data']['qq'] == '9'


def test_recall_requires_original_store_id_and_real_actors(tmp_path, caplog):
    with IdMapper(tmp_path / 'ids.db') as mapper, MessageStore(tmp_path / 'messages.db') as store:
        mid = store.get_or_allocate_id(account='a', event_name='group_message', session_key='g@chatroom', sequence_key='7')
        store.put(mid, account='a', event_name='group_message', session_key='g@chatroom', payload={'message_id': mid})
        store.remember_native_id('a', 'g@chatroom', '9001', mid)
        notice = NoticeEvent('group_recall', 'g@chatroom', 'alice', 'operator', raw={'recall': {'message_id': '9001'}})
        linked = store.native_message('a', 'g@chatroom', '9001')['message_id']
        event = notice_event(notice, mapper, 1, message_id=linked)
        assert event['message_id'] == mid
        assert event['operator_id'] == mapper.get('user', 'operator')
        assert notice_event(notice, mapper, 1) is None
        assert notice_event(NoticeEvent('group_recall', 'g@chatroom', 'alice'), mapper, 1, message_id=mid) is None
        assert store.native_message('b', 'g@chatroom', '9001') is None
        assert store.native_message('a', 'other@chatroom', '9001') is None
        friend = notice_event(NoticeEvent('friend_recall', 'alice', 'alice'), mapper, 1, message_id=mid)
        assert friend['message_id'] == mid and 'operator_id' not in friend
        assert 'Suppressing' in caplog.text


@pytest.mark.parametrize('kind,subtype,operator', [('group_increase', 'invite', 'admin'),
    ('group_increase', 'approve', 'admin'), ('group_decrease', 'leave', ''),
    ('group_decrease', 'kick', 'admin'), ('group_decrease', 'kick_me', 'admin')])
def test_membership_requires_known_schema(tmp_path, kind, subtype, operator):
    with IdMapper(tmp_path / 'ids.db') as mapper:
        notice = NoticeEvent(kind, 'g@chatroom', 'alice', operator, raw={'group_event': {'sub_type': subtype}})
        event = notice_event(notice, mapper, 1)
        assert event['sub_type'] == subtype
        assert event['operator_id'] == mapper.get('user', operator or 'alice')
        assert notice_event(NoticeEvent(kind, 'g@chatroom', 'alice'), mapper, 1) is None


def test_requests_require_group_and_known_subtype(tmp_path):
    with IdMapper(tmp_path / 'ids.db') as mapper:
        assert request_event(RequestEvent('group', 'alice'), mapper, 1) is None
        request = RequestEvent('group', 'alice', external_group_id='g@chatroom', raw={'sub_type': 'invite'})
        event = request_event(request, mapper, 1)
        assert event['group_id'] == mapper.get('group', 'g@chatroom') and event['sub_type'] == 'invite'
        assert request_event(RequestEvent('friend', 'alice'), mapper, 1)['request_type'] == 'friend'


def test_inbound_file_keeps_downloadable_url_in_standard_text(tmp_path):
    media = MediaStore(tmp_path / 'media', max_bytes=1024, public_base_url='http://example.com')
    document = tmp_path / 'document.pdf'
    document.write_bytes(b'%PDF-fixture')
    with IdMapper(tmp_path / 'ids.db') as mapper:
        incoming = IncomingMessage('alice', 'alice', segments=(
            Segment('file', {'file': str(document), 'name': 'report.pdf'}),))
        event = message_event(incoming, mapper, media, 1, message_id=42)
        row = event['message'][0]
        assert row['type'] == 'text' and set(row['data']) == {'text'}
        label, url = row['data']['text'].split('\n')
        assert label == '[文件] report.pdf'
        assert url.startswith('http://example.com/media/')
        media_id = url.split('/media/', 1)[1].split('/', 1)[0]
        assert media.get(media_id).path.read_bytes() == b'%PDF-fixture'
        assert parse_cq(event['raw_message']) == event['message']
