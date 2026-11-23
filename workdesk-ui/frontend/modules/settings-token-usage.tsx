import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight } from 'lucide-react';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import { formatMoney } from '../lib/format';
import './settings-token-usage.css';

type Tokens = { input: number | null; cache_hit: number | null; cache_miss: number | null; output: number | null; tokens: number | null };
type Counts = Tokens & { calls: number; native: number; records: number; cache_receipts: number; estimated_records: number; amount: string };
type Bucket = Counts & { date: string };
type Call = Tokens & { id: string; at: number; provider: string; model: string; source: string; status: string;
  estimate: { amount: string; period: 'peak' | 'off_peak'; time_basis: string; currency: string } | null };
type Report = { rate_basis?: {version:string;reference_date:string;source:string}; days: number; provider: string; page: number; page_size: number; count: number;
  date_first: string; date_last: string; totals: Counts; daily: Bucket[];
  models: (Counts & { provider: string; model: string })[]; rows: Call[] };
type View = 'daily' | 'weekly' | 'cumulative';

const providers = { all: '全部厂商', codex: 'Codex', deepseek: 'DeepSeek', qwen: 'Qwen' };
const count = new Intl.NumberFormat('zh-CN');
const clock = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' });
const dayMs = 86400000;
const fields = [['tokens','总 Tokens'], ['cache_hit','缓存命中输入'], ['cache_miss','缓存未命中输入'], ['output','输出 Tokens']] as const;
const token = (value?: number | null) => value == null ? '—' : count.format(value);
const providerName = (key: string) => providers[key as keyof typeof providers] || key;
const shortDate = (date: string) => date.slice(5).replace('-', '/');
const iso = (time: number) => new Date(time).toISOString().slice(0, 10);
const price = (amount: string, currency = 'CNY') => formatMoney(amount, currency);
const beijingTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', weekday: 'short',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
type ClockSample = { now: number; status: 'synced' | 'estimated' | 'local'; source: string; last_synced_at: number | null };

function BeijingClock() {
  const [sample,setSample] = useState<ClockSample | null>(null);
  const [now,setNow] = useState(Date.now);
  useEffect(()=>{
    const controller = new AbortController();
    let base = Date.now(), start = performance.now(), reading = false;
    const tick = () => setNow(base + performance.now() - start);
    async function sync() {
      if (reading || document.hidden) return;
      reading = true;
      try {
        const response = await fetch('api/v1/settings-network-time', {signal:controller.signal,cache:'no-store'});
        if (!response.ok) throw Error();
        const result: ClockSample = await response.json();
        if (controller.signal.aborted) return;
        base = result.now * 1000;
        start = performance.now();
        setSample(result);
      } catch {
        if (controller.signal.aborted) return;
        base = Date.now();
        start = performance.now();
        setSample({now:base/1000,status:'local',source:'本机',last_synced_at:null});
      } finally { reading = false; }
      tick();
    }
    void sync();
    const ticker = window.setInterval(tick,1000);
    const resync = window.setInterval(sync,60000);
    document.addEventListener('visibilitychange',sync);
    return () => {controller.abort();window.clearInterval(ticker);window.clearInterval(resync);document.removeEventListener('visibilitychange',sync);};
  },[]);
  const label = !sample ? '校时中' : sample.status === 'synced' ? '已联网校时'
    : sample.status === 'estimated' ? '沿用上次校时' : '联网校时失败，使用本机时间';
  return <span className="wd-token-clock" title={sample?.last_synced_at
    ? sample.source+' · 最近校时 '+beijingTime.format(sample.last_synced_at*1000) : label}>
    北京时间 {beijingTime.format(now)} · {label}</span>;
}
const dayLabel = (date: string, value?: Bucket) =>
  date + (value?.tokens != null ? ' · ' + token(value.tokens) + ' Tokens' : ' · 无 Token 回执');
const sources: Record<string, string> = {
  api_chat: 'API Chat', gateway: 'Work API', search: '搜索 API',
  deepseek_native: '原生模型响应', qwen_native: '原生回合', codex_native: '原生回合',
};
const statuses: Record<string, string> = {
  COMPLETE: '完成', COMPLETED: '完成', FAILED: '失败', INTERRUPTED: '已停止',
  STOPPED_RUNTIME: '已停止', RUNNING: '进行中', SEND_INTENT: '已发起', SEND_UNKNOWN: '待核对',
};
const timeBasis: Record<string,string> = {
  provider_created:'官方响应创建时间', request_sent:'请求发送时间', request_created:'请求登记时间',
  turn_created:'历史会话发起时间', response_observed:'原生响应回执记录时间',
};

function Metrics({ items }: { items: [string, string, string?][] }) {
  return <div className="wd-token-metrics">
    {items.map(([label, value, exact]) => <div className="wd-settings-card wd-token-metric" key={label}>
      <span>{label}</span><strong title={exact}>{value}</strong>
    </div>)}
  </div>;
}

function Heatmap({ report }: { report: Report }) {
  const byDate = new Map(report.daily.map(row => [row.date,row]));
  const [preview,setPreview] = useState<string | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = scroll.current;
    if (container) container.scrollLeft = container.scrollWidth;
  }, []);
  const current = preview || report.daily.at(-1)?.date || report.date_last;
  const start = Date.parse(report.date_first), end = Date.parse(report.date_last);
  const firstMonday = start - ((new Date(start).getUTCDay() + 6) % 7) * dayMs;
  const weeks = Array.from({length: Math.ceil((end - firstMonday + dayMs) / (dayMs * 7))}, (_,i) => firstMonday + i * 7 * dayMs);
  const magnitude = Math.max(1,...report.daily.map(row=>row.tokens??0));
  return <div className="wd-token-plot">
    <div className="wd-token-scroll" ref={scroll}>
      <div className="wd-token-calendar" aria-label="每日 Token 活动图">
        {weeks.map((week,i) => {
          const days = Array.from({length:7}, (_,j)=>week+j*dayMs);
          const monthStart = days.find(day=>day>=start&&day<=end&&new Date(day).getUTCDate()===1);
          const month = monthStart!=null||i===0 ? new Date(monthStart??start).getUTCMonth()+1 : null;
          return <div className="wd-token-week" key={week}>
            {days.map(day=>{
              const date=iso(day), value=byDate.get(date);
              const total=value?.tokens??0, level=total>0?Math.max(1,Math.ceil(4*Math.sqrt(total/magnitude))):0;
              return day < start || day > end ? <div key={day} className="wd-token-blank"/> :
                <button type="button" key={day} data-level={level} data-day={date}
                  className="wd-token-square" title={dayLabel(date,value)} aria-label={dayLabel(date,value)}
                  aria-pressed={date===current} tabIndex={value||date===current?0:-1}
                  onMouseEnter={()=>setPreview(date)} onFocus={()=>setPreview(date)} onClick={()=>setPreview(date)}>
                  <span aria-hidden="true"/>
                </button>;
            })}
            <span className="wd-token-month">{month==null?'':month+'月'}</span>
          </div>;
        })}
      </div>
    </div>
    <div className="wd-token-plot-readout">
      <strong>{dayLabel(current,byDate.get(current))}</strong>
      <span className="wd-token-scale" aria-label="颜色由少到多">少 {[0,1,2,3,4].map(level=><i data-level={level} key={level}/>)} 多</span>
    </div>
    {byDate.get(current)?.tokens!=null&&<p className="wd-token-detail">
      命中 {token(byDate.get(current)?.cache_hit)} · 未命中 {token(byDate.get(current)?.cache_miss)} · 输出 {token(byDate.get(current)?.output)}
    </p>}
  </div>;
}

type Bar = { label: string; value: number; description: string };
function Bars({ bars, label, unit }: { bars: Bar[]; label: string; unit: 'Tokens' | 'CNY' | '条' }) {
  const [selected, setSelected] = useState<number | null>(null);
  const observedMax=Math.max(0,...bars.map(bar=>Number.isFinite(bar.value)?bar.value:0));
  const max=observedMax||1;
  const tick=(value:number)=>new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:4}).format(value);
  const current=bars[selected??bars.map(bar=>bar.value>0).lastIndexOf(true)];
  return <div className="wd-token-plot">
    <p className="wd-token-axis-unit">单位：{unit}{unit==='CNY'?'（估算）':''}</p>
    <div className="wd-token-bar-layout">
    <div className="wd-token-y-axis" aria-label={'纵轴，单位 '+unit}>
      <span>{tick(max)}</span><span>{tick(max/2)}</span><span>0</span>
    </div>
    <div className="wd-token-scroll">
      <div className="wd-token-bars" style={{minWidth: Math.max(252,bars.length*11)}} aria-label={label}>
        {bars.map((bar,i)=><button type="button" className="wd-token-bar" title={bar.description}
          aria-label={bar.description} aria-pressed={selected===i} key={i}
          onClick={()=>setSelected(i)} onFocus={()=>setSelected(i)} onMouseEnter={()=>setSelected(i)}>
          <span style={{height:bar.value>0?bar.value/max*100+'%':'2px'}} data-empty={bar.value===0}/>
        </button>)}
      </div>
    </div>
    </div>
    <div className="wd-token-plot-readout"><span>{bars[0]?.label}</span><strong>{current?.description || '暂无记录'}</strong><span>{bars.at(-1)?.label}</span></div>
  </div>;
}

function tokenBars(report: Report, cumulative: boolean) {
  const start=Date.parse(report.date_first), end=Date.parse(report.date_last);
  const monday=start-((new Date(start).getUTCDay()+6)%7)*dayMs;
  const weeks=new Map<number,number>();
  for (const row of report.daily) {
    if (row.tokens==null) continue;
    const index=Math.floor((Date.parse(row.date)-monday)/(7*dayMs));
    weeks.set(index,(weeks.get(index)??0)+row.tokens);
  }
  let running:number|undefined;
  return Array.from({length:Math.ceil((end-monday+dayMs)/(7*dayMs))},(_,i)=>{
    const from=iso(Math.max(start,monday+i*7*dayMs)), to=iso(Math.min(end,monday+(i*7+6)*dayMs));
    const weekly=weeks.get(i);
    if (weekly!=null) running=(running??0)+weekly;
    const value=cumulative?running:weekly, label=shortDate(from);
    return {label,value:value??0,description:label+'–'+shortDate(to)+' · '+(value==null?'无 Token 回执':
      (cumulative?'累计 ':'')+token(value)+' Tokens')};
  });
}

function TokenActivity({ report }: { report: Report }) {
  const [view,setView]=useState<View>('daily');
  return <>
    <Metrics items={fields.map(([key,label])=>[label,token(report.totals[key])])}/>
    <section className="wd-settings-card wd-token-section" aria-label="Token 活动图">
      <div className="wd-token-heading"><h3>活动趋势</h3>
        <div className="wd-settings-theme" role="group" aria-label="活动统计方式">
          {([['daily','每天'],['weekly','每周'],['cumulative','累计总量']] as const).map(([key,label])=>
            <Button key={key} type="button" variant="ghost" aria-pressed={view===key} onClick={()=>setView(key)}>{label}</Button>)}
        </div>
      </div>
      {view==='daily'?<Heatmap key={report.provider+report.days} report={report}/>:
        <Bars key={view+report.provider+report.days} bars={tokenBars(report,view==='cumulative')} label={view==='weekly'?'每周 Token 柱状图':'累计 Token 柱状图'} unit="Tokens"/>}
    </section>
    <section className="wd-settings-card wd-token-section" aria-label="按模型统计">
      <h3>按模型统计</h3>
      {report.models.length?<div className="wd-token-scroll"><table className="wd-token-table">
        <thead><tr><th>厂商 / 型号</th><th>输入</th>{fields.slice(1).map(([key,label])=><th key={key}>{label}</th>)}<th>总计</th></tr></thead>
        <tbody>{report.models.map(row=><tr key={row.provider+row.model}><th scope="row">{providerName(row.provider)} / {row.model}</th>
          <td>{token(row.input)}</td>{fields.slice(1).map(([key])=><td key={key}>{token(row[key])}</td>)}<td>{token(row.tokens)}</td></tr>)}</tbody>
      </table></div>:<p className="wd-token-detail">这一周期暂无用量记录。</p>}
    </section>
    <p className="wd-settings-note">缓存拆分覆盖 {count.format(report.totals.cache_receipts)} / {count.format(report.totals.records)} 条用量记录。缺失的字段显示为「—」。</p>
  </>;
}

function CallDetails({ report, setPage }: { report: Report; setPage: (page: number) => void }) {
  const [graph,setGraph]=useState<'calls'|'cost'>('calls');
  const maxPage=Math.max(1,Math.ceil(report.count/report.page_size));
  const days=Array.from({length:report.days},(_,i)=>iso(Date.parse(report.date_first)+i*dayMs));
  const daily=new Map(report.daily.map(day=>[day.date,day]));
  const bars=days.map(date=>{
    const day=daily.get(date), label=shortDate(date);
    if(graph==='cost') return {label,value:Number(day?.amount??0),description:label+' · '+(day?.estimated_records?price(day.amount)+'（原始估算 CNY '+day.amount+'）':'无估算记录')};
    const value=(day?.calls??0)+(day?.native??0);
    return {label,value,description:label+' · '+(day?token(day.calls)+' 次 API 请求 / '+token(day.native)+' 条原生用量':'无调用记录')};
  });
  return <>
    <Metrics items={[
      ['API 请求', count.format(report.totals.calls)], ['原生用量记录', count.format(report.totals.native)],
      ['总 Tokens',token(report.totals.tokens)],
      ['DeepSeek Token 费用估算',report.totals.estimated_records ? price(report.totals.amount) : '—',report.totals.estimated_records ? '原始估算 CNY '+report.totals.amount : undefined],
    ]}/>
    <section className="wd-settings-card wd-token-section" aria-label="调用消耗图">
      <div className="wd-token-heading"><h3>{graph==='calls'?'每日调用记录':'每日 Token 费用估算'}</h3>
        <div className="wd-settings-theme" role="group" aria-label="调用图表">
          <Button type="button" variant="ghost" aria-pressed={graph==='calls'} onClick={()=>setGraph('calls')}>调用记录</Button>
          <Button type="button" variant="ghost" aria-pressed={graph==='cost'} onClick={()=>setGraph('cost')}>估算费用</Button>
        </div>
      </div>
      <Bars key={graph+report.provider+report.days} bars={bars} label={graph==='calls'?'每日调用记录':'每日估算费用'} unit={graph==='calls'?'条':'CNY'}/>
    </section>
    <section className="wd-settings-card wd-token-section" aria-label="调用明细">
      <h3>调用明细</h3>
      {report.rows.length?<div className="wd-token-scroll"><table className="wd-token-table wd-token-call-table">
        <thead><tr><th>时间</th><th>厂商 / 型号</th><th>渠道 / 状态</th><th>命中输入</th><th>未命中输入</th><th>输出</th><th>总 Tokens</th><th>参考费用</th></tr></thead>
        <tbody>{report.rows.map(row=><tr key={row.source+row.id}>
          <td><time dateTime={new Date(row.at*1000).toISOString()}>{clock.format(row.at*1000)}</time></td>
          <th scope="row">{providerName(row.provider)} / {row.model}</th><td>{sources[row.source]||row.source} · {statuses[row.status]||'待核对'}</td>
          <td>{token(row.cache_hit)}</td><td>{token(row.cache_miss)}</td><td>{token(row.output)}</td>
          <td>{token(row.tokens)}</td><td title={row.estimate ? '原始估算 '+row.estimate.currency+' '+row.estimate.amount+' · '+(timeBasis[row.estimate.time_basis] || '') : undefined}>{row.estimate?<>{price(row.estimate.amount,row.estimate.currency)} <small>{row.estimate.period==='peak'?'高峰':'空闲'}</small></>:'—'}</td>
        </tr>)}</tbody>
      </table></div>:<p className="wd-token-detail">这一周期暂无调用记录。</p>}
      {maxPage>1&&<div className="wd-token-page">
        <Button type="button" variant="outline" className="wd-settings-button" disabled={report.page<=1} onClick={()=>setPage(report.page-1)}>上一页</Button>
        <span>第 {report.page} / {maxPage} 页</span>
        <Button type="button" variant="outline" className="wd-settings-button" disabled={report.page>=maxPage} onClick={()=>setPage(report.page+1)}>下一页</Button>
      </div>}
    </section>
    <div className="wd-settings-note">估算覆盖 {count.format(report.totals.estimated_records)} 条 DeepSeek 模型 Token 用量，按各条记录的北京时间计价。{report.rate_basis && <details><summary>计价依据 · {report.rate_basis.version}</summary><div>当前高峰为周一至周五 09:00–12:00、14:00–18:00，中国法定节假日除外。其他时段执行空闲价。优先采用官方响应中的创建时间；历史记录按可用的请求或回执时间估算。费率核对日 {report.rate_basis.reference_date}。<a href={report.rate_basis.source} target="_blank" rel="noreferrer">DeepSeek 定价来源</a></div></details>}实际扣款请查看 <a href="https://platform.deepseek.com/usage" target="_blank" rel="noreferrer">DeepSeek 官方用量 <ArrowUpRight aria-hidden="true"/></a>。</div>
  </>;
}

export function SettingsTokenUsage({ mode }: { mode: 'activity' | 'calls' }) {
  const [provider,setProvider]=useState('all');
  const [days,setDays]=useState(mode==='activity'?365:30);
  const [page,setPage]=useState(1);
  const [report,setReport]=useState<Report|null>(null);
  const [error,setError]=useState(false);
  const [retry,setRetry]=useState(0);
  useEffect(()=>{
    const control=new AbortController();
    setError(false);
    fetch('api/v1/settings-token-usage?'+new URLSearchParams({provider,days:String(days),page:String(page)}),{signal:control.signal})
      .then(response=>{if(!response.ok)throw Error();return response.json() as Promise<Report>;})
      .then(value=>{if(!control.signal.aborted)setReport(value);})
      .catch(()=>{if(!control.signal.aborted)setError(true);});
    return ()=>control.abort();
  },[provider,days,page,retry]);
  const current=report?.provider===provider&&report.days===days&&report.page===page?report:null;
  return <div className="wd-token-report" aria-busy={!current&&!error}>
    <div className="wd-token-heading">
      <div className="wd-token-heading"><Button asChild size="sm" variant="outline" className="wd-settings-button">
        <a href="#settings/usage"><ArrowLeft aria-hidden="true"/>账户额度</a></Button>
        <h2>{mode==='activity'?'Token 活动':'调用消耗'}</h2>
        {mode==='calls' && <BeijingClock/>}
      </div>
      <div className="wd-token-filters">
        <NativeSelect aria-label="模型厂商" size="sm" value={provider} onChange={e=>{setProvider(e.target.value);setPage(1);}}>
          {Object.entries(providers).map(([key,label])=><option key={key} value={key}>{label}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="用量周期" size="sm" value={days} onChange={e=>{setDays(Number(e.target.value));setPage(1);}}>
          {[7,30,90,365].map(value=><option key={value} value={value}>{value===365?'近一年':'近 '+value+' 天'}</option>)}
        </NativeSelect>
      </div>
    </div>
    {!current?(error?<p className="wd-token-detail" role="status">暂时无法读取记录。 <Button type="button" variant="outline" onClick={()=>setRetry(retry+1)}>重试</Button></p>:
      <p className="wd-token-detail" role="status">正在读取本地用量…</p>):
      mode==='activity'?<TokenActivity report={current}/>:<CallDetails report={current} setPage={setPage}/>}
  </div>;
}
