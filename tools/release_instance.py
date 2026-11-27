"""Start, inspect and safely stop one installed Workdesk release."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
from pathlib import Path
import socket
import sys
import uuid

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'src'))
sys.path.insert(0, str(BASE / 'tools'))
import dev_instance as dev
from researchos.workdesk import core_runtime
from researchos.workdesk.instance import load_profile, release_marker, release_state_root
from researchos.workdesk.paths import Locations, atomic_json, process_identity


def require_profile():
    if not release_marker(BASE):
        raise ValueError('缺少 Workdesk 安装版标识。')
    root = release_state_root(BASE)
    config = root / 'instance.json'
    if not config.exists():
        root.mkdir(parents=True, exist_ok=True)
        # Registration can race on the first double click. Publish only a complete,
        # unique file; another process's winning stable identity must be retained.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        user = root / 'user'
        value = dict(schema=1, channel='release', instance_id=str(uuid.uuid4()), development_root=str(root),
                     program_root=str(BASE), user_root=str(user), data_root=str(user / 'libraries/main'),
                     python_executable=str(BASE / 'runtime/python/python.exe'), ui_port=port, protected_roots=[])
        temp = root / ('register-' + str(uuid.uuid4()) + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as out:
                json.dump(value, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temp, config)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    return load_profile(BASE)


@contextmanager
def _lock(path, create=True):
    if path.resolve() != path:
        raise ValueError('实例锁位置无效。')
    mode = 'a+b' if create else 'r+b'
    with path.open(mode) as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError('该实例正在运行或处理其他操作。') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def lifecycle_lock(profile):
    return _lock(Path(profile['development_root']) / 'lifecycle.lock')


def prepare_core(profile):
    locations = Locations.resolve(BASE)
    manifest_path = BASE / 'runtime/workdesk-core/manifest.json'
    manifest = core_runtime.read_json(manifest_path)
    expected = manifest['implementation_revision']
    selection_path = Path(profile['user_root']) / 'config/core.json'
    snapshot = locations.data / 'implementations' / expected / 'manifest.json'
    if not snapshot.is_file() or not core_runtime.snapshot_matches(locations, expected):
        actual = core_runtime.freeze(BASE, locations.data, manifest_path.parent / 'workdesk-core.exe',
                                     source_files=manifest['source_files'])
        if actual['implementation_revision'] != expected:
            raise ValueError('程序内容与安装版本不符，请重新运行安装程序。')
    value = {'schema': 1, 'engine': 'go', 'manifest': str(manifest_path)}
    if not selection_path.is_file() or core_runtime.read_json(selection_path) != value:
        atomic_json(selection_path, value)
    core_runtime.expected_implementation(locations)


def active_desktop(profile):
    record = Path(profile['user_root']) / 'runtime/desktop/launch-identity.json'
    if not record.exists():
        return None
    saved = dev.read_json(record)
    root = Path(profile['program_root']) / 'runtime/workdesk-desktop'
    if (not dev.same_path(saved.get('user_root'), profile['user_root']) or type(saved.get('pid')) is not int
            or not Path(saved.get('executable', '')).resolve().is_relative_to(root)):
        raise ValueError('桌面窗口归属待核对。')
    current = process_identity(saved['pid'])
    if current['alive'] is False or (current['creation'] and current['creation'] != saved.get('creation')):
        return None
    if not current['creation'] or current['alive'] is not True:
        raise ValueError('桌面进程身份待核对。')
    return saved


def activate_window(pid):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.IsWindowVisible.argtypes = api.IsIconic.argtypes = api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    @callback
    def activate(hwnd, unused):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(hwnd):
            if api.IsIconic(hwnd):
                api.ShowWindow(hwnd, 9)
            api.SetForegroundWindow(hwnd)
            return False
        return True

    api.EnumWindows(activate, 0)


def start(profile, open_window):
    intent = Path(profile['user_root']) / 'config/v101-import-intent.json'
    if intent.exists():
        raise ValueError('上一次资料导入尚未完成，请从开始菜单再次运行“导入 v1.0.1 资料”。')
    prepare_core(profile)
    state = dev.start(profile, open_window=False)
    if open_window:
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location('workdesk_desktop_host', BASE / 'workdesk-ui/desktop_host.py')
        host = module_from_spec(spec)
        spec.loader.exec_module(host)
        ui = dev.checked_ui(profile)
        previous = active_desktop(profile)
        if ui and previous:
            if previous.get('process_instance') == ui['state']['instance']:
                activate_window(previous['pid'])
                return state
            if dev._close_desktop(profile)['state'] == 'CLOSE_REQUESTED':
                raise ValueError('请先关闭原有 Workdesk 窗口。')
        if not ui or not host.launch(ui['origin'] + '/s/' + ui['state']['token'] + '/', BASE, profile['user_root']):
            raise ValueError('桌面窗口未能打开，请核对 WebView2 运行环境。')
        record = Path(profile['user_root']) / 'runtime/desktop/launch-identity.json'
        atomic_json(record, {**dev.read_json(record), 'process_instance': ui['state']['instance']})
    return state


def check_stopped(profile):
    backend = dev.checked_backend(profile)
    if backend or dev.checked_ui(profile):
        raise ValueError('请先停止此 Workdesk 实例。')
    if active_desktop(profile):
        raise ValueError('请先关闭 Workdesk 桌面窗口。')
    owner = Path(profile['data_root']) / 'backend.lock'
    if owner.exists():
        with _lock(owner, create=False):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'status', 'stop', 'check-stopped'])
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    if not release_marker(BASE):
        raise ValueError('安装版身份无效。')
    root = release_state_root(BASE)
    cfg = root / 'instance.json'
    if args.action != 'start' and not cfg.exists():
        if root.exists() and any(root.iterdir()):
            raise ValueError('安装资料的实例登记不完整。')
        return {'state': 'STOPPED'}
    profile = require_profile()
    if args.action == 'status':
        return dev.status(profile)
    if args.action == 'check-stopped':
        lock = root / 'lifecycle.lock'
        if lock.exists():
            with _lock(lock, create=False):
                check_stopped(profile)
        else:
            check_stopped(profile)
        return {'state': 'STOPPED'}
    with lifecycle_lock(profile):
        if args.action == 'stop':
            return dev.stop(profile)
        return start(profile, not args.no_open)


if __name__ == '__main__':
    try:
        result = main()
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        sys.exit(1)
