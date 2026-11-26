import { useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { NativeSelect } from '../components/ui/native-select';
import { coreClient } from './client';

type PendingRequest = { id: string; kind: string; message?: string; options?: unknown; details?: unknown; native?: unknown; params?: unknown; sensitive?: boolean; input_type?: string };
export type PartnerRun = {
  id: string; partner_id?: string; project_id?: string; workspace?: string; connection_id?: string; state: string; mode?: string;
  model?: string; account_label?: string; output?: string; error?: string; pending_requests?: PendingRequest[];
  local_exit_confirmed?: boolean; remote_state?: string; explicit_local_tracking_closed?: boolean;
  connection_snapshot?: Record<string, any>; release_snapshot?: Record<string, any>; result?: Record<string, any>;
  usage?: unknown; model_request_intent_count?: number; prompt?: string; created?: number;
};
export type PartnerPerform = (kind: string, payload: Record<string, unknown>, success: string, after?: () => void) => Promise<void>;
export const partnerRunning = (state: string) => ['QUEUED','RUNNING','STOP_REQUESTED','RESULT_RECEIVED','ERROR_RECEIVED','STOP_RESULT_RECEIVED','WAITING_LOGIN','WAITING_USER','WAITING_INPUT','WAITING_APPROVAL'].includes(state.toUpperCase());
const unknown = (state: string) => state.toUpperCase() === 'UNKNOWN' || state.toUpperCase().endsWith('_UNKNOWN');
const terminal = (state: string) => ['COMPLETED','SUCCEEDED','FAILED','ERROR','CANCELLED','STOPPED','STOPPED_RUNTIME','FAILED_NO_SEND'].includes(state.toUpperCase());
export function partnerStateLabel(state: string) {
  return ({QUEUED:'排队中',RUNNING:'运行中',COMPLETED:'已完成',FAILED:'未通过',FAILED_NO_SEND:'未启动',CANCELLED:'已取消',STOPPED_RUNTIME:'本地运行已停止',STOP_UNKNOWN:'停止结果待核对',ORPHANED_UNKNOWN:'运行待核对',UNKNOWN:'结果待核对',STOP_REQUESTED:'正在停止',RESULT_RECEIVED:'正在确认退出',ERROR_RECEIVED:'正在核对退出',STOP_RESULT_RECEIVED:'正在核对退出',WAITING_INPUT:'等待补充',WAITING_APPROVAL:'等待审批',READY:'已准备',VERIFIED:'检查通过',SAVED_UNVERIFIED:'已保存，待验证',DISABLED:'已停用'} as Record<string,string>)[state.toUpperCase()] || state;
}
const text = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const names: Record<string,string> = {codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen',claude:'Claude',grok:'Grok',gemini:'Gemini',kimi:'Kimi',glm:'GLM'};
const usageNames: Record<string,string> = {input_tokens:'输入 Token',prompt_tokens:'输入 Token',output_tokens:'输出 Token',completion_tokens:'输出 Token',total_tokens:'总 Token',cached_tokens:'缓存 Token',cache_read_input_tokens:'缓存读取 Token',cache_creation_input_tokens:'缓存写入 Token',cost:'费用',total_cost_usd:'费用估算 USD'};

function Usage({run}: {run: PartnerRun}) {
  const usage = run.usage ?? run.result?.usage ?? run.result?.native_usage ?? run.result?.native?.usage;
  return <div className="wd-partner-runtime-usage">
    <p className="wd-partners-note">模型请求意图：{typeof run.model_request_intent_count === 'number' ? `${run.model_request_intent_count} 条` : '尚未取得计数'}。意图条数不等于已计费请求次数。</p>
    {usage && typeof usage === 'object' ? <details><summary>原生用量回执</summary><dl>{Object.entries(usage as Record<string, unknown>).map(([key,value]) => <div key={key}><dt>{usageNames[key] || key}</dt><dd>{typeof value === 'object' ? text(value) : String(value)}</dd></div>)}</dl></details> : <p className="wd-partners-note">原生用量：官方回执未提供。</p>}
  </div>;
}
function RequestInput({request,value,disabled,onChange}: {request:PendingRequest;value:string;disabled:boolean;onChange:(value:string)=>void}) {
  const choices=Array.isArray(request.options)?request.options.filter((item):item is {value:string;label:string}=>!!item&&typeof item==='object'&&typeof item.value==='string'&&typeof item.label==='string'):[];
  return choices.length?<label>选择官方登录方式<NativeSelect aria-label="选择官方登录方式" value={value} disabled={disabled} onChange={event=>onChange(event.currentTarget.value)}><option value="">请选择</option>{choices.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</NativeSelect></label>:<label>补充内容<Textarea value={value} disabled={disabled} onChange={event=>onChange(event.currentTarget.value)}/></label>;
}

function NativeEvents({id}: {id: string}) {
  const [rows,setRows] = useState<{seq:number;event:Record<string,any>}[]>([]);
  const [busy,setBusy] = useState(false), [error,setError] = useState(''), [more,setMore] = useState(true);
  const alive = useRef(true), reads = useRef(coreClient.createReadScope());
  useEffect(() => {alive.current=true;return()=>{alive.current=false;reads.current.cancel();};},[id]);
  async function load() {
    if(busy)return;setBusy(true);setError('');const read=reads.current.begin();
    try {
      const after=rows.at(-1)?.seq || 0;
      const v=await coreClient.request<{events:{seq:number;event:Record<string,any>}[];has_more?:boolean}>(`v2/partner-runs/${encodeURIComponent(id)}/events?after=${after}`,{signal:read.signal});
      if(!alive.current||!read.isCurrent())return;
      setRows(prior=>[...prior,...v.events.filter(row=>row.seq>after)]);setMore(v.has_more ?? v.events.length===200);
    } catch(e){if(alive.current&&read.isCurrent())setError(e instanceof Error?e.message:'原生活动读取失败。');}
    finally{if(alive.current&&read.isCurrent())setBusy(false);}
  }
  return <details className="wd-partner-native-events" onToggle={event=>{if(event.currentTarget.open&&!rows.length&&!busy)void load();}}><summary>原生活动与工具详情</summary>
    <p className="wd-partners-note">按需读取同一运行的原始回执，包含工具参数和实际错误。</p>
    {rows.map(row=>{const event=row.event;const native=event.native||event.params||event;const label=event.kind||event.method||event.type||'官方活动';return <details key={row.seq}><summary>{row.seq} · {label}</summary><pre>{text(event.message||event.text||native||event)}</pre></details>;})}
    {error&&<p className="wd-partners-error" role="alert">{error}</p>}
    <Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>void load()}>{busy?'正在读取…':rows.length?(more?'继续读取活动':'刷新原生活动'):'读取原生活动'}</Button>
  </details>;
}

export function PartnerRunList({runs,busy,blocked,perform,label}: {runs:PartnerRun[];busy:boolean;blocked:boolean;perform:PartnerPerform;label?:string}) {
  return <div className="wd-partners-runs">{runs.map(run=><PartnerRunCard key={run.id} {...{run,busy,blocked,perform,label}}/>)}</div>;
}
function PartnerRunCard({run,busy,blocked,perform,label}: {run:PartnerRun;busy:boolean;blocked:boolean;perform:PartnerPerform;label?:string}) {
  const [input,setInput] = useState<Record<string,string>>({}), [closing,setClosing] = useState(false);
  const connection=run.connection_snapshot||{}, version=run.release_snapshot?.version;
  const model=run.model||connection.model, name=label||names[run.partner_id||'']||run.partner_id||'官方工作端';
  return <div className="wd-partners-run"><div className="wd-partners-run-heading"><strong>{name}{model?` ＋ ${model}`:''}</strong><span className="wd-partners-state" data-state={run.state}>{partnerStateLabel(run.state)}</span>
    {!terminal(run.state)&&!unknown(run.state)&&<Button variant="outline" className="wd-settings-button" disabled={blocked||run.state==='STOP_REQUESTED'} onClick={()=>void perform('partner.stop',{run_id:run.id},'已提交停止，请等待运行核对结果。')}>停止</Button>}
    {unknown(run.state)&&<Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>void perform('partner.reconcile',{run_id:run.id},'已核对原运行的本地退出证据，没有重新发送任务。')}>核对原运行</Button>}
    {unknown(run.state)&&run.result&&Object.keys(run.result).length>0&&<Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>void perform('partner.reconcile',{run_id:run.id,resolution:'accept_received_result'},'已核对原有官方结果，没有重新发送任务。')}>核对并接纳原结果</Button>}
  </div>
  <p className="wd-partners-note">{connection.route==='api'?'驾驶来源：DeepSeek API':'驾驶来源：官方账号'}{run.account_label?` · 账号：${run.account_label}`:''}{version?` · 端版本：${version}`:''}</p>
  {(run.project_id||run.workspace)&&<p className="wd-partners-note">项目：{run.project_id} {run.workspace}</p>}
  {run.prompt&&<details><summary>本次工作要求</summary><p className="wd-partners-output">{run.prompt}</p></details>}
  {run.output&&<div className="wd-partners-output">{run.output}</div>}{run.error&&<p className="wd-partners-error">{run.error}</p>}
  <Usage run={run}/>
  {run.explicit_local_tracking_closed===true&&<p className="wd-partners-note">本地追踪已结束，远端结果仍未知。不会自动重新发送。</p>}
  {unknown(run.state)&&run.result&&Object.keys(run.result).length>0&&<p className="wd-partners-note">已有官方结果。核对准确运行身份和本地退出证据后，可以接纳原结果；不会重新发送任务。</p>}
  {unknown(run.state)&&run.local_exit_confirmed===false&&<p className="wd-partners-note">尚未确认本地进程树退出，继续保留原运行。</p>}
  {unknown(run.state)&&run.local_exit_confirmed===true&&<div className="wd-partners-request"><p>本地进程退出已确认，远端请求结果仍未知。</p>{closing?<div role="group" aria-label="确认结束本地追踪"><p>保留远端结果未知，结束本地追踪。此操作不会重发任务，也不代表远端已取消或费用已退回。</p><div className="wd-partners-actions"><Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>setClosing(false)}>继续保留追踪</Button><Button disabled={busy} onClick={()=>void perform('partner.reconcile',{run_id:run.id,resolution:'close_local_tracking'},'已结束本地追踪，远端结果仍保留未知。',()=>setClosing(false))}>确认结束本地追踪</Button></div></div>:<Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>setClosing(true)}>保留远端结果未知，结束本地追踪</Button>}</div>}
  {!terminal(run.state)&&run.pending_requests?.map(request=><div className="wd-partners-request" key={request.id}><p>{request.message||(request.kind==='approval'?'官方端请求授权':'官方端等待补充输入')}</p>{!!(request.details||request.native||request.params)&&<details><summary>本次工具与参数</summary><pre>{text(request.details||request.native||request.params)}</pre></details>}
    {request.kind==='approval'?<div className="wd-partners-actions"><Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={()=>void perform('partner.approve',{run_id:run.id,request_id:request.id,decision:'deny'},'已返回拒绝意见。')}>拒绝</Button><Button disabled={blocked} onClick={()=>void perform('partner.approve',{run_id:run.id,request_id:request.id,decision:'allow'},'已返回本次授权。')}>允许本次操作</Button></div>:<form onSubmit={event=>{event.preventDefault();const value=input[request.id]?.trim();if(value)void perform('partner.input',{run_id:run.id,request_id:request.id,text:value},'补充内容已发送。',()=>setInput(prior=>({...prior,[request.id]:''})));}}><RequestInput request={request} value={input[request.id]||''} disabled={blocked} onChange={value=>setInput(prior=>({...prior,[request.id]:value}))}/><Button type="submit" disabled={blocked||!input[request.id]?.trim()}>发送补充</Button></form>}
  </div>)}
  <NativeEvents id={run.id}/>
  </div>;
}
