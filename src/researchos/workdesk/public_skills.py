"""Read-only catalog of portable Skills from an approved public repository."""
from http.client import HTTPException, HTTPSConnection
from io import BytesIO
import json
from pathlib import PureWindowsPath
import re
import stat
from threading import Lock
import time
from zipfile import BadZipFile, ZipFile

from .capabilities import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, skill_metadata, yaml_module


REPOSITORY = 'huggingface/skills'
COMMIT = re.compile(r'[0-9a-f]{40}')
MANIFEST = re.compile(r'skills/([a-z0-9]+(?:-[a-z0-9]+)*)/SKILL\.md')
CATALOG_TTL = 300


def public_bytes(host, route, limit):
    # Fixed GitHub hosts, no redirects, credentials or task information.
    connection = HTTPSConnection(host, timeout=6)
    try:
        connection.request('GET', route, headers={'User-Agent': 'Workdesk public Skills catalog'})
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError(f'公开 Skill 目录暂不可用（HTTP {response.status}）。')
            raw = response.read(limit + 1)
    except (OSError, HTTPException) as exc:
        raise ValueError('无法读取公开 Skill 目录，请稍后重试。') from exc
    finally:
        connection.close()
    if len(raw) > limit:
        raise ValueError('公开 Skill 目录超出可读取大小。')
    return raw


def archive_for(commit):
    return public_bytes('codeload.github.com', f'/{REPOSITORY}/zip/{commit}', 8 * 1024 * 1024)


def current_commit():
    raw = public_bytes('api.github.com', f'/repos/{REPOSITORY}/commits/main', 256 * 1024)
    commit = json.loads(raw).get('sha', '')
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError('公开 Skill 目录缺少可固定的提交版本。')
    return commit


def skill_entries(archive, commit, folder):
    root = f'skills-{commit}/'
    prefix = root + 'skills/' + folder + '/'
    entries = {}
    parts_seen = {}
    total = 0
    for entry in archive.infolist():
        if entry.is_dir() or not entry.filename.startswith(prefix):
            continue
        relative = entry.filename[len(prefix):]
        parts = relative.split('/')
        if any(not part or part.casefold() in {'.', '..', '.git'} or part.endswith(('.', ' '))
               or re.search(r'[<>:"\\|?*\x00-\x1f]', part) or PureWindowsPath(part).is_reserved()
               for part in parts):
            raise ValueError('公开 Skill 包含无法安全保存的文件名。')
        for index in range(1, len(parts) + 1):
            path = '/'.join(parts[:index])
            if parts_seen.setdefault(path.casefold(), path) != path:
                raise ValueError('公开 Skill 包含大小写冲突的文件名。')
        if relative in entries or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG):
            raise ValueError('公开 Skill 包含重复文件或链接。')
        entries[relative] = entry
        total += entry.file_size
    if any('/'.join(name.split('/')[:index]) in entries for name in entries
           for index in range(1, len(name.split('/')))):
        raise ValueError('公开 Skill 包含目录与文件名冲突。')
    for name in ('LICENSE', 'NOTICE'):
        if name.casefold() not in parts_seen and root + name in archive.namelist():
            entry = archive.getinfo(root + name)
            if stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG):
                raise ValueError('公开 Skill 的许可证或声明文件是链接。')
            entries[name] = entry
            total += entry.file_size
    if len(entries) > MAX_FILES or total > MAX_TOTAL_BYTES or any(
            entry.file_size > MAX_FILE_BYTES for entry in entries.values()):
        raise ValueError('公开 Skill 超过能力包文件数量或大小上限。')
    return entries


class PublicSkills:
    def __init__(self, locations):
        self.locations = locations
        self.lock = Lock()
        self.snapshot = None

    def catalog(self):
        with self.lock:
            if not self.snapshot or self.snapshot['expires'] <= time.monotonic():
                self.snapshot = self.fetch()
            return {'repository': REPOSITORY, 'skills': list(self.snapshot['skills'].values()), 'no_dispatch': True}

    def fetch(self):
        commit = current_commit()
        raw = archive_for(commit)
        prefix = f'skills-{commit}/'
        yaml = yaml_module(self.locations)
        skills = {}
        try:
            with ZipFile(BytesIO(raw)) as archive:
                for path in sorted(archive.namelist()):
                    match = MANIFEST.fullmatch(path.removeprefix(prefix)) if path.startswith(prefix) else None
                    if not match:
                        continue
                    name = match[1]
                    try:
                        entries = skill_entries(archive, commit, name)
                        if entries['SKILL.md'].file_size > 256 * 1024:
                            continue
                        metadata = skill_metadata(archive.read(entries['SKILL.md']), name, yaml)
                    except (ValueError, KeyError):
                        continue
                    skills[name] = {
                        'name': name, 'description': metadata['description'], 'commit': commit,
                        'url': f'https://github.com/{REPOSITORY}/tree/{commit}/skills/{name}',
                        'files': sorted(entries), 'repository_license': 'Apache-2.0',
                    }
        except (BadZipFile, NotImplementedError, RuntimeError) as exc:
            raise ValueError('公开 Skill 目录的归档无法读取。') from exc
        if not skills:
            raise ValueError('该版本的公开目录没有可导入的 Skill。')
        return {'commit': commit, 'raw': raw, 'skills': skills, 'expires': time.monotonic() + CATALOG_TTL}

    def selected(self, selection):
        if (not isinstance(selection, dict) or set(selection) != {'name', 'commit', 'scene'}
                or not all(isinstance(value, str) for value in selection.values())):
            raise ValueError('请选择公开目录里的准确 Skill 版本和预设分类。')
        snapshot = self.snapshot
        if (not snapshot or snapshot['expires'] <= time.monotonic()
                or snapshot['commit'] != selection['commit'] or selection['name'] not in snapshot['skills']):
            raise ValueError('公开目录的版本已过期或选择无效，请重新查询后选择。')
        choice = snapshot['skills'][selection['name']]
        try:
            with ZipFile(BytesIO(snapshot['raw'])) as archive:
                entries = skill_entries(archive, choice['commit'], choice['name'])
                files = {name: archive.read(entry) for name, entry in entries.items()}
        except (BadZipFile, NotImplementedError, RuntimeError) as exc:
            raise ValueError('所选 Skill 的归档无法读取。') from exc
        origin = {key: choice[key] for key in ('commit', 'url', 'repository_license')}
        return choice['name'], files, {'repository': REPOSITORY, **origin}
