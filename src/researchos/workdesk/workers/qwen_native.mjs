// Official Qwen Code query loop. No Workdesk MCP replacement tools installed.
import readline from 'node:readline';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
const input=readline.createInterface({input:process.stdin});
const lines=input[Symbol.asyncIterator]();
const config=JSON.parse((await lines.next()).value);
const {query}=await import(pathToFileURL(config.sdk+'/dist/index.mjs').href);
const emit=(type,data)=>process.stdout.write(JSON.stringify({type,data})+'\n');
const waiting=new Map();
const deny={behavior:'deny',message:'No current approval or input; action not granted'};
// Match the official Query SDK's default without relying on its timeout to
// abort the callback signal. Keep the original timeout for bounded legacy Work.
const approvalTimeout=config.official?60_000:Math.max(1000,config.deadline*1000-Date.now());
function ask(name,args,{signal}){
  if(signal.aborted)return Promise.resolve(deny);
  const id=randomUUID(),isInput=name==='ask_user_question';
  const params={session_id:config.session_id,tool_name:name,input:args,
    ...(isInput?{questions:(args.questions||[]).map((q,i)=>({...q,id:String(i)}))}:{})};
  return new Promise(resolve=>{
    const deadline=performance.now()+approvalTimeout;
    const finish=value=>{
      waiting.delete(id);
      clearTimeout(timer);
      signal.removeEventListener('abort',onAbort);
      resolve(value);
    };
    const cancel=reason=>{
      finish(deny);
      emit('native_request_cancelled',{id,reason});
    };
    const onAbort=()=>cancel('sdk_abort');
    const expire=()=>cancel('approval_timeout');
    const timer=config.official?setTimeout(expire,approvalTimeout):undefined;
    waiting.set(id,{name,args,finish,deadline,expire});
    signal.addEventListener('abort',onAbort,{once:true});
    emit('native_request',{id,method:isInput?'sdk/tool/requestUserInput':'sdk/tool/requestApproval',params});
  });
}
const abortController=new AbortController();
let releasePrompt,allowPrompt=false,promptClosed=false,wakePrompt,postedPrompts=0,finishedPrompts=0;
const promptQueue=[];
const enqueue=text=>{promptQueue.push({type:'user',session_id:config.session_id,parent_tool_use_id:null,message:{role:'user',content:text}});if(wakePrompt){wakePrompt();wakePrompt=null;}};
enqueue(config.prompt);
const readyForPrompt=new Promise(resolve=>{releasePrompt=resolve;});
async function* prompts(){
  await readyForPrompt;
  if(!allowPrompt)return;
  while(!promptClosed){if(promptQueue.length){postedPrompts++;yield promptQueue.shift();}else await new Promise(resolve=>{wakePrompt=resolve;});}
}
const options={
  cwd:config.workspace,model:config.model,authType:'openai',sessionId:config.resume?undefined:config.session_id,
  resume:config.resume?config.session_id:undefined,permissionMode:'default',
  maxToolCalls:config.max_tools,excludeTools:config.excluded_tools,
  canUseTool:ask,allowedMcpServerNames:[],mcpServers:{},includePartialMessages:true,abortController,
  systemPrompt:{type:'preset',preset:'qwen_code',append:config.prompt},
  timeout:{canUseTool:approvalTimeout,controlRequest:5000},
  env:{OPENAI_API_KEY:config.token,OPENAI_BASE_URL:config.url+'/v1',OPENAI_MODEL:config.model}
};
if(config.official){
  for(const key of ['permissionMode','maxToolCalls','excludeTools','allowedMcpServerNames','mcpServers','systemPrompt'])delete options[key];
  options.timeout={canUseTool:approvalTimeout};
  options.env={OPENAI_API_KEY:config.token,OPENAI_BASE_URL:config.url,OPENAI_MODEL:config.model};
  if(config.permission_mode)options.permissionMode=config.permission_mode;
  if(config.scene_mcp&&Object.keys(config.scene_mcp).length)options.mcpServers=config.scene_mcp;
  Object.assign(options.env,config.scene_env||{});
}
const q=query({prompt:prompts(),options});
async function control(op){
  const p=op.params||{};let result;
  switch(op.method){
    case 'scene.check':result={commands:await q.supportedCommands(),servers:await q.mcpServerStatus()};break;
    case 'supportedCommands':result=await q.supportedCommands();break;
    case 'mcpServerStatus':result=await q.mcpServerStatus();break;
    case 'getAvailableModels':result=await q.getAvailableModels();break;
    case 'getContextUsage':result=await q.getContextUsage(!!p.showDetails);break;
    case 'getUsageInfo':result=await q.getUsageInfo(p.range||'today');break;
    case 'setModel':result=await q.setModel(p.model);break;
    case 'setEffortStatus':result=await q.setEffortStatus(p.effort);break;
    case 'setPermissionMode':
      if(!['default','plan','auto-edit','auto','yolo'].includes(p.mode))throw Error('Unsupported native permission mode');
      result=await q.setPermissionMode(p.mode);break;
    case 'streamInput':
      if(promptClosed)throw Error('Native input stream is closed');
      enqueue(p.text);result={queued_in_native_input:true,session_id:config.session_id,delivery:'PUBLIC_ASYNC_INPUT_STREAM; application observed in native user events'};break;
    case 'interrupt':result=await q.interrupt();break;
    default:throw Error('Unsupported native control operation');
  }
  return result??{acknowledged:true};
}
(async()=>{for await(const line of lines){try{
  const command=JSON.parse(line);
  if(command.action==='interrupt'){
    for(const entry of waiting.values())entry.finish(deny);
    await q.interrupt();emit('interrupt_receipt',{received:true,stop_confirmed:false});
  }else if(command.action==='native.control'){
    const op=command.operation;
    try{emit('native_control_result',{id:op.id,result:await control(op)});}
    catch(error){emit('native_control_result',{id:op.id,error:String(error)});}
  }else if(command.action==='native.reply'){
    const entry=waiting.get(command.id);if(!entry)continue;
    if(config.official&&performance.now()>=entry.deadline){entry.expire();continue;}
    let decision=deny;
    if(entry.name==='ask_user_question'&&command.response.answers){
      const answers=Object.fromEntries(Object.entries(command.response.answers).map(([key,v])=>[key,v.answers.join(', ')]));
      decision={behavior:'allow',updatedInput:{...entry.args,answers}};
    }else if(command.response.decision==='accept')decision={behavior:'allow',updatedInput:entry.args};
    entry.finish(decision);emit('native_reply_applied',{id:command.id});
  }
}catch(error){emit('control_error',{message:String(error).slice(0,500)});}}})();
try{
  if(config.operation){
    try{emit('native_control_result',{id:config.operation.id,result:await control(config.operation)});}
    catch(error){emit('native_control_result',{id:config.operation.id,error:String(error)});}
  }else{
  const directory=await q.getAvailableModels();
  if(!directory||typeof directory!=='object')throw Error('Native model directory acknowledgement missing');
  await q.setModel(config.model);
  if(config.official&&['low','medium','high','xhigh','max'].includes(config.effort)){
    const effortStatus=await q.setEffortStatus(config.effort);
    emit('native_effort_receipt',{session_id:config.session_id,requested:config.effort,status:effortStatus});
  }else if(config.official){
    emit('native_effort_receipt',{session_id:config.session_id,requested:config.effort,status:{applied:false,reason:'Installed Query SDK exposes effort tiers, not the off/thinking toggle. Native configuration is inherited.'}});
  }
  const observedSession=q.getSessionId();
  if(observedSession&&observedSession!==config.session_id)throw Error('Native session identity differs before prompt');
  emit('model_selection_receipt',{model:config.model,effort:config.effort||'off',directory_method:'getAvailableModels',
    switch_method:'setModel',interface_acknowledged:true,effect:'BEFORE_FIRST_PROMPT_OF_THIS_ATTEMPT',
    resume_requested:config.resume,session_id_observed_before_prompt:observedSession||null,history_attestation:'SEPARATE_FROM_INTERFACE_ACK'});
  allowPrompt=true;releasePrompt();
  for await(const message of q){
    emit('message',message);
    if(message.type==='system'&&message.subtype==='init'&&message.session_id===config.session_id&&
       message.parent_tool_use_id==null&&(config.scene_skills?.length||Object.keys(config.scene_mcp||{}).length)){
      // Some installed CLI builds expose Skills through slash_commands rather
      // than the newer optional skills field. Both are native init catalogs.
      const names=message.skills||message.slash_commands||[];
      const missing=(config.scene_skills||[]).filter(s=>!names.includes(s.name)).map(s=>s.name);
      const missingMcp=Object.keys(config.scene_mcp||{}).filter(name=>!(message.mcp_servers||[]).some(s=>s.name===name&&s.status==='connected'));
      emit('scene_capability_receipt',{source:'NATIVE_SYSTEM_INIT',session_id:message.session_id,
        skills:names,skill_catalog:message.skills?'SKILLS':'SLASH_COMMANDS',
        servers:message.mcp_servers||[],missing:[...missing,...missingMcp]});
      if(missing.length||missingMcp.length)throw Error('Selected scene capability is unavailable: '+[...missing,...missingMcp].join(', '));
    }
    if(message.type==='result'&&message.session_id===config.session_id&&message.parent_tool_use_id==null){
      finishedPrompts++;
      if(finishedPrompts>=postedPrompts&&!promptQueue.length){promptClosed=true;if(wakePrompt)wakePrompt();}
    }
  }
  emit('closed',{session_id:q.getSessionId()});
  }
}
catch(error){emit('error',{message:String(error).slice(0,1500)});process.exitCode=1;}
finally{
  promptClosed=true;if(wakePrompt)wakePrompt();
  releasePrompt();
  for(const entry of waiting.values())entry.finish(deny);
  try {
    await q.close();
    if(config.official&&!config.operation)emit('native_sdk_lifecycle',{phase:'CLOSE_RETURNED',session_id:q.getSessionId()});
  } catch(error) {
    emit('error',{message:String(error).slice(0,1500)});
    process.exitCode=1;
  } finally {
    input.close();
  }
}
