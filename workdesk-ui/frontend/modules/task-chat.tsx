import {Fragment,useEffect,useRef,useState,type ReactNode} from 'react';
import {ArrowRightLeft,ArrowUp,FileSpreadsheet,FileText,PanelLeft,PanelRight} from 'lucide-react';
import {coreClient as core} from '../core/client';
import {partnerRunning,partnerStateLabel,type PartnerRun} from '../core/partner-runtime';
import {Template} from '../components/template';
import {SafeMarkdown} from '../components/safe-markdown';
import {QuickWorkspace,useQuickWorkspaceView} from './quick-workspace';
import {TaskFilePreview,type ResultFile} from './task-file-preview';
import {TurnModelPicker,partnerName,turnModelName,type TurnConnection} from './task-model-picker';
import type {TaskDetail,Job,Decision,Version,ToolRequest,Delivery} from './task-delivery';
import './task-chat.css';

type Snapshot={id:string;task_id:string;attempt_id:string;title:string;revision:string;snapshot_hash:string;snapshot:{manifest:Record<string,{sha256:string}>};
  decision?:{value:string;note:string;command_id:string}};
type Output={key:string;label:string;job?:Job;file?:Version;snapshot?:Snapshot;decision?:Decision};
type Command=(kind:string,payload:Record<string,unknown>,id?:string)=>ReturnType<typeof core.command>;
type Props={detail:TaskDetail;run:PartnerRun;jobs:Job[];requests:ToolRequest[];info:Delivery|null;speaker:string;turnEnded:boolean;
  blocked:boolean;busy:boolean;error:string;pending:{id:string;kind:string}[];refreshKey:number;act:(fn:()=>Promise<void>,recover?:boolean)=>Promise<void>;
  command:Command;approve:(r:ToolRequest)=>Promise<void>;onRunChange:()=>Promise<void>;onDetails:()=>void;onRefresh:()=>void};
const enc=encodeURIComponent;
const fileName=(s:string)=>s.split(/[\\/]/).pop()||s;
const snapshotRef=(s:Snapshot)=>({id:s.id,revision:s.revision,snapshot_hash:s.snapshot_hash});
function Message({name,user=false,children}:{name:string;user?:boolean;children:ReactNode}){
  return <article className={user?'wd-talk-user':'wd-talk-answer'}><span className="wk-byline">{name}</span><div className="wd-talk-body">{children}</div></article>;
}
function Text({value}:{value:string}){return <Template html={window.WorkdeskLegacy.markdown(value)}/>;}
function Answer({value,files}:{value:string;files:Map<string,()=>void>}){
  if(value.length<=580)return <SafeMarkdown text={value} fileLinks={files}/>;
  const opening=value.split(/\n\s*\n/)[0].trim();
  const short=opening.length<=300?opening:opening.slice(0,280)+'…';
  return <><SafeMarkdown text={short} fileLinks={files}/><details><summary>查看完整回复</summary><SafeMarkdown text={value} fileLinks={files}/></details></>;
}
function resultFiles(output:Output,round:number):ResultFile[]{
  if(output.file && output.job){
    const f=output.file;
    return [{name:fileName(f.path),path:f.path,round,sha256:f.sha256,mime:f.mime,source:'job',
      route:'v2/jobs/'+enc(output.job.id)+'/artifact?'+new URLSearchParams({path:f.path,download:'1'})}];
  }
  const s=output.snapshot;
  return s?Object.entries(s.snapshot.manifest).map(([file,{sha256}])=>({
    name:fileName(file),path:file,round,sha256,source:'snapshot',
    route:'v1/result-preview?'+new URLSearchParams({id:s.id,snapshot_hash:s.snapshot_hash,file})
  })):[];
}
function mutation(text:string){
  return !/[?？]|^(为什么|请问|能否|是否|如何|怎么|解释|告诉我)|(?:吗|呢)[。！!]?$/u.test(text)&&/^(请|帮我|麻烦|再|请你|我想|我需要|可以)?\s*(把|将|改为|改成|修改|修订|调整|更新|换成|删除|添加|增加|去掉|重做)|^[^。！？\n]{1,36}(改成|改为|调成|调整为)/u.test(text);
}
function summary(r:ToolRequest){
  const p=r.parameters;
  const name=typeof p.name==='string'?fileName(p.name):'文档';
  const cells=p.cells&&typeof p.cells==='object'?Object.entries(p.cells):[];
  return <><p>请求使用文档工具生成 {name}。</p>{r.input_files.length>0&&<p>使用材料：{r.input_files.map(v=>typeof v==='string'?fileName(v):typeof v==='object'&&v&&'kind' in v&&v.kind==='job'?'所选成果版本':'所选材料').join('、')}</p>}
    {cells.length>0&&<p>{cells.slice(0,6).map(([cell,value])=>cell+' = '+String(value)).join('，')}{cells.length>6?' 等 '+cells.length+' 处':''}</p>}
    <details><summary>本次操作内容</summary><pre>{JSON.stringify(r.parameters,null,2)}</pre></details></>;
}
export function TaskChat(p:Props){
  const {detail,run,info}=p,{task,attempts}=detail,latest=attempts.at(-1)!;
  const [text,setText]=useState(''),[target,setTarget]=useState(''),[askTarget,setAskTarget]=useState(false);
  const [snapshots,setSnapshots]=useState<Snapshot[]>([]),[snapshotError,setSnapshotError]=useState('');
  const [reply,setReply]=useState<Record<string,string>>({});
  const [nextConnection,setNextConnection]=useState<TurnConnection|null>(null);
  const [preview,setPreview]=useState<ResultFile>();
  const workspaceKey='work:'+task.id+':project:'+task.project_id;
  const workspace=useQuickWorkspaceView(true,workspaceKey);
  function openFile(file:ResultFile){setPreview(file);workspace.reveal();}
  const log=useRef<HTMLDivElement>(null),stick=useRef(true),input=useRef<HTMLTextAreaElement>(null);
  const live=useRef(true);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  const captured=detail.events?.filter(e=>e.kind==='work_result_captured').length||0;
  useEffect(()=>{
    const ctl=new AbortController();
    core.request<{results:Snapshot[]}>('v1/result-catalog?task='+enc(task.id),{signal:ctl.signal}).then(v=>{
      if(!ctl.signal.aborted){setSnapshots(v.results.filter(s=>s.task_id===task.id));setSnapshotError('');}
    }).catch(()=>{if(!ctl.signal.aborted)setSnapshotError('保存的回复和文件暂时无法读取。');});
    return()=>ctl.abort();
  },[task.id,captured,p.refreshKey,p.turnEnded]);
  const outputs=(id:string):Output[]=>[
    ...p.jobs.filter(j=>j.attempt_id===id).flatMap(j=>(j.artifacts||[]).map(f=>({key:j.id+'/'+f.sha256+'/'+f.path,
      label:fileName(f.path),job:j,file:f,decision:j.decisions?.filter(d=>d.sha256===f.sha256&&(d.path===f.path||!!f.relative_path&&f.relative_path===d.relative_path)).at(-1)}))),
    ...snapshots.filter(s=>s.attempt_id===id).map(s=>({key:s.id+'/'+s.snapshot_hash,label:Object.keys(s.snapshot.manifest).map(fileName).join('、')||'本轮回复',snapshot:s})),
  ];
  const items=outputs(latest.id);
  const fileItems=items.filter(i=>i.file||Object.keys(i.snapshot?.snapshot.manifest||{}).length);
  const automatic=fileItems.length?fileItems:items;
  const scope={task_id:task.id,attempt_id:latest.id,run_id:run.id,spec_hash:task.spec_hash};
  const ended=p.turnEnded;
  const active=partnerRunning(run.state);
  const selected=items.find(i=>i.key===target);
  const guidance=info?.guidance;
  const nickname=(window as Window & {WorkdeskSettingsPreferences?:{get:()=>{userNickname?:string}}}).WorkdeskSettingsPreferences?.get()?.userNickname||'你';
  useEffect(()=>{const el=log.current;if(el&&stick.current)el.scrollTop=el.scrollHeight;},[run.output,run.state,latest.id,p.jobs.length,p.requests.length,snapshots.length,guidance?.command_id]);
  const currentCommand=async(kind:string,payload:Record<string,unknown>)=>{await p.command(kind,payload);await p.onRunChange();};
  function send(){
    const content=text.trim();if(!content||p.blocked)return;
    const phrase=content.replace(/[。！!\s]+$/u,'');
    if(active&&/^(?:请)?(?:先)?(?:停一下|停止|停止运行|停止当前工作|暂停当前工作)$/u.test(phrase)){
      void p.act(async()=>{await currentCommand('task.stop',{task_id:task.id,attempt_id:latest.id,owner:latest.owner});if(live.current)setText('');});return;
    }
    const accepting=/^(?:我)?(?:确认)?(?:验收通过|验收(?:此|这|这个|该|最新)(?:一)?版(?:本)?|接受(?:此|这|该)版(?:本)?)$/u.test(phrase);
    let chosen=selected;
    if(target&&!chosen){setTarget('');setAskTarget(true);return;}
    if(ended&&!chosen&&(accepting||mutation(content))&&automatic.length){
      if(automatic.length>1){setAskTarget(true);return;}
      chosen=automatic[0];
    }
    if(ended&&chosen&&accepting){accept(chosen,true);return;}
    void p.act(async()=>{
      if(!ended){
        if(!active||run.state==='STOP_REQUESTED')throw Error('当前运行尚未确认可接续，请先核对原运行。');
        await p.command('work.guidance',{...scope,previous:guidance?.command_id||'',note:guidance?.note?guidance.note+'\n'+content:content});
      }else{
        let feedback:Record<string,unknown>={};
        if(chosen?.job&&chosen.file){
          const {job,file,decision}=chosen;
          const id=decision?.decision==='revise'&&decision.note===content?decision.command_id:
            (await p.command('tool.decide',{invocation_id:job.id,path:file.path,sha256:file.sha256,confirm:true,decision:'revise',note:content})).command_id;
          feedback={feedback_command_id:id};
        }else if(chosen?.snapshot){
          const s=chosen.snapshot;
          const decided=s.decision?.value==='return'&&s.decision.note===content?s:
            ((await p.command('result.decide',{...snapshotRef(s),decision:'return',note:content,confirm:true})).receipt as {result:Snapshot}).result;
          feedback={feedback_snapshot:snapshotRef(decided)};
        }
        if(!live.current)return;
        await p.command('independent.send',{task_id:task.id,attempt_id:latest.id,owner:latest.owner,goal:content,...feedback,
          ...(nextConnection?{provider:nextConnection.partner_id,connection_id:nextConnection.id,connection_revision:nextConnection.revision}:{}),
          ...(guidance?.note?{guidance_command_id:guidance.command_id}:{})});
        if(live.current)location.reload();
      }
      if(live.current){setText('');setTarget('');setAskTarget(false);}
    });
  }
  function accept(i:Output,fromMessage=false){
    void p.act(async()=>{
      if(i.job&&i.file)await p.command('tool.decide',{invocation_id:i.job.id,path:i.file.path,sha256:i.file.sha256,decision:'accept',note:'',confirm:true});
      else if(i.snapshot)await p.command('result.decide',{...snapshotRef(i.snapshot),decision:'accept',note:'',confirm:true});
      if(fromMessage&&live.current){setText('');setTarget('');setAskTarget(false);}
    });
  }
  const state=run.state==='STOP_REQUESTED'?'正在停止这项工作。':run.state.includes('UNKNOWN')?'上次运行的结果还没有确认。':
    run.state==='WAITING_APPROVAL'?'正在等你确认本次操作。':run.state==='WAITING_INPUT'?'需要你补充信息。':active?'正在处理你的要求…':
    ended?(info?.state==='COMPLETED'?'任务已完成。':items.length?'本轮已结束，成果可查看。':'本轮已结束。'):'本次工作已暂停。';
  const missingFile=detail.events?.some(e=>e.kind==='work_result_capture_pending'&&e.attempt_id===latest.id);
  const shortTitle=task.title.split(/[。！？\n]/u).find(part=>part.trim())||task.title;
  return <div className="wd-task-layout" data-expanded={workspace.open&&workspace.expanded}>
    <section className="wd-task-chat" aria-label="工作对话" data-partner-work>
    <header><button type="button" aria-label="任务详情" title="任务详情" onClick={p.onDetails}><PanelLeft size={17}/></button><strong title={task.title}>{shortTitle}</strong>
      <button ref={workspace.toggleRef} className="wd-task-side-toggle" aria-label="工作侧栏" title="工作侧栏" aria-expanded={workspace.open}
        aria-controls="wd-task-side" onClick={workspace.toggle}><PanelRight size={16}/></button></header>
    <div ref={log} className="wd-talk-scroll" onScroll={e=>{const el=e.currentTarget;stick.current=el.scrollHeight-el.scrollTop-el.clientHeight<64;}}>
    <div className="wd-talk-column">
    <Message name={nickname} user><Text value={task.spec.goal}/></Message>
    {attempts.map((a,n)=>{
      const continuation=detail.events?.find(e=>e.attempt_id===a.id&&e.kind==='single_task_continued')?.payload;
      const recorded=detail.events?.find(e=>e.attempt_id===a.id&&e.kind==='execution_reserved')?.payload.human_note;
      const priorDecision=continuation?.feedback_command_id&&p.jobs.flatMap(j=>j.decisions||[]).find(d=>d.command_id===continuation.feedback_command_id);
      const user=continuation?.user_message??(priorDecision?priorDecision.note:recorded);
      const isLatest=a.id===latest.id,answer=a.result?.answer||a.result?.native_result?.text||(isLatest?(run.output||run.result?.text):'');
      const versions=outputs(a.id);
      const configuration=a.model_configuration, previous=attempts[n-1]?.model_configuration;
      const before=turnModelName(previous?.provider,previous?.model);
      const after=turnModelName(configuration?.provider,configuration?.model);
      const author=partnerName(configuration?.provider||a.result?.partner_id)||(isLatest?p.speaker:'助手');
      const filesByVersion=versions.map(v=>resultFiles(v,n+1));
      const links=new Map<string,()=>void>(), seen=new Set<string>();
      for(const file of filesByVersion.flat()){
        if(seen.has(file.name))links.delete(file.name);
        else {seen.add(file.name);links.set(file.name,()=>openFile(file));}
      }
      return <Fragment key={a.id}>
        {before&&after&&before!==after&&<div className="wd-talk-model-change"><ArrowRightLeft size={13} aria-hidden="true"/><span>模型已从 {before} 更改为 {after}</span></div>}
        {user&&<Message name={nickname} user><Text value={user}/></Message>}
        {answer&&<Message name={author}><Answer value={answer} files={links}/></Message>}
        {versions.map((i,index)=>{
          const accepted=i.decision?.decision==='accept'||i.snapshot?.decision?.value==='accept';
          const returned=i.decision?.decision==='revise'||i.snapshot?.decision?.value==='return';
          const snapshot=i.snapshot;
          const expected=i.job&&p.requests.find(r=>r.invocation_id===i.job!.id)?.parameters.expected;
          const checkedValues=i.job?.checks?.some(c=>c.kind==='关键值'&&c.state==='PASSED'&&c.sha256===i.file?.sha256)&&expected&&typeof expected==='object'
            ?Object.entries(expected).slice(0,4).map(([cell,v])=>cell+' = '+String(v)).join('，'):'';
          return <article key={i.key} className="wd-talk-delivery" aria-label="本轮成果"><div className="wd-talk-artifact">
            {filesByVersion[index].length?filesByVersion[index].map(file=><button type="button" key={file.route} className="wd-talk-file-link"
              data-preview-file={file.name} title={'预览 '+file.path} onClick={()=>openFile(file)}>{/\.(xlsx|csv)$/i.test(file.name)?<FileSpreadsheet size={18}/>:<FileText size={18}/>}<span>{file.name}</span></button>):<strong>{i.label}</strong>}
            <small>第 {n+1} 轮 · {accepted?'已验收':returned?'已退回':'待验收'}</small></div>
            {checkedValues&&<p>文件已核对：{checkedValues}。</p>}
            {snapshot&&!Object.keys(snapshot.snapshot.manifest).length&&!answer&&<details><summary>查看回复</summary><Text value={String((snapshot.snapshot as {answer?:string}).answer||'已保存回复记录。')}/></details>}
            {isLatest&&ended&&<><button disabled={p.blocked} onClick={()=>{setTarget(i.key);setAskTarget(false);input.current?.focus();}}>修改此版</button>
              {!accepted&&<button disabled={p.blocked} onClick={()=>accept(i)}>验收此版</button>}</>}
          </article>;
        })}
      </Fragment>;
    })}
    {p.requests.filter(r=>r.run_id===run.id&&r.attempt_id===latest.id&&['PENDING','AUTHORIZED'].includes(r.state)).map(r=><Message key={r.id} name={p.speaker}>
      {summary(r)}<button disabled={p.blocked} onClick={()=>void p.act(()=>p.approve(r))}>允许本次操作</button>{r.state==='PENDING'&&<button disabled={p.blocked} onClick={()=>void p.act(()=>currentCommand('document.reject',{request_id:r.id}))}>拒绝</button>}
    </Message>)}
    {active&&run.pending_requests?.map(r=><Message key={r.id} name={p.speaker}><p>{r.message||'工作端需要你的确认。'}</p>
      {r.kind==='approval'?<>{!!(r.details||r.params||r.native)&&<details><summary>本次操作内容</summary><pre>{JSON.stringify(r.details||r.params||r.native,null,2)}</pre></details>}{['allow','deny'].map(d=><button key={d} disabled={p.blocked} onClick={()=>void p.act(()=>currentCommand('partner.approve',{run_id:run.id,request_id:r.id,decision:d}))}>{d==='allow'?'允许本次操作':'拒绝'}</button>)}</>:<>
        {Array.isArray(r.options)?<select aria-label="选择回答" value={reply[r.id]||''} onChange={e=>setReply({...reply,[r.id]:e.target.value})}><option value="">请选择</option>{r.options.filter(x=>x&&typeof x.value==='string').map(x=><option key={x.value} value={x.value}>{x.label}</option>)}</select>:<input type={r.sensitive?'password':'text'} aria-label="补充回答" value={reply[r.id]||''} onChange={e=>setReply({...reply,[r.id]:e.target.value})}/>}
        <button disabled={p.blocked||!reply[r.id]?.trim()} onClick={()=>void p.act(async()=>{await currentCommand('partner.input',{run_id:run.id,request_id:r.id,text:reply[r.id].trim()});if(live.current)setReply(v=>({...v,[r.id]:''}));})}>回答</button></>}
    </Message>)}
    {guidance?.note&&<Message name={nickname} user><p>{guidance.note}</p><small>已留给下一回合</small><button disabled={p.blocked} onClick={()=>void p.act(()=>currentCommand('work.guidance',{...scope,previous:guidance.command_id,note:''}))}>撤回</button></Message>}
    {(!ended||run.error||missingFile||(!items.length&&!run.output&&!latest.result?.answer))&&<Message name="Workdesk"><p role="status">{state}</p>{run.error&&<details><summary>查看中断说明</summary>{run.error}</details>}
      {run.state.includes('UNKNOWN')&&<button disabled={p.busy} onClick={()=>void p.act(()=>currentCommand('partner.reconcile',{run_id:run.id}),true)}>核对运行状态</button>}
      {missingFile&&<p>有一份交付文件还没取到。<a href={'#results/'+enc(task.id)}>查看已保存成果</a></p>}
    </Message>}</div></div>
    <div className="wd-talk-compose">{p.pending.length>0&&<p>有操作尚待确认。{p.pending.map(c=><button key={c.id} disabled={p.busy} onClick={()=>void p.act(async()=>{const v=await core.receipt(c.id);if(v.state!=='RECORDED')throw Error('上次操作的结果仍未确认。');await p.onRunChange();if(live.current&&c.kind==='independent.send')location.reload();},true)}>核对原操作</button>)}</p>}
      {(p.error||snapshotError)&&<p role="alert">{p.error||snapshotError} <button onClick={p.onRefresh}>刷新</button></p>}
      {askTarget&&<label>请选择对应的成果：<select value={target} onChange={e=>{setTarget(e.target.value);setAskTarget(false);input.current?.focus();}}><option value="">选择文件</option>{items.map(i=><option key={i.key} value={i.key}>{i.label}</option>)}</select></label>}
      {selected&&<div>正在修改 {selected.label} <button onClick={()=>setTarget('')}>取消选择</button></div>}
      <form className="wk-composer" onSubmit={e=>{e.preventDefault();send();}}><textarea ref={input} rows={2} aria-label="工作消息" placeholder="继续对话…" value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send();}}}/>
        <div className="wd-talk-actions">{active&&<button type="button" disabled={p.blocked||run.state==='STOP_REQUESTED'} onClick={()=>void p.act(()=>currentCommand('task.stop',{task_id:task.id,attempt_id:latest.id,owner:latest.owner}))}>停止</button>}
        {nextConnection&&<small className="wd-talk-model-scope" title="发送后，新连接将收到接续所需的对话和材料">下一条使用{nextConnection.source_label}{nextConnection.route==='api'?' · 按用量计费':''}</small>}
        <TurnModelPicker run={run} choice={nextConnection} onChoose={setNextConnection} disabled={p.blocked||!ended}/>
        <button className="wk-send" aria-label="发送工作消息" disabled={p.blocked||!text.trim()||(!active&&!ended)||run.state==='STOP_REQUESTED'}><ArrowUp size={18}/></button></div>
      </form>
    </div>
  </section>
  <aside id="wd-task-side" ref={workspace.panelRef} className="wd-task-side" aria-label="工作侧栏" hidden={!workspace.open}>
    <QuickWorkspace stateKey={workspaceKey} projectId={task.project_id} onClose={workspace.close}
      expanded={workspace.expanded} onToggleExpanded={workspace.toggleExpanded}
      preview={preview && {request:preview,name:preview.name,onClose:()=>setPreview(undefined),
        body:<TaskFilePreview key={preview.route+preview.sha256} file={preview}/>}}/>
  </aside></div>;
}
