import { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { coreClient } from '../core/client';

type Notice={id:string;category:string;title:string;state:string;revision:number;at:number;reason?:string;
  href:string;source_kind:string;source_state:string;task_id?:string;attempt_id?:string;job_id?:string;round_id?:string;
  source_command_id?:string;handling_command_id?:string;handling_note?:string;due_at?:number;due?:boolean};
const labels:Record<string,string>={todo:'待办',reminder:'提醒',approval:'审批或验收',failure:'失败或阻碍',completion:'完成',review:'待核对'};
const date=(at:number)=>new Date(at*1000).toLocaleString();

export function SettingsInbox(){
  const [revision,setRevision]=useState(0),[items,setItems]=useState<Notice[]>([]);
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]);
  const [project,setProject]=useState(''),[status,setStatus]=useState('OPEN'),[limit,setLimit]=useState(25);
  const [more,setMore]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [category,setCategory]=useState('todo'),[title,setTitle]=useState(''),[due,setDue]=useState(''),[task,setTask]=useState('');
  useEffect(()=>{
    let active=true;
    async function loadProjects(){
      const all:{id:string;name:string}[]=[];let cursor='0',morePages=true;
      while(morePages){const page=await coreClient.request<{projects:typeof all;cursor:string;has_more:boolean}>('v2/projects?limit=200&cursor='+cursor);
        all.push(...page.projects);cursor=page.cursor;morePages=page.has_more;}
      if(active)setProjects(all);
    }
    loadProjects().catch(e=>{if(active)setError(String(e.message||e));});return()=>{active=false;};
  },[]);
  useEffect(()=>{
    let active=true;
    const read=async()=>{
      const rows:Notice[]=[];let cursor='0',hasMore=true;
      while(rows.length<limit&&hasMore){
        const q=new URLSearchParams({state:status,project_id:project,limit:String(Math.min(100,limit-rows.length)),cursor});
        const data=await coreClient.request<{notifications:Notice[];cursor:string;has_more:boolean}>('v2/notifications?'+q);
        rows.push(...data.notifications);cursor=data.cursor;hasMore=data.has_more;
      }
      if(active){setItems(rows);setMore(hasMore);}
    };
    const refresh=()=>read().catch(e=>{if(active)setError(String(e.message||e));});
    void refresh();const timer=window.setInterval(refresh,15000);
    return ()=>{active=false;clearInterval(timer);};
  },[revision,status,project,limit]);
  async function submit(kind:string,payload:object){
    if(busy)return;setBusy(true);setError('');
    try{await coreClient.command(kind,payload);if(kind==='notice.create'){setTitle('');setDue('');setTask('');}
      setRevision(n=>n+1);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  }
  return <section className="wd-settings-group" aria-label="任务收件箱"><h2>任务收件箱</h2>
    <div className="wd-settings-actions"><NativeSelect aria-label="收件项目" value={project} onChange={e=>{setProject(e.target.value);setLimit(25);}}>
      <option value="">全部项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</NativeSelect>
      <NativeSelect aria-label="处理状态" value={status} onChange={e=>{setStatus(e.target.value);setLimit(25);}}>
        <option value="OPEN">待处理</option><option value="HANDLED">已处理</option></NativeSelect>
      <Button variant="outline" onClick={()=>{setError('');setRevision(n=>n+1);}}>刷新收件箱</Button></div>
    {error&&<p role="alert">{error}</p>}
    <details className="wd-settings-card"><summary>新增待办或提醒</summary>
      <form onSubmit={e=>{e.preventDefault();void submit('notice.create',{category,title,project_id:project,task_id:task.trim(),
          ...(category==='reminder'?{due_at:Math.floor(new Date(due).getTime()/1000)}:{})});}}>
        <label>类型<NativeSelect value={category} onChange={e=>setCategory(e.target.value)}><option value="todo">待办</option><option value="reminder">提醒</option></NativeSelect></label>
        <label>标题<Input required maxLength={240} value={title} onChange={e=>setTitle(e.target.value)}/></label>
        <label>关联任务标识（可选）<Input value={task} onChange={e=>setTask(e.target.value)}/></label>
        {category==='reminder'&&<label>提醒时间<Input required type="datetime-local" value={due} onChange={e=>setDue(e.target.value)}/></label>}
        <Button type="submit" disabled={busy||!project}>保存到所选项目</Button></form></details>
    {!items.length&&<p className="wd-settings-empty">当前没有此范围的通知。</p>}
    {items.map(n=><article key={n.id} className="wd-settings-card" data-notice-id={n.id}>
      <div className="wd-settings-row"><div className="wd-settings-copy"><h3>{n.title}</h3>
        <p>{labels[n.category]||n.category} · {n.due_at?(n.due?'已到期 ':'预约 ')+date(n.due_at):date(n.at)} · {n.source_state}</p>
        {n.reason&&<p>{n.reason}</p>}</div>
        <div className="wd-settings-value">
          {n.href.startsWith('#')&&n.href!=='#settings/notifications'&&<a href={n.href}>{n.round_id?'打开原轮次':'打开来源'}</a>}
          {n.task_id&&n.href!=='#control/task/'+n.task_id&&<a href={'#control/task/'+encodeURIComponent(n.task_id)}>原任务</a>}
          <Button variant="outline" disabled={busy} onClick={()=>void submit('notice.state',{id:n.id,revision:n.revision,state:n.state==='OPEN'?'HANDLED':'OPEN'})}>{n.state==='OPEN'?'标为已处理':'重新打开'}</Button>
        </div></div>
      <details><summary>来源与处理回执</summary>
        {Object.entries({'轮次':n.round_id,'作业':n.job_id,'任务尝试':n.attempt_id,'原命令':n.source_command_id,'处理命令':n.handling_command_id,'处理说明':n.handling_note})
          .filter(([,v])=>v).map(([k,v])=><p key={k}>{k} <code>{v}</code></p>)}</details>
    </article>)}
    {more&&<Button variant="outline" onClick={()=>setLimit(n=>n+25)}>加载更多通知</Button>}
  </section>;
}
