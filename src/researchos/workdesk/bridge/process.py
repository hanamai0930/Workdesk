"""Preserve native Popen behavior while the Go owner admits and tracks children."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import threading

from . import call


ORIGINAL_POPEN = subprocess.Popen


def describe(command):
    # Arguments can contain credentials or task content. Keep an exact digest,
    # executable and script identity; ordinary process records contain no values.
    if isinstance(command, (str, bytes, os.PathLike)):
        text = os.fsdecode(command)
        return ['<command sha256=' + sha256(text.encode('utf-8')).hexdigest() + '>']
    values = [os.fsdecode(value) for value in command]
    digest = sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    visible = values[:1]
    if len(values) > 1 and Path(values[1]).is_absolute() and Path(values[1]).suffix.lower() in {'.py', '.mjs', '.js', '.exe'}:
        visible.append(values[1])
    return [*visible, '<arguments sha256=' + digest + '>']


class CorePopen(ORIGINAL_POPEN):
    def __init__(self, *args, **kwargs):
        command = args[0] if args else kwargs.get('args')
        intent = call('process.intent', {'command': describe(command)})
        self._core_id = intent['id']
        self._core_registered = False
        self._core_reported = False
        self._core_lock = threading.Lock()
        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            try:
                call('process.exited', {'id': self._core_id, 'state': 'FAILED_TO_START', 'returncode': None})
            except (OSError, RuntimeError, ValueError):
                pass
            raise
        from ..paths import process_identity
        observed = process_identity(self.pid)
        executable = kwargs.get('executable')
        if executable is None and isinstance(command, (list, tuple)) and command:
            executable = os.fsdecode(command[0])
        try:
            call('process.started', {'id': self._core_id, 'pid': self.pid,
                                    'creation': observed.get('creation'), 'executable': os.fsdecode(executable) if executable else None})
            self._core_registered = True
        except BaseException:
            # This handle belongs to the process just created here. Do not leave
            # an unregistered native child running after owner loss.
            try:
                ORIGINAL_POPEN.terminate(self)
                ORIGINAL_POPEN.wait(self, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise
        threading.Thread(target=self._observe_exit, daemon=True, name='workdesk-owned-child').start()

    def _observe_exit(self):
        try:
            ORIGINAL_POPEN.wait(self)
            self._report_exit()
        except (OSError, RuntimeError, ValueError):
            pass

    def _report_exit(self):
        with self._core_lock:
            if not self._core_registered or self._core_reported or self.returncode is None:
                return
            self._core_reported = True
        try:
            call('process.exited', {'id': self._core_id, 'pid': self.pid, 'returncode': self.returncode})
        except (OSError, RuntimeError, ValueError):
            # The prior intent remains available for core-side reconciliation.
            pass

    def poll(self):
        result = super().poll()
        self._report_exit()
        return result

    def wait(self, timeout=None):
        result = super().wait(timeout=timeout)
        self._report_exit()
        return result


def install():
    subprocess.Popen = CorePopen
