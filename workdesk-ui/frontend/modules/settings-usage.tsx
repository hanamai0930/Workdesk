import { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import { formatMoney } from '../lib/format';
import type { APIProvider, Partner } from './partners';
import { SettingsProjectBudget } from './settings-project-budget';
import './settings-usage.css';

type Quota = {
  provider: string;
  legacy?:boolean;
  partner_id?:string;api_provider?:string;account_id?:string;account_label?:string;route?:'account'|'api';run_id?:string;
  label: string;
  remaining: number | null;
  unit: string | null;
  official_at: number | null;
  manual_at: number | null;
  refresh?: { connection_id: string; connection_revision: string } | null;
  refresh_status?: string | null;
  balances?: { currency: string; total_balance: string; granted_balance: string; topped_up_balance: string }[];
  balance_available?: boolean | null;
  buckets?: { limit_id: string; name: string | null; plan_type: string | null;
    primary: QuotaWindow | null; secondary: QuotaWindow | null;
    credits: { unlimited: boolean | null; balance: number | null } | null }[];
  usage?: { input_tokens: number | null; output_tokens: number | null };
  console_sources?: ('free' | 'cash')[];
  console_snapshots?: QwenQuota[];
};
type QuotaWindow = { duration_minutes: number | null; remaining_percent: number | null; resets_at: number | null };
export type Usage = {
  quotas: Quota[];
  partners?:Partner[];api_providers?:APIProvider[];
  model_summaries?:{id:string;model:string;provider:string;source_label:string;tokens:number|null;partial:boolean}[];
};

const count = new Intl.NumberFormat('zh-CN');
const date = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });

const quotaDate = (seconds: number) => date.format(new Date(seconds * 1000));
function windowLabel(minutes: number | null) {
  if (minutes === 10080) return '每周限额';
  if (!minutes) return '订阅限额';
  return minutes % 60 ? `${minutes} 分钟限额` : `${minutes / 60} 小时限额`;
}

type QwenQuota =
  | { source: 'free'; observed_at: number; rows: { model: string; remaining: number | null; total: number | null; expires: string | null }[] }
  | { source: 'cash'; observed_at: number; rows: { currency: string; available: string; cash: string } };

type Source = QwenQuota['source'];
type Connection = 'READY' | 'CONFIGURED' | 'UNKNOWN' | 'PENDING' | 'CONNECTED' | 'FAILED' | 'CHECKING' | 'CHECK_FAILED';
type Connections = Partial<Record<Source, Connection>>;

function QwenQuotaDetails({ sources, snapshots, refresh }: {
  sources: NonNullable<Quota['console_sources']>; snapshots: QwenQuota[]; refresh: () => void;
}) {
  const [states, setStates] = useState<Connections>({});
  const [busy, setBusy] = useState<Partial<Record<Source, 'login' | 'query' | 'cancel'>>>({});
  const [messages, setMessages] = useState<Partial<Record<Source, string>>>({});
  const [revision, setRevision] = useState(0);
  const [statusError, setStatusError] = useState(false);
  const available = sources.length > 0;
  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    let timer: number;
    let loading = false;
    async function read() {
      if (loading || controller.signal.aborted) return;
      window.clearTimeout(timer);
      loading = true;
      try {
        const reply = await fetch('api/v1/settings-qwen-quota-status', { signal: controller.signal, cache: 'no-store' });
        if (!reply.ok) throw new Error();
        const result: Connections = await reply.json();
        if (controller.signal.aborted) return;
        setStates(result);
        setStatusError(false);
        if (Object.values(result).some(state => state === 'PENDING' || state === 'CHECKING')) {
          timer = window.setTimeout(read, 1500);
        }
      } catch {
        if (!controller.signal.aborted) setStatusError(true);
      } finally {
        loading = false;
      }
    }
    void read();
    window.addEventListener('focus', read);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.removeEventListener('focus', read);
    };
  }, [available, revision]);
  const free = snapshots.find(row => row.source === 'free');
  const cash = snapshots.find(row => row.source === 'cash');
  async function act(source: Source, action: 'login' | 'query' | 'cancel') {
    setMessages(previous => ({ ...previous, [source]: '' }));
    setBusy(previous => ({ ...previous, [source]: action }));
    try {
      const response = await fetch('api/v1/settings-qwen-quota' + (action === 'query' ? '' : '-' + action), { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '暂时无法连接官方额度。');
      if (action !== 'cancel') setStates(previous => ({ ...previous, [source]: action === 'login' ? 'PENDING' : 'CONNECTED' }));
      refresh();
    } catch (error) {
      setMessages(previous => ({ ...previous, [source]: error instanceof Error ? error.message : '暂时无法连接官方额度。' }));
    } finally {
      setBusy(previous => ({ ...previous, [source]: undefined }));
      setRevision(previous => previous + 1);
    }
  }
  return <>
    {sources.map(source => {
      const isFree = source === 'free';
      const state = states[source];
      const waiting = state === 'PENDING' || busy[source] === 'login';
      const reading = state === 'CHECKING' || busy[source] === 'query';
      const connected = state === 'CONNECTED' || state === 'CONFIGURED';
      const cancelling = busy[source] === 'cancel';
      const label = cancelling ? '正在取消授权…' : waiting ? (isFree ? '百炼授权中…' : '账单授权中…')
        : reading ? (isFree ? '正在查询免费额度…' : '正在查询余额…')
        : state === 'CONNECTED' ? (isFree ? '百炼已登录' : '账单已连接')
        : state === 'CONFIGURED' ? (isFree ? '百炼已授权' : '账单已配置')
        : !state ? (statusError ? '连接状态未读取' : '正在读取连接状态…')
        : state === 'UNKNOWN' ? '连接状态待核对'
        : state === 'FAILED' || state === 'CHECK_FAILED' ? '连接需要核对'
        : isFree ? '百炼尚未登录' : '账单账号尚未连接';
      const hint = messages[source] || (waiting ? '请在默认浏览器完成授权。如果未打开网页，可取消后重试。'
        : state === 'FAILED' ? '授权未完成，可重新连接。'
        : state === 'CHECK_FAILED' ? '上次查询失败，可重试查询或重新授权。'
        : state === 'CONFIGURED' ? '已找到本机授权，可查询以验证连接。'
        : state === 'UNKNOWN' ? '暂时无法确认本机授权，可先查询验证。'
        : state === 'READY' ? '可先连接账号；如已在本机官方工具授权，也可直接查询验证。' : '');
      return <div key={source} role="group" aria-label={isFree ? '百炼免费额度' : '阿里云账单账号'} className="wd-settings-quota-bucket">
        <div className="wd-settings-quota-actions">
          <span className="wd-settings-quota-status" role="status" data-connected={connected || undefined} aria-busy={waiting || reading || cancelling}>
            {label}</span>
          {!connected && <Button type="button" variant="outline" className="wd-settings-button"
            disabled={waiting || reading || cancelling || (!state && !statusError)} onClick={() => act(source, 'login')}>
            {isFree ? '登录百炼' : '连接账单账号'}</Button>}
          <Button type="button" variant="ghost" className="wd-settings-button" disabled={waiting || reading || cancelling} onClick={() => act(source, 'query')}>
            {isFree ? '查询 Token 余量' : '查询余额'}</Button>
          {state === 'PENDING' && <Button type="button" variant="ghost" className="wd-settings-button"
            disabled={!!busy[source]} onClick={() => act(source, 'cancel')}>取消授权等待</Button>}
        </div>
        {hint && <small role="status">{hint}</small>}
      </div>;
    })}
    {statusError && <small role="status">暂时无法读取连接状态。 <Button type="button" variant="ghost" onClick={() => setRevision(value => value + 1)}>重试</Button></small>}
    {sources.includes('free') && !free && states.free === 'READY' && <small>首次百炼授权时，官方可能为账号创建普通 API Key。</small>}
    {free && <div className="wd-settings-quota-bucket">
      <small>百炼控制台账号 · {quotaDate(free.observed_at)}</small>
      {!free.rows.some(row => row.remaining != null) && <small>当前查询的型号未返回免费额度。</small>}
      {free.rows.filter(row => row.remaining != null).map(row => <div key={row.model} className="wd-settings-quota-line">
        <span>{row.model}{row.expires ? ' · ' + row.expires + ' 到期' : ''}</span>
        <span>剩余 {count.format(row.remaining!)}{row.total != null ? ' / ' + count.format(row.total) : ''} Tokens</span></div>)}
    </div>}
    {cash && <div className="wd-settings-quota-bucket"><small>阿里云账单账号 · {quotaDate(cash.observed_at)}</small>
      <div className="wd-settings-quota-line"><span>可用额度</span><strong title={cash.rows.currency+' '+cash.rows.available}>{formatMoney(cash.rows.available,cash.rows.currency)}</strong></div>
      <small title={cash.rows.currency+' '+cash.rows.cash}>现金余额 {formatMoney(cash.rows.cash,cash.rows.currency)}</small>
    </div>}
    <small>官方控制台 <a href="https://help.aliyun.com/zh/model-studio/new-free-quota" target="_blank" rel="noreferrer">免费 Token 额度</a>
      {' · '}<a href="https://usercenter2.aliyun.com/home" target="_blank" rel="noreferrer">阿里云账户余额</a></small>
    {!sources.length && <small>在 Workdesk 内查询需先安装并登录阿里云官方查询工具。{' '}
      <a href="https://help.aliyun.com/zh/model-studio/cli/installation" target="_blank" rel="noreferrer">配置免费额度查询</a>
    </small>}
  </>;
}

function QuotaRow({ quota, refresh, partnerName, apiName }: { quota: Quota; refresh: () => void;partnerName?:string;apiName?:string }) {
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState(false);
  const buckets = quota.buckets ?? [], balances = quota.balances ?? [];
  const input = quota.usage?.input_tokens, output = quota.usage?.output_tokens;
  const recordedTokens = input != null && output != null ? input + output : null;
  async function check() {
    if (!quota.refresh || checking) return;
    setChecking(true);
    setFailed(false);
    try {
      const response = await fetch('api/v1/resource-quota-check', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(quota.refresh) });
      if (!response.ok) throw new Error();
      const result = await response.json();
      setFailed(result.observation?.status !== 'OBSERVED');
      refresh();
    } catch {
      setFailed(true);
    } finally {
      setChecking(false);
    }
  }
  return <div className="wd-settings-row wd-settings-usage-quota">
    <div className="wd-settings-quota-heading">
      <strong>{quota.label}</strong>
      {quota.refresh && <Button type="button" variant="outline" className="wd-settings-button"
        disabled={checking} onClick={check} aria-label={`刷新 ${quota.label} 额度`}>{checking ? '查询中…' : '刷新'}</Button>}
    </div>
    {quota.legacy?<small>历史连接额度，未关联当前账号。</small>:quota.partner_id&&<small>{partnerName||quota.partner_id} · {quota.route==='api'?(apiName||quota.api_provider||'未知厂商')+' API':'官方账号'} · {quota.account_label||'账号未记录'}</small>}
    {quota.official_at != null && <small>官方查询 · {quotaDate(quota.official_at)}</small>}
    {(failed || quota.refresh_status === 'FAILED') && <small role="status">
      {quota.official_at != null ? '最近一次刷新失败，显示上次结果。' : '暂时无法查询官方额度。'}</small>}
    {buckets.map(bucket => <div className="wd-settings-quota-bucket" key={bucket.limit_id}>
      <small>{[bucket.name, bucket.plan_type && `${bucket.plan_type} 套餐`].filter(Boolean).join(' · ')}</small>
      {[bucket.primary, bucket.secondary].map((window, i) => window?.remaining_percent != null &&
        <div key={i} className="wd-settings-quota-line"><span>{windowLabel(window.duration_minutes)}</span>
          <span>剩余 {count.format(window.remaining_percent)}%{window.resets_at ? ' · ' + quotaDate(window.resets_at) + ' 重置' : ''}</span></div>)}
      {bucket.credits && (bucket.credits.unlimited || bucket.credits.balance != null) &&
        <div className="wd-settings-quota-line"><span>Credits</span><span>{bucket.credits.unlimited ? '不限量' : count.format(bucket.credits.balance!)}</span></div>}
    </div>)}
    {balances.map(balance => <div className="wd-settings-quota-bucket" key={balance.currency}>
      <div className="wd-settings-quota-line"><span>可用金额</span><strong title={balance.currency+' '+balance.total_balance}>{formatMoney(balance.total_balance,balance.currency)}</strong></div>
      <small>赠金 <span title={balance.currency+' '+balance.granted_balance}>{formatMoney(balance.granted_balance,balance.currency)}</span>
        {' · '}充值余额 <span title={balance.currency+' '+balance.topped_up_balance}>{formatMoney(balance.topped_up_balance,balance.currency)}</span></small>
    </div>)}
    {quota.balance_available === false && <small>官方返回余额不足以继续调用 API。</small>}
    {!balances.length && !buckets.length && quota.remaining != null && <small>
      手工记录 · {count.format(quota.remaining)} {quota.unit}{quota.manual_at ? ' · ' + quotaDate(quota.manual_at) : ''}
    </small>}
    {quota.provider === 'qwen'&&(quota.legacy||!quota.partner_id) ? <QwenQuotaDetails sources={quota.console_sources ?? []}
      snapshots={quota.console_snapshots ?? []} refresh={refresh}/> :
      !balances.length && !buckets.length && !failed && quota.refresh_status !== 'FAILED' && <small>
        {quota.refresh ? '点击刷新读取官方额度。' : quota.account_id?'暂无官方额度回执。':'请先在「账号与模型」中连接账户。'}</small>}
    {recordedTokens != null && <small className="wd-settings-recorded-tokens">近 7 天已记录 {count.format(recordedTokens)} Tokens</small>}
    {recordedTokens==null&&(input!=null||output!=null)&&<small className="wd-settings-recorded-tokens">近 7 天输入 {input==null?'—':count.format(input)} / 输出 {output==null?'—':count.format(output)} Tokens</small>}
  </div>;
}

export function SettingsUsage() {
  const [data, setData] = useState<Usage | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [partnerId,setPartnerId]=useState('all');

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    fetch('api/v1/settings-usage?days=7', { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('读取用量记录失败');
        return response.json() as Promise<Usage>;
      })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [retry]);

  const visible=(data?.quotas||[]).filter(quota=>partnerId==='all'||(quota.partner_id||quota.provider)===partnerId);
  const quotaRows=(rows:Quota[])=>rows.map(quota=><QuotaRow key={[quota.partner_id||quota.provider,quota.route,quota.api_provider,quota.account_id||quota.label].join(':')} quota={quota} partnerName={data?.partners?.find(partner=>partner.id===quota.partner_id)?.name} apiName={data?.api_providers?.find(provider=>provider.id===quota.api_provider)?.name} refresh={()=>setRetry(value=>value+1)}/>);
  return <div aria-busy={!data && !error}>
    <SettingsProjectBudget/>
    <section className="wd-settings-group wd-settings-card" aria-label="账户额度">
      <div className="wd-settings-usage-header">
        <h2>账户额度</h2>
        <nav aria-label="用量报告">
          <NativeSelect aria-label="额度伙伴" size="sm" value={partnerId} onChange={event=>setPartnerId(event.currentTarget.value)}><option value="all">全部伙伴</option>{data?.partners?.map(partner=><option value={partner.id} key={partner.id}>{partner.name}</option>)}</NativeSelect>
          <Button asChild size="sm" variant="outline" className="wd-settings-button"><a href="#settings/usage/activity">Token 活动</a></Button>
          <Button asChild size="sm" variant="outline" className="wd-settings-button"><a href="#settings/usage/calls">调用消耗</a></Button>
          <Button asChild size="sm" variant="ghost" className="wd-settings-button"><a href="#control/usage">工作用量</a></Button>
        </nav>
      </div>
      <p className="wd-settings-note">账号与 <a href="#settings/accounts">账号与模型</a> 保持一致，此处查看官方额度回执和已记录消耗。</p>
      {quotaRows(visible.filter(quota=>!quota.legacy))}
      {visible.some(quota=>quota.legacy)&&<details className="wd-settings-row"><summary>历史连接额度</summary>{quotaRows(visible.filter(quota=>quota.legacy))}</details>}
      {error ? <div className="wd-settings-row" role="status">
        <span>暂时无法读取账户额度。</span><Button className="wd-settings-button" variant="outline" type="button" onClick={() => setRetry(retry + 1)}>重试</Button>
      </div> : !data && <p className="wd-settings-empty" role="status">正在读取账户额度…</p>}
    </section>
  </div>;
}
