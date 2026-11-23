"""Build one self-contained Windows repair EXE from explicit old/new program files."""
from argparse import ArgumentParser
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile


PATCH_ID = '1.0.0-capability-recovery-20261123'


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def allowed(name):
    parts = PurePosixPath(name).parts
    if not name or '\\' in name or ':' in name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
        return False
    if name.startswith('src/researchos/workdesk/'):
        return name.endswith('.py')
    if name.startswith('workdesk-ui/static/'):
        return Path(name).suffix in ('.js', '.css')
    if name.startswith('workdesk-ui/dist/'):
        return Path(name).suffix in ('.js', '.css', '.html', '.json', '.svg', '.png', '.woff2')
    return False


def compile_exe(archive, output, *, extra_sources=(), main=None):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    args = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/out:' + str(output), '/resource:' + str(archive) + ',WorkdeskRepairPayload',
            '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
            '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
            '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll']
    if main:
        args.append('/main:' + main)
    args.extend([str(Path(__file__).with_name('WorkdeskRepair.cs')), *map(str, extra_sources)])
    subprocess.run(args, check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def build(baseline, release_manifest, payload, names, work, output):
    names = sorted(names)
    if len(names) != len(set(n.casefold() for n in names)) or not names or not all(allowed(n) for n in names):
        raise ValueError('Explicit file list contains duplicates or unsupported program paths')
    manifest = json.loads(release_manifest.read_text(encoding='utf-8-sig'))
    if manifest.get('product') != 'Workdesk' or manifest.get('version') != '1.0.0' or manifest.get('installer') is not True:
        raise ValueError('Use the official 1.0.0 installed release.json as baseline')
    files = []
    for name in names:
        old, new = baseline / name, payload / name
        if not new.is_file() or not new.resolve().is_relative_to(payload):
            raise ValueError('Missing or redirected payload file: ' + name)
        if old.is_file() and digest(old) == digest(new):
            continue
        files.append({'path': name, 'before': digest(old) if old.is_file() else None,
                      'after': digest(new), 'bytes': new.stat().st_size})
    if not files:
        raise ValueError('No changed program files')
    plan = {'schema': 1, 'product': 'Workdesk', 'patch_id': PATCH_ID, 'base_version': '1.0.0',
            'launcher_sha256': digest(baseline / 'Workdesk.exe'), 'release_sha256': digest(release_manifest), 'files': files}
    work.mkdir(parents=True, exist_ok=True)
    archive = work / 'repair-payload.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        package.writestr('manifest.json', json.dumps(plan, ensure_ascii=False, indent=2).encode('utf-8'))
        for file in files:
            package.write(payload / file['path'], 'payload/' + file['path'])
    (work / 'manifest.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    compile_exe(archive, output)
    receipt = {'exe': str(output), 'bytes': output.stat().st_size, 'sha256': digest(output), 'patch_id': PATCH_ID,
               'files': len(files), 'archive': str(archive), 'manifest': str(work / 'manifest.json')}
    (work / 'build-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    p = ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--release-manifest', type=Path, required=True)
    p.add_argument('--payload', type=Path, required=True)
    p.add_argument('--files', type=Path, required=True, help='JSON array of exact relative file names')
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = build(args.baseline.resolve(strict=True), args.release_manifest.resolve(strict=True),
                   args.payload.resolve(strict=True), json.loads(args.files.read_text(encoding='utf-8-sig')),
                   args.work.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
