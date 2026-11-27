import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import { coreClient } from '../core/client';
import { partnerRunning, type PartnerRun } from '../core/partner-runtime';
import './partners.css';

type Support = boolean | string | {state?:string;supported?:boolean;reason?:string};
export type Partner = {id:string;name:string;account_support?:Support;api_support?:Support;sources?:string[]};
export type APIProvider = {id:string;name:string;default_model:string;models:{id:string;name:string}[];compatibility:Record<string,{state:'supported'|'isolated';reason?:string;validation?:string}>};
export type PartnerRelease = {id:string;component_id:string;partner_id?:string;version:string;path:string;state:string;managed?:boolean};
export type PartnerAccount = {id:string;partner_id:string;label:string;kind:string;state:string;revision:number;api_provider?:string;auth_state?:'AUTHENTICATED'|'UNAUTHENTICATED'|'UNKNOWN';auth_options?:Record<string,string>;key_ready?:boolean;login_run_id?:string;login_state?:string;login_release_id?:string;login_url?:string;user_code?:string;instructions?:string};
export type PartnerConnection = {id:string;partner_id:string;release_id:string;route:'account'|'api';account_id:string;api_provider?:string;driver_provider?:string;model:string;options?:Record<string,unknown>;state:string;revision:number};
type Operation = {id:string;kind:string;partner_id?:string;state:string;error?:string;created?:number;updated?:number;result?:{id?:string};progress?:{phase:string;done:number;total:number}};
type LoginRun = PartnerRun & {account_id?:string;login_url?:string;user_code?:string;instructions?:string};
export type PartnersSnapshot = {partners:Partner[];api_providers?:APIProvider[];components:PartnerRelease[];accounts:PartnerAccount[];connections:PartnerConnection[];operations:Operation[];runs:LoginRun[]};
type Receipt = {account_id?:string;connection_id?:string;run_id?:string;operation_id?:string};
const empty:PartnersSnapshot={partners:[],components:[],accounts:[],connections:[],operations:[],runs:[]};
const usable=(release:PartnerRelease)=>['AVAILABLE','READY','INSTALLED','VERIFIED'].includes(release.state);
const unknown=(state?:string)=>!!state&&(state==='UNKNOWN'||state.endsWith('_UNKNOWN'));
const terminal=(state?:string)=>!!state&&['COMPLETED','FAILED','FAILED_NO_SEND','CANCELLED','STOPPED_RUNTIME','STOPPED','SUCCEEDED','ERROR'].includes(state);
const activeOperation=(state:string)=>['QUEUED','RUNNING','PREPARING','DOWNLOADING','INSTALLING','BUILDING','VERIFYING'].includes(state);
const installing=(op:Operation)=>op.kind==='component.prepare'||op.kind==='component.update';
const installSucceeded=(op:Operation)=>installing(op)&&['COMPLETED','SUCCEEDED'].includes(op.state);
const installFailed=(op:Operation)=>installing(op)&&['FAILED','FAILED_NO_SEND','ERROR'].includes(op.state);
const progressPhases:Record<string,string>={download:'正在下载',verify_bundle:'正在校验补充包',extract:'正在解包',verify_files:'正在校验组件',prepare:'正在准备工作端',publish:'正在登记工作端'};
function choosePartnerBundle():Promise<{path:string|null}>{
 const desktop=window as Window & {WorkdeskDesktop?:{partnerBundlePicker?:boolean};chrome?:{webview?:EventTarget & {postMessage:(request:object)=>void}}};
 const bridge=desktop.WorkdeskDesktop?.partnerBundlePicker&&desktop.chrome?.webview;
 if(!bridge)return coreClient.request('pick-partner-bundle',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 return new Promise((resolve,reject)=>{
  const requestId=crypto.randomUUID(),channel='workdesk.partner.bundle';
  const cleanup=()=>{clearTimeout(timer);bridge.removeEventListener('message',receive);};
  const receive=(event:Event)=>{const result=(event as MessageEvent).data;
   if(result?.channel!==channel||result.requestId!==requestId)return;
   cleanup();
   if(result.error)reject(new Error(result.error));else resolve({path:result.path});
  };
  const timer=setTimeout(()=>{cleanup();reject(new Error('补充包选择超时，请关闭原窗口后重试。'));},300000);
  bridge.addEventListener('message',receive);
  try{bridge.postMessage({channel,requestId});}catch(error){cleanup();reject(error);}
 });
}
function operationTime(op:Operation){return op.created??op.updated??0;}
function visibleOperations(operations:Operation[]){
 const ordered=[...operations].sort((left,right)=>operationTime(right)-operationTime(left)||(right.updated??0)-(left.updated??0));
 const newestSuccess=ordered.find(installSucceeded);
 const visible=ordered.filter(op=>!installSucceeded(op)&&!(newestSuccess&&installFailed(op)&&operationTime(op)<=operationTime(newestSuccess)));
 return [...visible.filter(op=>!terminal(op.state)),...visible.filter(op=>terminal(op.state)).slice(0,3)];
}
function InstallProgress({op,name}:{op:Operation;name:string}){
 const progress=op.progress;
 const percent=progress&&progress.total>0&&Number.isFinite(progress.done)?Math.max(0,Math.min(100,Math.floor(progress.done/progress.total*100))):undefined;
 const phase=op.state==='QUEUED'?'等待开始':progressPhases[progress?.phase||'']||'正在准备工作端';
 return <div className="wd-partners-install-progress"><div><span>{name} · {phase}</span>{percent!==undefined&&<span>{percent}%</span>}</div><progress aria-label={name+' '+phase} max={100} value={percent}/></div>;
}
const unsupported=(value?:Support)=>value===false||(typeof value==='string'&&['UNSUPPORTED','OFFICIALLY_UNSUPPORTED','NOT_SUPPORTED'].includes(value))||(typeof value==='object'&&(value.supported===false||['UNSUPPORTED','OFFICIALLY_UNSUPPORTED','NOT_SUPPORTED'].includes(value.state||'')));
const labels:Record<string,string>={AVAILABLE:'已安装',READY:'已安装',INSTALLED:'已安装',VERIFIED:'已连接',AUTHENTICATED:'已登录',UNAUTHENTICATED:'未登录',UNKNOWN:'待核对',DISABLED:'已停用',REMOVED:'已卸载',QUARANTINED:'已隔离',QUEUED:'等待开始',RUNNING:'进行中',WAITING_INPUT:'等待授权',WAITING_USER:'等待授权',WAITING_LOGIN:'等待授权',RESULT_RECEIVED:'正在确认',ERROR_RECEIVED:'正在确认',STOP_RESULT_RECEIVED:'正在确认',STOP_REQUESTED:'正在取消',COMPLETED:'已完成',FAILED:'未完成',STOPPED_RUNTIME:'已取消',ORPHANED_UNKNOWN:'过程待核对',STOP_UNKNOWN:'取消待核对',SAVED_UNVERIFIED:'已保存',KEY_SAVED_UNVERIFIED:'已保存'};
const stateLabel=(state?:string)=>state?labels[state]||state:'未设置';
function link(value?:string){try{const url=new URL(value||'');return !url.username&&!url.password&&(url.protocol==='https:'||(url.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(url.hostname)))?url.href:undefined;}catch{return undefined;}}
function Panel({title,children,plain=false}:{title:string;children:ReactNode;plain?:boolean}){if(plain)return <div className="wd-partners-panel-body">{children}</div>;return <Card className="wd-settings-card wd-partners-panel gap-0 py-0 shadow-none"><div className="wd-partners-panel-heading"><h3>{title}</h3></div><div className="wd-partners-panel-body">{children}</div></Card>;}
function Status({state,text}:{state?:string;text?:string}){return <span className="wd-partners-state" data-state={state}>{text||stateLabel(state)}</span>;}

function OfficialLoginLink({provider,identity,url}:{provider:string;identity:string;url:string}){
 const [state,setState]=useState<'IDLE'|'OPENING'|'OPENED'|'FAILED'|'REQUESTED'>('IDLE');
 const pending=useRef<string|null>(null),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const desktop=(window as Window&{WorkdeskDesktop?:{workLoginLinks?:boolean};chrome?:{webview?:{postMessage(value:object):void;addEventListener(name:string,listener:(event:MessageEvent)=>void):void;removeEventListener(name:string,listener:(event:MessageEvent)=>void):void}}});
 const bridge=desktop.WorkdeskDesktop?.workLoginLinks?desktop.chrome?.webview:undefined;
 useEffect(()=>{
  pending.current=null;setState('IDLE');
  const receive=(event:MessageEvent)=>{const value=event.data;if(value?.channel!=='workdesk.work.login'||!pending.current||value.requestId!==pending.current)return;
   if(value.state!=='OPENED'&&value.state!=='FAILED')return;
   pending.current=null;if(timer.current)clearTimeout(timer.current);setState(value.state);
  };
  bridge?.addEventListener('message',receive);
  return()=>{pending.current=null;if(timer.current)clearTimeout(timer.current);bridge?.removeEventListener('message',receive);};
 },[identity,url,bridge]);
 const feedback=state==='OPENING'?'正在请求打开浏览器…':state==='OPENED'?'已交给浏览器打开。请在浏览器中完成授权，再核对状态。':state==='FAILED'?'未能确认浏览器已打开，请重试。':state==='REQUESTED'?'已请求打开浏览器，请确认官方登录页面是否出现。':'';
 return <div className="wd-partners-login-open"><a className="wd-partners-login-link" href={url} target="_blank" rel="noopener noreferrer"
  data-work-login-provider={provider} data-work-login-managed={bridge?'true':undefined} onClick={event=>{
   if(!bridge){setState('REQUESTED');return;}
   event.preventDefault();if(pending.current){setState('OPENING');return;}
   const requestId=crypto.randomUUID();pending.current=requestId;setState('OPENING');
   timer.current=setTimeout(()=>{if(pending.current===requestId){pending.current=null;setState('FAILED');}},10000);
   try{bridge.postMessage({channel:'workdesk.work.login',action:'open',requestId,provider,url});}catch{pending.current=null;if(timer.current)clearTimeout(timer.current);setState('FAILED');}
  }}>{state==='FAILED'?'重试打开官方登录页面':state==='OPENED'||state==='REQUESTED'?'再次打开官方登录页面':'打开官方登录页面'} <ExternalLink size={14}/></a>
  {feedback&&<p className={state==='FAILED'?'wd-partners-error':'wd-partners-note'} role={state==='FAILED'?'alert':'status'}>{feedback}</p>}
 </div>;
}

export function SettingsPartners({initialPartner,mode='all',onReady,refreshRevision=0,chatApiConnections,onApiChange}:{initialPartner?:string;mode?:'all'|'work'|'api';onReady?:(ready:boolean)=>void;refreshRevision?:number;chatApiConnections?:Partial<Record<string,boolean>>;onApiChange?:()=>void}){
 const [data,setData]=useState<PartnersSnapshot>(empty),[partnerId,setPartnerId]=useState(initialPartner||'codex'),[accountId,setAccountId]=useState(''),[releaseId,setReleaseId]=useState('');
 const [apiProviderId,setApiProviderId]=useState('deepseek');
 const [localChatApi,setLocalChatApi]=useState<Partial<Record<string,boolean>>>({});
 useEffect(()=>{if(mode==='work'||chatApiConnections)return;const controller=new AbortController();void coreClient.request('v1/connection-setup',{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setLocalChatApi(value.chat_api_connections||{});}).catch(()=>{});return()=>controller.abort();},[mode,chatApiConnections,refreshRevision]);
 const [newLabel,setNewLabel]=useState(''),[apiKey,setApiKey]=useState(''),[site,setSite]=useState(''),[reply,setReply]=useState<Record<string,string>>({});
 const [busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState(''),[remove,setRemove]=useState<PartnerRelease|null>(null);
 const [installRequest,setInstallRequest]=useState<string|null>(null),[pickingBundle,setPickingBundle]=useState(false);
 const pickInFlight=useRef(false);
 const [closingLogin,setClosingLogin]=useState<string|null>(null);
 const [startingLogin,setStartingLogin]=useState(false),[savingApi,setSavingApi]=useState(false);
 const loginSequence=useRef(false),apiSequence=useRef(false);
 const [uncertain,setUncertain]=useState(()=>coreClient.uncertainCommands());const mounted=useRef(false),lock=useRef(false),reads=useRef(coreClient.createReadScope());
 const refresh=useCallback(async()=>{const read=reads.current.begin();try{const value=await coreClient.request<PartnersSnapshot>('v2/partners',{signal:read.signal});if(mounted.current&&read.isCurrent()){setData(value);setLoaded(true);}}catch(e){if(mounted.current&&read.isCurrent())setError(e instanceof Error?e.message:'暂时无法读取账号状态。');}},[]);
 useEffect(()=>{let active=true;mounted.current=true;void(async()=>{try{await coreClient.loadIdentity();if(active){setUncertain(coreClient.uncertainCommands());await refresh();}}catch(e){if(active)setError(e instanceof Error?e.message:'暂时无法连接本地服务。');}})();return()=>{active=false;mounted.current=false;reads.current.cancel();};},[refresh]);
 const previousRefresh=useRef(refreshRevision);
 useEffect(()=>{if(previousRefresh.current!==refreshRevision){previousRefresh.current=refreshRevision;if(mounted.current)void refresh();}},[refreshRevision,refresh]);
 const ongoing=data.runs.some(run=>partnerRunning(run.state))||data.operations.some(op=>activeOperation(op.state));
 useEffect(()=>{
  let timer:ReturnType<typeof setTimeout>|undefined,disposed=false,polling=false;
  const schedule=()=>{if(!disposed&&ongoing&&!document.hidden)timer=setTimeout(tick,1500);};
  async function tick(){if(polling)return;polling=true;try{await refresh();}finally{polling=false;schedule();}}
  const visible=()=>{if(timer)clearTimeout(timer);if(document.hidden)reads.current.cancel();else void tick();};
  schedule();document.addEventListener('visibilitychange',visible);
  return()=>{disposed=true;if(timer)clearTimeout(timer);document.removeEventListener('visibilitychange',visible);};
 },[ongoing,refresh]);
 useEffect(()=>{if(mode!=='all')return;const focus=()=>{if(!document.hidden)void refresh();};window.addEventListener('focus',focus);return()=>window.removeEventListener('focus',focus);},[mode,refresh]);
 const partner=data.partners.find(p=>p.id===partnerId)||data.partners[0],id=partner?.id||partnerId;
 const apiProviders=data.api_providers||[],apiProvider=apiProviders.find(provider=>provider.id===apiProviderId)||apiProviders.find(provider=>provider.id==='deepseek')||apiProviders[0];
 const selectedApiProvider=apiProvider?.id||'',compatibility=apiProvider?.compatibility[id],apiSupported=compatibility?.state==='supported';
 const readyChatKey=(chatApiConnections||localChatApi)[selectedApiProvider]===true;
 const official=data.accounts.filter(a=>a.partner_id===id&&a.kind==='official_login'),api=data.accounts.find(a=>a.partner_id===id&&['api','deepseek_api'].includes(a.kind)&&(a.api_provider||(a.kind==='deepseek_api'?'deepseek':''))===selectedApiProvider);
 const account=official.find(a=>a.id===accountId)||official[0],releases=data.components.filter(r=>(r.partner_id||r.component_id)===id),ready=releases.filter(usable);
 const connections=data.connections.filter(c=>c.partner_id===id),bound=connections.filter(c=>c.route==='account'&&c.account_id===account?.id).map(c=>c.release_id);
 const componentOperations=data.operations.filter(op=>op.partner_id===id&&op.kind.startsWith('component.'));
 const removalBlocked=data.runs.some(run=>run.partner_id===id&&!terminal(run.state));
 const release=ready.find(r=>r.id===releaseId)||ready.find(r=>r.id===account?.login_release_id&&!!account?.login_state&&!terminal(account.login_state))||(new Set(bound.filter(value=>ready.some(r=>r.id===value))).size===1?ready.find(r=>bound.includes(r.id)):undefined)||(ready.length===1?ready[0]:undefined);
 const loginRun=data.runs.find(run=>run.id===account?.login_run_id)||[...data.runs].filter(run=>run.account_id===account?.id&&run.mode==='login').sort((a,b)=>(b.created||0)-(a.created||0))[0];
 const loginState=account?.login_state||loginRun?.state,active=!!loginState&&!terminal(loginState)&&!unknown(loginState),auth=account?.auth_state||(account?.state==='AUTHENTICATED'?'AUTHENTICATED':'UNKNOWN');
 const blocked=busy||pickingBundle||!loaded||uncertain.length>0,authOptions=id==='glm'?{provider:site||account?.auth_options?.provider||'bigmodel'}:id==='kimi'?{region:site||account?.auth_options?.region||'global'}:{};
 const installBlocked=blocked||componentOperations.some(op=>!terminal(op.state));
 useEffect(()=>{setAccountId('');setReleaseId('');setNewLabel('');setApiKey('');setReply({});setSite('');setRemove(null);setNotice('');setError('');},[partnerId]);
 useEffect(()=>{setSite('');setReply({});setClosingLogin(null);},[account?.id]);
 useEffect(()=>{setApiKey('');setNotice('');setError('');},[selectedApiProvider]);
 async function perform(kind:string,payload:Record<string,unknown>,message:string):Promise<Receipt|undefined>{if(lock.current||busy||!loaded||(uncertain.length&&!kind.endsWith('.reconcile')))return;lock.current=true;setBusy(true);setNotice('');setError('');try{const response=await coreClient.command<Receipt>(kind,payload);if(!mounted.current)return;setNotice(message);await refresh();return response.receipt;}catch(e){if(mounted.current)setError(e instanceof Error?e.message:'操作尚未完成，请核对状态。');}finally{lock.current=false;if(mounted.current){setBusy(false);setUncertain(coreClient.uncertainCommands());}}}
 async function receipt(id:string){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{const value=await coreClient.receipt(id);if(mounted.current){setNotice(value.state==='RECORDED'?'已找到原操作回执。':'原操作仍待核对，不会重复执行。');setUncertain(coreClient.uncertainCommands());await refresh();}}catch(e){if(mounted.current)setError(e instanceof Error?e.message:'仍无法核对。');}finally{lock.current=false;if(mounted.current)setBusy(false);}}
 async function saveConnection(accountId:string,route:'account'|'api',target:PartnerRelease){const current=connections.find(c=>c.account_id===accountId&&c.route===route);return perform('connection.save',{...(current?{id:current.id,revision:current.revision}:{}),partner_id:id,release_id:target.id,route,account_id:accountId,...(route==='api'?{api_provider:selectedApiProvider,driver_provider:selectedApiProvider}:{}),model:route==='api'?(current?.model||apiProvider?.default_model||''):(current?.model||''),options:current?.options||{}},'连接已保存。');}
 async function useRelease(target:PartnerRelease){if(removalBlocked)return;setReleaseId(target.id);if(account&&!await saveConnection(account.id,'account',target))return;if(api)await saveConnection(api.id,'api',target);}
 async function startLogin(){
  if(blocked||loginSequence.current||unsupported(partner?.account_support))return;
  if(unknown(loginState)){if(loginRun)await perform('partner.reconcile',{run_id:loginRun.id},'已核对原登录，没有重新发起。');else setNotice('原登录状态待核对，请刷新连接状态。');return;}
  if(active){setNotice('正在读取本次登录进度，没有重新发起登录。');await refresh();return;}
  if(!release)return;
  loginSequence.current=true;setStartingLogin(true);setNotice('正在准备官方登录…');
  try{let target:string|undefined=account?.id;if(!target){const value=await perform('account.create',{partner_id:id,label:`${partner?.name} 账号`,kind:'official_login',auth_options:authOptions},'账号已添加。');target=value?.account_id;if(!target)return;setAccountId(target);}const current=connections.find(c=>c.account_id===target&&c.route==='account');if(!current||current.release_id!==release.id||current.state==='DISABLED'){if(!await saveConnection(target,'account',release))return;}await perform('account.login',{account_id:target,release_id:release.id,auth_options:authOptions},'正在准备官方登录页面，请留意下方进度。');}
  finally{loginSequence.current=false;if(mounted.current)setStartingLogin(false);}
 }
 async function saveKey(reuseChat=false){
  const key=apiKey.trim();if((reuseChat?(!readyChatKey||api?.key_ready===true):!key)||blocked||apiSequence.current||!apiProvider||!apiSupported)return;
  apiSequence.current=true;setSavingApi(true);setApiKey('');
  try{
   let target:string|undefined=api?.id;
   if(!target){const value=await perform('account.create',{partner_id:id,label:`${partner?.name} · ${apiProvider.name}`,kind:'api',api_provider:selectedApiProvider},'API 已添加。');target=value?.account_id;if(!target)return;}
   if(reuseChat){
    setError('');setNotice('');
    try{
     const result=await coreClient.request<{provider:string;ready:boolean}>('v1/chat-api-key/reuse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:crypto.randomUUID(),provider:selectedApiProvider,account_id:target})});
     if(!mounted.current)return;
     if(result.provider!==selectedApiProvider||result.ready!==true){setError('未确认已共用 Key，请刷新核对。');return;}
     setNotice('已共用同厂商的 Chat API Key。');await refresh();
    }catch(e){if(mounted.current)setError(e instanceof Error?e.message:'无法共用 Key，请核对状态。');return;}
    finally{if(mounted.current)setUncertain(coreClient.uncertainCommands());}
   }else if(!await perform('account.set-key',{account_id:target,key},'API Key 已保存。'))return;
   const current=connections.find(c=>c.account_id===target&&c.route==='api');
   if(release&&(!current||current.state==='DISABLED'))await saveConnection(target,'api',release);
   if(mounted.current)onApiChange?.();
  }finally{apiSequence.current=false;if(mounted.current)setSavingApi(false);}
 }
 async function installComponent(local:boolean){
  if(installBlocked||pickInFlight.current)return;
  let bundlePath:string|undefined;
  if(local){
   pickInFlight.current=true;setPickingBundle(true);setNotice('');setError('');
   try{
    const selected=await choosePartnerBundle();
    if(!mounted.current||!selected.path)return;
    bundlePath=selected.path;
   }catch(e){if(mounted.current)setError(e instanceof Error?e.message:'未能选择伙伴补充包。');return;}
   finally{pickInFlight.current=false;if(mounted.current)setPickingBundle(false);}
  }
  const receipt=await perform('component.prepare',{partner_id:id,version:'latest',...(bundlePath?{bundle_path:bundlePath}:{})},'');
  if(mounted.current&&receipt?.operation_id)setInstallRequest(receipt.operation_id);
 }
 async function removeRelease(target:PartnerRelease){if(removalBlocked||blocked)return;for(const current of connections.filter(c=>c.release_id===target.id&&c.state!=='DISABLED'))if(!await perform('connection.disable',{connection_id:current.id},'正在停用此版本的连接。'))return;if(await perform('component.remove',{release_id:target.id,allow_restore_download:true},'已提交卸载。'))setRemove(null);}
 useEffect(()=>{if(!installRequest||busy||!loaded||uncertain.length)return;const operation=data.operations.find(op=>op.id===installRequest&&op.partner_id===id);if(!operation||!terminal(operation.state))return;setInstallRequest(null);if(installSucceeded(operation)&&operation.result?.id){const target=ready.find(r=>r.id===operation.result?.id);if(target)void useRelease(target);}},[installRequest,data,busy,loaded,uncertain.length,id]);
 const canContinueLogin=active&&loginState!=='STOP_REQUESTED';
 const loginUrl=canContinueLogin?link(account?.login_url||loginRun?.login_url):undefined,code=canContinueLogin?(account?.user_code||loginRun?.user_code):undefined,requests=canContinueLogin?loginRun?.pending_requests||[]:[];
 const loginInstructions=(account?.instructions||loginRun?.instructions)?.replace(/https?:\/\/\S+/g,'官方登录页面').slice(0,300);
 const loginPageSlow=!!loginRun?.created&&Date.now()-loginRun.created*1000>=20000;
 const loginProgress=active?(loginState==='STOP_REQUESTED'?'正在取消本次登录，等待确认。':loginUrl?'登录页面已准备，请打开继续授权。':requests.some(request=>Array.isArray(request.options)&&request.options.length>0)?'请选择下方官方登录方式。':requests.length?'等待填写下方官方授权信息。':code?'设备码已准备，请完成官方授权。':loginPageSlow?'还未收到官方登录页面。可查看进度，或取消后重试。':'正在准备官方登录页面…'):loginState==='COMPLETED'?(auth==='AUTHENTICATED'?'官方账号已认证。':'登录过程已结束，账号认证尚待核对。'):loginState?'最近登录：'+stateLabel(loginState):'';
 const apiConnection=connections.find(c=>c.route==='api'&&c.account_id===api?.id);
 const readyForWork=data.accounts.some(account=>account.auth_state==='AUTHENTICATED'||account.key_ready===true);
 useEffect(()=>{onReady?.(readyForWork);},[readyForWork,onReady]);
 const officialPanel=()=>(<Panel title="官方账号" plain={mode!=='all'}>
    <div className="wd-partners-fields"><label>账号<NativeSelect aria-label="官方账号" value={account?.id||''} disabled={blocked} onChange={event=>setAccountId(event.currentTarget.value)}>{!official.length&&<option value="">尚未添加账号</option>}{official.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</NativeSelect></label>
    {id==='glm'&&<label>站点<NativeSelect aria-label="官方账号站点" value={authOptions.provider} disabled={blocked||active} onChange={event=>setSite(event.currentTarget.value)}><option value="bigmodel">智谱</option><option value="zai">Z.AI</option></NativeSelect></label>}
    {id==='kimi'&&<label>站点<NativeSelect aria-label="官方账号站点" value={authOptions.region} disabled={blocked||active} onChange={event=>setSite(event.currentTarget.value)}><option value="global">Kimi 国际</option><option value="mainland-cn">Kimi 中国</option></NativeSelect></label>}</div>
    <div className="wd-partners-account-status"><Status state={auth} text={auth==='UNKNOWN'?'登录状态待确认':undefined}/>{loginProgress&&<span className="wd-partners-note" role="status">{loginProgress}</span>}</div>
    {['FAILED','FAILED_NO_SEND','ERROR'].includes(loginState||'')&&<p className="wd-partners-error" role="alert">{loginRun?.error?.split('\n')[0].replace(/https?:\/\/\S+/g,'官方授权页面').slice(0,240)||'登录未完成，请重新登录或核对状态。'}</p>}
    {unsupported(partner.account_support)?<p className="wd-partners-note">官方暂不支持此登录方式。</p>:<div className="wd-partners-actions">{(unknown(loginState)||!loginUrl||terminal(loginState))&&<Button variant="outline" disabled={blocked||startingLogin||(!release&&!active&&!unknown(loginState))} onClick={()=>void startLogin()}>{startingLogin?'正在准备登录…':unknown(loginState)?'核对原登录':active?'查看登录进度':mode==='work'?(auth==='AUTHENTICATED'?'重新登录 Work 账号':'前往官方登录'):(auth==='AUTHENTICATED'?'重新登录':'登录')}</Button>}{account&&<Button variant="outline" disabled={blocked||!release} onClick={()=>void perform('account.inspect',{account_id:account.id,release_id:release?.id},'已核对官方登录状态。')}>核对状态</Button>}{active&&loginRun&&<Button variant="outline" disabled={blocked||loginState==='STOP_REQUESTED'} onClick={()=>void perform('partner.stop',{run_id:loginRun.id},'正在取消本次登录。')}>取消登录</Button>}</div>}
    {!release&&!unsupported(partner.account_support)&&<p className="wd-partners-note">{ready.length?'请在工作端管理中选择使用版本。':'请先安装工作端。'}</p>}
    {unknown(loginState)&&loginRun?.local_exit_confirmed===true&&<div className="wd-partners-login-close">{closingLogin===loginRun.id?<div role="group" aria-label="确认结束本次登录"><p className="wd-partners-note">结束本地追踪，远端授权结果仍待核对。账号和已确认的登录状态保留，不会重新发起登录。</p><div className="wd-partners-actions"><Button variant="outline" disabled={busy} onClick={()=>setClosingLogin(null)}>继续保留</Button><Button variant="outline" disabled={busy} onClick={()=>void perform('partner.reconcile',{run_id:loginRun.id,resolution:'close_local_tracking'},'本次登录追踪已结束。').then(value=>{if(value)setClosingLogin(null);})}>确认结束登录</Button></div></div>:<Button variant="outline" disabled={busy} onClick={()=>setClosingLogin(loginRun.id)}>结束本次登录</Button>}</div>}
    {loginUrl&&!terminal(loginState)&&<OfficialLoginLink key={account?.id+':'+loginRun?.id} provider={id} identity={account?.id+':'+loginRun?.id} url={loginUrl}/>}
    {code&&!terminal(loginState)&&<p className="wd-partners-device-code">设备码：<strong>{code}</strong></p>}
    {active&&loginInstructions&&<p className="wd-partners-note">{loginInstructions}</p>}
    {requests.map(request=>{const sensitive=request.sensitive===true||request.input_type==='password',choices=Array.isArray(request.options)?request.options.filter((v):v is {value:string;label:string}=>!!v&&typeof v==='object'&&typeof v.value==='string'&&typeof v.label==='string'):[];return <form key={request.id} className="wd-partners-auth-input" onSubmit={event=>{event.preventDefault();const value=reply[request.id]?.trim();if(!value||!loginRun)return;setReply(prior=>({...prior,[request.id]:''}));void perform(sensitive?'account.login-input':'partner.input',{run_id:loginRun.id,request_id:request.id,text:value},'授权信息已发送。');}}><label>{sensitive?'官方授权码':choices.length?'登录方式':'官方要求的补充信息'}{choices.length?<NativeSelect aria-label="官方登录方式" value={reply[request.id]||''} disabled={blocked} onChange={event=>{const value=event.currentTarget.value;setReply(prior=>({...prior,[request.id]:value}));}}><option value="">请选择</option>{choices.map(choice=><option value={choice.value} key={choice.value}>{choice.label}</option>)}</NativeSelect>:<Input aria-label={sensitive?'官方授权码':'官方补充信息'} type={sensitive?'password':'text'} autoComplete="off" spellCheck={false} value={reply[request.id]||''} disabled={blocked} onChange={event=>{const value=event.currentTarget.value;setReply(prior=>({...prior,[request.id]:value}));}}/>}</label><Button type="submit" variant="outline" disabled={blocked||!reply[request.id]?.trim()}>提交</Button></form>;})}
    {!active&&!unsupported(partner.account_support)&&<details className="wd-partners-secondary"><summary>添加账号</summary><form className="wd-partners-add-account" onSubmit={event=>{event.preventDefault();if(newLabel.trim())void perform('account.create',{partner_id:id,label:newLabel.trim(),kind:'official_login',auth_options:authOptions},'账号已添加。').then(value=>{if(value?.account_id){setAccountId(value.account_id);setNewLabel('');}});}}><label>账号名称<Input aria-label="新账号名称" value={newLabel} onChange={event=>setNewLabel(event.currentTarget.value)} disabled={blocked}/></label><Button type="submit" variant="outline" disabled={blocked||!newLabel.trim()}>添加</Button></form></details>}
   </Panel>);
 return <div className={mode==='all'?'wd-partners':'wd-connect-form wd-partners-embedded'} data-partners-panel>
  <div className="wd-settings-section-header wd-partners-toolbar"><label className="wd-partners-endpoint">{mode==='all'?'官方工作端':'工作端'}<NativeSelect aria-label={mode==='all'?'官方工作端':'工作端'} value={id} disabled={busy||savingApi||pickingBundle||!loaded} onChange={event=>setPartnerId(event.currentTarget.value)}>{data.partners.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</NativeSelect></label>{mode==='all'&&<Button variant="outline" className="wd-settings-button" disabled={busy} onClick={()=>void refresh()}><RefreshCw size={14}/>刷新状态</Button>}</div>
  {notice&&<p role="status" className="wd-partners-feedback">{notice}</p>}{error&&<p role="alert" className="wd-partners-error">{error}</p>}
  {uncertain.length>0&&<div className="wd-partners-uncertain" role="alert"><p>有操作回执待核对。</p>{uncertain.map(item=><Button key={item.id} variant="outline" disabled={busy} onClick={()=>void receipt(item.id)}>核对原操作 · {item.id.slice(0,8)}</Button>)}</div>}
  {!loaded&&<p role="status">正在读取账号…</p>}
  {partner&&<>
   {mode!=='api'&&officialPanel()}
   {mode!=='work'&&<Panel title="Work API" plain={mode!=='all'}>
    <form className="wd-partners-api-form" onSubmit={event=>{event.preventDefault();void saveKey();}}>
     <label>大模型 API<NativeSelect aria-label="大模型 API" value={selectedApiProvider} disabled={blocked||savingApi||!apiProviders.length} onChange={event=>{setApiKey('');setApiProviderId(event.currentTarget.value);}}>{!apiProviders.length&&<option value="">API 列表暂不可用</option>}{apiProviders.map(provider=><option key={provider.id} value={provider.id}>{provider.name}</option>)}</NativeSelect></label>
     {!apiSupported&&<p className="wd-partners-note">{compatibility?.reason||'此 API 暂不支持当前工作端。'}</p>}
     <label>API Key<Input aria-label="API Key" type="password" autoComplete="off" spellCheck={false} value={apiKey} disabled={blocked||savingApi||!apiSupported} onChange={event=>setApiKey(event.currentTarget.value)}/></label>
     <div className="wd-partners-api-footer"><Status state={apiConnection?.state==='VERIFIED'?'VERIFIED':api?.key_ready?'SAVED_UNVERIFIED':'UNKNOWN'} text={apiConnection?.state==='VERIFIED'?'已连接':api?.key_ready?'Key 已保存':'未设置 Key'}/><Button type="submit" disabled={blocked||savingApi||!apiKey.trim()||!apiSupported}>保存</Button></div>
     {apiSupported&&readyChatKey&&api?.key_ready!==true&&<Button type="button" variant="outline" disabled={blocked||savingApi} onClick={()=>void saveKey(true)}>使用 Chat 的 Key</Button>}
     {apiSupported&&api?.key_ready===true&&release&&(!apiConnection||apiConnection.state==='DISABLED')&&!savingApi&&<Button type="button" variant="outline" disabled={blocked} onClick={()=>void saveConnection(api.id,'api',release).then(saved=>{if(saved)onApiChange?.();})}>启用此 Work 连接</Button>}
    </form>
   </Panel>}
   {mode==='all'&&<details className="wd-partners-management" onToggle={event=>{if(event.currentTarget.open)void refresh();}}><summary>工作端管理</summary><div className="wd-partners-management-body">
    <div className="wd-partners-actions" role="group" aria-label="工作端安装来源">
     <Button variant="outline" disabled={installBlocked} onClick={()=>void installComponent(false)}>{ready.length?'联网更新':'联网安装'}</Button>
     <Button variant="outline" disabled={installBlocked} onClick={()=>void installComponent(true)}>{pickingBundle?'正在选择补充包…':'导入本地补充包'}</Button>
    </div>
    {removalBlocked&&<p className="wd-partners-note">此工作端仍有登录或工作待完成，请先处理后再卸载。</p>}
    {releases.map(target=><div key={target.id} className="wd-partners-release"><div><strong>{target.version}</strong> <Status state={target.state}/></div><div className="wd-partners-actions">{usable(target)&&<Button variant="outline" disabled={blocked} onClick={()=>void useRelease(target)}>使用此版本</Button>}{target.state==='DISABLED'?<Button variant="outline" disabled={blocked} onClick={()=>void perform('component.enable',{release_id:target.id},'已启用工作端。')}>启用</Button>:<Button variant="outline" disabled={blocked||!usable(target)} onClick={()=>void perform('component.disable',{release_id:target.id},'已停用工作端。')}>停用</Button>}<Button variant="outline" disabled={blocked||removalBlocked||target.managed===false||target.state==='REMOVED'} onClick={()=>setRemove(target)}>卸载</Button></div></div>)}
    {remove&&<div className="wd-partners-confirm" role="group" aria-label="确认卸载组件"><strong>卸载 {partner.name} {remove.version}？</strong><p>{remove.path}</p><p>先停用引用此版本的 {connections.filter(c=>c.release_id===remove.id&&c.state!=='DISABLED').length} 条连接，再移除此版本的程序；项目、成果、账号与会话记录保留。恢复清单继续保留，恢复时可能需要重新下载同一版本。在途或结果未知的运行仍会阻止卸载。</p><div className="wd-partners-actions"><Button variant="outline" disabled={blocked} onClick={()=>setRemove(null)}>保留</Button><Button variant="destructive" disabled={blocked||removalBlocked} onClick={()=>void removeRelease(remove)}>确认卸载</Button></div></div>}
    {visibleOperations(componentOperations).map(op=><div key={op.id} className="wd-partners-operation">
     {installing(op)&&activeOperation(op.state)?<InstallProgress op={op} name={partner.name}/>:<Status state={op.state}/>}
     {unknown(op.state)&&<Button variant="outline" disabled={busy} onClick={()=>void perform('component.reconcile',{operation_id:op.id},'已核对原安装结果。')}>核对安装结果</Button>}
     {op.error&&<p className="wd-partners-error">{op.error}</p>}
    </div>)}
   </div></details>}
  </>}
 </div>;
}
