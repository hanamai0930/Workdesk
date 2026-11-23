"""Prepare the installation-specific marker and guide from the folder release."""

from argparse import ArgumentParser
import json
from pathlib import Path
import shutil


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f'Guide source changed near {before[:65]!r}')
    return text.replace(before, after, 1)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('stage', type=Path)
    parser.add_argument('--official-release', action='store_true')
    args = parser.parse_args()
    release = args.release.resolve()
    stage = args.stage.resolve()
    if stage == release or stage.is_relative_to(release) or release.is_relative_to(stage):
        raise ValueError('The installer stage and folder release must be separate')

    expected = {'Workdesk.exe', 'release.json', 'runtime', 'src', 'workdesk-ui', '指南'}
    if {item.name for item in release.iterdir()} != expected:
        raise ValueError('Release root must match the six approved distribution entries')
    marker = json.loads((release / 'release.json').read_text(encoding='utf-8'))
    if marker['data_namespace'] != '.workdesk-release' or marker['personal_data_included']:
        raise ValueError('Release must use the clean, separate user data directory')
    forbidden = {'auth.json', 'credential-references.json', 'provider-keys.env', 'tasks.sqlite3',
                 'workdesk.cmd', '分发清单.json', '发行说明.md', '请先阅读.txt'}
    for item in release.rglob('*'):
        if item.is_symlink() or item.is_junction() or item.name.lower() in forbidden:
            raise ValueError(f'Unexpected payload item: {item.relative_to(release).as_posix()}')

    source_guide = release / '指南' / 'Workdesk使用指南.md'
    title = 'Workdesk 1.0' if args.official_release else 'Workdesk 1.0（安装测试版）'
    installer = 'Workdesk-Setup-x64.exe' if args.official_release else 'Workdesk-Setup-x64-Test.exe'
    guide_version = '2026.11.23-setup' if args.official_release else '2026.11.23-setup-test'
    text = source_guide.read_text(encoding='utf-8')
    text = replace_once(text, 'Workdesk 1.0（文件夹运行版）· Windows 11 x64',
                        f'{title} · Windows 11 x64')
    text = text.replace('2026.11.22-r4', guide_version)
    text = replace_once(text, '### 1. 准备与启动', '### 1. 安装与启动')
    start = text.index('Workdesk 使用文件夹运行方式')
    end = text.index('如果出现“启动失败”', start)
    text = text[:start] + f'''1. 双击 **{installer}**，选择程序安装位置。默认安装到 `%LOCALAPPDATA%\\Programs\\Workdesk`，供当前 Windows 用户使用。
2. 点击 **安装**。如本机缺少 Microsoft Edge WebView2 Runtime，安装程序会用随附的微软离线组件补齐。
3. 安装结束后，从 Windows 开始菜单打开 **Workdesk**。如果之前在同一台电脑上使用过文件夹版，请先处理在途工作，关闭窗口并重启 Windows，再打开安装版。
4. 首次启动会显示“连接你的账号”。完成连接后点击 **进入工作区**。已使用 Workdesk 的 Windows 用户会继续使用原有资料。

**首次运行提示。** 安装程序和 Workdesk 启动器未做数字签名。如果 SmartScreen 提示拦截，请核对文件来源。确认来自维护者后，可按 Windows 提供的选项继续。[Microsoft 的 SmartScreen 说明](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)

''' + text[end:]
    start = text.index('要完全退出当前文件夹运行版')
    end = text.index('\n<a id="chapter-2">', start)
    text = text[:start] + f'''更新或卸载前，先完成或停止在途工作，处理待核对状态并创建备份。关闭窗口后，重启 Windows 停止 Workdesk 后台服务。

- **更新**时，运行新版安装程序，使用原安装位置。启动新版后核对资料、连接和项目位置。
- **卸载**时，打开 **Windows 设置 → 应用 → 已安装的应用**，在 **{title}** 的菜单中选择 **卸载**。

卸载会移除程序和安装程序创建的快捷方式。用户资料和项目原件留在各自的位置。需要清理个人资料时，请查看[数据与备份](#chapter-12)。
''' + text[end:]
    text = replace_once(text, '按[第一章的移除步骤](#chapter-1)处理程序文件夹',
                        '按[第一章的卸载步骤](#chapter-1)移除程序')
    text = replace_once(text, '按同一章的退出步骤停止后台服务', '按同一章的步骤停止后台服务')
    text = replace_once(text, '重新解压完整 Workdesk 文件夹，保留内部结构', '重新运行 Workdesk 安装程序')
    text = replace_once(text, '|产品|Workdesk 1.0（文件夹运行版）|', f'|产品|{title}|')
    text = replace_once(text, '|运行方式|解压完整文件夹后运行 **Workdesk.exe**|',
                        '|运行方式|运行安装程序后，从 Windows 开始菜单打开 **Workdesk**|')

    marker['installer'] = True
    marker['channel'] = 'release' if args.official_release else 'installer-test'
    if args.official_release:
        marker['version'] = '1.0.0'
        text = replace_once(text, '如果出现“启动失败”',
                            '如果安装程序提示缺少 Visual C++ 运行库，请从微软安装最新的 '
                            '[Visual C++ v14 x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)，'
                            '然后重新运行 Workdesk 安装程序。\n\n如果出现“启动失败”')
        text += '\nWorkdesk 的开源许可证、第三方许可和对应源码材料位于安装目录中的 **第三方许可与源码** 文件夹。\n'
    stage_guide = stage / '指南'
    stage_guide.mkdir(parents=True, exist_ok=True)
    (stage / 'release.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (stage_guide / source_guide.name).write_text(text, encoding='utf-8')
    shutil.copytree(source_guide.parent / '图片', stage_guide / '图片', dirs_exist_ok=True)


if __name__ == '__main__':
    main()
