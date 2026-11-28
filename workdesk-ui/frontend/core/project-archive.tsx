import {useEffect, useRef, useState} from 'react';
import {coreClient as core, CoreRequestError} from './client';
import './document-tools.css';

type Version = {kind:string; source:string; entry:string; sha256:string};
type Material = {name:string; bytes:number; version:Version};
type ManifestEntry = Version & {name:string; bytes:number; archive_path:string};
type Scope = {archive_id:string; project_id:string; source_root:string; scope_sha256:string;
  state:string; revision:number; manifest:ManifestEntry[]; package_bytes?:number; package_sha256?:string};
type Page<T> = {cursor:string; has_more:boolean} & T;
const status:Record<string,string> = {PREVIEW:'待确认', EXPORTED:'已导出', ARCHIVED:'已归档', REOPENED:'已重开'};
const reason = (error:unknown) => error instanceof Error ? error.message : '读取项目资料失败。';
const key = (v:Version) => JSON.stringify([v.kind,v.source,v.entry,v.sha256]);

export default function ProjectArchive({context,onClose}:{context:{project_id:string};onClose:()=>void}) {
  return <ArchivePanel key={context.project_id} project={context.project_id} onClose={onClose}/>;
}

function ArchivePanel({project,onClose}:{project:string;onClose:()=>void}) {
  const base='v2/projects/'+encodeURIComponent(project)+'/';
  const [materials,setMaterials]=useState<Material[]>([]), [scopes,setScopes]=useState<Scope[]>([]);
  const [selection,setSelection]=useState<Version[]>([]), [focus,setFocus]=useState('');
  const [nextMaterials,setNextMaterials]=useState(''), [nextScopes,setNextScopes]=useState('');
  const [busy,setBusy]=useState(false), [pending,setPending]=useState(''), [error,setError]=useState('');
  const lock=useRef(false), alive=useRef(true);
  const active=scopes.find(s=>s.archive_id===focus);
  useEffect(()=>{
    const ctl=new AbortController();
    alive.current=true;
    void (async()=>{
      const identity=await core.loadIdentity(ctl.signal);
      if(identity.core_engine!=='go')throw Error('当前实例未启用项目范围归档。');
      const [m,a]=await Promise.all([
        core.request<Page<{materials:Material[]}>>(base+'materials',{signal:ctl.signal}),
        core.request<Page<{archives:Scope[]}>>(base+'archives',{signal:ctl.signal})
      ]);
      if(ctl.signal.aborted)return;
      setMaterials(m.materials);setNextMaterials(m.has_more?m.cursor:'');
      setScopes(a.archives);setNextScopes(a.has_more?a.cursor:'');
      setPending(core.uncertainCommands().find(c=>c.kind.startsWith('project.archive.'))?.id||'');
    })().catch(e=>{if(!ctl.signal.aborted)setError(reason(e));});
    return ()=>{alive.current=false;ctl.abort();};
  },[base]);

  async function perform(action:()=>Promise<void>) {
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');
    try{await action();}
    catch(e){if(alive.current){setError(reason(e));if(e instanceof CoreRequestError&&e.unknown)setPending(e.commandId||'');}}
    finally{lock.current=false;if(alive.current)setBusy(false);}
  }
  function show(scope:Scope) {
    if(!alive.current||scope.project_id!==project)return;
    setScopes(old=>[scope,...old.filter(v=>v.archive_id!==scope.archive_id)]);
    setFocus(scope.archive_id);
  }
  function submit(action:string, payload:Record<string,unknown>) {
    void perform(async()=>{
      const data=await core.command<Scope>('project.archive.'+action,{project_id:project,...payload});
      show(data.receipt);
    });
  }
  function transition(action:string){
    if(active)submit(action,{archive_id:active.archive_id,revision:active.revision,confirm:true});
  }
  const disabled=busy||!!pending;
  return <section className="dt-panel"><header><h2>范围归档</h2><button onClick={onClose}>关闭</button></header>
    <p>选择要保留的具体版本。导出包包含原始字节和来源清单，归档与重开均保留原位置的文件。</p>
    {error&&<p role="alert">{error}</p>}
    {pending&&<p>待核对命令 {pending} <button disabled={busy} onClick={()=>void perform(async()=>{
      const data=await core.receipt(pending);
      if(data.state!=='RECORDED')throw Error('尚未找到原命令回执。');
      if(alive.current){setPending('');if(data.receipt?.archive_id)show(data.receipt);}
    })}>查原回执</button></p>}
    <fieldset disabled={disabled}><legend>项目材料版本</legend>
      {materials.map(m=><label key={key(m.version)}><input type="checkbox"
        checked={selection.some(v=>key(v)===key(m.version))}
        onChange={e=>{setSelection(e.target.checked?[...selection,m.version]:selection.filter(v=>key(v)!==key(m.version)));setFocus('');}}/>
        {m.name} · {m.bytes} 字节 · {m.version.sha256.slice(0,12)}</label>)}
      {!materials.length&&<p>当前项目尚无已登记的材料版本。</p>}
      {nextMaterials&&<button onClick={()=>void perform(async()=>{
        const page=await core.request<Page<{materials:Material[]}>>(base+'materials?cursor='+encodeURIComponent(nextMaterials));
        if(alive.current){setMaterials(old=>[...old,...page.materials.filter(m=>!old.some(x=>key(x.version)===key(m.version)))]);setNextMaterials(page.has_more?page.cursor:'');}
      })}>更多材料</button>}
      <button disabled={selection.length<1||selection.length>32} onClick={()=>submit('preview',{versions:selection})}>预览选定范围（{selection.length}/32）</button>
    </fieldset>
    <h3>已登记范围</h3>
    {scopes.map(s=><button key={s.archive_id} disabled={busy} aria-pressed={s.archive_id===focus} onClick={()=>setFocus(s.archive_id)}>
      {status[s.state]||s.state} · {s.manifest[0]?.name} · {s.manifest.length} 个版本
    </button>)}
    {nextScopes&&<button disabled={busy} onClick={()=>void perform(async()=>{
      const page=await core.request<Page<{archives:Scope[]}>>(base+'archives?cursor='+encodeURIComponent(nextScopes));
      if(alive.current){setScopes(old=>[...old,...page.archives.filter(s=>!old.some(x=>x.archive_id===s.archive_id))]);setNextScopes(page.has_more?page.cursor:'');}
    })}>更多范围</button>}
    {active&&<article><h3>{status[active.state]} · 范围修订 {active.revision}</h3>
      <p>原件保留位置 {active.source_root}</p>
      <ul>{active.manifest.map(f=><li key={key(f)}>{f.name} · {f.bytes} 字节
        <details><summary>版本与来源</summary><p>{f.sha256}</p><p>{f.kind} · {f.source} · {f.entry}</p></details></li>)}</ul>
      {active.state==='PREVIEW'&&<button disabled={disabled} onClick={()=>transition('export')}>生成导出包</button>}
      {['PREVIEW','EXPORTED'].includes(active.state)&&<button disabled={disabled} onClick={()=>transition('confirm')}>确认归档此范围</button>}
      {active.state==='ARCHIVED'&&<button disabled={disabled} onClick={()=>transition('reopen')}>重开此范围</button>}
      {active.package_sha256&&<p><a download href={'api/'+base+'archive-download?archive_id='+encodeURIComponent(active.archive_id)}>下载 ZIP 与清单</a> · {active.package_bytes} 字节</p>}
    </article>}
  </section>;
}
