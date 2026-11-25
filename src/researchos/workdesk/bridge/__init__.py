"""Trusted compatibility boundary; the Go owner holds all live ledger connections."""
import os
from pathlib import Path
import threading


_lock = threading.Lock()
_peer = None
_binding = None


def enabled():
    return os.environ.get('WORKDESK_CORE_BRIDGE') == '1'


def bind(peer, value):
    global _peer, _binding
    if not enabled():
        raise ValueError('兼容桥接必须由应用核心启动。')
    required = ('base', 'data', 'process_instance', 'implementation', 'owner_epoch')
    if any(key not in value for key in required):
        raise ValueError('兼容桥接缺少核心归属。')
    with _lock:
        if _binding is not None:
            raise ValueError('兼容桥接不能更换活动所有者。')
        _peer, _binding = peer, dict(value)


def binding():
    if not enabled() or _binding is None:
        return None
    return dict(_binding)


def manages(root):
    value = binding()
    return bool(value and Path(root).resolve() == Path(value['data']).resolve())


def call(method, params=None):
    value = binding()
    if value is None or _peer is None:
        raise ConnectionError('应用核心连接不可用；未打开本地活动库。')
    params = dict(params or {})
    params.update(process_instance=value['process_instance'], owner_epoch=value['owner_epoch'])
    return _peer.call(method, params)
