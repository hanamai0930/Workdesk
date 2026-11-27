"""Exercise the final per-user installer with a disposable installation and legacy library copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import winreg


KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Workdesk-1.2-Windows-x64_is1'


def registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return Path(winreg.QueryValueEx(key, 'InstallLocation')[0]).resolve()
    except FileNotFoundError:
        return None


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def library(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        meta = dict(db.execute('SELECT * FROM meta'))
        return (meta['schema'], meta['store_id'], db.execute('SELECT count(*) FROM projects').fetchone()[0],
                db.execute('SELECT count(*) FROM work_items').fetchone()[0], db.execute('PRAGMA integrity_check').fetchone()[0])


def verify(root, fixture):
    if registered() is not None:
        raise ValueError('Existing registered Workdesk 1.2 installation; use a different test account.')
    root = root.resolve()
    trial = Path(tempfile.mkdtemp(prefix='Workdesk 1.2 安装验证 '))
    print(trial, flush=True)
    app = trial / '程序'
    legacy = trial / 'old-user'
    archive = legacy / 'libraries/main'
    shutil.copytree(fixture, archive)
    old_db = archive / 'tasks.sqlite3'
    old_sha, old_meta = sha(old_db), library(old_db)
    assert old_meta[0] == '1'
    env = dict(os.environ, LOCALAPPDATA=str(trial / 'local'), WORKDESK_RELEASE_DATA_HOME=str(legacy))
    (trial / 'local').mkdir()
    exe = root / 'Workdesk-Setup-1.2.0-x64.exe'
    quiet = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
    setup = [exe, *quiet, '/NOICONS', '/CURRENTUSER', '/DIR=' + str(app)]
    launcher = app / 'Workdesk.exe'
    py = [app / 'runtime/python/python.exe', '-I', '-B', '-X', 'utf8']
    state_cmd = [*py, app / 'tools/release_instance.py']

    def run(argv, *, data=None, success=True):
        result = subprocess.run(argv, input=data, cwd=trial, env=env, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=300)
        if (result.returncode == 0) != success:
            raise RuntimeError(f'{Path(argv[0]).name} returned {result.returncode}. See installer logs in {trial}')
        return result.stdout

    try:
        run([*setup, '/TASKS=import101', '/LOG=' + str(trial / 'install.log')])
        assert registered() == app
        receipt, = (trial / 'local').glob('Workdesk-v1.2/instances/*/user/config/v101-import.json')
        db = Path(json.loads(receipt.read_bytes())['data_root']) / 'tasks.sqlite3'
        migrated = library(db)
        assert migrated[0] == '3' and migrated[1:] == old_meta[1:] and sha(old_db) == old_sha
        print('Install and legacy import passed', flush=True)
        selector = app / 'Workdesk-Partners.exe'
        run([selector, '--check', '--package-dir', root / 'partners'])
        assert (app / 'selection.png').is_file() and (app / 'review.png').is_file()
        run([launcher, '--background'])
        status = json.loads(run([*state_cmd, 'status']))
        assert status['backend_state'] == status['ui_state'] == 'RUNNING' and status['channel'] == 'release'
        assert Path(status['data_root']) == db.parent
        run([launcher, '--check-stopped'], success=False)
        item = next(p for p in json.loads((root / 'partners/catalog.json').read_bytes())['partners'] if p['id'] == 'kimi')
        request = {k: item[k] for k in ('id', 'version', 'release_id')}
        request['bundle'] = str(root / 'partners' / item['file'])
        installed = json.loads(run([*py, app / 'tools/partner_command.py'], data=json.dumps(request)))
        assert installed['ok'] and installed['release_id'] == item['release_id']
        print('Installed selector, eight bundles, release services, and Kimi installation passed', flush=True)
        run([*state_cmd, 'stop'])
        run([launcher, '--check-stopped'])
        run([*setup, '/TASKS=', '/LOG=' + str(trial / 'update.log')])
        assert registered() == app and library(db) == migrated
        run([app / 'unins000.exe', *quiet, '/LOG=' + str(trial / 'uninstall.log')])
        assert registered() is None and not launcher.exists() and library(db) == migrated and sha(old_db) == old_sha
        record = dict(setup_sha256=sha(exe), install=True, legacy_import=True, selector_bundles=8,
                      dashboard_and_backend=True, running_guard=True, offline_component=installed['release_id'],
                      stop=True, in_place_reinstall=True, uninstall_kept_data=True, original_library_preserved=True)
        (root / 'installer-acceptance.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(record))
    finally:
        if registered() == app:
            run([*state_cmd, 'stop'])
            run([app / 'unins000.exe', *quiet])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--legacy-fixture', required=True, type=Path)
    args = parser.parse_args()
    verify(args.root, args.legacy_fixture)
