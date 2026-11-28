"""Versioned, project-scoped retrieval over the existing local resource library."""
import base64
from collections import Counter
import hashlib
import json
import math
import re
import time

from .resource_space import checked_text, fold, valid_id
from .store import canonical

ENGINE = 'corpus-lsa/1'
INDEX_VERSION = 'resource-passages/1'
SCOPE = 'knowledge_scope_v1'
INDEX = 'knowledge_passages_v1'
MODEL = 'knowledge_collection_lsa_v1'


def get_object(db, kind, identity):
    row = db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?', (kind, kind + ':' + identity)).fetchone()
    return json.loads(row[0]) if row else None


def put_object(db, kind, identity, value):
    db.execute('INSERT INTO collaboration(kind,id,data,updated) VALUES(?,?,?,?) ON CONFLICT(id) '
               'DO UPDATE SET data=excluded.data,updated=excluded.updated',
               (kind, kind + ':' + identity, canonical(value), time.time()))


def scope(db, project_id):
    if not db.execute('SELECT 1 FROM projects WHERE id=?', (valid_id(project_id),)).fetchone():
        raise ValueError('未找到所选项目。')
    return get_object(db, SCOPE, project_id) or {'project_id': project_id, 'revision': 0, 'collection_ids': []}


def collection_ids(ids):
    if not isinstance(ids, list) or len(ids) > 16:
        raise ValueError('每次最多选择 16 个知识库。')
    return sorted({valid_id(value) for value in ids})


def save_scope(db, payload, command_id):
    if set(payload) != {'project_id', 'collection_ids', 'revision', 'confirm'} or payload['confirm'] is not True:
        raise ValueError('请确认该项目可以使用的知识库。')
    grant = scope(db, payload['project_id'])
    if type(payload['revision']) is not int or grant['revision'] != payload['revision']:
        raise ValueError('项目知识库授权已变更，请重新读取。')
    ids = collection_ids(payload['collection_ids'])
    for identity in ids:
        if not db.execute('SELECT 1 FROM resource_space_collections WHERE id=?', (identity,)).fetchone():
            raise ValueError('所选知识库不存在。')
    grant.update(collection_ids=ids, revision=grant['revision'] + 1, authorization_id=command_id)
    put_object(db, SCOPE, grant['project_id'], grant)
    return {'scope': grant, 'no_dispatch': True}


def selected(db, project_id, ids):
    grant = scope(db, project_id)
    ids = collection_ids(ids)
    if not ids or not set(ids).issubset(grant['collection_ids']):
        raise ValueError('请选择该项目已获准使用的知识库。')
    return grant, ids


def sources(db, ids):
    placeholders = ','.join('?' for _ in ids)
    rows = db.execute(f'SELECT id,collection_id,title,revision FROM resource_space_items WHERE '
                      f'trashed_at IS NULL AND collection_id IN ({placeholders}) ORDER BY id LIMIT 513', ids).fetchall()
    if len(rows) > 512:
        raise ValueError('所选知识库超过 512 份资料，请缩小集合范围。')
    return rows


def document(db, source):
    from .knowledge_passages import passages
    row = db.execute('SELECT r.title,r.note,r.tags,r.body,r.url,f.filename,f.sha256,f.preview,f.content '
                     'FROM resource_space_items r LEFT JOIN resource_space_files f ON f.item_id=r.id '
                     'WHERE r.id=?', (source['id'],)).fetchone()
    chunks, limits = passages(row)
    return {'source_revision': source['revision'], 'index_version': INDEX_VERSION,
            'chunks': chunks, 'limits': limits}


def current_index(db, source):
    entry = get_object(db, INDEX, source['id'])
    if entry and entry['source_revision'] == source['revision'] and entry['index_version'] == INDEX_VERSION:
        return entry
    return None


def reindex(db, payload):
    if set(payload) != {'project_id', 'collection_ids'}:
        raise ValueError('重建索引需要项目和知识库。')
    grant, ids = selected(db, payload['project_id'], payload['collection_ids'])
    rebuilt, reused, limits = [], 0, []
    rows = sources(db, ids)
    by_collection = {identity: [] for identity in ids}
    for source in rows:
        index = current_index(db, source)
        if index is None:
            index = document(db, source)
            put_object(db, INDEX, source['id'], index)
            rebuilt.append(source['id'])
        else:
            reused += 1
        chunks = by_collection[source['collection_id']]
        chunks.extend(index['chunks'])
        if len(chunks) > 2048:
            raise ValueError('单个知识库超过 2048 个引用位置，请拆分集合后建索引。')
        if index['limits']:
            limits.append({'source_id': source['id'], 'limits': index['limits']})
    for identity, chunks in by_collection.items():
        signature = model_signature(rows, identity)
        saved = get_object(db, MODEL, identity)
        if not saved or saved.get('signature') != signature:
            model = train_collection(chunks) if chunks else {}
            put_object(db, MODEL, identity, {'signature': signature, 'engine': ENGINE, **model})
    return {'project_id': grant['project_id'], 'scope_revision': grant['revision'],
            'authorization_id': grant['authorization_id'], 'collection_ids': ids,
            'engine': ENGINE, 'index_version': INDEX_VERSION, 'rebuilt': rebuilt, 'reused': reused, 'limits': limits}


def tokens(text):
    result = []
    for word in re.findall(r'[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+', fold(text)):
        if '\u3400' <= word[0] <= '\u9fff':
            result.extend(word[i:i+2] for i in range(max(1, len(word) - 1)))
        else:
            result.append(word)
    return result


def model_signature(rows, collection):
    revisions = [(row['id'], row['revision']) for row in rows if row['collection_id'] == collection]
    return hashlib.sha256(canonical([ENGINE, INDEX_VERSION, revisions]).encode()).hexdigest()


def pack_matrix(values):
    return base64.b64encode(values.astype('<f4', copy=False).tobytes()).decode('ascii')


def train_collection(chunks):
    """Build the per-collection vocabulary, projection axes and passage embeddings."""
    import numpy as np
    counts = [Counter(tokens(chunk['text'])) for chunk in chunks]
    df = Counter()
    for counter in counts:
        df.update(counter.keys())
    vocab = {term: pos for pos, (term, _) in enumerate(df.most_common(4096))}
    if not vocab:
        return {}
    weights = np.array([math.log((1 + len(chunks)) / (1 + df[t])) + 1 for t in vocab], dtype=np.float32)
    matrix = np.zeros((len(chunks), len(vocab)), dtype=np.float32)
    for counter, row in zip(counts, matrix):
        for term, count in counter.items():
            if term in vocab:
                row[vocab[term]] = (1 + math.log(count)) * weights[vocab[term]]
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    rank = min(24, max(1, round(math.sqrt(len(chunks)))), min(matrix.shape))
    # A bounded, deterministic randomized SVD also works for sparse-sized local collections.
    sketch = np.random.default_rng(73).standard_normal((matrix.shape[1], min(rank+6, min(matrix.shape))), dtype=np.float32)
    projected = matrix @ sketch
    for _ in range(2):
        basis, _ = np.linalg.qr(projected)
        projected = matrix @ (matrix.T @ basis)
    basis, _ = np.linalg.qr(projected)
    _, singular, axes = np.linalg.svd(basis.T @ matrix, full_matrices=False)
    axes = axes[:min(rank, int((singular > 1e-6).sum()))]
    latent = matrix @ axes.T
    return {'vocabulary': list(vocab), 'axes': pack_matrix(axes * weights),
            'vectors': pack_matrix(latent), 'rank': len(axes)}


def semantic_scores(query, model):
    import numpy as np
    if not model.get('rank'):
        return []
    vocab = {term: idx for idx, term in enumerate(model['vocabulary'])}
    axes = np.frombuffer(base64.b64decode(model['axes']), dtype='<f4').reshape((model['rank'], len(vocab)))
    latent = np.frombuffer(base64.b64decode(model['vectors']), dtype='<f4').reshape((-1, model['rank']))
    target = np.zeros(model['rank'], dtype=np.float32)
    for term, count in Counter(tokens(query)).items():
        if term in vocab:
            target += axes[:, vocab[term]] * (1 + math.log(count))
    norms = np.linalg.norm(latent, axis=1) * np.linalg.norm(target)
    scores = (latent @ target) / np.maximum(norms, 1e-8)
    return [(idx, float(score)) for idx, score in enumerate(scores) if score > 0.15 and norms[idx] > 1e-6]


def citation(project_id, row, chunk):
    return {'project_id': project_id, 'collection_id': row['collection_id'], 'source_id': row['id'],
            'source_revision': row['revision'], 'locator': chunk['locator'],
            'text_sha256': hashlib.sha256(chunk['text'].encode()).hexdigest()}


def search(db, payload):
    if set(payload) != {'project_id', 'collection_ids', 'q', 'method'} or payload['method'] not in ('keyword', 'semantic'):
        raise ValueError('请选择关键词或本地语义检索。')
    query = checked_text(payload['q'], '检索词', 300, required=True)
    method = payload['method']
    grant, ids = selected(db, payload['project_id'], payload['collection_ids'])
    chunks, missing, limits, scores = [], [], [], []
    rows = sources(db, ids)
    for identity in ids:
        group = [source for source in rows if source['collection_id'] == identity]
        model = get_object(db, MODEL, identity) if method == 'semantic' else None
        if method == 'semantic' and (not model or model.get('signature') != model_signature(group, identity)):
            missing.extend(source['id'] for source in group)
            continue
        offset = len(chunks)
        for source in group:
            index = current_index(db, source) or document(db, source)
            if index['limits']:
                limits.append({'source_id': source['id'], 'limits': index['limits']})
            chunks.extend({**chunk, 'source': source} for chunk in index['chunks'])
            if len(chunks) > 2048:
                raise ValueError('所选范围超过 2048 个引用位置，请缩小范围。')
        if model:
            scores.extend((offset + idx, score) for idx, score in semantic_scores(query, model))
    if method == 'keyword':
        terms = set(fold(query).split())
        scores = []
        for idx, chunk in enumerate(chunks):
            text = fold(chunk['text'])
            if all(t in text for t in terms):
                scores.append((idx, sum(text.count(t) for t in terms)))
    results = []
    for idx, score in sorted(scores, key=lambda pair: (-pair[1], pair[0]))[:25]:
        chunk = chunks[idx]
        source = chunk['source']
        results.append({'title': source['title'], 'excerpt': chunk['text'][:450], 'score': round(score, 6),
                        'citation': citation(grant['project_id'], source, chunk)})
    return {'project_id': grant['project_id'], 'scope_revision': grant['revision'],
            'authorization_id': grant['authorization_id'], 'collection_ids': ids,
            'method': method, 'engine': ENGINE if method == 'semantic' else 'literal-passage/1',
            'results': results, 'needs_reindex': missing, 'limits': limits}


def verify_citation(db, project_id, reference):
    grant = scope(db, project_id)
    keys = {'project_id', 'collection_id', 'source_id', 'source_revision', 'locator', 'text_sha256'}
    if not isinstance(reference, dict) or set(reference) != keys or type(reference['source_revision']) is not int:
        raise ValueError('知识引用缺少有效的来源版本或位置。')
    if reference['project_id'] != project_id or reference['collection_id'] not in grant['collection_ids']:
        return {'state': 'OUT_OF_SCOPE'}
    source = db.execute('SELECT id,collection_id,title,revision FROM resource_space_items WHERE id=? '
                        'AND collection_id=? AND trashed_at IS NULL',
                        (valid_id(reference['source_id']), reference['collection_id'])).fetchone()
    if not source or source['revision'] != reference['source_revision']:
        return {'state': 'STALE'}
    indexed = current_index(db, source) or document(db, source)
    for chunk in indexed['chunks']:
        if reference == citation(project_id, source, chunk):
            return {'state': 'CURRENT', 'title': source['title'], 'text': chunk['text'], 'citation': reference}
    return {'state': 'STALE'}


def read(store, action, args):
    with store.read() as db:
        project_id = args.get('project_id', [''])[0]
        if action == 'scope':
            return {'scope': scope(db, project_id)}
        if action == 'search':
            return search(db, {'project_id': project_id, 'collection_ids': args.get('collection_id', []),
                               'q': args.get('q', [''])[0], 'method': args.get('method', ['keyword'])[0]})
        if action == 'verify':
            return verify_citation(db, project_id, json.loads(args.get('citation', ['null'])[0]))
        raise ValueError('未知知识库读操作。')
