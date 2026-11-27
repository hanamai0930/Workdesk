"""Local installation identity. This separates state; it is not a tool sandbox."""
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import uuid


SOURCE_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=32)
def _marks(base, stamp):
    result = subprocess.run(['git', '-C', base, 'config', '--local', '--get-regexp', r'^workdesk\.'],
                            capture_output=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode not in (0, 1):
        raise ValueError('无法读取本机实例标记；未启动服务。')
    return dict(line.split(None, 1) for line in result.stdout.splitlines() if len(line.split(None, 1)) == 2)


def release_state_root(base):
    local = os.environ.get('LOCALAPPDATA')
    if not local or not Path(local).is_absolute():
        raise ValueError('无法确定当前用户的安装版资料目录。')
    identity = sha256(os.path.normcase(str(Path(base).resolve())).encode('utf-8')).hexdigest()[:16]
    root = Path(local).resolve() / 'Workdesk-v1.2' / 'instances' / identity
    if root.resolve() != root:
        raise ValueError('安装版资料根不能重定向。')
    return root


def release_marker(base):
    marker = Path(base) / 'release.json'
    if not marker.exists():
        return None
    value = json.loads(marker.read_text(encoding='utf-8-sig'))
    if 'profile_layout' not in value:
        return None
    if any(value.get(k) != v for k, v in {'schema': 1, 'profile_layout': 1, 'channel': 'release', 'product': 'Workdesk'}.items()):
        raise ValueError('安装版本标识无效。')
    return value


def local_marks(base=None):
    base = Path(base or SOURCE_ROOT).resolve()
    if release_marker(base):
        return {'workdesk.channel': 'release', 'workdesk.instanceconfig': str(release_state_root(base) / 'instance.json')}
    marker = base / '.git'
    if not marker.exists():
        return {}
    config = marker / 'config' if marker.is_dir() else marker
    return _marks(str(base), config.stat().st_mtime_ns)


def _absolute(value, field):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('实例配置需要绝对位置：' + field)
    return Path(value).resolve()


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def protected_roots(profile):
    roots = [Path.home() / '.workdesk', *map(Path, profile.get('protected_roots', []))]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        roots.append(Path(local) / 'Workdesk')
    # Read only the location registry; never open a daily database or credential.
    for user in list(roots):
        pointer = user / 'config/locations.json'
        if pointer.is_file():
            value = json.loads(pointer.read_text(encoding='utf-8'))
            for key in ('active_data', 'previous_data'):
                if value.get(key):
                    roots.append(_absolute(value[key], key))
    return [p.resolve() for p in roots]


def validate_locations(base, data, user, profile=None):
    profile = profile or load_profile(base)
    if not profile:
        return
    base, data = Path(base).resolve(), Path(data).resolve()
    if user is None or Path(user).resolve() != Path(profile['user_root']):
        raise ValueError('开发用户根与登记身份不符；未打开资料库。')
    user = Path(user).resolve()
    if base != Path(profile['program_root']) or not data.is_relative_to(user / 'libraries'):
        raise ValueError('开发程序或资料位置越出登记范围；未打开资料库。')
    if _overlap(user, base):
        raise ValueError('开发源码和用户资料须分开保存。')
    if any(_overlap(candidate, protected) for candidate in (base, user, data)
           for protected in protected_roots(profile)):
        raise ValueError('开发位置与日用位置重叠；未打开资料库。')


def load_profile(base=None):
    base = Path(base or SOURCE_ROOT).resolve()
    marks = local_marks(base)
    channel = marks.get('workdesk.channel')
    if channel is None:
        return None
    if channel not in ('development', 'release'):
        raise ValueError('本机实例通道标记不受支持。')
    config = _absolute(marks.get('workdesk.instanceconfig'), 'instanceConfig')
    try:
        profile = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('开发实例配置缺失或损坏；未回落到日用。') from exc
    if not isinstance(profile, dict) or profile.get('schema') != 1 or profile.get('channel') != channel:
        raise ValueError('开发实例配置格式或通道不符。')
    try:
        uuid.UUID(profile['instance_id'])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('开发实例身份无效。') from exc
    for field in ('program_root', 'user_root', 'data_root', 'python_executable'):
        profile[field] = str(_absolute(profile.get(field), field))
    development_root=_absolute(profile.get('development_root'), 'development_root')
    if channel == 'release':
        if (development_root != release_state_root(base) or Path(profile['user_root']) != development_root / 'user'
                or Path(profile['python_executable']) != base / 'runtime/python/python.exe'):
            raise ValueError('安装版配置不属于本程序与用户。')
    elif any(not Path(profile[field]).is_relative_to(development_root) or Path(profile[field])==development_root
           for field in ('program_root','user_root')):
        raise ValueError('开发源码和用户根必须位于登记的开发根下。')
    protected=profile.get('protected_roots',[])
    if not isinstance(protected,list):raise ValueError('受保护位置清单无效。')
    for value in protected:_absolute(value,'protected_roots')
    if Path(profile['program_root']) != base:
        raise ValueError('开发实例配置不属于本程序目录。')
    if type(profile.get('ui_port')) is not int or not 1024 <= profile['ui_port'] <= 65535:
        raise ValueError('开发界面端口无效。')
    if channel == 'development' and profile.get('sync_branch') != 'codex/v2-dev':
        raise ValueError('开发公开同步只能使用 codex/v2-dev。')
    profile['config_path'] = str(config)
    profile['initial_data_root'] = profile['data_root']
    pointer = Path(profile['user_root']) / 'config/locations.json'
    if pointer.exists():
        value = json.loads(pointer.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schema') != 1:
            raise ValueError('开发资料位置指针无效。')
        selected = _absolute(value.get('active_data'), 'active_data')
        # Reject a protected location before even checking its database file.
        validate_locations(base, selected, profile['user_root'], profile)
        if not (selected / 'tasks.sqlite3').is_file():
            raise ValueError('已登记开发资料库缺失；未创建空库代替。')
        profile['data_root'] = str(selected)
    validate_locations(base, profile['data_root'], profile['user_root'], profile)
    return profile


def configure_environment(base=None):
    profile = load_profile(base)
    if profile:
        os.environ.update(WORKDESK_HOME=profile['user_root'], WORKDESK_DATA_DIR=profile['data_root'],
                          WORKDESK_INSTANCE_CONFIG=profile['config_path'])
    return profile


def snapshot(locations, process_instance='', implementation_revision=''):
    profile = load_profile(locations.base)
    if profile:
        validate_locations(locations.base, locations.data, locations.user, profile)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def git(*args):
        result = subprocess.run(['git', '-C', str(locations.base), *args], capture_output=True,
                                encoding='utf-8', errors='replace', creationflags=flags)
        return result.stdout.strip() if result.returncode == 0 else ''
    installed = release_marker(locations.base) if profile and profile['channel'] == 'release' else None
    return {'schema': 1, 'channel': profile['channel'] if profile else 'daily',
            'instance_id': profile['instance_id'] if profile else 'daily-' + str(locations.user or locations.data),
            'process_instance': process_instance, 'program_root': str(locations.base),
            'user_root': str(locations.user) if locations.user else None, 'data_root': str(locations.data),
            'source_revision': installed['source_revision'] if installed else git('rev-parse', 'HEAD'),
            'source_dirty': False if installed else bool(git('status', '--porcelain')),
            'implementation_revision': implementation_revision, 'protocol': 1}
