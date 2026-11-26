import { createContext, useContext, useLayoutEffect, useRef, useState, type ReactElement } from 'react';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useAuiState, ThreadPrimitive, MessagePrimitive, ComposerPrimitive, type ThreadMessageLike, type TextMessagePartProps } from '@assistant-ui/react';
import { ArrowUp, Plus, Folder, File, Plug, Lock, ChevronDown, Workflow, Sparkles, Copy, Pencil } from 'lucide-react';
import { ContextMenu } from 'radix-ui';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { NativeSelect } from '../components/ui/native-select';
import { Badge } from '../components/ui/badge';
import { Template } from '../components/template';
import { IndependentConversationHeader, IndependentModels, IndependentProvider, IndependentSidebar, IndependentWelcome, WorkModeSwitch, independentAppearance, independentBrand, IndependentService, IndependentRunState } from './independent-work';
import { ChatModels, ChatContext, ChatTurnDetails, chatFallback } from './chat';
import './conversation.css';
import { NativeAccess, NativeProcess, NativeUsage } from './native-work';
import { nativeTrace } from './native-events';
import { useQuickWorkspaceView } from './quick-workspace';
import { ConversationScroll } from './conversation-scroll';
import { PartnerWork } from './partner-work';

const active = new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
const ProviderName=createContext('AI');
const NativeDetail=createContext<any>(null);
const WorkNames=createContext<{user:string;assistant:string}|null>(null);
function message(id:string, role:'user'|'assistant', content:string, state?:string, question?:string): ThreadMessageLike {
  return {id, role, content,
    metadata:{custom:{originalText:content,originalQuestion:question}}};
}
function messagesFor(v:any): ThreadMessageLike[] {
  if(v.mode==='chat') {
    const c=v.chats.find((c:any)=>c.id===v.activeChat);
    return (c?.messages||[]).map((m:any,i:number)=>message(m.id||`${c.id}-${i}`,m.role==='human'?'user':'assistant',m.content||chatFallback(m.state),m.state));
  }
  if(v.dialogue) return v.dialogue.messages.map((m:any,i:number)=>message(m.id||`${v.dialogue.id}-${i}`,m.role==='human'?'user':'assistant',m.content));
  if(!v.detail) return [];
  const d=v.detail, result:ThreadMessageLike[]=[];
  if(!d.task.spec.native_fork&&!d.attempts[0]?.native_history_reverted)result.push(message(d.task.id+'-goal','user',d.task.spec.goal));
  let question=d.task.spec.goal;
  for(const a of d.attempts) {
    if(a.native_history_reverted)continue;
    const note=d.events.find((e:any)=>e.kind==='execution_reserved'&&e.attempt_id===a.id)?.payload.human_note;
    if(note){question=note;result.push(message(a.id+'-human','user',note));}
    const output=a.result?.answer||nativeTrace(d,a).text||d.events.filter((e:any)=>e.kind==='model_delta'&&e.attempt_id===a.id).map((e:any)=>e.payload.text||'').join('');
    result.push(message(a.id+'-answer','assistant',typeof output==='string'?output:JSON.stringify(output),a.state,question));
  }
  return result;
}
function SafeText({text}:TextMessagePartProps) {
  return <Template html={window.WorkdeskLegacy.markdown(text)}/>;
}
function WorkMessageActions({children, edit=false}:{children:ReactElement;edit?:boolean}) {
  const detail=useContext(NativeDetail);
  const text=useAuiState(s=>s.message.metadata.custom.originalText as string|undefined);
  const [selection,setSelection]=useState('');
  const movedFocus=useRef(false);
  if(!detail||!text?.trim())return children;
  const bridge=window.WorkdeskWorkbench;
  const editMessage=()=>bridge.editWorkMessage(detail.task.id,text);
  return <ContextMenu.Root modal={false}>
    <ContextMenu.Trigger asChild onContextMenuCapture={event=>{
      const selected=document.getSelection();
      const node=event.currentTarget;
      setSelection(selected?.anchorNode&&selected.focusNode&&node.contains(selected.anchorNode)&&node.contains(selected.focusNode)?selected.toString():'');
    }}>{children}</ContextMenu.Trigger>
    <div className="wk-message-actions">
      <Button type="button" variant="ghost" size="icon-xs" aria-label="复制消息" title="复制消息" onClick={()=>void bridge.copyMessage(text)}><Copy/></Button>
      {edit&&<Button type="button" variant="ghost" size="icon-xs" aria-label="重新编辑" title="将消息放回输入框编辑" onClick={editMessage}><Pencil/></Button>}
    </div>
    <ContextMenu.Portal>
      <ContextMenu.Content data-ui-module="workbench" className="wk-message-menu" aria-label="消息操作" collisionPadding={12}
        onCloseAutoFocus={event=>{if(movedFocus.current)event.preventDefault();movedFocus.current=false;}}>
        {selection&&<ContextMenu.Item className="wk-message-menu-item" onSelect={()=>void bridge.copyMessage(selection)}><Copy/>复制所选文字</ContextMenu.Item>}
        <ContextMenu.Item className="wk-message-menu-item" onSelect={()=>void bridge.copyMessage(text)}><Copy/>复制消息</ContextMenu.Item>
        {edit&&<ContextMenu.Item className="wk-message-menu-item" onSelect={()=>{movedFocus.current=editMessage();}}><Pencil/>重新编辑</ContextMenu.Item>}
      </ContextMenu.Content>
    </ContextMenu.Portal>
  </ContextMenu.Root>;
}
function UserMessage() {
  const work=!!useContext(NativeDetail);
  const names=useContext(WorkNames);
  return <MessagePrimitive.Root className={'wk-user'+(work?' wk-user-actions':'')}>
    {names&&<div className="wk-byline">{names.user}</div>}
    <WorkMessageActions edit><div className="aui-message-text"><MessagePrimitive.Parts components={{Text:SafeText}}/></div></WorkMessageActions>
  </MessagePrimitive.Root>;
}
function AssistantMessage() {
  const provider=useContext(ProviderName),names=useContext(WorkNames);
  const detail=useContext(NativeDetail),id=useAuiState(s=>s.message.id);
  const body=useAuiState(s=>s.message.metadata.custom.originalText as string|undefined);
  const question=useAuiState(s=>s.message.metadata.custom.originalQuestion as string|undefined);
  const attempt=detail?.attempts.find((a:any)=>id===a.id+'-answer');
  const served=attempt?.served_model_observations?.at(-1)?.served_model_labels;
  const model=(served?.length===1?served[0]:attempt?.model)||detail?.task.spec.model;
  const name=names?names.assistant||model||provider:provider;
  const retry=attempt&&(['FAILED','FAILED_NO_SEND','INTERRUPTED','STOPPED_RUNTIME','CANCELLED'].includes(attempt.state)||!!attempt.result?.manual_close)&&!!question?.trim();
  return <MessagePrimitive.Root className="wk-answer">{body?.trim()&&<span className="wk-avatar"><Sparkles/></span>}<div className="aui-message-body">{body?.trim()&&<div className="wk-byline">{name}</div>}{attempt&&<NativeProcess trace={nativeTrace(detail,attempt)}/>}<WorkMessageActions><div className="markdown wb-answer"><MessagePrimitive.Parts components={{Text:SafeText}}/></div></WorkMessageActions>
    {retry&&<div className="wk-message-recovery" role="group" aria-label="本回合原问题">
      <Button type="button" variant="outline" size="sm" onClick={()=>void window.WorkdeskWorkbench.copyMessage(question)}>复制原问题</Button>
      <Button type="button" variant="ghost" size="sm" onClick={()=>window.WorkdeskWorkbench.editWorkMessage(detail.task.id,question)}>重新编辑原问题</Button>
    </div>}
  </div></MessagePrimitive.Root>;
}

export function Conversation(props:any) {
  const detail=props.v.detail,runId=detail?.partner_run_id||detail?.task?.partner_run_id||detail?.task?.spec?.partner_run_id;
  if(props.v.mode!=='chat'&&runId)return <PartnerWork key={runId} runId={runId} task={detail.task}/>;
  if(props.v.mode!=='chat'&&detail?.task?.spec?.execution==='partner_native')return <section className="wd-partners"><h1>{detail.task.title||'伙伴工作'}</h1><p role="status">原生运行归属待核对。保留原记录，没有重新发送。</p><a href="#settings/operations">运行与恢复</a></section>;
  return <LegacyConversation {...props}/>;
}
function LegacyConversation({v,scenes,names,isLocked,controls,independent,cooperation,chat:chatInfo}:any) {
  const bridge=window.WorkdeskWorkbench;
  const chat=v.mode==='chat', c=v.chats.find((c:any)=>c.id===v.activeChat);
  const detail=v.detail||v.dialogueRuntime;
  const pending=chat?!!c?.pending:!!(v.dialogue?.pending||active.has(detail?.task?.state));
  const selected=chat?!!c:!!(v.detail||v.dialogue);
  const draft=chat?v.chatInput:v.input;
  const latest=useRef({chat,pending});latest.current={chat,pending};
  const runtime=useExternalStoreRuntime({
    messages:messagesFor(v),
    isRunning:pending,
    convertMessage:(m:ThreadMessageLike)=>m,
    onNew:async m=>{
      const text=m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n');
      try { await bridge.submitText(text); }
      finally {
        // Retain text on failed/unknown sends; clear only after a durable receipt.
        queueMicrotask(()=>runtime.thread.composer.setText(bridge.inputText()));
      }
    },
    onCancel:async()=>{await bridge.perform(latest.current.chat?'chat-stop':'runtime-stop');},
  });
  useLayoutEffect(()=>{if(runtime.thread.composer.getState().text!==draft)runtime.thread.composer.setText(draft);},[runtime,draft]);
  const profile=v.data?.profiles.find((p:any)=>p.id===v.provider);
  const project=v.data?.projects.find((p:any)=>p.id===v.project);
  const count=String(v.files||'').split(/\r?\n/).filter(Boolean).length+v.attachments.length;
  const independentWork=!chat&&v.type==='independent';
  const compactWork=independentWork&&!!v.detail?.attempts.length;
  const independentConversation=independentWork&&!!v.detail;
  const workspaceKey=`work:${v.detail?.task.id||'draft'}:project:${v.detail?.task.project_id||''}`;
  const workspace=useQuickWorkspaceView(independentConversation,workspaceKey);
  const preferences=(window as Window & {WorkdeskSettingsPreferences?:{get:()=>{userNickname?:string;assistantNickname?:string}}}).WorkdeskSettingsPreferences?.get();
  const workNames=independentConversation?{user:preferences?.userNickname?.trim()||'User',assistant:preferences?.assistantNickname?.trim()||''}:null;
  const Scene=()=> <Button variant="ghost" className="wk-chip" data-wb-menu="scene" aria-label="选择工作场景" disabled={isLocked||v.workLoading}><Folder/><span>{scenes[v.scene]?.name||'未分类'}</span>{isLocked?<Lock/>:<ChevronDown/>}</Button>;
  return <NativeDetail.Provider value={independentWork?v.detail:null}><WorkNames.Provider value={workNames}><ProviderName.Provider value={names[chat?(c?.provider||v.chatProvider):(v.dialogue?.config.provider||v.provider)]||'AI'}><AssistantRuntimeProvider runtime={runtime}>
    <ThreadPrimitive.Root className={`wk-conversation ${independentWork?'wk-independent':''} ${selected?'':'is-empty'} ${independentConversation?'wk-independent-active':''} ${independentConversation&&workspace.open?'wk-independent-side-open':''}`} aria-label={chat?'Chat 对话区':'Work 对话区'} aria-busy={!chat&&!!v.workLoading} data-workspace-expanded={independentConversation&&workspace.expanded} data-assistant-ui="external-store" data-work-mode={chat?'chat':v.type} data-work-provider={independentWork?v.provider:undefined} style={independentWork?independentAppearance(v.provider) as any:undefined}>
      {independentConversation?<IndependentConversationHeader v={v} open={workspace.open} toggleRef={workspace.toggleRef}
        onToggle={workspace.toggle} onOpenTab={(tool,entry)=>workspace.openTool(tool,entry?.file)}/>:!chat&&<div className="wk-work-toolbar">
        {independentWork?<IndependentProvider v={v} names={names} isLocked={isLocked}/>:<span className="wk-work-toolbar-label"><Workflow/>合作工作</span>}
        <WorkModeSwitch v={v} selected={selected}/>
      </div>}
      {selected&&<>
        {independentConversation?<aside className="wk-independent-context" data-independent-work="context" data-locked={isLocked} aria-label="独立工作概况">
          <strong className="wk-independent-context-caption">独立工作</strong>
          <dl>
            {v.detail.task.spec.model&&<div><dt>模型</dt><dd>{v.detail.task.spec.model}</dd></div>}
            {project&&<div><dt>项目</dt><dd>{project.name}</dd></div>}
            {isLocked&&<div><dt>场景</dt><dd className="wk-independent-context-scene">{scenes[v.scene]?.name||'未分类'}</dd></div>}
          </dl>
          {!isLocked&&<div className="wk-independent-draft-context"><Scene/><IndependentProvider v={v} names={names} isLocked={isLocked}/></div>}
          <div className="wk-independent-context-links">
            <a className="wk-independent-context-item" href={`#work-items/view-work/${encodeURIComponent(v.detail.task.id)}`}>任务详情</a>
          </div>
        </aside>:<div className="wk-active-bar">{chat?<><span>{names[c.provider]} · {c.config.model}</span><Badge variant="secondary">工作端文本对话</Badge>{pending&&<Button variant="outline" data-wb="chat-stop">停止回复</Button>}<Button variant="outline" data-wb="official-web">官方网页</Button></>:<><Scene/><span className="wk-faint">合作工作</span><Button variant="outline" data-wb="details">工作详情</Button><a className="wk-button" href={v.dialogue?'#collab/dialogue/'+v.dialogue.id:'#control/task/'+v.detail.task.id}>{v.dialogue?'方案与队列':'运行与控制'}</a>{v.detail?.task.spec.runtime_mode==='official'&&<Button variant="ghost" className="wk-chip" data-wb="native-panel">工作端控制</Button>}</>}</div>}
        <ThreadPrimitive.Viewport className="wk-thread" data-reading-viewport autoScroll scrollToBottomOnRunStart={false}>
          {!chat&&v.detail?.task.spec.native_fork&&<p className="wk-note">此会话沿用原生分叉历史，可在“工作端控制 → 会话历史”查看。</p>}
          {!chat&&v.detail?.attempts.some((a:any)=>a.native_history_reverted)&&<p className="wk-note">已按官方回退结果更新对话。回退前的运行审计与用量保留在“运行与控制”，项目文件没有撤销。</p>}
          <ThreadPrimitive.Messages components={{UserMessage,AssistantMessage}}/>
          {!chat&&<Template html={controls}/>}
          {!chat&&v.type==='independent'&&<NativeUsage detail={v.detail}/>}
          {chat&&<ChatTurnDetails messages={c.messages}/>}
          {!chat&&v.dialogue?.pending&&<p className="wk-note">统筹 AI 正在处理。<a href={'#control/task/'+v.dialogue.pending}>查看运行与审批</a></p>}
          {!chat&&v.detail&&!v.detail.attempts.length&&<p className="wk-note">任务草稿已保存，尚未开始。</p>}
          <ConversationScroll conversationKey={(chat?'chat:':'work:')+(c?.id||v.detail?.task.id||v.dialogue?.id||'new')}/>
        </ThreadPrimitive.Viewport>
      </>}
      <div className={'wk-home'+(compactWork?' wk-home-compact':'')}>
        {independentConversation&&<IndependentRunState detail={v.detail} busy={v.busy}/>}
        {!selected&&<>{independentWork?<IndependentWelcome provider={v.provider}/>:<div className="wk-greeting"><h1>{chat?'今天想聊些什么？':'我们要做什么？'}</h1>{!chat&&<p>从一个目标开始，把事情一步步完成。</p>}</div>}{!chat&&<div className="wk-setup"><Scene/></div>}</>}
        <ComposerPrimitive.Root id={chat?'wb-chat-form':'wb-work-form'} onSubmit={e=>e.stopPropagation()}>
          <div className="wk-composer-group"><div className="wk-composer">
            <ComposerPrimitive.Input asChild submitMode="none" addAttachmentOnPaste={false} cancelOnEscape={false} unstable_focusOnThreadSwitched={false}>
              <Textarea id={chat?'wb-chat-input':'wb-work-input'} rows={compactWork?1:2} aria-label={chat?'Chat 消息':'工作目标'} disabled={!chat&&v.workLoading} placeholder={chat?'有什么想法，尽管聊聊…':selected?'补充要求，继续推进这项工作…':independentWork?independentBrand(v.provider).placeholder:'描述目标，或说说你想做什么…'}/>
            </ComposerPrimitive.Input>
            <div className="wk-composer-tools">{!chat&&<Button type="button" variant="ghost" size="icon" className="wk-icon"
              data-wb-menu="work-tools" aria-haspopup="menu" aria-expanded={v.menu==='work-tools'}
              aria-label="工作功能" title="添加文件、Skill、MCP 和聊天引用" disabled={v.workLoading||v.busy}><Plus/></Button>}
              {!chat&&v.workPlanOnly&&<Button type="button" variant="ghost" className="wk-chip" data-wb="work-plan-mode"
                aria-pressed="true" disabled={v.busy||pending}>计划模式</Button>}
              {!chat&&!selected&&v.formDraft.composerCapabilities?.length>0&&<Button type="button" variant="ghost"
                className="wk-chip" data-wb="work-capabilities" data-kind="all" disabled={v.busy}>
                <Plug/>能力 · {v.formDraft.composerCapabilities.length}</Button>}
              {independentWork&&!v.workPlanOnly&&<NativeAccess v={v}/>}
              <div className="wk-model-controls">{chat?<ChatModels v={v} names={names} chat={chatInfo}/>:v.type==='cooperative'?
                <Button variant="ghost" type="button" className="wk-chip wk-cooperation-picker" data-wb={selected?'roles':'cooperation-settings'}
                  aria-label={selected?'协作分工':'合作方式与分工'} aria-haspopup="dialog" disabled={v.workLoading}>
                  <Workflow/><span>{selected?'协作分工':cooperation}</span><ChevronDown/>
                </Button>:<IndependentModels v={v} names={names} profile={profile} isLocked={isLocked}/>}</div>
              {independentWork&&pending&&v.detail?.task.spec.runtime_mode==='official'&&v.detail.task.spec.qwen_runtime!=='daemon'?<Button type="button" data-wb="native-steer" disabled={v.busy||!v.input.trim()} title="通过原生接口补充指令">补充指令</Button>:<ComposerPrimitive.Send asChild><Button type="button" className="wk-send" aria-label={chat?'发送 Chat 消息':v.workPlanOnly?'发送计划请求':selected?'发送工作消息':independentWork?'开始独立工作':'准备开始工作'} disabled={v.busy||pending||(!chat&&v.workLoading)}><ArrowUp/></Button></ComposerPrimitive.Send>}
            </div>
          </div>{!chat&&!compactWork&&<div className="wk-shelf"><Button type="button" variant="ghost" className="wk-chip wk-project-chip" data-wb-menu="project" aria-label="选择工作区或项目" disabled={selected||v.workLoading}><Folder/><span>{project?.name||'选择工作区'}</span><ChevronDown/></Button><Button type="button" variant="ghost" className="wk-chip" data-wb="files" disabled={v.workLoading}><File/>文件{count?' · '+count:''}</Button><Button type="button" variant="ghost" className="wk-chip" data-wb="skills" disabled={v.workLoading}><Plug/>插件与 Skills</Button></div>}</div>
        </ComposerPrimitive.Root>
        {chat?<ChatContext chat={chatInfo} selected={!!c}/>:!compactWork&&<p className="wk-hint" role={v.workLoading?'status':undefined}>{v.workLoading?'正在同步工作配置与记录…':v.detail?.task.spec.runtime_mode==='official'?'模型、思考和权限选择在下一回合生效':isLocked?'当前工作场景已固定':selected?'草稿尚未开始，可修订工作配置':independentWork?'开始前可调整场景、工作端和材料':'开始前可调整场景与分工'}</p>}
        {independentWork&&!compactWork&&<IndependentService service={independent?.service}/>}
        {chat&&!c&&<Button variant="outline" data-wb="official-web">使用官方网页</Button>}
        {v.pending&&<p className="wk-note">最近命令 {v.pending.id} · <a href="#control/receipts">查询回执</a></p>}
      </div>
      {independentConversation&&<IndependentSidebar v={v} open={workspace.open} panelRef={workspace.panelRef}
        onClose={workspace.close} expanded={workspace.expanded} onToggleExpanded={workspace.toggleExpanded}
        requestedTool={workspace.requestedTool}/>}
    </ThreadPrimitive.Root>
  </AssistantRuntimeProvider></ProviderName.Provider></WorkNames.Provider></NativeDetail.Provider>;
}
