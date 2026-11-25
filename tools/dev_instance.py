"""Operate one registered development instance without adopting another owner."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'src'))
from researchos.workdesk.instance import configure_environment, load_profile
from researchos.workdesk.paths import atomic_json, process_identity
from researchos.workdesk.workspace import io_path


class RequestRejected(RuntimeError):
    """The local service returned an explicit rejection."""


class RequestUncertain(RuntimeError):
    """Transport failed; a submitted command must not be repeated automatically."""


def read_json(path):
    path = Path(path)
    physical = io_path(path)
    if physical.is_symlink() or (hasattr(physical, 'is_junction') and physical.is_junction()):
        raise ValueError('开发状态文件不能使用链接。')
    if physical.stat().st_size > 1024 * 1024:
        raise ValueError('开发状态文件过大。')
    value = json.loads(physical.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('开发状态文件格式无效。')
    return value


def request_json(url, *, token=None, payload=None, origin=None, timeout=3):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    try:
        with urlopen(Request(url, data=raw, headers=headers), timeout=timeout) as response:
            value = json.loads(response.read(4 * 1024 * 1024 + 1))
    except HTTPError as exc:
        # The request URL may contain an entry token. Never print it.
        try:
            error = json.loads(exc.read(65536)).get('error', '本地服务拒绝请求。')
        except (ValueError, AttributeError):
            error = '本地服务拒绝请求。'
        raise RequestRejected('HTTP ' + str(exc.code) + '：' + str(error)) from None
    except (OSError, ValueError) as exc:
        raise RequestUncertain('本地服务连接或回执异常（' + type(exc).__name__ + '）；未自动重发。') from None
    if not isinstance(value, dict):
        raise RequestUncertain('本地服务回执格式异常；未自动重发。')
    return value


def same_path(value, expected):
    return isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve() == Path(expected).resolve()


def checked_backend(profile):
    path = Path(profile['data_root']) / 'backend.json'
    if not path.is_file():
        return None
    state = read_json(path)
    if (state.get('app') != 'workdesk-control-v1' or state.get('protocol') != 1
            or type(state.get('port')) is not int or not 1024 <= state['port'] <= 65535
            or type(state.get('pid')) is not int
            or any(not isinstance(state.get(k), str) or len(state[k]) != 43 for k in ('control', 'viewer'))):
        raise ValueError('开发控制服务状态无效；未发送控制命令。')
    origin = 'http://127.0.0.1:' + str(state['port'])
    try:
        health = request_json(origin + '/health')
    except RequestUncertain:
        if process_identity(state['pid'])['alive'] is False:
            return None
        raise ValueError('开发服务健康状态未知；保留原进程，未发送控制命令。') from None
    if (health.get('app') != state['app'] or health.get('protocol') != 1
            or health.get('instance') != state.get('instance')
            or not same_path(health.get('program_base'), profile['program_root'])):
        raise ValueError('开发控制服务身份不符；未发送控制命令。')
    identity = request_json(origin + '/api/v1/instance', token=state['viewer'])
    if (identity.get('schema') != 1 or identity.get('channel') != 'development'
            or identity.get('instance_id') != profile['instance_id']
            or identity.get('process_instance') != state['instance']
            or any(not same_path(identity.get(field), profile[field])
                   for field in ('program_root', 'user_root', 'data_root'))):
        raise ValueError('开发服务的程序或资料归属不符；未发送控制命令。')
    return {'state': state, 'origin': origin, 'identity': identity, 'process': process_identity(state['pid'])}


def checked_ui(profile):
    root = Path(profile['user_root']) / 'runtime/ui'
    if not root.is_dir():
        return None
    for path in sorted(root.glob('instance-*.json')):
        state = read_json(path)
        if state.get('port') != profile['ui_port']:
            continue
        if (not isinstance(state.get('token'), str) or len(state['token']) != 32
                or type(state.get('pid')) is not int):
            raise ValueError('开发界面入口回执无效。')
        origin = 'http://127.0.0.1:' + str(profile['ui_port'])
        try:
            health = request_json(origin + '/health')
        except RequestUncertain:
            if type(state.get('pid')) is int and process_identity(state['pid'])['alive'] is False:
                continue
            raise ValueError('开发界面健康状态未知；未终止原进程。') from None
        if (health.get('app') != 'workdesk-ui-02' or health.get('instance') != state.get('instance')
                or health.get('revision') != state.get('revision')):
            # Another revision may have left an old state file at the same port.
            continue
        if (health.get('instance_id') != profile['instance_id']
                or health.get('channel') != 'development'
                or any(not same_path(health.get(field), profile[field])
                       for field in ('program_root', 'user_root', 'data_root'))):
            raise ValueError('开发界面归属不符；未发送退出请求。')
        return {'state': state, 'origin': origin, 'process': process_identity(state['pid'])}
    return None


def command(profile, backend, kind, payload):
    identity = str(uuid.uuid4())
    record = Path(profile['user_root']) / 'runtime/dev-operations' / (identity + '.json')
    value = {'id': identity, 'kind': kind, 'instance_id': profile['instance_id'],
             'process_instance': backend['state']['instance'], 'state': 'INTENT', 'at': time.time()}
    atomic_json(record, value)
    try:
        result = request_json(backend['origin'] + '/api/v1/command', token=backend['state']['control'],
                              payload={'id': identity, 'kind': kind, 'payload': payload}, timeout=25)
    except RequestRejected:
        atomic_json(record, {**value, 'state': 'REJECTED'})
        raise
    except RequestUncertain:
        atomic_json(record, {**value, 'state': 'UNKNOWN'})
        raise RequestUncertain('本次操作回执未知，命令 ' + identity + ' 已保留；未自动重发。') from None
    atomic_json(record, {**value, 'state': 'RECEIVED', 'result': result})
    receipt = result.get('receipt')
    if not isinstance(receipt, dict):
        raise RequestUncertain('操作回执格式无效，命令 ' + identity + ' 已保留；未自动重发。')
    return receipt


def status(profile):
    backend, ui = checked_backend(profile), checked_ui(profile)
    return {'channel': 'development', 'instance_id': profile['instance_id'],
            'program_root': profile['program_root'], 'user_root': profile['user_root'],
            'data_root': profile['data_root'], 'ui_origin': ui['origin'] if ui else None,
            'ui_state': 'RUNNING' if ui else 'STOPPED',
            'backend_state': 'RUNNING' if backend else 'STOPPED',
            'backend_identity': backend['identity'] if backend else None,
            'stop_requested': (Path(profile['user_root']) / 'runtime/development-stop.json').exists()}


def start(profile, *, open_window=True):
    marker = Path(profile['user_root']) / 'runtime/development-stop.json'
    if marker.exists():
        saved = read_json(marker)
        if saved.get('instance_id') != profile['instance_id']:
            raise ValueError('停止意图不属于此开发实例；保留原文件。')
        # A fresh, explicit start is the only operation which clears the fence.
        marker.unlink()
    configure_environment(profile['program_root'])
    python = Path(profile['python_executable'])
    if not python.is_file():
        raise ValueError('登记的开发 Python 解释器不存在。')
    windowless = python.with_name('pythonw.exe')
    if os.name == 'nt' and windowless.is_file():
        python = windowless
    args = [str(python), '-B', '-X', 'utf8', str(Path(profile['program_root']) / 'workdesk-ui/server.py'),
            '--port', str(profile['ui_port'])]
    if open_window:
        args.append('--open')
    log = Path(profile['user_root']) / 'runtime/development-launch.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as stream:
        process = subprocess.Popen(args, cwd=profile['program_root'], env=os.environ.copy(),
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        ui = checked_ui(profile)
        if ui:
            return {'state': 'STARTED', 'instance_id': profile['instance_id'], 'ui_origin': ui['origin']}
        if process.poll() is not None:
            raise ValueError('开发入口已退出，尚未确认界面；请查看开发启动日志。')
        time.sleep(.15)
    raise ValueError('开发启动尚未确认；保留进程和日志，不重复启动。')


def _close_desktop(profile):
    record = Path(profile['user_root']) / 'runtime/desktop/launch-identity.json'
    if os.name != 'nt' or not record.is_file():
        return {'state': 'NO_NATIVE_WINDOW'}
    saved = read_json(record)
    if (not same_path(saved.get('user_root'), profile['user_root']) or type(saved.get('pid')) is not int
            or not isinstance(saved.get('creation'), str) or not isinstance(saved.get('executable'), str)):
        raise ValueError('桌面启动回执身份不完整；未关闭窗口。')
    executable = Path(saved['executable']).resolve()
    if not executable.is_relative_to(Path(profile['program_root']) / 'runtime/workdesk-desktop'):
        raise ValueError('桌面启动程序越出开发宿主目录；未关闭窗口。')
    current = process_identity(saved['pid'])
    if current['alive'] is False:
        return {'state': 'ALREADY_CLOSED'}
    if current['alive'] is not True or current['creation'] != saved['creation']:
        raise ValueError('桌面进程身份已经变化；未向旧 PID 发消息。')
    import ctypes as c
    from ctypes import wintypes as w
    kernel, user = c.WinDLL('kernel32', use_last_error=True), c.WinDLL('user32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, saved['pid'])
    if not handle:
        raise ValueError('无法核对桌面程序位置；未关闭窗口。')
    try:
        size = w.DWORD(32768); buffer = c.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, c.byref(size)) or Path(buffer.value).resolve() != executable:
            raise ValueError('桌面进程实际程序位置不符；未关闭窗口。')
    finally:
        kernel.CloseHandle(handle)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    closed = []
    def close(hwnd, unused):
        pid = w.DWORD(); user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if pid.value == saved['pid']:
            # Normal window close only; never terminate a process by name or PID.
            user.PostMessageW(hwnd, 0x0010, 0, 0); closed.append(int(hwnd))
        return True
    user.EnumWindows(callback_type(close), 0)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process_identity(saved['pid'])['alive'] is False:
            return {'state': 'CLOSED', 'windows': len(closed)}
        time.sleep(.1)
    return {'state': 'CLOSE_REQUESTED', 'windows': len(closed)}


def stop(profile):
    # Validate all reachable owners before creating the stop fence.
    backend, ui = checked_backend(profile), checked_ui(profile)
    for owner in (backend, ui):
        if owner and (owner['process']['alive'] is not True or owner['process']['creation'] is None):
            raise ValueError('开发进程创建身份未核对；未请求停止。')
    marker = Path(profile['user_root']) / 'runtime/development-stop.json'
    operation = str(uuid.uuid4())
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        if read_json(marker).get('instance_id') != profile['instance_id']:
            raise ValueError('停止意图不属于此开发实例。')
        if backend:
            raise ValueError('开发停止意图已存在，原服务仍在；请核对原回执。')
    else:
        with marker.open('x', encoding='utf-8') as stream:
            json.dump({'instance_id': profile['instance_id'], 'operation_id': operation, 'at': time.time()}, stream)
    can_undo_stop = backend is not None
    try:
        if backend:
            receipt = command(profile, backend, 'service.reload', {})
            if not receipt.get('retire_service'):
                raise RequestUncertain('开发服务尚未确认退休；保留停止意图与命令回执。')
            can_undo_stop = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = process_identity(backend['state']['pid'])
                if current['alive'] is False or (current['creation'] and current['creation'] != backend['process']['creation']):
                    break
                if current['alive'] is not True:
                    raise RequestUncertain('开发控制进程状态未知；保留停止意图，未关闭界面。')
                time.sleep(.1)
            else:
                raise RequestUncertain('开发控制服务仍未退出；保留停止意图，不关闭未知后台。')
        if ui:
            request_json(ui['origin'] + '/s/' + ui['state']['token'] + '/api/dev-exit',
                         payload={'instance_id': profile['instance_id']}, origin=ui['origin'])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = process_identity(ui['state']['pid'])
                if current['alive'] is False or (current['creation'] and current['creation'] != ui['process']['creation']):
                    break
                time.sleep(.1)
            else:
                raise RequestUncertain('开发界面服务退出尚未确认；保留停止意图和回执。')
        desktop = _close_desktop(profile)
        return {'state': 'STOPPED', 'instance_id': profile['instance_id'], 'desktop': desktop,
                'history_preserved': True, 'scope': 'development_only'}
    except RequestRejected:
        if can_undo_stop and marker.exists() and read_json(marker).get('operation_id') == operation:
            marker.unlink()
        raise


def required_backend(profile):
    backend = checked_backend(profile)
    if backend is None:
        raise ValueError('开发控制服务未运行；先通过开发入口打开界面。')
    return backend


def package_files(root, manifest):
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('开发备份清单格式无效。')
    body = {k: v for k, v in manifest.items() if k != 'manifest_hash'}
    digest = sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if digest != manifest.get('manifest_hash') or not 1 <= len(manifest['files']) <= 20000:
        raise ValueError('开发备份清单摘要无效。')
    total = 0
    for name, item in manifest['files'].items():
        parts = PurePosixPath(name)
        if (not name or parts.is_absolute() or '\\' in name or ':' in name
                or any(p in {'', '.', '..'} for p in name.split('/'))):
            raise ValueError('开发备份文件位置越界。')
        if name != 'tasks.sqlite3' and not name.startswith(('tasks/', 'implementations/', 'integration-journals/', 'capabilities/', 'results/')):
            raise ValueError('开发备份文件不在应用状态范围。')
        if any(p.lower() in {'.env', 'auth.json', 'credentials.json', 'backend.json', 'home'} for p in parts.parts):
            raise ValueError('开发备份不能包含认证或运行状态文件。')
        path = (root / 'state' / name).resolve()
        if not path.is_relative_to((root / 'state').resolve()):
            raise ValueError('开发备份文件位置越界。')
        for candidate in (root / 'state' / name, *(root / 'state' / name).parents):
            physical = io_path(candidate)
            if physical.is_symlink() or (hasattr(physical, 'is_junction') and physical.is_junction()):
                raise ValueError('开发备份文件不能使用链接。')
        limit = (128 if name == 'tasks.sqlite3' else 64) * 1024 * 1024
        physical = io_path(path)
        size = physical.stat().st_size; total += size
        if size > limit or total > 512 * 1024 * 1024:
            raise ValueError('开发备份超过文件或总量上限。')
        raw = physical.read_bytes()
        if len(raw) != item['bytes'] or sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('开发备份文件与清单不符。')
        yield name, raw


def recovery_root(profile):
    root = Path(profile['program_root']).parent
    if profile.get('development_root') and Path(profile['development_root']).resolve() != root.resolve():
        raise ValueError('开发恢复根与程序所在开发根不符。')
    recovery = root / 'recovery'
    for path in (recovery, *recovery.parents):
        physical = io_path(path)
        if physical.is_symlink() or (hasattr(physical, 'is_junction') and physical.is_junction()):
            raise ValueError('开发恢复根不能使用链接。')
    return recovery


def backup(profile):
    backend = required_backend(profile)
    receipt = command(profile, backend, 'backup.create', {})
    source = Path(receipt.get('root', '')).resolve()
    if receipt.get('state') != 'READY' or source.parent != Path(profile['data_root']) / 'backups':
        raise ValueError('开发备份回执位置或状态无效。')
    manifest = read_json(source / 'manifest.json')
    if manifest.get('manifest_hash') != receipt.get('manifest_hash'):
        raise ValueError('开发备份回执和清单不符。')
    recovery = recovery_root(profile)
    target = recovery / ('data-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(uuid.uuid4()))
    io_path(target).mkdir(parents=True, exist_ok=False)
    for name, raw in package_files(source, manifest):
        destination = target / 'state' / name
        io_path(destination.parent).mkdir(parents=True, exist_ok=True)
        with io_path(destination).open('xb') as stream:
            stream.write(raw)
    atomic_json(target / 'manifest.json', manifest)
    atomic_json(target / 'development-export.json', {'schema': 1, 'instance_id': profile['instance_id'],
                'source_data': profile['data_root'], 'backup_id': receipt['backup_id'],
                'manifest_hash': receipt['manifest_hash'], 'source_backup': str(source), 'created': time.time()})
    return {'state': 'EXPORTED', 'source': str(target), 'backup_id': receipt['backup_id'],
            'manifest_hash': receipt['manifest_hash'], 'files': len(manifest['files']), 'no_activation': True}


def restore_check(profile, source):
    recovery = recovery_root(profile)
    source = Path(source).resolve()
    if not source.is_relative_to(recovery.resolve()) or source == recovery.resolve():
        raise ValueError('恢复检查只接受开发 recovery 内的明确备份副本。')
    exported, manifest = read_json(source / 'development-export.json'), read_json(source / 'manifest.json')
    if exported.get('instance_id') != profile['instance_id'] or exported.get('manifest_hash') != manifest.get('manifest_hash'):
        raise ValueError('恢复副本不属于此开发实例。')
    for unused in package_files(source, manifest):
        pass
    backend = required_backend(profile)
    receipt = command(profile, backend, 'backup.restore', {'source': str(source), 'manifest_hash': manifest['manifest_hash']})
    target = Path(receipt.get('target', '')).resolve()
    if (receipt.get('state') != 'STAGED' or not receipt.get('no_dispatch')
            or target.parent != Path(profile['user_root']) / 'libraries' or target == Path(profile['data_root'])):
        raise ValueError('恢复暂存结果的位置或状态无效；未激活。')
    with closing(sqlite3.connect('file:' + quote(str(io_path(target / 'tasks.sqlite3'))) + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('开发恢复暂存库完整性检查失败；未激活。')
        counts = {table: connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('projects', 'tasks', 'attempts')}
    result = {**receipt, 'state': 'VERIFIED_STAGED', 'counts': counts, 'active_data_unchanged': profile['data_root']}
    atomic_json(source / ('restore-check-' + receipt['restore_id'] + '.json'), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='仅操作已登记的 Workdesk 开发实例；不切换日用资料。')
    parser.add_argument('action', choices=('start', 'status', 'stop', 'backup', 'restore-check'))
    parser.add_argument('--source', help='restore-check 的开发备份导出目录')
    parser.add_argument('--no-open', action='store_true', help='启动本地服务但不打开桌面窗口')
    parser.add_argument('--notify', action='store_true', help='启动失败时显示窗口提示')
    args = parser.parse_args(argv)
    try:
        profile = load_profile(BASE)
        if profile is None:
            raise ValueError('本仓库没有已登记的开发实例；未启动或控制其他实例。')
        if args.action == 'restore-check' and not args.source:
            raise ValueError('恢复检查需要 --source 指定开发备份导出目录。')
        if args.action == 'start':
            result = start(profile, open_window=not args.no_open)
        elif args.action == 'status':
            result = status(profile)
        elif args.action == 'stop':
            result = stop(profile)
        elif args.action == 'backup':
            result = backup(profile)
        else:
            result = restore_check(profile, args.source)
        if sys.stdout:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        message = str(exc)
        if sys.stderr:
            print('开发操作未完成：' + message, file=sys.stderr)
        if args.notify and os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Workdesk · 开发操作未完成', 16)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
