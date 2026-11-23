"""Build the Windows 10 1809+ and Windows 11 x64 installer from audited 1.0 inputs."""
from argparse import ArgumentParser
from base64 import b64decode
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'build/installer-release-20261123'
PATCH = ROOT / 'build/cooperation-repair-20261123'
DEFAULT_WORK = ROOT / 'build/installer-win10-20261123'
DEFAULT_OUTPUT = ROOT / '分发/Workdesk-Setup-Win10-Win11-x64.exe'
PRODUCT = 'Workdesk 1.0.1（Windows 10/11 兼容版）'
DOCUMENT_VERSION = '2026.11.23-setup-win10-win11'
TARGET_SYSTEMS = 'Windows 10 1809 及以上 x64 / Windows 11 x64'

# The audited installer tool environment already provides MarkdownIt.
if (BASE / 'build-tools/markdown_it').is_dir():
    sys.path.insert(0, str(BASE / 'build-tools'))
from render_guide import render


def digest(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def write_owned(path, content):
    """Break a staging hardlink before writing; never mutate the audited baseline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.write_bytes(content)


def copy_owned(source, target):
    write_owned(target, source.read_bytes())
    shutil.copystat(source, target)


def source_overrides():
    replacements = {}
    for name in json.loads((PATCH / 'files.json').read_text(encoding='utf-8')):
        if name.startswith('src/') or name.startswith('workdesk-ui/static/'):
            replacements[name] = PATCH / 'payload' / name
    for path in (ROOT / 'tools/windows-installer').iterdir():
        if path.is_file() and path.suffix in ('.py', '.iss'):
            replacements[path.relative_to(ROOT).as_posix()] = path
    for path in (ROOT / 'tools/windows-repair').iterdir():
        if path.is_file() and path.suffix in ('.cs', '.py', '.md'):
            replacements[path.relative_to(ROOT).as_posix()] = path
    for name in ('tools/stage_cooperation_repair.py', 'tests/test_capability_conditions.py',
                 'tests/test_work_fixed_preparation.py', 'tests/test_cooperation_conversation.py',
                 'tests/support.py'):
        replacements[name] = ROOT / name
    return replacements


def update_source_archive(work):
    target = work / 'stage/第三方许可与源码/Workdesk-source.zip'
    temporary = target.with_name('Workdesk-source.new.zip')
    replacements = source_overrides()
    with ZipFile(BASE / 'stage/第三方许可与源码/Workdesk-source.zip') as original:
        with ZipFile(temporary, 'w', ZIP_DEFLATED, compresslevel=7) as archive:
            for entry in original.infolist():
                if entry.filename not in replacements:
                    archive.writestr(entry, original.read(entry.filename))
            for name, source in sorted(replacements.items()):
                archive.write(source, name)
    if target.exists():
        target.unlink()
    temporary.replace(target)
    with ZipFile(target) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for name, source in replacements.items():
            assert archive.read(name) == source.read_bytes(), name
    return sorted(replacements)


def guide_source(text):
    text = text.replace('Workdesk 1.0', PRODUCT)
    text = text.replace('Windows 11 x64', TARGET_SYSTEMS)
    text = text.replace('2026.11.23-setup', DOCUMENT_VERSION)
    text = text.replace('Workdesk-Setup-x64.exe', 'Workdesk-Setup-Win10-Win11-x64.exe')
    text = text.replace('`1.0-preparation`', '`1.0.1`')
    text = text.replace('**Windows 设置 → 应用 → 已安装的应用**', '**Windows 设置 → 应用**')
    system_requirements = ('系统要求为 **Windows 10 1809 及以上的 64 位 x64 版本**（含对应的 LTSC 2019、LTSC 2021），'
                           '或 **Windows 11 x64**。Windows 10 1507 至 1803、32 位 Windows、'
                           'Windows on ARM 和 Windows 8.1 及更早版本不在本版本安装范围内。\n\n')
    anchor = '### 1. 安装与启动\n\n'
    if text.count(anchor) != 1:
        raise ValueError('The installation guide heading changed')
    text = text.replace(anchor, anchor + system_requirements)
    previous = ('如果安装程序提示缺少 Visual C++ 运行库，请从微软安装最新的 '
                '[Visual C++ v14 x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)，然后重新运行 Workdesk 安装程序。')
    current = ('当前安装版要求 Visual C++ v14 x64 ≥ 14.44.35208.0，建议安装微软提供的'
               '[最新版本](https://aka.ms/vc14/vc_redist.x64.exe)。安装程序提示缺少该组件时，安装后再运行 Workdesk 安装程序。')
    if text.count(previous) != 1:
        raise ValueError('The installer guide runtime paragraph changed')
    text = text.replace(previous, current)
    boundary = '<a id="chapter-7"></a>'
    if text.count(boundary) != 1:
        raise ValueError('The cooperation guide boundary changed')
    addition = '''### 5. 等待补充条件时继续

准备方案后若显示 **等待补充条件**，合作聊天会列出具体申请。

1. 点击 **处理补充条件**，逐项选择实际处理方式：提供外部资源或环境信息、由我配合或人工验证、安排到后续阶段核对。
2. 填写具体说明，点击 **保存处理说明**。需要工具扩展时，仍可在 **使用 Skill 或 MCP 补充工具** 中选择预设。
3. 所有条件处理后，点击 **按补充说明重新规划并继续**，阅读确认后继续原任务。

重新规划使用 1 次准备回合，保留原目标、成员、权限、累计上限和派发截止；原方案回复继续保留。补充说明会交给负责人核对，实际缺少的条件仍须在对应阶段处理。授权已到期时，按页面提示重新安排。

'''
    text = text.replace(boundary, addition + boundary)
    faq = '|等待审批或等待补充|进入当前准确回合，阅读请求并处理|'
    if text.count(faq) != 1:
        raise ValueError('The guide FAQ changed')
    text = text.replace(faq, faq + '\n|等待补充条件|点击 **处理补充条件**，逐项保存说明，再确认重新规划|')
    return text


def stage(work):
    if any((work / name).exists() for name in ('p', 'stage')):
        raise ValueError('Use a new staging directory; existing audited files will not be overwritten')
    baseline_names = ['Workdesk.exe', *json.loads((PATCH / 'files.json').read_text(encoding='utf-8'))]
    before = {name: digest(BASE / 'p' / name) for name in baseline_names if (BASE / 'p' / name).is_file()}
    source_zip_hash = digest(BASE / 'stage/第三方许可与源码/Workdesk-source.zip')
    marker_hash = digest(BASE / 'stage/release.json')
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BASE / 'p', work / 'p', copy_function=link_or_copy)
    shutil.copytree(BASE / 'stage', work / 'stage', copy_function=link_or_copy)
    names = json.loads((PATCH / 'files.json').read_text(encoding='utf-8'))
    patch_manifest = json.loads((ROOT / 'build/windows-repair-20261123/final-package/manifest.json').read_text(encoding='utf-8'))
    patch_hashes = {entry['path']: entry['after'] for entry in patch_manifest['files']}
    for name in names:
        source = PATCH / 'payload' / name
        if name not in patch_hashes or digest(source) != patch_hashes[name]:
            raise ValueError('Repair payload differs from the checked EXE: ' + name)
        copy_owned(source, work / 'p' / name)

    marker = json.loads((BASE / 'stage/release.json').read_text(encoding='utf-8'))
    marker.update(version='1.0.1', channel='win10-validation', installer=True,
                  windows_minimum_build=17763, architecture='x64',
                  compatibility_target=TARGET_SYSTEMS,
                  included_repairs=[patch_manifest['patch_id']])
    encoded = (json.dumps(marker, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    write_owned(work / 'stage/release.json', encoded)
    write_owned(work / 'p/release.json', encoded)

    guide = work / 'stage/指南/Workdesk使用指南.md'
    text = guide_source((BASE / 'stage/指南/Workdesk使用指南.md').read_text(encoding='utf-8'))
    write_owned(guide, text.encode('utf-8'))
    html = work / 'stage/指南/Workdesk使用指南.html'
    if html.exists():
        html.unlink()
    render(BASE / 'stage/指南/Workdesk使用指南.html', guide, html)
    html_text = html.read_text(encoding='utf-8')
    assert html_text.count('data:image/png;base64,') == 9
    embedded = re.search(r'data:text/markdown;charset=utf-8;base64,([\w+/=]+)', html_text).group(1)
    assert b64decode(embedded) == guide.read_bytes()
    assert '1.0-preparation' not in text and 'Workdesk-Setup-x64.exe' not in text
    assert '|适用系统|' + TARGET_SYSTEMS + '|' in text
    # Keep the duplicate folder guide and marker consistent with the actual installer source.
    for name in ('Workdesk使用指南.md', 'Workdesk使用指南.html'):
        copy_owned(work / 'stage/指南' / name, work / 'p/指南' / name)
    notice = work / 'stage/第三方许可与源码/README.md'
    write_owned(notice, notice.read_text(encoding='utf-8').replace('# Workdesk 1.0 许可与源码', '# Workdesk 1.0.1 许可与源码').encode('utf-8'))
    replaced_sources = update_source_archive(work)

    for name, expected in before.items():
        assert digest(BASE / 'p' / name) == expected, 'Audited baseline was changed: ' + name
    assert digest(BASE / 'stage/第三方许可与源码/Workdesk-source.zip') == source_zip_hash
    assert digest(BASE / 'stage/release.json') == marker_hash
    receipt = {'status': 'STAGED', 'product_version': '1.0.1', 'channel': 'win10-validation',
               'target': 'Windows 10 1809+ x64, build 17763 or later; Windows 11 x64',
               'real_win10_validation': 'PENDING_HUMAN_DEVICE',
               'real_model_requests': 0, 'baseline_unchanged': True,
               'program_root': str(work / 'p'), 'stage_root': str(work / 'stage'),
               'patched_files': names, 'source_archive_overrides': replaced_sources,
               'guide': {'chapters': 14, 'embedded_images': 9, 'offline_markdown_matches': True},
               'release_marker': marker}
    (work / 'stage-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def compile_installer(work, output):
    tools = ROOT / 'build/installer-test-20261123/tools'
    compiler = tools / 'InnoSetup/ISCC.exe'
    if not (work / 'stage-receipt.json').is_file():
        raise ValueError('Prepare the Win10 stage before compiling')
    update_source_archive(work)
    output.parent.mkdir(parents=True, exist_ok=True)
    private_output = work / 'output'
    private_output.mkdir(parents=True, exist_ok=True)
    command = [str(compiler), '/DReleaseBuild', '/DWin10Compatibility',
               '/DProgramRoot=' + str(work / 'p'), '/DStageRoot=' + str(work / 'stage'),
               '/DDependencyRoot=' + str(tools), '/O' + str(private_output),
               str(Path(__file__).with_name('Workdesk.iss'))]
    compile_log = work / 'compile-win10-win11.log'
    with compile_log.open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError('Inno Setup failed; see ' + str(compile_log))
    name = 'Workdesk-Setup-Win10-Win11-x64.exe'
    if output.name != name:
        raise ValueError('The output filename must match the compatibility installer macro')
    compiled = private_output / name
    completed_hash, completed_size = digest(compiled), compiled.stat().st_size
    # Publish only a completed installer; compilation never exposes a partial EXE.
    os.replace(compiled, output)
    if digest(output) != completed_hash or output.stat().st_size != completed_size:
        raise RuntimeError('Published installer differs from the completed build')
    receipt = {'status': 'BUILT', 'file': str(output), 'bytes': output.stat().st_size,
               'sha256': digest(output), 'version': '1.0.1', 'min_windows_build': 17763,
               'architecture': 'x64', 'signed': False, 'real_win10_validation': 'PENDING_HUMAN_DEVICE',
               'compile_log': str(compile_log)}
    (work / 'build-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=DEFAULT_WORK)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--compile-only', action='store_true')
    args = parser.parse_args()
    if args.stage_only and args.compile_only:
        parser.error('Choose either --stage-only or --compile-only')
    work, output = args.work.resolve(), args.output.resolve()
    if not args.compile_only:
        staged = stage(work)
        print(json.dumps({'status': staged['status'], 'stage': str(work)}, ensure_ascii=True))
    if not args.stage_only:
        print(json.dumps(compile_installer(work, output), ensure_ascii=True, indent=2))
