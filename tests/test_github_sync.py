"""Offline Git publication checks, using temporary repositories only."""
from __future__ import annotations

import importlib.util
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


SPEC = importlib.util.spec_from_file_location('workdesk_sync_github',
    Path(__file__).resolve().parents[1] / 'tools/sync_github.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def git(directory, *args):
    result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True,
                            encoding='utf-8', errors='replace')
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def identity(directory):
    git(directory, 'config', 'user.name', 'Sync Test')
    git(directory, 'config', 'user.email', 'sync-test@example.invalid')
    git(directory, 'config', 'core.autocrlf', 'false')


class GitHubSyncTests(unittest.TestCase):
    def test_explicit_api_transport_preserves_git_objects_and_rejects_race(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-api-sync-') as tmp:
            base = Path(tmp)
            remote, root, auth = base / 'remote.git', base / 'root', base / 'auth'
            remote.mkdir(); root.mkdir(); auth.mkdir()
            git(remote, 'init', '--bare', '--initial-branch=main'); identity(remote)
            git(root, 'init', '--initial-branch=main'); identity(root)
            (root / '.gitignore').write_text('.maintenance/\n', encoding='utf-8')
            (root / 'README.md').write_text('daily\n', encoding='utf-8')
            (root / 'asset.bin').write_bytes(b'\x00\xff\x01')
            (root / 'src').mkdir(); (root / 'src/old.py').write_text('VALUE = 1\n', encoding='utf-8')
            git(root, 'add', '.'); git(root, 'commit', '-m', 'Public seed')
            git(root, 'push', str(remote), 'main')
            public_url = 'https://github.com/example/workdesk-api-test.git'
            git(base, 'clone', str(remote), str(auth / 'github-publish'))
            git(auth / 'github-publish', 'remote', 'set-url', 'origin', public_url)
            daily = git(remote, 'rev-parse', 'main')
            config = {'repository': public_url, 'branch': 'main', 'files': ['README.md', 'asset.bin'],
                      'trees': [{'path': 'src', 'suffixes': ['.py']}]}
            with self.assertRaisesRegex(sync.SyncError, '开发通道'):
                sync.Synchronizer(root, config=config, authenticate=False, transport='github-api')
            git(root, 'switch', '-c', 'codex/v2-dev')
            user = base / 'user'
            profile = {'schema': 1, 'channel': 'development', 'instance_id': str(uuid.uuid4()),
                       'development_root': str(base), 'program_root': str(root), 'user_root': str(user),
                       'data_root': str(user / 'libraries/main'), 'ui_port': 18766,
                       'python_executable': sys.executable, 'sync_branch': 'codex/v2-dev', 'auth_root': str(auth)}
            profile_path = base / 'instance.json'
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            git(root, 'config', '--local', 'workdesk.channel', 'development')
            git(root, 'config', '--local', 'workdesk.instanceConfig', str(profile_path))
            (root / 'README.md').write_text('development\n', encoding='utf-8')
            (root / 'asset.bin').write_bytes(b'\x00\xff\x02')
            (root / 'src/old.py').rename(root / 'src/new.py')
            (root / 'docs').mkdir(); (root / 'docs/internal.md').write_text('private\n', encoding='utf-8')

            def input_git(args, body=None, env=None):
                result = subprocess.run(['git', '-C', str(remote), *args], input=body,
                                        capture_output=True, env=env)
                self.assertEqual(0, result.returncode, result.stderr)
                return result.stdout.decode('utf-8').strip()

            class LocalAPI(sync.Synchronizer):
                race = False
                writes = []

                def api_request(inner, method, path, value=None, *, missing=False):
                    if method == 'GET':
                        branch = path.removeprefix('git/ref/heads/')
                        result = subprocess.run(['git', '-C', str(remote), 'rev-parse', '--verify',
                                                 'refs/heads/' + branch], capture_output=True, text=True)
                        return None if result.returncode else {'ref': 'refs/heads/' + branch,
                                                               'object': {'sha': result.stdout.strip()}}
                    if path == 'git/blobs':
                        return {'sha': input_git(['hash-object', '-w', '--stdin'], base64.b64decode(value['content']))}
                    if path == 'git/trees':
                        env = {**os.environ, 'GIT_INDEX_FILE': str(base / 'api-index')}
                        input_git(['read-tree', value['base_tree']], env=env)
                        rows = ''.join((entry['mode'] + ' ' + entry['sha'] if entry['sha']
                                        else '0 ' + '0' * 40) + '\t' + entry['path'] + '\n' for entry in value['tree'])
                        input_git(['update-index', '--index-info'], rows.encode(), env=env)
                        return {'sha': input_git(['write-tree'], env=env)}
                    if path == 'git/commits':
                        rows = ['tree ' + value['tree'], *['parent ' + parent for parent in value['parents']]]
                        for field in ('author', 'committer'):
                            person = value[field]; date = datetime.fromisoformat(person['date'])
                            rows.append(f"{field} {person['name']} <{person['email']}> {int(date.timestamp())} {date.strftime('%z')}")
                        body = ('\n'.join(rows) + '\n\n' + value['message']).encode()
                        sha = input_git(['hash-object', '-t', 'commit', '-w', '--stdin'], body)
                        if inner.race:
                            inner.race = False
                            previous = git(remote, 'rev-parse', 'codex/v2-dev')
                            competing = input_git(['commit-tree', git(remote, 'rev-parse', previous + '^{tree}'),
                                                   '-p', previous], b'Other maintainer\n')
                            git(remote, 'update-ref', 'refs/heads/codex/v2-dev', competing, previous)
                        return {'sha': sha}
                    self.assertIn(path, ('git/refs', 'git/refs/heads/codex/v2-dev'))
                    inner.writes.append((method, path, value))
                    if method == 'POST':
                        self.assertEqual('refs/heads/codex/v2-dev', value['ref'])
                        git(remote, 'update-ref', value['ref'], value['sha'], '0' * 40)
                    else:
                        self.assertIs(value['force'], False)
                        previous = git(remote, 'rev-parse', 'codex/v2-dev')
                        git(remote, 'merge-base', '--is-ancestor', previous, value['sha'])
                        git(remote, 'update-ref', 'refs/heads/codex/v2-dev', value['sha'], previous)
                    return {}

            worker = LocalAPI(root, config=config, authenticate=False, transport='github-api')
            worker.synchronize('Prepare API', no_push=True)
            first = worker.synchronize('Publish API')
            self.assertTrue(first['pushed'])
            self.assertEqual(first['commit'], git(remote, 'rev-parse', 'codex/v2-dev'))
            self.assertEqual(daily, git(remote, 'rev-parse', 'main'))
            self.assertFalse((worker.checkout / 'docs/internal.md').exists())
            self.assertFalse((worker.checkout / 'src/old.py').exists())
            self.assertEqual(b'\x00\xff\x02', (worker.checkout / 'asset.bin').read_bytes())
            (root / 'README.md').write_text('next development\n', encoding='utf-8')
            worker.race = True
            with self.assertRaisesRegex(sync.SyncError, '发生变化'):
                worker.synchronize('Concurrent API')
            self.assertEqual(1, len(worker.writes))
            self.assertEqual(daily, git(remote, 'rev-parse', 'main'))

    def test_development_publication_is_separate_from_main(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-dev-sync-') as tmp:
            base = Path(tmp)
            remote, root = base / 'remote.git', base / 'root'
            remote.mkdir(); root.mkdir()
            git(remote, 'init', '--bare', '--initial-branch=main')
            git(root, 'init', '--initial-branch=main'); identity(root)
            (root / '.gitignore').write_text('.maintenance/\n', encoding='utf-8')
            (root / 'README.md').write_text('daily baseline\n', encoding='utf-8')
            git(root, 'add', '.gitignore', 'README.md'); git(root, 'commit', '-m', 'Daily baseline')
            git(root, 'push', str(remote), 'main')
            daily = git(remote, 'rev-parse', 'main')
            git(root, 'switch', '-c', 'codex/v2-dev')
            profile_path = base / 'instance.json'
            user = base / 'user'
            profile = {'schema': 1, 'channel': 'development', 'instance_id': str(uuid.uuid4()),
                       'program_root': str(root), 'user_root': str(user), 'development_root': str(base),
                       'data_root': str(user / 'libraries/main'), 'ui_port': 18766,
                       'python_executable': sys.executable, 'sync_branch': 'codex/v2-dev'}
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            git(root, 'config', '--local', 'workdesk.channel', 'development')
            git(root, 'config', '--local', 'workdesk.instanceConfig', str(profile_path))
            config = {'repository': str(remote), 'branch': 'main',
                      'files': ['README.md'], 'trees': []}
            (root / 'README.md').write_text('development candidate\n', encoding='utf-8')
            (root / 'docs').mkdir()
            (root / 'docs/internal.md').write_text('internal development design\n', encoding='utf-8')
            source_before = git(root, 'rev-parse', 'HEAD')
            with self.assertRaisesRegex(sync.SyncError, '通道不一致'):
                sync.Synchronizer(root, config=config, authenticate=False, branch='main')
            profile['sync_branch'] = 'main'
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'codex/v2-dev'):
                sync.Synchronizer(root, config=config, authenticate=False)
            profile['sync_branch'] = 'codex/v2-dev'
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            self.assertEqual(source_before, git(root, 'rev-parse', 'HEAD'))

            # Explicit account references take precedence over inherited tokens.
            auth = base / 'auth'
            (auth / 'tools/gh/bin').mkdir(parents=True)
            (auth / 'tools/gh/bin/gh.exe').write_text('not executed', encoding='utf-8')
            (auth / 'gh-config').mkdir()
            profile['auth_root'] = str(auth)
            profile_path.write_text(json.dumps(profile), encoding='utf-8')
            overrides = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')
            with mock.patch('os.environ', {**os.environ, **{name: 'synthetic-test-token' for name in overrides}}):
                authenticated = sync.Synchronizer(root, config={**config,
                    'repository': 'https://github.com/example/workdesk-sync-test.git'})
                self.assertTrue(all(name not in authenticated.environment for name in overrides))
                self.assertEqual(str(auth / 'gh-config'), authenticated.environment['GH_CONFIG_DIR'])

            worker = sync.Synchronizer(root, config=config, authenticate=False)
            preview = worker.synchronize('Preview development', check=True)
            self.assertEqual('development', preview['channel'])
            self.assertEqual('codex/v2-dev', preview['public_branch'])
            self.assertFalse(worker.checkout.exists())
            pending = worker.synchronize('Prepare development', no_push=True)
            self.assertTrue(pending['pending_push'])
            self.assertEqual(daily, git(remote, 'rev-parse', 'main'))
            absent = subprocess.run(['git', '-C', str(remote), 'show-ref', '--verify', '--quiet',
                                     'refs/heads/codex/v2-dev']).returncode
            self.assertEqual(1, absent)
            self.assertFalse((worker.checkout / 'docs/internal.md').exists())
            self.assertIn('docs/internal.md', git(root, 'ls-files'))
            first = worker.synchronize('Publish development')
            self.assertTrue(first['pushed'])
            self.assertEqual(first['commit'], git(remote, 'rev-parse', 'codex/v2-dev'))
            self.assertEqual(daily, git(remote, 'rev-parse', 'main'))

            # A new filtered checkout finds the existing development branch.
            reopened = sync.Synchronizer(root, checkout=root / '.maintenance/reopened',
                                         config=config, authenticate=False)
            again = reopened.synchronize('No new development')
            self.assertFalse(again['pushed'])
            self.assertFalse(again['local_saved'])
            self.assertEqual(first['commit'], again['commit'])
            self.assertEqual(daily, git(remote, 'rev-parse', 'main'))

    def test_temporary_remote_full_chain(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-sync-') as tmp:
            base = Path(tmp)
            remote, root, public = base / 'remote.git', base / 'root', base / 'public'
            remote.mkdir(); root.mkdir()
            git(remote, 'init', '--bare', '--initial-branch=main')
            git(root, 'init', '--initial-branch=main'); identity(root)
            (root / 'README.md').write_text('initial\n', encoding='utf-8')
            (root / '.gitignore').write_text('.env\nruntime/\n.maintenance/\n', encoding='utf-8')
            git(root, 'add', 'README.md', '.gitignore'); git(root, 'commit', '-m', 'Initial')
            git(root, 'push', str(remote), 'main')
            git(base, 'clone', str(remote), str(public)); identity(public)
            config = {'repository': str(remote), 'branch': 'main',
                      'files': ['README.md'],
                      'trees': [{'path': 'src', 'suffixes': ['.py', '.json']}]}
            worker = sync.Synchronizer(root, checkout=public, config=config, authenticate=False)
            (root / 'src').mkdir(); (root / 'docs').mkdir()
            (root / 'runtime').mkdir()
            (root / '.env').write_text('PRIVATE_KEY=local-only', encoding='utf-8')
            (root / 'docs/internal.md').write_text('private planning', encoding='utf-8')
            (root / 'runtime/private.json').write_text('{"account":"private"}', encoding='utf-8')
            (root / 'src/example.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'src/auth.json').write_text('{"account":"private"}', encoding='utf-8')
            (root / 'README.md').write_text('published progress\n', encoding='utf-8')
            old_local = git(root, 'rev-parse', 'HEAD')
            before = git(public, 'rev-parse', 'HEAD')
            preview = worker.synchronize('Preview', check=True)
            self.assertIn('src/example.py', preview['changed'])
            self.assertEqual(old_local, git(root, 'rev-parse', 'HEAD'))
            self.assertEqual(before, git(public, 'rev-parse', 'HEAD'))
            # Credentials accidentally visible to local Git are rejected too.
            with self.assertRaisesRegex(sync.SyncError, '凭据'):
                worker.synchronize('Refuse local credential')
            with (root / '.gitignore').open('a', encoding='utf-8') as out:
                out.write('auth.json\n')
            prepared = worker.synchronize('First progress', no_push=True)
            self.assertTrue(prepared['pending_push'])
            self.assertEqual(before, git(remote, 'rev-parse', 'main'))
            self.assertTrue((public / 'src/example.py').exists())
            self.assertFalse((public / 'docs/internal.md').exists())
            self.assertFalse((public / '.env').exists())
            self.assertFalse((public / 'src/auth.json').exists())
            self.assertTrue((root / 'runtime/private.json').exists())
            self.assertIn('docs/internal.md', git(root, 'ls-files'))
            first = worker.synchronize('Publish prepared')
            self.assertTrue(first['pushed'])
            self.assertEqual(git(public, 'rev-parse', 'HEAD'), git(remote, 'rev-parse', 'main'))
            counts = (git(root, 'rev-list', '--count', 'HEAD'), git(remote, 'rev-list', '--count', 'main'))
            again = worker.synchronize('No new changes')
            self.assertFalse(again['pushed']); self.assertFalse(again['local_saved'])
            self.assertEqual(counts, (git(root, 'rev-list', '--count', 'HEAD'), git(remote, 'rev-list', '--count', 'main')))

            # Removing a source and adding a new one reaches the same public tree.
            (root / 'src/example.py').unlink()
            (root / 'src/new.py').write_text('VALUE = 2\n', encoding='utf-8')

            class FailPush(sync.Synchronizer):
                def run_git(self, directory, *args, **kwargs):
                    if args[0] == 'push':
                        raise sync.SyncError('Simulated offline push')
                    return super().run_git(directory, *args, **kwargs)

            offline = FailPush(root, checkout=public, config=config, authenticate=False)
            previous_remote = git(remote, 'rev-parse', 'main')
            with self.assertRaisesRegex(sync.SyncError, 'offline'):
                offline.synchronize('Offline progress')
            self.assertEqual(previous_remote, git(remote, 'rev-parse', 'main'))
            pending = git(public, 'rev-parse', 'HEAD')
            self.assertNotEqual(previous_remote, pending)
            retried = worker.synchronize('Retry saved progress')
            self.assertTrue(retried['pushed'])
            self.assertEqual(pending, git(remote, 'rev-parse', 'main'))
            self.assertFalse((public / 'src/example.py').exists())
            self.assertTrue((public / 'src/new.py').exists())

            # Another maintainer's commit and an uncommitted public edit survive.
            writer = base / 'writer'
            git(base, 'clone', str(remote), str(writer)); identity(writer)
            (writer / 'README.md').write_text('remote edit\n', encoding='utf-8')
            git(writer, 'add', 'README.md'); git(writer, 'commit', '-m', 'Other maintainer')
            git(writer, 'push', 'origin', 'main')
            external = git(remote, 'rev-parse', 'main')
            (public / 'README.md').write_text('pending manual public edit\n', encoding='utf-8')
            with self.assertRaisesRegex(sync.SyncError, '未提交'):
                worker.synchronize('Preserve public edit')
            self.assertEqual('pending manual public edit\n', (public / 'README.md').read_text(encoding='utf-8'))
            git(public, 'restore', 'README.md')
            (root / 'src/new.py').write_text('VALUE = 3\n', encoding='utf-8')
            with self.assertRaisesRegex(sync.SyncError, '新的提交'):
                worker.synchronize('Concurrent local progress')
            self.assertEqual(external, git(remote, 'rev-parse', 'main'))
            self.assertEqual(pending, git(public, 'rev-parse', 'HEAD'))
            self.assertFalse(git(root, 'status', '--porcelain'))

            # Suspected real tokens are refused before any source is copied.
            (root / 'src/key.py').write_text("KEY = '" + 'ghp_' + 'a' * 36 + "'\n", encoding='utf-8')
            with self.assertRaisesRegex(sync.SyncError, '疑似凭据'):
                worker.synchronize('Refuse token', check=True)

    def test_existing_public_transformations(self):
        # Exercise the actual currently used icon syntax without copying runtime data.
        root = Path(__file__).resolve().parents[1]
        for name in ['workdesk-ui/frontend/modules/independent-work.tsx',
                     'workdesk-ui/frontend/modules/independent-work.css',
                     'workdesk-ui/static/ui-modules.js',
                     'src/researchos/workdesk/paths.py']:
            source = (root / name).read_bytes()
            result = sync.public_content(name, source)
            self.assertEqual(result, sync.public_content(name, result))
            if name.endswith('independent-work.tsx'):
                self.assertNotIn(b'work-brands/', result)
                self.assertNotIn(b'icon:codexIcon', result)
                self.assertIn(b"initial:'C'", result)
            if name.endswith('ui-modules.js'):
                self.assertNotIn(b'work-brands/', result)
            if name.endswith('paths.py'):
                self.assertNotIn(b'Python312/python.exe', result)
        self.assertFalse(sync.safe_name('../auth.json'))
        self.assertFalse(sync.safe_name('src/credentials.json'))
        self.assertFalse(sync.safe_name('runtime/tools.py'))


if __name__ == '__main__':
    unittest.main()
