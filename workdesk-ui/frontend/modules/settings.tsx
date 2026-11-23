import { createPortal } from 'react-dom';
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { Archive, ArrowRight, Bell, ChevronDown, ChevronRight, Cpu, Folder, History, Keyboard, LayoutGrid, LifeBuoy, MessageSquare, Moon, Plug, Search, Settings, ShieldCheck, Sparkles, Sun, Wrench } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { AppearancePanel } from './settings-appearance';
import { SettingsAccounts } from './settings-accounts';
import { SettingsUsage } from './settings-usage';
import { SettingsTokenUsage } from './settings-token-usage';
import { SettingsExtensions, SettingsManagementSummary } from './settings-extensions';

type Destination = { name: string; href: string; Icon: ComponentType; aliases: string };
type NoticeKind = 'approval' | 'failure' | 'review';
type WorkProvider = 'codex' | 'deepseek' | 'qwen';
type Snapshot = {
  launch: string;
  close: string;
  opener: string;
  userNickname?: string;
  assistantNickname?: string;
  notifications: Record<NoticeKind, boolean>;
  connectionNoticeSeconds: number;
  workAccess: Record<WorkProvider, 'workspace' | 'auto'>;
  lastRecovery?: {at: number; source: 'defaults' | 'backup'; original: string};
  recovery?: {revision: string; backup: {workDirectory: string; workAccess: Record<WorkProvider, string>} | null};
  chatApiModels?: Record<string, string[]>;
  chatDefaults?: {route: string; model: string; provider: string; apiProvider?: string} | null;
  workDirectory?: string;
};
type Notice = { kind: NoticeKind; title: string; href: string; at?: number };
type PreferencesBridge = {
  get(): Snapshot;
  recover(source: 'defaults' | 'backup'): Promise<Snapshot>;
  reload(): Promise<Snapshot>;
  save(key: keyof Snapshot, value: Snapshot[keyof Snapshot]): Promise<Snapshot>;
  getAttention(): Promise<{ items: Notice[] }>;
};
type SettingsProps = {
  data?: { health?: { program_base?: string; implementation?: string }; storage?: { library?: string } };
  theme: string;
  busy?: boolean;
  pending?: string;
  diagnostics?: boolean;
  appearance?: any;
  page?: string;
  focus?: string;
};

const groups: { title: string; items: Destination[] }[] = [
  { title: '偏好', items: [
    { name: '常规', href: '#settings/general', Icon: Settings, aliases: '启动 关闭窗口 确认 文件 打开方式 对话昵称 称呼' },
    { name: '外观', href: '#settings/appearance', Icon: Sun, aliases: '浅色 深色 主题 色彩 区域配色' },
    { name: '键盘快捷键', href: '#settings/shortcuts', Icon: Keyboard, aliases: 'Enter Ctrl K Esc 发送 关闭 搜索' },
    { name: '通知', href: '#settings/notifications', Icon: Bell, aliases: '审批 失败 成果 验收 提醒 连接提示时长 浮层' },
  ] },
  { title: 'Chat 与 Work', items: [
    { name: 'Chat 接入', href: '#settings/chat', Icon: MessageSquare, aliases: '渠道 DeepSeek API 官网 聊天 历史' },
    { name: 'Work 权限', href: '#settings/access', Icon: ShieldCheck, aliases: '审批 执行权限 文件 网络 命令 PowerShell 默认 工作端' },
    { name: '账号与模型', href: '#settings/accounts', Icon: Cpu, aliases: '账号 连接 工作端 模型 Codex Qwen DeepSeek' },
    { name: '用量与预算', href: '#settings/usage', Icon: History, aliases: '额度 费用 使用情况 账单 预留' },
  ] },
  { title: '资料与扩展', items: [
    { name: '数据与记录', href: '#settings/records', Icon: Archive, aliases: '归档 恢复 导入 导出 Markdown JSON 项目外 工作目录' },
    { name: '本地存储与恢复', href: '#settings/storage', Icon: Folder, aliases: '备份 恢复 迁移 资料库' },
    { name: '插件与 Skills', href: '#settings/extensions', Icon: Plug, aliases: '插件 Skill MCP 安装 场景 能力预设' },
    { name: '自然语言管理', href: '#settings/management', Icon: Sparkles, aliases: '外观 场景 配置 技能 请求' },
  ] },
  { title: '帮助与维护', items: [
    { name: '运行与恢复', href: '#settings/operations', Icon: Wrench, aliases: '运行核对 操作回执 接管 快照' },
    { name: '帮助与诊断', href: '#settings/diagnostics', Icon: LifeBuoy, aliases: '工程 版本 路径 服务状态 重载 回执' },
  ] },
];

const destinations = groups.flatMap(group => group.items.map(item => ({ ...item, group: group.title })));
const searchable = [
  { name: '启动时打开', href: '#settings/general/launch', Icon: Settings, group: '常规', aliases: '启动 Chat Work 上次页面' },
  { name: '关闭窗口前确认', href: '#settings/general/close', Icon: Settings, group: '常规', aliases: '关闭快捷键 窗口' },
  { name: '文件默认打开方式', href: '#settings/general/opener', Icon: Folder, group: '常规', aliases: '文件资源管理器 编辑器' },
  { name: '用户昵称', href: '#settings/general/user-nickname', Icon: MessageSquare, group: '常规', aliases: '对话称呼 User 显示名称' },
  { name: 'AI 称呼', href: '#settings/general/assistant-nickname', Icon: MessageSquare, group: '常规', aliases: '助手昵称 模型称呼 显示名称 AI昵称' },
  { name: '默认审批方式', href: '#settings/access/default', Icon: ShieldCheck, group: 'Work 权限', aliases: '默认权限 自动审核 请求批准' },
  { name: '查看与恢复归档', href: '#settings/records/archives', Icon: Archive, group: '数据与记录', aliases: '历史 归档 恢复' },
  { name: '查看与导出记录', href: '#settings/records/export', Icon: Archive, group: '数据与记录', aliases: '导出 Markdown JSON 下载' },
];
const overview: Destination = { name: '总览', href: '#settings', Icon: LayoutGrid, aliases: '设置 概览' };
const noticeLabels: Record<NoticeKind, string> = { approval: '需要我审批', failure: '运行失败', review: '成果待验收' };
const providerLabels: Record<WorkProvider, string> = { codex: 'Codex', deepseek: 'DeepSeek', qwen: 'Qwen' };
const shortcutRows = [
  ['在设置中搜索', ['Ctrl', 'K']],
  ['在工作台搜索记录', ['Ctrl', 'K']],
  ['发送消息或工作要求', ['Enter']],
  ['输入换行', ['Shift', 'Enter']],
  ['打开或收起右侧工具栏', ['Ctrl', 'Shift', 'B']],
  ['切换右栏全宽视图（右栏已打开时）', ['Ctrl', 'Shift', 'F']],
  ['关闭桌面窗口（沿用关闭确认设置）', ['Ctrl', 'W']],
  ['关闭菜单或弹窗', ['Esc']],
] as const;

function preferencesBridge() {
  return (window as Window & { WorkdeskSettingsPreferences?: PreferencesBridge }).WorkdeskSettingsPreferences;
}

type PreferenceFeedback = { target: string | null; message: string; kind: 'saving' | 'success' | 'error' };
const FeedbackContext = createContext<PreferenceFeedback | null>(null);
function FeedbackMessage({ feedback, inline = false }: { feedback: PreferenceFeedback; inline?: boolean }) {
  return <p className={(inline?'wd-settings-inline-feedback ':'wd-settings-feedback ') + feedback.kind}
    role={feedback.kind==='error'?'alert':'status'} aria-live={feedback.kind==='error'?'assertive':'polite'}>{feedback.message}</p>;
}

function SettingsNavigation({ page, search, setSearch }: { page: string; search: string; setSearch: (value: string) => void }) {
  const keywords = search.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches = [...searchable, ...destinations].filter(item => keywords.every(word => (item.name + ' ' + item.aliases + ' ' + item.group).toLocaleLowerCase().includes(word)));
  const current = page ? '#settings/' + page : '#settings';
  function navItem(item: Destination, group?: string) {
    const Icon = item.Icon;
    return <a key={item.href} href={item.href} aria-current={item.href === current ? 'page' : undefined}>
      <Icon aria-hidden="true"/><span>{item.name}{group && <small>{group}</small>}</span>
    </a>;
  }
  return <nav className="wd-settings-nav" aria-label="设置分类">
    <label className="wd-settings-search">
      <Search aria-hidden="true"/><input type="search" data-settings-search aria-label="搜索设置" placeholder="搜索设置" value={search}
        onChange={event => setSearch(event.currentTarget.value)}
        onKeyDown={event => {
          if (event.key === 'Escape' && search) {
            event.stopPropagation();
            event.preventDefault();
            setSearch('');
          }
          if (event.key === 'Enter' && keywords.length && matches.length && !event.nativeEvent.isComposing) {
            event.preventDefault();
            setSearch('');
            window.location.hash = matches[0].href;
          }
        }}/>
    </label>
    {keywords.length ? <div className="wd-settings-nav-items">
      {matches.length ? matches.map(item => navItem(item, item.group)) : <p role="status">未找到相关设置</p>}
    </div> : <>
      <div className="wd-settings-nav-items">{navItem(overview)}</div>
      {groups.map(group => <div className="wd-settings-nav-group" key={group.title}>
        <h2>{group.title}</h2><div className="wd-settings-nav-items">{group.items.map(item => navItem(item))}</div>
      </div>)}
    </>}
  </nav>;
}

function Group({ title, children, inside = false }: { title?: string; children: ReactNode; inside?: boolean }) {
  return <section className="wd-settings-group">
    {title && !inside && <h2 className="wd-settings-group-title">{title}</h2>}
    <Card className="wd-settings-card gap-0 py-0 shadow-none">
      {title && inside && <h2 className="wd-settings-card-title">{title}</h2>}
      {children}
    </Card>
  </section>;
}

function Row({ label, Icon, children, target }: { label: string; Icon?: ComponentType; children: ReactNode; target?: string }) {
  const feedback = useContext(FeedbackContext), feedbackTarget = target || label;
  return <div className="wd-settings-row" data-settings-target={target} data-feedback-target={feedbackTarget} tabIndex={target ? -1 : undefined}>
    {Icon && <span className="wd-settings-icon"><Icon/></span>}
    <div className="wd-settings-copy"><h3>{label}</h3></div>
    <div className="wd-settings-value">{children}{feedback?.message && feedback.target===feedbackTarget && <FeedbackMessage feedback={feedback} inline/>}</div>
  </div>;
}

function LinkRow({ destination }: { destination: Destination }) {
  const { href, name, Icon } = destination;
  return <a className="wd-settings-row wd-settings-direct" href={href}>
    <span className="wd-settings-icon"><Icon/></span><span>{name}</span><ChevronRight aria-hidden="true"/>
  </a>;
}

function LinkButton({ href, children }: { href: string; children: ReactNode }) {
  return <Button asChild variant="outline" className="wd-settings-button"><a href={href}>{children}<ArrowRight aria-hidden="true"/></a></Button>;
}

function SelectRow({ label, value, onChange, values, disabled = false, target, unavailableLabel }: { label: string; value: string; onChange: (value: string) => void; values: Record<string, string>; disabled?: boolean; target?: string; unavailableLabel?: string }) {
  return <Row label={label} target={target}><NativeSelect aria-label={label} value={value} disabled={disabled} onChange={event => onChange(event.currentTarget.value)}>
    {unavailableLabel && !Object.hasOwn(values,value) && <option value={value} disabled>{unavailableLabel}</option>}
    {Object.entries(values).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
  </NativeSelect></Row>;
}

function DisplayNameRow({ label, target, value, placeholder, disabled, onSave }: {
  label: string; target: string; value: string; placeholder: string; disabled: boolean; onSave: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const trimmed = draft.trim();
  const tooLong = [...trimmed].length > 40;
  const canSave = !disabled && !tooLong && trimmed !== value;
  return <Row label={label} target={target}>
    <form className="wd-settings-display-name" onSubmit={event => {
      event.preventDefault();
      if (canSave) onSave(trimmed);
    }}>
      <Input type="text" aria-label={label} autoComplete="off" placeholder={placeholder} value={draft}
        onChange={event => setDraft(event.currentTarget.value)} disabled={disabled} aria-invalid={tooLong || undefined}
        aria-describedby={tooLong ? target + '-length' : undefined}/>
      <Button type="submit" variant="outline" size="sm" aria-label={'保存' + label} disabled={!canSave}>保存</Button>
      {tooLong && <small id={target + '-length'}>最多 40 个字符</small>}
    </form>
  </Row>;
}

function ThemeControls({ theme }: { theme: string }) {
  return <div className="wd-settings-theme" role="group" aria-label="界面主题">{['light', 'dark', 'system'].map(next =>
    <Button key={next} variant={theme === next ? 'secondary' : 'ghost'} data-settings-theme={next} aria-pressed={theme === next}>
      {next === 'light' ? '浅色' : next === 'dark' ? '深色' : '跟随系统'}
    </Button>
  )}</div>;
}

function Diagnostics({ data, diagnostics, busy, pending }: SettingsProps) {
  return <details className="wd-settings-diagnostics" open={diagnostics}>
    <summary><Wrench aria-hidden="true"/><span>服务与诊断</span><ChevronDown aria-hidden="true"/></summary>
    <div className="wd-settings-diagnostics-body">
      <h3>控制服务来源与版本</h3>
      <dl>
        <dt>工程位置</dt><dd>{data?.health?.program_base || '未读取'}</dd>
        <dt>实现版本</dt><dd className="wd-settings-version">{data?.health?.implementation || '未读取'}</dd>
        <dt>资料位置</dt><dd>{data?.storage?.library || '未读取'}</dd>
      </dl>
      <div className="wd-settings-diagnostic-actions">
        <Button variant="outline" className="wd-settings-button" data-settings="reload" disabled={busy}>重载空闲控制服务</Button>
        <Button variant="outline" className="wd-settings-button" data-settings="refresh" disabled={busy}>刷新状态</Button>
        <LinkButton href="#control/receipts">操作回执</LinkButton>
      </div>
      {pending && <details className="wd-settings-receipt"><summary>最近操作</summary><code>{pending}</code> · <a href="#control/receipts">查询回执</a></details>}
    </div>
  </details>;
}

function NoticeList({ bridge, choices }: { bridge: PreferencesBridge; choices: Snapshot['notifications'] }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [status, setStatus] = useState('正在读取任务提醒…');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setStatus('正在读取任务提醒…');
    bridge.getAttention().then(result => {
      if (!active) return;
      setNotices(Array.isArray(result.items) ? result.items : []);
      setStatus('');
    }).catch(error => {
      if (!active) return;
      setNotices([]);
      setStatus(error instanceof Error ? error.message : '无法读取任务提醒。');
    });
    return () => { active = false; };
  }, [bridge, refresh]);
  const visible = notices.filter(item => choices[item.kind] && /^#[a-z]/i.test(item.href));
  return <Group title="工作台提醒">
    {status ? <p className="wd-settings-empty" role="status">{status}</p> : visible.length ? visible.map((item, index) =>
      <a className="wd-settings-row wd-settings-direct" href={item.href} key={item.href + ':' + item.kind + ':' + index}>
        <span className="wd-settings-icon"><Bell/></span><span>{item.title}<small>{noticeLabels[item.kind]}</small></span><ChevronRight aria-hidden="true"/>
      </a>
    ) : <p className="wd-settings-empty">当前没有相关提醒。</p>}
    <div className="wd-settings-actions"><Button variant="outline" className="wd-settings-button" onClick={() => setRefresh(count => count + 1)}>刷新提醒</Button></div>
  </Group>;
}

function useSettingsRead<T>(route: string) {
  const [data, setData] = useState<T>();
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState('正在读取…');
  useEffect(() => {
    const request = new AbortController();
    setStatus('正在读取…');
    fetch('api/v1/' + route, { cache: 'no-store', signal: request.signal }).then(async response => {
      if (!response.ok) throw Error('无法读取本机资料，请重试。');
      const value: T = await response.json();
      if (!request.signal.aborted) { setData(value); setStatus(''); }
    }).catch(() => {
      if (!request.signal.aborted) setStatus('无法读取本机资料，请重试。');
    });
    return () => request.abort();
  }, [route, revision]);
  return { data, status, refresh: () => setRevision(value => value + 1) };
}

function StorageSettings() {
  const { data, status, refresh } = useSettingsRead<{
    current_data: string; backups: { id: string; created: number }[];
    restores: { state: string }[];
  }>('libraries');
  const support = (window as Window & { WorkdeskSupport?: {
    directory(): Promise<string | null>;
    command(kind: string, payload: object): Promise<unknown>;
  } }).WorkdeskSupport;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function saveBackup(importing: boolean) {
    if (!support || busy) return;
    setBusy(true);
    setMessage('');
    try {
      const directory = importing ? await support.directory() : null;
      if (importing && !directory) return;
      await support.command(importing ? 'library.import' : 'backup.create', directory ? { directory } : {});
      setMessage(importing ? '备份已登记，可在备份管理中核对。' : '备份已保存。');
      refresh();
    } catch {
      setMessage('备份操作未能完成，请到备份管理核对。');
    } finally { setBusy(false); }
  }

  const mostRecentBackup = data?.backups.reduce((latest, entry) => Math.max(latest, entry.created), 0);
  const staged = data?.restores.filter(entry => entry.state === 'STAGED').length;
  return <>
    <Group title="资料库" inside>
      <Row label="当前资料库"><span>{data?.current_data.split(/[\\/]/).filter(Boolean).at(-1) || 'Workdesk 资料库'}</span></Row>
      {data && <details className="wd-settings-path-disclosure"><summary>查看保存位置</summary><p>{data.current_data}</p></details>}
      {status && <div className="wd-settings-empty" role="status">{status}
        {!status.startsWith('正在') && <div className="wd-settings-actions"><Button className="wd-settings-button" variant="outline" onClick={refresh}>重试</Button></div>}
      </div>}
    </Group>
    <Group title="备份" inside>
      <Row label="创建本地备份"><Button className="wd-settings-button wd-settings-primary" disabled={busy || !data} onClick={() => void saveBackup(false)}>创建备份</Button></Row>
      <Row label="最近备份"><span>{mostRecentBackup ? new Date(mostRecentBackup * 1000).toLocaleString('zh-CN', { hour12: false }) : data ? '暂无备份' : '—'}</span></Row>
      <Row label="备份副本"><LinkButton href="#libraries">管理备份</LinkButton></Row>
    </Group>
    <Group title="恢复与迁移" inside>
      <Row label="导入本机备份"><Button className="wd-settings-button" variant="outline" disabled={busy || !data} onClick={() => void saveBackup(true)}>选择备份</Button></Row>
      <Row label="恢复暂存"><span>{staged ? `${staged} 份待切换` : data ? '暂无暂存恢复' : '—'}</span><LinkButton href="#libraries">管理恢复</LinkButton></Row>
    </Group>
    {message && <p className="wd-settings-empty" role="status">{message}</p>}
  </>;
}

type RunSummary = {
  title: string; state: string; local_worker_alive?: boolean; pending_approvals?: number;
  block_reasons?: string[]; manual_control?: { state: string }; work_items?: { manual: boolean }[];
};
function OperationsSettings() {
  const { data, status, refresh } = useSettingsRead<{
    runs: (RunSummary & { task_id: string })[];
    api_chats: (RunSummary & { chat_id: string })[];
    limited: boolean;
  }>('monitor?scope=attention');
  const active = new Set(['RESERVED', 'SEND_INTENT', 'RUNNING']);
  const transient = new Set(['CURRENT_TURN', 'LOCAL_WORKER', 'EXIT_UNPROVED']);
  const labels: Record<string, string> = { WAITING_APPROVAL: '待审批', WAITING_INPUT: '待补充', STOP_REQUESTED: '正在停止' };
  const runs = [...(data?.runs || []).map(entry => ({ ...entry, href: '#control/task/' + encodeURIComponent(entry.task_id) })),
    ...(data?.api_chats || []).map(entry => ({ ...entry, href: '#chat/' + encodeURIComponent(entry.chat_id) }))];
  const pending: { title: string; label: string; href: string }[] = [];
  const running: typeof pending = [];
  for (const { title, state, href, ...entry } of runs) {
    const manual = entry.manual_control?.state === 'MANUAL' || entry.work_items?.some(item => item.manual);
    const needsAttention = manual || entry.pending_approvals || labels[state] || state.endsWith('_UNKNOWN') ||
      entry.block_reasons?.some(reason => !active.has(state) || !transient.has(reason));
    if (needsAttention) pending.push({ title, href, label: manual ? '人工处理中' : labels[state] || '待核对' });
    else if (active.has(state) || entry.local_worker_alive) running.push({ title, href, label: '运行中' });
  }
  function list(title: string, list: typeof pending, empty: string) {
    return <Group title={title} inside>
      {status ? <p className="wd-settings-empty" role="status">{status}</p> : list.length ? list.slice(0, 5).map(entry =>
        <a className="wd-settings-row wd-settings-direct" href={entry.href} key={entry.href}>
          <span className="wd-settings-icon"><History/></span><span>{entry.title}<small>{entry.label}</small></span><ChevronRight/>
        </a>) : <p className="wd-settings-empty">{empty}</p>}
      {(list.length > 5 || data?.limited) && <Row label="其余工作"><LinkButton href="#monitor">查看运行监控</LinkButton></Row>}
    </Group>;
  }
  return <>
    {list('待核对的工作', pending, '当前没有待核对的工作。')}
    {list('正在运行', running, '当前没有运行中的工作。')}
    <Group title="恢复工具" inside>
      <Row label="本地备份与恢复"><LinkButton href="#settings/storage">管理资料库</LinkButton></Row>
      <Row label="任务状态"><Button className="wd-settings-button" variant="outline" onClick={refresh}>刷新</Button><LinkButton href="#monitor">运行监控</LinkButton></Row>
    </Group>
    <details className="wd-settings-diagnostics">
      <summary><Wrench/><span>更多运行工具</span><ChevronDown/></summary>
      <div className="wd-settings-diagnostics-body wd-settings-tools">
        <LinkButton href="#operations">恢复工具与诊断</LinkButton>
        <LinkButton href="#settings/diagnostics">服务诊断</LinkButton>
        <LinkButton href="#control/receipts">操作回执</LinkButton>
      </div>
    </details>
  </>;
}

export function SettingsPage(props: SettingsProps) {
  const { page = '', theme, appearance, focus } = props;
  const active = destinations.find(item => item.href === '#settings/' + page);
  const [search, setSearch] = useState('');
  const [provider, setProvider] = useState<WorkProvider>('codex');
  const [pendingPreference, setPendingPreference] = useState(false);
  const [message, setMessage] = useState('');
  const [messageKind, setMessageKind] = useState<'saving'|'success'|'error'>('success');
  const [messageTarget, setMessageTarget] = useState<string | null>(null);
  const feedbackGeneration = useRef(0);
  const [restore, setRestore] = useState<'defaults'|'backup'|null>(null);
  const bridge = preferencesBridge();
  const [preferences, setPreferences] = useState(() => bridge?.get());
  const root = useRef<HTMLElement>(null);
  const sidebar = document.getElementById('wb-settings-sidebar');
  useEffect(() => {
    const update = () => setPreferences(preferencesBridge()?.get());
    window.addEventListener('workdesk:preferences', update);
    update();
    return () => window.removeEventListener('workdesk:preferences', update);
  }, []);
  useEffect(() => {
    setSearch('');
    setMessage('');
    setMessageTarget(null); feedbackGeneration.current++;
    if (!focus) return;
    const target = [...root.current?.querySelectorAll<HTMLElement>('[data-settings-target]') || []]
      .find(row => row.dataset.settingsTarget === focus);
    if (target) {
      target.scrollIntoView({ block: 'nearest' });
      target.focus({ preventScroll: true });
    }
  }, [page, focus]);
  useEffect(() => {
    if (!message || messageKind!=='success') return;
    const timer = window.setTimeout(() => { setMessage(''); setMessageTarget(null); }, 3000);
    return () => window.clearTimeout(timer);
  }, [message, messageKind]);

  function locateFeedback() {
    const row = document.activeElement?.closest<HTMLElement>('[data-feedback-target]');
    setMessageTarget(row?.dataset.feedbackTarget || null);
    return feedbackGeneration.current;
  }

  async function save(key: keyof Snapshot, value: Snapshot[keyof Snapshot]) {
    if (!bridge || pendingPreference) return;
    setPendingPreference(true);
    const generation = locateFeedback();
    const labels: Partial<Record<keyof Snapshot,string>> = {
      launch:'启动偏好', close:'关闭窗口偏好', opener:'文件打开方式', userNickname:'用户昵称', assistantNickname:'AI 称呼',
      notifications:'通知偏好', connectionNoticeSeconds:'连接提示时长', workAccess:'Work 权限偏好', chatDefaults:'Chat 默认设置', workDirectory:'工作目录',
    };
    const label = labels[key] || '设置';
    setMessageKind('saving');
    setMessage('正在保存' + label + '…');
    try { setPreferences(await bridge.save(key, value)); if(generation===feedbackGeneration.current){setMessageKind('success');setMessage(label + '已保存。');} }
    catch (error) { if(generation===feedbackGeneration.current){setMessageKind('error');setMessage(error instanceof Error ? '保存失败：' + error.message : '无法保存设置，请重试。');} }
    finally { setPendingPreference(false); }
  }

  async function changeWorkDirectory() {
    const support = (window as Window & { WorkdeskSupport?: { directory(): Promise<string | null> } }).WorkdeskSupport;
    if (!bridge || !support || pendingPreference) return;
    setPendingPreference(true);
    const generation = locateFeedback();
    setMessageKind('saving');
    setMessage('正在选择工作目录…');
    try {
      const path = await support.directory();
      if (path) {
        if(generation===feedbackGeneration.current)setMessage('正在保存工作目录…');
        setPreferences(await bridge.save('workDirectory', path));
        if(generation===feedbackGeneration.current){setMessageKind('success');setMessage('工作目录已保存。');}
      } else setMessage('');
    } catch (error) { if(generation===feedbackGeneration.current){setMessageKind('error');setMessage(error instanceof Error ? error.message : '无法选择工作目录。');} }
    finally { setPendingPreference(false); }
  }

  const blocked = pendingPreference || !!preferences?.recovery;
  async function confirmRestore(){
    if(!restore || !bridge || pendingPreference)return;
    setPendingPreference(true);
    setMessageTarget(null);
    setMessageKind('saving');setMessage('正在恢复偏好…');
    try{
      setPreferences(await bridge.recover(restore));
      setRestore(null);
      setMessageKind('success');
      setMessage('偏好已恢复，损坏原件已保留。');
    }catch(error){setMessageKind('error');setMessage(error instanceof Error ? error.message : '恢复失败，请刷新后核对。');}
    finally{setPendingPreference(false);}
  }
  let content: ReactNode;
  switch (page) {
    case 'general':
      content = preferences ? <>
        <Group title="启动与窗口">
          <SelectRow label="启动时打开" target="launch" value={preferences.launch} onChange={value => void save('launch', value)} disabled={blocked}
            values={{chat: 'Chat', work: 'Work', last: '上次页面'}}/>
          <SelectRow label="关闭窗口前确认" target="close" value={preferences.close} onChange={value => void save('close', value)} disabled={blocked}
            values={{keyboard: '使用关闭快捷键时', always: '每次关闭时', never: '从不'}}/>
        </Group>
        <Group title="对话称呼">
          <DisplayNameRow label="用户昵称" target="user-nickname" value={preferences.userNickname ?? ''}
            placeholder="User" disabled={blocked} onSave={value => void save('userNickname', value)}/>
          <DisplayNameRow label="AI 称呼" target="assistant-nickname" value={preferences.assistantNickname ?? ''}
            placeholder="按角色和模型显示" disabled={blocked} onSave={value => void save('assistantNickname', value)}/>
        </Group>
        <Group title="文件">
          <SelectRow label="文件默认打开方式" target="opener" value={preferences.opener} onChange={value => void save('opener', value)} disabled={blocked}
            values={{system: '系统默认应用', explorer: '在文件资源管理器中显示'}}/>
        </Group>
      </> : <p role="status">暂无可用的桌面偏好设置。</p>;
      break;
    case 'appearance':
      content = <>
        <Group title="主题"><Row label="界面主题" Icon={theme === 'dark' ? Moon : Sun}><ThemeControls theme={theme}/></Row></Group>
        <AppearancePanel a={appearance}/>
      </>;
      break;
    case 'shortcuts':
      content = <Group title="应用快捷键">{shortcutRows.map(([label, keys]) =>
        <Row key={label} label={label}><span className="wd-settings-keys">{keys.map(key => <kbd key={key}>{key}</kbd>)}</span></Row>
      )}</Group>;
      break;
    case 'notifications':
      content = bridge && preferences ? <>
        <Group title="任务通知">{Object.entries(noticeLabels).map(([kind, label]) =>
          <Row key={kind} label={label}><input className="wd-settings-switch" type="checkbox" role="switch" aria-label={label}
            checked={preferences.notifications[kind as NoticeKind]} disabled={blocked}
            onChange={event => void save('notifications', { ...preferences.notifications, [kind]: event.currentTarget.checked })}/></Row>
        )}</Group>
        <Group title="连接提示">
          <SelectRow label="连接提示时长" target="connection-duration" value={`${preferences.connectionNoticeSeconds ?? 10}s`}
            values={{ '5s': '5 秒', '10s': '10 秒', '15s': '15 秒', '30s': '30 秒', '0s': '永久显示' }}
            onChange={value => void save('connectionNoticeSeconds', Number.parseInt(value, 10))} disabled={blocked}/>
        </Group>
        <NoticeList bridge={bridge} choices={preferences.notifications}/>
      </> : <p role="status">暂无可用的任务通知设置。</p>;
      break;
    case 'chat': {
      const chat=preferences?.chatDefaults??{route:'api',model:'deepseek-flash',provider:'codex'};
      const apiProvider = chat.apiProvider || 'deepseek';
      const models = preferences?.chatApiModels || {};
      const modelsFor = (name: string) => Object.fromEntries((models[name] || []).map(model=>[model,(name==='deepseek'?'DeepSeek':name==='qwen'?'Qwen':name)+' · '+model]));
      content = <>
        <Group title="聊天渠道">
          {preferences ? <>
            <SelectRow label="新聊天默认渠道" value={chat.route} values={{api:'API 连接',web:'官网聊天'}}
              onChange={route=>void save('chatDefaults',{...chat,route})} disabled={blocked}/>
            {chat.route==='api' && <>
              <SelectRow label="默认 API 厂商" value={apiProvider} values={Object.fromEntries(Object.keys(models).map(key=>[key,key==='qwen'?'Qwen':'DeepSeek']))}
                onChange={value=>void save('chatDefaults',{...chat,apiProvider:value,model:models[value][0]})} disabled={blocked || !Object.keys(models).length}/>
              <SelectRow label="API 默认模型" value={chat.model} values={modelsFor(apiProvider)}
                unavailableLabel={(chat.model || '未选择型号') + ' · 当前 API 厂商不可用，请重新选择'}
                onChange={model=>void save('chatDefaults',{...chat,apiProvider,model})} disabled={blocked || !models[apiProvider]?.length}/>
            </>}
            {chat.route==='web' && <SelectRow label="默认官网" value={chat.provider} values={{codex:'ChatGPT',deepseek:'DeepSeek',qwen:'Qwen'}}
              onChange={provider=>void save('chatDefaults',{...chat,provider})} disabled={blocked}/>}
          </> : <Row label="启动默认渠道"><span>DeepSeek API</span></Row>}
          <Row label="打开聊天"><LinkButton href="#chat">打开 Chat</LinkButton></Row>
        </Group>
        <Group title="聊天记录"><Row label="历史与归档"><LinkButton href="#records">查看记录</LinkButton></Row></Group>
      </>;
      break;
    }
    case 'access':
      content = preferences ? <>
        <Group title="新工作默认权限">
          <Row label="工作端"><div className="wd-settings-theme" role="group" aria-label="权限偏好工作端">{Object.entries(providerLabels).map(([id, name]) =>
            <Button key={id} variant="ghost" aria-pressed={provider === id} onClick={() => setProvider(id as WorkProvider)}>{name}</Button>
          )}</div></Row>
          <SelectRow label="默认审批方式" target="default" value={preferences.workAccess[provider]} disabled={blocked}
            values={provider === 'deepseek' ? { workspace: '请求批准' } : { workspace: '请求批准', auto: '帮我批准' }}
            onChange={value => void save('workAccess', { ...preferences.workAccess, [provider]: value })}/>
        </Group>
        <Group title="本次工作的访问范围">
          <Row label="文件、命令与网络"><LinkButton href="#work">在 Work 中查看</LinkButton></Row>
          <p className="wd-settings-note">开始工作前，可按工作端支持的方式确认本次权限。完全访问在本次工作中单独选择。执行限制由所选工作端落实。</p>
        </Group>
      </> : <p role="status">暂无可用的权限偏好。</p>;
      break;
    case 'records':
      content = <>
        <Group title="对话与任务记录">
          <Row label="已归档记录" target="archives"><LinkButton href="#archives">查看与恢复归档</LinkButton></Row>
          <Row label="当前记录" target="export"><LinkButton href="#records">查看与导出</LinkButton></Row>
        </Group>
        <Group title="文件位置">
          {typeof preferences?.workDirectory === 'string' &&
            <Row label="项目外工作默认目录"><span className="wd-settings-path">{preferences.recovery ? '读取失败，待确认目录' : preferences.workDirectory || 'Workdesk 默认目录'}</span>
              <Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => void changeWorkDirectory()}>更改</Button>
              {preferences.workDirectory && <Button variant="outline" className="wd-settings-button" disabled={blocked} onClick={() => void save('workDirectory', '')}>使用默认</Button>}
            </Row>}
          <Row label="资料库、备份与迁移"><LinkButton href="#settings/storage">打开本地存储</LinkButton></Row>
          <Row label="项目目录"><LinkButton href="#projects">查看项目</LinkButton></Row>
        </Group>
      </>;
      break;
    case 'diagnostics':
      content = <>
        <Group title="支持"><Row label="Workdesk 使用"><Button variant="outline" className="wd-settings-button" data-wb="help">查看使用说明</Button></Row>
          <Row label="运行状态与恢复"><LinkButton href="#settings/operations">查看运行</LinkButton></Row>
        </Group>
        <Group title="本地偏好">
          <Row label="偏好文件状态">{preferences?.recovery ? '损坏，等待确认恢复' : preferences ? '已就绪' : '尚未读取'}</Row>
          {preferences?.lastRecovery && <details><summary>上次恢复记录</summary><p>{new Date(preferences.lastRecovery.at * 1000).toLocaleString('zh-CN')} · {preferences.lastRecovery.source==='backup'?'使用有效副本':'使用默认设置'}</p>
            <p>原件校验值 <code style={{overflowWrap:'anywhere'}}>{preferences.lastRecovery.original}</code></p></details>}
        </Group>
        <Diagnostics {...props}/>
      </>;
      break;
    case 'accounts': content = <SettingsAccounts key={(focus||'')+(preferences?.recovery?.revision||'ready')} initialProvider={focus} preferenceBlocked={blocked}/>; break;
    case 'usage': content = focus === 'activity' || focus === 'calls'
      ? <SettingsTokenUsage key={focus} mode={focus}/> : <SettingsUsage/>; break;
    case 'storage': content = <StorageSettings/>; break;
    case 'extensions': content = <SettingsExtensions/>; break;
    case 'management': content = <SettingsManagementSummary/>; break;
    case 'operations': content = <OperationsSettings/>; break;
    default:
      content = <>
        <div className="wd-settings-overview">
          {groups.map(group => <Group key={group.title} title={group.title}>{group.items.map(destination => <LinkRow key={destination.href} destination={destination}/>)}</Group>)}
        </div>
      </>;
  }

  const navigation = <SettingsNavigation page={active ? page : ''} search={search} setSearch={setSearch}/>;
  return <FeedbackContext.Provider value={{target:messageTarget,message,kind:messageKind}}><>
    {sidebar && createPortal(navigation, sidebar)}
    <section className="wd-settings" ref={root} aria-labelledby="wd-settings-title" data-react-settings>
      <header className="wd-settings-header"><h1 id="wd-settings-title" className="wd-settings-title">{active?.name || '设置'}</h1></header>
      {['general','notifications','chat','access'].includes(page) && <p className="wd-settings-save-hint">
        {page==='general'?'下拉选项更改后立即保存；昵称需点击“保存”。':'选项更改后立即保存。'}</p>}
      {message && !messageTarget && <FeedbackMessage feedback={{target:null,message,kind:messageKind}}/>}
      <details className={'wd-settings-mobile-nav' + (sidebar ? '' : ' standalone')}><summary>设置分类与搜索</summary>{navigation}</details>
      {preferences?.recovery && <section className="wd-settings-card wd-settings-group" role="alert">
        <h2>工作台偏好需要恢复</h2>
        <p>常规、通知、Chat 和 Work 默认值暂未读取。项目外目录与审批策略须重新确认。原文件会保留。</p>
        {!restore ? <div className="wd-settings-actions">
          {preferences.recovery.backup && <Button variant="outline" onClick={()=>setRestore('backup')}>查看有效副本</Button>}
          <Button variant="outline" onClick={()=>setRestore('defaults')}>查看默认设置</Button>
          <Button variant="outline" onClick={()=>bridge?.reload().catch(error=>{setMessageKind('error');setMessage(error.message);})}>重新读取偏好</Button>
        </div> : <>
          <p>项目外目录：{restore==='backup' && preferences.recovery.backup?.workDirectory || 'Workdesk 默认目录（资料库下的 unfiled-work）'}</p>
          <p>审批策略：{Object.entries(providerLabels).map(([key,name])=>name+' '+(restore==='backup' && preferences.recovery?.backup?.workAccess[key as WorkProvider]==='auto'?'自动审核':'请求批准')).join('，')}</p>
          <div className="wd-settings-actions"><Button disabled={pendingPreference} onClick={()=>void confirmRestore()}>{restore==='backup'?'确认恢复副本':'确认恢复默认设置'}</Button>
            <Button variant="outline" disabled={pendingPreference} onClick={()=>setRestore(null)}>取消恢复</Button></div>
        </>}
      </section>}
      {content}
    </section>
  </></FeedbackContext.Provider>;
}
