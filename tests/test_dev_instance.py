"""Development helper checks use temporary data and mocked owners, never a live app."""
from contextlib import closing, ExitStack
from hashlib import sha256
import importlib.util
import json
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
from urllib.error import HTTPError


SPEC = importlib.util.spec_from_file_location('workdesk_dev_instance',
    Path(__file__).resolve().parents[1] / 'tools/dev_instance.py')
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


class DevelopmentToolsTests(unittest.TestCase):
    def test_internal_unknown_receipt_is_not_an_explicit_rejection(self):
        address = 'http://127.0.0.1:12345/api/v1/command'
        unknown = HTTPError(address, 503, 'Unavailable', {}, BytesIO(b'{"state":"UNKNOWN","no_resend":true}'))
        with patch.object(dev, 'urlopen', side_effect=unknown):
            with self.assertRaises(dev.RequestUncertain):
                dev.request_json(address, payload={'id': str(uuid.uuid4())})
        rejected = HTTPError(address, 409, 'Conflict', {}, BytesIO(b'{"state":"REJECTED","explicit_rejection":true}'))
        with patch.object(dev, 'urlopen', side_effect=rejected):
            with self.assertRaises(dev.RequestRejected):
                dev.request_json(address, payload={'id': str(uuid.uuid4())})

    def profile(self, root):
        user = root / 'user'
        data = user / 'libraries/main'
        data.mkdir(parents=True)
        return {'instance_id': str(uuid.uuid4()), 'channel': 'development', 'program_root': str(root / 'repo'),
                'user_root': str(user), 'data_root': str(data), 'ui_port': 18766, 'development_root': str(root)}

    def backend(self, profile):
        return {'state': {'instance': 'backend-run', 'pid': 98765, 'control': 'c' * 43},
                'origin': 'http://127.0.0.1:12345', 'identity': {},
                'process': {'alive': True, 'creation': '123'}}

    def test_wrong_owner_refused_before_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            state = {'app': 'workdesk-control-v1', 'protocol': 1, 'instance': 'expected', 'pid': 99999,
                     'port': 12345, 'viewer': 'v' * 43, 'control': 'c' * 43}
            (Path(profile['data_root']) / 'backend.json').write_text(json.dumps(state), encoding='utf-8')
            health = {'app': state['app'], 'protocol': 1, 'instance': 'expected', 'program_base': str(Path(tmp) / 'other')}
            with patch.object(dev, 'request_json', return_value=health) as request:
                with self.assertRaisesRegex(ValueError, '身份不符'):
                    dev.checked_backend(profile)
                self.assertEqual(request.call_count, 1)
                self.assertNotIn('payload', request.call_args.kwargs)

    def test_stop_rejection_removes_only_its_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            with patch.object(dev, 'checked_backend', return_value=self.backend(profile)), \
                    patch.object(dev, 'checked_ui', return_value=None), \
                    patch.object(dev, 'command', side_effect=dev.RequestRejected('still active')):
                with self.assertRaises(dev.RequestRejected):
                    dev.stop(profile)
            self.assertFalse((Path(profile['user_root']) / 'runtime/development-stop.json').exists())

    def test_unknown_stop_preserves_fence_and_sends_no_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            with patch.object(dev, 'checked_backend', return_value=self.backend(profile)), \
                    patch.object(dev, 'checked_ui', return_value=None), \
                    patch.object(dev, 'command', side_effect=dev.RequestUncertain('unknown')), \
                    patch.object(dev, '_close_desktop') as close:
                with self.assertRaises(dev.RequestUncertain):
                    dev.stop(profile)
                close.assert_not_called()
            fence = dev.read_json(Path(profile['user_root']) / 'runtime/development-stop.json')
            self.assertEqual(fence['instance_id'], profile['instance_id'])

    def test_ui_exit_waits_for_the_original_backend_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            backend = self.backend(profile)
            ui = {'state': {'token': 'u' * 32, 'pid': 99998}, 'origin': 'http://127.0.0.1:18766',
                  'process': {'alive': True, 'creation': '456'}}
            events = []
            def observed(pid):
                events.append(('process', pid))
                return {'alive': False, 'creation': None}
            def requested(url, **kwargs):
                events.append(('exit', kwargs['payload']))
                return {'state': 'EXITING'}
            with patch.object(dev, 'checked_backend', return_value=backend), \
                    patch.object(dev, 'checked_ui', return_value=ui), \
                    patch.object(dev, 'command', return_value={'retire_service': True}), \
                    patch.object(dev, 'process_identity', side_effect=observed), \
                    patch.object(dev, 'request_json', side_effect=requested), \
                    patch.object(dev, '_close_desktop', return_value={'state': 'CLOSED'}):
                result = dev.stop(profile)
            self.assertEqual(result['state'], 'STOPPED')
            self.assertEqual(events[0], ('process', backend['state']['pid']))
            self.assertEqual(events[1], ('exit', {'instance_id': profile['instance_id']}))

    def test_ui_rejection_after_retirement_preserves_stop_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            ui = {'state': {'token': 'u' * 32, 'pid': 99998}, 'origin': 'http://127.0.0.1:18766',
                  'process': {'alive': True, 'creation': '456'}}
            with patch.object(dev, 'checked_backend', return_value=self.backend(profile)), \
                    patch.object(dev, 'checked_ui', return_value=ui), \
                    patch.object(dev, 'command', return_value={'retire_service': True}), \
                    patch.object(dev, 'process_identity', return_value={'alive': False, 'creation': None}), \
                    patch.object(dev, 'request_json', side_effect=dev.RequestRejected('UI still active')):
                with self.assertRaises(dev.RequestRejected):
                    dev.stop(profile)
            self.assertTrue((Path(profile['user_root']) / 'runtime/development-stop.json').is_file())

    def test_stale_desktop_pid_is_not_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile(Path(tmp))
            record = Path(profile['user_root']) / 'runtime/desktop/launch-identity.json'
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({'pid': 99888, 'creation': 'old', 'user_root': profile['user_root'],
                'executable': str(Path(profile['program_root']) / 'runtime/workdesk-desktop/Workdesk.Desktop.exe')}), encoding='utf-8')
            with patch.object(dev.os, 'name', 'nt'), \
                    patch.object(dev, 'process_identity', return_value={'alive': True, 'creation': 'new'}):
                with self.assertRaisesRegex(ValueError, '旧 PID'):
                    dev._close_desktop(profile)

    def test_recovery_export_and_restore_stay_in_development(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as long_files:
            root = Path(tmp); profile = self.profile(root)
            backup_id, restore_id = str(uuid.uuid4()), str(uuid.uuid4())
            saved = Path(profile['data_root']) / 'backups' / backup_id
            (saved / 'state').mkdir(parents=True)
            with closing(sqlite3.connect(saved / 'state/tasks.sqlite3')) as db:
                for table in ('projects', 'tasks', 'attempts'):
                    db.execute('CREATE TABLE ' + table + '(id TEXT)')
                db.execute("INSERT INTO projects VALUES('sample-project')")
                db.commit()
            raw = (saved / 'state/tasks.sqlite3').read_bytes()
            long_name = 'implementations/' + 'a' * 64 + '/src/researchos/workdesk/work_handoff_' + 'x' * 100 + '.py'
            long_source = saved / 'state' / long_name
            self.assertGreater(len(str(long_source)), 260)
            dev.io_path(long_source.parent).mkdir(parents=True)
            dev.io_path(long_source).write_bytes(b'long path implementation evidence\n')
            long_files.callback(lambda: dev.io_path(long_source).unlink(missing_ok=True))
            long_raw = dev.io_path(long_source).read_bytes()
            body = {'schema': 1, 'files': {'tasks.sqlite3': {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()},
                    long_name: {'bytes': len(long_raw), 'sha256': sha256(long_raw).hexdigest()}}}
            manifest_hash = sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            (saved / 'manifest.json').write_text(json.dumps({**body, 'manifest_hash': manifest_hash}), encoding='utf-8')
            target = Path(profile['user_root']) / 'libraries' / restore_id
            calls = []
            def command(unused_profile, unused_backend, kind, payload):
                calls.append(kind)
                if kind == 'backup.create':
                    return {'state': 'READY', 'root': str(saved), 'manifest_hash': manifest_hash, 'backup_id': backup_id}
                self.assertEqual(kind, 'backup.restore')
                self.assertTrue(Path(payload['source']).is_relative_to(root / 'recovery'))
                target.mkdir(); (target / 'tasks.sqlite3').write_bytes(raw)
                return {'state': 'STAGED', 'target': str(target), 'no_dispatch': True, 'restore_id': restore_id}
            with patch.object(dev, 'required_backend', return_value={}), patch.object(dev, 'command', side_effect=command):
                exported = dev.backup(profile)
                long_export = Path(exported['source']) / 'state' / long_name
                long_files.callback(lambda: dev.io_path(long_export).unlink(missing_ok=True))
                self.assertGreater(len(str(long_export)), 260)
                self.assertEqual(dev.io_path(long_export).read_bytes(), long_raw)
                self.assertFalse(str(exported['source']).startswith('\\\\?\\'))
                result = dev.restore_check(profile, exported['source'])
                with self.assertRaisesRegex(ValueError, 'recovery'):
                    dev.restore_check(profile, profile['data_root'])
            self.assertEqual(calls, ['backup.create', 'backup.restore'])
            self.assertEqual(result['counts']['projects'], 1)
            self.assertEqual(result['active_data_unchanged'], profile['data_root'])
            self.assertTrue((saved / 'state/tasks.sqlite3').exists())
            self.assertFalse((Path(profile['user_root']) / 'config/locations.json').exists())


if __name__ == '__main__':
    unittest.main()
