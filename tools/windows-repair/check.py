"""Exercise repair transactions only in isolated program folders with data sentinels."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import mkdtemp

from build import compile_exe


HARNESS = r'''
using System;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
internal static class RepairHarness {
    [STAThread] private static int Main(string[] args) {
        var serializer = new JavaScriptSerializer();
        try {
            var engine = new RepairEngine();
            if (args[0] == "busy") {
                using (var locked = new FileStream(Path.Combine(args[1], @"runtime\python\pythonw.exe"), FileMode.Open, FileAccess.ReadWrite, FileShare.None)) engine.Apply(args[1], null);
            } else engine.Apply(args[1], delegate(int count) { if (count == Math.Min(2, engine.Manifest.files.Length)) throw new IOException("controlled transaction failure"); });
            File.WriteAllText(args[2], serializer.Serialize(new { refused = false }), new UTF8Encoding(false)); return 1;
        } catch (Exception error) {
            File.WriteAllText(args[2], serializer.Serialize(new { refused = true, error = error.Message }), new UTF8Encoding(false)); return 0;
        }
    }
}
'''


def fingerprint(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '.workdesk-repair' not in p.relative_to(root).parts}


def check(baseline, release_manifest, package, exe, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(prefix='修复验证 中文 空格 ', dir=evidence))
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    data = work / 'user' / '.workdesk-release'
    for name, content in {'config/provider-keys.env': b'PRIVATE-KEY-SENTINEL-NOT-REAL',
                          'libraries/main/tasks.sqlite3': b'PRIVATE-TASK-SENTINEL-NOT-A-DATABASE',
                          'projects/keep.txt': b'PRIVATE-PROJECT-SENTINEL'}.items():
        target = data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    data_before = fingerprint(data)

    def fixture(name):
        root = work / name
        root.mkdir()
        shutil.copy2(release_manifest, root / 'release.json')
        shutil.copy2(baseline / 'Workdesk.exe', root / 'Workdesk.exe')
        for entry in manifest['files']:
            if entry['before'] is not None:
                target = root / entry['path']; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(baseline / entry['path'], target)
        runtime = root / 'runtime/python'; runtime.mkdir(parents=True)
        (runtime / 'pythonw.exe').write_bytes(b'locked-runtime-fixture')
        # This extra data is outside the payload allowlist and must remain byte-identical too.
        (root / 'private-account-record.txt').write_bytes(b'UNCHANGED-INSTALL-ADJACENT-RECORD')
        return root

    def run(root, name, apply=True):
        output = work / (name + '.json')
        result = subprocess.run([str(exe), '--apply' if apply else '--check', '--directory', str(root), '--result', str(output)],
                                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode, json.loads(output.read_text(encoding='utf-8'))

    outcomes = []
    root = fixture('已安装 Workdesk')
    before = fingerprint(root)
    code, ready = run(root, 'inspect', apply=False)
    assert code == 0 and ready['state'] == 'READY' and fingerprint(root) == before
    code, applied = run(root, 'applied')
    assert code == 0 and applied['state'] == 'APPLIED'
    for entry in manifest['files']:
        assert sha256((root / entry['path']).read_bytes()).hexdigest() == entry['after']
    assert (root / 'private-account-record.txt').read_bytes() == b'UNCHANGED-INSTALL-ADJACENT-RECORD'
    backups = list((root / '.workdesk-repair').rglob('transaction.json'))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding='utf-8'))['state'] == 'APPLIED'
    backup_before = fingerprint(root / '.workdesk-repair')
    code, repeated = run(root, 'repeated')
    assert code == 0 and repeated['state'] == 'ALREADY_APPLIED' and fingerprint(root / '.workdesk-repair') == backup_before
    outcomes.append({'case': 'inspect_apply_repeat_unicode_path', 'passed': True, 'files': applied['files']})

    wrong = work / 'not-workdesk'; wrong.mkdir()
    code, refused = run(wrong, 'wrong-directory')
    assert code == 1 and refused['state'] == 'REFUSED' and not list(wrong.iterdir())
    outcomes.append({'case': 'wrong_directory_refused_without_changes', 'passed': True})

    modified = fixture('用户改动的程序文件')
    first = next(e for e in manifest['files'] if e['before'] is not None)
    (modified / first['path']).write_bytes(b'custom changed program file')
    modified_before = fingerprint(modified)
    code, refused = run(modified, 'modified-baseline')
    assert code == 1 and refused['state'] == 'REFUSED' and fingerprint(modified) == modified_before and not (modified / '.workdesk-repair').exists()
    outcomes.append({'case': 'modified_baseline_refused_without_changes', 'passed': True})

    harness = evidence / 'repair-test-harness.cs'; harness.write_text(HARNESS, encoding='utf-8')
    harness_exe = evidence / 'repair-test-harness.exe'
    compile_exe(package / 'repair-payload.zip', harness_exe, extra_sources=[harness], main='RepairHarness')
    for mode in ('busy', 'rollback'):
        sample = fixture(mode)
        sample_before = fingerprint(sample)
        result_file = work / (mode + '.json')
        subprocess.run([str(harness_exe), mode, str(sample), str(result_file)], check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        outcome = json.loads(result_file.read_text(encoding='utf-8'))
        assert outcome['refused'] and fingerprint(sample) == sample_before
        if mode == 'busy':
            assert not (sample / '.workdesk-repair').exists()
        else:
            records = list((sample / '.workdesk-repair').rglob('transaction.json'))
            assert len(records) == 1 and json.loads(records[0].read_text(encoding='utf-8'))['state'] == 'ROLLED_BACK'
        outcomes.append({'case': 'busy_runtime_refused' if mode == 'busy' else 'update_failure_restores_old_files', 'passed': True})

    assert fingerprint(data) == data_before
    outcomes.append({'case': 'account_task_project_sentinels_unchanged', 'passed': True})
    receipt = {'passed': True, 'exe': str(exe), 'evidence_root': str(work), 'checks': outcomes, 'real_installed_application_modified': False}
    (evidence / 'repair-check.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    p = ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--release-manifest', type=Path, required=True)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--exe', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    a = p.parse_args()
    result = check(a.baseline.resolve(), a.release_manifest.resolve(), a.package.resolve(), a.exe.resolve(), a.evidence.resolve())
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
