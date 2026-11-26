import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Download, ExternalLink, RefreshCw, ShieldCheck, Unplug } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { Textarea } from '../components/ui/textarea';
import { coreClient, CoreRequestError } from '../core/client';
import { PartnerRunList, type PartnerRun } from '../core/partner-runtime';
import './partners.css';

type Support = boolean | string | { state?: string; supported?: boolean; reason?: string; source?: string };
export type Partner = { id: string; name: string; account_support?: Support; api_support?: Support; sources?: string[]; component_state?: string };
export type PartnerRelease = { id: string; component_id: string; partner_id?: string; version: string; path: string; state: string; entry?: string[]; managed?: boolean };
export type PartnerAccount = { id: string; partner_id: string; label: string; kind: string; state: string; revision: number; login_url?: string; user_code?: string; instructions?: string };
export type PartnerConnection = { id: string; partner_id: string; release_id: string; route: 'account' | 'api'; account_id: string; model: string; options?: Record<string, unknown>; state: string; revision: number };
type PartnerOperation = { id: string; kind: string; partner_id?: string; account_id?: string; state: string; error?: string; login_url?: string; user_code?: string; instructions?: string };
export type PartnersSnapshot = { partners: Partner[]; components: PartnerRelease[]; accounts: PartnerAccount[]; connections: PartnerConnection[]; operations: PartnerOperation[]; runs: PartnerRun[] };
type Receipt = { account_id?: string; connection_id?: string; run_id?: string; operation_id?: string; state?: string; login_url?: string; user_code?: string; instructions?: string };
type Notice = { text: string; error?: boolean };
type ConnectionForm = { id: string; revision?: number; route: 'account' | 'api'; release_id: string; account_id: string; model: string; options?: Record<string, unknown> };

const stateLabels: Record<string, string> = {
  MISSING: '未安装', UNVERIFIED: '待核对', AVAILABLE: '已准备', READY: '已准备', INSTALLED: '已安装',
  QUEUED: '排队中', PREPARING: '准备中', DOWNLOADING: '下载中', INSTALLING: '安装中', BUILDING: '构建中', VERIFYING: '校验中',
  RUNNING: '运行中', COMPLETED: '已完成', SUCCEEDED: '已完成', FAILED: '未通过', ERROR: '出错',
  DISABLED: '已停用', REMOVED: '已移除', DISCONNECTED: '已断开', CONFIGURED: '已配置，待验证',
  UNCONFIGURED: '待配置', WAITING_LOGIN: '等待官方登录', WAITING_USER: '等待你处理', LOGIN_REQUIRED: '需要登录',
  AUTHENTICATED: '官方登录已确认', CONNECTED: '已连接', VERIFIED: '检查通过', LOCAL_READY: '本地准备通过',
  WAITING_INPUT: '等待补充', WAITING_APPROVAL: '等待审批', CANCELLED: '已取消', STOPPED: '已停止', STOP_REQUESTED: '正在停止',
  UNKNOWN: '结果待核对', ORPHANED_UNKNOWN: '运行待核对', UNSUPPORTED: '官方不支持', NOT_SUPPORTED: '官方不支持',
  NOT_CONFIGURED: '待配置', KEY_SAVED_UNVERIFIED: 'API Key 已保存，待验证', SAVED_UNVERIFIED: '已保存，待验证',
  STOPPED_RUNTIME: '本地运行已停止', FAILED_NO_SEND: '未启动', LOGIN_NOT_CONFIRMED: '官方登录尚未确认', OFFICIALLY_UNSUPPORTED: '官方不支持',
  RESULT_RECEIVED: '已收到结果，正在确认退出',
  ERROR_RECEIVED: '已收到错误，正在核对退出', STOP_RESULT_RECEIVED: '已收到停止回执，正在核对退出',
};
const ongoing = new Set(['QUEUED', 'PREPARING', 'DOWNLOADING', 'INSTALLING', 'BUILDING', 'VERIFYING', 'RUNNING', 'RESULT_RECEIVED', 'ERROR_RECEIVED', 'STOP_RESULT_RECEIVED', 'STOP_REQUESTED', 'WAITING_LOGIN', 'WAITING_USER', 'WAITING_INPUT', 'WAITING_APPROVAL']);
const stopped = new Set(['COMPLETED', 'SUCCEEDED', 'FAILED', 'ERROR', 'CANCELLED', 'STOPPED', 'STOPPED_RUNTIME', 'FAILED_NO_SEND']);
const componentAliases: Record<string, string[]> = {
  codex: ['codex'], deepseek: ['deepseek', 'deepseek-cli', 'deepseek-harness'], qwen: ['qwen', 'qwen-code'],
  claude: ['claude', 'claude-code'], grok: ['grok', 'grok-build'], gemini: ['gemini', 'gemini-cli'],
  kimi: ['kimi', 'kimi-code'], glm: ['glm', 'zcode'],
};
const emptySnapshot: PartnersSnapshot = { partners: [], components: [], accounts: [], connections: [], operations: [], runs: [] };
const labelState = (value?: string) => value ? stateLabels[value.toUpperCase()] || value : '尚未核对';
const freshForm = (): ConnectionForm => ({ id: '', route: 'account', release_id: '', account_id: '', model: '' });
const sameComponent = (release: PartnerRelease, partner: string) => release.partner_id === partner || (componentAliases[partner] || [partner]).includes(release.component_id);
const isUsableRelease = (release: PartnerRelease) => ['READY', 'AVAILABLE', 'INSTALLED', 'VERIFIED'].includes(release.state?.toUpperCase());
const loggedIn = (account: PartnerAccount) => ['AUTHENTICATED', 'CONNECTED', 'VERIFIED'].includes(account.state?.toUpperCase());
const isApiAccount = (account: PartnerAccount) => account.kind === 'deepseek_api' || account.kind === 'api';
const isRelatedCommand = (kind: string) => /^(component|account|connection|partner)\./.test(kind);
const isUnknownState = (state: string) => state.toUpperCase() === 'UNKNOWN' || state.toUpperCase().endsWith('_UNKNOWN');

function unsupported(support?: Support) {
  const states = ['UNSUPPORTED', 'NOT_SUPPORTED', 'OFFICIALLY_UNSUPPORTED'];
  return support === false || (typeof support === 'string' && states.includes(support.toUpperCase()))
    || (!!support && typeof support === 'object' && (support.supported === false || states.includes(support.state?.toUpperCase() || '')));
}
function supportReason(support?: Support) {
  return typeof support === 'object' && support ? support.reason : '';
}
function httpsLink(value?: string) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : undefined;
  } catch { return undefined; }
}
function loginLink(value?: string) {
  if (!value) return undefined;
  try { const url = new URL(value); return !url.username && !url.password && (url.protocol === 'https:' || (url.protocol === 'http:' && ['127.0.0.1','localhost','[::1]'].includes(url.hostname))) ? url.href : undefined; } catch { return undefined; }
}
function Panel({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return <Card className="wd-settings-card wd-partners-panel gap-0 py-0 shadow-none">
    <div className="wd-partners-panel-heading"><h3>{title}</h3>{aside}</div>{children}
  </Card>;
}
function State({ value }: { value?: string }) {
  return <span className="wd-partners-state" data-state={value?.toUpperCase()}>{labelState(value)}</span>;
}
function LoginPrompt({ value }: { value: { login_url?: string; user_code?: string; instructions?: string } }) {
  const href = loginLink(value.login_url);
  if (!href && !value.user_code && !value.instructions) return null;
  return <div className="wd-partners-login">
    {value.instructions && <p>{value.instructions}</p>}
    {value.user_code && <p>官方授权码：<strong>{value.user_code}</strong></p>}
    {href && <a href={href} target="_blank" rel="noopener noreferrer">打开官方登录页面 <ExternalLink size={14}/></a>}
    <p className="wd-partners-note">在官方页面完成登录后，点击“核对登录状态”。</p>
  </div>;
}

export function SettingsPartners({ initialPartner }: { initialPartner?: string }) {
  const [data, setData] = useState<PartnersSnapshot>(emptySnapshot);
  const [partnerId, setPartnerId] = useState(initialPartner || 'codex');
  const [notice, setNotice] = useState<Notice | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [form, setForm] = useState<ConnectionForm>(freshForm);
  const [accountLabel, setAccountLabel] = useState('');
  const [accountKind, setAccountKind] = useState<'official_login' | 'deepseek_api'>('official_login');
  const [keyAccountId, setKeyAccountId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [applyAllApi, setApplyAllApi] = useState(true);
  const [remove, setRemove] = useState<PartnerRelease | null>(null);
  const [prompt, setPrompt] = useState('');
  const [loginReceipt, setLoginReceipt] = useState<({ account_id: string } & Receipt) | null>(null);
  const [uncertain, setUncertain] = useState(() => coreClient.uncertainCommands().filter(item => isRelatedCommand(item.kind)));
  const mounted = useRef(false);
  const actionLock = useRef(false);
  const savedSelection = useRef<{ id: string; partner: string } | null>(null);
  const selected = useRef(partnerId);
  const readScope = useRef(coreClient.createReadScope());
  selected.current = partnerId;

  const refresh = useCallback(async () => {
    const read = readScope.current.begin();
    try {
      const value = await coreClient.request<PartnersSnapshot>('v2/partners', { signal: read.signal });
      if (!mounted.current || !read.isCurrent()) return;
      setData(Object.fromEntries(Object.entries(emptySnapshot).map(([key]) => [key, Array.isArray(value[key as keyof PartnersSnapshot]) ? value[key as keyof PartnersSnapshot] : []])) as PartnersSnapshot);
      setLoaded(true);
    } catch (error) {
      if (mounted.current && read.isCurrent()) setNotice({ text: error instanceof Error ? error.message : '伙伴信息读取失败，请刷新核对。', error: true });
    } finally {
      if (mounted.current && read.isCurrent()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    mounted.current = true;
    const bootstrap = async () => {
      try {
        await coreClient.loadIdentity();
        if (!active || !mounted.current) return;
        setUncertain(coreClient.uncertainCommands().filter(item => isRelatedCommand(item.kind)));
        await refresh();
      } catch (error) {
        if (active && mounted.current) { setLoading(false); setNotice({ text: error instanceof Error ? error.message : '开发实例尚未连接。', error: true }); }
      }
    };
    void bootstrap();
    return () => { active = false; mounted.current = false; readScope.current.cancel(); };
  }, [refresh]);

  const needsRefresh = data.operations.some(item => ongoing.has(item.state.toUpperCase())) || data.runs.some(item => ongoing.has(item.state.toUpperCase()));
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      if (!document.hidden && needsRefresh) timer = setTimeout(() => { void refresh(); }, 2500);
    };
    const onVisibility = () => { if (document.hidden) { if (timer) clearTimeout(timer); readScope.current.cancel(); } else { void refresh(); } };
    schedule();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', onVisibility); };
  }, [data, needsRefresh, refresh]);

  const partner = data.partners.find(item => item.id === partnerId) || data.partners[0];
  const actualPartnerId = partner?.id || partnerId;
  const releases = data.components.filter(item => sameComponent(item, actualPartnerId));
  const accounts = data.accounts.filter(item => item.partner_id === actualPartnerId);
  const connections = data.connections.filter(item => item.partner_id === actualPartnerId);
  const operations = data.operations.filter(item => item.partner_id === actualPartnerId || accounts.some(account => account.id === item.account_id));
  const runs = data.runs.filter(item => item.partner_id === actualPartnerId || connections.some(connection => connection.id === item.connection_id));
  const editingConnection = connections.find(item => item.id === form.id);
  const relevantAccounts = accounts.filter(item => isApiAccount(item) === (form.route === 'api'));
  const readyReleases = releases.filter(isUsableRelease);
  const loginRelease = readyReleases.find(item => item.id === form.release_id) || (readyReleases.length === 1 ? readyReleases[0] : undefined);
  const accountSupport = partner?.account_support;
  const apiSupport = partner?.api_support;
  const unsupportedRoute = unsupported(form.route === 'account' ? accountSupport : apiSupport);
  const blocked = busy || uncertain.length > 0 || !loaded;

  useEffect(() => {
    savedSelection.current = null;
    setForm(freshForm()); setRemove(null); setKeyAccountId(''); setApiKey(''); setAccountLabel(''); setNotice(null); setPrompt('');
  }, [partnerId]);

  useEffect(() => {
    const saved = savedSelection.current;
    if (!saved || saved.partner !== actualPartnerId) return;
    const connection = data.connections.find(item => item.id === saved.id && item.partner_id === saved.partner);
    if (!connection) return;
    savedSelection.current = null;
    setForm({ id: connection.id, revision: connection.revision, route: connection.route, release_id: connection.release_id, account_id: connection.account_id, model: connection.model, options: structuredClone(connection.options || {}) });
  }, [data.connections, actualPartnerId]);

  async function perform(kind: string, payload: Record<string, unknown>, success: string, after?: (receipt: Receipt) => void) {
    if (busy || !loaded || actionLock.current || (uncertain.length > 0 && !kind.endsWith('.reconcile'))) return;
    actionLock.current = true;
    const sourcePartner = selected.current;
    setBusy(true); setNotice(null);
    try {
      const result = await coreClient.command<Receipt>(kind, payload);
      if (mounted.current && sourcePartner === selected.current) {
        setNotice({ text: success }); after?.(result.receipt || {});
      }
      if (mounted.current) await refresh();
    } catch (error) {
      if (mounted.current && sourcePartner === selected.current) {
        setNotice({ text: error instanceof Error ? error.message : '操作未完成，请核对当前状态。', error: true });
      }
    } finally {
      actionLock.current = false;
      if (mounted.current) {
        setBusy(false); setUncertain(coreClient.uncertainCommands().filter(item => isRelatedCommand(item.kind)));
      }
    }
  }

  async function reconcile(id: string) {
    if (busy || actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    try {
      const value = await coreClient.receipt(id);
      if (!mounted.current) return;
      setNotice({ text: value.state === 'RECORDED' ? '原操作回执已找到，请查看最新状态。' : '原操作结果仍待核对，保留原操作，不重新发送。' });
      await refresh();
    } catch (error) {
      if (mounted.current) setNotice({ text: error instanceof Error ? error.message : '原操作仍待核对。', error: true });
    } finally {
      actionLock.current = false;
      if (mounted.current) { setBusy(false); setUncertain(coreClient.uncertainCommands().filter(item => isRelatedCommand(item.kind))); }
    }
  }

  function editConnection(id: string) {
    const connection = connections.find(item => item.id === id);
    setForm(connection ? { id: connection.id, revision: connection.revision, route: connection.route, release_id: connection.release_id, account_id: connection.account_id, model: connection.model, options: structuredClone(connection.options || {}) } : freshForm());
  }
  const selectedRelease = data.components.find(item => item.id === form.release_id);
  const connectionValid = !!partner && !!selectedRelease && isUsableRelease(selectedRelease) && !!form.account_id && !unsupportedRoute && (form.route === 'account' || !!form.model.trim());
  const staleConnection = !!editingConnection && form.revision !== editingConnection.revision;
  const savedConnectionSelected = !!editingConnection && !staleConnection && editingConnection.route === form.route && editingConnection.release_id === form.release_id && editingConnection.account_id === form.account_id && editingConnection.model === form.model.trim();

  return <div className="wd-partners" data-partners-panel>
    <div className="wd-settings-section-header"><h2>伙伴与连接</h2><Button variant="outline" className="wd-settings-button" disabled={busy || loading} onClick={() => { setLoading(true); void refresh(); }}><RefreshCw size={14}/>刷新状态</Button></div>
    <p className="wd-partners-note">分别管理官方工作端、账号和驾驶模型。账号走官方登录；API 路线统一使用 DeepSeek。</p>
    {notice && <p className={`wd-partners-feedback${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    {uncertain.length > 0 && <div className="wd-partners-uncertain" role="alert"><strong>有操作回执待核对</strong><p>先核对原操作，再开始新的安装、登录或连接操作。</p>{uncertain.map(item => <Button key={item.id} variant="outline" className="wd-settings-button" disabled={busy} onClick={() => void reconcile(item.id)}>核对原操作 · {item.id.slice(0, 8)}</Button>)}</div>}
    {loading && !loaded && <p role="status">正在读取伙伴信息…</p>}
    {!loaded && !loading && <p className="wd-partners-empty">暂时无法读取伙伴目录。刷新只读取状态，不启动安装或模型。</p>}
    {loaded && <>
      <div className="wd-partners-picker" aria-label="选择伙伴">{data.partners.map(item => {
        const installed = data.components.some(release => sameComponent(release, item.id) && isUsableRelease(release));
        return <button type="button" key={item.id} className="wd-partners-choice" aria-pressed={actualPartnerId === item.id} disabled={busy} onClick={() => setPartnerId(item.id)}><strong>{item.name}</strong><span>{installed ? '官方端已准备' : '官方端未准备'}</span></button>;
      })}</div>
      {partner && <>
        <Panel title={`${partner.name} 官方工作端`} aside={<Button variant="outline" className="wd-settings-button" disabled={blocked || operations.some(item => item.kind.startsWith('component.') && ongoing.has(item.state.toUpperCase()))} onClick={() => void perform('component.prepare', { partner_id: actualPartnerId, version: 'latest' }, '已接收准备操作，请查看安装进度。')}><Download size={14}/>{releases.some(isUsableRelease) ? '准备新版本' : '安装官方端'}</Button>}>
          <div className="wd-partners-panel-body">
            {readyReleases.length > 0 && <label>登录使用的官方端版本<NativeSelect aria-label="登录使用的官方端版本" value={loginRelease?.id || ''} disabled={blocked} onChange={event => setForm(previous => ({ ...previous, release_id: event.currentTarget.value }))}><option value="">请选择一个版本</option>{readyReleases.map(release => <option value={release.id} key={release.id}>{release.version}</option>)}</NativeSelect></label>}
            {!releases.length && <p className="wd-partners-empty">尚未准备此伙伴的官方工作端。点击安装时才下载所需组件。</p>}
            {releases.map(release => <div className="wd-partners-release" key={release.id}>
              <div className="wd-partners-release-title"><strong>{release.version}</strong><State value={release.state}/>{release.managed === false && <span className="wd-partners-note">外部只读组件</span>}</div>
              <div className="wd-partners-actions">{release.state === 'DISABLED' ? <Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => void perform('component.enable', { release_id: release.id }, '已启用此版本。原来停用的连接仍需重新保存后使用。')}>启用此版本</Button> : <Button variant="outline" className="wd-settings-button" disabled={blocked || !isUsableRelease(release)} onClick={() => void perform('component.disable', { release_id: release.id }, '已停用此版本，新工作不会选用它。')}><Unplug size={14}/>停用</Button>}<Button variant="outline" className="wd-settings-button" disabled={blocked || release.managed === false || release.state === 'REMOVED'} onClick={() => setRemove(release)}>卸载此版本</Button></div>
              <details className="wd-partners-location"><summary>安装位置</summary><p>{release.path}</p></details>
            </div>)}
            {remove && <div className="wd-partners-confirm" role="group" aria-label="确认卸载组件"><strong>卸载 {partner.name} {remove.version}？</strong><p>{remove.path}</p><p>只移除此版本的程序；项目、成果、账号与会话记录保留。恢复清单继续保留，恢复时可能需要重新下载同一版本。在途或结果未知的运行仍会阻止卸载。</p><div className="wd-partners-actions"><Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => setRemove(null)}>保留</Button><Button variant="destructive" disabled={blocked} onClick={() => void perform('component.remove', { release_id: remove.id, allow_restore_download: true }, '已接收卸载操作，请查看结果。', () => setRemove(null))}>确认卸载此版本</Button></div></div>}
            {operations.length > 0 && <div className="wd-partners-operations">{operations.map(operation => <div key={operation.id}><span>{operation.kind.startsWith('component.') ? '组件操作' : '账号操作'}</span><State value={operation.state}/>{operation.kind.startsWith('component.') && isUnknownState(operation.state) && <Button variant="outline" className="wd-settings-button" disabled={busy || !loaded} onClick={() => void perform('component.reconcile', { operation_id: operation.id }, '已核对原安装操作的发布证据；没有重新安装。')}>核对安装结果</Button>}{operation.error && <p className="wd-partners-error">{operation.error}</p>}{operation.login_url && <LoginPrompt value={operation}/>}</div>)}</div>}
          </div>
        </Panel>

        <Panel title="账号与 API 凭据">
          <div className="wd-partners-panel-body">
            <div className="wd-partners-route-status"><span>官方账号：{unsupported(accountSupport) ? '官方不支持' : '官方登录入口'}</span><span>API：{unsupported(apiSupport) ? '官方不支持' : 'DeepSeek 替换驾驶'}</span></div>
            {unsupported(accountSupport) && <p className="wd-partners-note">{supportReason(accountSupport) || '官方明确不支持此账号接入方式，单独保留说明。'}</p>}
            {unsupported(apiSupport) && <p className="wd-partners-note">{supportReason(apiSupport) || '官方明确不支持此 API 接入方式，单独保留说明。'}</p>}
            {accounts.map(account => <div className="wd-partners-account" key={account.id}>
              <div><strong>{account.label}</strong><span className="wd-partners-note">{isApiAccount(account) ? 'DeepSeek API' : '官方账号'}</span><State value={account.state}/></div>
              <div className="wd-partners-actions">{isApiAccount(account) ? <Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => { setApiKey(''); setKeyAccountId(account.id); }}>输入 API Key</Button> : <><Button variant="outline" className="wd-settings-button" disabled={blocked || unsupported(accountSupport) || !loginRelease} onClick={() => void perform('account.login', { account_id: account.id, release_id: loginRelease?.id }, '官方登录已启动，请完成授权。', receipt => setLoginReceipt({ ...receipt, account_id: account.id }))}>官方登录</Button><Button variant="outline" className="wd-settings-button" disabled={blocked || !loginRelease} onClick={() => void perform('account.inspect', { account_id: account.id, release_id: loginRelease?.id }, '已开始核对官方登录状态。')}>核对登录状态</Button></>}</div>
              {!loggedIn(account) && <LoginPrompt value={account}/>}
              {!loggedIn(account) && loginReceipt?.account_id === account.id && !account.login_url && <LoginPrompt value={loginReceipt}/>}
            </div>)}
            {!accounts.length && <p className="wd-partners-empty">先为此伙伴添加账号或 DeepSeek API 引用，可以保存多个账号。</p>}
            <form className="wd-partners-account-form" onSubmit={event => { event.preventDefault(); if (!accountLabel.trim()) return; void perform('account.create', { partner_id: actualPartnerId, label: accountLabel.trim(), kind: accountKind }, '账号引用已添加。', () => setAccountLabel('')); }}>
              <label>添加方式<NativeSelect aria-label="添加方式" value={accountKind} disabled={blocked} onChange={event => setAccountKind(event.currentTarget.value as typeof accountKind)}><option value="official_login" disabled={unsupported(accountSupport)}>官方账号</option><option value="deepseek_api" disabled={unsupported(apiSupport)}>DeepSeek API</option></NativeSelect></label>
              <label>便于辨认的名称<Input value={accountLabel} disabled={blocked} placeholder="例如：我的工作账号" onChange={event => setAccountLabel(event.currentTarget.value)}/></label>
              <Button variant="outline" className="wd-settings-button" type="submit" disabled={blocked || !accountLabel.trim() || unsupported(accountKind === 'official_login' ? accountSupport : apiSupport)}>添加</Button>
            </form>
            {keyAccountId && accounts.some(account => account.id === keyAccountId) && <form className="wd-partners-key-form" onSubmit={event => { event.preventDefault(); if (!apiKey.trim()) return; const key = apiKey.trim(); setApiKey(''); void perform('account.set-key', { account_id: keyAccountId, key, apply_all_api: applyAllApi }, 'API Key 已保存。保存不会发起模型请求。', () => setKeyAccountId('')); }}>
              <label>为“{accounts.find(account => account.id === keyAccountId)?.label}”输入 DeepSeek API Key<Input type="password" value={apiKey} autoComplete="off" spellCheck={false} disabled={blocked} onChange={event => setApiKey(event.currentTarget.value)}/></label>
              <label className="wd-partners-checkbox"><input type="checkbox" checked={applyAllApi} disabled={blocked} onChange={event => setApplyAllApi(event.currentTarget.checked)}/><span>同时用于已添加的全部 DeepSeek API 账号</span></label>
              <p className="wd-partners-note">保存到本地受保护的凭据存储。此处只保存，接通验证在下方单独发起。</p><div className="wd-partners-actions"><Button variant="outline" className="wd-settings-button" type="button" disabled={busy} onClick={() => { setApiKey(''); setKeyAccountId(''); }}>取消</Button><Button type="submit" disabled={blocked || !apiKey.trim()}>保存 API Key</Button></div>
            </form>}
          </div>
        </Panel>

        <Panel title="连接配置">
          <form className="wd-partners-panel-body wd-partners-connection" onSubmit={event => { event.preventDefault(); if (!connectionValid || staleConnection) return; const payload = { partner_id: actualPartnerId, release_id: form.release_id, route: form.route, account_id: form.account_id, model: form.model.trim(), options: form.options || {}, ...(form.id ? { id: form.id, revision: form.revision } : {}) }; void perform('connection.save', payload, '连接配置已保存，新运行使用这一版本。', receipt => { const id = receipt.connection_id || form.id; if (id) savedSelection.current = { id, partner: actualPartnerId }; }); }}>
            <label>已保存连接<NativeSelect aria-label="已保存连接" value={form.id} disabled={blocked} onChange={event => editConnection(event.currentTarget.value)}><option value="">新建连接</option>{connections.map(connection => <option key={connection.id} value={connection.id}>{connection.route === 'api' ? 'DeepSeek API' : '官方账号'} · {accounts.find(account => account.id === connection.account_id)?.label || '账号待核对'} · {labelState(connection.state)}</option>)}</NativeSelect></label>
            <div className="wd-partners-fields"><label>接入方式<NativeSelect aria-label="接入方式" value={form.route} disabled={blocked} onChange={event => { const route = event.currentTarget.value as 'api' | 'account'; setForm(previous => ({ ...previous, route, account_id: '', model: route === 'api' ? 'deepseek-flash' : '' })); }}><option value="account" disabled={unsupported(accountSupport)}>官方账号登录</option><option value="api" disabled={unsupported(apiSupport)}>DeepSeek API 替换驾驶</option></NativeSelect></label>
              <label>官方端版本<NativeSelect aria-label="官方端版本" value={form.release_id} disabled={blocked} onChange={event => setForm(previous => ({ ...previous, release_id: event.currentTarget.value }))}><option value="">请选择已准备的版本</option>{releases.map(release => <option key={release.id} value={release.id} disabled={!isUsableRelease(release)}>{release.version} · {labelState(release.state)}</option>)}</NativeSelect></label>
              <label>使用账号<NativeSelect aria-label="使用账号" value={form.account_id} disabled={blocked} onChange={event => setForm(previous => ({ ...previous, account_id: event.currentTarget.value }))}><option value="">请选择账号</option>{relevantAccounts.map(account => <option key={account.id} value={account.id}>{account.label} · {labelState(account.state)}</option>)}</NativeSelect></label>
              <label>{form.route === 'api' ? 'DeepSeek 驾驶模型' : '官方模型（留空使用官方默认）'}<Input value={form.model} disabled={blocked} placeholder={form.route === 'api' ? 'deepseek-flash' : '使用官方默认模型'} onChange={event => setForm(previous => ({ ...previous, model: event.currentTarget.value }))}/></label>
            </div>
            {staleConnection && <div className="wd-partners-uncertain" role="alert"><p>此连接已在其他位置更新。先读取最新版本，再决定需要修改的内容。</p><Button type="button" variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => editConnection(form.id)}>读取最新版本</Button></div>}
            <div className="wd-partners-actions"><Button type="submit" disabled={blocked || !connectionValid || staleConnection}>保存连接</Button><Button variant="outline" className="wd-settings-button" type="button" disabled={blocked || !savedConnectionSelected} onClick={() => editingConnection && void perform('connection.inspect', { connection_id: editingConnection.id }, '已核对本地连接准备情况；尚未发起模型请求。')}><ShieldCheck size={14}/>本地检查</Button></div>
          </form>
        </Panel>

        <Panel title="接通验证与运行">
          <div className="wd-partners-panel-body">
            <p className="wd-partners-note">先完成安装和账号配置，再用一次简短任务检查真实连接。API 验证会使用所选 DeepSeek 凭据并产生费用。</p>
            <form className="wd-partners-run-form" onSubmit={event => { event.preventDefault(); if (!editingConnection || !savedConnectionSelected || !prompt.trim()) return; void perform('partner.run', { connection_id: editingConnection.id, connection_revision: editingConnection.revision, prompt: prompt.trim() }, '验证已开始，请查看运行状态。'); }}>
              <label>验证内容<Textarea value={prompt} disabled={blocked} placeholder="例如：在当前项目中写一份简短的连接确认文件。" onChange={event => setPrompt(event.currentTarget.value)}/></label>
              <div className="wd-partners-actions"><Button type="submit" disabled={blocked || !savedConnectionSelected || !prompt.trim()}>{editingConnection?.route === 'api' ? '开始 API 接通验证（付费）' : '开始账号接通验证'}</Button></div>
              {!savedConnectionSelected && <p className="wd-partners-note">请先在上方选择一个已保存连接；修改配置后需要先保存。</p>}
            </form>
            <PartnerRunList runs={runs} busy={busy} blocked={blocked} perform={perform} label={partner.name}/>
          </div>
        </Panel>
        {partner.sources?.some(source => !!httpsLink(source)) && <details className="wd-partners-sources"><summary>官方接入说明</summary>{partner.sources.filter(source => !!httpsLink(source)).map(source => <a key={source} href={httpsLink(source)} target="_blank" rel="noopener noreferrer">{new URL(source).hostname}<ExternalLink size={13}/></a>)}</details>}
      </>}
    </>}
  </div>;
}
