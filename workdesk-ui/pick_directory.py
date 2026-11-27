"""Native file and directory choosers launched by explicit UI actions."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

arguments = sys.argv[1:]
if arguments not in ([], ['--partner-bundle']):
    raise SystemExit(2)
sys.stdout.reconfigure(encoding='utf-8')
root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
try:
    if arguments:
        path = filedialog.askopenfilename(title='导入本地补充包', parent=root,
                                          filetypes=[('Workdesk 伙伴补充包', ('*.wdpa', '*.wdpartner'))])
    else:
        path = filedialog.askdirectory(title='选择工作区目录', parent=root, mustexist=True)
    print(json.dumps({'path':path or None},ensure_ascii=False))
finally:
    root.destroy()
