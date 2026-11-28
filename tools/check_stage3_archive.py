"""Verify selected material scope, durable package bytes and reopening via Go HTTP."""
import hashlib
from io import BytesIO
import json
from urllib.parse import urlencode
from uuid import uuid4
import zipfile

from check_core_chain import require, request


def check_archive(chain):
    project = chain.project_id
    base = '/api/v2/projects/' + project
    original = chain.project / 'notes.txt'
    source = original.read_bytes()
    other = chain.project / 'measurements.csv'
    untouched = other.read_bytes()
    chosen = chain.command('file.selection-save', {'project_id': project, 'files': ['notes.txt', 'measurements.csv']})
    selection = chosen['receipt']['selection']['id']
    rows = chain.api(base + '/materials')['materials']
    selected = [m for m in rows if m['version']['source'] == selection]
    left = next(m['version'] for m in selected if m['name'] == 'notes.txt')
    right = next(m['version'] for m in selected if m['name'] == 'measurements.csv')
    comparison = chain.api(base + '/compare?' + urlencode({'left': json.dumps(left), 'right': json.dumps(right)}))
    require(comparison['mode'] == 'text' and not comparison['identical'], '无法回读所选两版的文本差异')
    initial = {'project_id': project, 'versions': [left]}
    preview = chain.command('project.archive.preview', initial)['receipt']
    require(preview['state'] == 'PREVIEW' and preview['versions'] == [left], '预览范围不符')
    mid = preview['archive_id']

    def payload(revision):
        return {'project_id': project, 'archive_id': mid, 'revision': revision, 'confirm': True}

    other_root = chain.root / 'scope-other'
    other_root.mkdir()
    other_project = chain.command('project.create', {'root': str(other_root), 'name': '归档范围核对'})['receipt']['project_id']
    chain.command('project.archive.preview', {'project_id': other_project, 'versions': [left]}, expected=409)
    chain.command('project.archive.confirm', {**payload(preview['revision']), 'project_id': other_project}, expected=409)

    original.write_bytes(source + b'changed during preview')
    chain.command('project.archive.confirm', payload(preview['revision']), expected=409)
    original.write_bytes(source)
    exported = chain.command('project.archive.export', payload(preview['revision']))['receipt']
    require(exported['state'] == 'EXPORTED' and exported['scope_sha256'] == preview['scope_sha256'], '导出范围变化')
    url = base + '/archive-download?' + urlencode({'archive_id': mid})
    download = request(chain.origin, url, token=chain.backend['viewer'], raw=True)
    require(hashlib.sha256(download).hexdigest() == exported['package_sha256'], '导出包摘要与回执不符')
    with zipfile.ZipFile(BytesIO(download)) as z:
        manifest = json.loads(z.read('manifest.json'))
        require(len(z.namelist()) == 2 and len(manifest['versions']) == 1, '导出范围意外扩大')
        require(z.read(manifest['versions'][0]['archive_path']) == source, '包内文件与原件不一致')
    chain.command('project.archive.confirm', payload(preview['revision']), expected=409)
    identity = str(uuid4())
    params = payload(exported['revision'])
    archived = chain.command('project.archive.confirm', params, identity)['receipt']
    replay = chain.command('project.archive.confirm', params, identity)
    require(replay['replayed'] and replay['receipt'] == archived, '重复请求未返回原回执')
    chain.command('project.archive.confirm', params, expected=409)
    require(archived['state'] == 'ARCHIVED' and original.read_bytes() == source and other.read_bytes() == untouched,
            '归档更改了原文件或未登记')
    # A subsequent edit to the original must not change the sealed download.
    original.write_bytes(source + b'later working edit')
    reopened = chain.command('project.archive.reopen', payload(archived['revision']))['receipt']
    require(reopened['state'] == 'REOPENED' and original.read_bytes() == source + b'later working edit',
            '重开覆盖了后续工作版本')
    require(request(chain.origin, url, token=chain.backend['viewer'], raw=True) == download, '重开后已封存包改变')
    records = chain.api(base + '/archives')['archives']
    require(next(r for r in records if r['archive_id'] == mid)['revision'] == reopened['revision'], '重开修订未持久化')
    # Restore only this test's own edit before a sibling check uses the fixture.
    original.write_bytes(source)
    return {'archive_id': mid, 'source_sha256': left['sha256'], 'package_sha256': exported['package_sha256'],
            'versions': 1, 'revision': reopened['revision'], 'reopened': True, 'originals_retained': True,
            'stale_preview_rejected': True, 'cross_project_rejected': True, 'command_replayed': True,
            'comparison_mode': comparison['mode']}
