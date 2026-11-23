import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowRight, Check, KeyRound, MessageSquare, ShieldCheck, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import './connection-setup.css';

type Provider = 'codex' | 'deepseek' | 'qwen';
type ApiProvider = Exclude<Provider, 'codex'>;
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
type LoginResult = { state: 'PENDING' | 'CONNECTED' | 'FAILED' | 'CANCELLED'; message?: string };
type WorkStart = { id?: string; state?: 'OPENED'; message?: string };
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

const names: Record<Mode, Record<Provider, string>> = {
  work: { codex: 'Codex', deepseek: 'DeepSeek Harness', qwen: 'Qwen Code' },
  chat: { codex: 'ChatGPT', deepseek: 'DeepSeek', qwen: 'Qwen' },
  api: { codex: 'OpenAI', deepseek: 'DeepSeek', qwen: 'Qwen' },
};
const descriptions: Record<Provider, string> = {
  codex: '用你的 ChatGPT 账号完成 Codex 官方授权。',
  deepseek: '使用 DeepSeek 账号在官方 Harness 中完成授权。',
  qwen: '使用已开通 Plus 或 Pro 的千问账号在 Qwen Code 中授权。',
};
const apiHelp: Record<ApiProvider, string> = {
  deepseek: '使用 DeepSeek 开放平台的 API Key。',
  qwen: '使用千问开放平台的通用按量 API Key。',
};
const reservedProviders = ['Gemini', 'Kimi', 'GLM', 'Claude', 'Grok'];
const accountProviders: Provider[] = ['codex', 'deepseek', 'qwen'];
const apiProviders: ApiProvider[] = ['deepseek', 'qwen'];
const modes = [
  { id: 'work', label: 'Work 账号', Icon: ShieldCheck },
  { id: 'chat', label: 'Chat 账号', Icon: MessageSquare },
  { id: 'api', label: 'API Key', Icon: KeyRound },
] as const;
const durations = [[5, '5 秒'], [10, '10 秒'], [15, '15 秒'], [30, '30 秒'], [0, '永久显示']] as const;
const chatChannel = 'workdesk.chat.account';

function providerOptions(mode: Mode) {
  const enabled = mode === 'api' ? apiProviders : accountProviders;
  return <>
    <optgroup label="可连接">
      {enabled.map(id => <option key={id} value={id}>{names[mode][id]}</option>)}
    </optgroup>
    <optgroup label="待接入" disabled>
      {mode === 'api' && <option value="codex">{names.api.codex}</option>}
      {reservedProviders.map(name => <option key={name} value={name.toLowerCase()}>{name}</option>)}
    </optgroup>
  </>;
}

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

function ConnectionNotices({ notices, dismiss }: { notices: Notice[]; dismiss: (id: number) => void }) {
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
      const label = names[mode][provider] + ' 的 ' + (mode === 'api' ? 'API' : mode === 'chat' ? 'Chat 账号' : 'Work 账号');
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
  const [workProvider, setWorkProvider] = useState<Provider>('codex');
  const [chatProvider, setChatProvider] = useState<Provider>('codex');
  const [apiProvider, setApiProvider] = useState<ApiProvider>('deepseek');
  const [busy, setBusy] = useState(false);
  const [login, setLogin] = useState<{ id: string; provider: Provider }>();
  const [chatPending, setChatPending] = useState<Provider>();
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const preference = bridges().WorkdeskSettingsPreferences;
  const [seconds, setSeconds] = useState(() => preference?.get().connectionNoticeSeconds ?? 10);
  const [savingSeconds, setSavingSeconds] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);
  const serial = useRef(0);
  const keyInput = useRef<HTMLInputElement>(null);
  const pending = useRef<typeof login>(undefined);
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
    const saved = modes.flatMap(({ id }) => (Object.keys(names[id]) as Provider[])
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
      if (pending.current) void request('account-cancel', pending.current).catch(() => {});
    };
  }, []);

  useEffect(() => {
    if (!login) return;
    let closed = false;
    let timer = 0;
    async function poll(active: NonNullable<typeof login>) {
      try {
        const result = await request<LoginResult>('account-poll', active);
        if (closed || pending.current?.id !== active.id) return;
        if (result.state === 'PENDING') {
          setProgress(result.message || '请在浏览器中完成登录…');
          timer = window.setTimeout(() => void poll(active), 1400);
          return;
        }
        pending.current = undefined;
        if (result.state === 'CONNECTED') {
          const updated = await request<ConnectionStatus>();
          if (closed) return;
          setStatus(updated);
          announce(active.provider, 'work');
        } else {
          setError(result.message || '登录未完成，可以重新连接。');
        }
        setProgress('');
        setLogin(undefined);
      } catch {
        if (closed) return;
        if (pending.current?.id === active.id) void request('account-cancel', active).catch(() => {});
        pending.current = undefined;
        setProgress('');
        setLogin(undefined);
        setError('暂时无法核对登录状态，请刷新连接状态。');
      }
    }
    timer = window.setTimeout(() => void poll(login), 600);
    return () => {
      closed = true;
      window.clearTimeout(timer);
    };
  }, [login]);

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

  async function startWorkLogin() {
    setBusy(true);
    setError('');
    setProgress('');
    try {
      const result = await request<WorkStart>('account-start', { provider: workProvider });
      if (result.id) {
        const active = { id: result.id, provider: workProvider };
        if (!alive.current) {
          await request('account-cancel', active);
          return;
        }
        pending.current = active;
        setLogin(active);
        setProgress('请在浏览器中完成登录…');
      } else {
        setProgress(result.message || '请在官方工作端完成登录。');
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '无法打开官方登录，请重试。');
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  function startChatLogin() {
    setError('');
    try { chatBridge?.postMessage({ channel: chatChannel, action: 'open', provider: chatProvider }); }
    catch { setError('暂时无法打开官网 Chat，请重试。'); }
  }

  async function cancelLogin() {
    if (!login) return;
    setBusy(true);
    setError('');
    try {
      await request('account-cancel', login);
      pending.current = undefined;
      setLogin(undefined);
      setProgress('');
      setStatus(await request<ConnectionStatus>());
    } catch { setError('暂时无法取消本次登录，请稍后重试。'); }
    finally { setBusy(false); }
  }

  async function saveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = keyInput.current;
    if (!input || busy || !input.value.trim()) return;
    const key = input.value.trim();
    input.value = '';
    setBusy(true);
    setError('');
    try {
      setStatus(await request<ConnectionStatus>('key', { provider: apiProvider, key }));
      announce(apiProvider, 'api');
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法保存 API Key，请重试。');
      input.focus();
    } finally { setBusy(false); }
  }

  const locked = busy || !!login || !!chatPending;
  const workMethod = status?.work_login?.[workProvider] || (workProvider === 'codex' && status?.account_available ? 'connect' : 'unavailable');
  const dismiss = (id: number) => setNotices(previous => previous.filter(item => item.id !== id));
  return <div className="wd-connect">
    <aside className="wd-connect-intro">
      <div className="wd-connect-brand"><span>W</span> Workdesk</div>
      <div>
        <h1>{status?'连接你的账号':'本地服务暂时不可用'}</h1>
        <p>{status?'使用你已有的账号或 API Key。连接一项服务后，即可进入工作区。':'未能读取已有连接。重试成功后将返回原页面。'}</p>
      </div>
      <ConnectionNotices notices={notices} dismiss={dismiss}/>
    </aside>
    <section className="wd-connect-sheet" aria-label="连接方式">
      <div>
        <h2>{status?'连接方式':'连接状态'}</h2>
        {status && <div className="wd-connect-switch" role="group" aria-label="选择连接方式">
          {modes.map(({ id, label, Icon }) => <Button key={id} type="button" variant="ghost"
            aria-pressed={mode === id} disabled={locked} onClick={() => { setMode(id); setError(''); setProgress(''); }}>
            <Icon aria-hidden="true"/>{label}
          </Button>)}
        </div>}
      </div>
      {!status ? <div className="wd-connect-form">
        <p>请重新读取工作台的本地连接状态。</p>
        <Button type="button" variant="outline" onClick={refresh} disabled={busy}>重试</Button>
      </div> : mode === 'work' ? <div className="wd-connect-form">
        <label className="wd-connect-field">
          <span>工作端</span>
          <NativeSelect aria-label="工作端" value={workProvider} disabled={locked} onChange={event => {
            setWorkProvider(event.currentTarget.value as Provider); setError(''); setProgress('');
          }}>
            {providerOptions('work')}
          </NativeSelect>
        </label>
        <p>{descriptions[workProvider]}</p>
        {!login && <Button type="button" variant="outline" onClick={startWorkLogin} disabled={busy || workMethod === 'unavailable'}>
          {workMethod === 'guide' ? '查看官方登录方式' : workMethod === 'launch' ? '打开官方工作端' :
            status.work_accounts?.[workProvider] ? '重新登录 Work 账号' : '前往官方登录'}<ArrowRight/>
        </Button>}
        {workMethod === 'guide' && !progress && <p>本机 Qwen Code 版本暂不支持订阅登录。Workdesk 的 Qwen Work 可用 API Key。</p>}
        {workMethod === 'unavailable' && <p>本机暂未找到可用于登录的官方工作端。</p>}
        {(progress || login) && <div className="wd-connect-actions">
          {progress && <span role="status">{progress}</span>}
          {login && <Button type="button" variant="outline" onClick={cancelLogin} disabled={busy}>取消登录</Button>}
        </div>}
      </div> : mode === 'chat' ? <div className="wd-connect-form">
        <label className="wd-connect-field">
          <span>官网聊天</span>
          <NativeSelect aria-label="官网聊天" value={chatProvider} disabled={locked} onChange={event => {
            setChatProvider(event.currentTarget.value as Provider); setError('');
          }}>
            {providerOptions('chat')}
          </NativeSelect>
        </label>
        <p>在 Workdesk 内打开 {names.chat[chatProvider]} 官网。已登录的浏览器会话会在下次启动时复用。</p>
        {chatBridge ? <Button type="button" variant="outline" disabled={!!chatPending} onClick={startChatLogin}>
          {chatPending ? '等待完成官网登录…' : '打开官网 Chat'}<ArrowRight/>
        </Button> : <p>请在 Workdesk 桌面应用中打开官网 Chat。</p>}
      </div> : <form className="wd-connect-form" onSubmit={saveKey} data-api-key-form>
        <label className="wd-connect-field">
          <span>API 服务商</span>
          <NativeSelect aria-label="API 服务商" value={apiProvider} disabled={busy} onChange={event => {
            setApiProvider(event.currentTarget.value as ApiProvider); setError('');
          }}>
            {providerOptions('api')}
          </NativeSelect>
        </label>
        <label className="wd-connect-field">
          <span>{names.api[apiProvider]} API Key</span>
          <input key={apiProvider} ref={keyInput} type="password" autoComplete="off" spellCheck={false}
            aria-describedby="wd-connect-api-help" required maxLength={512} disabled={busy} placeholder="粘贴 API Key"/>
        </label>
        <p id="wd-connect-api-help">{apiHelp[apiProvider]}</p>
        <Button type="submit" variant="outline" disabled={busy}>{busy ? '正在核对…' : '验证并保存'}</Button>
      </form>}
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
          {status && <Button type="button" variant="ghost" onClick={refresh} disabled={locked}>刷新连接状态</Button>}
          <Button type="button" className="wd-connect-enter" disabled={!status?.ready || locked}
            onClick={() => { if (status?.ready) onEnter(status); }}>进入工作区<ArrowRight/></Button>
        </div>
      </footer>
    </section>
  </div>;
}
