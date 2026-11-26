import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowRight, Check, KeyRound, MessageSquare, ShieldCheck, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import { SettingsPartners } from './partners';
import './connection-setup.css';

type Provider = string;
type Mode = 'work' | 'chat' | 'api';
type Accounts = Partial<Record<Provider, boolean>>;
export type ConnectionStatus = {
  ready: boolean;
  account_available: boolean;
  connections: Record<Provider, boolean>;
  work_accounts?: Accounts;
  chat_accounts?: Accounts;
  api_connections?: Accounts;
  work_login?: Record<Provider, 'connect' | 'launch' | 'guide' | 'unavailable'>;
};
type Props = { initialStatus: ConnectionStatus | null; onEnter: (status: ConnectionStatus) => void };
type Notice = { id: number; provider: Provider; mode: Mode; created: number };
type Preferences = {
  get(): { connectionNoticeSeconds?: number };
  save(key: 'connectionNoticeSeconds', value: number): Promise<{ connectionNoticeSeconds: number }>;
};
type ChatAccountMessage = {
  channel?: string;
  provider?: Provider;
  state?: 'OPENED' | 'CONFIRMED' | 'CLOSED' | 'FAILED';
  connections?: Accounts;
};
type DesktopBridge = {
  postMessage(value: object): void;
  addEventListener(event: 'message', listener: (event: MessageEvent<ChatAccountMessage>) => void): void;
  removeEventListener(event: 'message', listener: (event: MessageEvent<ChatAccountMessage>) => void): void;
};

const modes = [
  { id: 'work', label: 'Work 账号', Icon: ShieldCheck },
  { id: 'chat', label: 'Chat 账号', Icon: MessageSquare },
  { id: 'api', label: 'API Key', Icon: KeyRound },
] as const;
const durations = [[5, '5 秒'], [10, '10 秒'], [15, '15 秒'], [30, '30 秒'], [0, '永久显示']] as const;
const chatChannel = 'workdesk.chat.account';

function bridges() {
  return window as Window & {
    WorkdeskSettingsPreferences?: Preferences;
    WorkdeskDesktop?: { chatAccounts?: boolean };
    chrome?: { webview?: DesktopBridge };
  };
}

async function request<T>(action = '', payload?: object): Promise<T> {
  const response = await fetch('api/v1/connection-setup' + (action ? '/' + action : ''), {
    cache: 'no-store',
    ...(payload ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '暂时无法连接，请重试。');
  return result;
}

function ConnectionNotices({ notices, dismiss, partners,apiProviders }: { notices: Notice[]; dismiss: (id: number) => void;partners:{id:string;name:string}[];apiProviders:{id:string;name:string}[] }) {
  const list = useRef<HTMLUListElement>(null);
  const positions = useRef(new Map<string, number>());
  useLayoutEffect(() => {
    if (!list.current) return;
    const stack = list.current;
    const next = new Map<string, number>();
    const rows = Array.from(stack.children) as HTMLElement[];
    rows.forEach(node => node.getAnimations().forEach(animation => animation.cancel()));
    stack.scrollTop = stack.scrollHeight;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (const node of rows) {
      const id = node.dataset.noticeId!;
      const bottom = stack.getBoundingClientRect().bottom - node.getBoundingClientRect().bottom;
      const previous = positions.current.get(id);
      next.set(id, bottom);
      if (!reducedMotion && previous !== bottom) {
        node.animate([
          { transform: 'translateY(' + (previous === undefined ? 10 : bottom - previous) + 'px)', opacity: previous === undefined ? 0 : 1 },
          { transform: 'translateY(0)', opacity: 1 },
        ], { duration: 180, easing: 'ease-out' });
      }
    }
    positions.current = next;
  }, [notices]);

  return <ul ref={list} className="wd-connect-notices" aria-label="连接提示" aria-live="polite" aria-relevant="additions">
    {notices.map(({ id, provider, mode }) => {
      const label = (mode==='chat'&&provider==='codex'?'ChatGPT':(mode==='api'?apiProviders:partners).find(item=>item.id===provider)?.name||provider) + ' 的 ' + (mode === 'api' ? 'API' : mode === 'chat' ? 'Chat 账号' : 'Work 账号');
      return <li key={id} data-notice-id={id}>
        <span>{label}</span>
        <span className="wd-connect-success"><Check aria-hidden="true"/>已连接</span>
        <Button type="button" size="icon" variant="ghost" aria-label={'关闭 ' + label + ' 提示'} onClick={() => dismiss(id)}><X/></Button>
      </li>;
    })}
  </ul>;
}

export function ConnectionSetup({ initialStatus, onEnter }: Props) {
  const [status, setStatus] = useState(initialStatus);
  const [mode, setMode] = useState<Mode>('work');
  const [chatProvider, setChatProvider] = useState<Provider>('codex');
  const [partners,setPartners]=useState<{id:string;name:string}[]>([]);
  const [apiProviders,setApiProviders]=useState<{id:string;name:string}[]>([]);
  useEffect(()=>{const controller=new AbortController();fetch('api/v2/partners',{signal:controller.signal,cache:'no-store'}).then(response=>{if(!response.ok)throw Error();return response.json();}).then(value=>{if(!controller.signal.aborted){setPartners(value.partners||[]);setApiProviders(value.api_providers||[]);}}).catch(()=>{if(!controller.signal.aborted)setError('暂时无法读取官网列表。');});return()=>controller.abort();},[]);
  const [busy, setBusy] = useState(false);
  const [chatPending, setChatPending] = useState<Provider>();
  const [error, setError] = useState('');
  const preference = bridges().WorkdeskSettingsPreferences;
  const [seconds, setSeconds] = useState(() => preference?.get().connectionNoticeSeconds ?? 10);
  const [savingSeconds, setSavingSeconds] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [partnerReady,setPartnerReady]=useState(false),[partnerRefresh,setPartnerRefresh]=useState(0);
  const serial = useRef(0);
  const alive = useRef(true);
  const desktop = bridges();
  const chatBridge = desktop.WorkdeskDesktop?.chatAccounts ? desktop.chrome?.webview : undefined;

  function announce(provider: Provider, kind: Mode) {
    const notice = { id: ++serial.current, provider, mode: kind, created: Date.now() };
    setNotices(previous => [...previous.filter(item => item.provider !== provider || item.mode !== kind), notice]);
  }

  useEffect(() => {
    if (seconds !== 0 || !status) return;
    const groups = { work: status.work_accounts, chat: status.chat_accounts, api: status.api_connections };
    const saved = modes.flatMap(({ id }) => Object.keys(groups[id]||{})
      .filter(provider => groups[id]?.[provider]).map(provider => ({ provider, mode: id })));
    setNotices(previous => {
      const additions = saved.filter(item => !previous.some(entry => entry.provider === item.provider && entry.mode === item.mode));
      if (!additions.length) return previous;
      return [...additions.map(({ provider, mode }) => ({ id: ++serial.current, provider, mode, created: Date.now() })), ...previous];
    });
  }, [seconds, status]);

  useEffect(() => {
    if (!seconds || !notices.length) return;
    const age = seconds * 1000;
    const expires = Math.min(...notices.map(item => item.created + age));
    const timer = window.setTimeout(() => {
      setNotices(previous => previous.filter(item => item.created + age > Date.now()));
    }, Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [seconds, notices]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!chatBridge) return;
    const receive = (event: MessageEvent<ChatAccountMessage>) => {
      const message = event.data;
      if (message?.channel !== chatChannel) return;
      if (message.state === 'OPENED') setChatPending(message.provider);
      if (message.state === 'CLOSED' || message.state === 'FAILED' || message.state === 'CONFIRMED')
        setChatPending(undefined);
      if (message.state === 'FAILED') setError('无法打开官网 Chat，请重试。');
      if (message.state !== 'CONFIRMED' || !message.provider) return;
      announce(message.provider, 'chat');
      void request<ConnectionStatus>().then(updated => {
        if (alive.current) setStatus(updated);
      }).catch(() => {
        if (alive.current) setError('暂时无法读取连接状态，请刷新。');
      });
    };
    chatBridge.addEventListener('message', receive);
    return () => chatBridge.removeEventListener('message', receive);
  }, [chatBridge]);

  async function refresh() {
    setBusy(true);
    setError('');
    try {
      const updated=await request<ConnectionStatus>();
      if(!alive.current)return;
      setStatus(updated);
      if(!status && updated.ready)onEnter(updated);
    }
    catch { setError('暂时无法读取连接状态，请重试。'); }
    finally { setBusy(false); }
  }

  async function changeSeconds(value: number) {
    setSavingSeconds(true);
    setError('');
    try {
      const saved = await preference?.save('connectionNoticeSeconds', value);
      setSeconds(saved?.connectionNoticeSeconds ?? value);
    } catch { setError('暂时无法保存提示时长，请重试。'); }
    finally { setSavingSeconds(false); }
  }

  function startChatLogin() {
    setError('');
    try { chatBridge?.postMessage({ channel: chatChannel, action: 'open', provider: chatProvider }); }
    catch { setError('暂时无法打开官网 Chat，请重试。'); }
  }

  const locked = busy || !!chatPending;
  const dismiss = (id: number) => setNotices(previous => previous.filter(item => item.id !== id));
  return <div className="wd-connect">
    <aside className="wd-connect-intro">
      <div className="wd-connect-brand"><span>W</span> Workdesk</div>
      <div>
        <h1>{status?'连接你的账号':'本地服务暂时不可用'}</h1>
        <p>{status?'使用你已有的账号或 API Key。连接一项服务后，即可进入工作区。':'未能读取已有连接。重试成功后将返回原页面。'}</p>
      </div>
        <ConnectionNotices notices={notices} dismiss={dismiss} partners={partners} apiProviders={apiProviders}/>
    </aside>
    <section className="wd-connect-sheet" aria-label="连接方式">
      <div>
        <h2>{status?'连接方式':'连接状态'}</h2>
        {status && <div className="wd-connect-switch" role="group" aria-label="选择连接方式">
          {modes.map(({ id, label, Icon }) => <Button key={id} type="button" variant="ghost"
            aria-pressed={mode === id} disabled={locked} onClick={() => { setMode(id); setError(''); }}>
            <Icon aria-hidden="true"/>{label}
          </Button>)}
        </div>}
      </div>
      {!status ? <div className="wd-connect-form"><p>请重新读取工作台的本地连接状态。</p><Button type="button" variant="outline" onClick={refresh} disabled={busy}>重试</Button></div> : mode === 'chat' ? <div className="wd-connect-form">
        <label className="wd-connect-field">
          <span>官网聊天</span>
          <NativeSelect aria-label="官网聊天" value={chatProvider} disabled={locked} onChange={event => {
            setChatProvider(event.currentTarget.value as Provider); setError('');
          }}>
            {!partners.length&&<option value="">正在读取官网列表</option>}{partners.map(partner=><option value={partner.id} key={partner.id}>{partner.id==='codex'?'ChatGPT':partner.name}</option>)}
          </NativeSelect>
        </label>
        <p>在 Workdesk 内打开 {chatProvider==='codex'?'ChatGPT':partners.find(item=>item.id===chatProvider)?.name||chatProvider} 官网。已登录的浏览器会话会在下次启动时复用。</p>
        <p role="status">{status.chat_accounts?.[chatProvider]?'Chat 账号已连接':'Chat 账号尚未确认登录'}</p>
        {chatBridge ? <Button type="button" variant="outline" disabled={!!chatPending} onClick={startChatLogin}>
          {chatPending ? '等待完成官网登录…' : '打开官网 Chat'}<ArrowRight/>
        </Button> : <p>请在 Workdesk 桌面应用中打开官网 Chat。</p>}
      </div> : <SettingsPartners mode={mode} refreshRevision={partnerRefresh} onReady={setPartnerReady}/>}
      {error && <p className="wd-connect-error" role="alert">{error}</p>}
      <footer className="wd-connect-footer">
        <label className="wd-connect-duration">
          <span>连接提示时长</span>
          <NativeSelect aria-label="连接提示时长" size="sm" value={seconds} disabled={savingSeconds} onChange={event => void changeSeconds(Number(event.currentTarget.value))}>
            {durations.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </label>
        <div className="wd-connect-footer-actions">
          {!locked && <Button asChild variant="ghost"><a href="#resources">打开资料空间</a></Button>}
          {status && <Button type="button" variant="ghost" onClick={()=>{void refresh();setPartnerRefresh(value=>value+1);}} disabled={locked}>刷新连接状态</Button>}
          <Button type="button" className="wd-connect-enter" disabled={(!status?.ready&&!partnerReady) || locked}
            onClick={() => { if (status&&(status.ready||partnerReady)) onEnter({...status,ready:true}); }}>进入工作区<ArrowRight/></Button>
        </div>
      </footer>
    </section>
  </div>;
}
