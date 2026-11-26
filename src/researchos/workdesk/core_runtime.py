"""Select an immutable Go core without falling back to a different execution owner."""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def selection(locations):
    if locations.user is None:
        return None
    path = Path(locations.user) / 'config/core.json'
    if not path.exists():
        return None
    value = read_json(path)
    if value.get('schema') != 1 or value.get('engine') != 'go':
        raise ValueError('应用核心选择无效；未回退旧核心。')
    manifest_path = Path(value['manifest']).resolve()
    core_root = (locations.base / 'runtime/workdesk-core').resolve()
    if not manifest_path.is_relative_to(core_root):
        raise ValueError('核心清单越出程序位置。')
    manifest = read_json(manifest_path)
    executable = Path(manifest['executable']).resolve()
    if not executable.is_relative_to(core_root) or not executable.is_file():
        raise ValueError('所选Go核心缺失，未回退旧核心。')
    if sha256(executable.read_bytes()).hexdigest() != manifest['binary_sha256']:
        raise ValueError('Go核心内容与清单不符。')
    return manifest


def expected_implementation(locations):
    current = selection(locations)
    if current is None:
        return None
    for name, expected in current['source_files'].items():
        path = (locations.base / name).resolve()
        if not path.is_relative_to(locations.base) or not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('源码已变化，请重新构建Go候选；未向旧实现发送新操作。')
    return current['implementation_revision']


def bound_revision(locations):
    from .bridge import enabled, binding
    if enabled():
        value = binding()
        if value and Path(value['data']).resolve() == Path(locations.data).resolve():
            return value['implementation']
    return expected_implementation(locations)


def snapshot_matches(locations, revision):
    from .workspace import io_path
    root = io_path(locations.data / 'implementations' / revision)
    saved = read_json(root / 'manifest.json')
    if sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest() != revision:
        return False
    for name, expected in saved.items():
        path = root / name
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def freeze(base, data, executable):
    base, data, executable = Path(base).resolve(), Path(data).resolve(), Path(executable).resolve()
    names = []
    for folder, suffixes in [(base / 'src/researchos/workdesk', {'.py', '.mjs'}),
                             (base / 'core', {'.go', '.mod', '.sum'}),
                             (base / 'contracts', {'.json'})]:
        names.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts)
    names.extend(p for p in (base / 'workdesk-ui').glob('*.py') if p.is_file())
    names.extend(p for p in (base / 'workdesk-ui/archive_sources').glob('*.py') if p.is_file())
    for folder in ('workdesk-ui/static', 'workdesk-ui/dist'):
        names.extend(p for p in (base / folder).rglob('*') if p.is_file() and not p.is_symlink())
    source = {p.relative_to(base).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sorted(names)}
    content = {p.relative_to(base).as_posix(): p.read_bytes() for p in names}
    binary_hash = sha256(executable.read_bytes()).hexdigest()
    # The stable snapshot path is independent from its eventual installation directory.
    content['core/workdesk-core.exe'] = executable.read_bytes()
    manifest = {name: sha256(raw).hexdigest() for name, raw in sorted(content.items())}
    revision = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    from .workspace import io_path
    root = io_path(data / 'implementations' / revision)
    root.mkdir(parents=True, exist_ok=True)
    for name, raw in content.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('不可变实现快照内容已变化。')
        if not target.exists():
            target.write_bytes(raw)
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    contract = read_json(base / 'contracts/core-v2.json')
    return {'schema': 1, 'engine': 'go', 'protocol': 2, 'storage_schema': contract['storage_schema'],
            'executable': str(executable), 'binary_sha256': binary_hash,
            'implementation_revision': revision, 'source_files': source}


def install_candidate(locations, executable):
    executable = Path(executable)
    digest = sha256(executable.read_bytes()).hexdigest()
    manifest = freeze(locations.base, locations.data, executable)
    target = locations.base / 'runtime/workdesk-core' / manifest['implementation_revision'] / 'workdesk-core.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError('候选程序内容异常。')
    if not target.exists():
        shutil.copyfile(executable, target)
    manifest['executable'] = str(target)
    path = target.parent / 'manifest.json'
    from .paths import atomic_json
    if path.exists() and read_json(path) != manifest:
        raise ValueError('已登记的不可变候选清单不符，未覆盖。')
    if not path.exists():atomic_json(path, manifest)
    return path, manifest


def start(locations, profile, *, fixture=False, manifest=None):
    manifest = manifest or selection(locations)
    if manifest is None:
        raise ValueError('没有选择Go候选。')
    expected_implementation(locations) if not fixture else None
    if (Path(locations.user) / 'runtime/development-stop.json').exists():
        raise ValueError('开发实例已请求停止，请从显式开发启动入口重开。')
    try:
        source = subprocess.run(['git', '-C', str(locations.base), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        source = 'UNKNOWN'
    args = [manifest['executable'], '--config', profile['config_path'], '--base', str(locations.base),
            '--implementation', manifest['implementation_revision'], '--source', source]
    if fixture:
        args.append('--fixture')
    dirty = subprocess.run(['git', '-C', str(locations.base), 'status', '--porcelain', '--',
                            'src', 'core', 'contracts', 'workdesk-ui', 'tools/dev_instance.py'],
                           capture_output=True, text=True,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if dirty.returncode or dirty.stdout.strip():
        args.append('--source-dirty')
    log = Path(locations.user) / 'runtime/core-launch.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as stream:
        process = subprocess.Popen(args, cwd=locations.base, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=stream,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return process


def prepare_migration(profile, executable, *, fixture=False):
    args = [str(executable), '--config', profile['config_path'], '--base', profile['program_root'], '--migrate-only']
    if fixture:
        args.append('--fixture')
    result = subprocess.run(args, cwd=profile['program_root'], capture_output=True, text=True,
                            encoding='utf-8', timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Go资料迁移未通过。')
    return json.loads(result.stdout)
