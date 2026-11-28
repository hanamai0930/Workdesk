import { useEffect,useRef,useState } from 'react';
import { Button } from '../components/ui/button';
import { coreClient } from '../core/client';
import './stage4-dialog.css';

type Defaults={project?:string;goal?:string;provider?:string;connection?:string;files?:string[];access?:string;selection?:{id:string;revision:string}};
type Connection={id:string;partner_id:string;name:string;model?:string;revision:number;auto_goal_supported:boolean};
type Goal={id:string;title:string;revision:string;criteria:string;max_rounds:number;task_id?:string;state:string;reason?:string;blocker?:string;file_selection?:{id:string};source_draft_id?:string};
type Catalog={projects:{id:string;name:string}[];partner_connections:Connection[];providers:{id:string;name:string;legacy_auto_goal_supported:boolean}[];goals:Goal[]};
export function WorkGoalButton({taskId,defaults={}}:{taskId?:string;defaults?:Defaults}){
 const dialog=useRef<HTMLDialogElement>(null),lock=useRef(false);
 const [catalog,setCatalog]=useState<Catalog|null>(null),[detail,setDetail]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState('');
 const [initial,setInitial]=useState<Defaults>({});
 const [saved,setSaved]=useState<Goal|null>(null);
 const draftId=location.hash.startsWith('#work/draft/')?location.hash.split('/')[2]:'';
 const owner=taskId||(draftId?'draft:'+draftId:undefined);
 const pendingGoal=()=>coreClient.uncertainCommands(owner).find(c=>['planning.goal-save','planning.state','project.scratch'].includes(c.kind))?.id||'';
 useEffect(()=>{setSaved(null);setError('');dialog.current?.close();},[owner]);
 function savedGoal(goal:Goal){setSaved(goal);dialog.current?.close();}
 async function open(){
   setCatalog(null);setError('');setDetail(null);setInitial(structuredClone(defaults));dialog.current?.showModal();
   try{await coreClient.loadIdentity();setPending(pendingGoal());
    const [data,d]=await Promise.all([coreClient.request<Catalog>('v1/planning'),taskId?coreClient.request('v1/task?id='+encodeURIComponent(taskId)):null]);
    if(!dialog.current?.open)return;
    if(d){const t=d.task;setInitial({project:t.project_id,goal:t.spec.goal,provider:t.spec.provider,connection:t.spec.partner_connection?.id,access:t.spec.access,files:Object.keys(t.spec.materials||{})});setDetail(d);}
    setCatalog(data);
   }catch(e){setError(String(e));}
 }
 async function save(form:HTMLFormElement){
   if(!catalog||lock.current||pending)return;
   lock.current=true;setBusy(true);setError('');
   try{
    const f=new FormData(form),project=(taskId||initial.files?.length||initial.selection?initial.project:String(f.get('project')||''))||(await coreClient.command('project.scratch',{},undefined,owner)).receipt.project_id;
    const connId=taskId?(initial.connection||'legacy:'+initial.provider):String(f.get('connection'));
    const conn=catalog.partner_connections.find(c=>c.id===connId);
    const provider=conn?.partner_id||(connId.startsWith('legacy:')?connId.slice(7):'');
    if(!provider)throw Error('请选择可持续运行的工作端。');
    if(taskId&&!detail?.attempts?.length)throw Error('原任务尚无可接续的回合。');
    const deadline=String(f.get('deadline')||'');
    const goal=String(f.get('goal')||'').trim();
    const payload={title:String(f.get('title')||goal).trim().slice(0,160),goal,criteria:f.get('criteria'),project_id:project,provider,
      access:initial.access||'workspace',max_rounds:Number(f.get('limit')),confirm:true,
      ...(conn?{connection_id:conn.id,connection_revision:conn.revision}:{}),
      ...(deadline?{dispatch_before:new Date(deadline).getTime()/1000}:{}),
      ...(draftId&&!taskId?{source_draft_id:draftId}:{}),
      ...(taskId?{source_task:{task_id:taskId,attempt_id:detail.attempts.at(-1).id,spec_hash:detail.task.spec_hash}}:
       initial.files?.length||initial.selection?{files:initial.files||[],...(initial.selection?{selection_ref:initial.selection}:{})}:{})};
    const r=await coreClient.command('planning.goal-save',payload,undefined,owner);
    if(dialog.current?.open)savedGoal(r.receipt.job);
   }catch(e){setError(String(e));setPending(pendingGoal());}
   finally{lock.current=false;setBusy(false);}
 }
 async function changeState(goal:Goal){
   if(lock.current||pendingGoal())return;
   const start=goal.state==='PAUSED';lock.current=true;setBusy(true);setError('');
   try{const r=await coreClient.command('planning.state',{kind:'goal_job',id:goal.id,revision:goal.revision,state:start?'RUNNING':'PAUSED',confirm:start},undefined,owner);setSaved(r.receipt.job);}
   catch(e){setError(String(e));setPending(pendingGoal());}
   finally{lock.current=false;setBusy(false);}
 }
 const existing=catalog?.goals.filter(g=>taskId?g.task_id===taskId:!!draftId&&g.source_draft_id===draftId);
 const selectedConnection=initial.connection||(initial.provider?'legacy:'+initial.provider:'');
 const knownConnection=catalog?.partner_connections.some(c=>c.id===selectedConnection&&c.auto_goal_supported)||catalog?.providers.some(p=>'legacy:'+p.id===selectedConnection&&p.legacy_auto_goal_supported);
 return <><Button type="button" variant="outline" size="sm" onClick={open}>按完成条件继续</Button>
 {saved&&<div className="wd-goal-status" role="status"><strong>{saved.title}</strong> · {saved.state==='PAUSED'?'已暂停':saved.state==='RUNNING'?'已启用':'状态已更新'}
   <p>{saved.criteria} · 最多 {saved.max_rounds} 回合</p>
   {(saved.blocker||saved.reason)&&<p>{saved.blocker||saved.reason}</p>}
   {['PAUSED','RUNNING'].includes(saved.state)&&<Button type="button" size="sm" variant="outline" disabled={busy||!!pending} onClick={()=>void changeState(saved)}>{saved.state==='PAUSED'?'确认启用自动运行':'暂停后续运行'}</Button>}
   {' '}<a href={'#planning/goal/'+saved.id}>目标详情</a>
   {error&&<p role="alert">{error}</p>}</div>}
 <dialog ref={dialog} className="wd-stage4-dialog" aria-label="创建持续目标"><h2>创建持续目标</h2>
 {error&&<p role="alert">{error}</p>}
 {existing?.map(g=><Button type="button" key={g.id} variant="outline" onClick={()=>savedGoal(g)}>{g.title} · 查看已有目标</Button>)}
 {catalog?<form onSubmit={e=>{e.preventDefault();void save(e.currentTarget);}}>
 <label>完成标准<textarea name="criteria" required maxLength={8000} placeholder="做到什么就可以停下来？"/></label>
 <label>最多后续回合<input name="limit" type="number" required min={1} max={1000} defaultValue={3}/></label>
 <details open={!initial.goal||!knownConnection}><summary>工作目标与设置</summary>
 <label>工作目标<textarea name="goal" required maxLength={12000} defaultValue={initial.goal}/></label>
 <label>目标名称<input name="title" maxLength={160} defaultValue={(detail?.task?.title||initial.goal||'').slice(0,100)}/></label>
 <label>目标项目<select name="project" disabled={!!taskId||!!initial.files?.length||!!initial.selection} defaultValue={initial.project||''}><option value="">项目外工作目录</option>{catalog.projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
 <label>官方工作端连接<select name="connection" required disabled={!!taskId} defaultValue={initial.connection||(initial.provider?'legacy:'+initial.provider:'')}><option value="">请选择连接</option>
 {catalog.partner_connections.filter(c=>c.auto_goal_supported||c.id===initial.connection).map(c=><option key={c.id} disabled={!c.auto_goal_supported} value={c.id}>{c.name} · {c.model}</option>)}
 {catalog.providers.filter(p=>p.legacy_auto_goal_supported).map(p=><option key={p.id} value={'legacy:'+p.id}>{p.name} · 原生旧配置</option>)}</select></label>
 {initial.files?.length?<details><summary>沿用的材料（{initial.files.length}）</summary>{initial.files.map(file=><div key={file}>{file}</div>)}</details>:null}
 <label>最晚派发时间（可选）<input name="deadline" type="datetime-local"/></label>
 </details><p>目标保存后暂停。确认启用后，将按所选工作端及其计费方式继续。</p>
 <Button disabled={busy||!!pending} type="submit">保存持续目标</Button></form>:!error&&<p role="status">正在核对工作端与原任务…</p>}
 {pending&&<Button type="button" onClick={async()=>{try{const r=await coreClient.receipt(pending);if(r.state==='RECORDED'){setPending('');if(r.receipt?.job?.id)savedGoal(r.receipt.job);}else setError('原操作的结果暂时无法确认，请稍后再核对。');}catch(e){setError(String(e));}}}>核对原目标命令</Button>}
 <Button type="button" variant="outline" onClick={()=>dialog.current?.close()}>关闭</Button></dialog></>;
}
