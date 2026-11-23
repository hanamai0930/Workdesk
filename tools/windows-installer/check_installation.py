"""Verify a test installer in an isolated program directory and user data root."""

from argparse import ArgumentParser
from base64 import b64decode
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import winreg


SOURCE = Path(__file__).resolve().parents[2]
REFERENCE = SOURCE / 'build/clean-release-20261122'
UNINSTALL_ROOT = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'


def registered_location(app_id):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_ROOT + '\\' + app_id + '_is1', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return Path(winreg.QueryValueEx(key, 'InstallLocation')[0]).resolve()
    except FileNotFoundError:
        return None


def run_hidden(command, timeout):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.run(command, timeout=timeout, startupinfo=startup, check=False)


def first_start(program, user, evidence):
    script = REFERENCE / 'check-first-start.py'
    code = script.read_text(encoding='utf-8')
    start = code.index('OUT=Path(__file__)')
    end = code.index('assert not USER.exists()', start)
    code = code[:start] + 'OUT=report_dir\nROOT=program_dir\nUSER=user_dir\n' + code[end:]
    old_audit = "str(ROOT.parents[1]/'tools/check_clean_release.py')"
    if code.count(old_audit) != 1:
        raise ValueError('The release startup audit changed')
    code = code.replace(old_audit, 'str(clean_release_audit)')
    shutil.copy2(REFERENCE / 'check-first-start.cjs', evidence / 'check-first-start.cjs')
    exec(compile(code, str(script), 'exec'), {
        '__file__': str(script), '__name__': '__main__',
        'report_dir': evidence, 'program_dir': program, 'user_dir': user,
        'clean_release_audit': SOURCE / 'tools/check_clean_release.py',
    })


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--official-release', action='store_true')
    parser.add_argument('--expected-version', default='1.0.0')
    parser.add_argument('--expected-installer', default='Workdesk-Setup-x64.exe')
    parser.add_argument('--expected-channel', default='release')
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    evidence = args.evidence.resolve(strict=True)
    app_id = 'Workdesk-Windows-x64' if args.official_release else 'Workdesk-1_0-Installer-Test'
    if registered_location(app_id) is not None:
        raise RuntimeError('A Workdesk installer test is already registered for this Windows user')
    test_root = Path(tempfile.mkdtemp(prefix='安装验证 中文 空格 ', dir=evidence))
    program = test_root / '程序文件'
    user = test_root / '.workdesk-release'
    shortcuts = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Workdesk'
    if shortcuts.exists():
        raise RuntimeError('An existing Workdesk Start Menu group would be affected')
    occupied = test_root / '已有文件'
    occupied.mkdir()
    keep = occupied / '保留.txt'
    keep.write_text('Existing file', encoding='utf-8')
    install_log = evidence / 'install.log'
    options = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LANG=zhcn', '/TASKS=']
    refused = run_hidden([str(installer), *options, f'/DIR={occupied}', f'/LOG={evidence / "occupied.log"}'], 90)
    assert refused.returncode == 7 and list(occupied.iterdir()) == [keep]
    assert registered_location(app_id) is None and not shortcuts.exists()

    result = {'status': 'FAIL', 'install_dir': str(program), 'user_data_dir': str(user)}
    try:
        setup = run_hidden([str(installer), *options, f'/DIR={program}', f'/LOG={install_log}'], 300)
        assert setup.returncode == 0, f'Installer exit code {setup.returncode}, see {install_log}'
        assert registered_location(app_id) == program
        assert not user.exists(), 'Installer unexpectedly created user data'
        assert not (program / 'Workdesk.cmd').exists()
        assert (shortcuts / 'Workdesk.lnk').is_file()
        assert len(list(shortcuts.glob('*.lnk'))) == 2
        marker = json.loads((program / 'release.json').read_text(encoding='utf-8'))
        assert marker['installer'] is True and marker['data_namespace'] == '.workdesk-release'
        guide = (program / '指南/Workdesk使用指南.html').read_text(encoding='utf-8')
        source = (program / '指南/Workdesk使用指南.md').read_bytes()
        markdown_data = re.search(r'data:text/markdown;charset=utf-8;base64,([\w+/=]+)', guide).group(1)
        assert guide.count('data:image/png;base64,') == 9 and b64decode(markdown_data) == source
        if args.official_release:
            assert marker['channel'] == args.expected_channel and marker['version'] == args.expected_version
            assert args.expected_installer in guide
            licenses = program / '第三方许可与源码'
            assert (licenses / 'README.md').is_file()
            assert (licenses / 'Workdesk-source.zip').is_file()
            assert all((licenses / name).is_dir() for name in ('Poppler', 'Codex', 'DeepSeek', 'Qwen'))
            assert 'Microsoft Visual C++ v14 x64 Redistributable is present.' in install_log.read_text(encoding='utf-8-sig')
        else:
            assert 'Workdesk 1.0（安装测试版）' in guide
        first_start(program, user, evidence)
        assert (user / 'libraries/main/tasks.sqlite3').is_file()
        sentinel = user / '用户资料保留测试.txt'
        sentinel.write_text('workdesk user data retention', encoding='utf-8')
        result.update(installed=True, start_menu_shortcuts=2, first_start='PASS', empty_accounts=True,
                      guide_single_file=True, webview2_already_present='Microsoft Edge WebView2 Runtime is present.' in install_log.read_text(encoding='utf-8-sig'),
                      directory_guard='PASS')
    finally:
        if registered_location(app_id) == program:
            uninstaller = program / 'unins000.exe'
            assert uninstaller.is_file(), 'Installed app has no uninstaller'
            removal = run_hidden([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                  f'/LOG={evidence / "uninstall.log"}'], 300)
            assert removal.returncode == 0, f'Uninstaller exit code {removal.returncode}'
            for _ in range(50):
                if registered_location(app_id) is None and not program.exists():
                    break
                time.sleep(.1)
            assert registered_location(app_id) is None and not program.exists() and not shortcuts.exists()
    assert (user / '用户资料保留测试.txt').read_text(encoding='utf-8') == 'workdesk user data retention'
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    result.update(status='PASS', uninstall='PASS', user_data_retained=True,
                  bytes=installer.stat().st_size, sha256=digest)
    (evidence / 'installer-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
