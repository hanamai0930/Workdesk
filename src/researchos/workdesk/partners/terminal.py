"""Windows ConPTY for an official login command that requires real TTYs.

This only supplies terminal transport. The official child owns authorization,
storage and completion; it inherits the Workdesk worker's existing Windows Job.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import io
import os
from pathlib import Path
import subprocess
import sys
import threading


class Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


def _api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreatePipe": ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "ReadFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
        "WriteFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
        "CreatePseudoConsole": ([Coord, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
        "ClosePseudoConsole": ([w.HANDLE], None),
        "InitializeProcThreadAttributeList": ([ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        "UpdateProcThreadAttribute": ([ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                            ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ProcessInfo)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
    }
    try:
        for name, (args, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = args, result
    except AttributeError as exc:
        raise OSError("Official terminal login requires Windows ConPTY support") from exc
    return api


def _check(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())


class _Pipe:
    def __init__(self, api, handle, *, input=False):
        self.api, self.handle, self.input = api, handle, input
        self.closed = False

    def read1(self, size=4096):
        if self.closed:
            return b""
        buffer = ctypes.create_string_buffer(size)
        count = w.DWORD()
        if not self.api.ReadFile(self.handle, buffer, size, ctypes.byref(count), None):
            error = ctypes.get_last_error()
            if error in {109, 232, 233, 995}:
                return b""
            raise ctypes.WinError(error)
        return buffer.raw[:count.value]

    read = read1

    def write(self, data):
        if self.closed:
            raise BrokenPipeError("Official login terminal input is closed")
        # A terminal's Enter key is CR. The caller supplies exactly one line.
        raw = data.replace(b"\n", b"\r") if self.input else data
        count = w.DWORD()
        buffer = ctypes.create_string_buffer(raw)
        _check(self.api.WriteFile(self.handle, buffer, len(raw), ctypes.byref(count), None))
        if count.value != len(raw):
            raise BrokenPipeError("Official terminal input receipt is incomplete")
        return len(data)

    def flush(self):
        pass

    def close(self):
        if not self.closed:
            self.closed = True
            self.api.CloseHandle(self.handle)


class WindowsLoginProcess:
    def __init__(self, argv, *, cwd, env):
        self.api, self.argv = _api(), list(argv)
        self.returncode = None
        self._process = None
        self._console = None
        self._console_lock = threading.Lock()
        input_read, input_write, output_read, output_write = (w.HANDLE() for _ in range(4))
        attributes = None
        try:
            _check(self.api.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), None, 0))
            _check(self.api.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), None, 0))
            console = w.HANDLE()
            result = self.api.CreatePseudoConsole(Coord(120, 30), input_read, output_write, 0, ctypes.byref(console))
            if result < 0:
                raise OSError("Windows could not create the official login terminal")
            self._console = console
            size = ctypes.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            _check(self.api.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
            _check(self.api.UpdateProcThreadAttribute(attributes, 0, 0x00020016, console,
                                                      ctypes.sizeof(w.HANDLE), None, None))
            startup = StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            # Never inherit the JSON worker's redirected standard handles. The
            # bootstrap opens this pseudoconsole's handles before starting CLI.
            startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES, all initially NULL
            startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            info = ProcessInfo()
            # A process launched from redirected parent streams may retain those
            # handles despite being attached to ConPTY. Bind all three standard
            # handles to this console in a small same-Job bootstrap, then launch
            # the exact official argv. No shell or command-string evaluation.
            bootstrap = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--", *self.argv]
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(bootstrap))
            environment = ctypes.create_unicode_buffer("\0".join(k + "=" + str(v) for k, v in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
            # No BREAKAWAY_FROM_JOB and no visible console window. Native child
            # membership stays with the Go owner's inherited process Job.
            _check(self.api.CreateProcessW(sys.executable, command, None, None, False, 0x00080000 | 0x00000400,
                                           environment, cwd, ctypes.byref(startup), ctypes.byref(info)))
            self._process, self.pid = info.hProcess, info.dwProcessId
            self.api.CloseHandle(info.hThread)
            self.stdin = _Pipe(self.api, input_write, input=True)
            self.stdout = _Pipe(self.api, output_read)
            self.stderr = io.BytesIO()
            input_write, output_read = None, None
        except Exception:
            if self._console:
                self.api.ClosePseudoConsole(self._console)
                self._console = None
            raise
        finally:
            if attributes is not None:
                self.api.DeleteProcThreadAttributeList(attributes)
            for handle in (input_read, input_write, output_read, output_write):
                if handle:
                    self.api.CloseHandle(handle)
        self._monitor = threading.Thread(target=self._close_console_after_exit, daemon=True)
        self._monitor.start()

    def _close_console_after_exit(self):
        self.api.WaitForSingleObject(self._process, 0xFFFFFFFF)
        with self._console_lock:
            console, self._console = self._console, None
        if console:
            # Output is drained by the worker's reader while ConPTY closes.
            self.api.ClosePseudoConsole(console)

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        result = self.api.WaitForSingleObject(self._process, 0)
        if result == 258:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        _check(self.api.GetExitCodeProcess(self._process, ctypes.byref(code)))
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        milliseconds = 0xFFFFFFFF if timeout is None else max(0, min(0xFFFFFFFE, int(timeout * 1000)))
        if self.api.WaitForSingleObject(self._process, milliseconds) == 258:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.poll()

    def terminate(self):
        _check(self.api.TerminateProcess(self._process, 1))

    kill = terminate

    def close_transport(self):
        self.stdin.close()
        self._monitor.join(timeout=3)
        self.stdout.close()
        self.stderr.close()
        if self._process:
            self.api.CloseHandle(self._process)
            self._process = None


def _terminal_child(argv):
    import msvcrt
    console_input = os.open("CONIN$", os.O_RDWR)
    console_output = os.open("CONOUT$", os.O_WRONLY)
    try:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.SetStdHandle.argtypes, api.SetStdHandle.restype = [w.DWORD, w.HANDLE], w.BOOL
        for fd, source, standard in ((0, console_input, -10), (1, console_output, -11), (2, console_output, -12)):
            os.dup2(source, fd)
            _check(api.SetStdHandle(standard & 0xFFFFFFFF, msvcrt.get_osfhandle(fd)))
        child = subprocess.Popen(argv, stdin=0, stdout=1, stderr=2)
        # Remain attached until the official command exits. Otherwise the host's
        # monitor would close ConPTY before browser authorization can finish.
        return child.wait()
    finally:
        os.close(console_input)
        os.close(console_output)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        raise SystemExit(2)
    try:
        raise SystemExit(_terminal_child(sys.argv[2:]))
    except OSError:
        # Startup diagnostics must not expose arguments or environment values.
        raise SystemExit(1)
