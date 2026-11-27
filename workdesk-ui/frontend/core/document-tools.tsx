import {useEffect, useRef, useState} from 'react';
import {coreClient as core, CoreRequestError} from './client';
import {DocumentForm, type DocumentInput, type Capability, type VersionTemplate, templateFormat} from './document-form';
import './document-tools.css';

type Target = {project_id:string; project_root?:string; task_id?:string};
type Binding = {task_id:string; attempt_id:string; spec_hash:string; title:string};
type Artifact = {path:string; relative_path?:string; sha256:string; bytes:number; mime:string};
type Decision = {path:string; sha256:string; decision:string; note?:string};
type Job = {id:string; state:string; project_id:string; task_id?:string; attempt_id?:string; authorization_id?:string;
  tool_version:string; artifacts?:Artifact[]; checks?:unknown; decisions?:Decision[]; summary?:string; error?:string; input_versions?:unknown};
const stateNames:Record<string,string>={QUEUED:'排队中',RUNNING:'运行中',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消',STOP_REQUESTED:'正在停止',STOP_UNKNOWN:'停止待核对',ORPHANED_UNKNOWN:'重启后待核对'};
const active = (job:Job)=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(job.state);
const reason = (e:unknown)=>e instanceof Error ? e.message : '操作失败，请核对原记录。';
const baseName = (p:string)=>p.split(/[\\/]/).pop() || p;
const artifactUrl = (id:string,file:Artifact)=>'api/v2/jobs/'+encodeURIComponent(id)+'/artifact?'+new URLSearchParams({path:file.path,download:'1'});

function ArtifactPreview({job,file,onClose}:{job:string;file:Artifact;onClose:()=>void}){
  const [body,setBody]=useState(''),[error,setError]=useState(''),[blob,setBlob]=useState('');
  const mime=file.mime.split(';')[0];
  const renderedHtml=mime==='text/html'&&file.relative_path==='results/'+job+'/preview/index.html'&&/[\\/]preview[\\/]index\.html$/i.test(file.path);
  const binary=['image/png','image/jpeg','application/pdf'].includes(mime);
  useEffect(()=>{
    const ctl=new AbortController();let url='';
    (async()=>{
      const res=await fetch(artifactUrl(job,file),{signal:ctl.signal,cache:'no-store'});
      if(!res.ok)throw Error('成果版本读取失败，请重新核对。');
      if(binary){const data=await res.blob();if(!ctl.signal.aborted){url=URL.createObjectURL(new Blob([data],{type:mime}));setBlob(url);}}
      else{const text=await res.text();if(!ctl.signal.aborted)setBody(text.slice(0,64000));}
    })().catch(e=>{if(!ctl.signal.aborted)setError(reason(e));});
    return ()=>{ctl.abort();if(url)URL.revokeObjectURL(url);};
  },[job,file.path,file.sha256,mime,binary]);
  return <div className="dt-preview"><button type="button" onClick={onClose}>收起预览</button>
    {error?<p role="alert">{error}</p>:blob?(mime==='application/pdf'?<iframe title={baseName(file.path)} src={blob}/>:<img alt={baseName(file.path)} src={blob}/>)
      :renderedHtml&&body?<iframe sandbox="" title="排版预览" srcDoc={'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; form-action \'none\'; base-uri \'none\'">'+body}/>
      :<pre>{body||'正在读取预览…'}</pre>}</div>;
}

export default function DocumentTools({context,onClose}:{context:Target;onClose:()=>void}){
  const [contexts,setContexts]=useState<Binding[]>([]),[attempt,setAttempt]=useState('');
  const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [catalog,setCatalog]=useState<{capabilities?:Record<string,Capability>;probe_state?:string}>({});
  const [version,refresh]=useState(0),[jobs,setJobs]=useState<Job[]>([]),[unknown,setUnknown]=useState('');
  const [historyCount,showMore]=useState(20),[more,setMore]=useState(false);
  const [focusId,setFocusId]=useState(''),[focusJob,setFocusJob]=useState<Job|null>(null);
  const [template,setTemplate]=useState<VersionTemplate|null>(null);
  const formArea=useRef<HTMLDivElement>(null);
  const [preview,setPreview]=useState<{job:string;file:Artifact}|null>(null);
  const [decision,setDecision]=useState<{job:string;file:Artifact;value:'accept'|'revise'}|null>(null),[note,setNote]=useState('');
  const alive=useRef(true),lock=useRef(false);
  const binding=contexts.find(c=>c.attempt_id===attempt);
  const taskId=context.task_id||binding?.task_id;
  useEffect(()=>{
    alive.current=true;
    const ctl=new AbortController();
    (async()=>{
      const identity=await core.loadIdentity(ctl.signal);
      if(identity.core_engine!=='go'||identity.protocol!==2)throw Error('此实例尚未启用文档工具。');
      const [catalog,{contexts:all}]=await Promise.all([
        core.request<{tools:Array<{tool_version:string;worker_present:boolean;capabilities?:Record<string,Capability>;probe_state?:string}>}>('v2/tools',{signal:ctl.signal}),
        core.request<{contexts:Binding[]}>('v2/tool-contexts?'+new URLSearchParams({project_id:context.project_id}),{signal:ctl.signal})
      ]);
      if(ctl.signal.aborted)return;
      const documentTool=catalog.tools.find(t=>t.tool_version==='document/1');
      if(!documentTool?.worker_present)throw Error('此实例的文档执行器尚未就绪。');
      setCatalog(documentTool);
      const valid=context.task_id?all.filter(c=>c.task_id===context.task_id):all;
      setContexts(valid);if(context.task_id&&valid.length)setAttempt(valid[0].attempt_id);
      setUnknown(core.uncertainCommands().find(c=>c.kind.startsWith('tool.'))?.id||'');setReady(true);
    })().catch(e=>{if(!ctl.signal.aborted)setError(reason(e));});
    return ()=>{alive.current=false;ctl.abort();};
  },[context.project_id,context.task_id]);
  useEffect(()=>{
    const ctl=new AbortController();let timer:ReturnType<typeof setTimeout>;
    async function poll(){
      try{
        const query=new URLSearchParams({project_id:context.project_id,limit:'50'});
        if(taskId)query.set('task_id',taskId);
        const rows:Job[]=[];let hasMore=false;
        do{
          const data=await core.request<{jobs:Job[];has_more?:boolean;cursor?:string}>('v2/jobs?'+query,{signal:ctl.signal});
          rows.push(...data.jobs.filter(j=>j.tool_version==='document/1'));
          hasMore=!!data.has_more && !!data.cursor && String(data.cursor)!==query.get('cursor');
          if(hasMore)query.set('cursor',String(data.cursor));
        }while(hasMore&&rows.length<historyCount);
        let current:Job|null=null;
        if(focusId)current=await core.request<Job>('v2/jobs/'+encodeURIComponent(focusId),{signal:ctl.signal});
        if(ctl.signal.aborted)return;
        setJobs(rows.slice(0,historyCount));setMore(rows.length>historyCount||hasMore);
        setFocusJob(current&&current.project_id===context.project_id&&(!taskId||current.task_id===taskId)?current:null);
        if(rows.some(active)||(current&&active(current)))timer=setTimeout(poll,1600);
      }catch(e){if(!ctl.signal.aborted)setError(reason(e));}
    }
    if(ready)void poll();
    return ()=>{ctl.abort();clearTimeout(timer);};
  },[context.project_id,taskId,ready,version,historyCount,focusId]);
  async function perform(action:()=>Promise<string>){
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');setNotice('');
    try{const msg=await action();if(alive.current){setNotice(msg);refresh(n=>n+1);}}
    catch(e){if(alive.current){setError(reason(e));if(e instanceof CoreRequestError&&e.unknown)setUnknown(e.commandId||'');}}
    finally{lock.current=false;if(alive.current)setBusy(false);}
  }
  function submit(input:DocumentInput){
    void perform(async()=>{
      if(context.task_id&&!binding)throw Error('当前任务没有可绑定的执行尝试。');
      const {receipt}=await core.command('tool.authorize',{project_id:context.project_id,tool_version:'document/1',
        input_files:input.files,parameters:input.parameters,confirm:true,
        ...(binding?{task_id:binding.task_id,attempt_id:binding.attempt_id,spec_hash:binding.spec_hash}:{})});
      if(!receipt?.authorization_id)throw Error('授权回执缺少身份，请核对原操作。');
      if(!alive.current)return '';
      const run=await core.command('tool.run',{authorization_id:receipt.authorization_id});
      if(!run.receipt?.invocation_id)throw Error('运行回执缺少作业身份，请核对记录。');
      if(alive.current)setFocusId(run.receipt.invocation_id);
      return '已登记文档作业 '+run.receipt.invocation_id;
    });
  }
  const blocked=busy||!!unknown||!ready;
  const list=focusJob?[focusJob,...jobs.filter(j=>j.id!==focusJob.id)]:jobs;
  return <section className="dt-panel">
    <header><h2>文档工具</h2><button onClick={onClose}>关闭</button></header>
    <p className="dt-root">{context.project_root||'项目 '+context.project_id}</p>
    <label>关联任务<select disabled={blocked} value={attempt} onChange={e=>{setAttempt(e.target.value);setFocusId('');setFocusJob(null);setJobs([]);setDecision(null);setPreview(null);setTemplate(null);}}>
      {!context.task_id&&<option value="">仅关联当前项目</option>}
      {context.task_id&&!contexts.length&&<option value="">当前任务暂无可绑定的尝试</option>}
      {contexts.map(c=><option key={c.attempt_id} value={c.attempt_id}>{c.title||c.task_id} · {c.attempt_id.slice(0,8)}</option>)}</select></label>
    <div ref={formArea}><DocumentForm key={template?template.ref.source+':'+template.ref.entry+':'+template.ref.sha256:'manual'} template={template} clearTemplate={()=>setTemplate(null)} disabled={blocked||!!context.task_id&&!binding} submit={submit} capabilities={catalog.capabilities||{}} checked={catalog.probe_state==='CHECKED'}/></div>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {unknown&&<p>待核对操作 <code>{unknown}</code> <button disabled={busy} onClick={()=>perform(async()=>{
      const r=await core.receipt(unknown);if(r.state!=='RECORDED')return '原操作仍待核对。';
      if(alive.current)setUnknown(core.uncertainCommands().find(c=>c.kind.startsWith('tool.'))?.id||'');
      return '原操作回执已取得，请核对作业记录。';
    })}>查询原回执</button></p>}
    <header><h3>文档作业与成果</h3><button disabled={!ready||busy} onClick={()=>refresh(n=>n+1)}>刷新</button></header>
    {ready&&!list.length&&<p>暂无文档作业。</p>}
    {list.map(job=><article key={job.id}><header><strong>{stateNames[job.state]||job.state}</strong>
      {active(job)&&job.state!=='STOP_REQUESTED'&&<button disabled={blocked} onClick={()=>perform(async()=>{await core.command('tool.cancel',{invocation_id:job.id});return '已登记停止请求。';})}>停止作业</button>}</header>
      {job.error&&<p role="alert">{job.error}</p>}{job.summary&&<p>{job.summary}</p>}
      <details><summary>授权与执行记录</summary><dl>
        {[['作业',job.id],['授权',job.authorization_id],['项目',job.project_id],['任务',job.task_id],['执行尝试',job.attempt_id]].map(([k,v])=>v&&<div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        {!!job.input_versions&&<pre>{JSON.stringify(job.input_versions,null,2)}</pre>}</details>
      {Array.isArray(job.checks)&&job.checks.length>0&&<details open><summary>版本检查</summary><ul>{job.checks.map((check,i)=><li key={i}>
        {check.detail||check.kind} · {({PASSED:'通过',FAILED:'未通过',PENDING:'待检查',SKIPPED:'已跳过'} as Record<string,string>)[check.state]||check.state}
      </li>)}</ul></details>}
      {job.artifacts?.map((file,index)=><div className="dt-file" key={file.path}><strong>{baseName(file.path)}</strong>
        <small>{file.bytes.toLocaleString()} 字节 · SHA-256 {file.sha256.slice(0,12)}</small>
        <div className="dt-actions"><a href={artifactUrl(job.id,file)} download>下载</a>
        {/^(text\/|application\/(pdf|json)|image\/(png|jpeg))/.test(file.mime)&&<button onClick={()=>setPreview({job:job.id,file})}>预览</button>}
        {templateFormat(file.path)&&<button disabled={blocked} onClick={()=>{
          const format=templateFormat(file.path);if(!format)return;
          setTemplate({name:baseName(file.path),format,ref:{kind:'job',source:job.id,entry:String(index),sha256:file.sha256}});
          setError('');setNotice('');formArea.current?.scrollIntoView({block:'start'});
        }}>以此版本继续修改</button>}
        {(['accept','revise'] as const).map(value=><button key={value} disabled={blocked} onClick={()=>{setNote('');setDecision({job:job.id,file,value});}}>{value==='accept'?'验收':'退回修改'}</button>)}</div>
        {job.decisions?.filter(d=>d.path===file.path&&d.sha256===file.sha256).map((d,i)=><p key={i}>{d.decision==='accept'?'已验收':'已退回'}{d.note&&' · '+d.note}</p>)}
        {preview?.job===job.id&&preview.file.path===file.path&&<ArtifactPreview key={file.sha256} job={job.id} file={file} onClose={()=>setPreview(null)}/>}
        {decision?.job===job.id&&decision.file.path===file.path&&<form onSubmit={e=>{e.preventDefault();void perform(async()=>{
          await core.command('tool.decide',{invocation_id:job.id,path:file.path,sha256:decision.file.sha256,decision:decision.value,note,confirm:true});
          if(alive.current)setDecision(null);return '已记录该版本的验收意见。';
        });}}><label>版本意见<input value={note} maxLength={500} onChange={e=>setNote(e.target.value)}/></label>
          <button disabled={blocked}>{decision.value==='accept'?'确认验收此版本':'确认退回此版本'}</button> <button type="button" onClick={()=>setDecision(null)}>取消</button></form>}
      </div>)}</article>)}
    {more&&<button onClick={()=>showMore(n=>n+20)}>更多作业</button>}
  </section>;
}
