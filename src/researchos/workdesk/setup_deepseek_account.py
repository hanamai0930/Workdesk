"""Hand off DeepSeek account sign-in to the installed official Harness."""

import os
from pathlib import Path


def _desktop():
    # The official Windows per-user installer location. The request cannot
    # specify an executable, URL, arguments, or a credential directory.
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    root = Path(local)
    if not root.is_absolute() or str(root).startswith('\\\\'):
        return None
    return root / 'Programs' / 'DeepSeek Harness' / 'DeepSeek Harness.exe'


def available(_locations):
    executable = _desktop()
    return bool(os.name == 'nt' and executable and executable.is_file())


def launch(_locations):
    executable = _desktop()
    if os.name != 'nt' or not executable or not executable.is_file():
        raise ValueError('本机未找到官方 DeepSeek Harness。')
    try:
        os.startfile(str(executable), 'open', cwd=str(executable.parent))
    except OSError:
        raise ValueError('无法打开官方 DeepSeek Harness，请在本机启动后登录。') from None
    # The official app owns its account lifecycle. A launch receipt does not
    # attest to sign-in or to Workdesk dispatch authorization.
    return {'state': 'OPENED'}
