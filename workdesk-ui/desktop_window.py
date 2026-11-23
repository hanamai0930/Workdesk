"""Size only the new Workdesk app window created by this launch.

Edge can reuse its existing browser process and ignore --window-size. The
launcher therefore checks the new native window's visible frame as well.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import json
import os
import threading
import time


class NativeWindows:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.dwm = ctypes.WinDLL('dwmapi')
        self.callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        signatures = [
            (self.user.EnumWindows, [self.callback, wt.LPARAM], wt.BOOL),
            (self.user.IsWindowVisible, [wt.HWND], wt.BOOL),
            (self.user.IsZoomed, [wt.HWND], wt.BOOL),
            (self.user.IsIconic, [wt.HWND], wt.BOOL),
            (self.user.ShowWindow, [wt.HWND, ctypes.c_int], wt.BOOL),
            (self.user.GetWindowTextW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetClassNameW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetWindowThreadProcessId, [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            (self.user.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (self.user.SetWindowPos, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL),
            (self.user.MonitorFromWindow, [wt.HWND, wt.DWORD], wt.HANDLE),
            (self.user.GetMonitorInfoW, [wt.HANDLE, ctypes.c_void_p], wt.BOOL),
            (self.user.SetThreadDpiAwarenessContext, [ctypes.c_void_p], ctypes.c_void_p),
            (self.kernel.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (self.kernel.QueryFullProcessImageNameW, [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                    ctypes.POINTER(wt.DWORD)], wt.BOOL),
            (self.kernel.CloseHandle, [wt.HANDLE], wt.BOOL),
            (self.dwm.DwmGetWindowAttribute, [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD], ctypes.c_long),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    @contextmanager
    def physical_pixels(self):
        previous = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)

    def handles(self):
        result = set()
        @self.callback
        def collect(hwnd, _):
            result.add(hwnd)
            return True
        if not self.user.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def is_workdesk(self, hwnd, executable):
        if not self.user.IsWindowVisible(hwnd):
            return False
        title, name = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        self.user.GetWindowTextW(hwnd, title, len(title))
        self.user.GetClassNameW(hwnd, name, len(name))
        if title.value != 'Workdesk · 工作台' or name.value != 'Chrome_WidgetWin_1':
            return False
        pid = wt.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            return False
        try:
            path, length = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                return False
            return os.path.normcase(os.path.realpath(path.value)) == os.path.normcase(os.path.realpath(executable))
        finally:
            self.kernel.CloseHandle(process)

    def bounds(self, hwnd, visible=True):
        rect = wt.RECT()
        if visible:
            if self.dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)):
                raise OSError('Cannot read the visible Workdesk window frame.')
        elif not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (rect.left, rect.top, rect.right, rect.bottom)

    def work_area(self, hwnd):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('monitor', wt.RECT), ('work', wt.RECT), ('flags', wt.DWORD)]
        info = MonitorInfo(); info.size = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(hwnd, 2)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (info.work.left, info.work.top, info.work.right, info.work.bottom)

    def size(self, hwnd, width=1440, height=900):
        with self.physical_pixels():
            if self.user.IsZoomed(hwnd) or self.user.IsIconic(hwnd):
                self.user.ShowWindow(hwnd, 4)  # Restore without activating another window.
            before = self.bounds(hwnd)
            outer = self.bounds(hwnd, visible=False)
            area = self.work_area(hwnd)
            width, height = min(width, area[2]-area[0]), min(height, area[3]-area[1])
            left, top = area[0]+(area[2]-area[0]-width)//2, area[1]+(area[3]-area[1]-height)//2
            # Include Windows' invisible resize borders so the visible frame is exact.
            x, y = left+outer[0]-before[0], top+outer[1]-before[1]
            w, h = width+(outer[2]-outer[0])-(before[2]-before[0]), height+(outer[3]-outer[1])-(before[3]-before[1])
            if not self.user.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic()+1
            actual = self.bounds(hwnd)
            while [actual[2]-actual[0], actual[3]-actual[1]] != [width, height] and time.monotonic() < deadline:
                time.sleep(.05)
                actual = self.bounds(hwnd)
            return {'before': before, 'visible_frame': actual, 'width': actual[2]-actual[0],
                    'height': actual[3]-actual[1], 'target': [width, height],
                    'verified': [actual[2]-actual[0], actual[3]-actual[1]] == [width, height]}


def launch_sized(executable, command, spawn, report_path):
    """Launch normally; adjust once after this launch creates one new app window.

    Older Workdesk windows and ordinary browser windows are excluded. The
    non-daemon waiter also works when a short-lived --open process reuses the
    already-running local server. No browser profile or login data is changed.
    """
    native = NativeWindows()
    previous = native.handles()
    process = spawn(command)

    def finish():
        report = {'requested': [1440, 900], 'verified': False}
        try:
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                candidates = [h for h in native.handles()-previous if native.is_workdesk(h, executable)]
                if len(candidates) > 1:
                    report['error'] = 'Ambiguous new Workdesk windows; no window was resized.'
                    break
                if candidates:
                    hwnd = candidates[0]
                    report.update(native.size(hwnd))
                    report['window'] = hwnd
                    break
                time.sleep(.1)
            else:
                report['error'] = 'New Workdesk window not found before the launch timeout.'
        except OSError as exc:
            report['error'] = type(exc).__name__
        report['completed'] = time.time()
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temp = report_path.with_suffix('.tmp')
            temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(report_path)
        except OSError:
            pass

    threading.Thread(target=finish, name='workdesk-window-size', daemon=False).start()
    return process
