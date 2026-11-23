"""Windows current-user protection for Workdesk's own credential snapshots."""

import ctypes
from ctypes import wintypes
import os


PREFIX = b'WORKDESK-DPAPI-V1\0'


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(raw, protect):
    if os.name != 'nt':
        raise ValueError('此凭据文件须由保存它的 Windows 用户读取。')
    # Keep the input allocation alive for the entire native call.
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, target = _Blob(len(raw), buffer), _Blob()
    blob = ctypes.POINTER(_Blob)
    operation = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    operation.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob]
    operation.restype = wintypes.BOOL
    free = ctypes.windll.kernel32.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    try:
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ValueError('无法在当前 Windows 用户下安全处理 Workdesk 凭据。')
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(buffer, 0, len(raw))
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            free(target.data)


def encode(raw):
    """Windows snapshots are encrypted. Other local hosts secure file mode."""
    return PREFIX + _dpapi(raw, protect=True) if os.name == 'nt' else raw


def decode(raw):
    """Explicitly selected legacy .env references remain readable as before."""
    if raw.startswith(PREFIX):
        return _dpapi(raw[len(PREFIX):], protect=False)
    return raw
