"""Offline regression coverage for installed-version import and large snapshots."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import unittest

from tests.support import BASE, LocalFlowCase

sys.path.insert(0, str(BASE / 'tools'))
import release_upgrade as upgrade
from researchos.workdesk.paths import atomic_json
from researchos.workdesk.store import digest


class InstalledUpgrade(LocalFlowCase):
    def test_cold_import_recovers_interruption_and_preserves_v1(self):
        draft = self.draft()
        unfiled = self.loc.data / 'unfiled-work'
        unfiled.mkdir()
        (unfiled / '本地资料.txt').write_text('old document', encoding='utf-8')
        project = self.command('project.create', {'root': str(unfiled)})['project_id']
        self.settled(self.start(draft)['launch_attempt'])
        key_file = self.root / 'legacy-only.env'
        key_file.write_text('DEEPSEEK_API_KEY=fixture-upgrade\n', encoding='utf-8')
        connection = self.command('resource.connection-register', {
            'name': '原版测试连接', 'provider': 'deepseek', 'reference': str(key_file)})['connection']
        self.s.close()
        atomic_json(self.loc.user / 'config/locations.json', {'schema': 1, 'active_data': str(self.loc.data)})
        source_db = self.loc.data / 'tasks.sqlite3'
        before = upgrade.file_record(source_db)
        user = self.root / 'installed-v12/user'
        target = user / 'libraries/main'
        profile = {'program_root': str(self.root / 'program'), 'user_root': str(user), 'data_root': str(target)}

        def interrupted(profile, option):
            raise ValueError('interrupted')

        with self.assertRaisesRegex(ValueError, 'interrupted'):
            upgrade.import_library(profile, self.loc.user, interrupted)
        self.assertTrue((user / 'config' / upgrade.INTENT).is_file())
        self.assertTrue((target / upgrade.MARKER).is_file())

        # Exercise crash recovery without bringing an external core into the unit suite.
        # The release smoke separately invokes both shipped Go migration entry points.
        def migrate(profile, option):
            first = option == '--migrate-only'
            version = '2' if first else '3'
            with closing(sqlite3.connect(target / 'tasks.sqlite3')) as db, db:
                db.execute("UPDATE meta SET value=? WHERE key='schema'", (version,))
            return {'state': 'MIGRATED' if first else 'PARTNERS_MIGRATED', 'storage_schema': version, 'data_root': str(target)}

        receipt = upgrade.import_library(profile, self.loc.user, migrate)
        self.assertEqual(receipt, upgrade.import_library(profile, self.loc.user, interrupted))
        self.assertEqual(before, upgrade.file_record(source_db))
        self.assertEqual(receipt['counts']['tasks'], 1)
        with upgrade.read_db(target) as db:
            task = db.execute('SELECT id,grant FROM tasks').fetchone()
            self.assertEqual((task[0], task[1]), (draft['task_id'], None))
            copied = Path(db.execute('SELECT root FROM projects WHERE id=?', (project,)).fetchone()[0])
            self.assertEqual(copied, target / 'unfiled-work')
            self.assertEqual((copied / '本地资料.txt').read_text(encoding='utf-8'), 'old document')
            self.assertTrue(Path(db.execute('SELECT workspace FROM attempts').fetchone()[0]).is_relative_to(target))
            self.assertFalse(db.execute("SELECT 1 FROM collaboration WHERE kind='resource_connection_reference'").fetchone())
            row = db.execute("SELECT data FROM collaboration WHERE kind='resource_connection' AND id=?", (connection['id'],)).fetchone()
            self.assertFalse(json.loads(row[0])['enabled'])
        self.assertFalse((user / 'config' / upgrade.INTENT).exists())
        self.assertEqual(len(list((user.parent / 'recovery').glob('*/interrupted-copy'))), 1)
        backup = Path(receipt['backup'])
        manifest = upgrade.read_json(backup / 'manifest.json')
        self.assertEqual(manifest['manifest_hash'], receipt['manifest_hash'])
        with upgrade.read_db(backup / 'state') as db:
            self.assertEqual(upgrade.ledger(db, '1')['counts'], receipt['counts'])

    def test_standard_snapshot_over_512_mib(self):
        # Several individually supported files exceed the old accumulated ceiling.
        block = bytes(1024 * 1024)
        size = 57 * len(block)
        h = sha256()
        for _ in range(57):
            h.update(block)
        manifest = {f'asset-{n}.dat': h.hexdigest() for n in range(9)}
        revision = digest(manifest)
        directory = self.loc.data / 'implementations' / revision
        directory.mkdir()
        for name in manifest:
            with (directory / name).open('wb') as handle:
                for _ in range(57):
                    handle.write(block)
        atomic_json(directory / 'manifest.json', manifest)
        with self.s.store.write() as db:
            self.s.store.event(None, None, 'execution_reserved', {'implementation_revision': revision}, db)
        backup = self.command('backup.create', {})
        record = json.loads((Path(backup['root']) / 'manifest.json').read_bytes())
        self.assertGreater(record['total_bytes'], 512 * 1024 * 1024)
        restored = self.command('backup.restore', {'backup_id': backup['backup_id']})
        last = Path(restored['target']) / 'implementations' / revision / 'asset-8.dat'
        self.assertEqual(upgrade.file_record(last), {'bytes': size, 'sha256': h.hexdigest()})


if __name__ == '__main__':
    unittest.main()
