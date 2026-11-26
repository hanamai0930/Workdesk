import { useState } from 'react';
import { Popover } from 'radix-ui';
import { Shield, ShieldCheck, ShieldAlert, Hand, Check, ChevronDown, Brain, Wrench, ListChecks, Activity, GitBranch } from 'lucide-react';
import { Button } from '../components/ui/button';
import { nativeTrace, type NativeTrace } from './native-events';
import './native-work.css';

export function NativeAccess({v}:any){
  const [open,setOpen]=useState(false);
  const raw=v.workAccess||v.detail?.task.spec.access||'workspace',access=raw==='inherit'?'workspace':raw;
  const connection=!v.detail?v.data?.partner_connections?.connections.find((row:any)=>row.id===v.connectionId&&row.partner_id===v.provider):null;
  const service=v.detail?.independent_service||v.data?.independent_work?.services.find((s:any)=>s.provider===v.provider);
  const options=!v.detail&&v.data?.partner_connections?connection?.permission_modes||[]:service?.access_modes||[];
  const label=options.find((mode:any)=>mode.id===access)?.name||(access==='full'?'完全访问权限':access==='auto'?'官方自动审核':'请求批准');
  const icons:any={workspace:Hand,auto:ShieldCheck,full:ShieldAlert};const Icon=icons[access]||Shield;
  return <Popover.Root open={open} onOpenChange={setOpen}><Popover.Trigger asChild><Button type="button" variant="ghost" className={'wk-native-access '+(access==='full'?'is-full':'')} aria-label={'访问权限：'+label} disabled={v.workLoading||v.busy}><Icon/><span>{label}</span><ChevronDown/></Button></Popover.Trigger>
    <Popover.Portal><Popover.Content data-ui-module="workbench" className="wk-access-menu" side="top" align="start" sideOffset={9} collisionPadding={12} aria-label="访问权限">
      <div className="wk-access-menu-heading">应如何批准操作？</div>{options.filter((m:any)=>m.id!=='inherit').map((m:any)=>{const ItemIcon=icons[m.id]||Shield;return <button key={m.id} type="button" className={'wk-access-menu-option '+(m.id==='full'?'is-full':'')} aria-pressed={m.id===access} disabled={m.supported===false||v.busy} onClick={async()=>{setOpen(false);await window.WorkdeskWorkbench.selectWorkAccess(m.id);}}><ItemIcon/><span><strong>{m.name}</strong><small>{m.supported===false?m.reason||m.description:m.description}</small></span>{m.id===access&&<Check/>}</button>;})}
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

export function NativeProcess({trace}: {trace:NativeTrace}){
  const steps=trace.steps,last=steps.at(-1),icons:any={thinking:Brain,tool:Wrench,plan:ListChecks,agent:GitBranch};
  if(!steps.length)return trace.active?<div className="wk-native-wait" role="status"><Activity/>等待官方工作端返回过程事件…</div>:null;
  return <details className="wk-native-process" data-native-process>
    <summary><Activity/><span>{trace.active?(last?.kind==='thinking'?'正在思考':last?.label||'正在执行'):'执行过程'} · {steps.length} 项</span><ChevronDown/></summary>
    <div className="wk-native-steps">{trace.agents.length>0&&<details className="wk-native-agents" data-native-agent-tree><summary><GitBranch/>原生子代理 · {trace.agents.length}</summary>{trace.agents.map(agent=>{
      let depth=0,current=agent.parent;const visited=new Set([agent.id]);while(trace.agents.some(a=>a.id===current)&&!visited.has(current)){visited.add(current);depth++;current=trace.agents.find(a=>a.id===current)!.parent;}
      return <div key={agent.id} style={{paddingLeft:Math.min(depth,8)*12}}><strong>{agent.name}</strong><small>{agent.status}</small><code>{agent.id}</code><span>来源：{agent.parent}</span></div>;
    })}<p>只展示官方已返回的父子关系与状态；回合结束不代表长期后台工作已完成。</p></details>}{steps.map(step=>{
      const Icon=icons[step.kind]||Activity;
      return <details key={step.id} className={'wk-native-step is-'+step.status} data-native-kind={step.kind}>
        <summary><Icon/><span>{step.label}</span><small>{step.status==='failed'?'失败':step.status==='unapplied'?'沿用配置':step.status==='running'&&trace.active?'进行中':'已记录'}</small></summary>
        {step.parent&&<small>子代理 · {step.session||step.parent}</small>}
        {step.text&&<pre>{step.text}</pre>}
        {step.input!==undefined&&<div><small>操作参数</small><pre>{typeof step.input==='string'?step.input:JSON.stringify(step.input,null,2)}</pre></div>}
        {step.result!==undefined&&<div><small>操作结果</small><pre>{typeof step.result==='string'?step.result:JSON.stringify(step.result,null,2)}</pre></div>}
      </details>;
    })}</div>
  </details>;
}

const tokenFormat = new Intl.NumberFormat('zh-CN');
const usdFormat = new Intl.NumberFormat('zh-CN', {style:'currency',currency:'USD',maximumFractionDigits:12});
type UsageRow = [label:string,value:string];
type CountField = readonly [key:string,label:string];
const deepseekCounts = [
  ['totalTokens','合计'], ['inputTokens','直接输入'], ['cacheReadTokens','缓存命中输入'],
  ['cacheWriteTokens','写入缓存的输入'], ['outputTokens','输出'],
] as const;
const codexCounts = [
  ['totalTokens','合计'], ['inputTokens','输入合计'], ['cachedInputTokens','缓存命中输入'],
  ['cacheWriteInputTokens','写入缓存的输入'], ['outputTokens','输出'], ['reasoningOutputTokens','推理输出'],
] as const;
const qwenCounts = [
  ['total_tokens','合计'], ['input_tokens','输入合计'], ['cache_read_input_tokens','缓存命中输入'],
  ['cache_creation_input_tokens','写入缓存的输入'], ['output_tokens','输出'],
] as const;

function tokenCount(value:unknown) {
  return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?`${tokenFormat.format(value)} Token`:'';
}

function countRows(source:Record<string,unknown>|undefined,fields:readonly CountField[]):UsageRow[] {
  return fields.flatMap(([key,label])=>{
    const value=tokenCount(source?.[key]);
    return value?[[label,value]]:[];
  });
}

function UsageGroup({title,rows}:{title:string;rows:UsageRow[]}) {
  if(!rows.length)return null;
  return <section className="wk-native-usage-group"><h3>{title}</h3><dl>{rows.map(([label,value])=>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>;
}

export function NativeUsage({detail}:any){
  const attempt=detail?.attempts.at(-1);
  if(!attempt)return null;
  const usage=nativeTrace(detail,attempt).usage;
  const {provider}=detail.task.spec;
  const hasSessionTotals=provider==='codex'&&!!(usage.last||usage.total);
  const deepseek=provider==='deepseek';
  const fields=deepseek?deepseekCounts:hasSessionTotals?codexCounts:qwenCounts;
  const receipt=hasSessionTotals?usage.last:usage;
  const recent=countRows(receipt,fields);
  const total=countRows(hasSessionTotals?usage.total:undefined,codexCounts);
  const summaryTotal=tokenCount(receipt?.[fields[0][0]]);
  const context=usage.context;
  const contextRows=countRows({window:context?.contextWindow??usage.modelContextWindow},[['window','上下文窗口上限']]);
  if(typeof context?.model==='string')contextRows.push(['模型',context.model]);
  if(typeof context?.provider==='string')contextRows.push(['来源',context.provider==='deepseek-official'?'DeepSeek 官方工作端':context.provider]);
  if(typeof context?.systemPromptUpdate==='string')contextRows.push(['系统提示词更新',context.systemPromptUpdate==='in-history'?'通过会话历史更新':context.systemPromptUpdate]);
  const cost=usage.total_cost_usd;
  if(typeof cost==='number'&&Number.isFinite(cost)&&cost>=0)recent.push(['官方返回费用',usdFormat.format(cost)]);
  if(!Object.keys(usage).length)return null;
  return <details className="wk-native-usage">
    <summary><span>官方用量与上下文</span>{summaryTotal&&<small>最近记录 · {summaryTotal}</small>}</summary>
    <div className="wk-native-usage-body">
      <UsageGroup title={hasSessionTotals?'最近一次模型调用':'最近一份官方用量回执'} rows={recent}/>
      {deepseek&&recent.some(([name])=>name==='直接输入')&&<p>直接输入指未命中缓存且未计入写入缓存的部分。</p>}
      <UsageGroup title="会话累计" rows={total}/>
      <UsageGroup title="模型与上下文" rows={contextRows}/>
      {Object.entries(usage).filter(([key])=>key.startsWith('child:')).map(([key,value],index)=><UsageGroup key={key}
        title={`子代理 ${index+1} 的最近回执`} rows={countRows(value as Record<string,unknown>,fields)}/>)}
      <p>仅列出回执提供的数值。账单金额以官方账单为准。</p>
      <details className="wk-native-usage-raw"><summary>查看原始回执</summary><pre>{JSON.stringify(usage,null,2)}</pre></details>
    </div>
  </details>;
}
