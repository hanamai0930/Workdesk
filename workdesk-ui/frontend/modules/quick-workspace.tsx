import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowUpRight, FileText, FolderOpen, Maximize2, Minimize2, MessageSquarePlus, PanelRightClose, Plus, Presentation, Search, X } from 'lucide-react';
import './quick-workspace.css';

const QuickDocuments = lazy(async () => ({ default: (await import('./quick-documents')).QuickDocuments }));
const QuickFiles = lazy(async () => ({ default: (await import('./quick-files')).QuickFiles }));

type Tool = 'start' | 'page' | 'chat' | 'presentation' | 'files';
export type QuickTool = Exclude<Tool, 'start'>;
export type QuickToolRequest = { tool: QuickTool; file?: string };
type WorkspaceToolName = Exclude<QuickTool, 'chat'>;
type RecordLink = { id: string; title?: string; archived?: boolean };
type WorkspaceState = { open?: boolean; expanded?: boolean; current?: Tool; tabs?: QuickTool[] };
const workspaceStates = new Map<string, WorkspaceState>();
let pendingLibrary: Promise<string | undefined> | undefined;
function useWorkspaceKey(stateKey?: string) {
  const [resolved, setResolved] = useState<{ key: string; library: string }>();
  useEffect(() => {
    let cancelled = false;
    if (!stateKey) { setResolved(undefined); return; }
    const support = (window as Window & { WorkdeskSupport?: { request<T>(route: string): Promise<T> } }).WorkdeskSupport;
    if (!support) return;
    // Share only concurrent reads. A later mount checks the current library again.
    pendingLibrary ||= support.request<{ storage?: { library?: string } }>('settings')
      .then(data => data.storage?.library).finally(() => { pendingLibrary = undefined; });
    pendingLibrary.then(library => { if (!cancelled && library) setResolved({ key: stateKey, library }); }).catch(() => {});
    return () => { cancelled = true; };
  }, [stateKey]);
  return stateKey && resolved?.key === stateKey ? JSON.stringify([resolved.library, stateKey]) : undefined;
}
function readWorkspace(stateKey?: string): WorkspaceState {
  if (!stateKey) return {};
  if (workspaceStates.has(stateKey)) return workspaceStates.get(stateKey)!;
  try {
    const value = JSON.parse(sessionStorage.getItem('workdesk.workspace.view.' + stateKey) || '{}');
    if (!value || typeof value !== 'object') return {};
    return { open: value.open === true, expanded: value.expanded === true,
      current: typeof value.current === 'string' && ['start','page','presentation','files','chat'].includes(value.current) ? value.current as Tool : 'start',
      tabs: Array.isArray(value.tabs) ? value.tabs.filter((tab: unknown): tab is QuickTool => typeof tab === 'string' && ['page','presentation','files','chat'].includes(tab)) : [] };
  }
  catch { return {}; }
}
function rememberWorkspace(stateKey: string | undefined, value: WorkspaceState) {
  if (!stateKey) return;
  const next = { ...readWorkspace(stateKey), ...value };
  workspaceStates.set(stateKey, next);
  try { sessionStorage.setItem('workdesk.workspace.view.' + stateKey, JSON.stringify(next)); } catch { /* Layout remains available in this window. */ }
}

const tools = {
  page: { label: '新建页面', tab: '页面', Icon: FileText },
  chat: { label: '侧边聊天', tab: '侧边聊天', Icon: MessageSquarePlus },
  presentation: { label: '新建演示文稿', tab: '演示文稿', Icon: Presentation },
  files: { label: '打开文件', tab: '文件', Icon: FolderOpen },
} as const;

export function WorkspaceTool({ tool, projectId, initialQuery, onBack, onSaved }: {
  tool: WorkspaceToolName; projectId?: string | null; initialQuery?: string; onBack?: () => void;
  onSaved?: (item: {id: string}) => void;
}) {
  return <Suspense fallback={<p className="wd-quick-loading" role="status">正在打开…</p>}>
    {tool === 'files'
      ? <QuickFiles key={initialQuery} projectId={projectId} initialQuery={initialQuery} onBack={onBack}/>
      : <QuickDocuments kind={tool} onSaved={onSaved}/>}
  </Suspense>;
}

function webAddress(query: string) {
  if (!/^https?:\/\//i.test(query)) return '';
  try {
    const url = new URL(query);
    return url.username || url.password ? '' : url.href;
  } catch {
    return '';
  }
}

export function WorkspaceExpandButton({ expanded, onToggle, className = 'wd-quick-icon' }: {
  expanded: boolean; onToggle: () => void; className?: string;
}) {
  return <button type="button" className={className} data-workspace-expand aria-pressed={expanded}
    aria-label={expanded ? '退出全宽视图' : '扩展到整个聊天区'}
    aria-keyshortcuts="Control+Shift+F"
    title={(expanded ? '退出全宽视图' : '扩展到整个聊天区') + '（Ctrl+Shift+F）'} onClick={onToggle}>
    {expanded ? <><Minimize2 aria-hidden="true"/><span>退出全宽</span></> : <Maximize2 aria-hidden="true"/>}
  </button>;
}

export function useQuickWorkspaceView(enabled = true, stateKey?: string) {
  const callerKey = stateKey;
  stateKey = useWorkspaceKey(stateKey);
  const [open, setOpen] = useState(() => readWorkspace(stateKey).open === true);
  const [expanded, setExpanded] = useState(() => readWorkspace(stateKey).expanded === true);
  const [requestedTool, setRequestedTool] = useState<QuickToolRequest>();
  const [loadedKey, setLoadedKey] = useState(stateKey);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const interacted = useRef(false);
  useLayoutEffect(() => { interacted.current = false; }, [callerKey]);
  useEffect(() => {
    const saved = readWorkspace(stateKey);
    if (!interacted.current) { setOpen(saved.open === true); setExpanded(saved.expanded === true); setRequestedTool(undefined); }
    setLoadedKey(stateKey);
  }, [stateKey]);
  useEffect(() => { if (loadedKey === stateKey) rememberWorkspace(stateKey, { open, expanded }); }, [stateKey, loadedKey, open, expanded]);

  function close() {
    interacted.current = true;
    setOpen(false);
    setExpanded(false);
    toggleRef.current?.focus();
  }
  function toggle() { interacted.current = true; if (open) close(); else setOpen(true); }
  function toggleExpanded() { interacted.current = true; if (open) setExpanded(!expanded); }
  function openTool(tool: QuickTool, file?: string) {
    interacted.current = true;
    setOpen(true);
    setRequestedTool({ tool, file });
  }
  useEffect(() => {
    if (!enabled) return;
    const panel = panelRef.current;
    const reveal = () => { interacted.current = true; setOpen(true); };
    panel?.addEventListener('workdesk:sidechat', reveal);
    return () => panel?.removeEventListener('workdesk:sidechat', reveal);
  }, [enabled]);
  useEffect(() => {
    if (!enabled) return;
    function shortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || !event.ctrlKey || !event.shiftKey || event.altKey ||
          !toggleRef.current?.getClientRects().length || document.querySelector('dialog[open]')) return;
      const key = event.key.toLowerCase();
      if (key !== 'b' && !(key === 'f' && open)) return;
      event.preventDefault();
      if (key === 'b') toggle(); else toggleExpanded();
    }
    document.addEventListener('keydown', shortcut);
    return () => document.removeEventListener('keydown', shortcut);
  });
  return { open, expanded, requestedTool, toggleRef, panelRef, close, toggle, toggleExpanded, openTool };
}

export function QuickWorkspace({ projectId, records = [], currentChat, children, onClose, expanded = false, onToggleExpanded, requestedTool, stateKey }: {
  projectId?: string | null;
  records?: RecordLink[];
  currentChat?: string;
  children?: ReactNode;
  onClose: () => void;
  expanded?: boolean;
  onToggleExpanded?: () => void;
  requestedTool?: QuickToolRequest;
  stateKey?: string;
}) {
  const callerKey = stateKey;
  stateKey = useWorkspaceKey(stateKey);
  // Restoring a layout must not reopen an external conversation or replay a file request.
  function savedTabs() { return (readWorkspace(stateKey).tabs || []).filter((tab): tab is QuickTool => Object.hasOwn(tools, tab) && tab !== 'chat'); }
  const [current, setCurrent] = useState<Tool>(() => { const saved = readWorkspace(stateKey).current; return saved && savedTabs().includes(saved as QuickTool) ? saved : 'start'; });
  const [tabs, setTabs] = useState<QuickTool[]>(savedTabs);
  const [query, setQuery] = useState('');
  const [fileQuery, setFileQuery] = useState('');
  const [loadedKey, setLoadedKey] = useState(stateKey);
  const sideChat = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const queryInput = useRef<HTMLInputElement>(null);
  const interacted = useRef(false);
  useLayoutEffect(() => { interacted.current = false; }, [callerKey]);
  useEffect(() => {
    const restored = savedTabs(), saved = readWorkspace(stateKey).current;
    if (!interacted.current) {
      setTabs(restored); setCurrent(saved && restored.includes(saved as QuickTool) ? saved : 'start');
      setQuery(''); setFileQuery('');
    }
    setLoadedKey(stateKey);
  }, [stateKey]);
  useEffect(() => { if (loadedKey === stateKey) rememberWorkspace(stateKey, { current, tabs }); }, [stateKey, loadedKey, current, tabs]);
  useEffect(() => {
    const active = workspaceRef.current?.querySelector('.wd-quick-tab[aria-current="page"]');
    (active?.closest('.wd-quick-tab-group') || active)?.scrollIntoView({ block:'nearest', inline:'nearest' });
  }, [current, tabs]);
  const text = query.trim();
  const address = webAddress(text);
  const localLink = /^#(?:chat|work\/(?:task|dialogue)|work-items\/conversation)\/[a-z\d_%-]+$/i.test(text);
  const matches = text && !address && !localLink ? records.filter(record =>
    !record.archived && record.id !== currentChat && record.title?.toLocaleLowerCase().includes(text.toLocaleLowerCase())
  ).slice(0, 8) : [];

  function open(tool: Exclude<Tool, 'start'>) {
    interacted.current = true;
    setTabs(previous => previous.includes(tool) ? previous : [...previous, tool]);
    setCurrent(tool);
  }

  useLayoutEffect(() => {
    const host = sideChat.current;
    const reveal = () => open('chat');
    const hide = () => {
      setTabs(previous => previous.filter(tab => tab !== 'chat'));
      setCurrent(previous => previous === 'chat' ? 'start' : previous);
    };
    host?.addEventListener('workdesk:sidechat', reveal);
    host?.addEventListener('workdesk:sidechat-close', hide);
    return () => {
      host?.removeEventListener('workdesk:sidechat', reveal);
      host?.removeEventListener('workdesk:sidechat-close', hide);
    };
  }, []);
  useLayoutEffect(() => {
    if (!requestedTool) return;
    if (requestedTool.tool === 'files') setFileQuery(requestedTool.file || '');
    open(requestedTool.tool);
    if (requestedTool.tool === 'chat') void window.WorkdeskWorkbench?.perform('sidechat');
  }, [requestedTool]);

  function close(tool: Exclude<Tool, 'start'>) {
    setTabs(previous => previous.filter(tab => tab !== tool));
    if (tool === current) setCurrent('start');
  }

  function browseFiles(value: string) {
    setFileQuery(value);
    open('files');
  }

  function search(event: FormEvent) {
    event.preventDefault();
    if (address) {
      window.open(address, '_blank', 'noopener,noreferrer');
      return;
    }
    if (localLink) {
      location.hash = text;
      return;
    }
    browseFiles(text);
  }

  return <div ref={workspaceRef} className="wd-quick-workspace" data-expanded={expanded}
    onPointerDownCapture={() => { interacted.current = true; }} onKeyDownCapture={() => { interacted.current = true; }} onKeyDown={event => {
    if (event.key === 'Escape' && !event.defaultPrevented) {
      event.stopPropagation();
      if (expanded && onToggleExpanded) onToggleExpanded(); else onClose();
    }
  }}>
    <header className="wd-quick-header">
      <nav className="wd-quick-tabs" aria-label="右侧工具页签">
        <button type="button" className="wd-quick-tab wd-quick-home-tab" aria-current={current === 'start' ? 'page' : undefined}
          aria-label="快捷工作区" title="快捷工作区" onClick={() => { setCurrent('start'); requestAnimationFrame(() => queryInput.current?.focus()); }}><Plus aria-hidden="true"/><span>快捷工作区</span></button>
        {tabs.map(tab => { const Icon = tools[tab].Icon; return <span key={tab} className="wd-quick-tab-group" data-selected={current === tab}>
          <button type="button" className="wd-quick-tab" aria-current={current === tab ? 'page' : undefined}
            aria-label={tools[tab].tab} title={tools[tab].tab} onClick={() => setCurrent(tab)}><Icon aria-hidden="true"/><span>{tools[tab].tab}</span></button>
          <button type="button" className="wd-quick-icon wd-quick-tab-close" title={'关闭'+tools[tab].tab+'页签'} aria-label={'关闭'+tools[tab].tab+'页签'} onClick={() => close(tab)}><X/></button>
        </span>; })}
      </nav>
      <div className="wd-quick-panel-actions">
        {onToggleExpanded && <WorkspaceExpandButton expanded={expanded} onToggle={onToggleExpanded}/>}
        <button type="button" className="wd-quick-icon wd-quick-close" onClick={onClose} title="收起侧栏，保留工具页签" aria-label="收起快捷工作区"><PanelRightClose/></button>
      </div>
    </header>
    <div className="wd-quick-scroll">
      {current === 'start' && <div className="wd-quick-launcher">
        <form className="wd-quick-search" onSubmit={search}>
          <Search aria-hidden="true"/>
          <input ref={queryInput} type="search" aria-label="搜索当前目录文件、聊天或输入网址" placeholder="搜索当前目录文件、聊天或输入网址"
            value={query} onChange={event => setQuery(event.currentTarget.value)}/>
          <button type="submit" disabled={!text}>{address ? '打开网址' : localLink ? '打开对话' : '搜索'}</button>
        </form>
        {text && <ul className="wd-quick-results">
          {address ? <li><a href={address} target="_blank" rel="noopener noreferrer">{address}<ArrowUpRight aria-hidden="true"/></a></li>
            : localLink ? <li><a href={text}>打开 Workdesk 对话<ArrowUpRight aria-hidden="true"/></a></li> : <>
            <li><button type="button" onClick={() => browseFiles(text)}>在当前项目目录搜索「{text}」</button></li>
            {matches.map(record => <li key={record.id}><a href={'#chat/'+encodeURIComponent(record.id)}>{record.title}</a></li>)}
          </>}
        </ul>}
        <div className="wd-quick-grid">
          {Object.entries(tools).map(([key, { label, Icon }]) => <button key={key} type="button"
            data-wb={key === 'chat' ? 'sidechat' : undefined}
            onClick={() => key === 'files' ? browseFiles('') : open(key as Exclude<Tool, 'start'>)}>
            <Icon aria-hidden="true"/><span>{label}</span>
          </button>)}
        </div>
        {children && <div className="wd-quick-context">{children}</div>}
      </div>}
      <div hidden={current !== 'chat'} data-wb-side-host="" ref={sideChat}/>
      {tabs.filter(tab => tab !== 'chat').map(tab => <div key={tab} hidden={current !== tab} className={tab === 'files' ? 'wd-quick-files-tab' : undefined}>
        <WorkspaceTool tool={tab} projectId={projectId} initialQuery={fileQuery} onBack={() => setCurrent('start')}/>
      </div>)}
    </div>
  </div>;
}
