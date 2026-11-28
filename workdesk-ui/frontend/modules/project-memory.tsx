import { useEffect, useState } from 'react';
import { coreClient } from '../core/client';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Textarea } from '../components/ui/textarea';

type Citation = {project_id:string;collection_id:string;source_id:string;source_revision:number;locator:string;text_sha256:string};
type Source = {citation?:Citation;kind:string; id?:string; revision?:number; title?:string; href?:string; version?:Record<string,string>};
type Memory = {id:string;revision:number;title:string;body:string;state:string;area:string;sources?:Source[];command_id:string};
type Handoff = {id:string; sections:Record<string,{text:string;source:Source}[]>;updated:number};
type Detail = {memory:Memory;versions:{revision:number;command_id:string}[]};
const areas:Record<string,string>={note:'笔记',goal:'目标',decision:'决定',blocker:'未决问题',next_step:'下一步'};
const sections:Record<string,string>={goals:'目标',material_versions:'材料版本',decisions:'决定',open_questions:'未决问题',next_steps:'下一步',notes:'补充资料'};
const refKey=(r:Source)=>JSON.stringify([r.kind,r.id,r.revision,r.version,r.citation]);
const blank={title:'',body:'',area:'note'};
function sourceLink(ref:Source){
  const text=(ref.title||ref.id||ref.kind)+(ref.revision?' · r'+ref.revision:'');
  return ref.href?<a href={ref.href}>{text}</a>:<span>{text}</span>;
}

export function ProjectMemory({route}:{route:string}) {
  const params=new URLSearchParams(route.split('?')[1]||'');
  const project=params.get('project_id')||'', memoryId=params.get('id'), rev=params.get('revision');
  const scope=project?'project':'personal', query=new URLSearchParams({scope,project_id:project}).toString();
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]);
  const [rows,setRows]=useState<Memory[]>([]),[sources,setSources]=useState<Source[]>([]);
  const [handoffs,setHandoffs]=useState<Handoff[]>([]),[detail,setDetail]=useState<Detail>();
  const [citation]=useState<Source|undefined>(()=>{try{
    const c: Citation=JSON.parse(params.get('citation')||'null');
    if(c?.project_id===project && project && c.source_id && typeof c.locator==='string' && c.text_sha256)
      return {kind:'knowledge',citation:c,title:'知识段落 '+c.locator};
  }catch{/* An invalid deep link grants no source. */}return undefined;});
  const [selected,setSelected]=useState<string[]>(citation?[refKey(citation)]:[]),[updated,setUpdated]=useState(0);
  const [draft,setDraft]=useState(blank),[editing,setEditing]=useState<Memory>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[loaded,setLoaded]=useState(false);
  const startUrl='#resources/memory'+(project?'?project_id='+encodeURIComponent(project):'');
  const currentLink=(id:string,revision:number)=>'#resources/memory?'+new URLSearchParams({project_id:project,id,revision:String(revision)});
  useEffect(()=>{
    let active=true;
    async function load(){
      const catalog=await coreClient.request<{memories:Memory[]}>('v2/memories?'+query);
      const projectRows:{id:string;name:string}[]=[]; let more=true,cursor='0';
      while(more){
        const list=await coreClient.request<{projects:{id:string;name:string}[];has_more:boolean;cursor:string}>('v2/projects?limit=200&cursor='+cursor);
        projectRows.push(...list.projects);more=list.has_more;cursor=list.cursor;
      }
      const refs:Source[]=citation?[citation]:[];
      let history:Handoff[]=[];
      if(project){
        const items=await coreClient.request<{work_items:{id:string;revision:number;title:string}[]}>('v1/work-items?project='+encodeURIComponent(project));
        refs.push(...items.work_items.map(i=>({kind:'work_item',id:i.id,revision:i.revision,title:i.title})));
        cursor='0'; more=true;
        while(more){
          const page=await coreClient.request<{materials:{name:string;version:Record<string,string>}[];has_more:boolean;cursor:string}>('v2/projects/'+encodeURIComponent(project)+'/materials?limit=100&cursor='+cursor);
          refs.push(...page.materials.map(m=>({kind:'material',title:m.name,version:m.version})));
          more=page.has_more;cursor=page.cursor;
        }
        history=(await coreClient.request<{handoffs:Handoff[]}>('v2/project-handoffs?'+query)).handoffs;
      }
      let open:Detail|undefined;
      if(memoryId)open=await coreClient.request<Detail>('v2/memories?'+query+'&id='+encodeURIComponent(memoryId)+(rev?'&revision='+encodeURIComponent(rev):''));
      if(active){setProjects(projectRows);setRows(catalog.memories);setSources(refs);setHandoffs(history);setDetail(open);setLoaded(true);}
    }
    load().catch(e=>{if(active)setError(String(e.message||e));});
    return ()=>{active=false;};
  },[query,updated,memoryId,rev,project,citation]);

  const memories=rows.filter(r=>r.state==='ACTIVE').map(r=>({kind:'memory',id:r.id,revision:r.revision,title:r.title}));
  const chosen=[...memories,...sources].filter(r=>selected.includes(refKey(r)));
  const selectedMemories=chosen.filter(r=>r.kind==='memory');
  function select(ref:Source){
    const key=refKey(ref);setSelected(old=>old.includes(key)?old.filter(k=>k!==key):[...old,key]);
  }
  async function mutate(kind:string,payload:object){
    if(busy)return;
    setBusy(true);setError('');
    try{
      await coreClient.command(kind,{scope,project_id:project,...payload});
      setSelected([]);setDraft(blank);setEditing(undefined);
      if(memoryId)location.hash=startUrl;else setUpdated(n=>n+1);
    }catch(e){setError(e instanceof Error?e.message:'操作未确认，请核对原回执。');}
    finally{setBusy(false);}
  }
  async function remove(row:Memory){
    const dialog=(window as Window & {WorkdeskDialogs?:{confirm(options:object):Promise<boolean>}}).WorkdeskDialogs;
    const message='删除「'+row.title+'」及其所有记忆修订？已生成的项目接续和合并记忆另行保存。';
    if(await (dialog?dialog.confirm({title:'删除记忆',message,confirmLabel:'删除',tone:'danger'}):Promise.resolve(window.confirm(message)))){
      await mutate('memory.delete',{id:row.id,revision:row.revision,confirm:true});
    }
  }
  function edit(row:Memory){setEditing(row);setDraft({title:row.title,body:row.body,area:row.area});setSelected([]);}
  function save(merge:boolean){
    const refs=merge?selectedMemories:chosen;
    const base=editing?{id:editing.id,revision:editing.revision}:{};
    void mutate(merge?'memory.merge':'memory.save',{...draft,...base,...(refs.length?{sources:refs}:{})});
  }
  return <section className="wd-resource-space"><aside className="wd-resource-index" aria-label="记忆范围">
    <a href="#resources">返回资料空间</a><nav><a href="#resources/memory" aria-current={!project?'page':undefined}>个人记忆</a>
    {projects.map(p=><a key={p.id} href={'#resources/memory?project_id='+encodeURIComponent(p.id)} aria-current={p.id===project?'page':undefined}>{p.name}</a>)}</nav></aside>
    <main className="wd-resource-content"><header className="wd-resource-header"><h1>{project?'项目记忆与接续':'个人记忆'}</h1></header>
    {error&&<p role="alert">{error} <Button variant="outline" onClick={()=>setUpdated(n=>n+1)}>刷新</Button></p>}
    {!loaded&&!error&&<p role="status">正在读取记忆…</p>}
    {detail&&<section className="wd-resource-detail-card"><a href={startUrl}>返回列表</a><h2>{detail.memory.title} · r{detail.memory.revision}</h2>
      <p style={{whiteSpace:'pre-wrap'}}>{detail.memory.body}</p><p>记录命令 {detail.memory.command_id}</p>
      {(detail.memory.sources||[]).map(source=><p key={refKey(source)}>{sourceLink(source)}</p>)}
      <details><summary>历史修订</summary>{detail.versions.map(v=><p key={v.revision}><a href={currentLink(detail.memory.id,v.revision)}>r{v.revision}</a> · {v.command_id}</p>)}</details></section>}
    {loaded&&!detail&&<fieldset disabled={busy} style={{border:0,padding:0,minWidth:0}}>
      <form className="wd-resource-detail-card" onSubmit={e=>{e.preventDefault();save(false);}}>
        <h2>{editing?'编辑记忆':'新增记忆'}</h2><label>标题<Input required value={draft.title} maxLength={160} onChange={e=>setDraft({...draft,title:e.target.value})}/></label>
        <label>用途 <select value={draft.area} onChange={e=>setDraft({...draft,area:e.target.value})}>{Object.entries(areas).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
        <label>内容<Textarea required value={draft.body} maxLength={32768} onChange={e=>setDraft({...draft,body:e.target.value})}/></label>
        <div className="wd-resource-heading-actions">{editing&&<Button type="button" variant="outline" onClick={()=>{setDraft(blank);setEditing(undefined);}}>取消编辑</Button>}
        <Button type="submit">保存记忆</Button>
        {!editing&&selectedMemories.length>1&&<Button type="button" variant="outline" disabled={!draft.body.trim()||!draft.title.trim()||chosen.length!==selectedMemories.length}
          onClick={()=>save(true)}>合并所选记忆并归档来源</Button>}</div></form>
      <h2>记忆记录</h2>{!rows.length&&<p>当前范围尚无记忆。</p>}
      {rows.map(row=>{const ref={kind:'memory',id:row.id,revision:row.revision};return <article className="wd-resource-detail-card" key={row.id}>
        <h3>{row.state==='ACTIVE'&&<input aria-label={'选择 '+row.title} type="checkbox" checked={selected.includes(refKey(ref))} onChange={()=>select(ref)}/>} <a href={currentLink(row.id,row.revision)}>{row.title}</a></h3>
        <p>{areas[row.area]} · r{row.revision} · {row.state==='ACTIVE'?'有效':'已归档'}</p><p style={{whiteSpace:'pre-wrap'}}>{row.body}</p>
        {row.state==='ACTIVE'&&<Button type="button" variant="ghost" onClick={()=>edit(row)}>编辑</Button>}
        <Button type="button" variant="ghost" onClick={()=>void mutate('memory.archive',{id:row.id,revision:row.revision,archived:row.state==='ACTIVE'})}>{row.state==='ACTIVE'?'归档':'恢复'}</Button>
        <Button type="button" variant="ghost" onClick={()=>void remove(row)}>删除</Button></article>;})}
      {project&&<section><h2>项目接续材料</h2><details><summary>选择工作项和材料版本</summary>
        {sources.map(ref=><p key={refKey(ref)}><label><input type="checkbox" checked={selected.includes(refKey(ref))} onChange={()=>select(ref)}/> {ref.title} · {ref.version?.sha256?.slice(0,12)||'r'+(ref.citation?.source_revision||ref.revision)}</label></p>)}
        {!sources.length&&<p>项目尚无工作项或已登记材料。</p>}</details>
        <Button type="button" disabled={!chosen.length} onClick={()=>void mutate('project.handoff-create',{sources:chosen})}>按所选来源生成接续材料</Button>
        {handoffs.map(h=><details key={h.id} className="wd-resource-detail-card"><summary>{new Date(h.updated*1000).toLocaleString()} · 接续材料</summary>
          {Object.entries(sections).map(([section,label])=><section key={section}><h3>{label}</h3>{(h.sections[section]||[]).map((line,i)=><p key={i}>{line.text} · {sourceLink(line.source)}</p>)}</section>)}</details>)}</section>}
    </fieldset>}</main></section>;
}
