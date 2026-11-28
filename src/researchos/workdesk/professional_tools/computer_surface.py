"""Finite browser or Windows control operation; the job supplies a frozen target."""
import hashlib
import json
from pathlib import Path


class OutcomeUnknown(RuntimeError):
    """An operation was sent but its effect could not be verified."""


def execute(parameters, output_dir):
    action = parameters['action']
    root = Path(output_dir)
    if action.startswith('browser.'):
        from .computer_browser import operate
    elif action.startswith('desktop.'):
        from .computer_windows import operate
    else:
        raise ValueError('不支持此操作。')
    receipt, files = operate(parameters, root)
    out = root / 'surface-receipt.json'
    out.write_text(json.dumps({'action':action, **receipt}, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts = []
    for path, mime in [(out, 'application/json'), *files]:
        raw = path.read_bytes()
        artifacts.append({'path':str(path), 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(), 'mime':mime})
    return {'state':'COMPLETED','summary':receipt['summary'], 'artifacts':artifacts,
            'checks':[{'kind':'目标与回执','state':'PASSED','detail':receipt['summary'],'sha256':artifacts[0]['sha256']}]}
