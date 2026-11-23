import { useContext, useLayoutEffect, useState, createContext, type DragEvent } from 'react';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useAuiState, ThreadPrimitive, MessagePrimitive, ComposerPrimitive, type ThreadMessageLike, type TextMessagePartProps } from '@assistant-ui/react';
import { ArrowUp, Square, Sparkles, Brain, Globe, Paperclip, Copy, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { NativeSelect } from '../components/ui/native-select';
import { Template } from '../components/template';
import { ChatFrame, ChatRoutePicker, chatDisplayNames } from './chat';
import './api-chat.css';
import { ConversationScroll } from './conversation-scroll';

const active=new Set(['RESERVED','SEND_INTENT','RUNNING','STOP_REQUESTED']);
const ApiDisplayNames=createContext({user:'User',assistant:'AI'});
const ApiMessages=createContext<any[]>([]);
function SafeText({text}:TextMessagePartProps){return <Template html={window.WorkdeskLegacy.markdown(text)}/>;}
function UserMessage(){const {user}=useContext(ApiDisplayNames);return <MessagePrimitive.Root className="wd-api-user"><span className="wd-chat-byline">{user}</span><div><MessagePrimitive.Parts components={{Text:SafeText}}/></div></MessagePrimitive.Root>;}
function AssistantMessage(){
  const {assistant}=useContext(ApiDisplayNames),rows=useContext(ApiMessages);
  const id=useAuiState(state=>state.message.id),turn=rows.find(row=>row.id===id);
  const question=turn?.turn_id?rows.find(row=>row.role==='human'&&row.turn_id===turn.turn_id):null;
  const hasBody=!!turn?.content?.trim(),waiting=active.has(turn?.state);
  const stopped=turn?.state==='INTERRUPTED';
  const unknown=['SEND_UNKNOWN','ORPHANED_UNKNOWN','STOP_UNKNOWN'].includes(turn?.state);
  const failed=['FAILED','FAILED_NO_SEND'].includes(turn?.state);
  const status=waiting?(turn.state==='STOP_REQUESTED'?'正在停止接收回复':turn.search_state==='REQUEST_INTENT'?'正在联网搜索':turn.reasoning?'正在思考':'正在回复'):
    stopped?'已停止接收':unknown?'本回合状态待核对':failed?(turn.state==='FAILED_NO_SEND'?'本回合未发送':'本回合请求失败'):!hasBody?'本回合没有回复正文':'';
  return <MessagePrimitive.Root className={'wd-api-answer'+(!hasBody?' wd-api-answer-status':'')}>
    {hasBody&&<Sparkles aria-hidden="true"/>}
    <div>
      {hasBody&&<><span className="wd-chat-byline">{assistant}</span><div className="markdown"><MessagePrimitive.Parts components={{Text:SafeText}}/></div></>}
      {(status||turn?.error)&&<section className="wd-api-turn-status" data-api-turn-state={turn?.state} role={failed||unknown?'alert':'status'}>
        {status&&<strong>{status}</strong>}
        {turn?.error&&<p>{turn.error}</p>}
        {(failed||unknown||stopped)&&question?.content&&<div className="wd-api-retry">
          <Button type="button" variant="outline" size="sm" onClick={()=>void window.WorkdeskWorkbench.copyMessage(question.content)}><Copy/>复制原问题</Button>
          <small>可粘贴到输入框重新编辑。再次发送会创建新请求，按官方规则计费。{question.attachments?.length?'原附件需重新选择。':''}</small>
        </div>}
      </section>}
    </div>
  </MessagePrimitive.Root>;
}

function isFileDrag(event: DragEvent) {
  return event.dataTransfer.types.includes('Files');
}

type TokenReceipt = {
  prompt_tokens?: number; completion_tokens?: number; total_tokens?: number;
  prompt_cache_hit_tokens?: number; prompt_cache_miss_tokens?: number;
  prompt_tokens_details?: { cached_tokens?: number };
};
const tokenNumber = new Intl.NumberFormat('zh-CN');
function tokenTotal(usage?: TokenReceipt) {
  return usage?.total_tokens ?? (usage?.prompt_tokens != null && usage.completion_tokens != null
    ? usage.prompt_tokens + usage.completion_tokens : undefined);
}
function ChatTokenUsage({ replies }: { replies: { usage?: TokenReceipt; state: string }[] }) {
  const last = replies.at(-1);
  if (!last) return null;
  const receipt = last.usage;
  const totals = replies.map(reply => tokenTotal(reply.usage)).filter((n): n is number => n != null);
  const total = tokenTotal(receipt);
  const fields = [
    ['输入合计', receipt?.prompt_tokens], ['其中缓存命中', receipt?.prompt_cache_hit_tokens ?? receipt?.prompt_tokens_details?.cached_tokens],
    ['其中缓存未命中', receipt?.prompt_cache_miss_tokens], ['输出', receipt?.completion_tokens],
  ] as const;
  return <details className="wd-api-usage">
    <summary>本轮 Token 用量 · {total != null ? tokenNumber.format(total) : active.has(last.state) ? '等待官方回执' : '未取得完整回执'}
      {totals.length > 0 && <span>会话累计{totals.length < replies.length ? '（已知）' : ''} {tokenNumber.format(totals.reduce((a,b)=>a+b,0))}</span>}
    </summary>
    <dl>{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value == null ? '未提供' : tokenNumber.format(value)}</dd></div>)}</dl>
    <small>累计按各轮回执相加，包含重复发送的历史内容。</small>
  </details>;
}

export function ApiChat({v,chat}:any){
  const c=v.chats.find((row:any)=>row.id===v.activeChat),rows=c?.route==='api'?c.messages:[];
  const pending=!!c?.pending,bridge=window.WorkdeskWorkbench,api=chat.api;
  const name=api?.name?.replace(/ API$/, '')||'DeepSeek',deepseek=api?.provider!=='qwen';
  const display=chatDisplayNames(c?.config?.model||v.chatApiModel||name);
  const [draft,setDraft]=useState(v.chatInput||'');
  const [dragging,setDragging]=useState(false);
  const [reading,setReading]=useState(false);
  const attachmentsBusy = pending || v.busy || reading;
  useLayoutEffect(()=>setDragging(false),[v.activeChat]);
  async function attachFiles(files: File[]) {
    if (!files.length || attachmentsBusy) return;
    setReading(true);
    try { await bridge.chatFiles(files); }
    finally { setReading(false); }
  }
  function dragOver(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    const overComposer = (event.target as Element).closest('[data-chat-file-drop]') !== null;
    const allowed = overComposer && !attachmentsBusy;
    event.dataTransfer.dropEffect = allowed ? 'copy' : 'none';
    setDragging(allowed);
  }
  function drop(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    if ((event.target as Element).closest('[data-chat-file-drop]')) {
      void attachFiles(Array.from(event.dataTransfer.files));
    }
  }
  useLayoutEffect(()=>setDraft(v.chatInput||''),[v.chatInput,v.activeChat]);
  const files=v.chatFiles||[],limit=api?.context_window||1048576;
  const draftTokens=Array.from(draft as string).reduce((n:number,ch:string)=>n+(ch.codePointAt(0)!<128?.3:.6),0);
  const contextTokens=Math.ceil((c?.context?.tokens||0)+draftTokens+files.reduce((n:number,f:any)=>n+(f.tokens||0),0));
  const ratio=contextTokens/limit,warning=ratio>=.95?95:ratio>=.8?80:0;
  const messages:ThreadMessageLike[]=rows.map((m:any)=>({id:m.id,role:m.role==='human'?'user':'assistant',
    content:(m.content||'')+(m.attachments?.length?'\n\n附件：'+m.attachments.map((f:any)=>f.name).join('、'):'')}));
  const runtime=useExternalStoreRuntime({messages,isRunning:pending,convertMessage:(m:ThreadMessageLike)=>m,
    onNew:async m=>{try{await bridge.submitText(m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n'));}finally{queueMicrotask(()=>runtime.thread.composer.setText(bridge.inputText()));}},
    onCancel:async()=>{await bridge.perform('chat-api-stop');}});
  useLayoutEffect(()=>{if(runtime.thread.composer.getState().text!==v.chatInput)runtime.thread.composer.setText(v.chatInput);},[runtime,v.chatInput]);
  const replies=rows.filter((m:any)=>m.role==='assistant'),last=replies.at(-1),locked=rows.length>0;
  return <ApiDisplayNames.Provider value={display}><ApiMessages.Provider value={rows}><AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root className={'wd-chat wd-api '+(!rows.length?'is-empty':'')} data-chat-surface data-api-chat style={{'--chat-rgb':'var(--wd-chat-api-rgb,77,107,254)','--chat-opacity':'var(--wd-chat-api-opacity,.05)'} as any}>
    <ChatFrame record={c} records={v.chats} projectId={v.project} api filesDisabled={attachmentsBusy}>
    <div className="wd-chat-toolbar">
      <ChatRoutePicker route="api" busy={v.busy}/>
      <NativeSelect key={api?.provider} id="wb-api-provider" aria-label="API 大模型" defaultValue={api?.provider||'deepseek'} disabled={locked||v.busy}>
        {(chat.apiServices?.length?chat.apiServices:[{provider:'deepseek',name:'DeepSeek'}]).map((service:any)=><option key={service.provider} value={service.provider}>{service.name.replace(/ API$/, '')}</option>)}
      </NativeSelect>
      <NativeSelect key={v.chatApiModel} id="wb-api-model" aria-label={name+' API 模型'} defaultValue={v.chatApiModel} disabled={locked||v.busy}>{(api?.models||['deepseek-flash','deepseek-v4-pro']).map((m:string)=><option key={m} value={m}>{m}</option>)}</NativeSelect>
      <span className="wd-api-route-label" title="当前会话和待发内容的本地估算">上下文约 {tokenNumber.format(contextTokens)} / {tokenNumber.format(limit)} Token（{(ratio*100).toFixed(1)}%）</span>
      {locked&&<p className="wd-api-model-lock">本聊天的厂商与模型已固定。<button type="button" data-wb="new" disabled={attachmentsBusy}>新建聊天以更换模型</button></p>}
    </div>
    <ThreadPrimitive.Viewport className="wd-api-viewport" data-reading-viewport scrollToBottomOnRunStart={false} onDragOverCapture={dragOver} onDropCapture={drop}
      onDragLeave={event=>{if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);}}>
      {rows.length>0&&<div className="wd-api-thread"><ThreadPrimitive.Messages components={{UserMessage,AssistantMessage}}/>{last?.sources?.length>0&&<details className="wd-api-sources"><summary>本回合联网来源 · {last.sources.length}</summary>{last.sources.map((s:any,i:number)=><a key={i} href={s.url} target="_blank" rel="noopener noreferrer">{i+1}. {s.title||s.url}</a>)}</details>}{last?.search_state==='EMPTY'&&<p className="wd-api-status">本回合联网搜索没有返回结果。</p>}{last?.reasoning&&<details className="wd-api-reasoning"><summary>查看本回合思考过程</summary><div><Template html={window.WorkdeskLegacy.markdown(last.reasoning)}/></div></details>}</div>}
      <div className="wd-api-receipts"><ChatTokenUsage key={v.activeChat} replies={replies}/></div>
      <ConversationScroll conversationKey={'api:'+(c?.id||'new')}/>
    </ThreadPrimitive.Viewport>
    <div className="wd-api-compose-area" onDragOverCapture={dragOver} onDropCapture={drop}
      onDragLeave={event=>{if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);}}>
      <div className="wd-api-home">
        {!rows.length&&<h2>欢迎回来，随时开始吧</h2>}
        {warning>0&&<div className="wd-api-context-warning" role="alert" data-context-warning={warning}>上下文已达到{warning}%{warning===95?'，即将用满':'，请留意剩余空间'}。请保存或导出聊天记录，并切换新聊天；这里不会自动压缩或删减历史。<button type="button" data-wb="new">切换新聊天</button><button type="button" data-wb="export-md" disabled={!c}>导出记录</button></div>}
        <ComposerPrimitive.Root id="wb-chat-form" onSubmit={e=>e.stopPropagation()}>
          <div className="wd-api-composer" data-chat-file-drop data-dragging={dragging || undefined}>
            {dragging && <div className="wd-api-drop-hint">松开即可添加附件</div>}
            {reading && <small role="status">正在读取附件…</small>}
            <ComposerPrimitive.Input asChild submitMode="enter" addAttachmentOnPaste={false} cancelOnEscape={false} unstable_focusOnThreadSwitched={false}>
              <Textarea id="wb-chat-input" aria-label="Chat 消息" rows={2} maxLength={6000000} placeholder={'给 '+display.assistant+' 发送消息'} onInput={e=>setDraft(e.currentTarget.value)}/>
            </ComposerPrimitive.Input>
            {files.length>0&&<div className="wd-api-files">{files.map((f:any)=><span key={f.id}><Paperclip/>{f.name}<button type="button" data-wb="chat-api-remove-file" data-id={f.id} aria-label={'移除附件：'+f.name} disabled={attachmentsBusy}><X/></button></span>)}</div>}
            {deepseek&&v.chatSearch&&<div className="wd-api-search-query"><label htmlFor="wb-chat-search-query">搜索词（可选）</label><input id="wb-chat-search-query" maxLength={128} defaultValue={v.chatSearchQuery||''} placeholder="问题超过128字时，在这里填写关键词" onChange={e=>bridge.chatSearchQuery(e.target.value)} disabled={attachmentsBusy}/><small>发送时调用一次官方搜索，可能产生搜索费用。</small></div>}
            <input id="wb-chat-file-input" type="file" multiple hidden accept=".txt,.md,.csv,.json,.log,.py,.js,.ts,.tsx,.html,.css,.xml,.yaml,.yml,.sql,.ini,.toml,.rst,.pdf,.docx,.epub,.png,.jpg,.jpeg,.gif,.webp" onChange={e=>{const selected=Array.from(e.target.files||[]);e.target.value='';void attachFiles(selected);}}/>
            <div className="wd-api-composer-tools"><Button type="button" variant="ghost" className="wd-api-thinking" data-wb="chat-api-thinking" aria-pressed={v.chatThinking} disabled={attachmentsBusy}><Brain/>深度思考</Button>{deepseek&&<Button type="button" variant="ghost" className="wd-api-thinking" data-wb="chat-api-search" aria-pressed={v.chatSearch} disabled={attachmentsBusy}><Globe/>联网搜索</Button>}
              <Button type="button" variant="ghost" size="icon" data-wb="chat-api-upload" aria-label="上传聊天文件" title={'点击或拖入文本、PDF、DOCX、EPUB 文件。'+(deepseek?'DeepSeek Flash 也可读图片。':'')} disabled={attachmentsBusy}><Paperclip/></Button>
              {pending?<ComposerPrimitive.Cancel asChild><Button type="button" className="wd-api-send" aria-label="停止 API 回复"><Square/></Button></ComposerPrimitive.Cancel>:files.length&&!draft.trim()?<Button type="button" className="wd-api-send" aria-label="发送 Chat 消息" disabled={reading||v.busy||!api?.ready} onClick={()=>bridge.submitText('')}><ArrowUp/></Button>:<ComposerPrimitive.Send asChild><Button type="button" className="wd-api-send" aria-label="发送 Chat 消息" disabled={reading||v.busy||!api?.ready}><ArrowUp/></Button></ComposerPrimitive.Send>}
            </div>
          </div>
        </ComposerPrimitive.Root>
        <p className="wd-api-caption">{deepseek?'本chat由deepseek的API提供服务。TOKEN有价，请谨慎聊天':'本聊天通过 Qwen API 发送，按官方用量规则计费。'}</p>
        {api&&!api.ready&&<p className="wd-api-status">尚未配置有效的 {name} API 凭据。<a href="#connect">连接 API</a></p>}
      </div>
    </div>
    </ChatFrame>
  </ThreadPrimitive.Root></AssistantRuntimeProvider></ApiMessages.Provider></ApiDisplayNames.Provider>;
}
