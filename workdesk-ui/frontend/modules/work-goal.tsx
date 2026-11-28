import { useRef,useState } from 'react';
import { Button } from '../components/ui/button';
import { coreClient } from '../core/client';
import './stage4-dialog.css';

type Defaults={project?:string;goal?:string;provider?:string;connection?:string;files?:string[];access?:string;selection?:{id:string;revision:string}};
type Connection={id:string;partner_id:string;name:string;model?:string;revision:number;auto_goal_supported:boolean};
type Catalog={projects:{id:string;name:string}[];partner_connections:Connection[];providers:{id:string;name:string;legacy_auto_goal_supported:boolean}[];goals:{id:string;title:string;task_id?:string;state:string}[]};
export function WorkGoalButton({taskId,defaults={}}:{taskId?:string;defaults?:Defaults}){
 const dialog=useRef<HTMLDialogElement>(null),lock=useRef(false);
 const [catalog,setCatalog]=useState<Catalog|null>(null),[detail,setDetail]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState('');
 const [initial,setInitial]=useState<Defaults>({});
 async function open(){
   setCatalog(null);setError('');setDetail(null);setInitial(structuredClone(defaults));dialog.current?.showModal();
   try{await coreClient.loadIdentity();setPending(coreClient.uncertainCommands().find(c=>c.kind==='planning.goal-save')?.id||'');
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
    const f=new FormData(form),project=taskId||initial.files?.length?initial.project:String(f.get('project')||'');
    const connId=taskId?(initial.connection||'legacy:'+initial.provider):String(f.get('connection'));
    const conn=catalog.partner_connections.find(c=>c.id===connId);
    const provider=conn?.partner_id||(connId.startsWith('legacy:')?connId.slice(7):'');
    if(!provider)throw Error('请选择可持续运行的工作端。');
    if(taskId&&!detail?.attempts?.length)throw Error('原任务尚无可接续的回合。');
    const deadline=String(f.get('deadline')||'');
    const payload={title:f.get('title'),goal:f.get('goal'),criteria:f.get('criteria'),project_id:project,provider,
      access:initial.access||'workspace',max_rounds:Number(f.get('limit')),confirm:true,
      ...(conn?{connection_id:conn.id,connection_revision:conn.revision}:{}),
      ...(deadline?{dispatch_before:new Date(deadline).getTime()/1000}:{}),
      ...(taskId?{source_task:{task_id:taskId,attempt_id:detail.attempts.at(-1).id,spec_hash:detail.task.spec_hash}}:
       initial.files?.length?{files:initial.files,...(initial.selection?{selection_ref:initial.selection}:{})}:{})};
    const r=await coreClient.command('planning.goal-save',payload);
    if(dialog.current?.open){dialog.current.close();location.hash='#planning/goal/'+r.receipt.job.id;}
   }catch(e){setError(String(e));setPending(coreClient.uncertainCommands().find(c=>c.kind==='planning.goal-save')?.id||'');}
   finally{lock.current=false;setBusy(false);}
 }
 const existing=catalog?.goals.filter(g=>taskId&&g.task_id===taskId);
 return <><Button type="button" variant="outline" size="sm" onClick={open}>按完成条件继续</Button>
 <dialog ref={dialog} className="wd-stage4-dialog" aria-label="创建持续目标"><h2>创建持续目标</h2>
 {error&&<p role="alert">{error}</p>}
 {existing?.map(g=><a key={g.id} href={'#planning/goal/'+g.id} onClick={()=>dialog.current?.close()}>{g.title} · 查看原目标</a>)}
 {catalog?<form onSubmit={e=>{e.preventDefault();void save(e.currentTarget);}}>
 <label>目标名称<input name="title" required maxLength={160} defaultValue={(detail?.task?.title||initial.goal||'').slice(0,100)}/></label>
 <label>目标项目<select name="project" required disabled={!!taskId||!!initial.files?.length} defaultValue={initial.project||''}><option value="">请选择项目</option>{catalog.projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
 <label>官方工作端连接<select name="connection" required disabled={!!taskId} defaultValue={initial.connection||(initial.provider?'legacy:'+initial.provider:'')}><option value="">请选择连接</option>
 {catalog.partner_connections.filter(c=>c.auto_goal_supported||c.id===initial.connection).map(c=><option key={c.id} disabled={!c.auto_goal_supported} value={c.id}>{c.name} · {c.model}</option>)}
 {catalog.providers.filter(p=>p.legacy_auto_goal_supported).map(p=><option key={p.id} value={'legacy:'+p.id}>{p.name} · 原生旧配置</option>)}</select></label>
 {initial.files?.length?<details><summary>沿用的材料（{initial.files.length}）</summary>{initial.files.map(file=><div key={file}>{file}</div>)}</details>:null}
 <label>工作目标<textarea name="goal" required maxLength={12000} defaultValue={initial.goal}/></label>
 <label>完成标准<textarea name="criteria" required maxLength={8000}/></label>
 <label>最多后续回合<input name="limit" type="number" required min={1} max={1000} defaultValue={3}/></label>
 <label>最晚派发时间（可选）<input name="deadline" type="datetime-local"/></label>
 <p>保存后为暂停状态，从计划页启动。使用原工作端和材料，失败或结果未知时暂停。</p>
 <Button disabled={busy||!!pending} type="submit">保存持续目标</Button></form>:!error&&<p role="status">正在核对工作端与原任务…</p>}
 {pending&&<Button type="button" onClick={async()=>{try{const r=await coreClient.receipt(pending);if(r.state==='RECORDED'){setPending('');if(r.receipt?.job?.id){dialog.current?.close();location.hash='#planning/goal/'+r.receipt.job.id;}}else setError('原命令仍待核对。');}catch(e){setError(String(e));}}}>核对原目标命令</Button>}
 <Button type="button" variant="outline" onClick={()=>dialog.current?.close()}>关闭</Button></dialog></>;
}
