import { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { coreClient } from '../core/client';

type Policy = { revision: number; max_dispatches: number | null; max_concurrent: number | null; unknown_cost_policy: string };
type Entry = {id: string; source: string; state: string; outcome: string; policy_revision: number; tool_version?: string; partner_id?: string; observed_at: number};
type Budget = { policy: Policy; reserved_dispatches: number; settled_dispatches: number; entries: Entry[] };
type Amount = { known: number; known_records: number; unknown_records: number };
type Usage = { record_count: number; observed_at: number; tokens: Record<string, Amount>; has_more: boolean;
  groups: { id: string | null; records: number; known_tokens: number; unknown_token_records: number }[];
  cost: { known_model_cny: string | null; estimated_cny: string | null; unknown_records: number; price_basis: {version: string} };
  rows: { id: string; source: string; status: string; at: number; tokens: number | null; elapsed_seconds?: number; model_cost_cny?: string; estimate?: {amount: string} }[] };
const dimensionLabels = {task_id:'任务',attempt_id:'尝试',tool_version:'工具版本',partner_id:'伙伴',account_id:'账号'};
type Dimension = keyof typeof dimensionLabels;
const stateLabel: Record<string,string> = {RESERVED:'预留中',SETTLED:'已结算',RELEASED:'已释放'};
const displayError = (e: unknown) => e instanceof Error ? e.message : '项目预算读取失败。';

function BudgetDetails({ project }: {project: string}) {
  const [budget,setBudget] = useState<Budget>();
  const [usage,setUsage] = useState<Usage>();
  const [count,setCount] = useState('');
  const [concurrent,setConcurrent] = useState('');
  const [unpriced,setUnpriced] = useState('allow_unpriced');
  const [days,setDays] = useState('30');
  const [group,setGroup] = useState<Dimension>('tool_version');
  const [filter,setFilter] = useState('');
  const [page,setPage] = useState(1);
  const [version,refresh] = useState(0);
  const [saving,setSaving] = useState(false);
  const [failure,setFailure] = useState('');
  const [usageFailure,setUsageFailure] = useState('');
  const [message,setMessage] = useState('');
  useEffect(() => {
    const abort=new AbortController();
    setFailure('');
    coreClient.request<Budget>('v2/project-budget?' + new URLSearchParams({project_id:project}),{signal:abort.signal}).then(result=>{
      if(abort.signal.aborted)return;
      setBudget(result); setCount(String(result.policy.max_dispatches ?? '')); setConcurrent(String(result.policy.max_concurrent ?? '')); setUnpriced(result.policy.unknown_cost_policy);
    }).catch(e=>{if(!abort.signal.aborted)setFailure(displayError(e));});
    return ()=>abort.abort();
  },[project,version]);
  useEffect(() => {
    const abort=new AbortController();
    setUsageFailure('');setUsage(undefined);
    const args=new URLSearchParams({project_id:project,days,group_by:group,page:String(page)});
    if(filter)args.set(group,filter);
    coreClient.request<Usage>('v1/project-usage?'+args,{signal:abort.signal}).then(result=>{
      if(!abort.signal.aborted)setUsage(result);
    }).catch(e=>{if(!abort.signal.aborted)setUsageFailure(displayError(e));});
    return ()=>abort.abort();
  },[project,version,days,group,filter,page]);

  async function save() {
    if(!budget||saving)return;
    setSaving(true);setMessage('');setFailure('');
    try{
      await coreClient.command('budget.save',{project_id:project,revision:budget.policy.revision,
        max_dispatches:count===''?null:Number(count),max_concurrent:concurrent===''?null:Number(concurrent),unknown_cost_policy:unpriced,confirm:true});
      setMessage('项目预算已保存。');refresh(v=>v+1);
    }catch(e){setFailure(displayError(e));}finally{setSaving(false);}
  }
  const times=(at:number)=>new Date(at*1000).toLocaleString();
  return <>
    {failure && <p role="alert">{failure}</p>}
    {budget && <><p>累计已结算 {budget.settled_dispatches} 次 · 当前预留 {budget.reserved_dispatches} 次</p>
      <form className="wd-project-budget-fields" onSubmit={e=>{e.preventDefault();void save();}}>
        <label>累计派发上限<Input aria-label="累计派发上限" type="number" min={0} max={10000000} step={1} placeholder="不限" value={count} onChange={e=>setCount(e.target.value)}/></label>
        <label>同时预留上限<Input aria-label="同时预留上限" type="number" min={0} max={10000000} step={1} placeholder="不限" value={concurrent} onChange={e=>setConcurrent(e.target.value)}/></label>
        <label>费用无法判定时<NativeSelect aria-label="费用无法判定时" value={unpriced} onChange={e=>setUnpriced(e.target.value)}>
          <option value="allow_unpriced">允许派发并记录缺项</option><option value="block_unpriced">阻止派发</option></NativeSelect></label>
        <Button size="sm" disabled={saving}>保存项目预算</Button>
      </form>
      <p className="wd-settings-note">派发次数按项目累计。当前支持次数、并发与缺少费用依据时的准入策略；项目级金额和 Token 硬上限尚不可用。</p>
      {budget.entries.length>0 && <details><summary>最近的预算记录</summary><ul>{budget.entries.map(entry=><li key={entry.id}>{entry.tool_version || entry.partner_id || entry.source} · {stateLabel[entry.state] || entry.state} · {entry.outcome} · 预算版本 {entry.policy_revision} · {times(entry.observed_at)} · <code>{entry.id}</code></li>)}</ul></details>}
    </>}
    {message&&<p role="status">{message}</p>}
    <div className="wd-project-budget-fields">
      <label>统计区间<NativeSelect value={days} onChange={e=>{setDays(e.target.value);setPage(1);}}>{[1,7,30,90,365].map(v=><option value={v} key={v}>最近 {v} 天</option>)}</NativeSelect></label>
      <label>按项归集<NativeSelect value={group} onChange={e=>{setGroup(e.target.value as Dimension);setFilter('');setPage(1);}}>
        {Object.entries(dimensionLabels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</NativeSelect></label>
      {filter&&<Button type="button" variant="outline" size="sm" onClick={()=>{setFilter('');setPage(1);}}>清除筛选</Button>}
      <Button type="button" variant="outline" size="sm" onClick={()=>refresh(v=>v+1)}>刷新记录</Button>
    </div>
    {usageFailure&&<p role="alert">{usageFailure}</p>}
    {usage&&<>
      <p>实测 Token {usage.tokens.tokens.known_records?usage.tokens.tokens.known:'暂无'}（{usage.tokens.tokens.unknown_records} 条未知） · 已知缓存命中 {usage.tokens.cache_hit.known_records?usage.tokens.cache_hit.known:'暂无'} · 已知缓存未命中 {usage.tokens.cache_miss.known_records?usage.tokens.cache_miss.known:'暂无'}</p>
      <p>已知模型费用 {usage.cost.known_model_cny===null?'暂无':usage.cost.known_model_cny+' 元'} · 估算费用 {usage.cost.estimated_cny===null?'暂无':usage.cost.estimated_cny+' 元'} · 费用未知 {usage.cost.unknown_records} 条</p>
      <p className="wd-settings-note">观测于 {times(usage.observed_at)} · 估算价目 {usage.cost.price_basis.version} · 共 {usage.record_count} 条用量记录</p>
      {usage.groups.length>0&&<ul>{usage.groups.map(row=><li key={row.id||'none'}>{row.id?<button type="button" onClick={()=>{setFilter(row.id!);setPage(1);}}>{row.id}</button>:'未关联'+dimensionLabels[group]} · {row.records} 条 · 已知 {row.known_tokens} Token，{row.unknown_token_records} 条待核</li>)}</ul>}
      {usage.rows.length>0&&<details><summary>查阅原始用量来源</summary><ul>{usage.rows.map(row=><li key={row.source+'/'+row.id}><code>{row.id}</code> · {row.source} · {row.status} · {times(row.at)} · {row.tokens===null?'Token 未知':row.tokens+' Token'}{row.elapsed_seconds!=null?' · '+row.elapsed_seconds.toFixed(2)+' 秒（含排队）':''}</li>)}</ul>
        {page>1&&<Button type="button" variant="outline" size="sm" onClick={()=>setPage(p=>p-1)}>上一页</Button>}
        {usage.has_more&&<Button type="button" variant="outline" size="sm" onClick={()=>setPage(p=>p+1)}>下一页</Button>}
      </details>}
    </>}
  </>;
}

export function SettingsProjectBudget(){
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]);
  const [project,selectProject]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{
    const abort=new AbortController();
    async function load(){
      await coreClient.loadIdentity();
      const list:{id:string;name:string}[]=[];
      let cursor='0';
      do{
        const result=await coreClient.request<{projects:typeof list;cursor?:number|string;has_more:boolean}>('v2/projects?'+new URLSearchParams({limit:'100',cursor}),{signal:abort.signal});
        list.push(...result.projects);cursor=result.has_more&&result.cursor?String(result.cursor):'';
      }while(cursor);
      if(!abort.signal.aborted){setProjects(list);selectProject(list[0]?.id||'');}
    }
    void load().catch(e=>{if(!abort.signal.aborted)setError(displayError(e));});
    return ()=>abort.abort();
  },[]);
  return <section aria-label="项目预算与用量" className="wd-settings-group wd-settings-card wd-project-budget">
    <h2>项目预算与用量</h2>
    <label>项目<NativeSelect aria-label="预算项目" value={project} onChange={e=>selectProject(e.target.value)}>{projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</NativeSelect></label>
    {error&&<p role="alert">{error}</p>}
    {project&&<BudgetDetails key={project} project={project}/>}
  </section>;
}
