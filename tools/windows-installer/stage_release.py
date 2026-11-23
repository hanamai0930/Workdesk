"""Stage the clean Windows application and record the release frontend inputs."""

from argparse import ArgumentParser
import json
from pathlib import Path
import shutil


FRONTEND_FILES = ('components.json', 'index.html', 'package.json', 'pnpm-lock.yaml',
                  'pnpm-workspace.yaml', 'tsconfig.json', 'vite.config.ts')
FRONTEND_FOLDERS = ('frontend', 'static')
REPLACED_RUNTIMES = ('codex', 'poppler')
SYSTEM_VC_FILES = ('vcruntime140.dll', 'vcruntime140_1.dll')
SYSTEM_VC_DIRECTORIES = ('runtime/python', 'runtime/deepseek-venv/Scripts',
                         'runtime/deepseek-legacy-venv/Scripts')


def initialize(clean, frontend_source, work):
    program = work / 'p'
    frontend = work / 'ui'
    if program.exists() or frontend.exists():
        raise ValueError('Use a new staging location to avoid stale release files')
    program.mkdir(parents=True)
    shutil.copy2(clean / 'Workdesk.exe', program)
    shutil.copy2(clean / 'release.json', program)
    for name in ('src', '指南'):
        shutil.copytree(clean / name, program / name)
    for name in ('runtime', 'workdesk-ui'):
        def excluded(directory, entries):
            relative = Path(directory).relative_to(clean).as_posix()
            if relative == 'runtime':
                return [entry for entry in entries if entry in REPLACED_RUNTIMES]
            if relative == 'workdesk-ui':
                return ['dist'] if 'dist' in entries else []
            if relative in SYSTEM_VC_DIRECTORIES:
                return [entry for entry in entries if entry in SYSTEM_VC_FILES]
            return []

        shutil.copytree(clean / name, program / name, ignore=excluded)

    frontend.mkdir()
    for name in FRONTEND_FOLDERS:
        shutil.copytree(frontend_source / name, frontend / name)
    for name in FRONTEND_FILES:
        shutil.copy2(frontend_source / name, frontend / name)
    config = frontend / 'vite.config.ts'
    source = config.read_text(encoding='utf-8')
    source = "import { writeFileSync } from 'node:fs';\n" + source
    original = 'plugins: [react(), tailwindcss(), {'
    if source.count(original) != 1:
        raise ValueError('Frontend build configuration has changed')
    source = source.replace(original, """plugins: [react(), tailwindcss(), {
    name: 'workdesk-release-module-inventory',
    generateBundle(_, bundle) {
      const modules = Object.values(bundle).flatMap(chunk => chunk.type === 'chunk'
        ? Object.keys(chunk.modules) : []);
      writeFileSync('rollup-modules.json', JSON.stringify([...new Set(modules)].sort(), null, 2));
    },
  }, {""", 1)
    config.write_text(source, encoding='utf-8')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('clean', type=Path)
    parser.add_argument('frontend', type=Path)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    clean = args.clean.resolve(strict=True)
    frontend = args.frontend.resolve(strict=True)
    work = args.work.resolve()
    for source in (clean, frontend):
        if work.is_relative_to(source) or source.is_relative_to(work):
            raise ValueError('Source and staging directories must be separate')
    release = json.loads((clean / 'release.json').read_text(encoding='utf-8'))
    if release['personal_data_included'] or release['data_namespace'] != '.workdesk-release':
        raise ValueError('The input is not the clean release tree')
    initialize(clean, frontend, work)


if __name__ == '__main__':
    main()
