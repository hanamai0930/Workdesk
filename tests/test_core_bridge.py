"""Compatibility boundaries against a temporary Go-protocol ledger substitute."""
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from researchos.workdesk import bridge
from researchos.workdesk.bridge.database import Connection, Row
from researchos.workdesk.bridge.rpc import Peer, decode, encode, remote_error
from researchos.workdesk.profiles import Profiles
from researchos.workdesk.store import Store


class Core:
    def __init__(self, path):
        self.path = path
        self.connect = sqlite3.connect
        self.sessions = {}

    def call(self, method, params):
        if method == 'db.begin':
            connection = self.connect(self.path, isolation_level=None)
            connection.execute('PRAGMA query_only=' + ('OFF' if params['write'] else 'ON'))
            connection.execute('BEGIN IMMEDIATE' if params['write'] else 'BEGIN')
            identity = str(uuid.uuid4())
            self.sessions[identity] = connection
            return {'session': identity}
        connection = self.sessions[params['session']]
        if method == 'db.execute':
            cursor = connection.execute(params['sql'], params['args'])
            return {'columns': [item[0] for item in cursor.description] if cursor.description else [],
                    'rows': cursor.fetchall(), 'lastrowid': cursor.lastrowid, 'rowcount': cursor.rowcount,
                    'total_changes': connection.total_changes, 'in_transaction': connection.in_transaction}
        if method in {'db.commit', 'db.rollback'}:
            connection.commit() if method == 'db.commit' else connection.rollback()
            connection.close()
            del self.sessions[params['session']]
            return {'ok': True}
        raise AssertionError(method)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name) / 'libraries' / 'main'
        self.root.mkdir(parents=True)
        self.core = Core(self.root / 'tasks.sqlite3')
        self.patches = [patch.dict('os.environ', {'WORKDESK_CORE_BRIDGE': '1'}),
                        patch.object(bridge, '_peer', self.core),
                        patch.object(bridge, '_binding', {'data': str(self.root), 'process_instance': 'test-run',
                                                         'owner_epoch': 4})]
        for item in self.patches:
            item.start()
        self.store = Store(self.root)

    def tearDown(self):
        for connection in self.core.sessions.values():
            connection.close()
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def test_managed_store_never_opens_local_ledger_and_receipt_is_idempotent(self):
        with patch('sqlite3.connect', side_effect=AssertionError('local SQLite is forbidden')):
            store = Store(self.root)
            identity = str(uuid.uuid4())
            effects = []

            def effect(db):
                effects.append(True)
                store.event(None, None, 'verified', {'value': '中文'}, db)
                return {'value': '中文'}

            first = store.command(identity, 'test.save', {'fraction': 1.0}, effect)
            again = store.command(identity, 'test.save', {'fraction': 1.0}, effect)
            self.assertEqual(first, ({'value': '中文'}, True))
            self.assertEqual(again, ({'value': '中文'}, False))
            self.assertEqual(effects, [True])
            with self.assertRaises(ValueError):
                store.command(identity, 'test.save', {'fraction': 2}, effect)

    def test_rollback_read_only_and_metadata_rows(self):
        with self.assertRaisesRegex(ValueError, 'cancel'):
            with self.store.write() as db:
                db.execute("INSERT INTO meta VALUES('temporary','yes')")
                raise ValueError('cancel')
        with self.store.read() as db:
            self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='temporary'").fetchone())
            row = db.execute("SELECT key,value FROM meta WHERE key='schema'").fetchone()
            self.assertEqual((tuple(row), dict(row)), (('schema', '2'), {'key': 'schema', 'value': '2'}))
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("INSERT INTO meta VALUES('forbidden','yes')")
        with self.store.write() as db:
            db.execute('COMMIT')
            self.assertFalse(db.in_transaction)
        self.assertEqual(self.core.sessions, {})

    def test_blob_changes_and_profile_transaction_binding(self):
        with self.store.write() as db:
            before = db.total_changes
            db.execute("INSERT INTO meta VALUES('bytes',?)", (b'\x00\xff',))
            self.assertGreater(db.total_changes, before)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='bytes'").fetchone()[0], b'\x00\xff')
            profiles = Profiles(None, self.store)
            with profiles.transaction(db):
                self.assertIs(profiles._transaction_db.get(), db)
            self.assertIsNone(profiles._transaction_db.get())
            db.row_factory = None
            cursor = db.cursor()
            cursor.row_factory = sqlite3.Row
            row = cursor.execute("SELECT key,value FROM meta WHERE key='bytes'").fetchone()
            self.assertEqual(row['value'], b'\x00\xff')
            self.assertIsNone(db.row_factory)
        self.assertFalse(db.in_transaction)

    def test_legacy_receipt_route_uses_proxy_without_http_listener(self):
        from researchos.workdesk.bridge.__main__ import Legacy
        legacy = Legacy()
        legacy.service = type('Service', (), {'store': self.store, 'retiring': False})()
        identity = str(uuid.uuid4())
        self.store.command(identity, 'test', {}, lambda db: {'saved': True})
        answer = legacy.request({'method': 'GET', 'path': '/api/v1/receipt?id=' + identity})
        self.assertEqual(answer['status'], 200)
        self.assertEqual(answer['body']['receipt'], {'saved': True})
        self.assertFalse(answer['retiring'])

    def test_offline_v2_store_is_scoped_and_old_standalone_refuses_it(self):
        destination = self.root.parent / 'restored'
        destination.mkdir()
        with closing(self.core.connect(self.core.path)) as source, closing(self.core.connect(destination / 'tasks.sqlite3')) as target:
            source.backup(target)
        offline = Store(destination, offline=True)
        self.assertFalse(offline.managed)
        with offline.read() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], '2')
        with self.assertRaises(ValueError):
            Store(Path(self.folder.name) / 'outside', offline=True)
        with patch.dict('os.environ', {'WORKDESK_CORE_BRIDGE': '0'}):
            with self.assertRaises(ValueError):
                Store(destination)

    def test_wire_preserves_bytes_and_sqlite_errors(self):
        value = {'body': b'\x00\xff', 'id': 9223372036854775807}
        self.assertEqual(decode(json.loads(json.dumps(encode(value)))), value)
        error = remote_error({'message': 'constraint', 'sqlite_errorcode': 19})
        self.assertIsInstance(error, sqlite3.IntegrityError)
        self.assertEqual(error.sqlite_errorcode, 19)


class RpcTests(unittest.TestCase):
    def test_incoming_request_can_wait_for_outgoing_callback(self):
        left, right = socket.socketpair()
        reader, writer = left.makefile('rb'), left.makefile('wb')
        other_reader, other_writer = right.makefile('rb'), right.makefile('wb')
        peer = Peer(reader, writer)
        peer.dispatch = lambda method, params: peer.call('db.execute', {'sql': 'SELECT 1'})
        thread = threading.Thread(target=peer.serve)
        thread.start()
        try:
            other_writer.write(b'{"id":"go-request","method":"legacy.request","params":{}}\n')
            other_writer.flush()
            callback = json.loads(other_reader.readline())
            self.assertTrue(callback['id'].startswith('py-'))
            other_writer.write(json.dumps({'id': callback['id'], 'result': {'rows': [[1]]}}).encode() + b'\n')
            other_writer.flush()
            response = json.loads(other_reader.readline())
            self.assertEqual(response, {'id': 'go-request', 'result': {'rows': [[1]]}})
        finally:
            right.shutdown(socket.SHUT_RDWR)
            thread.join(3)
            for stream in (reader, writer, other_reader, other_writer):
                stream.close()
            left.close()
            right.close()
        self.assertFalse(thread.is_alive())

    def test_owner_disconnect_fails_pending_callback_without_retry(self):
        peer = Peer(BytesIO(), BytesIO())
        error = []

        def callback():
            try:
                peer.call('db.begin', {'write': True})
            except ConnectionError as exc:
                error.append(str(exc))

        thread = threading.Thread(target=callback)
        thread.start()
        peer.close()
        thread.join(3)
        self.assertEqual(len(error), 1)
        self.assertFalse(thread.is_alive())


class ProcessTests(unittest.TestCase):
    def test_native_popen_api_and_single_exit_receipt(self):
        from researchos.workdesk.bridge.process import CorePopen, ORIGINAL_POPEN
        requests = []
        exited = threading.Event()

        def call(method, value):
            requests.append((method, value))
            if method == 'process.exited':
                exited.set()
            return {'id': 'owned-test'} if method == 'process.intent' else {'ok': True}

        with patch('researchos.workdesk.bridge.process.call', side_effect=call):
            with CorePopen([sys.executable, '-c', "print('local-result')"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True) as process:
                output, error = process.communicate(timeout=5)
                self.assertEqual((output.strip(), error, process.wait(), process.poll()), ('local-result', '', 0, 0))
                self.assertIsInstance(process, ORIGINAL_POPEN)
                if sys.platform == 'win32':
                    self.assertTrue(process._handle)
                self.assertTrue(exited.wait(2))
        self.assertEqual([method for method, unused in requests],
                         ['process.intent', 'process.started', 'process.exited'])
        self.assertNotIn('local-result', json.dumps(requests))

    def test_owner_rejection_prevents_process_creation(self):
        from researchos.workdesk.bridge.process import CorePopen, ORIGINAL_POPEN
        with patch('researchos.workdesk.bridge.process.call', side_effect=ConnectionError('owner gone')):
            with patch.object(ORIGINAL_POPEN, '__init__') as spawn:
                with self.assertRaisesRegex(ConnectionError, 'owner gone'):
                    CorePopen([sys.executable, '-c', 'raise SystemExit(0)'])
                spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
