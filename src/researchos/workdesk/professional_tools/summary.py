"""Produce a small, real material report for the core tool invocation boundary."""
from __future__ import annotations
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys


def execute(request):
    root = Path(request['project_root']).resolve(strict=True)
    output = Path(request['output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=False)
    selected = request.get('input_files') or []
    if not selected:
        selected = [p.name for p in sorted(root.iterdir())
                    if p.is_file() and not p.is_symlink()
                    and p.suffix.lower() in {'.txt', '.md', '.csv', '.json', '.py', '.go', '.ts', '.toml'}][:40]
    if len(selected) > 200:
        raise ValueError('一次最多选择200份本地材料。')
    rows = []
    for name in selected:
        candidate = Path(name)
        path = (root / candidate).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('材料必须是所选项目内的正常文件。')
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('单份概要材料超过8MiB，请另选材料。')
        try:
            text = raw.decode('utf-8-sig')
            lines = len(text.splitlines())
        except UnicodeDecodeError:
            lines = None
        rows.append({'file': path.relative_to(root).as_posix(), 'bytes': len(raw),
                     'sha256': sha256(raw).hexdigest(), 'lines': lines})
    report = output / '材料概要.txt'
    report.write_text('项目材料概要\n\n' + str(root) + '\n\n' +
                      '\n'.join(f"{r['file']}  {r['bytes']} 字节  {r['lines']} 行" for r in rows) + '\n', encoding='utf-8')
    table = output / '材料清单.csv'
    with table.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['file', 'bytes', 'lines', 'sha256'])
        writer.writeheader()
        writer.writerows(rows)
    artifacts = []
    for path, mime in [(report, 'text/plain'), (table, 'text/csv')]:
        raw = path.read_bytes()
        artifacts.append({'path': str(path), 'sha256': sha256(raw).hexdigest(), 'bytes': len(raw), 'mime': mime})
    return {'state': 'COMPLETED', 'summary': f'已核对 {len(rows)} 份材料，生成概要与CSV清单。',
            'artifacts': artifacts, 'real_model_requests': 0, 'tool_version': 'material-summary/1'}


def main():
    try:
        request = json.loads(sys.stdin.read(1024 * 1024 + 1))
        print(json.dumps(execute(request), ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'state': 'FAILED', 'error': str(exc), 'artifacts': []}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
