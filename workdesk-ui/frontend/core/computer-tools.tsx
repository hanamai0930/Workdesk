import {useEffect,useRef,useState} from 'react';
import {coreClient as core, CoreRequestError} from './client';
import './document-tools.css';

type Context={project_id:string; project_root?:string; task_id?:string};
type Binding={task_id:string;attempt_id:string;spec_hash:string;title:string};
type Job={id:string;state:string;summary?:string;error?:string;authorization_id?:string;task_id?:string;
  action?:string;tool_version:string;artifacts?:{path:string;mime:string;sha256:string;bytes:number}[]};
type Input={key:string;name:string;multi?:boolean;optional?:boolean;placeholder?:string;type?:'lines'|'number'|'bool'|'text'};
type Action={name:string;fields:Input[]};
const pathField:Input={key:'path',name:'项目内文件路径'};
const destField:Input={key:'destination',name:'项目内新位置'};
const branchField:Input={key:'branch',name:'新分支名称'};
const site:Input[]=[{key:'origin',name:'授权站点',placeholder:'https://example.org'},{key:'url',name:'页面地址'}];
const element:Input={key:'element_id',name:'页面元素 ID'};
const actions:Record<string,Action>={
  'file.list':{name:'列出目录',fields:[{...pathField,name:'项目内目录',optional:true}]},
  'file.read':{name:'查看文件',fields:[pathField]},
  'file.edit':{name:'新建或替换文件内容',fields:[pathField,{key:'content',name:'新内容',multi:true}]},
  'file.copy':{name:'复制到新文件',fields:[pathField,destField]},'file.move':{name:'移到新位置',fields:[pathField,destField]},
  'git.status':{name:'Git 工作区状态',fields:[]},'git.history':{name:'查看提交历史',fields:[]},
  'git.branches':{name:'列出分支',fields:[]},'git.worktrees':{name:'列出工作副本',fields:[]},
  'git.diff':{name:'查看文件差异',fields:[pathField]},'git.stage':{name:'暂存文件',fields:[pathField]},
  'git.commit':{name:'提交指定文件',fields:[pathField,{key:'message',name:'提交说明'}]},
  'git.restore':{name:'从 Git 恢复指定文件',fields:[pathField]},'git.branch':{name:'建立分支',fields:[branchField]},
  'git.worktree':{name:'建立工作副本',fields:[destField,branchField]},
  'env.inspect':{name:'查看开发环境',fields:[]},'env.prepare':{name:'准备项目 Python 环境',fields:[]},
  'terminal.run':{name:'运行项目 Python 脚本',fields:[{...pathField,name:'项目内脚本'},{key:'args',name:'脚本参数，每行一个',type:'lines',multi:true,optional:true},{key:'stdin',name:'初始输入',multi:true,optional:true}]},
  'browser.read':{name:'读取网页',fields:site},'browser.download':{name:'下载同站资源',fields:site},
  'browser.click':{name:'操作网页元素',fields:[...site,element,{key:'destination_url',name:'预期链接地址',optional:true}]},
  'browser.fill':{name:'填写网页元素',fields:[...site,element,{key:'text',name:'填写内容'}]},
  'desktop.inspect':{name:'查看指定窗口与控件',fields:[{key:'window_title',name:'窗口完整标题'}]},
  'desktop.set':{name:'填写窗口控件',fields:[{key:'text',name:'填写内容'}]},
  'desktop.invoke':{name:'操作窗口按钮',fields:[]},'desktop.screenshot':{name:'截取指定窗口',fields:[]},
};
type WindowReceipt={target:Record<string,unknown>;controls:{control_id:number;control_hwnd:number;class_name:string;actions:string[]}[]};
const artifactUrl=(job:string,file:string)=>'api/v2/jobs/'+encodeURIComponent(job)+'/artifact?'+new URLSearchParams({path:file,download:'1'});
const live=(j:Job)=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(j.state);
const labels:Record<string,string>={QUEUED:'排队中',RUNNING:'运行中',STOP_REQUESTED:'正在停止',CANCELLED:'已取消',FAILED:'失败',COMPLETED:'已完成',OUTCOME_UNKNOWN:'操作结果待核对',STOP_UNKNOWN:'停止待核对',ORPHANED_UNKNOWN:'重启后待核对'};
const reason=(e:unknown)=>e instanceof Error?e.message:String(e);
const pendingId=()=>core.uncertainCommands().find(x=>x.kind.startsWith('tool.'))?.id||'';

export default function ComputerTools({context,onClose}:{context:Context;onClose:()=>void}){
  const [available,setAvailable]=useState<string[]>([]),[bindings,setBindings]=useState<Binding[]>([]);
  const [attempt,setAttempt]=useState(''),[action,setAction]=useState(''),[values,setValues]=useState<Record<string,string>>({});
  const [jobs,setJobs]=useState<Job[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[pending,setPending]=useState('');
  const [serial,setSerial]=useState(0),[limit,setLimit]=useState(20),[more,setMore]=useState(false);
  const [windowInfo,setWindowInfo]=useState<WindowReceipt|null>(null),[control,setControl]=useState('');
  const [preview,setPreview]=useState<{path:string;text:string}|null>(null),[input,setInput]=useState<Record<string,string>>({});
  const lock=useRef(false),alive=useRef(true);
  const selected=bindings.find(b=>b.attempt_id===attempt);
  const task=context.task_id||selected?.task_id;
  useEffect(()=>{
    alive.current=true;const c=new AbortController();
    (async()=>{
      await core.loadIdentity(c.signal);
      const [catalog,parents]=await Promise.all([
        core.request('v2/tools',{signal:c.signal}),
        core.request('v2/tool-contexts?'+new URLSearchParams({project_id:context.project_id}),{signal:c.signal})]);
      const tool=catalog.tools?.find((t:{tool_version:string})=>t.tool_version==='computer/1');
      if(!tool||tool.worker_present===false)throw Error('当前实例未安装电脑工具。');
      const list=(tool.actions as string[]).filter(k=>actions[k]);
      if(!list.length)throw Error('没有可用的电脑操作。');
      const valid=(parents.contexts||[]).filter((b:Binding)=>!context.task_id||b.task_id===context.task_id);
      if(!c.signal.aborted){setAvailable(list);setAction(list[0]);setBindings(valid);if(context.task_id)setAttempt(valid[0]?.attempt_id||'');setPending(pendingId());}
    })().catch(e=>{if(!c.signal.aborted)setError(reason(e));});
    return()=>{alive.current=false;c.abort();};
  },[context.project_id,context.task_id]);
  useEffect(()=>{
    const ctl=new AbortController();let timer:ReturnType<typeof setTimeout>;
    async function load(){
      const q=new URLSearchParams({project_id:context.project_id,tool_version:'computer/1',limit:String(limit)});if(task)q.set('task_id',task);
      try{const result=await core.request('v2/jobs?'+q,{signal:ctl.signal});if(ctl.signal.aborted)return;
        setJobs(result.jobs);setMore(result.has_more);if(result.jobs.some(live))timer=setTimeout(load,1100);
      }catch(e){if(!ctl.signal.aborted)setError(reason(e));}
    }
    void load();return()=>{ctl.abort();clearTimeout(timer);};
  },[context.project_id,serial,task,limit]);
  async function perform(operation:()=>Promise<void>){
    if(lock.current)return;lock.current=true;setBusy(true);setError('');
    try{await operation();if(alive.current)setSerial(x=>x+1);}
    catch(e){if(alive.current){setError(reason(e));if(e instanceof CoreRequestError&&e.unknown)setPending(e.commandId||'');}}
    finally{lock.current=false;if(alive.current)setBusy(false);}
  }
  const definition=actions[action];
  async function run(){
    if(!definition||context.task_id&&!selected)throw Error('当前任务没有可绑定的尝试。');
    const parameters:Record<string,unknown>={action};
    for(const field of definition.fields){
      const value=values[field.key]??'';
      if(!value&&field.optional)continue;
      parameters[field.key]=field.type==='lines'?value.split(/\r?\n/).filter(Boolean):field.type==='number'?Number(value):field.type==='bool'?value==='yes':value;
    }
    if(action.startsWith('desktop.')&&action!=='desktop.inspect'){
      if(!windowInfo)throw Error('请先在窗口检查作业中选择已核对的窗口。');parameters.target=windowInfo.target;
      if(action!=='desktop.screenshot'){
        const c=windowInfo.controls.find(x=>String(x.control_hwnd)===control&&x.actions.includes(action));
        if(!c)throw Error('请选择支持该操作的窗口控件。');
        parameters.control_hwnd=c.control_hwnd;parameters.control_id=c.control_id;
      }
    }
    const {receipt}=await core.command('tool.authorize',{project_id:context.project_id,tool_version:'computer/1',
      parameters,confirm:true,input_files:[],...(selected?{task_id:selected.task_id,attempt_id:selected.attempt_id,spec_hash:selected.spec_hash}:{})});
    if(!receipt?.authorization_id)throw Error('授权回执缺少身份。');
    if(alive.current)await core.command('tool.run',{authorization_id:receipt.authorization_id});
  }
  const blocked=busy||!!pending;
  return <section className="dt-panel"><header><h2>电脑与开发工具</h2><button onClick={onClose}>关闭</button></header>
    <p className="dt-root">{context.project_root||context.project_id}</p>
    <form onSubmit={e=>{e.preventDefault();void perform(run);}}><fieldset disabled={blocked||!available.length}>
      <label>关联任务<select value={attempt} onChange={e=>setAttempt(e.target.value)}>
        {!context.task_id&&<option value="">当前项目</option>}{context.task_id&&!bindings.length&&<option value="">没有可绑定的尝试</option>}
        {bindings.map(b=><option key={b.attempt_id} value={b.attempt_id}>{b.title||b.task_id} · {b.attempt_id.slice(0,8)}</option>)}</select></label>
      <label>操作<select value={action} onChange={e=>{setAction(e.target.value);setValues({});}}>{available.map(k=><option key={k} value={k}>{actions[k].name}</option>)}</select></label>
      {definition?.fields.map(f=><label key={f.key}>{f.name}{f.optional?'（可选）':''}
        {f.multi?<textarea required={!f.optional} placeholder={f.placeholder} value={values[f.key]||''} onChange={e=>setValues({...values,[f.key]:e.target.value})}/>:
        <input type={f.type==='number'?'number':'text'} required={!f.optional} placeholder={f.placeholder} value={values[f.key]||''} onChange={e=>setValues({...values,[f.key]:e.target.value})}/>}</label>)}
      {action.startsWith('desktop.')&&action!=='desktop.inspect'&&<><p>{windowInfo?'已选窗口 · '+String(windowInfo.target.title):'请先查看指定窗口，再从作业结果中选择该窗口。'}</p>
      {action!=='desktop.screenshot'&&<label>目标控件<select required value={control} onChange={e=>setControl(e.target.value)}><option value="">请选择</option>
      {windowInfo?.controls.filter(c=>c.actions.includes(action)).map(c=><option key={c.control_hwnd} value={c.control_hwnd}>{c.class_name} · {c.control_id}</option>)}</select></label>}</>}
      <button type="submit" disabled={!definition||!!context.task_id&&!selected}>授权并执行本次操作</button>
    </fieldset></form>
    {error&&<p role="alert">{error}</p>}
    {pending&&<p>待核对命令 {pending} <button onClick={()=>perform(async()=>{const r=await core.receipt(pending);if(r.state==='RECORDED')setPending(pendingId());else throw Error('尚未找到原命令回执。');})}>查原回执</button></p>}
    <header><h3>作业与输出</h3><button onClick={()=>setSerial(x=>x+1)}>刷新</button></header>
    {jobs.map(j=><article key={j.id}><header><strong>{labels[j.state]||j.state}</strong>{live(j)&&j.state!=='STOP_REQUESTED'&&<button disabled={blocked} onClick={()=>perform(async()=>{await core.command('tool.cancel',{invocation_id:j.id});})}>停止</button>}</header>
      <p>{j.error||j.summary}</p>
      {j.action==='terminal.run'&&live(j)&&<><button onClick={()=>perform(async()=>{const data=await core.request('v2/jobs/'+encodeURIComponent(j.id)+'/console');setPreview({path:j.id,text:data.text||'尚无输出'});})}>读取当前输出</button>{preview?.path===j.id&&<pre>{preview.text}</pre>}</>}
      {j.action==='terminal.run'&&j.state==='RUNNING'&&<label>向此进程输入<input value={input[j.id]||''} onChange={e=>setInput({...input,[j.id]:e.target.value})}/>
        <button disabled={blocked} onClick={()=>perform(async()=>{await core.command('tool.input',{invocation_id:j.id,text:(input[j.id]||'')+'\n'});setInput({...input,[j.id]:''});})}>发送一行</button></label>}
      {(j.artifacts||[]).map(f=><div key={f.path}><a download href={artifactUrl(j.id,f.path)}>{f.path.split(/[\\/]/).pop()} · {f.bytes} 字节</a>
        {/\.(json|txt|log|diff|patch)$/i.test(f.path)&&f.bytes<=1048576&&<button onClick={()=>perform(async()=>{const res=await fetch(artifactUrl(j.id,f.path));if(!res.ok)throw Error('无法读取该输出版本。');setPreview({path:f.path,text:(await res.text()).slice(0,60000)});})}>查看输出</button>}
        {j.action==='desktop.inspect'&&f.path.endsWith('surface-receipt.json')&&<button onClick={()=>perform(async()=>{const res=await fetch(artifactUrl(j.id,f.path));if(!res.ok)throw Error('窗口回执读取失败。');const win=await res.json();if(!win.target||!Array.isArray(win.controls))throw Error('窗口回执不完整。');setWindowInfo(win);setControl('');setAction('desktop.screenshot');setValues({});})}>使用此窗口</button>}
        {preview?.path===f.path&&<pre>{preview.text}</pre>}</div>)}
      <details><summary>执行记录</summary><p>作业 {j.id}</p><p>授权 {j.authorization_id}</p>{j.task_id&&<p>任务 {j.task_id}</p>}</details></article>)}
    {!jobs.length&&<p>没有已登记的电脑作业。</p>}{more&&limit<100&&<button onClick={()=>setLimit(x=>Math.min(100,x+20))}>查看更多</button>}
  </section>;
}
