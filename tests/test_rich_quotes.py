import hashlib
import json
import queue
from base64 import b64decode
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

from test_inbound_replies import helper_path, message
from test_service import make_service, make_config
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.onebot.message_store import MessageStore

PNG = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=')


def text_item(text='first', name='Speaker'):
    return f'<dataitem datatype="1"><sourcename>{name}</sourcename><sourcetime>2026-01-02 10:00</sourcetime><datadesc>{escape(text)}</datadesc></dataitem>'


def image_item():
    return f'<dataitem datatype="2"><sourcename>Image author</sourcename><datasize>{len(PNG)}</datasize><fullmd5>{hashlib.md5(PNG).hexdigest()}</fullmd5><cdndataurl>fixture-private-reference</cdndataurl><cdndatakey>{"a"*32}</cdndatakey></dataitem>'


def link_item():
    return '<dataitem datatype="5"><sourcename>Link author</sourcename><datatitle>Video title</datatitle><datadesc>Video summary</datadesc><weburlitem><link>https://example.com/video?a=1&amp;b=2</link></weburlitem></dataitem>'


def record(items):
    return '<recordinfo><datalist>'+''.join(items)+'</datalist></recordinfo>'


def nested(items, form='recordxml'):
    inside=record(items)
    content=inside if form == 'direct' else f'<{form}>{escape(inside)}</{form}>'
    return '<dataitem datatype="17"><sourcename>Forwarder</sourcename><datatitle>Nested title</datatitle>'+content+'</dataitem>'


def forward(items):
    return '<msg><appmsg><type>19</type><title>History title</title><recorditem>'+escape(record(items))+'</recorditem></appmsg></msg>'


LINK='<msg><appmsg><type>5</type><title>Video title</title><des>Video summary</des><url>https://example.com/video?a=1&amp;b=2</url></appmsg></msg>'


def quote(body, kind=49):
    return '<msg><appmsg><type>57</type><title>@Robot\u2005 evaluate</title><refermsg><type>'+str(kind)+'</type><svrid>9001</svrid><chatusr>wxid_author</chatusr><displayname>Author</displayname><content>'+escape(body)+'</content></refermsg></appmsg></msg>'


def native_context(receiver=None):
    from wxbot_framework.text import TextRouter
    router=TextRouter(); router.set_account('wxid_robot')
    return SimpleNamespace(router=router,self_wxid='wxid_robot', _sender_wechat_name=lambda _: 'User',
        _sent_texts=(),chats={},mention_messages=0,image_allowlist=(),_forwarded_receiver=receiver)


def native_message(xml):
    return SimpleNamespace(type=49,image_xml=('wxid_alice:\n'+xml).encode(),
        sender=b'123@chatroom',receiver=b'wxid_robot',sequence=8,thread_id=0,native_message_id=9002,
        mention_source=b'<msgsource><atuserlist>wxid_robot</atuserlist></msgsource>')


@pytest.mark.parametrize('form',['direct','recordinfo','recordxml','recorditem','datadesc'])
def test_nested_forward_text_image_and_links(helper_path,form):
    from wxbot_framework.forwarded import parse_forwarded
    parsed=parse_forwarded(forward([text_item(),nested([link_item(),nested([image_item(),text_item('deep')])],form),text_item('last')]))
    rows=parsed.public()['forwarded_records']
    assert rows[0]['text']=='first' and rows[-1]['text']=='last'
    children=rows[1]['children']
    assert children[0]['url']=='https://example.com/video?a=1&b=2'
    assert children[1]['children'][1]['text']=='deep'
    assert children[1]['children'][0]['image_status']=='pending'
    serialized=json.dumps(rows)
    assert 'fixture-private-reference' not in serialized and 'a'*32 not in serialized


def test_recursive_limits_are_shared_and_incomplete_siblings_survive(helper_path):
    from wxbot_framework.forwarded import parse_forwarded
    parsed=parse_forwarded(forward([nested([image_item()]*17),text_item('last')]))
    assert parsed.items[0].children[-1].image is None
    assert parsed.items[-1].text=='last'
    deep=text_item()
    for _ in range(12):
        deep=nested([deep])
    assert parse_forwarded(forward([deep,text_item('tail')])).truncated
    malformed='<dataitem datatype="17"><recordxml>&lt;broken</recordxml></dataitem>'
    result=parse_forwarded(forward([malformed,text_item('retained')]))
    assert result.items[0].text=='嵌套转发内容不可用' and result.items[1].text=='retained'
    many=parse_forwarded(forward([nested([text_item()]*199),text_item('over-limit')]))
    assert many.truncated and len(many.items)==1


@pytest.mark.parametrize('form',['bare-appmsg','recordxml','inline','wrapped-recordxml'])
def test_forward_containers_in_quote_bodies(helper_path,form):
    from wxbot_framework.forwarded import parse_forwarded
    source=forward([text_item('retained')])
    if form=='bare-appmsg':
        source=source.removeprefix('<msg>').removesuffix('</msg>')
    elif form=='recordxml':
        source=source.replace('recorditem','recordxml')
    elif form=='inline':
        source='<msg><appmsg><type>19</type><recorditem>'+record([text_item('retained')])+'</recorditem></appmsg></msg>'
    else:
        source='<msg><appmsg><type>19</type><recordxml>'+escape('<recordxml>'+record([text_item('retained')])+'</recordxml>')+'</recordxml></appmsg></msg>'
    assert parse_forwarded(source).items[0].text=='retained'


@pytest.mark.asyncio
async def test_link_quote_snapshot_preserves_title_summary_url(helper_path,tmp_path):
    from text_service import NativeTextService
    raw=NativeTextService._map_message(native_context(),native_message(quote(LINK)))
    service,_,mapper,events=await make_service(tmp_path)
    try:
        incoming=HelperDriver(make_config(tmp_path))._convert_raw_data(raw)
        await service._on_driver_event(incoming)
        mid=int(events[-1]['message'][0]['data']['id'])
        result=(await service.dispatch('get_msg',{'message_id':mid}))['data']
        data=result['message'][0]['data']
        assert set(data) == {'text'}
        assert 'https://example.com/video?a=1&b=2' in data['text']
        assert 'Video title' in data['text'] and 'Video summary' in data['text']
        assert '<appmsg>' not in data['text']
        direct=NativeTextService._map_message(native_context(),native_message(LINK))
        assert 'https://example.com/video?a=1&b=2' in HelperDriver(make_config(tmp_path))._convert_raw_data(direct).segments[1].data['text']
    finally:
        await service.stop(); mapper.close()


@pytest.mark.asyncio
async def test_quoted_forward_downloads_nested_media_then_astrbot_reads_all_content(helper_path,tmp_path):
    from text_service import NativeTextService
    from wxbot_framework.forwarded_receiver import ForwardedReceiver
    from wxbot_framework.image_metadata import ImageStore
    events_queue=queue.Queue()
    downloads=[]
    def download(metadata,destination):
        downloads.append(metadata.md5); destination.write_bytes(PNG); return True
    receiver=ForwardedReceiver(ImageStore(tmp_path/'receive','wxid_robot'),download,events_queue.put,lambda:True)
    service,_,mapper,events=await make_service(tmp_path)
    try:
        source=forward([text_item(),nested([link_item(),nested([image_item(),text_item('deep')])]),text_item('last')])
        assert NativeTextService._map_message(native_context(receiver),native_message(quote(source))) is None
        raw=events_queue.get(timeout=3)
        assert raw['msg']=='@Robot\u2005 evaluate' and downloads
        assert 'fixture-private-reference' not in json.dumps(raw)
        incoming=HelperDriver(make_config(tmp_path))._convert_raw_data(raw)
        await service._on_driver_event(incoming)
        quote_id=int(events[-1]['message'][0]['data']['id'])
        original=(await service.dispatch('get_msg',{'message_id':quote_id}))['data']
        fid=original['message'][0]['data']['id']
        result=await service.dispatch('get_forward_msg',{'message_id':fid})
        assert result['status']=='ok'
        payload=result['data']
        assert payload['status']=='ready'
        assert payload['records'][1]['children'][1]['children'][0]['message'][0]['type']=='image'
        content=[seg for row in payload['messages'] for seg in row['content']]
        text='\n'.join(seg['data']['text'] for seg in content if seg['type']=='text')
        assert text.index('first')<text.index('Video title')<text.index('deep')<text.index('last')
        assert '[聊天记录开始' in text and '[聊天记录结束]' in text
        assert any(seg['type']=='image' and seg['data']['url'] for seg in content)
        assert all(row['sender']['user_id']==0 for row in payload['messages'])
        assert service.message_store.get_forward('different-account',fid) is None
        assert (await service.dispatch('get_forward_msg',{'id':'unknown'}))['retcode']==1404
        assert (await service.dispatch('get_forward_msg',{'id':True}))['retcode']==1400
        (tmp_path/'forward-payload.json').write_text(json.dumps(payload),encoding='utf-8')
    finally:
        receiver.close(3); await service.stop(); mapper.close()


@pytest.mark.asyncio
async def test_uncached_quoted_image_uses_native_receiver_and_can_refresh_snapshot(helper_path,tmp_path):
    from text_service import NativeTextService
    from wxbot_framework.forwarded_receiver import ForwardedReceiver
    from wxbot_framework.image_metadata import ImageStore
    q=queue.Queue()
    def download(metadata,destination):
        destination.write_bytes(PNG); return True
    receiver=ForwardedReceiver(ImageStore(tmp_path/'receive','wxid_robot'),download,q.put,lambda:True)
    service,_,mapper,events=await make_service(tmp_path)
    try:
        await service._on_driver_event(message('8000',reply={'native_message_id':'9001','sender_id':'wxid_author','type':3}))
        first_mid=events[-1]['message'][0]['data']['id']
        xml=f'<msg><img md5="{hashlib.md5(PNG).hexdigest()}" length="{len(PNG)}" cdnbigimgurl="fixture-private-reference" aeskey="{"a"*32}"/></msg>'
        assert NativeTextService._map_message(native_context(receiver),native_message(quote(xml,3))) is None
        raw=q.get(timeout=3)
        incoming=HelperDriver(make_config(tmp_path))._convert_raw_data(raw)
        # Match the original quoted author; the current message sender is separate.
        await service._on_driver_event(incoming)
        assert events[-1]['message'][0]['data']['id']==first_mid
        result=(await service.dispatch('get_msg',{'message_id':int(first_mid)}))['data']
        assert result['message'][0]['type']=='image' and result['message'][0]['data']['url']
    finally:
        receiver.close(3); await service.stop(); mapper.close()


def test_forward_store_survives_restart_and_isolates_accounts(tmp_path):
    path=tmp_path/'store.db'
    with MessageStore(path) as store:
        store.put_forward('a','id',{'messages':[{'content':[{'type':'text','data':{'text':'retained'}}]}]})
    with MessageStore(path) as store:
        assert store.get_forward('a','id')['messages']
        assert store.get_forward('b','id') is None


def test_quote_image_quality_and_link_truncation_are_explicit(helper_path):
    from wxbot_framework.forwarded import parse_reference_content, parse_link, parse_forwarded
    xml=f'<msg><img md5="{hashlib.md5(PNG).hexdigest()}" length="{len(PNG)}" cdnmidimgurl="fixture-reference" aeskey="{"a"*32}"/></msg>'
    assert parse_reference_content(3,xml).items[0].image_quality=='standard'
    thumbnail=xml.replace('cdnmidimgurl','cdnthumburl')
    assert parse_reference_content(3,thumbnail).items[0].image is None
    link=parse_link(LINK.replace('Video summary','x'*32769))
    assert link.truncated
    invalid=parse_link(LINK.replace('https://example.com/video?a=1&amp;b=2','javascript:alert(1)'))
    assert not invalid.items[0].url
    incomplete=forward([text_item('retained')]).replace('datalist&gt;', 'datalist count="2"&gt;', 1)
    parsed=parse_forwarded(incomplete)
    assert parsed.truncated and parsed.items[0].text=='retained'


@pytest.mark.parametrize('restricted',[False,True])
def test_forward_without_downloader_preserves_nested_text(helper_path,restricted):
    from text_service import NativeTextService
    context=native_context()
    if restricted:
        context.image_allowlist=('different@chatroom',)
    data=NativeTextService._map_message(context,native_message(forward([text_item('keep'),nested([image_item(),link_item()])])))
    assert data['forwarded_records'][0]['text']=='keep'
    child=data['forwarded_records'][1]['children']
    assert child[0]['image_status'] in ('skipped','unavailable')
    assert child[1]['url']=='https://example.com/video?a=1&b=2'


@pytest.mark.asyncio
async def test_unmentioned_forward_is_cached_then_resolved_by_quote(helper_path,tmp_path):
    from text_service import NativeTextService
    from wxbot_framework.forwarded_receiver import ForwardedReceiver
    from wxbot_framework.image_metadata import ImageStore
    q=queue.Queue()
    receiver=ForwardedReceiver(ImageStore(tmp_path/'receive','wxid_robot'),lambda *_:False,q.put,lambda:True)
    service,_,mapper,events=await make_service(tmp_path)
    try:
        service.group_mention_only=True
        source=forward([text_item('/historical-command @Robot'),nested([link_item(),text_item('deep')])])
        original=native_message(source)
        original.native_message_id=9001
        original.sequence=7
        original.mention_source=b''
        assert NativeTextService._map_message(native_context(receiver),original) is None
        driver=HelperDriver(make_config(tmp_path))
        await service._on_driver_event(driver._convert_raw_data(q.get(timeout=3)))
        assert not events
        cached=service.message_store.native_message(service.account,'123@chatroom','9001')
        raw=NativeTextService._map_message(native_context(receiver),native_message(quote(source).replace('wxid_author','wxid_alice')))
        await service._on_driver_event(driver._convert_raw_data(raw))
        assert len(events)==1
        assert int(events[0]['message'][0]['data']['id'])==cached['message_id']
        fid=cached['message'][0]['data']['id']
        payload=(await service.dispatch('get_forward_msg',{'id':fid}))['data']
        assert 'https://example.com/video?a=1&b=2' in payload['records'][1]['children'][0]['message'][0]['data']['text']
        assert (await service.dispatch('send_group_forward_msg',{}))['retcode']==1400
    finally:
        receiver.close(3); await service.stop(); mapper.close()


@pytest.mark.asyncio
async def test_nested_image_failure_keeps_other_content_and_marks_partial(helper_path,tmp_path):
    from text_service import NativeTextService
    from wxbot_framework.forwarded_receiver import ForwardedReceiver
    from wxbot_framework.image_metadata import ImageStore
    q=queue.Queue()
    def corrupt(metadata,destination):
        destination.write_bytes(PNG[:-1]); return True
    receiver=ForwardedReceiver(ImageStore(tmp_path/'receive','wxid_robot'),corrupt,q.put,lambda:True)
    service,_,mapper,events=await make_service(tmp_path)
    try:
        source=forward([text_item('retained'),nested([image_item(),link_item()])])
        assert NativeTextService._map_message(native_context(receiver),native_message(quote(source))) is None
        raw=q.get(timeout=3)
        assert raw['reply']['records'][1]['children'][0]['image_status']=='failed'
        await service._on_driver_event(HelperDriver(make_config(tmp_path))._convert_raw_data(raw))
        mid=int(events[-1]['message'][0]['data']['id'])
        original=(await service.dispatch('get_msg',{'message_id':mid}))['data']
        payload=(await service.dispatch('get_forward_msg',{'id':original['message'][0]['data']['id']}))['data']
        assert payload['status']=='partial'
        serialized=json.dumps(payload,ensure_ascii=False)
        assert 'retained' in serialized and 'Video title' in serialized and '图片内容不可用' in serialized
        assert 'fixture-private-reference' not in serialized and 'a'*32 not in serialized
    finally:
        receiver.close(3); await service.stop(); mapper.close()
