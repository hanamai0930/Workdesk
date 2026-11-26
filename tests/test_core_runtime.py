import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from researchos.workdesk import core_runtime
from researchos.workdesk.paths import atomic_json


class CoreRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.locations = SimpleNamespace(base=root / 'repo', user=root / 'user', data=root / 'user/libraries/main')
        source = self.locations.base / 'src/researchos/workdesk/example.py'
        source.parent.mkdir(parents=True)
        source.write_text('VERSION = 1\n', encoding='utf-8')
        self.source = source
        contract = self.locations.base / 'contracts/core-v2.json'
        contract.parent.mkdir(parents=True)
        contract.write_text(json.dumps({'storage_schema': 2}), encoding='utf-8')
        binary = root / 'candidate.exe'
        binary.write_bytes(b'independent core candidate')
        self.manifest_path, self.manifest = core_runtime.install_candidate(self.locations, binary)
        atomic_json(self.locations.user / 'config/core.json', {'schema': 1, 'engine': 'go', 'manifest': str(self.manifest_path)})

    def tearDown(self):
        self.temp.cleanup()

    def test_mutated_source_and_binary_refuse_new_owner(self):
        self.assertEqual(core_runtime.expected_implementation(self.locations), self.manifest['implementation_revision'])
        self.source.write_text('VERSION = 2\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '源码已变化'):
            core_runtime.expected_implementation(self.locations)
        self.source.write_text('VERSION = 1\n', encoding='utf-8')
        Path(self.manifest['executable']).write_bytes(b'changed binary')
        with self.assertRaisesRegex(ValueError, '内容与清单不符'):
            core_runtime.selection(self.locations)

    def test_missing_selected_core_never_falls_back(self):
        Path(self.manifest['executable']).unlink()
        with self.assertRaisesRegex(ValueError, '缺失'):
            core_runtime.selection(self.locations)

    def test_snapshot_can_reconstruct_source_and_runtime(self):
        from researchos.workdesk.workspace import io_path
        snapshot = io_path(self.locations.data / 'implementations' / self.manifest['implementation_revision'])
        manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
        self.assertIn('core/workdesk-core.exe', manifest)
        self.assertEqual((snapshot / 'src/researchos/workdesk/example.py').read_bytes(), self.source.read_bytes())
        self.assertEqual((snapshot / 'core/workdesk-core.exe').read_bytes(), Path(self.manifest['executable']).read_bytes())
        from researchos.workdesk.paths import freeze_implementation, implementation_matches
        self.assertEqual(freeze_implementation(self.locations), self.manifest['implementation_revision'])
        self.assertTrue(implementation_matches(self.locations, self.manifest['implementation_revision']))
        (snapshot / 'core/workdesk-core.exe').write_bytes(b'modified archived runtime')
        self.assertFalse(implementation_matches(self.locations, self.manifest['implementation_revision']))


if __name__ == '__main__':
    unittest.main()
