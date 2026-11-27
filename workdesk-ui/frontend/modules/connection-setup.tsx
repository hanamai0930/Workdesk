import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowRight, Check, KeyRound, MessageSquare, ShieldCheck, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import { coreClient, CoreRequestError } from '../core/client';
import { SettingsPartners, type PartnerAccount } from './partners';
import './connection-setup.css';

type Provider = string;
type Mode = 'work' | 'chat' | 'chat-api' | 'api';
type ChatEnter = {route:'api'|'web';provider:string};
const chatApiNames={deepseek:'DeepSeek',qwen:'Qwen'};
type ChatApiProvider=keyof typeof chatApiNames;
const isChatApiProvider=(name?:string):name is ChatApiProvider=>!!name&&Object.hasOwn(chatApiNames,name);
type KeyReceipt={provider?:string;ready?:boolean;accounts_updated?:number};
type Accounts = Partial<Record<Provider, boolean>>;
export type ConnectionStatus = {
  ready: boolean;
  account_available: boolean;
  connections: Record<Provider, boolean>;
  work_accounts?: Accounts;
  chat_accounts?: Accounts;
  api_connections?: Accounts;
  chat_api_connections?: Accounts;
  work_login?: Record<Provider, 'connect' | 'launch' | 'guide' | 'unavailable'>;
};
type Props = { initialStatus: ConnectionStatus | null; initialMode?:Mode; initialChatApi?:string; onEnter: (status: ConnectionStatus,target?:ChatEnter) => void };
type Notice = { id: number; provider: Provider; mode: Mode; created: number };
type Preferences = {
  get(): { connectionNoticeSeconds?: number };
  save(key: 'connectionNoticeSeconds', value: number): Promise<{ connectionNoticeSeconds: number }>;
};
type ChatAccountMessage = {
  channel?: string;
  provider?: Provider;
  state?: 'OPENING' | 'OPENED' | 'LOAD_FAILED' | 'CONFIRMED' | 'CLOSED' | 'FAILED';
  message?: string;
  connections?: Accounts;
};
type ChatWindowState = {state:NonNullable<ChatAccountMessage['state']>;message?:string};
const safeMessage=(value?:string)=>typeof value==='string'?value.replace(/https?:\/\/\S+/g,'官方页面').replace(/(?:token|code|state|key|secret|credential)\s*[:=]\s*[^\s,;]+/gi,'授权信息已隐藏').slice(0,180):undefined;
type DesktopBridge = {
  postMessage(value: object): void;
  addEventListener(event: 'message', listener: (event: MessageEvent<ChatAccountMessage>) => void): void;
  removeEventListener(event: 'message', listener: (event: MessageEvent<ChatAccountMessage>) => void): void;
};

const modes = [
  { id: 'work', label: 'Work 账号', Icon: ShieldCheck },
  { id: 'chat', label: '官网 Chat', Icon: MessageSquare },
  { id: 'chat-api', label: 'API Chat', Icon: KeyRound },
  { id: 'api', label: 'Work API', Icon: KeyRound },
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

function request<T>() { return coreClient.request<T>('v1/connection-setup'); }

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
      const name=mode==='chat'&&provider==='codex'?'ChatGPT':(mode==='api'||mode==='chat-api'?apiProviders:partners).find(item=>item.id===provider)?.name||provider;
      const label = name + ' 的 ' + (mode === 'api' ? 'Work API' : mode==='chat-api'?'API Chat':mode === 'chat' ? '官网 Chat' : 'Work 账号');
      return <li key={id} data-notice-id={id}>
        <span>{label}</span>
        <span className="wd-connect-success"><Check aria-hidden="true"/>{mode==='chat-api'||mode==='api'?'已配置':'已连接'}</span>
        <Button type="button" size="icon" variant="ghost" aria-label={'关闭 ' + label + ' 提示'} onClick={() => dismiss(id)}><X/></Button>
      </li>;
    })}
  </ul>;
}

export function ConnectionSetup({ initialStatus, initialMode='work', initialChatApi, onEnter }: Props) {
  const [status, setStatus] = useState(initialStatus);
  const [mode, setMode] = useState<Mode>(initialMode);
  const [chatApiProvider,setChatApiProvider]=useState<ChatApiProvider>(isChatApiProvider(initialChatApi)?initialChatApi:'deepseek');
  const [chatKey,setChatKey]=useState(''),[sourceAccount,setSourceAccount]=useState('');
  const [chatKeyFeedback,setChatKeyFeedback]=useState(''),[savingChatKey,setSavingChatKey]=useState(false);
  const keyLock=useRef(false);
  const readPending=()=>coreClient.uncertainCommands().filter(item=>item.kind.startsWith('chat.api-key.'));
  const [pendingKeys,setPendingKeys]=useState(readPending);
  const [keyServiceKnown,setKeyServiceKnown]=useState(false);
  useEffect(()=>{let active=true;void coreClient.loadIdentity().then(()=>{if(active){setKeyServiceKnown(true);setPendingKeys(readPending());}}).catch(()=>{});return()=>{active=false;};},[]);
  const [chatProvider, setChatProvider] = useState<Provider>('codex');
  const [partners,setPartners]=useState<{id:string;name:string}[]>([]);
  const [apiProviders,setApiProviders]=useState<{id:string;name:string}[]>([]);
  const [workKeyAccounts,setWorkKeyAccounts]=useState<PartnerAccount[]>([]);
  const [partnerReady,setPartnerReady]=useState(false),[partnerRefresh,setPartnerRefresh]=useState(0);
  useEffect(()=>{const controller=new AbortController();coreClient.request('v2/partners',{signal:controller.signal}).then(value=>{if(!controller.signal.aborted){setPartners(value.partners||[]);setApiProviders(value.api_providers||[]);setWorkKeyAccounts((value.accounts||[]).filter((a:PartnerAccount)=>a.key_ready===true&&a.state!=='DISCONNECTED'));}}).catch(()=>{if(!controller.signal.aborted)setError('暂时无法读取伙伴列表，请刷新。');});return()=>controller.abort();},[partnerRefresh]);
  const [busy, setBusy] = useState(false);
  const [chatWindows, setChatWindows] = useState<Record<Provider,ChatWindowState>>({});
  const chatLaunches=useRef<Record<Provider,number>>({});
  const [error, setError] = useState('');
  const preference = bridges().WorkdeskSettingsPreferences;
  const [seconds, setSeconds] = useState(() => preference?.get().connectionNoticeSeconds ?? 10);
  const [savingSeconds, setSavingSeconds] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);
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
    const groups = { work: status.work_accounts, chat: status.chat_accounts, 'chat-api':status.chat_api_connections, api: status.api_connections };
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
      if (message?.channel !== chatChannel || !message.provider || !message.state || !alive.current) return;
      const provider=message.provider,state=message.state;
      setChatWindows(previous=>({...previous,[provider]:{state,message:safeMessage(message.message)}}));
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
      if(!keyServiceKnown)void coreClient.loadIdentity().then(()=>{if(alive.current){setKeyServiceKnown(true);setPendingKeys(readPending());}}).catch(()=>{});
      if(!status && updated.ready)onEnter(updated);
    }
    catch { setError('暂时无法读取连接状态，请重试。'); }
    finally { setBusy(false); }
  }

  function acceptKey(receipt:KeyReceipt,provider:ChatApiProvider) {
    if(receipt.ready!==true||receipt.provider!==provider)return false;
    setStatus(previous=>previous&&({...previous,ready:true,chat_api_connections:{...previous.chat_api_connections,[provider]:true}}));
    announce(provider,'chat-api');
    setChatKeyFeedback(receipt.accounts_updated?'Key 已保存，已更新共用此 Key 的 Work 连接。':'API Chat 的 Key 已接好。');
    setPartnerRefresh(value=>value+1);
    return true;
  }

  async function saveChatKey(useWorkSource=false) {
    if(keyLock.current||!keyServiceKnown||pendingKeys.length)return;
    const provider=chatApiProvider,key=chatKey.trim();
    const exactAccount=workKeyAccounts.find(a=>a.id===sourceAccount&&accountApi(a)===provider);
    if(useWorkSource?!exactAccount:!key)return;
    keyLock.current=true;setSavingChatKey(true);setError('');setChatKeyFeedback('');setChatKey('');
    const id=crypto.randomUUID();
    try{
      const data=await coreClient.request<KeyReceipt>(useWorkSource?'v1/chat-api-key/select':'v1/chat-api-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(useWorkSource?{id,provider,source_account_id:exactAccount!.id}:{id,provider,key})});
      if(alive.current&&!acceptKey(data,provider))setError('尚未确认 API Chat 的 Key 已保存，请刷新核对。');
    }catch(e){if(alive.current)setError(e instanceof CoreRequestError&&e.unknown?'保存回执待核对，没有重发。':'未能保存 Key，请检查配置后重试。');}
    finally{keyLock.current=false;if(alive.current){setSavingChatKey(false);setPendingKeys(readPending());}}
  }

  async function checkKeyReceipt(id:string,kind:string){
    if(keyLock.current)return;keyLock.current=true;setSavingChatKey(true);setError('');setChatKeyFeedback('');
    try{
      const data=await coreClient.receipt(id);
      if(!alive.current)return;
      const result=data.receipt as KeyReceipt|undefined;
      if(data.state==='RECORDED'&&data.kind===kind&&isChatApiProvider(result?.provider)&&result?.ready===true){
        if(kind!=='chat.api-key.reuse')acceptKey(result,result.provider);
        else setChatKeyFeedback('已核对原 Work 共用 Key 操作。');
      }else setError('原保存仍待核对，没有重发。');
    }catch{if(alive.current)setError('暂时无法核对原保存。');}
    finally{keyLock.current=false;if(alive.current){setSavingChatKey(false);setPendingKeys(readPending());}}
  }

  function updateApiStatus(){
    void request<ConnectionStatus>().then(updated=>{if(alive.current)setStatus(updated);}).catch(()=>{if(alive.current)setError('暂时无法读取连接状态，请刷新。');});
    setPartnerRefresh(value=>value+1);
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
    if(!chatBridge||!partners.some(partner=>partner.id===chatProvider))return;
    setError('');
    const now=Date.now();if(chatWindows[chatProvider]?.state==='OPENING'&&now-(chatLaunches.current[chatProvider]||0)<400)return;
    chatLaunches.current[chatProvider]=now;
    setChatWindows(previous=>({...previous,[chatProvider]:{state:'OPENING'}}));
    try { chatBridge.postMessage({ channel: chatChannel, action: 'open', provider: chatProvider }); }
    catch { setChatWindows(previous=>({...previous,[chatProvider]:{state:'FAILED',message:'暂时无法打开官网 Chat，请重试。'}})); }
  }

  const locked=busy||savingChatKey;
  const chatApiReady=status?.chat_api_connections?.[chatApiProvider]===true;
  const accountApi=(a:PartnerAccount)=>a.kind==='deepseek_api'?(a.api_provider||'deepseek'):a.kind==='api'?a.api_provider:undefined;
  const availableSources=workKeyAccounts.filter(a=>accountApi(a)===chatApiProvider);
  const chatTarget:ChatEnter|undefined=mode==='chat-api'?{route:'api',provider:chatApiProvider}:mode==='chat'?{route:'web',provider:chatProvider}:undefined;
  const chatWindow=chatWindows[chatProvider],chatFailed=chatWindow?.state==='FAILED'||chatWindow?.state==='LOAD_FAILED';
  const chatProgress=chatWindow?.state==='OPENING'?'正在打开官网窗口…':chatWindow?.state==='OPENED'?'官网页面已打开。请在窗口中完成登录并确认。':chatFailed?(chatWindow.message||'官网页面未能载入，请重试打开。'):chatWindow?.state==='CLOSED'?'官网窗口已关闭，可再次打开。':chatWindow?.state==='CONFIRMED'?'已收到你在官网窗口中的登录确认。':'';
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
            aria-pressed={mode === id} disabled={locked} onClick={() => {
              // The native Work login bridge accepts only the exact Work setup page.
              if(id==='work'&&location.hash!=='#connect')history.replaceState(history.state,'','#connect');
              setMode(id);setChatKey('');setSourceAccount('');setChatKeyFeedback('');setError('');
            }}>
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
        {chatProgress&&<p className={chatFailed?'wd-connect-error':'wd-connect-login-progress'} role={chatFailed?'alert':'status'}>{chatProgress}</p>}
        {chatBridge ? <Button type="button" variant="outline" disabled={!partners.length} onClick={startChatLogin}>
          {chatFailed?'重试打开官网 Chat':chatWindow&&['OPENING','OPENED'].includes(chatWindow.state)?'继续打开官网 Chat':'打开官网 Chat'}<ArrowRight/>
        </Button> : <p>请在 Workdesk 桌面应用中打开官网 Chat。</p>}
      </div> : mode==='chat-api'?<div className="wd-connect-form wd-connect-api-chat">
        <form className="wd-connect-api-key" onSubmit={event=>{event.preventDefault();void saveChatKey();}}>
          <label className="wd-connect-field">API 厂商<NativeSelect aria-label="Chat API 厂商" value={chatApiProvider} disabled={locked} onChange={event=>{const value=event.currentTarget.value;if(!isChatApiProvider(value))return;setChatApiProvider(value);setChatKey('');setSourceAccount('');setChatKeyFeedback('');setError('');}}>
            {Object.entries(chatApiNames).map(([id,name])=><option value={id} key={id}>{name}</option>)}
          </NativeSelect></label>
          <label className="wd-connect-field">API Key<input type="password" autoComplete="off" spellCheck={false} aria-label={chatApiNames[chatApiProvider]+' Chat API Key'} value={chatKey} disabled={locked||!keyServiceKnown||pendingKeys.length>0} onChange={event=>setChatKey(event.currentTarget.value)}/></label>
          <div className="wd-connect-actions wd-connect-api-actions"><span role="status">{chatApiReady?'Key 已就绪':'未设置 Key'}</span><Button type="submit" variant="outline" disabled={locked||!keyServiceKnown||pendingKeys.length>0||!chatKey.trim()}>{savingChatKey?'正在核对…':'保存 Key'}</Button></div>
        </form>
        <p>普通 API 聊天可与同厂商的 Work 连接共用 Key。</p>
        {availableSources.length>0&&<details className="wd-connect-api-sources"><summary>从 Work 选择已有 Key</summary>
          <label className="wd-connect-field">Work 来源<NativeSelect aria-label="Work Key 来源" value={sourceAccount} disabled={locked||pendingKeys.length>0} onChange={event=>setSourceAccount(event.currentTarget.value)}><option value="">请选择</option>{availableSources.map(a=><option value={a.id} key={a.id}>{a.label}</option>)}</NativeSelect></label>
          <Button type="button" variant="outline" disabled={locked||!keyServiceKnown||pendingKeys.length>0||!sourceAccount} onClick={()=>void saveChatKey(true)}>使用所选 Work Key</Button>
        </details>}
        {pendingKeys.map(item=><Button key={item.id} type="button" variant="outline" disabled={locked} onClick={()=>void checkKeyReceipt(item.id,item.kind)}>核对原保存 · {item.id.slice(0,8)}</Button>)}
        {chatKeyFeedback&&<p role="status">{chatKeyFeedback}</p>}
      </div>:<SettingsPartners mode={mode} refreshRevision={partnerRefresh} onReady={setPartnerReady} chatApiConnections={status.chat_api_connections} onApiChange={updateApiStatus}/>}
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
            onClick={() => { if (status&&(status.ready||partnerReady)) onEnter({...status,ready:true},chatTarget); }}>进入工作区<ArrowRight/></Button>
        </div>
      </footer>
    </section>
  </div>;
}
