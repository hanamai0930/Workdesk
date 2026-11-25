"""Multiplexed line-framed RPC for inherited, trusted parent/child pipes."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import uuid


MAX_FRAME = 64 * 1024 * 1024


def encode(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'$bytes'}:
            return base64.b64decode(value['$bytes'], validate=True)
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def remote_error(value):
    kind = value.get('type', 'RuntimeError')
    types = {'ValueError': ValueError, 'KeyError': KeyError, 'LookupError': LookupError,
             'OSError': OSError, 'ConnectionError': ConnectionError,
             'IntegrityError': sqlite3.IntegrityError, 'OperationalError': sqlite3.OperationalError,
             'ProgrammingError': sqlite3.ProgrammingError, 'DatabaseError': sqlite3.DatabaseError}
    error_type = types.get(kind, RuntimeError)
    if value.get('sqlite_errorcode') is not None and error_type is RuntimeError:
        error_type = sqlite3.IntegrityError if int(value['sqlite_errorcode']) & 255 == 19 else sqlite3.OperationalError
    error = error_type(value.get('message', '核心调用失败。'))
    if value.get('sqlite_errorcode') is not None:
        error.sqlite_errorcode = value['sqlite_errorcode']
    return error


class Peer:
    def __init__(self, reader, writer, dispatch=None):
        self.reader, self.writer, self.dispatch = reader, writer, dispatch
        self.pending = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.closed = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix='core-bridge')

    def send(self, value):
        raw = json.dumps(encode(value), ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8') + b'\n'
        if len(raw) > MAX_FRAME:
            raise ValueError('兼容协议结果超过单帧上限；请分页读取。')
        with self.write_lock:
            if self.closed.is_set():
                raise ConnectionError('应用核心管道已关闭。')
            self.writer.write(raw)
            self.writer.flush()

    def call(self, method, params=None, timeout=180):
        identity = 'py-' + str(uuid.uuid4())
        slot = {'event': threading.Event()}
        with self.lock:
            if self.closed.is_set():
                raise ConnectionError('应用核心连接已退出。')
            self.pending[identity] = slot
        try:
            self.send({'id': identity, 'method': method, 'params': params or {}})
            if not slot['event'].wait(timeout):
                raise ConnectionError('应用核心回执未知；未重发。')
            if 'error' in slot:
                raise remote_error(slot['error'])
            return slot.get('result')
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def _request(self, value):
        try:
            if self.dispatch is None:
                raise ValueError('兼容操作不存在。')
            result = self.dispatch(value['method'], value.get('params') or {})
            self.send({'id': value['id'], 'result': result})
        except Exception as error:
            failure = {'message': str(error), 'type': type(error).__name__}
            if hasattr(error, 'sqlite_errorcode'):
                failure['sqlite_errorcode'] = error.sqlite_errorcode
            try:
                self.send({'id': value['id'], 'error': failure})
            except (OSError, ValueError):
                pass

    def serve(self):
        try:
            while not self.closed.is_set():
                line = self.reader.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b'\n'):
                    raise ValueError('兼容协议帧无效。')
                value = decode(json.loads(line))
                if not isinstance(value, dict) or not isinstance(value.get('id'), str):
                    raise ValueError('兼容协议缺少请求身份。')
                if 'method' in value:
                    if not value['id'].startswith('go-'):
                        raise ValueError('兼容请求来源身份无效。')
                    self.pool.submit(self._request, value)
                else:
                    with self.lock:
                        slot = self.pending.get(value['id'])
                        if slot is not None:
                            slot.update({key: value[key] for key in ('result', 'error') if key in value})
                            slot['event'].set()
        finally:
            self.close()

    def close(self):
        self.closed.set()
        with self.lock:
            for slot in self.pending.values():
                slot['error'] = {'type': 'ConnectionError', 'message': '应用核心已退出；未重发。'}
                slot['event'].set()
        self.pool.shutdown(wait=False, cancel_futures=True)
