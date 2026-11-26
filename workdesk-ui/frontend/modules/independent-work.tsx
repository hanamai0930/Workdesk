import { useEffect, useState, type RefObject } from 'react';
import { ClipboardList, File, Folder, Lock, MoreHorizontal, PanelRight, Share, SlidersHorizontal, Terminal } from 'lucide-react';
import { NativeSelect } from '../components/ui/native-select';
import { Button } from '../components/ui/button';
import { QuickWorkspace } from './quick-workspace';
import type { QuickToolRequest } from './quick-workspace';
import { ConversationSummaryButton, workSummary, type ConversationSummaryProps } from './conversation-summary';
import './independent-work.css';
import { WorkModelPicker } from './work-model-picker';

const brands:Record<string,{initial:string;rgb:string;title:string;hint:string;placeholder:string}>={
  codex:{initial:'C',rgb:'45,133,115',title:'从一个想法开始，把它做出来',hint:'编写代码、检查文件，让改动逐步落地。',placeholder:'描述要构建、修改或检查的内容…'},
  deepseek:{initial:'D',rgb:'77,107,254',title:'把复杂问题拆开，一步步推进',hint:'梳理问题、分析材料，把思路转为具体工作。',placeholder:'描述问题、提供材料，或说说你的目标…'},
  qwen:{initial:'Q',rgb:'112,81,220',title:'从材料到成果，一起完成这项工作',hint:'整理文档、处理文件，让任务有序展开。',placeholder:'描述需要整理、处理或完成的工作…'},
};
const genericBrand={icon:'',rgb:'92,113,142',title:'从一个想法开始，把它做出来',hint:'使用所选官方工作端，逐步完成这项工作。',placeholder:'描述要构建、修改或检查的内容…'};
export const independentBrand=(provider:string)=>brands[provider]||genericBrand;
export function independentAppearance(provider:string){
  return {'--work-rgb':`var(--wd-work-${provider}-rgb,${independentBrand(provider).rgb})`,'--work-opacity':`var(--wd-work-${provider}-opacity,.04)`};
}
export function IndependentProvider({v,names,isLocked}:any){
  return <div className="wk-independent-provider" data-independent-work="provider">
    {independentBrand(v.provider).icon?<span className="wk-work-brand-icon" aria-hidden="true">{independentBrand(v.provider).initial}</span>:<Terminal className="wk-work-brand-icon" aria-hidden="true"/>}
    <NativeSelect key={v.provider} id="wb-provider" aria-label="选择工作端" className="wk-model-select" defaultValue={v.provider} disabled={isLocked||v.workLoading||v.busy}>
      {Object.entries(names).map(([id,name]:any)=><option key={id} value={id}>{name}</option>)}
    </NativeSelect>
    {!isLocked && <a className="wk-independent-side-link" href="#work/partners">使用已保存伙伴连接</a>}
    {isLocked&&<Lock className="wk-lock" aria-label="工作端已固定"/>}
  </div>;
}

export function IndependentConversationHeader({v,open,onToggle,toggleRef,onOpenTab}:{
  v:any;open:boolean;onToggle:()=>void;toggleRef:RefObject<HTMLButtonElement|null>;
  onOpenTab:NonNullable<ConversationSummaryProps['onOpenTab']>;
}){
  const task=v.detail.task;
  const recordTitle=v.co?.display_records?.find((row:any)=>row.kind==='task'&&row.record_id===task.id)?.title;
  const title=recordTitle||task.title||'独立工作';
  return <header className="wk-independent-heading" data-independent-work="conversation-header">
    <div className="wk-independent-chat-title"><Folder aria-hidden="true"/><h1 title={title}>{title}</h1></div>
    <div className="wk-independent-header-actions" role="group" aria-label="当前工作操作">
      <Button asChild variant="ghost" size="icon" className="wk-icon">
        <a href={'#work-items/view-work/'+encodeURIComponent(task.id)} aria-label="打开任务详情" title="任务详情"><ClipboardList/></a>
      </Button>
      {task.spec.runtime_mode==='official'&&<Button variant="ghost" size="icon" type="button" className="wk-icon" data-wb="native-panel" aria-label="工作端控制" title="工作端控制"><Terminal/></Button>}
      <Button variant="ghost" size="icon" type="button" className="wk-icon" data-wb-menu="conversation"
        aria-label="对话操作" title="对话操作" aria-haspopup="menu" aria-expanded={v.menu==='conversation'}><MoreHorizontal/></Button>
      <Button variant="ghost" size="icon" type="button" className="wk-icon" data-wb="share" aria-label="分享或导出" title="分享或导出"><Share/></Button>
      <ConversationSummaryButton {...workSummary(v.detail)} title={title} onOpenTab={onOpenTab}
        onAttach={()=>{void window.WorkdeskWorkbench.perform('files');}}/>
      <Button ref={toggleRef} variant="ghost" size="icon" type="button" className="wk-icon" aria-expanded={open} aria-controls={open?'wk-independent-side-panel':undefined}
        aria-label="工作侧栏" aria-keyshortcuts="Control+Shift+B"
        title={open?'收起标签页（Ctrl+Shift+B）':'显示标签页（Ctrl+Shift+B）'} onClick={onToggle}><PanelRight/></Button>
    </div>
  </header>;
}

export function IndependentSidebar({v,open,panelRef,onClose,expanded,onToggleExpanded,requestedTool}:{
  v:any;open:boolean;panelRef:RefObject<HTMLElement|null>;onClose:()=>void;expanded:boolean;
  onToggleExpanded:()=>void;requestedTool?:QuickToolRequest;
}){
  const {task,work_items:workItems=[]}=v.detail;
  const taskId=encodeURIComponent(task.id);
  const member=task.spec.execution_resource?.member?.name;
  return <aside ref={panelRef} id="wk-independent-side-panel" className="wk-independent-side" aria-label="工作侧栏"
    data-independent-work="side-panel" hidden={!open}>
    <QuickWorkspace stateKey={`work:${task.id}:project:${task.project_id||''}`} projectId={v.project} records={v.chats} onClose={onClose}
      expanded={expanded} onToggleExpanded={onToggleExpanded} requestedTool={requestedTool}>
      <Button variant="ghost" type="button" className="wk-independent-side-link" data-wb="files"><File/>工作材料</Button>
      <a className="wk-independent-side-link" href={'#control/task/'+taskId}><Terminal/>运行与控制</a>
      {task.spec.runtime_mode==='official'&&<Button variant="ghost" type="button" className="wk-independent-side-link" data-wb="native-panel"><SlidersHorizontal/>官方工作端控制</Button>}
      {workItems.map((item:{id:string;title:string})=><a className="wk-independent-side-link" key={item.id} href={'#work-items/item/'+encodeURIComponent(item.id)+'/from-work/'+taskId} title={item.title}>
        <ClipboardList/><span>{item.title||'关联任务'}</span></a>)}
      {member&&<a className="wk-independent-side-link" href="#control/profiles"><span>成员 · {member}</span></a>}
    </QuickWorkspace>
  </aside>;
}
export function WorkModeSwitch({v,selected}:any){
  return <div className="wk-work-type" role="group" aria-label="工作方式">
    <Button variant="ghost" type="button" data-wb-type="independent" aria-pressed={v.type==='independent'} disabled={selected||v.workLoading||v.busy}>独立</Button>
    <Button variant="ghost" type="button" data-wb-type="cooperative" aria-pressed={v.type==='cooperative'} disabled={selected||v.workLoading||v.busy}>合作</Button>
  </div>;
}
export function IndependentWelcome({provider}:any){
  const brand=independentBrand(provider);
  return <div className="wk-greeting wk-independent-welcome">
    {brand.icon?<span className="wk-work-brand-icon" aria-hidden="true">{brand.initial}</span>:<Terminal className="wk-work-brand-icon" aria-hidden="true"/>}
    <h1>{brand.title}</h1><p>{brand.hint}</p>
  </div>;
}

// Presentation for independent work only. Cooperative roles are deliberately
// supplied by their own presenter; the official runtime still executes tools.
export function IndependentModels({v, names, profile, isLocked}:any) {
  const official=!v.detail||v.detail.task.spec.runtime_mode==='official';
  if(official)return <span className="wk-model-controls" data-independent-work="models"><WorkModelPicker v={v} profile={profile} disabled={v.workLoading||v.busy}/></span>;
  return <span className="wk-model-controls" data-independent-work="models">
    <NativeSelect key={`${v.provider}:${v.model}`} id="wb-model" aria-label="选择工作模型" className="wk-model-select" defaultValue={v.model} disabled={(!official&&isLocked)||v.workLoading||v.busy} title={official?'下一回合使用的模型':undefined}>
      {!v.model&&<option value="">正在读取模型…</option>}
      {(profile?.enabled_models||profile?.models||[]).map((model:string)=><option key={model} value={model}>{model}</option>)}
    </NativeSelect>
    {!official&&isLocked&&<span className="wk-lock" title="历史任务模型已固定"><Lock aria-label="模型已固定"/></span>}
  </span>;
}

export function IndependentService({service}:any) {
  if(!service)return null;
  return <div className="wk-hint"><Button type="button" variant="ghost" className="wk-chip" data-wb="independent-service" data-independent-work="service" title="查看当前官方运行服务与接续方式">官方运行服务 · {service.harness}</Button></div>;
}

export function IndependentRunState({detail,busy}:any) {
  const attempt=detail?.attempts.at(-1);
  const names:Record<string,string>={RESERVED:'正在启动官方服务',SEND_INTENT:'消息发送待核对',RUNNING:'官方服务正在执行',WAITING_APPROVAL:'等待本次操作审批',WAITING_INPUT:'等待补充回答',STOP_REQUESTED:'已请求停止，等待退出确认',COMPLETED:'本回合已结束',FAILED:'本回合失败',FAILED_NO_SEND:'本回合未发送',STOPPED_RUNTIME:'运行环境已停止',INTERRUPTED:'本回合已中断',SEND_UNKNOWN:'发送或执行状态待核对',STOP_UNKNOWN:'停止状态待核对'};
  const live=['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED'].includes(attempt?.state);
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{
    if(!live)return;
    setNow(Date.now());
    const timer=window.setInterval(()=>setNow(Date.now()),5000);
    return ()=>window.clearInterval(timer);
  },[attempt?.id,live]);
  if(!attempt)return null;
  const showErrors=['FAILED','FAILED_NO_SEND','SEND_UNKNOWN','STOP_UNKNOWN'].includes(attempt.state);
  const closed=!!attempt.result?.manual_close;
  const elapsed=live&&Number.isFinite(attempt.started)?Math.max(0,Math.floor(now/1000-attempt.started)):null;
  const duration=elapsed==null?'':elapsed<60?`${elapsed} 秒`:`${Math.floor(elapsed/60)} 分 ${elapsed%60} 秒`;
  return <section className="wk-run-status" data-independent-work="status" aria-label="当前回合状态">
    <div className="wk-run-status-row">
      <p className="wk-note" role="status">{closed?'待核对回合已结束，可以继续聊天':names[attempt.state]||attempt.state}{live&&duration&&<span> · 已持续 {duration}</span>}</p>
      <div className="wk-run-status-actions">
        {(live||showErrors)&&<a href={'#control/task/'+encodeURIComponent(detail.task.id)}>{['WAITING_APPROVAL','WAITING_INPUT'].includes(attempt.state)?'查看待处理请求':'运行与控制'}</a>}
        {live&&<Button type="button" variant="outline" size="sm" data-wb="runtime-stop" disabled={busy||attempt.state==='STOP_REQUESTED'}>{attempt.state==='STOP_REQUESTED'?'正在停止':'停止此回合'}</Button>}
      </div>
    </div>
    {closed&&<p className="wk-note">上一条消息没有自动重发，原记录与用量已保留。</p>}
    {showErrors&&attempt.result?.errors?.length>0&&<details className="wk-run-status-errors"><summary>查看错误说明</summary><p>{attempt.result.errors.map((e:any)=>e.message||JSON.stringify(e)).join('；')}</p></details>}
  </section>;
}
