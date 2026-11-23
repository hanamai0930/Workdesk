import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, FileOutput, FileText, GitFork, Link2, List, MessageSquare, Paperclip, Pin, PinOff, Plus, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { nativeTrace } from './native-events';
import './conversation-summary.css';

export type SummaryTab = 'chat' | 'page' | 'presentation' | 'files';
export type SummaryEntry = {
  id: string;
  title: string;
  detail?: string;
  href?: string;
  file?: string;
  tab?: SummaryTab;
  status?: string;
};

export type ConversationSummaryProps = {
  title?: string;
  pinned?: boolean;
  outputs?: SummaryEntry[];
  sideChats?: SummaryEntry[];
  agents?: SummaryEntry[];
  sources?: SummaryEntry[];
  onOpenFile?: (entry: SummaryEntry) => void;
  onOpenTab?: (tab: SummaryTab, entry?: SummaryEntry) => void;
  onAttach?: () => void;
};

type SummaryContent = Pick<ConversationSummaryProps, 'title' | 'outputs' | 'agents' | 'sources'>;
type ChatSummaryRow = {
  route?: string;
  title?: string;
  messages?: {
    attachments?: { id: string; name: string }[];
    sources?: { url: string; title?: string }[];
  }[];
};
type WorkDetail = {
  task: { id: string; title?: string; spec: { materials?: Record<string, unknown>; runtime_mode?: string } };
  attempts?: { id: string; session_id?: string; state?: string; native_history_reverted?: boolean }[];
  events?: {
    seq: number;
    kind: string;
    payload: { result_id?: string; files?: Record<string, unknown> };
  }[];
  has_more_events?: boolean;
};
type SideConversation = { open: boolean; id?: string | null; title?: string; route?: string };

function currentSideConversation(): SideConversation | null | undefined {
  return window.WorkdeskWorkbench?.sideConversation?.();
}

export function useSideConversationSummary(): SummaryEntry[] | undefined {
  const [side, setSide] = useState(currentSideConversation);
  useEffect(() => {
    const sync = () => setSide(currentSideConversation());
    document.addEventListener('workdesk:sidechat-state', sync);
    sync();
    return () => document.removeEventListener('workdesk:sidechat-state', sync);
  }, []);
  if (side === undefined) return undefined;
  if (!side?.open) return [];
  const detail = side.route === 'api' ? 'API 连接' : side.route === 'web' ? '官方网页' : undefined;
  return [{
    id: side.id || 'active-side-chat',
    title: side.title?.trim() || (side.id ? '侧边聊天' : '未发送的侧边聊天'),
    href: side.id ? '#chat/' + encodeURIComponent(side.id) : undefined,
    tab: 'chat',
    detail,
  }];
}

function externalURL(href?: string) {
  if (!href) return;
  try {
    const url = new URL(href);
    if ((url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password) return url;
  } catch { /* Invalid URL in an old receipt. */ }
}

export function apiSummary(record?: ChatSummaryRow | null): SummaryContent {
  if (!record || record.route !== 'api') return { title: record?.title };
  const sources = new Map<string, SummaryEntry>();
  for (const message of record.messages ?? []) {
    for (const attachment of message.attachments ?? []) {
      sources.set('file:' + attachment.id, { id: 'file:' + attachment.id, title: attachment.name, detail: '聊天附件' });
    }
    for (const source of message.sources ?? []) {
      const url = externalURL(source.url);
      if (url) sources.set(url.href, { id: url.href, title: source.title?.trim() || url.href, href: url.href, detail: url.hostname });
    }
  }
  return { title: record.title, sources: [...sources.values()] };
}

export function workSummary(detail?: WorkDetail | null): SummaryContent {
  if (!detail) return {};
  const taskLink = encodeURIComponent(detail.task.id);
  const outputs = new Map<string, SummaryEntry>();
  const agents = new Map<string, SummaryEntry>();
  for (const event of detail.events ?? []) {
    if (event.kind !== 'work_result_captured' || !event.payload?.result_id || !event.payload.files) continue;
    for (const file of Object.keys(event.payload.files)) {
      const id = event.payload.result_id + ':' + file;
      outputs.set(id, { id, title: file.split('/').at(-1) || file, detail: file, href: '#results/' + taskLink });
    }
  }
  for (const attempt of detail.attempts ?? []) {
    if (attempt.native_history_reverted) continue;
    for (const agent of nativeTrace(detail, attempt).agents) {
      agents.set(agent.id, { id: agent.id, title: agent.name, detail: agent.id, status: agent.status, href: '#control/task/' + taskLink });
    }
  }
  const materials = detail.task.spec.materials;
  return {
    title: detail.task.title,
    outputs: outputs.size || (detail.events && !detail.has_more_events) ? [...outputs.values()] : undefined,
    agents: agents.size || (detail.events && !detail.has_more_events && detail.task.spec.runtime_mode === 'official')
      ? [...agents.values()] : undefined,
    sources: materials && Object.keys(materials).map(file => ({ id: file, file, tab: 'files', title: file.split('/').at(-1) || file, detail: file })),
  };
}

function SummaryRow({ entry, onOpenFile, onOpenTab }: {
  entry: SummaryEntry;
  onOpenFile?: ConversationSummaryProps['onOpenFile'];
  onOpenTab?: ConversationSummaryProps['onOpenTab'];
}) {
  const external = externalURL(entry.href);
  const href = external?.href || (entry.href?.startsWith('#') ? entry.href : undefined);
  const tab = entry.tab;
  const open = entry.file && onOpenFile
    ? () => onOpenFile(entry)
    : tab && onOpenTab ? () => onOpenTab(tab, entry) : undefined;
  const text = <>
    <span className="wd-summary-entry-name" title={entry.title}>{entry.title}</span>
    {entry.status && <small className="wd-summary-entry-status" title={entry.status}>{entry.status}</small>}
    {(open || href) && <ArrowUpRight aria-hidden="true"/>}
    {entry.detail && entry.detail !== entry.title &&
      <small className="wd-summary-entry-detail" title={entry.detail}>{entry.detail}</small>}
  </>;
  if (open) return <button type="button" className="wd-summary-entry" onClick={open}>{text}</button>;
  if (href) return <a className="wd-summary-entry" href={href} target={external ? '_blank' : undefined}
    rel={external ? 'noopener noreferrer' : undefined}>{text}</a>;
  return <div className="wd-summary-entry">{text}</div>;
}

function SummaryGroup({ title, icon, entries, empty, unknown, action, onOpenFile, onOpenTab }: {
  title: string;
  icon: ReactNode;
  entries?: SummaryEntry[];
  empty: string;
  unknown: string;
  action?: ReactNode;
  onOpenFile?: ConversationSummaryProps['onOpenFile'];
  onOpenTab?: ConversationSummaryProps['onOpenTab'];
}) {
  return <section className="wd-summary-group" aria-label={title}>
    <div className="wd-summary-group-heading">
      <h3>{icon}{title}</h3>
      {action}
    </div>
    {entries?.length ? <ul className="wd-summary-entries">
      {entries.map(entry => <li key={entry.id}><SummaryRow entry={entry} onOpenFile={onOpenFile} onOpenTab={onOpenTab}/></li>)}
    </ul> : <p className="wd-summary-empty">{entries ? empty : unknown}</p>}
  </section>;
}

export function ConversationSummary({ title, pinned, outputs, sideChats, agents, sources, onAttach, onOpenFile, onOpenTab }: ConversationSummaryProps) {
  return <div className="wd-summary-content" data-pinned={pinned || undefined}>
    {title && <p className="wd-summary-title" title={title}>{title}</p>}
    <SummaryGroup title="输出" icon={<FileOutput aria-hidden="true"/>} entries={outputs}
      empty="暂无已保存的成果" unknown="未记录成果" onOpenFile={onOpenFile} onOpenTab={onOpenTab}
      action={onOpenTab && <button type="button" className="wd-summary-action" onClick={() => onOpenTab('page')}>
        <Plus aria-hidden="true"/>新建页面
      </button>}/>
    <SummaryGroup title="侧边聊天" icon={<MessageSquare aria-hidden="true"/>} entries={sideChats}
      empty="暂无侧边聊天" unknown="未记录关联的聊天" onOpenTab={onOpenTab}
      action={onOpenTab && <button type="button" className="wd-summary-action" onClick={() => onOpenTab('chat')}>打开</button>}/>
    <SummaryGroup title="子代理" icon={<GitFork aria-hidden="true"/>} entries={agents}
      empty="暂无子代理活动" unknown="未记录子代理活动"/>
    <SummaryGroup title="来源" icon={<Link2 aria-hidden="true"/>} entries={sources}
      empty="暂无来源" unknown="未记录来源" onOpenFile={onOpenFile} onOpenTab={onOpenTab}
      action={onAttach && <button type="button" className="wd-summary-action" onClick={onAttach}>
        <Paperclip aria-hidden="true"/>添加附件
      </button>}/>
  </div>;
}

function notifyVisibility() {
  document.dispatchEvent(new Event('workdesk:summary-visibility'));
}

export function ConversationSummaryButton(props: ConversationSummaryProps) {
  const id = useId();
  const anchor = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const sideChats = useSideConversationSummary();
  const [open, setOpen] = useState(!!props.pinned);
  const [pinned, setPinned] = useState(!!props.pinned);

  useLayoutEffect(() => {
    const node = panel.current;
    const button = anchor.current;
    if (!open || !node || !button) {
      notifyVisibility();
      return;
    }
    const host = button.closest('#main') || button.closest('[data-ui-module]');
    const margin = 12;
    function align() {
      if (!node || !button) return;
      const bounds = host?.getBoundingClientRect();
      const start = Math.max(0, bounds?.left ?? 0) + margin;
      const end = Math.min(document.documentElement.clientWidth, bounds?.right ?? window.innerWidth) - margin;
      const right = button.getBoundingClientRect().right;
      node.style.maxWidth = Math.max(1, end - start) + 'px';
      const width = node.offsetWidth;
      const left = Math.max(start, Math.min(right - width, end - width));
      node.style.setProperty('--wd-summary-inline-end', right - left - width + 'px');
      notifyVisibility();
    }
    align();
    window.addEventListener('resize', align);
    const observer = new ResizeObserver(align);
    observer.observe(host || button);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', align);
      queueMicrotask(notifyVisibility);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!pinned && event.target instanceof Node && !anchor.current?.contains(event.target)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      setOpen(false);
      toggle.current?.focus();
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open, pinned]);

  function closeAndAct(action: () => void) {
    if (!pinned) setOpen(false);
    action();
  }

  return <div className="wd-summary-anchor" ref={anchor}>
    <Button ref={toggle} type="button" className="wk-icon wd-summary-toggle" variant="ghost" size="icon"
      aria-label="对话摘要" title="对话摘要" aria-controls={open ? id : undefined} aria-expanded={open}
      onClick={() => setOpen(!open)}><List aria-hidden="true"/></Button>
    {open && <section ref={panel} id={id} aria-label="对话摘要面板" className="wd-summary-panel"
      data-conversation-summary-panel="" data-pinned={pinned || undefined}
      onClick={event => {
        if (event.target instanceof Element && event.target.closest('a[href^="#"]')) setOpen(false);
      }}>
      <header className="wd-summary-header">
        <h2><FileText aria-hidden="true"/>对话摘要</h2>
        <div>
          <Button type="button" variant="ghost" size="icon-xs" className="wd-summary-pin"
            aria-label="固定摘要面板" aria-pressed={pinned} title={pinned ? '取消固定摘要面板' : '固定摘要面板'}
            onClick={() => setPinned(!pinned)}>{pinned ? <PinOff aria-hidden="true"/> : <Pin aria-hidden="true"/>}</Button>
          <Button type="button" variant="ghost" size="icon-xs" aria-label="关闭摘要面板"
            onClick={() => { setOpen(false); toggle.current?.focus(); }}><X aria-hidden="true"/></Button>
        </div>
      </header>
      <ConversationSummary {...props} pinned={pinned} sideChats={props.sideChats ?? sideChats}
        onOpenFile={props.onOpenFile && (entry => closeAndAct(() => props.onOpenFile?.(entry)))}
        onOpenTab={props.onOpenTab && ((tab, entry) => closeAndAct(() => props.onOpenTab?.(tab, entry)))}
        onAttach={props.onAttach && (() => closeAndAct(props.onAttach!))}/>
    </section>}
  </div>;
}
