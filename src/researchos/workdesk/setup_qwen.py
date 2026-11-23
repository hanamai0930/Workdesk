"""Launch a subscription-capable Qwen Code separately from Workdesk tasks."""

import json
import os
from pathlib import Path
import re
import subprocess

from . import workspace
from .paths import clean_env


GUIDE = 'https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/#option-1-qwen-oauth--qwen-account-with-subscription'


def _entry(locations):
    if not locations.user or not locations.node.is_file():
        return None
    side_by_side = locations.runtime / 'qwen-subscription/node_modules/@qwen-code'
    bundled = locations.runtime / 'qwen/node_modules/@qwen-code'
    candidates = [
        (side_by_side / 'qwen-code', '@qwen-code/qwen-code', 'cli-entry.js', (0, 27, 2)),
        (side_by_side / 'sdk', '@qwen-code/sdk', 'dist/cli/cli.js', (0, 1, 35)),
        (bundled / 'sdk', '@qwen-code/sdk', 'dist/cli/cli.js', (0, 1, 35)),
    ]
    if appdata := os.environ.get('APPDATA'):
        npm = Path(appdata) / 'npm/node_modules/@qwen-code/qwen-code'
        candidates.append((npm, '@qwen-code/qwen-code', 'cli-entry.js', (0, 27, 2)))
    for package, name, entry, minimum in candidates:
        try:
            metadata = workspace.plain(package / 'package.json', package)
            if metadata.stat().st_size > 32_768:
                continue
            value = json.loads(metadata.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('name') != name:
                continue
            version = re.fullmatch(r'(\d{1,4})\.(\d{1,4})\.(\d{1,4})', str(value.get('version', '')))
            if not version or tuple(map(int, version.groups())) < minimum:
                continue
            candidate = workspace.plain(package / entry, package)
            if candidate.is_file():
                return candidate
        except (OSError, ValueError):
            continue
    return None


def available(locations):
    """Whether an installed Qwen Code release can open subscription setup."""
    return _entry(locations) is not None


def launch(locations):
    """Open Qwen Code without creating a Workdesk connection or model turn."""
    entry = _entry(locations)
    if entry is None:
        raise ValueError('当前未安装支持订阅登录的 Qwen Code。')
    root = workspace.plain(locations.user / 'providers/qwen/subscription', locations.user, missing=True)
    working = workspace.plain(root / 'workspace', locations.user, missing=True)
    working.mkdir(parents=True, exist_ok=True)
    env = clean_env()
    env.update(QWEN_HOME=str(root / 'config'), QWEN_RUNTIME_DIR=str(root / 'runtime'), QWEN_CODE_NO_RELAUNCH='1',
               HOME=str(root), USERPROFILE=str(root), HOMEDRIVE=root.drive, HOMEPATH=str(root)[len(root.drive):])
    try:
        subprocess.Popen([str(locations.node), str(entry), '--safe-mode'], cwd=working, env=env,
                         creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)
    except OSError:
        raise ValueError('暂时无法打开官方 Qwen Code 登录，请重试。') from None
    return {'state': 'OPENED'}
