"""Explicit backed-up stage-two cutover. Never selects the daily user root."""
from __future__ import annotations
import argparse
from hashlib import sha256
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'src'))
from researchos.workdesk.instance import load_profile
from researchos.workdesk.paths import Locations, atomic_json
from researchos.workdesk import core_runtime


def dev_module():
    spec = importlib.util.spec_from_file_location('stage2_dev', BASE / 'tools/dev_instance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade(candidate: Path, backup: Path):
    profile = load_profile(BASE)
    if not profile or Path(profile['program_root']).resolve() != BASE.resolve():
        raise ValueError('仅操作已登记的开发副本。')
    dev = dev_module()
    state = dev.status(profile)
    if state['backend_state'] != 'STOPPED' or state['ui_state'] != 'STOPPED':
        raise ValueError('先从开发停止入口安全停止，不接管日用。')
    root = Path(profile['development_root']).resolve()
    backup = backup.resolve()
    if not backup.is_relative_to(root / 'recovery') or backup.is_symlink():
        raise ValueError('须指定本开发实例 recovery 中的标准升级前备份。')
    export = json.loads((backup / 'development-export.json').read_text(encoding='utf-8'))
    if export.get('instance_id') != profile['instance_id']:
        raise ValueError('备份不属于本开发实例。')
    if Path(export.get('source_data', '')).resolve() != Path(profile['data_root']).resolve():
        raise ValueError('备份来源不是当前开发活动库。')
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if export.get('manifest_hash') != manifest.get('manifest_hash'):
        raise ValueError('导出回执与备份清单不一致。')
    for _name, _raw in dev.package_files(backup, manifest):
        pass
    # Only the offline backup database is opened by this helper. Active-library
    # migration is exclusively the candidate Go executable under backend.lock.
    with sqlite3.connect((backup / 'state/tasks.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]
    if version not in ('2', '3'):
        raise ValueError('伙伴迁移须有格式2或3的准确备份。')
    locations = Locations.resolve(BASE)
    previous = core_runtime.selection(locations)
    if not previous:
        raise ValueError('缺少原Go核心选择，保留资料。')
    build = json.loads(candidate.with_name(candidate.name + '.build.json').read_text(encoding='utf-8-sig'))
    if build.get('sha256') != sha256(candidate.read_bytes()).hexdigest():
        raise ValueError('候选程序与构建回执不一致。')
    inputs = build.get('source_files')
    expected_names = {p.relative_to(BASE).as_posix() for p in (BASE / 'core').rglob('*') if p.is_file() and p.suffix in {'.go','.mod','.sum'}}
    if not isinstance(inputs, dict) or set(inputs) != expected_names:
        raise ValueError('候选缺少完整Go编译输入清单。')
    for name, digest in inputs.items():
        if sha256((BASE / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Go源码已在构建后改变，请重新构建候选。')
    path, candidate_manifest = core_runtime.install_candidate(locations, candidate)
    atomic_json(root / 'artifacts/stage2/cutover-intent.json', {
        'schema': 1, 'state': 'PREPARED_NOT_MIGRATED', 'backup': str(backup),
        'backup_manifest_hash': manifest['manifest_hash'], 'previous_manifest': previous,
        'candidate_manifest': str(path), 'data_root': str(locations.data),
        'implementation_revision': candidate_manifest['implementation_revision']})
    args = [candidate_manifest['executable'], '--config', profile['config_path'], '--base', str(BASE), '--migrate-partners-only']
    result = subprocess.run(args, cwd=BASE, capture_output=True, encoding='utf-8', timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    if result.returncode:
        raise ValueError(result.stderr.strip() or '伙伴资料迁移失败，未替换原核心选择。')
    migrated = json.loads(result.stdout)
    if migrated.get('storage_schema') != 3 or migrated.get('state') != 'PARTNERS_MIGRATED':
        raise ValueError('迁移回执未核对；不自动重试。')
    if Path(migrated.get('data_root', '')).resolve() != locations.data.resolve():
        raise ValueError('迁移回执资料位置不符，保留检查点，未切换核心。')
    atomic_json(locations.user / 'config/core.json', {'schema': 1, 'engine': 'go',
                'instance_id': profile['instance_id'], 'manifest': str(path)})
    record = {'schema': 1, 'state': 'STAGE2_CANDIDATE_SELECTED', 'recorded_utc': datetime.now(timezone.utc).isoformat(),
              'instance_id': profile['instance_id'], 'data_root': str(locations.data),
              'backup': str(backup), 'previous_manifest': previous, 'manifest': str(path),
              'implementation_revision': candidate_manifest['implementation_revision'], 'storage_schema': 3,
              'no_dispatch': True, 'stop_intent_preserved': state['stop_requested']}
    atomic_json(root / 'artifacts/stage2/cutover.json', record)
    return {k: record[k] for k in ('state', 'manifest', 'implementation_revision', 'storage_schema', 'no_dispatch')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('upgrade',))
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--backup', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(upgrade(args.candidate, args.backup), ensure_ascii=False))


if __name__ == '__main__':
    main()
