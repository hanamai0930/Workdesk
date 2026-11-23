import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { Textarea } from '../components/ui/textarea';
import { Template } from '../components/template';
import { ArrowUp, RotateCw, ExternalLink, ChevronDown, Sparkles, MessageSquare, PanelRight, MoreHorizontal, Share2, Plus, Paperclip, History, Info } from 'lucide-react';
import { useDesktopChat } from './desktop-chat';
import { QuickWorkspace, useQuickWorkspaceView } from './quick-workspace';
import { apiSummary, ConversationSummaryButton } from './conversation-summary';
import './chat.css';

export function ChatRoutePicker({route,busy}:any){return <NativeSelect key={route} id="wb-chat-route" aria-label="聊天方式" defaultValue={route} disabled={busy}><option value="api">API 连接</option><option value="web">官方网页</option></NativeSelect>;}

export function chatDisplayNames(model:string){
  const preferences=(window as Window & {WorkdeskSettingsPreferences?: {get:()=>{userNickname?:string;assistantNickname?:string}}}).WorkdeskSettingsPreferences?.get();
  return {user:preferences?.userNickname?.trim()||'User',assistant:preferences?.assistantNickname?.trim()||model||'AI'};
}

type ChatRecord=NonNullable<Parameters<typeof apiSummary>[0]>&{id:string;archived?:boolean;list_project?:string};

export function ChatFrame({record,records,projectId,api,filesDisabled,actions,children}:{
  record?:ChatRecord;records:ChatRecord[];projectId?:string;api?:boolean;filesDisabled?:boolean;actions?:ReactNode;children:ReactNode;
}){
  const workspaceKey=`chat:${record?.id||'new'}`;
  const workspace=useQuickWorkspaceView(true,workspaceKey);
  const title=record?.title||'新聊天';
  return <>
    <header className="wd-chat-header">
      <h1 title={title}><MessageSquare aria-hidden="true"/><span>{title}</span></h1>
      <div className="wd-chat-tools">
        {actions}
        {record?<>
          <Button asChild variant="ghost" size="icon-sm"><a href={'#work-items/view-chat/'+encodeURIComponent(record.id)}
            aria-label="对话详情" title="对话详情" data-chat-detail><Info aria-hidden="true"/></a></Button>
          <Button type="button" variant="ghost" size="icon-sm" data-wb-menu="conversation" aria-label="对话操作" title="对话操作" aria-expanded={false}><MoreHorizontal/></Button>
          <Button type="button" variant="ghost" size="icon-sm" data-wb="share" aria-label="分享 / 导出" title="分享 / 导出"><Share2/></Button>
          <ConversationSummaryButton {...apiSummary(record)} onOpenTab={(tool,entry)=>workspace.openTool(tool,entry?.file)}
            onAttach={api&&!filesDisabled?()=>{void window.WorkdeskWorkbench.perform('chat-api-upload');}:undefined}/>
        </>:<Button type="button" variant="ghost" size="icon-sm" data-wb="new" aria-label="新建对话" title="新建对话"><Plus/></Button>}
        <Button ref={workspace.toggleRef} type="button" variant="ghost" size="icon-sm" data-chat-panel-toggle
          aria-label={workspace.open?'关闭右侧边栏':'打开右侧边栏'} aria-keyshortcuts="Control+Shift+B"
          title={workspace.open?'收起标签页（Ctrl+Shift+B）':'显示标签页（Ctrl+Shift+B）'}
          aria-controls="wd-chat-side-panel" aria-expanded={workspace.open} onClick={workspace.toggle}><PanelRight/></Button>
      </div>
    </header>
    <div className="wd-chat-layout" data-workspace-expanded={workspace.expanded}>
      <div className="wd-chat-main">{children}</div>
      <aside ref={workspace.panelRef} id="wd-chat-side-panel" className="wd-chat-side" data-chat-panel hidden={!workspace.open} aria-label="聊天侧栏">
        <QuickWorkspace stateKey={workspaceKey} projectId={record?.list_project} records={records} currentChat={record?.id} onClose={workspace.close}
          expanded={workspace.expanded} onToggleExpanded={workspace.toggleExpanded} requestedTool={workspace.requestedTool}>
          <div className="wd-chat-side-actions">
            {api&&<button type="button" data-wb="chat-api-upload" disabled={filesDisabled}><Paperclip aria-hidden="true"/>添加聊天文件</button>}
            <a href="#records"><History aria-hidden="true"/>查看记录</a>
          </div>
        </QuickWorkspace>
      </aside>
    </div>
  </>;
}

export function OfficialChat({v,chat}:any){
  const c=v.chats.find((row:any)=>row.id===v.activeChat),service=chat.services.find((s:any)=>s.provider===v.chatProvider)||chat.services[0];
  const slot=useRef<HTMLDivElement>(null),intro=useRef<HTMLDivElement>(null),legacy=c&&c.route!=='web';
  const active=!!v.chatEmbedded&&!legacy;
  const desktop=useDesktopChat(v.chatProvider,c?.provider===v.chatProvider?c.web_url:undefined,active,slot);
  const display=chatDisplayNames(c?.config?.model||service?.name);
  useLayoutEffect(()=>{if(!matchMedia('(prefers-reduced-motion: reduce)').matches)intro.current?.animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,easing:'ease-out'});},[v.chatProvider]);
  return <section className={'wd-chat '+(active?'is-connected':'')} data-chat-surface data-official-chat="desktop" data-provider={v.chatProvider} style={{'--chat-rgb':'var(--wd-chat-'+v.chatProvider+'-rgb,'+(service?.brand_rgb||'16,163,127')+')','--chat-opacity':'var(--wd-chat-'+v.chatProvider+'-opacity,.05)'} as any}>
    <ChatFrame record={c} records={v.chats} projectId={v.project} actions={<>
      {active&&<Button type="button" variant="ghost" size="icon-sm" data-wb="chat-reload" aria-label="重新载入聊天" title="重新载入聊天"><RotateCw/></Button>}
      <Button type="button" variant="ghost" size="icon-sm" data-wb="chat-window" aria-label="备用：在浏览器打开" title="在浏览器打开"><ExternalLink/></Button>
    </>}>
    <div className="wd-chat-toolbar">
      <ChatRoutePicker route="web" busy={v.busy}/>
      <div className="wd-chat-switch"><NativeSelect key={v.chatProvider} id="wb-chat-provider" aria-label="Chat 大模型" defaultValue={v.chatProvider}>{chat.services.map((s:any)=><option key={s.provider} value={s.provider}>{s.name}</option>)}</NativeSelect><ChevronDown aria-hidden="true"/></div>
      {active&&service?.web_url&&<span className="wd-chat-origin">{desktop.status==='loading'?'正在载入…':new URL(service.web_url).host}</span>}
      {c?.route==='web'&&<Button type="button" variant="ghost" size="sm" data-wb="bind-web" className="wd-chat-link">会话链接</Button>}
    </div>
    {!active?<div className="wd-chat-welcome" ref={intro}>
      {legacy?<><p className="wd-chat-legacy-note">已保存的历史对话</p><div className="wd-chat-history">{c.messages.map((m:any,i:number)=><div key={m.id||i}><span className="wd-chat-byline">{m.role==='human'?display.user:display.assistant}</span><Template html={window.WorkdeskLegacy.markdown(m.content||'')}/></div>)}</div></>:<h2>{service?.greeting||'今天想聊点什么？'}</h2>}
      {c?.forked_from&&c.prompt&&<div className="wd-chat-history"><p className="wd-chat-legacy-note">分叉保留的开场文字</p><Template html={window.WorkdeskLegacy.markdown(c.prompt)}/></div>}
      <button type="button" className="wd-chat-entry" data-wb="chat-enter" aria-label={'开始与 '+service?.name+' 聊天'} disabled={v.busy}><Sparkles className="wd-chat-spark"/><span>{service?.placeholder||'开始聊天'}</span><span className="wd-chat-send"><ArrowUp/></span></button>
      <p className="wd-chat-caption">{desktop.supported?'点击进入，直接在这里聊天':'请在 Workdesk 桌面窗口打开内置聊天'}</p>
      {!desktop.supported&&<p className="wd-chat-unavailable">当前浏览器窗口不支持内置聊天。<a href={c?.web_url||service?.web_url} target="_blank" rel="noopener noreferrer">需要时在浏览器继续</a></p>}
    </div>:<div className="wd-chat-browser" ref={slot} aria-label={service?.name+' 主聊天区'}>
      <div className="wd-chat-loading" ref={intro}><span className="wd-chat-mark">{service?.name?.slice(0,1)}</span><h2>{service?.greeting}</h2><p>{desktop.status==='error'?'页面暂时未能载入，请重新载入；也可使用右上角的备用入口。':'正在准备 '+service?.name+'…'}</p></div>
    </div>}
    </ChatFrame>
  </section>;
}

export function ChatModels({v,names,chat}:any){
  const models=chat?.service?.models||[];
  return <><NativeSelect key={v.chatProvider} id="wb-chat-provider" aria-label="Chat 大模型" className="wk-model-select" defaultValue={v.chatProvider}>{Object.entries(names).map(([id,n]:any)=><option key={id} value={id}>{id==='codex'?'ChatGPT / Codex':n}</option>)}</NativeSelect><NativeSelect key={v.chatProvider+':'+v.chatModel} id="wb-chat-model" aria-label="Chat 工作模型" className="wk-model-select" defaultValue={v.chatModel}>{models.map((m:any)=><option key={m.model} value={m.model}>{m.model}</option>)}</NativeSelect></>;
}
export function ChatContext({chat,selected}:any){
  return <div className="wk-hint" data-chat-service><span>{chat?.changesConversation?'此选择会新建聊天 · 原聊天配置保留':selected?'使用本地保存正文建立上下文':'使用已配置的官方服务进行文本聊天'} · 不授予工具权限</span><div><Button type="button" variant="ghost" data-wb="chat-service">聊天服务与上下文</Button></div>{chat?.service&&!chat.service.runtime_present&&<p className="wk-error">本地运行服务不可用，可检查配置或使用官方网页。</p>}</div>;
}
const stopped=new Set(['INTERRUPTED','STOPPED_RUNTIME','CANCELLED']);
export function chatFallback(state:string){
  if(stopped.has(state))return '本回合已停止，没有回复正文。';
  if(['SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN'].includes(state))return '本回合状态待核对，请查看运行记录。';
  return '';
}
export function ChatTurnDetails({messages}:any){
  return <>{messages.map((m:any,i:number)=><div key={m.id||i}>{m.errors?.length>0&&!stopped.has(m.state)&&<p className="wk-error">{m.errors.map((e:any)=>e.message||JSON.stringify(e)).join('；')}</p>}{m.task_id&&<a className="wk-faint" href={'#control/task/'+m.task_id}>查看本回合运行记录</a>}</div>)}</>;
}
