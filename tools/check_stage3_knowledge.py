"""Exercise project library grants, local corpus semantics and version-bound citations."""
import base64
import json
from urllib.parse import urlencode
from uuid import uuid4
from check_core_chain import require


def check_knowledge(chain):
    def write(kind, **payload):
        return chain.command(kind, payload)['receipt']

    def page(collection, title, body):
        return chain.command(
            'resource-space.create', {'kind': 'page', 'collection_id': collection, 'title': title, 'body': body}
        )['receipt']['item']

    def query(ids, term, method):
        return chain.api('/api/v1/knowledge/search?' + urlencode({'project_id': chain.project_id,
                         'collection_id': ids, 'q': term, 'method': method}, doseq=True))

    def verify(ref):
        return chain.api('/api/v1/knowledge/verify?' + urlencode({'project_id': chain.project_id,
                                                                  'citation': json.dumps(ref)}))['state']

    first, second = [write('resource-space.collection-create', title=t)['collection']['id']
                     for t in ('差旅资料', '另一组差旅资料')]
    source = page(first, '财务说明', '发票 报销凭证\n付款凭证 报销凭证\n云层 降雨 气象')
    sample = '用途,材料\n差旅报销,发票\n旅费,报销凭证\n'.encode('utf-8')
    other = chain.command('resource-space.create', {'kind': 'file', 'collection_id': second, 'title': '外勤说明',
        'file': {'name': '差旅凭据.csv', 'mime': 'text/csv', 'data': base64.b64encode(sample).decode()}})['receipt']['item']
    scope = {'project_id': chain.project_id, 'collection_ids': [first]}
    grant = write('knowledge.scope-save', **scope, revision=0, confirm=True)['scope']
    denied = chain.api('/api/v1/knowledge/search?' + urlencode({'project_id': chain.project_id,
                       'collection_id': second, 'q': '发票'}), expected=409)
    require('获准' in denied.get('error', ''), '未授权知识库可查询。')
    require(query([first], '发票', 'semantic')['needs_reindex'] == [source['id']], '首次未提示构建索引。')
    both = {**scope, 'collection_ids': [first, second]}
    grant = write('knowledge.scope-save', **both, revision=grant['revision'], confirm=True)['scope']
    built = write('knowledge.reindex', **both)
    require(set(built['rebuilt']) == {source['id'], other['id']}, '两个集合的真实来源没有完整建索引。')

    literal = query([first], '发票', 'keyword')['results']
    require([hit['citation']['locator'] for hit in literal] == ['paragraph:1'], '关键词位置不符。')
    commandscope = {**scope, 'method': 'semantic', 'q': '发票'}
    command_id = str(uuid4())
    result = chain.command('knowledge.search', commandscope, command_id)['receipt']
    recovered = chain.api('/api/v1/receipt?' + urlencode({'id': command_id}))['receipt']
    require(result == recovered and result['scope_revision'] == grant['revision'], '无法复查本次检索授权版本和回执。')
    frozen_grant = chain.api('/api/v1/receipt?' + urlencode({'id': result['authorization_id']}))['receipt']['scope']
    require(frozen_grant == grant, '本次检索未关联原始授权回执。')
    require(all(hit['citation']['collection_id'] == first for hit in result['results']), '混入未选集合。')
    similar = next((hit for hit in result['results'] if hit['citation']['locator'] == 'paragraph:2'), None)
    require(similar and '发票' not in similar['excerpt'], '未检出用语不同的相关段落。')
    ref = similar['citation']
    require(verify(ref) == 'CURRENT', '来源版本或位置核对失败。')
    both_hits = query([first, second], '发票', 'keyword')['results']
    require({(hit['citation']['source_id'], hit['citation']['locator']) for hit in both_hits}
            == {(source['id'], 'paragraph:1'), (other['id'], 'cell:B2')}, '双集合检索范围或 CSV 单元格位置不符。')
    write('resource-space.update', id=source['id'], revision=source['revision'],
          changes={'body': '电子发票 报销凭证\n付款凭证 报销凭证\n云层 降雨 气象'})
    stale = query([first], '发票', 'semantic')
    require(verify(ref) == 'STALE' and stale['needs_reindex'] == [source['id']] and not stale['results'],
            '来源修改后仍沿用了旧引用或旧索引。')
    incremental = write('knowledge.reindex', **both)
    require(incremental['rebuilt'] == [source['id']] and incremental['reused'] == 1, '没有按来源增量重建。')
    newest = query([first], '电子发票', 'keyword')['results'][0]['citation']
    require(newest['source_revision'] == 2 and verify(newest) == 'CURRENT', '重建后新引用不可核对。')
    return {'collection_id': first, 'other_collection_id': second, 'source_id': source['id'],
            'authorization_revision': grant['revision'], 'search_command_id': command_id, 'engine': result['engine'],
            'related_locator': ref['locator'], 'stale_revision': ref['source_revision'], 'current_reference': newest,
            'incremental_rebuild': incremental['rebuilt'], 'reused': incremental['reused'], 'scope_denial': True}
