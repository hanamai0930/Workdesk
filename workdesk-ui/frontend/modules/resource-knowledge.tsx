import { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { coreClient } from '../core/client';

type Collection = { id: string; title: string };
type Project = { id: string; name: string };
type Grant = { project_id: string; revision: number; collection_ids: string[] };
type Citation = { project_id: string; collection_id: string; source_id: string; source_revision: number; locator: string; text_sha256: string };
type Hit = { title: string; excerpt: string; citation: Citation };
type Support = { request<T>(route: string): Promise<T>; command<T>(kind: string, data: object): Promise<T> };
type Coverage = { source_id: string; limits: string[] };
const support = () => (window as Window & { WorkdeskSupport?: Support }).WorkdeskSupport!;
const errorText = (e: unknown) => e instanceof Error ? e.message : '读取知识库失败。';

function ScopedSearch({ project, collections }: { project: string; collections: Collection[] }) {
  const [grant, setGrant] = useState<Grant>();
  const [ids, setIds] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [method, setMethod] = useState('keyword');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [limits, setLimits] = useState<Coverage[]>([]);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setFailure('');
    support().request<{scope: Grant}>('knowledge/scope?' + new URLSearchParams({ project_id: project }))
      .then(({ scope }) => { if (active) { setGrant(scope); setIds(scope.collection_ids); } })
      .catch(e => { if (active) setFailure(errorText(e)); });
    return () => { active = false; };
  }, [project, reload]);

  const authorized = !!grant && ids.length > 0 && ids.every(id => grant.collection_ids.includes(id));
  const changed = grant && (ids.length !== grant.collection_ids.length || !ids.every(id => grant.collection_ids.includes(id)));
  function clearResults() { setHits([]); setNotice(''); setLimits([]); setFailure(''); }
  async function act(operation: string, citation?: Citation) {
    if (pending || !grant) return;
    setPending(true); setFailure(''); setNotice('');
    try {
      const scope = { project_id: project, collection_ids: ids };
      if (operation === 'grant') {
        const result = await support().command<{scope: Grant}>('knowledge.scope-save', { ...scope, revision: grant.revision, confirm: true });
        setGrant(result.scope); setHits([]); setLimits([]); setNotice('项目授权已保存。');
      } else if (operation === 'reindex') {
        const result = await support().command<{rebuilt: string[]; reused: number; limits: Coverage[]}>('knowledge.reindex', scope);
        setHits([]); setLimits(result.limits); setNotice(`已重建 ${result.rebuilt.length} 份资料，沿用 ${result.reused} 份。`);
      } else if (operation === 'search') {
        const result = await support().command<{results: Hit[]; needs_reindex: string[]; limits: Coverage[]}>('knowledge.search',
          { ...scope, method, q: query.trim() });
        setHits(result.results); setLimits(result.limits);
        setNotice(`找到 ${result.results.length} 个引用位置。${result.needs_reindex.length ? '有 ' + result.needs_reindex.length + ' 份资料需重建索引。' : ''}`);
      } else if (citation) {
        const result = await support().request<{state: string}>('knowledge/verify?' + new URLSearchParams({ project_id: project, citation: JSON.stringify(citation) }));
        setNotice(result.state === 'CURRENT' ? '已核对来源版本、位置和文字摘要。' : result.state === 'STALE' ? '来源已变更，请重新检索。' : '该引用已超出项目授权。');
      }
    } catch (e) { setFailure(errorText(e)); }
    finally { setPending(false); }
  }

  return <>
    <fieldset disabled={!grant || pending}><legend>可供本项目使用的知识库</legend>
      {collections.map(item => <label key={item.id}><input type="checkbox" checked={ids.includes(item.id)}
        onChange={e => { clearResults(); setIds(e.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id)); }}/>{item.title}</label>)}
    </fieldset>
    <div className="wd-knowledge-controls">
      <Button type="button" variant="outline" size="sm" disabled={!changed || pending} onClick={() => void act('grant')}>保存项目授权</Button>
      <Button type="button" variant="outline" size="sm" disabled={!authorized || pending} onClick={() => void act('reindex')}>重建所选索引</Button>
    </div>
    <form className="wd-knowledge-controls" onSubmit={e => { e.preventDefault(); void act('search'); }}>
      <select aria-label="检索方式" value={method} disabled={pending} onChange={e => { clearResults(); setMethod(e.target.value); }}>
        <option value="keyword">关键词</option><option value="semantic">本地语义</option></select>
      <Input aria-label="检索内容" value={query} maxLength={300} disabled={pending} placeholder="输入检索内容"
        onChange={e => { clearResults(); setQuery(e.target.value); }}/>
      <Button disabled={!authorized || !query.trim() || pending}>检索</Button>
    </form>
    {notice && <p role="status">{notice}</p>}
    {failure && <div role="alert">{failure} <Button type="button" variant="outline" size="sm" onClick={() => { clearResults(); setGrant(undefined); setReload(v => v+1); }}>重新读取授权</Button></div>}
    {limits.length > 0 && <details><summary>部分资料的索引范围有限（{limits.length}）</summary><ul>{limits.map(item =>
      <li key={item.source_id}><a href={'#resources/item/' + encodeURIComponent(item.source_id)}>查看资料</a> · {item.limits.join(' ')}</li>)}</ul></details>}
    {hits.length > 0 && <ol>{hits.map(hit => <li key={hit.citation.source_id + '/' + hit.citation.locator} data-knowledge-source={hit.citation.source_id}>
      <a href={'#resources/item/' + encodeURIComponent(hit.citation.source_id)}>{hit.title}</a> · {hit.citation.locator} · 版本 {hit.citation.source_revision}
      <p>{hit.excerpt}</p><div className="wd-knowledge-controls"><Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => void act('verify', hit.citation)}>核对引用</Button>
      <a href={'#resources/memory?' + new URLSearchParams({ project_id: project, citation: JSON.stringify(hit.citation) })}>用于记忆或接续</a></div>
    </li>)}</ol>}
  </>;
}

export function ResourceKnowledge({ collections }: { collections: Collection[] }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState('');
  const [failure, setFailure] = useState('');
  useEffect(() => {
    let active = true;
    async function loadProjects() {
      await coreClient.loadIdentity();
      const available: Project[] = [];
      let cursor = '';
      do {
        const page = await coreClient.request<{projects: Project[]; has_more: boolean; cursor?: string | number}>(
          'v2/projects?' + new URLSearchParams({ limit: '100', cursor: cursor || '0' }));
        if (!active) return;
        available.push(...page.projects);
        cursor = page.has_more && page.cursor ? String(page.cursor) : '';
      } while (cursor);
      setProjects(available);
      setProject(available[0]?.id || '');
    }
    void loadProjects().catch(e => { if (active) setFailure(errorText(e)); });
    return () => { active = false; };
  }, []);
  return <section aria-label="项目知识检索" className="wd-resource-knowledge">
    <h2>项目知识检索</h2>
    {failure && <p role="alert">{failure}</p>}
    <label>检索项目 <select aria-label="检索项目" value={project} onChange={e => setProject(e.target.value)}>
      {projects.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    {project && <ScopedSearch key={project} project={project} collections={collections}/>}
  </section>;
}
