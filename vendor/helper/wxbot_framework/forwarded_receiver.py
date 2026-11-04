"""合并转发图片的有界异步接收，复用普通图片的账号目录及退出保护。"""
import hashlib
import queue

from .forwarded import ForwardedMessage
from .image_receiver import ImageReceiver


class ForwardedReceiver(ImageReceiver):
    def submit(self, data, forwarded, *, reference=False):
        if not isinstance(data, dict) or not isinstance(forwarded, ForwardedMessage):
            raise ValueError('合并转发需要已解析的消息')
        if not self._active():
            return False
        # 凭据只在已校验的元数据对象中排队，不进入插件事件或管理台。
        snapshot = {key:value for key,value in data.items() if key not in ('raw_content', 'msg', 'content', 'monitor_only')}
        if reference:
            snapshot['reply'] = dict(data['reply'], records=forwarded.public()['forwarded_records'])
            snapshot.update(msg=data.get('msg', ''), content=data.get('content', ''))
        else:
            snapshot.update(forwarded.public())
        snapshot['forwarded_status'] = 'ready'
        full = False
        with self._enqueue_lock:
            if self._stop.is_set():
                return False
            try:
                self._queue.put_nowait((snapshot, forwarded))
            except queue.Full:
                full = True
        if full:
            for _, item in self._items(forwarded.items, self._rows(snapshot)):
                if item.get('image_status') == 'pending':
                    item.update(image_status='failed', image_error='转发接收队列已满')
            snapshot['forwarded_status'] = 'partial'
            self._deliver(snapshot)
            return False
        return True

    @staticmethod
    def _rows(data):
        return data['reply']['records'] if data.get('app_type') == 57 else data['forwarded_records']

    @classmethod
    def _items(cls, items, rows):
        for item, row in zip(items, rows):
            yield item, row
            yield from cls._items(item.children, row.get('children', []))

    def _process(self, data, forwarded):
        saved = []
        delivered = False
        try:
            for item, row in self._items(forwarded.items, self._rows(data)):
                if not self._active():
                    return
                if item.kind != 'image':
                    continue
                if item.image is None:
                    data['forwarded_status'] = 'partial'
                    continue
                try:
                    body = self._download(item.image)
                    if body is None or not self._active():
                        return
                    if ((item.image_size and len(body) != item.image_size) or
                            hashlib.md5(body).hexdigest() != item.image.md5):
                        raise ValueError('图片校验失败')
                    path = self.store.save(body, sequence=data.get('sequence'))
                    saved.append(path)
                    row.update(image_path=str(path), image_status='ready', image_error='')
                except Exception:
                    row.update(image_path='', image_status='failed', image_error='图片下载或校验失败')
                    data['forwarded_status'] = 'partial'
            if self._active():
                delivered = self._deliver(data)
        finally:
            if not delivered:
                for path in saved:
                    self._remove_saved(path)
