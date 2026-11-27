"""Assemble the relocatable Windows release from source and explicitly named runtimes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / 'src'))
from researchos.workdesk.core_runtime import freeze


def tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', '*.pyo', '.git', '.pytest_cache'))


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-source', type=Path, required=True)
    parser.add_argument('--core', type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    source = args.runtime_source.resolve()
    for name in ('python', 'python-site', 'poppler', 'deepseek-tokenizer'):
        tree(source / name, target / 'runtime' / name)
    pathfile = target / 'runtime/python/Lib/site-packages/workdesk-shared.pth'
    pathfile.write_text('../../../python-site\n', encoding='ascii')
    for name in ('src/researchos', 'contracts', 'workdesk-ui/static', 'workdesk-ui/dist', 'workdesk-ui/archive_sources'):
        tree(BASE / name, target / name)
    for script in (BASE / 'workdesk-ui').glob('*.py'):
        copy_file(script, target / 'workdesk-ui' / script.name)
    for name in ('dev_instance.py', 'release_instance.py', 'release_upgrade.py'):
        src = BASE / 'tools' / name
        if src.is_file() or name != 'release_upgrade.py':
            copy_file(src, target / 'tools' / name)
    helper = BASE / 'tools/windows-installer/partner-preview/partner_command.py'
    if helper.exists():
        copy_file(helper, target / 'tools/partner_command.py')
    pointer = Path('runtime/workdesk-desktop/current.json')
    desktop = json.loads((BASE / pointer).read_text(encoding='utf-8-sig'))['executable']
    binary = (BASE / pointer.parent / desktop).resolve()
    if not binary.is_relative_to(BASE / pointer.parent) or binary.name != 'Workdesk.Desktop.exe':
        raise ValueError('Desktop selection invalid.')
    copy_file(BASE / pointer, target / pointer)
    for name in ('Workdesk.Desktop.exe', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'):
        copy_file(binary.parent / name, target / binary.parent.relative_to(BASE) / name)
    core = target / 'runtime/workdesk-core/workdesk-core.exe'
    copy_file(args.core, core)
    with tempfile.TemporaryDirectory(prefix='workdesk-build-') as temp:
        manifest = freeze(target, temp, core)
    manifest['executable'] = core.name
    write_json(core.parent / 'manifest.json', manifest)
    write_json(target / 'release.json', {'schema': 1, 'profile_layout': 1, 'product': 'Workdesk', 'channel': 'release',
               'version': '1.2.1', 'source_revision': manifest['implementation_revision']})
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([compiler, '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/reference:System.Windows.Forms.dll',
                    '/out:' + str(target / 'Workdesk.exe'), BASE / 'tools/windows-installer/WorkdeskReleaseLauncher.cs'], check=True)
    print(manifest['implementation_revision'])


if __name__ == '__main__':
    main()
