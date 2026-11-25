"""Launch the local WebView2 shell; never start an agent or copy login data."""
import os
import json
from pathlib import Path
import subprocess


def launch(url, base, user_root, report=None, title=None):
    if title not in (None, 'Workdesk · 工作台', 'Workdesk · 开发'):
        raise ValueError('桌面窗口标题不支持。')
    executable=Path(base)/'runtime/workdesk-desktop/Workdesk.Desktop.exe'
    pointer=executable.parent/'current.json'
    if pointer.is_file():
        try:
            relative=json.loads(pointer.read_text(encoding='utf-8-sig'))['executable']
            candidate=(executable.parent/relative).resolve()
            if not candidate.is_relative_to(executable.parent.resolve()) or candidate.name!='Workdesk.Desktop.exe' or not candidate.is_file():return False
            executable=candidate
        except (ValueError,KeyError,TypeError,OSError):return False
    if os.name!='nt' or not executable.is_file():return False
    report=Path(report) if report else Path(user_root)/'runtime/desktop/native-status.json'
    command=[str(executable),'--url',url,'--user-root',str(user_root),'--report',str(report)]
    if title is not None:command+=['--title',title]
    process=subprocess.Popen(command,
                     cwd=executable.parent,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    from researchos.workdesk.paths import atomic_json,process_identity
    identity=process_identity(process.pid)
    atomic_json(Path(user_root)/'runtime/desktop/launch-identity.json',{
        'pid':process.pid,'creation':identity['creation'],'executable':str(executable.resolve()),
        'user_root':str(Path(user_root).resolve()),'title':title or 'Workdesk · 工作台'})
    return True
