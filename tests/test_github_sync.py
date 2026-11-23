"""Offline Git publication checks, using temporary repositories only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


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
