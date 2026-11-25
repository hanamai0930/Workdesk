"""Save local progress and publish an explicit, credential-free source tree."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class SyncError(RuntimeError):
    pass


BLOCKED_PARTS = {'.git', '.maintenance', '.venv', '.workdesk', '.workdesk-dev',
                 '.workdesk-release', '__pycache__', 'node_modules', 'runtime',
                 'build', 'dist', '分发', 'b138', 'work-brands'}
BLOCKED_NAMES = {'auth.json', 'oauth_creds.json', 'credentials.json',
                 'credential-references.json', 'provider-keys.env'}
BLOCKED_SUFFIXES = {'.exe', '.dll', '.msi', '.pyc', '.pyo', '.sqlite', '.sqlite3',
                    '.db', '.log', '.pem', '.key', '.pfx', '.p12'}
TOKEN_PATTERN = re.compile(
    rb'(?:\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}'
    rb'|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}'
    rb'|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
TEXT_SUFFIXES = {'.py', '.mjs', '.tsx', '.ts', '.css', '.js', '.html', '.json',
                 '.cs', '.manifest', '.md', '.svg', '.toml', '.yaml', '.yml',
                 '.ps1', '.cmd', '.iss', '.txt'}


def safe_name(name: str) -> bool:
    p = PurePosixPath(name)
    parts = [v.casefold() for v in p.parts]
    return bool(parts) and not p.is_absolute() and '\\' not in name and ':' not in name and all(
        v not in ('', '.', '..') and v not in BLOCKED_PARTS for v in parts
    ) and not any(v.startswith('.env') for v in parts) and p.name.casefold() not in BLOCKED_NAMES and (
        p.suffix.casefold() not in BLOCKED_SUFFIXES and '.sqlite3-' not in p.name.casefold())


def plain(root: Path, name: str = '') -> Path:
    if name and not safe_name(name):
        raise SyncError('公开文件路径不在允许范围内：' + name)
    path = root / name
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise SyncError('同步路径包含链接，已停止：' + (name or root.name))
    if name and not path.resolve().is_relative_to(root.resolve()):
        raise SyncError('文件路径越出同步目录。')
    return path


class PublicFiles:
    def __init__(self, config: dict):
        self.config = config
        self.files = set(config['files'])
        self.trees = config.get('trees', [])
        for name in self.files:
            if not safe_name(name):
                raise SyncError('公开清单含不允许的路径：' + name)
        for tree in self.trees:
            if not safe_name(tree['path']):
                raise SyncError('公开清单含不允许的目录。')

    def manages(self, name: str) -> bool:
        if not safe_name(name):
            return False
        return name in self.files or any(
            name.startswith(tree['path'].rstrip('/') + '/') and
            PurePosixPath(name).suffix in tree['suffixes'] for tree in self.trees)

    def collect(self, root: Path) -> dict[str, bytes]:
        names = set(self.files)
        for tree in self.trees:
            top = plain(root, tree['path'])
            if not top.exists():
                continue
            for folder, dirs, files in os.walk(top, followlinks=False):
                dirs[:] = [v for v in dirs if v.casefold() not in BLOCKED_PARTS]
                for directory in dirs:
                    plain(root, (Path(folder) / directory).relative_to(root).as_posix())
                for name in files:
                    relative = (Path(folder) / name).relative_to(root).as_posix()
                    if self.manages(relative):
                        names.add(relative)
        result = {}
        for name in sorted(names):
            source = plain(root, name)
            if not source.is_file():
                continue
            if source.stat().st_size > self.config.get('max_file_bytes', 10000000):
                raise SyncError('公开文件超过大小限制：' + name)
            data = source.read_bytes()
            if TOKEN_PATTERN.search(data):
                raise SyncError('检测到疑似凭据，已停止；请检查文件：' + name)
            result[name] = public_content(name, data)
        return result


def replace_required(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SyncError('公开图标转换与当前源码不符，请先检查 independent-work 源码。')
    return text.replace(old, new, 1)


def public_content(name: str, data: bytes) -> bytes:
    """Keep the published initial-letter icons and portable Python selection."""
    if PurePosixPath(name).suffix not in TEXT_SUFFIXES and name not in {
            '.gitignore', '.gitattributes', 'LICENSE'}:
        return data
    try:
        text = data.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeDecodeError as error:
        raise SyncError('源码不是 UTF-8：' + name) from error
    if name == 'workdesk-ui/frontend/modules/independent-work.tsx' and 'work-brands/' in text:
        for variable, filename, label in [('codexIcon', 'codex.png', 'C'),
                                          ('deepseekIcon', 'deepseek.svg', 'D'),
                                          ('qwenIcon', 'qwen.svg', 'Q')]:
            text = replace_required(text, f"import {variable} from './work-brands/{filename}?inline';\n", '')
            text = replace_required(text, f'icon:{variable}', f"initial:'{label}'")
        text = replace_required(text, '{icon:string;', '{initial:string;')
        text = replace_required(text,
            '''<img className={'wk-work-brand-icon is-'+v.provider} src={independentBrand(v.provider).icon} alt="" aria-hidden="true"/>''',
            '''<span className="wk-work-brand-icon" aria-hidden="true">{independentBrand(v.provider).initial}</span>''')
        text = replace_required(text,
            '''<img className={'wk-work-brand-icon is-'+provider} src={brand.icon} alt="" aria-hidden="true"/>''',
            '''<span className="wk-work-brand-icon" aria-hidden="true">{brand.initial}</span>''')
    if name == 'workdesk-ui/frontend/modules/independent-work.css':
        text = text.replace('object-fit:contain;flex-shrink:0;',
            'display:grid;place-items:center;border-radius:20%;color:inherit;background:rgba(var(--work-rgb,45,133,115),.12);font-weight:700;flex-shrink:0;')
        text = text.replace('display:block;width:48px;height:48px;margin:0 auto 22px;',
                            'width:48px;height:48px;margin:0 auto 22px;font-size:26px;')
        text = text.replace('  :scope:where([data-theme="dark"] *) .wk-work-brand-icon.is-codex { filter:invert(1); }\n', '')
    if name == 'workdesk-ui/static/ui-modules.js':
        text = text.replace("'../frontend/modules/work-brands/codex.png','../frontend/modules/work-brands/deepseek.svg','../frontend/modules/work-brands/qwen.svg','../frontend/modules/work-brands/provenance.json',", '')
        if 'work-brands/' in text:
            raise SyncError('公开页面仍引用未公开的品牌素材，请检查 ui-modules.js。')
    if name == 'workdesk-ui/static/app.js':
        text = text.replace('RESEARCHOS / WORKSPACE', 'WORKDESK / WORKSPACE')
    if name == 'src/researchos/workdesk/paths.py':
        text = text.replace(
            "        installed = Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Python312/python.exe'\n"
            '        return installed if installed.is_file() else Path(sys.executable)',
            '        return Path(sys.executable)')
    if name == 'pyproject.toml' and not re.search(r'^license\s*=', text, re.M):
        text = text.replace('requires-python = ">=3.12"\n', 'requires-python = ">=3.12"\nlicense = "MIT"\n', 1)
    if name == 'workdesk-ui/package.json':
        package = json.loads(text)
        if 'packageManager' not in package:
            ordered = {}
            for key, value in package.items():
                ordered[key] = value
                if key == 'version':
                    ordered['packageManager'] = 'pnpm@11.25.0'
            text = json.dumps(ordered, ensure_ascii=False, indent=2)
    return (text.rstrip('\n') + '\n').encode('utf-8')


def existing_content(name: str, data: bytes) -> bytes:
    if PurePosixPath(name).suffix in TEXT_SUFFIXES or name in {'.gitignore', '.gitattributes', 'LICENSE'}:
        return data.replace(b'\r\n', b'\n')
    return data


class Synchronizer:
    def __init__(self, root: Path, *, checkout: Path | None = None,
                 config: dict | None = None, authenticate: bool = True,
                 branch: str | None = None, transport: str | None = None):
        from researchos.workdesk.instance import load_profile

        self.root = root.resolve()
        self.checkout = checkout or self.root / '.maintenance/github-publish'
        self.config = config or json.loads((self.root / 'tools/github-public-files.json').read_text(encoding='utf-8'))
        self.public = PublicFiles(self.config)
        self.remote = self.config['repository']
        self.profile = load_profile(self.root)
        self.channel = 'development' if self.profile else 'daily'
        transport=transport or (self.profile or {}).get('sync_transport','git')
        if transport not in {'git', 'github-api'} or (transport == 'github-api' and not self.profile):
            raise SyncError('GitHub API 传输仅用于已登记的开发通道，不操作公开 main。')
        self.transport = transport
        self.gh = None
        self.branch = self.profile.get('sync_branch') if self.profile else self.config.get('branch', 'main')
        if self.profile and self.branch != 'codex/v2-dev':
            raise SyncError('开发实例只能同步到公开 codex/v2-dev 分支；未保存或上传本次改动。')
        if branch is not None and branch != self.branch:
            raise SyncError('指定公开分支与本机实例通道不一致；未保存或上传本次改动。')
        self.seed_branch = 'main' if self.profile else self.branch
        self.remote_tip = None
        self.git = shutil.which('git')
        if not self.git:
            raise SyncError('找不到 Git。请先安装 Git for Windows，再运行同步。')
        self.environment = os.environ.copy()
        self.environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
        self.git_options = ['-c', 'http.version=HTTP/1.1']
        self.run_git(self.root, 'check-ref-format', '--branch', self.branch)
        if authenticate:
            if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', self.remote):
                raise SyncError('请在公开文件清单中设置不含凭据的 GitHub HTTPS 仓库地址。')
            configured_auth = self.profile.get('auth_root') if self.profile else None
            if self.profile and not configured_auth:
                raise SyncError('开发实例未登记显式认证目录；未回退其他账号。')
            if configured_auth:
                for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
                    self.environment.pop(name, None)
                self.environment['GH_HOST'] = 'github.com'
            auth_root = Path(configured_auth) if configured_auth else self.root / '.maintenance'
            if configured_auth and (not auth_root.is_absolute() or not plain(auth_root).is_dir()):
                raise SyncError('显式认证维护目录不存在或不是本机绝对目录；未回退其他账号。')
            gh = auth_root / 'tools/gh/bin/gh.exe'
            if not gh.is_file():
                found = shutil.which('gh')
                if not found:
                    raise SyncError('找不到 GitHub CLI。请安装 gh 并运行 gh auth login，或恢复本机维护工具。')
                gh = Path(found)
            local_config = auth_root / 'gh-config'
            if configured_auth and not plain(local_config).is_dir():
                raise SyncError('显式认证目录缺少 gh-config；未回退其他账号。')
            if local_config.is_dir():
                self.environment['GH_CONFIG_DIR'] = str(local_config)
            self.gh = gh
            self.git_options += ['-c', 'credential.helper=', '-c',
                                 f'credential.helper=!"{gh.as_posix()}" auth git-credential']

    def run_git(self, directory: Path, *args: str, accept: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        result = subprocess.run([self.git, *self.git_options, '-C', str(directory), *args],
                                env=self.environment, capture_output=True, encoding='utf-8', errors='replace')
        if result.returncode not in accept:
            operation = args[0] if args else 'operation'
            raise SyncError(f'Git {operation} 失败（代码 {result.returncode}）。本地文件和提交均保留；请检查网络、认证或仓库状态后重试。')
        return result

    def git_names(self, directory: Path, *args: str) -> list[str]:
        return [v for v in self.run_git(directory, *args, '-z').stdout.split('\0') if v]

    def context(self) -> dict:
        return {'channel': self.channel, 'source': str(self.root),
                'source_branch': self.run_git(self.root, 'branch', '--show-current').stdout.strip(),
                'public_branch': self.branch, 'transport': self.transport}

    def api_request(self, method: str, path: str, value: dict | None = None,
                    *, missing: bool = False):
        """Explicit fallback using the same account, filtered tree and exact Git objects."""
        if self.channel != 'development' or self.branch != 'codex/v2-dev' or not self.gh:
            raise SyncError('GitHub API 传输需要已登记开发身份和显式认证目录。')
        if method != 'GET' and path.startswith('git/refs'):
            create = method == 'POST' and path == 'git/refs' and value.get('ref') == 'refs/heads/codex/v2-dev'
            update = (method == 'PATCH' and path == 'git/refs/heads/codex/v2-dev'
                      and value.get('force') is False)
            if not (create or update):
                raise SyncError('API 不允许更新公开 main 或强制移动分支。')
        repository = self.remote.removeprefix('https://github.com/').removesuffix('.git')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise SyncError('API 传输需要不含凭据的 GitHub 仓库地址。')
        command = [str(self.gh), 'api', '--hostname', 'github.com', '--method', method,
                   'repos/' + repository + '/' + path]
        if value is not None:
            command += ['--input', '-']
        try:
            result = subprocess.run(command, input=json.dumps(value) if value is not None else None,
                                    env=self.environment, capture_output=True, encoding='utf-8', errors='replace',
                                    timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as error:
            raise SyncError('GitHub API 请求超时；本地提交保留，先核对远端再重试。') from error
        if result.returncode:
            if missing and 'HTTP 404' in result.stderr:
                return None
            raise SyncError('GitHub API ' + method + ' ' + path + ' 失败；本地提交保留，未自动重发。')
        return json.loads(result.stdout)

    def api_ref(self, branch: str):
        value = self.api_request('GET', 'git/ref/heads/' + branch, missing=True)
        if value is None:
            return None
        if value.get('ref') != 'refs/heads/' + branch or not re.fullmatch(r'[0-9a-f]{40}', value.get('object', {}).get('sha', '')):
            raise SyncError('GitHub API 分支回执身份不符。')
        return value['object']['sha']

    def prepare_api_checkout(self):
        plain(self.checkout)
        self.api_main_tip = self.api_ref('main')
        self.remote_tip = self.api_ref(self.branch)
        if not self.api_main_tip:
            raise SyncError('公开 main 缺失；未建立开发公开副本。')
        if not (self.checkout / '.git').exists():
            if self.remote_tip:
                raise SyncError('公开开发分支已存在但本地过滤副本缺失；先获取或恢复该副本，不从旧 main 覆盖。')
            seed = plain(Path(self.profile['auth_root']) / 'github-publish')
            seed_head = self.run_git(seed, '--no-optional-locks', 'rev-parse', 'HEAD').stdout.strip()
            seed_origin = self.run_git(seed, '--no-optional-locks', 'remote', 'get-url', 'origin').stdout.strip()
            if (seed_head != self.api_main_tip or seed_origin.rstrip('/') != self.remote.rstrip('/')
                    or self.run_git(seed, '--no-optional-locks', 'branch', '--show-current').stdout.strip() != 'main'
                    or self.run_git(seed, '--no-optional-locks', 'status', '--porcelain').stdout.strip()):
                raise SyncError('本地公开种子与 API main 不一致或有未提交修改；未复制或上传。')
            self.checkout.parent.mkdir(parents=True, exist_ok=True)
            self.run_git(self.root, 'clone', '--no-hardlinks', '--branch', 'main', '--single-branch',
                         '--', str(seed), str(self.checkout))
            self.run_git(self.checkout, 'remote', 'set-url', 'origin', self.remote)
            self.run_git(self.checkout, 'switch', '-c', self.branch)
            for name in ('user.name', 'user.email'):
                identity = self.run_git(self.root, 'config', '--get', name, accept=(0, 1)).stdout.strip()
                if identity:
                    self.run_git(self.checkout, 'config', '--local', name, identity)
        if (self.run_git(self.checkout, 'remote', 'get-url', 'origin').stdout.strip().rstrip('/') != self.remote.rstrip('/')
                or self.run_git(self.checkout, 'branch', '--show-current').stdout.strip() != self.branch
                or self.run_git(self.checkout, 'status', '--porcelain').stdout.strip()):
            raise SyncError('开发公开副本的地址、分支或未提交状态不符；保留现状。')
        if self.remote_tip:
            if self.run_git(self.checkout, 'merge-base', '--is-ancestor', self.remote_tip, 'HEAD', accept=(0, 1, 128)).returncode:
                raise SyncError('公开开发分支有新的或未知提交；未覆盖远端。')
            self.api_anchor = self.remote_tip
        else:
            if self.run_git(self.checkout, 'show-ref', '--verify', '--quiet',
                            f'refs/remotes/origin/{self.branch}', accept=(0, 1)).returncode == 0:
                raise SyncError('公开开发分支已被移除；先核对再重建。')
            self.api_anchor = self.run_git(self.checkout, 'rev-parse', 'origin/main').stdout.strip()
            if self.api_anchor != self.api_main_tip:
                raise SyncError('首次开发分支的种子已落后于公开 main；未上传。')

    def git_object(self, kind: str, sha: str) -> bytes:
        result = subprocess.run([self.git, '-C', str(self.checkout), 'cat-file', kind, sha],
                                env=self.environment, capture_output=True)
        if result.returncode:
            raise SyncError('本地公开 Git 对象缺失；未上传。')
        return result.stdout

    @staticmethod
    def commit_payload(raw: bytes):
        headers, message = raw.decode('utf-8').split('\n\n', 1)
        value = {'message': message, 'parents': []}
        for line in headers.splitlines():
            field, text = line.split(' ', 1)
            if field == 'tree':
                value['tree'] = text
            elif field == 'parent':
                value['parents'].append(text)
            elif field in {'author', 'committer'}:
                match = re.fullmatch(r'(.*) <([^<>]+)> (\d+) ([+-])(\d{2})(\d{2})', text)
                if not match:
                    raise SyncError('公开提交身份格式无法精确传递；未上传。')
                name, email, stamp, sign, hours, minutes = match.groups()
                offset = timedelta(hours=int(hours), minutes=int(minutes)) * (1 if sign == '+' else -1)
                value[field] = {'name': name, 'email': email,
                                'date': datetime.fromtimestamp(int(stamp), timezone(offset)).isoformat()}
            else:
                raise SyncError('公开提交有 API 无法精确传递的额外头字段；未移动远端分支。')
        if len(value['parents']) != 1:
            raise SyncError('API 备用传输只处理过滤副本的线性提交。')
        return value

    def publish_api(self, head: str):
        commits = self.run_git(self.checkout, 'rev-list', '--reverse', self.api_anchor + '..' + head).stdout.splitlines()
        uploaded = set()
        for commit in commits:
            payload = self.commit_payload(self.git_object('commit', commit))
            parent = payload['parents'][0]
            entries = {}
            for row in self.run_git(self.checkout, 'ls-tree', '-r', '-z', commit).stdout.split('\0'):
                if row:
                    metadata, name = row.split('\t', 1)
                    mode, kind, sha = metadata.split(' ')
                    entries[name] = {'path': name, 'mode': mode, 'type': kind, 'sha': sha}
            changed = self.run_git(self.checkout, 'diff', '--no-renames', '--name-only', '-z', parent, commit).stdout.split('\0')
            tree = []
            for name in filter(None, changed):
                if not self.public.manages(name):
                    raise SyncError('待上传公开提交包含清单外改动：' + name)
                entry = entries.get(name, {'path': name, 'mode': '100644', 'type': 'blob', 'sha': None})
                if entry['type'] != 'blob' or entry['mode'] not in {'100644', '100755'}:
                    raise SyncError('公开改动不是普通源码文件；未上传。')
                sha = entry['sha']
                if sha and sha not in uploaded:
                    body = self.git_object('blob', sha)
                    if len(body) > self.config.get('max_file_bytes', 10000000) or TOKEN_PATTERN.search(body):
                        raise SyncError('公开提交历史含疑似凭据；未移动远端分支。')
                    result = self.api_request('POST', 'git/blobs', {'content': base64.b64encode(body).decode('ascii'), 'encoding': 'base64'})
                    if result.get('sha') != sha:
                        raise SyncError('API blob 与本地 Git 摘要不符；未移动远端分支。')
                    uploaded.add(sha)
                tree.append(entry)
            if tree:
                base_tree = self.run_git(self.checkout, 'rev-parse', parent + '^{tree}').stdout.strip()
                result = self.api_request('POST', 'git/trees', {'base_tree': base_tree, 'tree': tree})
                if result.get('sha') != payload['tree']:
                    raise SyncError('API tree 与本地 Git 摘要不符；未移动远端分支。')
            result = self.api_request('POST', 'git/commits', payload)
            if result.get('sha') != commit:
                raise SyncError('API commit 与本地 Git 摘要不符；未移动远端分支。')
        if self.api_ref('main') != self.api_main_tip or self.api_ref(self.branch) != self.remote_tip:
            raise SyncError('上传期间公开分支发生变化；对象保留，未移动远端分支。')
        if self.remote_tip:
            if self.run_git(self.checkout, 'merge-base', '--is-ancestor', self.remote_tip, head, accept=(0, 1)).returncode:
                raise SyncError('API 更新不是快进；未移动远端分支。')
            self.api_request('PATCH', 'git/refs/heads/' + self.branch, {'sha': head, 'force': False})
        else:
            self.api_request('POST', 'git/refs', {'ref': 'refs/heads/' + self.branch, 'sha': head})
        if self.api_ref(self.branch) != head or self.api_ref('main') != self.api_main_tip:
            raise SyncError('API 上传后的远端核对未完成；先查明实际分支再重试。')
        self.run_git(self.checkout, 'update-ref', f'refs/remotes/origin/{self.branch}', head)

    def save_local(self, message: str) -> bool:
        if not (self.root / '.git').exists():
            raise SyncError('本机源码尚未建立 Git 仓库，请先在项目根目录建立本地仓库。')
        if self.run_git(self.root, 'diff', '--name-only', '--diff-filter=U').stdout.strip():
            raise SyncError('本地仓库存在未解决的合并冲突，请处理后再同步。')
        names = set(self.git_names(self.root, 'ls-files'))
        names.update(self.git_names(self.root, 'ls-files', '--others', '--exclude-standard'))
        # The local repository also keeps internal design documents; none of them
        # are copied to the public checkout without an entry in the manifest.
        forbidden = [n for n in names if any(v.casefold() in {'.maintenance', 'runtime', 'node_modules', 'build', 'dist', '分发'}
                                             for v in PurePosixPath(n).parts) or
                     PurePosixPath(n).name.casefold() in BLOCKED_NAMES or
                     PurePosixPath(n).name.casefold().startswith('.env')]
        if forbidden:
            raise SyncError('本地 Git 将包含运行数据或凭据，请先修正忽略规则：' + forbidden[0])
        for offset in range(0, len(names), 80):
            chunk = sorted(names)[offset:offset + 80]
            self.run_git(self.root, 'add', '-A', '--', *chunk)
        staged = self.run_git(self.root, 'diff', '--cached', '--quiet', accept=(0, 1)).returncode == 1
        if staged:
            self.run_git(self.root, 'commit', '-m', message)
        return staged

    def prepare_checkout(self) -> None:
        plain(self.checkout)
        present = self.run_git(self.root, 'ls-remote', '--exit-code', '--heads',
                               self.remote, f'refs/heads/{self.branch}', accept=(0, 2)).returncode == 0
        if not present and self.channel != 'development':
            raise SyncError('公开目标分支不存在；未创建或覆盖日用分支。')
        if not (self.checkout / '.git').exists():
            self.checkout.parent.mkdir(parents=True, exist_ok=True)
            initial = self.branch if present else self.seed_branch
            self.run_git(self.root, 'clone', '--branch', initial, '--single-branch', '--', self.remote, str(self.checkout))
            if not present:
                self.run_git(self.checkout, 'switch', '-c', self.branch)
            # Use the source maintainer's identity, not a copied credential file.
            for name in ('user.name', 'user.email'):
                identity = self.run_git(self.root, 'config', '--get', name, accept=(0, 1)).stdout.strip()
                if identity:
                    self.run_git(self.checkout, 'config', '--local', name, identity)
        if self.run_git(self.checkout, 'remote', 'get-url', 'origin').stdout.strip().rstrip('/') != self.remote.rstrip('/'):
            raise SyncError('公开工作副本的 origin 与配置不一致，已停止。')
        if self.run_git(self.checkout, 'branch', '--show-current').stdout.strip() != self.branch:
            raise SyncError('公开工作副本不在目标分支，已停止。')
        if self.run_git(self.checkout, 'status', '--porcelain').stdout.strip():
            raise SyncError('公开工作副本有未提交的改动，请先处理；同步不会覆盖这些改动。')
        if not present:
            previously_published = self.run_git(self.checkout, 'show-ref', '--verify', '--quiet',
                                                f'refs/remotes/origin/{self.branch}', accept=(0, 1)).returncode == 0
            if previously_published:
                raise SyncError('公开开发分支已被远端移除；保留本地提交，先核对再重建。')
            self.remote_tip = None
            return
        self.run_git(self.checkout, 'fetch', 'origin',
                     f'refs/heads/{self.branch}:refs/remotes/origin/{self.branch}')
        local = self.run_git(self.checkout, 'rev-parse', 'HEAD').stdout.strip()
        remote = self.run_git(self.checkout, 'rev-parse', f'origin/{self.branch}').stdout.strip()
        self.remote_tip = remote
        if local != remote and self.run_git(self.checkout, 'merge-base', '--is-ancestor',
                                            remote, local, accept=(0, 1)).returncode:
            raise SyncError('GitHub 已有新的提交。请先把远端修改合入本机源码，再同步；未覆盖远端。')

    def changes(self, desired: dict[str, bytes]) -> tuple[list[str], list[str]]:
        changed = [name for name, data in desired.items()
                   if not plain(self.checkout, name).is_file() or existing_content(name, plain(self.checkout, name).read_bytes()) != data]
        tracked = self.git_names(self.checkout, 'ls-files') if (self.checkout / '.git').exists() else []
        removed = [name for name in tracked if self.public.manages(name) and name not in desired]
        return sorted(changed), sorted(removed)

    def synchronize(self, message: str, *, check: bool = False, no_push: bool = False) -> dict:
        desired = self.public.collect(self.root)
        if check:
            changed, removed = self.changes(desired)
            return {'check': True, 'public_files': len(desired), 'changed': changed, 'removed': removed,
                    **self.context()}
        saved = self.save_local(message)
        if self.transport == 'github-api':
            self.prepare_api_checkout()
        else:
            self.prepare_checkout()
        changed, removed = self.changes(desired)
        for name in removed:
            plain(self.checkout, name).unlink()
        for name in changed:
            target = plain(self.checkout, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(desired[name])
        paths = changed + removed
        for offset in range(0, len(paths), 80):
            self.run_git(self.checkout, 'add', '-A', '--', *paths[offset:offset + 80])
        if paths and self.run_git(self.checkout, 'diff', '--cached', '--quiet', accept=(0, 1)).returncode:
            if self.transport=='github-api':
                # GitHub's commit API expresses dates in UTC. Generate this
                # public commit in UTC too so its native Git SHA is identical.
                keys=('GIT_AUTHOR_DATE','GIT_COMMITTER_DATE')
                previous={key:self.environment.get(key) for key in keys}
                stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                self.environment.update({key:stamp for key in keys})
                try:self.run_git(self.checkout,'commit','-m',message)
                finally:
                    for key,value in previous.items():
                        if value is None:self.environment.pop(key,None)
                        else:self.environment[key]=value
            else:self.run_git(self.checkout, 'commit', '-m', message)
        local = self.run_git(self.checkout, 'rev-parse', 'HEAD').stdout.strip()
        remote = self.remote_tip
        pending_push = local != remote
        pushed = pending_push and not no_push
        if pushed:
            if self.transport == 'github-api':
                self.publish_api(local)
            else:
                self.run_git(self.checkout, 'push', 'origin', f'HEAD:refs/heads/{self.branch}')
                self.run_git(self.checkout, 'fetch', 'origin',
                             f'refs/heads/{self.branch}:refs/remotes/origin/{self.branch}')
            if self.run_git(self.checkout, 'rev-parse', f'origin/{self.branch}').stdout.strip() != local:
                raise SyncError('推送后远端版本又有变化，请检查 GitHub；本地提交已保留。')
        return {'check': False, 'local_saved': saved, 'public_files': len(desired),
                'changed': changed, 'removed': removed, 'pushed': pushed,
                'pending_push': pending_push and no_push, 'commit': local, **self.context()}


@contextmanager
def sync_lock(root: Path):
    # Windows releases the byte-range lock even after an interrupted process.
    import msvcrt
    target = root / '.maintenance/sync.lock'
    plain(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise SyncError('另一个源码同步正在运行，请稍后重试。') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description='保存本地源码并同步 GitHub；不更新安装包或 Release。')
    parser.add_argument('--message', help='本次进展或修复说明')
    parser.add_argument('--check', action='store_true', help='只预览，不提交、不联网')
    parser.add_argument('--no-push', action='store_true', help='保存并准备公开提交，暂不上传')
    parser.add_argument('--no-input', action='store_true', help='不询问提交说明')
    parser.add_argument('--branch', help='核对公开目标分支；不能覆盖本机实例通道')
    parser.add_argument('--transport', choices=('git', 'github-api'), default=None,
                        help='按实例 sync_transport 选择，未配置时使用 Git；github-api 仅用于开发通道')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        worker = Synchronizer(root, authenticate=not args.check, branch=args.branch, transport=args.transport)
        context = worker.context()
        print('源码目录：' + context['source'])
        print('同步通道：' + context['channel'] + '；本地 ' + context['source_branch'] +
              ' → 公开 ' + context['public_branch'] + '；传输 ' + context['transport'])
        message = args.message
        if not message and not args.no_input and not args.check:
            message = input('本次进展或修复说明（直接回车使用日期）：').strip()
        message = message or 'Sync Workdesk source ' + datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')
        with sync_lock(root):
            result = worker.synchronize(message, check=args.check, no_push=args.no_push)
        if args.check:
            print(f"预览：允许公开 {result['public_files']} 个文件，更新 {len(result['changed'])} 个，删除 {len(result['removed'])} 个。未提交、未联网。")
            for name in result['changed'] + result['removed']:
                print('  ' + name)
        else:
            state = ('公开提交已准备，尚未上传。' if result['pending_push'] else
                     'GitHub 同步完成。' if result['pushed'] else 'GitHub 已是最新版本。')
            print('本地源码已保存。' + state)
            print('公开提交：' + result['commit'][:12])
        return 0
    except (SyncError, OSError, ValueError) as error:
        print('同步未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
