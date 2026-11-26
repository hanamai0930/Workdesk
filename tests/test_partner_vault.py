"""Protected credential transport uses a disposable identity and fake key only."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from researchos.workdesk.bridge.__main__ import Legacy


class PartnerVaultTests(unittest.TestCase):
    def test_explicit_dispatch_is_protected_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Legacy()
            worker.service = SimpleNamespace(locations=SimpleNamespace(user=Path(folder)))
            params = {'account_id': str(uuid.uuid4()), 'version': str(uuid.uuid4()), 'key': 'fake-test-key-not-a-provider-secret'}
            result = worker.dispatch('partner.secret.store', params)
            self.assertTrue(result['stored'])
            self.assertEqual(worker.dispatch('partner.secret.store', params), result)
            files = list(Path(folder).rglob('*.vault'))
            self.assertEqual(len(files), 1)
            if __import__('os').name == 'nt':
                self.assertNotIn(params['key'].encode(), files[0].read_bytes())
            read = worker.dispatch('partner.secret.read', {k: params[k] for k in ('account_id', 'version')})
            self.assertEqual(read['key'], params['key'])
            with self.assertRaisesRegex(ValueError, '已存在'):
                worker.dispatch('partner.secret.store', dict(params, key='different-fake-test-key'))


if __name__ == '__main__':
    unittest.main()
