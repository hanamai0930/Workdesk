"""Build a focused repair payload from the audited 1.0 frontend and edited controllers."""
from argparse import ArgumentParser
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'src/researchos/workdesk/work_fixed_preparation.py',
    'src/researchos/workdesk/cooperation_conversation.py',
    'workdesk-ui/static/work-items-page.js',
    'workdesk-ui/static/work-items-page.css',
)


def stage(output):
    baseline = ROOT / 'build/installer-release-20261123/p'
    original_ui = ROOT / 'build/installer-release-20261123/ui'
    ui = output / 'ui'
    payload = output / 'payload'
    if ui.exists() or payload.exists():
        raise ValueError('Use a fresh output folder to preserve earlier evidence')
    ui.mkdir(parents=True)
    for name in ('frontend', 'static'):
        shutil.copytree(original_ui / name, ui / name)
    for name in ('components.json', 'index.html', 'package.json', 'pnpm-lock.yaml',
                 'pnpm-workspace.yaml', 'tsconfig.json', 'vite.config.ts'):
        shutil.copy2(original_ui / name, ui / name)
    for name in ('work-items-page.js', 'work-items-page.css'):
        shutil.copy2(ROOT / 'workdesk-ui/static' / name, ui / 'static' / name)

    # Reuse the audited dependency installation, without copying it into the repair.
    env = dict(os.environ, WORKDESK_REPAIR_NODE_SOURCE=str(original_ui / 'node_modules'),
               WORKDESK_REPAIR_NODE_LINK=str(ui / 'node_modules'))
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                    "New-Item -ItemType Junction -Path $env:WORKDESK_REPAIR_NODE_LINK "
                    "-Target $env:WORKDESK_REPAIR_NODE_SOURCE | Out-Null"],
                   env=env, check=True)
    node = shutil.which('node')
    if not node:
        raise ValueError('Node is required to build the frontend')
    for script, args in (
        ('typescript/bin/tsc', ['--noEmit']),
        ('vite/bin/vite.js', ['build']),
    ):
        subprocess.run([node, str(ui / 'node_modules' / script), *args], cwd=ui, check=True)

    files = []
    for name in SOURCE_FILES:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        files.append(name)
    for source in sorted((ui / 'dist').rglob('*')):
        if not source.is_file():
            continue
        name = 'workdesk-ui/dist/' + source.relative_to(ui / 'dist').as_posix()
        old = baseline / name
        if old.is_file() and old.read_bytes() == source.read_bytes():
            continue
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(name)
    (output / 'files.json').write_text(json.dumps(sorted(files), indent=2) + '\n', encoding='utf-8')
    receipt = {'baseline': str(baseline), 'frontend_source': str(original_ui),
               'ui': str(ui), 'payload': str(payload), 'files': [
                   {'path': name, 'sha256': sha256((payload / name).read_bytes()).hexdigest(),
                    'bytes': (payload / name).stat().st_size} for name in sorted(files)],
               'scope': 'Two backend files, two workitems controllers, and changed frontend build assets'}
    (output / 'stage-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'payload': str(payload), 'files': len(files), 'ui': str(ui)}, ensure_ascii=True))


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    stage(parser.parse_args().output.resolve())
