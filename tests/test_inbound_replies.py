from pathlib import Path
from types import SimpleNamespace

import pytest

from test_service import make_service, make_config
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.models import IncomingMessage, Segment
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.onebot.network.config import AccountNetworkConfig
from wechat_onebot.config import ConfigError


XML = '''<msg><appmsg><title>@Robot\u2005 评价一下</title><type>57</type><refermsg>
<type>1</type><svrid>9001</svrid><fromusr>123@chatroom</fromusr>
<chatusr>wxid_author</chatusr><displayname>Author</displayname>
<content><![CDATA[original & text]]></content></refermsg></appmsg></msg>'''


@pytest.fixture
def helper_path(monkeypatch):
    root = Path(__file__).resolve().parents[1] / 'vendor/helper'
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / 'native'))


def test_native_quote_reaches_gateway_without_file_receiver(helper_path, tmp_path):
    from text_service import NativeTextService
    from wxbot_framework.text import TextRouter
    router = TextRouter()
    router.set_account('wxid_robot')
    context = SimpleNamespace(router=router, self_wxid='wxid_robot',
        _sender_wechat_name=lambda _: 'User', _sent_texts=(), chats={},
        mention_messages=0, is_at_self=0)
    message = SimpleNamespace(type=49, image_xml=('wxid_alice:\n'+XML).encode(),
        sender=b'123@chatroom', receiver=b'wxid_robot', sequence=8, thread_id=0,
        native_message_id=9002, mention_source=b'<msgsource><atuserlist>wxid_robot</atuserlist></msgsource>')
    # No file receiver is installed: valid quotes must bypass attachment dispatch.
    raw = NativeTextService._map_message(context, message)
    assert raw['app_type'] == 57 and raw['is_at_self']
    driver = HelperDriver(make_config(tmp_path))
    incoming = driver._convert_raw_data(raw)
    assert incoming.external_sender_id == 'wxid_alice'
    assert [s.type for s in incoming.segments] == ['at', 'text']
    assert incoming.segments[-1].data['text'] == '评价一下'
    assert incoming.raw['reply']['content'] == 'original & text'
    wrapped = SimpleNamespace(data=raw, type=49, text=raw['msg'],
        sender_id=raw['sender_id'], chat_id=raw['chat_id'], is_group=True, is_self=False, sequence=8)
    assert driver._convert_message(wrapped).segments == incoming.segments


@pytest.mark.parametrize('xml', ['<broken', XML.replace('<type>57</type>', '<type>6</type>'),
    XML.replace('9001', '0'), XML.replace('wxid_author', '../wrong'),
    '<!DOCTYPE msg [<!ENTITY x "bad">]>'+XML, 'x' * (1024*1024+1)],
    ids=['malformed','other-app','zero-id','invalid-sender','doctype','oversized'])
def test_invalid_or_other_app_messages_are_not_quotes(helper_path, xml):
    from wxbot_framework.replies import parse_reply
    assert parse_reply(xml) is None


def message(native_id, *, at=False, reply=None, segments=None, group='123@chatroom'):
    return IncomingMessage(group, 'wxid_author',
        segments or ((Segment('at', {'qq': 'wxid_robot'}),) if at else ()) + (Segment('text', {'text': 'hello'}),),
        external_message_id=native_id, is_group=group.endswith('@chatroom'), raw={'reply':reply} if reply else {})


@pytest.mark.asyncio
async def test_filtered_image_remains_available_to_astrbot_reply(tmp_path):
    from PIL import Image
    service, _, mapper, events = await make_service(tmp_path)
    try:
        service.group_mention_only = True
        image = tmp_path / 'original.png'
        Image.new('RGB', (2, 2), 'blue').save(image)
        await service._on_driver_event(message('9001', segments=(Segment('image', {'file':str(image)}),)))
        assert events == []
        reference = {'native_message_id':'9001', 'sender_id':'wxid_author', 'type':3}
        await service._on_driver_event(message('9002', at=True, reply=reference))
        assert len(events) == 1
        assert events[0]['message'][0]['type'] == 'reply'
        target = events[0]['message'][0]['data']['id']
        result = await service.dispatch('get_msg', {'message_id':int(target)})
        assert result['status'] == 'ok'
        original = result['data']
        assert original['message_type'] == 'group' and original['group_id'] == events[0]['group_id']
        assert original['sender']['user_id'] == original['user_id']
        assert original['message'][0]['type'] == 'image'
        assert original['message'][0]['data']['url']
        # Ordinary visible labels and mentions of someone else do not trigger.
        await service._on_driver_event(message('9003', segments=(Segment('text',{'text':'@Robot hello'}),)))
        await service._on_driver_event(message('9004', segments=(Segment('at',{'qq':'wxid_other'}),)))
        assert len(events) == 1
        await service._on_driver_event(message('9005', group='wxid_author'))
        assert len(events) == 2
        service.group_mention_only = False
        await service._on_driver_event(message('9006'))
        assert len(events) == 3
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_uncached_quote_snapshot_and_account_group_boundaries(tmp_path):
    service, _, mapper, events = await make_service(tmp_path)
    try:
        await service._on_driver_event(message('9001'))
        reference={'native_message_id':'9001','sender_id':'wxid_author','sender_name':'Author','type':1,'content':'quoted text'}
        await service._on_driver_event(message('9002', reply=reference, group='other@chatroom'))
        target=int(events[-1]['message'][0]['data']['id'])
        data=(await service.dispatch('get_msg',{'message_id':target}))['data']
        assert data['message'] == [{'type':'text','data':{'text':'quoted text'}}]
        assert data['time'] == 0
        assert service.message_store.get(target, account='different-account') is None
        # Snapshot is context, not a newly received message in local history.
        history=service.message_store.history(account=service.account,session_key='other@chatroom',count=10)
        assert len(history) == 1
        # A conflicting sender must not overwrite a known source alias.
        await service._on_driver_event(message('9003', reply={**reference,'sender_id':'wxid_wrong'}))
        assert all(s['type'] != 'reply' for s in events[-1]['message'])
        assert service.message_store.native_message(service.account,'123@chatroom','9001')['user_id'] == mapper.get('user','wxid_author')
    finally:
        await service.stop()
        mapper.close()


def test_quote_aliases_and_snapshots_survive_restart(tmp_path):
    path=tmp_path/'store.db'
    with MessageStore(path) as store:
        mid=store.get_or_allocate_id(account='a',event_name='reference_snapshot',session_key='g',sequence_key='9001')
        store.put_reference(mid,'a',{'message_id':mid,'message':[{'type':'text','data':{'text':'original'}}]})
        store.remember_native_id('a','g','9001',mid)
    with MessageStore(path) as store:
        assert store.native_message('a','g','9001')['message_id'] == mid
        assert store.native_message('a','other','9001') is None
        assert store.native_message('b','g','9001') is None


def test_group_filter_config_roundtrip_and_validation():
    assert not AccountNetworkConfig.from_dict({}).group_mention_only
    assert AccountNetworkConfig.from_dict({'groupMentionOnly':True}).to_dict()['groupMentionOnly'] is True
    for bad in ('true',1,None,[]):
        with pytest.raises(ConfigError):
            AccountNetworkConfig.from_dict({'groupMentionOnly':bad})


def test_webui_filter_hot_apply_and_persistence(tmp_path):
    from fastapi.testclient import TestClient
    from test_webui import make_config as web_config, login_and_set_password
    from wechat_onebot.core.runtime import RuntimeManager, AccountRuntime
    from wechat_onebot.webui import create_webui_app
    import asyncio
    config = web_config(tmp_path)
    manager = RuntimeManager(config)
    with TestClient(create_webui_app(config, manager)) as client:
        login_and_set_password(client)
        assert client.post('/api/accounts/wxid_robot/start').json()['ok']
        runtime = manager.get('wxid_robot')
        driver = runtime.driver
        url = '/api/accounts/wxid_robot/network'
        payload = client.get(url).json()
        payload['groupMentionOnly'] = True
        assert client.put(url,json=payload).json()['applied']
        assert runtime.service.group_mention_only and runtime.driver is driver and driver.online
        assert client.get(url).json()['groupMentionOnly']
        assert not client.put(url,json={**payload,'groupMentionOnly':'true'}).json()['saved']
        assert runtime.service.group_mention_only
    restored = AccountRuntime.create(config)
    try:
        assert restored.service.group_mention_only
    finally:
        asyncio.run(restored.close())


@pytest.mark.asyncio
async def test_uncached_image_quote_is_explicitly_unavailable(tmp_path):
    service, _, mapper, events = await make_service(tmp_path)
    try:
        await service._on_driver_event(message('9002', reply={
            'native_message_id':'9001','sender_id':'wxid_author','type':3,
            'content':'<img aeskey="never-expose" cdnthumburl="https://invalid/"/>'}))
        mid=int(events[-1]['message'][0]['data']['id'])
        payload=(await service.dispatch('get_msg',{'message_id':mid}))['data']
        assert payload['message'] == [{'type':'text','data':{'text':'[引用图片：原图未缓存]'}}]
    finally:
        await service.stop()
        mapper.close()
