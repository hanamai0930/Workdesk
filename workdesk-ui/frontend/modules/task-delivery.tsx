import {useEffect,useRef,useState} from 'react';
import {coreClient as core} from '../core/client';
import {partnerStateLabel,type PartnerRun} from '../core/partner-runtime';
import {WorkGoalButton} from './work-goal';
import {ArtifactPreview} from '../core/document-tools';

type Version={path:string;relative_path?:string;sha256:string;bytes:number;mime:string};
type Decision={decision:string;relative_path?:string;path:string;sha256:string;command_id:string;note:string};
type Job={id:string;attempt_id:string;state:string;authorization_id:string;command_id:string;artifacts:Version[];decisions?:Decision[];checks?:{kind:string;state:string;detail:string}[];error?:string};
type ToolRequest={id:string;state:string;authorization_id:string;command_id:string;invocation_id?:string;attempt_id:string;run_id:string;spec_hash:string;project_id:string;task_id:string;input_files:unknown[];parameters:Record<string,unknown>;error?:string};
type Delivery={state:string;completion?:{criteria:string;command_id:string};guidance?:{command_id:string;note:string}};
type Task={id:string;project_id:string;spec_hash:string;title:string;spec:{goal:string;materials?:Record<string,unknown>;acceptance?:string;work_mode?:string}};
type Attempt={id:string;state:string;owner:string;session_id?:string;result?:{partner_run_id?:string}};
type TaskEvent={kind:string;attempt_id:string;payload:{reason?:string;result_id?:string;chat_source?:{chat_id:string;command_id:string}}};
export type TaskDetail={task:Task;attempts:Attempt[];events?:TaskEvent[]};
const enc=encodeURIComponent;
const basename=(name:string)=>name.split(/[\\/]/).pop();
const versionRef=(job:Job,f:Version,index:number)=>({kind:'job',source:job.id,entry:String(index),sha256:f.sha256});

export function TaskDelivery({detail,run}:{detail:TaskDetail;run:PartnerRun}){
  const {task,attempts}=detail,attempt=attempts.at(-1)!;
  const [jobs,setJobs]=useState<Job[]>([]),[requests,setRequests]=useState<ToolRequest[]>([]),[info,setInfo]=useState<Delivery|null>(null);
  const [refresh,setRefresh]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[loadError,setLoadError]=useState(''),[pending,setPending]=useState(core.uncertainCommands());
  const [note,setNote]=useState(''),[criteria,setCriteria]=useState(task.spec.acceptance||task.spec.goal);
  const [preview,setPreview]=useState<{job:string;file:Version}|null>(null),[compare,setCompare]=useState('');
  const [decision,setDecision]=useState<{job:Job;file:Version;kind:'accept'|'revise'|'complete'}|null>(null),[opinion,setOpinion]=useState('');
  const [limit,setLimit]=useState(20),[more,setMore]=useState(false);
  const alive=useRef(true),lock=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const turnEnded=attempt?.state==='COMPLETED'&&run.state==='COMPLETED'&&run.local_exit_confirmed===true;
  useEffect(()=>{
    const ctl=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const query=new URLSearchParams({task_id:task.id,project_id:task.project_id,tool_version:'document/1',limit:String(limit)});
    async function read(){
      try{
        const options={signal:ctl.signal};
        const [history,proposals,delivery]=await Promise.all([
          core.request<{jobs:Job[];has_more:boolean}>('v2/jobs?'+query,options),
          core.request<{requests:ToolRequest[]}>('v2/document-requests?task_id='+enc(task.id),options),
          core.request<Delivery>('v2/work-delivery?task_id='+enc(task.id),options),
        ]);
        if(ctl.signal.aborted)return;
        setJobs(history.jobs);setMore(history.has_more);setRequests(proposals.requests);setInfo(delivery);setLoadError('');
      }catch(e){if(!ctl.signal.aborted)setLoadError(e instanceof Error?e.message:'任务交付记录读取失败');}
      if(!ctl.signal.aborted&&!turnEnded)timer=setTimeout(read,1800);
    }
    void read();return()=>{ctl.abort();clearTimeout(timer);};
  },[task.id,task.project_id,refresh,turnEnded,limit]);
  async function act(fn:()=>Promise<void>,recovery=false){
    if(lock.current||(!recovery&&core.uncertainCommands().length))return;lock.current=true;setBusy(true);setError('');
    try{await fn();}catch(e){if(alive.current)setError(e instanceof Error?e.message:'操作失败，请核对原命令');}
    finally{lock.current=false;if(alive.current){setPending(core.uncertainCommands());setBusy(false);setRefresh(x=>x+1);}}
  }
  const blocked=busy||pending.length>0||!!loadError||!info;
  const scope={task_id:task.id,attempt_id:attempt?.id,spec_hash:task.spec_hash,run_id:run.id};
  function continueTurn(goal:string,extras:Record<string,unknown>={}){
    void act(async()=>{await core.command('independent.send',{task_id:task.id,attempt_id:attempt.id,owner:attempt.owner,goal,...extras});if(alive.current)location.reload();});
  }
  async function approve(r:ToolRequest){
    if(r.state==='PENDING')await core.command('tool.authorize',{request_id:r.id,project_id:r.project_id,task_id:r.task_id,attempt_id:r.attempt_id,spec_hash:r.spec_hash,input_files:r.input_files,parameters:r.parameters,tool_version:'document/1',confirm:true},r.authorization_id);
    await core.command('tool.run',{authorization_id:r.authorization_id},r.command_id);
  }
  const stateName=info?.state==='COMPLETED'?'任务已完成':info?.state==='REVISION_REQUIRED'?'等待修订':'待核定';
  const guidance=info?.guidance;
  return <section className="wd-partner-work-history wd-task-delivery" aria-label="单任务交付">
    <h2>任务交付</h2><p>{task.spec.goal}</p>
    <p>本回合 · {turnEnded?'回合已结束':partnerStateLabel(attempt?.state||run.state)}　任务 · {stateName}</p>
    {info?.completion&&<p>完成依据 · {info.completion.criteria}</p>}
    <details><summary>任务、材料与历次尝试</summary><p>项目 {task.project_id} · 任务 {task.id} · 规格 {task.spec_hash}</p>
      <pre>{JSON.stringify(task.spec.materials||{},null,2)}</pre>
      {detail.events?.filter(e=>e.kind==='file_selection_delivery_verified'&&e.payload.chat_source?.chat_id).map(e=><p key={e.payload.chat_source!.command_id}><a href={'#chat/'+enc(e.payload.chat_source!.chat_id)}>返回交接材料的 Chat</a> · 交接命令 {e.payload.chat_source!.command_id}</p>)}
      {attempts.map(a=><p key={a.id}>{partnerStateLabel(a.state)} · 尝试 {a.id} · 会话 {a.session_id||'等待登记'} {a.result?.partner_run_id&&<a href={'#work/partners/run/'+enc(a.result.partner_run_id)}>查看原运行</a>}</p>)}</details>
    <WorkGoalButton taskId={task.id}/>{' '}<a href={'#results/'+enc(task.id)}>回复和项目文件成果</a>{' '}<a href={'#control/task/'+enc(task.id)}>运行与恢复</a>
    {error&&<p role="alert">{error}</p>}{loadError&&<p role="alert">{loadError}</p>}
    <button onClick={()=>setRefresh(x=>x+1)}>刷新交付记录</button>
    {pending.map(c=><button key={c.id} disabled={busy} onClick={()=>void act(async()=>{const receipt=await core.receipt(c.id);if(receipt.state!=='RECORDED')throw Error('原回执仍待核对：'+c.id);},true)}>核对原命令 {c.id}</button>)}
    {requests.map(r=><details key={r.id} open={['PENDING','AUTHORIZED'].includes(r.state)}><summary>文档请求 · {r.state}</summary>
      <p>原运行 {r.run_id} · 尝试 {r.attempt_id}</p><pre>{JSON.stringify({输入:r.input_files,操作:r.parameters},null,2)}</pre><p>请求 {r.id} · 授权 {r.authorization_id} · 执行命令 {r.command_id} · 作业 {r.invocation_id||'尚未派发'}</p>{r.error&&<p>{r.error}</p>}
      {r.attempt_id===attempt?.id&&r.run_id===run.id&&['PENDING','AUTHORIZED'].includes(r.state)&&<><button disabled={blocked} onClick={()=>void act(()=>approve(r))}>{r.state==='PENDING'?'确认本次参数并执行':'执行已授权请求'}</button>
      {r.state==='PENDING'&&<button disabled={blocked} onClick={()=>void act(async()=>{await core.command('document.reject',{request_id:r.id});})}>拒绝本次请求</button>}</>}</details>)}
    <h3>成果版本</h3>{detail.events?.filter(e=>e.kind==='work_result_captured'||e.kind==='work_result_capture_pending').map((e,i)=><p key={i}>{e.kind==='work_result_captured'?'已保存原生回复／文件快照':'原生交付物待补取'} · {e.payload.result_id||e.payload.reason} <a href={'#results/'+enc(task.id)}>查看成果与验收</a></p>)}{!jobs.length&&<p>尚无文档作业成果。</p>}
    {jobs.map(j=><article key={j.id}><details><summary>作业 {j.id} · {partnerStateLabel(j.state)}</summary><p>尝试 {j.attempt_id} · 授权 {j.authorization_id} · 执行命令 {j.command_id}</p>
      <a target="_blank" rel="noreferrer" href={'api/v2/jobs/'+enc(j.id)+'/log'}>查看作业日志</a>{j.error&&<p>{j.error}</p>}{j.checks?.map((c,i)=><p key={i}>{c.kind} · {c.state} · {c.detail}</p>)}</details>
      {['RUNNING','QUEUED'].includes(j.state)&&<button disabled={blocked} onClick={()=>void act(async()=>{await core.command('tool.cancel',{invocation_id:j.id});})}>停止此作业</button>}
      {j.artifacts?.map((f,i)=>{const versions=j.decisions?.filter(d=>(d.path===f.path||!!f.relative_path&&d.relative_path===f.relative_path)&&d.sha256===f.sha256)||[],d=versions.at(-1),v=JSON.stringify(versionRef(j,f,i));return <div key={f.path}>
        <strong>{basename(f.path)}</strong> · {d?.decision==='accept'?'已验收':d?.decision==='revise'?'已退回':'待验收'}<p>SHA-256 {f.sha256}</p>
        <a download href={'api/v2/jobs/'+enc(j.id)+'/artifact?'+new URLSearchParams({path:f.path,download:'1'})}>下载此版本</a>{' '}
        {/^(text\/|application\/(pdf|json)|image\/(png|jpeg))/.test(f.mime)&&<button onClick={()=>setPreview({job:j.id,file:f})}>预览</button>}
        <button onClick={()=>setCompare(compare===v?'':v)}>{compare===v?'取消比较起点':'作为比较起点'}</button>
        {compare&&compare!==v&&<a target="_blank" rel="noreferrer" href={'api/v2/projects/'+enc(task.project_id)+'/compare?'+new URLSearchParams({left:compare,right:v})}>比较两版</a>}
        {j.attempt_id===attempt?.id&&turnEnded&&(['complete','accept','revise'] as const).map(kind=><button key={kind} disabled={blocked} onClick={()=>{setOpinion('');setDecision({job:j,file:f,kind});}}>{{complete:'登记任务完成',accept:'验收此版',revise:'退回此版'}[kind]}</button>)}
        {versions.map(x=><p key={x.command_id}>{x.note||x.decision} · 决定 {x.command_id}</p>)}
        {turnEnded&&j.attempt_id===attempt?.id&&d?.decision==='revise'&&<button disabled={blocked} onClick={()=>continueTurn('按退修意见修订此版本。',{feedback_command_id:d.command_id})}>用此版本在原会话修订</button>}
      </div>;})}</article>)}
    {more&&limit<200&&<button onClick={()=>setLimit(x=>Math.min(x+30,200))}>更多作业</button>}
    {preview&&<ArtifactPreview job={preview.job} file={preview.file} onClose={()=>setPreview(null)}/>}
    {decision&&<form onSubmit={e=>{e.preventDefault();const {job,file,kind}=decision;void act(async()=>{
      const target={invocation_id:job.id,path:file.path,sha256:file.sha256,confirm:true};
      if(kind==='complete')await core.command('work.complete',{...scope,...target,criteria});else await core.command('tool.decide',{...target,decision:kind,note:opinion});
      if(alive.current)setDecision(null);
    });}}><p>{basename(decision.file.path)} · {decision.file.sha256}</p>{decision.kind==='complete'?<label>完成依据<input required value={criteria} onChange={e=>setCriteria(e.target.value)}/></label>:<label>版本意见<input required={decision.kind==='revise'} value={opinion} onChange={e=>setOpinion(e.target.value)}/></label>}
    <button disabled={blocked}>{{complete:'确认任务已完成',accept:'确认验收此版本',revise:'确认退回此版本'}[decision.kind]}</button><button type="button" onClick={()=>setDecision(null)}>取消</button></form>}
    <h3>补充要求</h3>{guidance?.note&&<p>已排队，待下一回合发送 · {guidance.note} <button disabled={blocked} onClick={()=>void act(async()=>{await core.command('work.guidance',{...scope,previous:guidance.command_id,note:''});})}>撤回</button></p>}
    <textarea aria-label="下一回合补充要求" value={note} onChange={e=>setNote(e.target.value)}/>
    {turnEnded&&<button disabled={blocked||!(note.trim()||guidance?.note)} onClick={()=>continueTurn(note.trim()||'请继续处理补充要求。',guidance?.note?{guidance_command_id:guidance.command_id}:{})}>在原会话发送</button>}
    <button disabled={blocked||!note.trim()} onClick={()=>void act(async()=>{await core.command('work.guidance',{...scope,previous:guidance?.command_id||'',note});if(alive.current)setNote('');})}>保存为下一回合要求</button>
  </section>;
}
