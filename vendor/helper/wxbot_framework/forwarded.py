"""合并转发元数据。仅解析，不联网；转发中的作者名称不代表已验证的 wxid。"""
from dataclasses import dataclass, field
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .image_metadata import ImageMetadata

MAX_FORWARD_XML_BYTES = 256 * 1024 - 1
MAX_FORWARD_ITEMS = 200
MAX_FORWARD_IMAGES = 16
MAX_FORWARD_IMAGE_BYTES = 64 * 1024 * 1024
MAX_FORWARD_DEPTH = 8
_FORBIDDEN = re.compile(r'<!\s*(?:DOCTYPE|ENTITY)\b', re.I)
_MD5 = re.compile(r'[a-fA-F0-9]{32}')


class ForwardedError(ValueError):
    def __init__(self, message, *, forwarded=False):
        super().__init__(message)
        self.forwarded = forwarded


@dataclass(frozen=True)
class ForwardedItem:
    kind: str
    sender_name: str
    source_time: str
    text: str = ''
    image: ImageMetadata | None = field(default=None, repr=False)
    image_size: int = 0
    image_error: str = ''
    title: str = ''
    url: str = ''
    children: tuple = ()
    truncated: bool = False
    image_quality: str = 'original'

    def public(self):
        result = {'kind': self.kind, 'sender_name': self.sender_name,
                  'source_time': self.source_time, 'text': self.text}
        if self.kind == 'image':
            result.update(image_status='pending' if self.image else 'unavailable', image_path='',
                          image_error=self.image_error, image_quality=self.image_quality)
        if self.kind == 'link':
            result.update(title=self.title, url=self.url)
        if self.kind == 'forwarded':
            result.update(title=self.title, children=[item.public() for item in self.children],
                          truncated=self.truncated)
        return result


@dataclass(frozen=True)
class ForwardedMessage:
    title: str
    items: tuple[ForwardedItem, ...]
    truncated: bool = False
    is_forward: bool = True

    def public(self):
        return {'app_type': 19, 'forwarded_title': self.title,
                'forwarded_records': [x.public() for x in self.items],
                'forwarded_truncated': self.truncated}


def _xml(content):
    if not isinstance(content, str) or not content.strip() or '\0' in content:
        raise ForwardedError('转发记录内容无效')
    try:
        if len(content) > MAX_FORWARD_XML_BYTES or len(content.encode('utf-8')) > MAX_FORWARD_XML_BYTES:
            raise ForwardedError('转发记录超过 256 KiB 上限')
    except UnicodeError:
        raise ForwardedError('转发记录编码无效') from None
    if _FORBIDDEN.search(content):
        raise ForwardedError('转发记录不允许实体声明')
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        raise ForwardedError('转发记录 XML 无效') from None
    pending, count = [(root, 1)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 20000 or depth > 32:
            raise ForwardedError('转发记录结构超过上限')
        pending.extend((child, depth + 1) for child in node)
    return root


def _text(node, name, limit):
    value = node.findtext(name) or ''
    return value[:limit]


def _image(node):
    md5 = _text(node, 'fullmd5', 33)
    size = _text(node, 'datasize', 16)
    url, key = node.findtext('cdndataurl') or '', node.findtext('cdndatakey') or ''
    if not size.isascii() or not size.isdecimal() or not 1 <= int(size) <= 20 * 1024 * 1024:
        return None, 0, '图片声明大小无效'
    if not _MD5.fullmatch(md5):
        return None, int(size), '图片校验信息缺失'
    if not 1 <= len(url.encode('utf-8')) <= 1023 or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None, int(size), '图片下载信息缺失'
    if len(key) not in (32, 64) or not all(ch in '0123456789abcdefABCDEF' for ch in key):
        return None, int(size), '图片下载信息缺失'
    # 实测使用图片原图任务，转发的 datafmt 与真实文件格式可能不同，保存时以文件头为准。
    return ImageMetadata(md5=md5.lower(), length=int(size), cdnbigimgurl=url, aeskey=key), int(size), ''


def _url(value):
    if len(value) > 8192 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return ''
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ('http', 'https') and parsed.hostname else ''
    except ValueError:
        return ''


def parse_link(content):
    root = _xml(content)
    app = root if root.tag == 'appmsg' else root.find('appmsg')
    if app is None or (app.findtext('type') or '').strip() != '5':
        return None
    truncated = any(len(app.findtext(tag) or '') > limit for tag, limit in
                    (('des', 32768), ('title', 4096), ('url', 8192)))
    return ForwardedMessage('', (ForwardedItem('link', '', '',
        _text(app, 'des', 32768), title=_text(app, 'title', 4096),
        url=_url(_text(app, 'url', 8193))),), truncated, is_forward=False)


def _embedded_record(node):
    direct = node.find('recordinfo')
    if direct is not None and direct.find('datalist') is not None:
        return direct
    for tag in ('recordinfo', 'recorditem', 'recordxml', 'datadesc'):
        element = node.find(tag)
        if element is None:
            continue
        if element.find('recordinfo') is not None:
            return element.find('recordinfo')
        raw = element.text or ''
        if raw.lstrip().startswith('<'):
            inner = _xml(raw)
            if inner.tag == 'recordinfo':
                return inner
            if inner.find('recordinfo') is not None:
                return inner.find('recordinfo')
            app = inner if inner.tag == 'appmsg' else inner.find('appmsg')
            if app is not None and app.findtext('recorditem'):
                return _xml(app.findtext('recorditem'))
    raise ForwardedError('嵌套转发未携带可展开内容', forwarded=True)


def _records(inner, budget, depth=0):
    if inner.tag != 'recordinfo' or len(inner.findall('datalist')) != 1:
        raise ForwardedError('转发记录列表无效', forwarded=True)
    listing = inner.find('datalist')
    nodes = listing.findall('dataitem')
    declared = listing.get('count')
    if declared is not None and (not declared.isascii() or not declared.isdecimal() or
                                 len(declared) > 9 or int(declared) != len(nodes)):
        budget['truncated'] = True
    items = []
    for node in nodes:
        if budget['items'] >= MAX_FORWARD_ITEMS:
            budget['truncated'] = True
            break
        budget['items'] += 1
        kind = {'1':'text', '2':'image', '3':'voice', '4':'video', '5':'link',
                '8':'file', '17':'forwarded'}.get(node.get('datatype'), 'unknown')
        name, stamp = _text(node, 'sourcename', 128), _text(node, 'sourcetime', 64)
        image, size, error, children = None, 0, '', ()
        title = _text(node, 'datatitle', 4096)
        text = _text(node, 'datadesc', 32768) if kind in ('text', 'link', 'file') else ''
        if kind in ('text', 'link', 'file') and len(node.findtext('datadesc') or '') > 32768:
            budget['truncated'] = True
        url = ''
        truncated = False
        if kind == 'image':
            image, size, error = _image(node)
            if image:
                budget['images'] += 1
                budget['bytes'] += size
                if budget['images'] > MAX_FORWARD_IMAGES or budget['bytes'] > MAX_FORWARD_IMAGE_BYTES:
                    image, error = None, '本条转发图片下载额度已用完'
        elif kind == 'link':
            web = node.find('weburlitem')
            if web is not None:
                title = title or _text(web, 'title', 4096)
                text = text or _text(web, 'desc', 32768)
                url = _url(_text(web, 'link', 8193))
            url = url or _url(_text(node, 'url', 8193))
        elif kind == 'forwarded':
            if depth >= MAX_FORWARD_DEPTH:
                text, truncated = '嵌套转发超过解析深度上限', True
            else:
                try:
                    children = _records(_embedded_record(node), budget, depth + 1)
                except ForwardedError:
                    text = '嵌套转发内容不可用'
            budget['truncated'] |= truncated
        items.append(ForwardedItem(kind, name, stamp, text, image, size, error,
                                   title, url, children, truncated))
    return tuple(items)


def parse_forwarded(content: str) -> ForwardedMessage | None:
    outer = _xml(content)
    if outer.tag not in ('msg', 'appmsg'):
        return None
    apps = [outer] if outer.tag == 'appmsg' else outer.findall('appmsg')
    if len(apps) != 1 or (apps[0].findtext('type') or '').strip() != '19':
        return None
    app = apps[0]
    container = app.find('recorditem')
    if container is None:
        container = app.find('recordxml')
    if container is None:
        raise ForwardedError('转发记录未携带可展开内容', forwarded=True)
    try:
        inner = container.find('recordinfo')
        if inner is None:
            inner = _xml(container.text or '')
        if inner.tag == 'recordxml' and inner.find('recordinfo') is not None:
            inner = inner.find('recordinfo')
    except ForwardedError as error:
        raise ForwardedError(str(error), forwarded=True) from None
    if inner.tag != 'recordinfo' or len(inner.findall('datalist')) != 1:
        raise ForwardedError('转发记录列表无效', forwarded=True)
    budget = dict(items=0, images=0, bytes=0, truncated=False)
    items = _records(inner, budget)
    return ForwardedMessage(_text(app, 'title', 256), items, budget['truncated'])


def parse_reference_content(kind, content):
    """Parse the embedded body without exposing download credentials publicly."""
    if kind == 1:
        return ForwardedMessage('', (ForwardedItem('text', '', '', content[:32768]),),
                                len(content) > 32768, False)
    if kind == 49:
        try:
            return parse_forwarded(content) or parse_link(content)
        except ForwardedError as error:
            if error.forwarded:
                return ForwardedMessage('聊天记录', (ForwardedItem('forwarded', '', '',
                    text='转发未携带可展开记录或内容无效'),))
            return None
    if kind == 3:
        from dataclasses import replace
        from .image_metadata import parse_image_metadata
        try:
            metadata = parse_image_metadata(content)
            # Prefer the original; never label a thumbnail as the original image.
            if metadata.cdnbigimgurl:
                metadata = replace(metadata, cdnmidimgurl='', filekey='', cdnthumburl='')
                size = metadata.hdlength or 0
                quality = 'original'
            elif metadata.cdnmidimgurl or metadata.filekey:
                metadata = replace(metadata, cdnthumburl='')
                size = metadata.length or 0
                quality = 'standard'
            else:
                raise ValueError('缺少原图或标准图信息')
            if (not _MD5.fullmatch(metadata.md5) or size > 20 * 1024 * 1024 or
                    len(metadata.aeskey) not in (32, 64) or
                    not all(ch in '0123456789abcdefABCDEF' for ch in metadata.aeskey)):
                raise ValueError('图片校验信息无效')
            item = ForwardedItem('image', '', '', image=metadata, image_size=size, image_quality=quality)
        except ValueError:
            item = ForwardedItem('image', '', '', image_error='引用图片下载信息不可用')
        return ForwardedMessage('', (item,), is_forward=False)
    return None
