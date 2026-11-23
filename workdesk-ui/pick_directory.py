"""Native directory chooser, launched only by an explicit UI action."""
import json
import sys
import tkinter as tk
from tkinter import filedialog

sys.stdout.reconfigure(encoding='utf-8')
root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
try:
    path=filedialog.askdirectory(title='选择工作区目录',parent=root,mustexist=True)
    print(json.dumps({'path':path or None},ensure_ascii=False))
finally:
    root.destroy()
