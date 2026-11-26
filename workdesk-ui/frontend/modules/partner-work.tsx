import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { Textarea } from '../components/ui/textarea';
import { coreClient } from '../core/client';
import { PartnerRunList, partnerRunning, partnerStateLabel, type PartnerRun } from '../core/partner-runtime';
import type { PartnersSnapshot } from './partners';
import './partner-work.css';

type Project = {id:string;name:string;root:string};
type Receipt = {run_id?:string};
const errorText=(value:unknown)=>value instanceof Error?value.message:'操作未完成，请核对当前状态。';
export function PartnerWork({runId}: {runId?:string}) {
  const [data,setData]=useState<PartnersSnapshot|null>(null),[projects,setProjects]=useState<Project[]>([]),[run,setRun]=useState<PartnerRun|null>(null);
  const [connection,setConnection]=useState(''),[project,setProject]=useState(''),[prompt,setPrompt]=useState(''),[lookup,setLookup]=useState('');
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[notice,setNotice]=useState(''),[error,setError]=useState('');
  const [pending,setPending]=useState(()=>coreClient.uncertainCommands());
  const alive=useRef(false),lock=useRef(false),reads=useRef(coreClient.createReadScope());
  const refresh=useCallback(async()=>{
    const read=reads.current.begin();
    try {
      const [snapshot,record]=await Promise.all([coreClient.request<PartnersSnapshot>('v2/partners',{signal:read.signal}),runId?coreClient.request<{run:PartnerRun}>(`v2/partner-runs/${encodeURIComponent(runId)}`,{signal:read.signal}):Promise.resolve(null)]);
      if(!alive.current||!read.isCurrent())return;
      setData(snapshot);setRun(record?.run||null);setError('');
    } catch(e){if(alive.current&&read.isCurrent())setError(errorText(e));}
    finally{if(alive.current&&read.isCurrent())setLoading(false);}
  },[runId]);
  useEffect(()=>{
    let active=true;alive.current=true;
    void(async()=>{try{await coreClient.loadIdentity();if(!active)return;setPending(coreClient.uncertainCommands());const value=await coreClient.request<{projects:Project[]}>('v2/projects?limit=200');if(!active)return;setProjects(value.projects);await refresh();}catch(e){if(active){setError(errorText(e));setLoading(false);}}})();
    return()=>{active=false;alive.current=false;reads.current.cancel();};
  },[refresh]);
  const recent=(data?.runs||[]).filter(item=>!item.mode||item.mode==='run');
  const running=run?partnerRunning(run.state):recent.some(item=>partnerRunning(item.state));
  useEffect(()=>{
    let timer:ReturnType<typeof setTimeout>|undefined;
    if(running&&!document.hidden)timer=setTimeout(()=>void refresh(),2500);
    const visible=()=>{if(document.hidden){if(timer)clearTimeout(timer);reads.current.cancel();}else void refresh();};
    document.addEventListener('visibilitychange',visible);
    return()=>{if(timer)clearTimeout(timer);document.removeEventListener('visibilitychange',visible);};
  },[data,run,running,refresh]);
  const blocked=busy||!data||pending.length>0;
  async function perform(kind:string,payload:Record<string,unknown>,success:string,after?:()=>void) {
    if(lock.current||busy||!data||(pending.length&&!kind.endsWith('.reconcile')))return;
    lock.current=true;setBusy(true);setNotice('');setError('');
    try {
      const value=await coreClient.command<Receipt>(kind,payload);
      if(!alive.current)return;
      setNotice(success);after?.();
      if(kind==='partner.run'&&value.receipt.run_id){location.hash='#work/partners/run/'+encodeURIComponent(value.receipt.run_id);return;}
      await refresh();
    }catch(e){if(alive.current)setError(errorText(e));}
    finally{lock.current=false;if(alive.current){setBusy(false);setPending(coreClient.uncertainCommands());}}
  }
  async function receipt(id:string){
    if(lock.current)return;lock.current=true;setBusy(true);
    try{const v=await coreClient.receipt(id);if(alive.current){setNotice(v.state==='RECORDED'?'原操作回执已找到。':'原操作仍待核对，不会自动重发。');setPending(coreClient.uncertainCommands());await refresh();}}
    catch(e){if(alive.current)setError(errorText(e));}finally{lock.current=false;if(alive.current)setBusy(false);}
  }
  const selected=data?.connections.find(item=>item.id===connection);
  const apiName=(item:{api_provider?:string;driver_provider?:string;account_id:string})=>{const account=data?.accounts.find(account=>account.id===item.account_id),provider=item.api_provider||item.driver_provider||account?.api_provider||'deepseek';return data?.api_providers?.find(item=>item.id===provider)?.name||provider;};
  const release=data?.components.find(item=>item.id===selected?.release_id),account=data?.accounts.find(item=>item.id===selected?.account_id),selectedProject=projects.find(item=>item.id===project);
  const available=selected&&selected.state!=='DISABLED'&&release&&['AVAILABLE','READY','INSTALLED','VERIFIED'].includes(release.state);
  return <section className="wd-partners wd-partner-work" data-partner-work>
    <header className="wd-partner-work-header"><div><h1>独立 Work · 伙伴连接</h1><p className="wd-partners-note">选择已保存的官方工作端组合，在指定项目中工作。</p></div><div className="wd-partners-actions"><Button asChild variant="outline"><a href="#work">返回独立 Work</a></Button><Button asChild variant="outline"><a href="#settings/accounts">账号与模型</a></Button><Button variant="outline" disabled={busy||loading} onClick={()=>void refresh()}>刷新状态</Button></div></header>
    {error&&<p role="alert" className="wd-partners-error">{error}</p>}{notice&&<p role="status" className="wd-partners-note">{notice}</p>}
    {pending.length>0&&<div className="wd-partners-request" role="alert"><p>有操作回执待核对。新工作暂不发送；核对不会重复执行。</p>{pending.map(item=><Button key={item.id} variant="outline" disabled={busy} onClick={()=>void receipt(item.id)}>核对原操作 · {item.id.slice(0,8)}</Button>)}</div>}
    {loading&&<p role="status">正在读取伙伴连接与项目…</p>}
    {!runId&&data&&<form className="wd-partner-work-composer" onSubmit={event=>{event.preventDefault();if(available&&selectedProject&&prompt.trim())void perform('partner.run',{connection_id:connection,connection_revision:selected.revision,project_id:project,prompt:prompt.trim()},'工作已发送至所选官方端。');}}>
      <div className="wd-partner-work-fields"><label>使用伙伴连接<NativeSelect aria-label="使用伙伴连接" value={connection} disabled={blocked} onChange={event=>setConnection(event.currentTarget.value)}><option value="">请选择已保存的连接</option>{data.connections.map(item=><option key={item.id} value={item.id}>{data.partners.find(p=>p.id===item.partner_id)?.name||item.partner_id} · {item.route==='api'?apiName(item)+' API':'官方账号'} · {data.accounts.find(a=>a.id===item.account_id)?.label||'账号待核对'} · {partnerStateLabel(item.state)}</option>)}</NativeSelect></label>
      <label>项目<NativeSelect aria-label="伙伴工作项目" value={project} disabled={blocked} onChange={event=>setProject(event.currentTarget.value)}><option value="">请选择已登记项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name||item.id}</option>)}</NativeSelect></label></div>
      {selected&&<div className="wd-partner-work-selection"><p>{data.partners.find(p=>p.id===selected.partner_id)?.name||selected.partner_id} ＋ {selected.route==='api'?apiName(selected)+' API':'官方账号'} · {selected.model||'官方默认模型'} · {account?.label} · 版本 {release?.version||'未准备'}</p><p className="wd-partners-note">{selectedProject?.root||'请明确选择本次项目。'}{!available?' · 此连接暂不可派发，请检查组件和连接状态。':''}</p></div>}
      <label>工作要求<Textarea aria-label="伙伴工作要求" value={prompt} disabled={blocked} onChange={event=>setPrompt(event.currentTarget.value)} placeholder="描述本次要完成的工作…"/></label>
      {selected?.route==='api'&&<p className="wd-partners-note">使用所选 API 凭据，会产生实际费用。</p>}
      <div className="wd-partners-actions"><Button type="submit" disabled={blocked||!available||!selectedProject||!prompt.trim()}>开始伙伴工作</Button></div>
    </form>}
    {runId&&run&&<><div className="wd-partners-actions"><Button asChild variant="outline"><a href="#work/partners">开始另一项工作</a></Button><span className="wd-partners-note">同一运行记录 · {run.id}</span></div><PartnerRunList runs={[run]} busy={busy} blocked={blocked} perform={perform} apiProviders={data?.api_providers}/></>}
    <section className="wd-partner-work-history"><h2>伙伴工作记录</h2>{!recent.length&&<p className="wd-partners-note">尚无伙伴工作记录。登录和本地检查不会作为工作列入。</p>}<ul>{recent.map(item=><li key={item.id}><a href={'#work/partners/run/'+encodeURIComponent(item.id)}>{item.prompt?.slice(0,100)||data?.partners.find(p=>p.id===item.partner_id)?.name||item.id}</a><span>{partnerStateLabel(item.state)}</span></li>)}</ul>
      <form onSubmit={event=>{event.preventDefault();if(lookup.trim())location.hash='#work/partners/run/'+encodeURIComponent(lookup.trim());}}><label>按准确运行标识打开<Input aria-label="伙伴运行标识" value={lookup} onChange={event=>setLookup(event.currentTarget.value)}/></label><Button type="submit" variant="outline" disabled={!lookup.trim()}>打开原运行</Button></form>
    </section>
  </section>;
}
