"""Project rich WeChat content to OneBot without turning history into live events."""
from __future__ import annotations

import hashlib
from typing import Any

from ..models import Segment

MAX_ITEMS = 200
MAX_DEPTH = 8


def record_segments(row: dict[str, Any]) -> tuple[Segment, ...]:
    kind = row.get('kind')
    if kind == 'text':
        return (Segment('text', {'text': str(row.get('text', ''))[:32768]}),)
    if kind == 'link':
        link = {key: str(row.get(key, ''))[:8192] for key in ('title', 'url', 'text')}
        text = '\n'.join(value for value in link.values() if value)
        if not link['url']:
            text += '\n[链接地址不可用]'
        return (Segment('text', {'text': text or '[链接内容不可用]'}),)
    if kind == 'image' and row.get('image_status') == 'ready' and row.get('image_path'):
        return (Segment('image', {'file': row['image_path'], 'image_quality': row.get('image_quality', 'unknown')}),)
    label = {'image':'图片', 'voice':'语音', 'video':'视频', 'file':'文件',
             'forwarded':'嵌套聊天记录'}.get(kind, '消息')
    detail = row.get('image_error') if kind == 'image' else row.get('text')
    return (Segment('text', {'text': f'[{label}内容不可用]' + (f' {detail}' if detail else '')}),)


def rich_segments(records, *, is_forward=False, title='', truncated=False) -> tuple[Segment, ...]:
    if is_forward:
        return (Segment('forward', {'records': records, 'title': title, 'truncated': truncated}),)
    result = tuple(segment for row in records[:MAX_ITEMS] if isinstance(row, dict)
                   for segment in record_segments(row))
    if truncated:
        result += (Segment('text', {'text': '[引用内容超出解析上限，部分内容已截断]'}),)
    return result


def materialize_forwards(payload, *, store, mapper, media, account, message_id):
    from .converters import _segment_to_onebot

    for index, segment in enumerate(payload['message']):
        if segment['type'] != 'forward' or 'records' not in segment['data']:
            continue
        source = segment['data']
        flat, budget = [], {'items': 0, 'truncated': bool(source.get('truncated')), 'unavailable': False}

        def walk(rows, path=()):
            normalized = []
            if len(path) > MAX_DEPTH:
                budget['truncated'] = True
                return normalized
            for position, row in enumerate(rows):
                if budget['items'] >= MAX_ITEMS:
                    budget['truncated'] = True
                    break
                if not isinstance(row, dict):
                    continue
                budget['items'] += 1
                here = (*path, position)
                name = str(row.get('sender_name') or '')[:256]
                stamp = str(row.get('source_time') or '')[:64]
                base = {'sender': {'user_id': 0, 'nickname': name}, 'time': 0,
                        'source_time': stamp, 'path': list(here)}
                if row.get('kind') == 'forwarded':
                    title = str(row.get('title') or '嵌套聊天记录')[:4096]
                    text = f'[聊天记录开始：{title}]'
                    if row.get('text'):
                        text += ' ' + str(row['text'])[:32768]
                        budget['unavailable'] = True
                    flat.append({**base, 'content': [{'type': 'text', 'data': {'text': text}}]})
                    children = walk(row.get('children', []), here)
                    flat.append({**base, 'content': [{'type': 'text', 'data': {'text': '[聊天记录结束]'}}]})
                    normalized.append({'kind': 'forwarded', 'sender_name': name, 'source_time': stamp,
                                       'title': title, 'text': str(row.get('text') or '')[:32768],
                                       'truncated': bool(row.get('truncated')), 'children': children})
                    budget['truncated'] |= bool(row.get('truncated'))
                    continue
                content = [_segment_to_onebot(item, mapper, media) for item in record_segments(row)]
                if row.get('kind') not in ('text', 'link', 'image') or (row.get('kind') == 'link' and not row.get('url')):
                    budget['unavailable'] = True
                if row.get('kind') == 'image' and (row.get('image_status') != 'ready' or
                        not any(item['type'] == 'image' for item in content)):
                    budget['unavailable'] = True
                # Registration failure must not expose an unusable local path as a fetched image.
                for item in content:
                    if item['type'] == 'image' and not item['data'].get('url'):
                        item.update(type='text', data={'text': '[图片注册失败，内容不可用]'})
                        budget['unavailable'] = True
                if stamp:
                    content.insert(0, {'type': 'text', 'data': {'text': f'[{stamp}] '}})
                flat.append({**base, 'content': content})
                normalized.append({'kind': row.get('kind', 'unknown'), 'sender_name': name,
                                   'source_time': stamp, 'message': content})
            return normalized

        tree = walk(source.get('records', []))
        if budget['truncated'] or not flat:
            budget['unavailable'] = True
            flat.append({'sender': {'user_id': 0, 'nickname': ''}, 'time': 0,
                         'content': [{'type':'text', 'data':{'text':
                             '[聊天记录超出解析上限，部分内容未展开]' if budget['truncated'] else '[聊天记录内容不可用]'}}]})
        forward_id = hashlib.sha256(f'{account}\0{message_id}\0{index}'.encode()).hexdigest()[:32]
        store.put_forward(account, forward_id, canonical_forward({'messages': flat, 'records': tree,
                          'title': source.get('title', ''), 'truncated': budget['truncated'],
                          'status': 'partial' if budget['unavailable'] else 'ready'}))
        segment['data'] = {'id': forward_id}


def canonical_forward(payload: dict[str, Any]) -> dict[str, Any]:
    """Add canonical nodes while preserving aliases, including pre-fix stored rows.

    This is a read-time projection; the supplied payload is never modified.
    Unknown original authors use 0, and no original timestamp is invented.
    """
    from .codec import normalize_message

    if isinstance(payload.get('message'), list):
        return payload

    def standard_content(content):
        if not isinstance(content, list):
            return normalize_message(content)
        result = []
        for segment in content:
            kind = segment.get('type')
            data = segment.get('data') or {}
            if kind == 'text':
                data = {'text': str(data.get('text', ''))}
            elif kind == 'file':
                text = '[文件] ' + str(data.get('name') or data.get('file') or '内容不可用')
                text += '\n' + str(data['url']) if data.get('url') else '\n[文件内容不可用]'
                kind, data = 'text', {'text': text}
            elif kind == 'face' and data.get('file'):
                kind, data = 'image', {key: data[key] for key in ('file', 'url') if key in data}
            elif kind in ('image', 'record', 'video'):
                data = {key: value for key, value in data.items() if key in ('file', 'url', 'type', 'magic')}
            result.extend(normalize_message({'type': kind, 'data': data}))
        return result

    nodes = []
    for row in payload.get('messages', []):
        sender = row.get('sender') or {}
        nodes.append({'type': 'node', 'data': {
            'user_id': str(sender.get('user_id', 0)),
            'nickname': str(sender.get('nickname') or ''),
            'content': standard_content(row.get('content', [])),
        }})
    return {**payload, 'message': nodes}
