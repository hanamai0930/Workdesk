export type NativeStep={id:string,kind:string,label:string,status:string,text:string,input?:any,result?:any,session?:string,parent?:string};
export type NativeAgent={id:string,parent:string,name:string,status:string};
export type NativeTrace={steps:NativeStep[],agents:NativeAgent[],text:string,usage:any,active:boolean};
const running=new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_INPUT','WAITING_APPROVAL','STOP_REQUESTED']);
const stringify=(v:any)=>typeof v==='string'?v:JSON.stringify(v,null,2);
const parts=(v:any):string=>typeof v==='string'?v:Array.isArray(v)?v.map(x=>x.text||x.thinking||x.reasoning||parts(x.content)||'').filter(Boolean).join('\n'):'';

/** A projection of received native events. Never generates model commentary. */
export function nativeTrace(detail:any,attempt:any):NativeTrace {
  const steps=new Map<string,NativeStep>(),streamIds=new Map<string,string>(),agents=new Map<string,NativeAgent>(),usageMetadata:Record<string,unknown>={};
  let text='',serial=0,latestUsage:Record<string,unknown>={};
  const put=(id:string,kind:string,label:string,patch:any={})=>{
    const old=steps.get(id)||{id,kind,label,status:'running',text:''};
    const next={...old,...patch};if(label&&label!=='原生工具')next.label=label;steps.set(id,next);return next;
  };
  const tool=(id:string,name:string,input:any,status='running',result?:any,session?:string,parent?:string)=>put('tool:'+id,'tool',name||'原生工具',{...(input!==undefined?{input}:{}),status,...(result!==undefined?{result}:{}),session,parent});
  const message=(id:string,content:any,session?:string,parent?:string)=>{
    let visible='';
    for(const [index,p] of (Array.isArray(content)?content:[]).entries()){
      if(['thinking','reasoning'].includes(p.type))put('message:'+id+':'+index,'thinking','思考与分析',{text:p.thinking||p.reasoning||p.text||'',status:'completed',session,parent});
      else if(p.type==='text')visible+=p.text||'';
      else if(['tool_use','tool-call'].includes(p.type))tool(p.id||p.toolCallId||p.tool_call_id,p.name||p.toolName,p.input||p.arguments,'running',undefined,session,parent);
      else if(['tool_result','tool-result'].includes(p.type))tool(p.tool_use_id||p.toolCallId,'',undefined,p.is_error||p.isError?'failed':'completed',p.content,session,parent);
    }
    if(visible){if(!parent&&(!session||session===attempt.session_id))text=visible;put('message:'+id+':text','commentary',parent?'子代理进展':'进展说明',{text:visible,status:'completed',session,parent});}
  };
  for(const e of detail.events||[]){
    if(e.attempt_id!==attempt.id)continue;
    const p=e.payload||{},kind=e.kind;
    if(kind==='native_child_session'&&p.session_id&&p.parent_session_id)agents.set(p.session_id,{id:p.session_id,parent:p.parent_session_id,name:'原生子代理',status:'已建立'});
    if(kind==='native_event'){
      const m=p.method,d=p.params||{},item=d.item||{},id=item.id||d.itemId||d.turnId;
      const agent=agents.get(d.threadId);
      if(agent){
        if(m==='turn/started')agent.status='运行中';
        if(m==='turn/completed')agent.status=d.turn?.status==='completed'?'回合完成':d.turn?.status==='interrupted'?'已中断':d.turn?.status==='failed'?'失败':'状态待核对';
        if(m==='thread/status/changed')agent.status=typeof d.status==='string'?d.status:stringify(d.status);
      }
      if(m==='item/started'||m==='item/completed'){
        const status=m==='item/started'?'running':item.status==='failed'?'failed':'completed';
        if(item.type==='agentMessage')message(id,[{type:'text',text:item.text}],d.threadId);
        else if(item.type==='reasoning')put('reasoning:'+id,'thinking','思考摘要',{text:parts(item.summary)||parts(item.content)||steps.get('reasoning:'+id)?.text||'',status,session:d.threadId});
        else if(item.type==='plan')put('plan:'+id,'plan','执行计划',{text:item.text||parts(item.content),status});
        else if(!['userMessage','contextCompaction'].includes(item.type))tool(id,item.type,item.command||item.arguments||item.changes||item,status,item.aggregatedOutput||item.result||item.error,d.threadId,item.parentThreadId);
      }else if(m?.includes('reasoning/')&&m.endsWith('Delta')){
        const key='reasoning:'+id,old=steps.get(key);put(key,'thinking','思考摘要',{text:(old?.text||'')+(d.delta||''),session:d.threadId});
      }else if(m==='item/commandExecution/outputDelta'){
        const old=steps.get('tool:'+id);tool(id,old?.label||'执行命令',old?.input,'running',(typeof old?.result==='string'?old.result:'')+(d.delta||''),d.threadId);
      }else if(m==='turn/plan/updated')put('plan:'+id,'plan','执行计划',{text:stringify(d.plan),status:'running'});
      else if(m==='error')put('error:'+e.seq,'error',d.willRetry?'正在重新连接':'运行错误',{text:d.error?.message||stringify(d.error),status:d.willRetry?'running':'failed'});
      else if(m==='thread/status/changed')put('status:'+e.seq,'status','工作端状态',{text:stringify(d.status),status:'completed'});
    }else if(kind==='native_tool_started'){
      const item=p.item||{},id=item.id||p.call_id;
      tool(id,item.tool_name||item.name||item.type||'原生工具',item.input||item.arguments||item.command||item,'running');
    }else if(kind==='native_token_usage_observation')latestUsage=p;
    else if(kind==='native_configuration_receipt')put('configuration:'+e.seq,'status','官方配置回执',{text:stringify(p),status:'completed'});
    else if(kind==='native_effort_receipt')put('effort:'+e.seq,'status','原生思考设置回执',{text:stringify(p),status:p.status?.applied===false?'unapplied':'completed'});
    else if(kind==='model_event'){
      if(p.method==='session.event'){
        const payload=p.payload||{},ev=payload.event||{},d=ev.data||{},session=payload.sessionId;
        if(ev.type==='assistant/message'){
          message(d.message?.id||String(e.seq),d.message?.content,session,session!==attempt.session_id?'native-child':undefined);
          if(d.usage&&session===attempt.session_id)latestUsage=d.usage;
          else if(d.usage&&session)usageMetadata['child:'+session]=d.usage;
        }else if(ev.type==='tool/call'){
          const call=d.call||d;tool(call.id||call.toolCallId||call.callId,call.name||call.toolName,call.arguments||call.input,'running',undefined,session);
        }else if(ev.type==='tool/result'){
          const msg=d.message||{};if(msg.toolCallId)tool(msg.toolCallId,'',undefined,msg.isError?'failed':'completed',msg.content,session);
          for(const part of d.message?.content||[])if(part.type==='tool-result')tool(part.toolCallId,'',undefined,part.isError?'failed':'completed',part.content,session);
        }else if(ev.type==='request/context'&&session===attempt.session_id)usageMetadata.context=d;
        else if(/goal|workflow|subagent|permission|sandbox|approval|job|background/.test(ev.type||''))put('native:'+e.seq,'status',ev.type,{text:stringify(d),status:'completed',session});
        else if(ev.type==='assistant/chunk'){
          const id='deepseek-stream:'+session+':'+(d.messageId||d.step||0),delta=d.delta||d.chunk||{};
          if(delta.type?.includes('reason')){const old=steps.get(id);put(id,'thinking','思考与分析',{text:(old?.text||'')+(delta.text||delta.reasoning||''),session});}
        }
      }else if(p.method?.startsWith('subagent.')){
        const child=p.payload?.childSessionId,parent=p.payload?.parentSessionId;
        if(child&&parent)agents.set(child,{id:child,parent,name:'原生子代理',status:p.method.endsWith('finished')?'回合结束':'运行中'});
        put('agent:'+child,'agent','原生子代理',{text:stringify(p.payload),status:p.method.endsWith('finished')?'completed':'running',session:child,parent});
      }
      else if(p.type==='stream_event'){
        const ev=p.event||{},route=(p.session_id||'root')+':'+(p.parent_tool_use_id||''),index=ev.index||0;
        if(ev.type==='message_start')streamIds.set(route,ev.message?.id||String(++serial));
        const mid=streamIds.get(route)||route,key='message:'+mid+':'+index;
        if(ev.type==='content_block_start'){
          const b=ev.content_block||{};
          if(b.type==='thinking')put(key,'thinking','思考与分析',{text:b.thinking||'',session:p.session_id,parent:p.parent_tool_use_id});
          else if(b.type==='tool_use')tool(b.id,b.name,b.input,'running',undefined,p.session_id,p.parent_tool_use_id);
        }else if(ev.type==='content_block_delta'){
          const delta=ev.delta||{};
          if(delta.type==='thinking_delta'){const old=steps.get(key);put(key,'thinking','思考与分析',{text:(old?.text||'')+(delta.thinking||''),session:p.session_id,parent:p.parent_tool_use_id});}
          else if(delta.type==='text_delta'){const k='message:'+mid+':text',old=steps.get(k),value=(old?.text||'')+(delta.text||'');if(!p.parent_tool_use_id)text=value;put(k,'commentary','进展说明',{text:value,session:p.session_id,parent:p.parent_tool_use_id});}
        }else if(ev.type==='content_block_stop'){const old=steps.get(key);if(old)old.status='completed';}
        else if(ev.type==='goal_state')put('goal:'+route,'plan','原生目标',{text:stringify(ev.goal_state),status:'running'});
      }else if(p.type==='assistant'){
        if(p.parent_tool_use_id){const id=p.session_id&&p.session_id!==attempt.session_id?p.session_id:'tool:'+p.parent_tool_use_id;agents.set(id,{id,parent:attempt.session_id,name:'子代理 · '+p.parent_tool_use_id,status:'已有过程事件'});}
        message(p.message?.id||p.uuid||String(e.seq),p.message?.content,p.session_id,p.parent_tool_use_id);
      }
      else if(p.type==='user')message(p.message?.id||p.uuid||String(e.seq),(p.message?.content||[]).filter((x:any)=>['tool_result','tool-result'].includes(x.type)),p.session_id,p.parent_tool_use_id);
      else if(p.type==='result'&&!p.parent_tool_use_id){
        if(p.usage)latestUsage=p.usage;
        if(p.result)text=String(p.result);
        if(p.total_cost_usd!==undefined)usageMetadata.total_cost_usd=p.total_cost_usd;
      }
      else if(p.type==='system'&&p.subtype!=='init')put('system:'+e.seq,'status',p.subtype||'工作端状态',{text:stringify(p),status:'completed'});
    }
  }
  // Explicit tool observations and native protocol events may refer to one id.
  // Both are keyed by native call identity, not by display position.
  const answer=attempt.result?.answer||text;
  for(const step of steps.values())if(step.session&&agents.has(step.session)){step.parent=step.parent||agents.get(step.session)!.parent;if(step.kind==='commentary')step.label='子代理进展';}
  return {steps:[...steps.values()].filter(s=>s.kind!=='commentary'||s.text!==answer),agents:[...agents.values()],text:typeof answer==='string'?answer:stringify(answer),usage:{...latestUsage,...usageMetadata},active:running.has(attempt.state)};
}
