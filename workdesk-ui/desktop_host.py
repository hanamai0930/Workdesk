"""Launch the local WebView2 shell; never start an agent or copy login data."""
import os
import json
from pathlib import Path
import subprocess


def launch(url, base, user_root, report=None):
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
    subprocess.Popen([str(executable),'--url',url,'--user-root',str(user_root),'--report',str(report)],
                     cwd=executable.parent,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return True
