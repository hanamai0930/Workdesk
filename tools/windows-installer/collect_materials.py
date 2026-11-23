"""Collect public source and license material for the installer outside the application tree."""

from argparse import ArgumentParser
import json
from pathlib import Path
import re
import shutil
from zipfile import ZIP_DEFLATED, ZipFile


NOTICE_NAMES = re.compile(r'^(?:licen[sc]e|copying|notice|third.party|copyright)(?:[_. -]|$)', re.I)
UI_FILES = ('components.json', 'index.html', 'package.json', 'pnpm-lock.yaml',
            'pnpm-workspace.yaml', 'tsconfig.json', 'vite.config.ts')


def package_directory(module):
    module = module.removeprefix('\0').split('?', 1)[0].replace('\\', '/')
    root, separator, relative = module.rpartition('/node_modules/')
    if not separator:
        return None
    names = relative.split('/')
    return Path(root) / 'node_modules' / '/'.join(names[:2] if names[0].startswith('@') else names[:1])


def collect_frontend(frontend, target, upstream_notices):
    modules = json.loads((frontend / 'rollup-modules.json').read_text(encoding='utf-8'))
    packages = {directory for module in modules if (directory := package_directory(module))}
    packages.add((frontend / 'node_modules/tailwindcss').resolve(strict=True))
    target.mkdir(parents=True, exist_ok=True)
    index = ['# Workdesk 前端依赖', '', '按正式前端编译记录收集。jszip 采用其 MIT 许可选项。', '']
    seen = set()
    for package in sorted(packages):
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8-sig'))
        identity = (metadata['name'], metadata['version'])
        if identity in seen:
            continue
        seen.add(identity)
        name, version = identity
        folder_name = name.removeprefix('@').replace('/', '+') + '@' + version
        notices = [path for path in package.iterdir() if path.is_file() and NOTICE_NAMES.match(path.name)]
        if not notices and (upstream := upstream_notices / folder_name).is_dir():
            notices = [path for path in upstream.iterdir() if path.is_file()]
        if not notices:
            raise ValueError(f'No original notice located for frontend package {name}@{version}')
        folder = target / folder_name
        folder.mkdir(exist_ok=True)
        for notice in sorted(notices):
            shutil.copy2(notice, folder / notice.name)
        index.append(f'- `{name}@{version}` · `{metadata["license"]}` · `{folder.name}/`')
    (target / 'README.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    return len(seen)


def collect_qwen(runtime, target):
    target.mkdir(parents=True, exist_ok=True)
    for directory in ('qwen', 'qwen-subscription'):
        sdk = runtime / directory / 'node_modules/@qwen-code/sdk'
        version = json.loads((sdk / 'package.json').read_text(encoding='utf-8'))['version']
        current = target / ('qwen-code-sdk-' + version)
        current.mkdir(exist_ok=True)
        shutil.copy2(sdk / 'dist/LICENSE', current / 'LICENSE')
        comments = set()
        for source in sorted((sdk / 'dist').rglob('*')):
            if source.suffix not in {'.cjs', '.js', '.mjs'}:
                continue
            text = source.read_text(encoding='utf-8-sig')
            comments.update(match.group().strip() for match in re.finditer(r'/\*[\s\S]*?\*/', text)
                            if re.search(r'@license|\bcopyright\b|SPDX-License-Identifier|licensed under',
                                         match.group(), re.I) and len(match.group()) < 20000)
        notice = current / 'BUNDLED-COMMENTS.txt'
        notice.write_text('\n\n'.join(sorted(comments)) + '\n', encoding='utf-8')
        (current / 'ORIGIN.txt').write_text(
            f'https://www.npmjs.com/package/@qwen-code/sdk/v/{version}\n', encoding='utf-8')
    for name in ('bl-LICENSE.txt', 'aliyun-LICENSE.txt'):
        shutil.copy2(runtime / 'qwen-account-tools' / name, target / name)


def source_archive(program, frontend, public_source, target):
    with ZipFile(target, 'w', ZIP_DEFLATED, compresslevel=7) as archive:
        def store_tree(directory, prefix):
            for path in sorted(directory.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    archive.write(path, f'{prefix}/{path.relative_to(directory).as_posix()}')

        archive.write(public_source / 'LICENSE', 'LICENSE')
        store_tree(program / 'src', 'src')
        store_tree(frontend / 'frontend', 'workdesk-ui/frontend')
        store_tree(frontend / 'static', 'workdesk-ui/static')
        desktop = public_source / 'workdesk-ui/desktop'
        if desktop.is_dir():
            store_tree(desktop, 'workdesk-ui/desktop')
        for name in UI_FILES:
            from_dir = public_source / 'workdesk-ui' if name == 'vite.config.ts' else frontend
            archive.write(from_dir / name, f'workdesk-ui/{name}')
        for source in sorted((program / 'workdesk-ui').glob('*.py')):
            archive.write(source, f'workdesk-ui/{source.name}')
        installer_tools = Path(__file__).resolve().parent
        for name in ('Workdesk.iss', 'prepare_payload.py', 'render_guide.py', 'stage_release.py',
                     'collect_materials.py'):
            archive.write(installer_tools / name, f'tools/windows-installer/{name}')
        archive.write(program.parent / 'WorkdeskLauncher.cs', 'workdesk-ui/desktop/WorkdeskLauncher.cs')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('public_source', type=Path)
    parser.add_argument('audit', type=Path)
    args = parser.parse_args()
    work = args.work.resolve(strict=True)
    public_source = args.public_source.resolve(strict=True)
    audit = args.audit.resolve(strict=True)
    program, frontend = work / 'p', work / 'ui'
    target = work / 'stage' / '第三方许可与源码'
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(public_source / 'LICENSE', target / 'Workdesk-LICENSE.txt')
    base = target / '基础组件许可'
    shutil.copytree(audit / 'confirmed-notices', base, dirs_exist_ok=True)
    runtime = program / 'runtime'
    for path, name in (
        ('python/LICENSE.txt', 'Python-3.12.7-LICENSE.txt'),
        ('node-v22.23.3-win-x64/LICENSE', 'Node.js-22.23.3-LICENSE.txt'),
        ('deepseek-tokenizer/LICENSE', 'DeepSeek-Tokenizer-LICENSE.txt'),
    ):
        shutil.copy2(runtime / path, base / name)
    packages = collect_frontend(frontend, target / '前端许可', work / 'frontend-upstream')
    collect_qwen(runtime, target / 'Qwen')
    source_archive(program, frontend, public_source, target / 'Workdesk-source.zip')
    print(f'Collected original notices for {packages} frontend packages in {target}')


if __name__ == '__main__':
    main()
