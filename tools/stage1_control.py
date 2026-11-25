"""Stage-one development cutover with explicit offline backup and owner checks."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'src'))
from researchos.workdesk.instance import load_profile
from researchos.workdesk.paths import Locations, atomic_json, data_owner_lock
from researchos.workdesk import core_runtime


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def write(path, value):
    atomic_json(Path(path), value)
    return value


def protected_snapshot():
    daily = Path(r'C:\Workdesk')
    names = ['Workdesk.cmd', 'src/researchos/workdesk/paths.py', 'src/researchos/workdesk/client.py',
             'src/researchos/workdesk/instance.py', 'src/researchos/workdesk/server.py',
             'src/researchos/workdesk/service.py', 'src/researchos/workdesk/store.py',
             'docs/WORKDESK_CONTINUATION_STATE_20261026.json']
    paths = [daily / name for name in names]
    paths.append(Path.home() / '.workdesk/config/locations.json')
    return {str(path): sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in paths}


def baseline(profile):
    locations = Locations.resolve(BASE)
    if core_runtime.selection(locations) is not None:
        raise ValueError('Go已经选用，不能把升级后资料记成迁移前基线。')
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('stage1_dev', BASE / 'tools/dev_instance.py')
    dev = module_from_spec(spec)
    spec.loader.exec_module(dev)
    state = dev.status(profile)
    if state['backend_state'] != 'STOPPED' or state['ui_state'] != 'STOPPED':
        raise ValueError('请先通过开发停止入口安全退出，当前不备份运行中实例。')
    destination = Path(profile['development_root']) / 'recovery' / ('stage1-before-' + stamp())
    destination.mkdir(parents=True, exist_ok=False)
    data = Path(profile['data_root'])
    data_copy = destination / 'data'
    data_copy.mkdir()
    with data_owner_lock(data):
        with sqlite3.connect((data / 'tasks.sqlite3').as_uri() + '?mode=ro', uri=True) as source:
            schema = source.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]
            if schema != '1':
                raise ValueError('迁移前资料必须仍是v1格式。')
            with sqlite3.connect(data_copy / 'tasks.sqlite3') as target:
                source.backup(target)
        # Preserve application records and immutable versions, without account/native homes.
        excluded = {'native-home', 'native_home', 'home', '__pycache__', 'node_modules', 'runtime',
                    'auth.json', 'credentials.json', 'oauth_creds.json', 'provider-keys.env'}
        for name in ('tasks', 'implementations', 'results', 'capabilities', 'integration-journals'):
            folder = data / name
            if folder.is_dir():
                shutil.copytree(folder, data_copy / name,
                                ignore=lambda _folder, names: [n for n in names if n in excluded or n.startswith('.env')])
    bundle = destination / 'source.bundle'
    result = subprocess.run(['git', 'bundle', 'create', str(bundle), 'HEAD'], cwd=BASE,
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('源码bundle保存失败：' + result.stderr)
    files = {p.relative_to(data_copy).as_posix(): {'sha256': sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
             for p in data_copy.rglob('*') if p.is_file()}
    value = {'schema': 1, 'stage': 1, 'state': 'PRE_MIGRATION_SAVED', 'program_root': str(BASE),
             'data_root': str(data), 'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=BASE, text=True).strip(),
             'source_bundle': str(bundle), 'data_backup': str(data_copy), 'files': files,
             'daily_protection': protected_snapshot(), 'account_directories_copied': False}
    write(destination / 'manifest.json', value)
    evidence = Path(profile['development_root']) / 'artifacts/stage1/pre-migration.json'
    return write(evidence, value)


def activate(profile, candidate):
    locations = Locations.resolve(BASE)
    if core_runtime.selection(locations) is not None:
        raise ValueError('Go核心已选用；重建候选请走明确更新核对，不重复迁移。')
    evidence = Path(profile['development_root']) / 'artifacts/stage1/pre-migration.json'
    previous = core_runtime.read_json(evidence)
    if previous['daily_protection'] != protected_snapshot():
        raise ValueError('日用保护目标在基线后发生变化，请先核对来源。')
    if Path(previous['data_root']).resolve() != locations.data:
        raise ValueError('活动库已变化，不能复用另一库备份。')
    # Hold the existing stop intention across the cutover, so an old window cannot
    # restart Python between the offline checks and Go acquiring the owner lock.
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('stage1_stop', BASE / 'tools/dev_instance.py')
    dev = module_from_spec(spec)
    spec.loader.exec_module(dev)
    dev.stop(profile)
    with data_owner_lock(locations.data):
        def logical_database(path):
            with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as connection:
                return sha256('\n'.join(connection.iterdump()).encode('utf-8')).hexdigest()
        if logical_database(locations.data / 'tasks.sqlite3') != logical_database(Path(previous['data_backup']) / 'tasks.sqlite3'):
            raise ValueError('开发资料在备份后发生变化，请重新保存迁移前备份。')
        from researchos.workdesk.workspace import io_path
        for name, saved in previous['files'].items():
            if name == 'tasks.sqlite3':continue
            path = io_path(locations.data / name)
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != saved['sha256']:
                raise ValueError('备份后的资料文件发生变化：' + name)
    migration = core_runtime.prepare_migration(profile, candidate)
    path, manifest = core_runtime.install_candidate(locations, candidate)
    write(locations.user / 'config/core.json', {'schema': 1, 'engine': 'go',
                                             'instance_id': profile['instance_id'], 'manifest': str(path)})
    return write(Path(profile['development_root']) / 'artifacts/stage1/cutover.json',
                 {'state': 'GO_SELECTED', 'migration': migration,
                  'manifest': str(path), 'implementation_revision': manifest['implementation_revision'],
                  'pre_migration': str(evidence), 'daily_unchanged': protected_snapshot() == previous['daily_protection']})


def update_candidate(profile, candidate):
    locations = Locations.resolve(BASE)
    previous = core_runtime.selection(locations)
    if previous is None:raise ValueError('尚未切换Go，请先完成明确迁移。')
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('stage1_update', BASE / 'tools/dev_instance.py')
    dev = module_from_spec(spec)
    spec.loader.exec_module(dev)
    state = dev.status(profile)
    if state['backend_state'] != 'STOPPED' or state['ui_state'] != 'STOPPED':
        raise ValueError('核心更新前须先通过原开发入口安全停止。')
    path, manifest = core_runtime.install_candidate(locations, candidate)
    write(locations.user / 'config/core.json', {'schema': 1, 'engine': 'go',
                                             'instance_id': profile['instance_id'], 'manifest': str(path)})
    return write(Path(profile['development_root']) / 'artifacts/stage1/candidate-update.json',
                 {'state': 'GO_CANDIDATE_UPDATED', 'previous_implementation': previous['implementation_revision'],
                  'manifest': str(path), 'implementation_revision': manifest['implementation_revision'],
                  'data_root_unchanged': str(locations.data)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('baseline', 'activate', 'update', 'protect-check'))
    parser.add_argument('--candidate')
    args = parser.parse_args()
    profile = load_profile(BASE)
    if not profile or Path(profile['program_root']).resolve() != BASE:
        raise ValueError('仅操作登记的开发实例。')
    if args.action == 'baseline':
        result = baseline(profile)
        print(json.dumps({k: result[k] for k in ('state', 'source_bundle', 'data_backup')}, ensure_ascii=False))
    elif args.action in ('activate', 'update'):
        if not args.candidate:raise ValueError('请指定检查通过的Go候选。')
        action = activate if args.action == 'activate' else update_candidate
        print(json.dumps(action(profile, Path(args.candidate).resolve()), ensure_ascii=False))
    else:
        previous = core_runtime.read_json(Path(profile['development_root']) / 'artifacts/stage1/pre-migration.json')
        if previous['daily_protection'] != protected_snapshot():raise ValueError('日用保护核对不一致。')
        print('日用程序、资料指针与旧维护接续状态未变。')


if __name__ == '__main__':
    main()
