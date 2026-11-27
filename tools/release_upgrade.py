"""Import an installed v1.0.1 ledger into the separate v1.2 installation."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

TOOLS = Path(__file__).resolve().parent
sys.path[:0] = [str(TOOLS), str(TOOLS.parent / 'src')]
from researchos.workdesk import workspace
from researchos.workdesk.instance import protected_roots
from researchos.workdesk.library_assets import rebind_catalog
from researchos.workdesk.operations import Operations
from researchos.workdesk.paths import atomic_json, data_owner_lock
from researchos.workdesk.persistence import Persistence
from researchos.workdesk.store import ACTIVE, UNKNOWN, canonical, digest


DATA_DIRS = ('tasks', 'results', 'capabilities', 'integration-journals', 'implementations', 'unfiled-work', 'handoffs')
COUNTS = ('projects', 'tasks', 'attempts', 'events', 'work_items', 'requests')
MARKER = '.v101-import.json'
RECEIPT = 'v101-import.json'
INTENT = 'v101-import-intent.json'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def local_path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or str(path).startswith('\\\\'):
        raise ValueError('资料位置必须是本机绝对路径。')
    workspace.plain(path, Path(path.anchor), missing=True)
    return path.resolve()


def overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def legacy_library(profile, source_user):
    root = local_path(source_user)
    # The old release supports a separately configured active library. Inspect
    # only its location pointer after ruling out the daily and development roots.
    restricted = protected_roots({'protected_roots': [r'C:\Workdesk-dev\user']})
    restricted += [local_path(profile['user_root']).parent, local_path(profile['program_root'])]
    if any(overlap(root, blocked) for blocked in restricted):
        raise ValueError('旧安装资料的位置与日用、开发或新版程序资料重叠。')
    pointer = local_path(root / 'config/locations.json')
    if pointer.exists():
        value = read_json(pointer)
        if not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('active_data'), str):
            raise ValueError('旧安装版的资料位置登记无效。')
        source = local_path(value['active_data'])
    else:
        source = local_path(root / 'libraries/main')
    if source == Path(source.anchor) or source == root or any(overlap(source, blocked) for blocked in restricted):
        raise ValueError('旧资料库的位置与受保护资料重叠。')
    if not local_path(source / 'tasks.sqlite3').is_file():
        raise ValueError('未找到 v1.0.1 安装版的资料库。')
    if (source / 'retired-target.json').exists():
        raise ValueError('旧资料库已切换或切换回执待核对。请先在 v1.0.1 核对当前资料库。')
    return source


def read_db(directory):
    db = sqlite3.connect((directory / 'tasks.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return closing(db)


def ledger(db, schema):
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('资料库完整性检查失败。')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR upper(sql) LIKE 'CREATE VIRTUAL TABLE%'").fetchone():
        raise ValueError('资料库包含无法识别的结构。')
    meta = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('schema','store_id','retired_to')"))
    if meta.get('schema') != schema or not meta.get('store_id') or 'retired_to' in meta:
        raise ValueError('资料库版本、台账身份或当前归属与导入要求不符。')
    return {'store_id': meta['store_id'], 'counts': {
        table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in COUNTS}}


def require_idle(db):
    latest = db.execute('SELECT * FROM attempts a WHERE started=(SELECT MAX(started) FROM attempts WHERE task_id=a.task_id)')
    if any(row['state'] in ACTIVE | UNKNOWN or not Operations.exit_proved(row) for row in latest):
        raise ValueError('旧资料库仍有在途或退出情况待核对的工作。请在 v1.0.1 中核对后关闭程序。')
    debt = db.execute("SELECT data FROM usage_offsets WHERE scope='global' AND id='all'").fetchone()
    if db.execute("SELECT 1 FROM requests WHERE state='UNKNOWN' LIMIT 1").fetchone() or (debt and json.loads(debt[0]).get('unknown_requests')):
        raise ValueError('旧资料库存在未知的请求回执，请先在原版核对。')


def data_files(root):
    def fail(error):
        raise error

    for name in DATA_DIRS:
        directory = root / name
        workspace.plain(directory, root, missing=True)
        if not directory.exists():
            continue
        for parent, dirs, files in os.walk(workspace.io_path(directory), onerror=fail):
            relative = Path(name) / Path(parent).relative_to(workspace.io_path(directory))
            dirs[:] = sorted(d for d in dirs if d not in {'.git', '__pycache__'} and
                              not (d == 'native-home' and relative.parts[0] == 'tasks' and len(relative.parts) == 2))
            for child in dirs:
                workspace.plain(root / relative / child, root)
            for child in sorted(files):
                if child.lower() in {'.env', 'auth.json', 'credentials.json'}:
                    continue
                path = relative / child
                workspace.plain(root / path, root)
                yield path.as_posix()


def file_record(path):
    with workspace.io_path(path).open('rb') as stream:
        h = sha256()
        size = 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
            size += len(block)
    return {'bytes': size, 'sha256': h.hexdigest()}


def copy_file(source, destination):
    target = workspace.io_path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with workspace.io_path(source).open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def verified_backup(source, recovery):
    state = recovery / 'state'
    state.mkdir(parents=True, exist_ok=False)
    with read_db(source) as old:
        identity = ledger(old, '1')
        require_idle(old)
        with closing(sqlite3.connect(state / 'tasks.sqlite3')) as dest:
            old.backup(dest)
    files = {'tasks.sqlite3': file_record(state / 'tasks.sqlite3')}
    for name in data_files(source):
        copy_file(source / name, state / name)
        files[name] = file_record(state / name)
    source_names = set(data_files(source))
    if source_names != files.keys() - {'tasks.sqlite3'} or any(file_record(source / n) != files[n] for n in source_names):
        raise ValueError('备份期间旧资料发生变化，请关闭原版后再导入。')
    with read_db(state) as restored:
        if ledger(restored, '1') != identity:
            raise ValueError('备份与原台账记录不一致。')
        Persistence.cross_check(restored, files, asset_root=state)
    value = {'schema': 1, 'source': str(source), **identity, 'files': files}
    value['manifest_hash'] = digest(value)
    atomic_json(recovery / 'manifest.json', value)
    return value


def prepare_copy(stage, target, recovery, manifest):
    for name, expected in manifest['files'].items():
        copy_file(recovery / 'state' / name, stage / name)
        if file_record(stage / name) != expected:
            raise ValueError('新版资料副本未通过备份逐文件校验。')
    # Only change mutable settings in the copy. Keep task inputs, results,
    # usage, official receipts, and versioned records at their original values.
    reason = '从 v1.0.1 导入，请核对后手动继续。'
    with closing(sqlite3.connect(stage / 'tasks.sqlite3')) as db, db:
        rebind_catalog(db, target)
        # Current workspace and project paths inside the old library must follow
        # their copied files. External project folders keep their registered path.
        original = Path(manifest['source'])
        for table, field in [('projects', 'root'), ('attempts', 'workspace')]:
            for identity, value in db.execute(f'SELECT id,{field} FROM {table}').fetchall():
                path = Path(value)
                if not path.is_relative_to(original):
                    continue
                rel = local_path(path).relative_to(original)
                if not rel.parts or rel.parts[0] not in DATA_DIRS:
                    raise ValueError('项目或工作副本位于旧库中未包含的位置。请在原版核对位置后导入。')
                if workspace.io_path(path).is_dir():
                    workspace.io_path(stage / rel).mkdir(parents=True, exist_ok=True)
                db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (str(target / rel), identity))
        retire_credential_references(db)
        db.execute('UPDATE tasks SET grant=NULL WHERE grant IS NOT NULL')
        db.execute("UPDATE approvals SET state='CANCELLED' WHERE state='PENDING'")
        db.execute("UPDATE native_requests SET state='CANCELLED' WHERE state IN ('PENDING','READY','REPLY_INTENT')")
        db.execute("UPDATE collaboration SET data=json_set(data,'$.state','HISTORICAL_RESTORED') "
                   "WHERE kind IN ('restore','relocation')")
        for kind, identity, raw in db.execute("SELECT kind,id,data FROM collaboration WHERE kind IN ('goal_job','scheduled_job','ui_schedule','cooperative_job','work_flow','parallel','plan')").fetchall():
            value = json.loads(raw)
            changed = False
            for key in ('state', 'queue_state'):
                if value.get(key) in {'ACTIVE', 'RUNNING', 'SCHEDULED', 'QUEUED'}:
                    value[key] = 'PAUSED'
                    changed = True
            if changed:
                value.update(reason=reason, revision=str(uuid.uuid4()))
                db.execute('UPDATE collaboration SET data=? WHERE kind=? AND id=?', (canonical(value), kind, identity))


def retire_credential_references(db):
    # Catalog refresh validates registered references, including reading API files.
    # Keep the old reference as history and require a new explicit registration.
    identities = set()
    for record_id, raw in db.execute("SELECT id,data FROM collaboration WHERE kind='resource_connection_reference'").fetchall():
        identities.add(json.loads(raw)['connection_id'])
        db.execute("UPDATE collaboration SET id=?,kind='v101_connection_history' WHERE id=?", ('v101-history:' + record_id, record_id))
    # Resuming a frozen task can look up its old connection independently of the
    # current catalog. Disable that exact connection in its mutable metadata.
    for (raw,) in db.execute('SELECT spec FROM tasks'):
        spec = json.loads(raw)
        bound = spec.get('execution_connection') or (spec.get('execution_resource') or {}).get('connection')
        if bound:
            identities.add(bound['id'])
    for identity in identities:
        row = db.execute("SELECT data FROM collaboration WHERE kind='resource_connection' AND id=?", (identity,)).fetchone()
        value = json.loads(row[0]) if row else {'id': identity, 'revision': 0}
        value.update(enabled=False, revision=value['revision'] + 1)
        db.execute("INSERT INTO collaboration(kind,id,data,updated) VALUES('resource_connection',?,?,?) "
                   'ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                   (identity, canonical(value), time.time()))


def migrate(profile, option):
    base = Path(profile['program_root'])
    binary = base / 'runtime/workdesk-core/workdesk-core.exe'
    command = [str(binary), '--config', profile['config_path'], '--base', str(base), option]
    result = subprocess.run(command, cwd=base, capture_output=True, text=True, encoding='utf-8',
                            timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('升级核心未完成资料转换。' + result.stderr.strip()[:300])
    return json.loads(result.stdout)


def import_library(profile, source_user, run_migration=migrate):
    user = local_path(profile['user_root'])
    target = local_path(profile['data_root'])
    if target != user / 'libraries/main':
        raise ValueError('新版已经切换资料库，无法导入到当前库。')
    config = local_path(user / 'config')
    intent, receipt = config / INTENT, config / RECEIPT
    if receipt.is_file():
        value = read_json(receipt)
        with read_db(target) as db:
            if ledger(db, '3')['store_id'] != value['store_id']:
                raise ValueError('已导入回执与当前资料库身份不符。')
        if intent.exists():
            if read_json(intent)['id'] != value['id']:
                raise ValueError('导入回执与待处理导入身份不符。')
            intent.unlink()
        return value
    recovery_root = local_path(user.parent / 'recovery')
    if intent.exists():
        previous = str(uuid.UUID(read_json(intent)['id']))
        if target.exists():
            marker = target / MARKER
            if not marker.is_file() or read_json(marker) != {'id': previous}:
                raise ValueError('新版库已有其他资料，无法自动恢复上次导入。')
            quarantine = local_path(recovery_root / previous / 'interrupted-copy')
            if quarantine.exists():
                raise ValueError('上次导入的保留副本与待恢复位置冲突。')
            target.rename(quarantine)
        intent.unlink()
    if target.exists() and any(target.iterdir()):
        raise ValueError('该 v1.2 安装已使用。请另选一个空安装目录导入，原有资料保留。')
    source = legacy_library(profile, source_user)
    identity = str(uuid.uuid4())
    recovery = recovery_root / identity
    stage = target.parent / ('.import-' + identity)
    with data_owner_lock(source):
        manifest = verified_backup(source, recovery)
        stage.mkdir(parents=True, exist_ok=False)
        prepare_copy(stage, target, recovery, manifest)
        atomic_json(stage / MARKER, {'id': identity})
        if target.exists():
            target.rmdir()  # OS rejects a nonempty destination even after the earlier check.
        atomic_json(intent, {'id': identity})
        stage.rename(target)
        for option, state, version in [('--migrate-only', 'MIGRATED', '2'), ('--migrate-partners-only', 'PARTNERS_MIGRATED', '3')]:
            result = run_migration(profile, option)
            if result['state'] != state or str(result['storage_schema']) != version or local_path(result['data_root']) != target:
                raise ValueError('资料转换回执与已登记资料库不符。')
        with read_db(target) as db:
            actual = ledger(db, '3')
            if actual['store_id'] != manifest['store_id'] or actual['counts'] != manifest['counts']:
                raise ValueError('升级后台账身份或历史记录数量不符。')
        value = {'state': 'IMPORTED', 'id': identity, 'source': str(source), 'data_root': str(target),
                 'backup': str(recovery), 'manifest_hash': manifest['manifest_hash'], **actual}
        atomic_json(receipt, value)
        intent.unlink()
        return value


def summary(value):
    counts = value['counts']
    return (f"已导入 {counts['projects']} 个项目和 {counts['tasks']} 个任务。\n\n"
            f"新版资料库：{value['data_root']}\n已核对的旧版备份：{value['backup']}\n\n"
            f"需要回到旧版时，关闭 v1.2 后启动原 v1.0.1。原资料库：{value['source']}\n"
            "两版各自保存后续修改。账号及 API Key 在新版连接设置中配置。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('import', 'status'))
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    code = 0
    try:
        import release_instance
        profile = release_instance.require_profile()
        source_user = os.environ.get('WORKDESK_RELEASE_DATA_HOME') or Path.home() / '.workdesk-release'
        config = Path(profile['user_root']) / 'config'
        with release_instance.lifecycle_lock(profile):
            if args.action == 'import':
                message = summary(import_library(profile, source_user))
            elif (config / RECEIPT).is_file():
                message = summary(read_json(config / RECEIPT))
            elif (config / INTENT).exists():
                message = '上次资料导入已中断，请再次选择“导入 v1.0.1 资料”。'
            else:
                message = '此安装尚未导入旧版资料。'
    except Exception as error:
        code, message = 1, '导入未完成。\n' + str(error)
    if args.notify and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Workdesk · v1.0.1 资料', (0x10 if code else 0x40) | 0x10000)
    elif sys.stdout is not None:
        print(message)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
