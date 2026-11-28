import {useCallback, useEffect, useRef, useState} from 'react';
import {coreClient as core, CoreRequestError} from './client';
import './document-tools.css';

type Context={project_id:string;project_root?:string;round_id?:string};
type Parent={task_id:string;attempt_id:string;spec_hash:string;title:string};
type Schedule={schedule_id:string;revision:number;state:string;authorization:{task_id?:string;parameters:{action:string;path?:string}};
  timezone:string;repeat:string;next_at?:number;round_count:number;max_runs:number;reason?:string};
type Round={round_id:string;schedule_id:string;job_id?:string;state:string;command_id?:string;reason?:string;at:number};
type Index={schedules:Schedule[];rounds:Round[];actions:string[]};
type File={path:string;bytes:number;sha256:string};
type Job={state:string;summary?:string;error?:string;artifacts?:File[];authorization_id?:string;task_id?:string};
const label:Record<string,string>={ACTIVE:'已启用',PAUSED:'已暂停',CANCELLED:'已取消',COMPLETE:'已结束',COMPLETED:'已完成',
  DISPATCHED:'已派发',QUEUED:'排队中',RUNNING:'运行中',FAILED:'执行失败',MISSED:'已错过',BLOCKED_SCOPE:'授权范围需核对',BLOCKED_BUDGET:'预算不足',BLOCKED:'派发受阻',UNKNOWN:'待核对',
  ORPHANED_UNKNOWN:'重启后待核对',STOP_UNKNOWN:'停止待核对',STOP_REQUESTED:'正在停止'};
const actions:Record<string,string>={'file.read':'读取项目文件','env.inspect':'检查开发环境'};
const frequencies:Record<string,string>={once:'一次',daily:'每天',weekly:'每周'};
const message=(e:unknown)=>e instanceof Error?e.message:String(e);
const pending=()=>core.uncertainCommands().find(c=>c.kind.startsWith('schedule.tool-'))?.id||'';

export default function ToolSchedules({context,onClose}:{context:Context;onClose:()=>void}){
  const [index,setIndex]=useState<Index>({schedules:[],rounds:[],actions:[]});
  const [parents,setParents]=useState<Parent[]>([]),[parentId,setParentId]=useState('');
  const [action,setAction]=useState('file.read'),[path,setPath]=useState(''),[local,setLocal]=useState('');
  const [zone,setZone]=useState(()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai');
  const [frequency,setFrequency]=useState('once'),[count,setCount]=useState(3);
  const [busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(''),[error,setError]=useState('');
  const [selected,setSelected]=useState<{id:string;job:Job}|null>(null);
  const route='v2/tool-schedules?'+new URLSearchParams({project_id:context.project_id});
  const refresh=useCallback(async(signal?:AbortSignal)=>{
    const data=await core.request<Index>(route,{signal});
    if(context.round_id&&!data.rounds.some(r=>r.round_id===context.round_id)){
      const original=await core.request<Index>(route+'&round_id='+encodeURIComponent(context.round_id),{signal});
      data.rounds=[...original.rounds,...data.rounds];
      data.schedules.push(...original.schedules.filter(s=>!data.schedules.some(row=>row.schedule_id===s.schedule_id)));
    }
    if(!signal?.aborted)setIndex(data);
  },[route,context.round_id]);
  useEffect(()=>{
    const abort=new AbortController();
    (async()=>{
      await core.loadIdentity(abort.signal);
      const [,bindings]=await Promise.all([refresh(abort.signal),core.request<{contexts:Parent[]}>('v2/tool-contexts?'+
        new URLSearchParams({project_id:context.project_id}),{signal:abort.signal})]);
      if(!abort.signal.aborted){setParents(bindings.contexts);setUncertain(pending());}
    })().catch(e=>{if(!abort.signal.aborted)setError(message(e));});
    return ()=>abort.abort();
  },[context.project_id,refresh]);
  useEffect(()=>{
    if(!index.schedules.some(s=>s.state==='ACTIVE')&&!index.rounds.some(r=>['RUNNING','QUEUED','STOP_REQUESTED'].includes(r.state)))return;
    const abort=new AbortController();
    const tick=setTimeout(()=>refresh(abort.signal).catch(e=>{if(!abort.signal.aborted)setError(message(e));}),2500);
    return ()=>{clearTimeout(tick);abort.abort();};
  },[index,refresh]);
  async function change(kind:string,body:Record<string,unknown>){
    if(busy||uncertain)return;
    setBusy(true);setError('');
    try{await core.command(kind,body);await refresh();}
    catch(e){setError(message(e));if(e instanceof CoreRequestError&&e.unknown)setUncertain(e.commandId||pending());}
    finally{setBusy(false);}
  }
  async function recover(){
    try{const entry=await core.receipt(uncertain);if(entry.state!=='RECORDED')throw Error('尚未查到此命令回执。');
      setUncertain(pending());setError('');await refresh();}catch(e){setError(message(e));}
  }
  async function inspect(id:string){
    try{setError('');setSelected({id,job:await core.request<Job>('v2/jobs/'+encodeURIComponent(id))});}
    catch(e){setError(message(e));}
  }
  const focused=useRef('');
  useEffect(()=>{
    if(!context.round_id||focused.current===context.round_id)return;
    const round=index.rounds.find(r=>r.round_id===context.round_id);
    if(!round)return;
    focused.current=round.round_id;
    document.getElementById('tool-round-'+round.round_id)?.focus();
    if(round.job_id)void inspect(round.job_id);
  },[context.round_id,index]);
  const disabled=busy||!!uncertain;
  const parent=parents.find(p=>p.attempt_id===parentId);
  function save(){
    const parameters=action==='file.read'?{action,path}:{action};
    return change('schedule.tool-create',{project_id:context.project_id,tool_version:'computer/1',parameters,
      first_local:local,timezone:zone,repeat:frequency,max_runs:frequency==='once'?1:count,confirm:true,
      ...(parent?{task_id:parent.task_id,attempt_id:parent.attempt_id,spec_hash:parent.spec_hash}:{})});
  }
  return <section className="dt-panel"><header><h2>项目工具预约</h2><button onClick={onClose}>关闭</button></header>
    <p className="dt-root">{context.project_root||context.project_id}</p>
    <form onSubmit={e=>{e.preventDefault();void save();}}><fieldset disabled={disabled||!index.actions.length}>
      <label>关联任务<select value={parentId} onChange={e=>setParentId(e.target.value)}><option value="">当前项目</option>
        {parents.map(p=><option key={p.attempt_id} value={p.attempt_id}>{p.title||p.task_id} · {p.attempt_id.slice(0,8)}</option>)}</select></label>
      <label>预约操作<select value={action} onChange={e=>setAction(e.target.value)}>{index.actions.filter(a=>actions[a]).map(a=>
        <option key={a} value={a}>{actions[a]}</option>)}</select></label>
      {action==='file.read'&&<label>项目内文件路径<input required value={path} onChange={e=>setPath(e.target.value)}/></label>}
      <label>首次执行时间<input type="datetime-local" required value={local} onChange={e=>setLocal(e.target.value)}/></label>
      <label>执行时区<input required value={zone} onChange={e=>setZone(e.target.value)}/></label>
      <label>重复方式<select value={frequency} onChange={e=>setFrequency(e.target.value)}>{Object.entries(frequencies).map(([k,v])=>
        <option key={k} value={k}>{v}</option>)}</select></label>
      {frequency!=='once'&&<label>最多执行次数<input type="number" min={1} max={366} value={count} required onChange={e=>setCount(Number(e.target.value))}/></label>}
      <p>保存后，开发服务会按所选时区自动执行已授权的操作。文件版本变化时需要重新核对。</p>
      <button type="submit">授权并保存预约</button>
    </fieldset></form>
    {error&&<p role="alert">{error}</p>}
    {uncertain&&<p>待核对命令 {uncertain} <button onClick={()=>void recover()}>查原回执</button></p>}
    <header><h3>已保存的预约</h3><button onClick={()=>refresh().catch(e=>setError(message(e)))}>刷新</button></header>
    {index.schedules.map(s=><article key={s.schedule_id}><strong>{actions[s.authorization.parameters.action]||s.authorization.parameters.action} · {label[s.state]||s.state}</strong>
      <p>{s.authorization.parameters.path} {frequencies[s.repeat]} · {s.timezone} · 已登记 {s.round_count} / {s.max_runs} 轮{s.next_at?' · 下次 '+new Date(s.next_at*1000).toLocaleString(undefined,{timeZone:s.timezone}):''}</p>
      {s.reason&&<p>{s.reason}</p>}
      <div>{['ACTIVE','PAUSED'].includes(s.state)&&[s.state==='ACTIVE'?'PAUSED':'ACTIVE','CANCELLED'].map(v=><button key={v} disabled={disabled}
        onClick={()=>void change('schedule.tool-state',{schedule_id:s.schedule_id,revision:s.revision,state:v,confirm:true})}>
        {v==='PAUSED'?'暂停':v==='ACTIVE'?'继续':'取消后续轮次'}</button>)}</div>
      <details><summary>计划记录</summary><p>{s.schedule_id} · 修订 {s.revision}</p>{s.authorization.task_id&&<p>原任务 {s.authorization.task_id}</p>}</details>
    </article>)}
    {!index.schedules.length&&<p>暂无工具预约。</p>}
    <h3>执行轮次</h3>{index.rounds.map(r=><article key={r.round_id} id={'tool-round-'+r.round_id} tabIndex={-1}><strong>{label[r.state]||r.state}</strong>
      <p>{new Date(r.at*1000).toLocaleString()} {r.reason}</p><details><summary>轮次与命令</summary><p>{r.round_id}</p><p>{r.command_id}</p></details>
      {r.job_id&&<button onClick={()=>void inspect(r.job_id!)}>查看原作业</button>}
    </article>)}
    {selected&&<article><h4>原作业 · {label[selected.job.state]||selected.job.state}</h4><p>{selected.job.summary||selected.job.error}</p>
      <p>作业 {selected.id}</p><p>授权 {selected.job.authorization_id}</p>
      {selected.job.artifacts?.map(f=><a key={f.path} download href={'api/v2/jobs/'+encodeURIComponent(selected.id)+'/artifact?'+new URLSearchParams({path:f.path,download:'1'})}>
        {f.path.split(/[\\/]/).pop()} · {f.bytes} 字节</a>)}</article>}
  </section>;
}
