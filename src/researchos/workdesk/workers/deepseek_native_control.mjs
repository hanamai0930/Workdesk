// Workdesk presents native Cordis approval/input events; native tools stay intact.
import {randomUUID,createHash} from 'node:crypto';
export const name='workdesk-native-control';
export const inject=['agents'];
export function apply(ctx){
  const expected=process.env.WORKDESK_SESSION_ID,calls=new Map();
  const post=async(path,body,signal)=>{
    const response=await fetch(process.env.WORKDESK_GATE_URL+'/native/'+path,{
      method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.WORKDESK_GATE_TOKEN},
      body:JSON.stringify(body),signal});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Native control refused');return result;
  };
  const sessions=new Set([expected]),agents=new Map();let polling=false;
  const bound=agent=>sessions.has(agent?.session?.id);
  const nativeAgent=()=>agents.get(expected);
  const revision=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const pick=(value,keys)=>Object.fromEntries(keys.filter(key=>value[key]!==undefined).map(key=>[key,value[key]]));
  const bundleView=b=>pick(b,['name','version','availability','enabled','installed','optional','removable','readOnlyReason']);
  const pluginView=(entry,bundles)=>({
    ...pick(entry,['entryId','moduleName','enabled','fiberPhase','patchId','readOnlyReason']),
    bundles:bundles.filter(b=>b.rows?.some(row=>row.entryId===entry.entryId)).map(bundleView),
  });
  const checkedText=(value,label)=>{if(typeof value!=='string'||!value.trim()||/[\0\r\n]/.test(value))throw Error('请提供有效的'+label);return value.trim();};
  const execute=async op=>{
    const a=nativeAgent(),p=op.params||{};if(!a)throw Error('原生会话尚未就绪');
    const c=a.ctx||ctx;
    if(op.method.startsWith('plugins.')){
      const manager=c.get('pluginManager');if(!manager)throw Error('当前原生 profile 未提供插件管理服务');
      const bundles=await manager.listBundles();
      const envelope=data=>({data,session_id:expected,source:'NATIVE_PLUGIN_MANAGER',scope:'NATIVE_SDK_PROFILE'});
      if(op.method==='plugins.list')return envelope((await manager.listPlugins()).map(entry=>{
        const view=pluginView(entry,bundles);return {...view,selection_revision:revision(view)};
      }));
      if(op.method==='plugins.bundles')return envelope(bundles.map(bundle=>{
        const view=bundleView(bundle);return {...view,selection_revision:revision(view)};
      }));
      let result;
      if(op.method==='plugins.install'){
        const source=checkedText(p.source,'插件来源');
        if(/(?:https?|git\+https?):\/\/[^/\s]*@/i.test(source)||source.includes('://')&&source.includes('?'))throw Error('插件来源不能包含登录凭据或查询参数');
        result=await manager.installBundle(source,{enabled:true,saveExact:true,requestId:op.id});
      }else if(['plugins.enable','plugins.disable'].includes(op.method)){
        const entry=(await manager.listPlugins()).find(entry=>entry.entryId===p.entry_id);
        if(!entry||revision(pluginView(entry,bundles))!==p.selection_revision)throw Error('插件身份、版本或配置已变化，请重新查询');
        if(entry.readOnlyReason||entry.moduleName===import.meta.url||entry.moduleName.includes('deepseek_native_control.mjs'))throw Error('该运行插件由官方管理服务保护或属于当前交互桥，不能通过此入口修改');
        result=await manager.setPluginEnabled(entry.entryId,op.method==='plugins.enable');
      }else if(['plugins.bundle.enable','plugins.bundle.disable','plugins.remove'].includes(op.method)){
        const bundle=bundles.find(bundle=>bundle.name===p.name);
        if(!bundle||revision(bundleView(bundle))!==p.selection_revision)throw Error('插件包身份、版本或配置已变化，请重新查询');
        if(bundle.readOnlyReason)throw Error('该插件包由官方管理服务保护');
        if(op.method==='plugins.remove'){
          if(!bundle.removable)throw Error('此插件包不可移除');
          result=await manager.removeBundle(bundle.name);
        }else result=await manager.setBundleEnabled(bundle.name,op.method==='plugins.bundle.enable');
      }else throw Error('未登记的原生插件管理操作');
      // Keep identities and application state; configuration and package logs may contain credentials.
      return {session_id:expected,source:'NATIVE_PLUGIN_MANAGER',scope:'NATIVE_SDK_PROFILE',
        ...pick(p,['entry_id','name']),
        ...pick(result,['changed','application','stage','enabled','bundle','version','restartReason','pendingBuilds']),
        ...(result.error?{error:{code:result.error.code}}:{}),
        ...(result.packageResult?{packageResult:pick(result.packageResult,['exitCode','kind','timedOut'])}:{})};
    }
    switch(op.method){
      case 'permissions.get':if(!c.get('permissionPresets'))throw Error('当前安装未提供权限预设服务');return {catalog:c.get('permissionPresets').catalog(),current:c.get('permissionPresets').current(a.session)};
      case 'permissions.set':if(!c.get('permissionPresets'))throw Error('当前安装未提供权限预设服务');await c.get('permissionPresets').set(a.session,p.mode);return c.get('permissionPresets').current(a.session);
      case 'skills.list':{
        const skills=c.get('skills'),directory=c.get('workingDirectory');
        if(!skills||!directory)throw Error('当前安装未提供原生 Skill 目录服务');
        return {data:await skills.list({scope:a,cwd:await directory.ensure(a,new AbortController().signal)}),session_id:expected,source:'NATIVE_SKILL_REGISTRY'};
      }
      case 'jobs.list':{
        const jobs=c.get('jobs');if(!jobs)throw Error('当前安装未提供原生后台作业服务');
        return {data:jobs.list(a.id).filter(job=>job.owner===a.id),session_id:expected,source:'NATIVE_JOB_REGISTRY'};
      }
      case 'jobs.output':
      case 'jobs.stop':{
        const jobs=c.get('jobs');if(!jobs)throw Error('当前安装未提供原生后台作业服务');
        if(typeof p.job_id!=='string'||!p.job_id)throw Error('请选择当前会话的原生后台作业');
        const job=jobs.get(p.job_id,a.id);
        if(job.owner!==a.id)throw Error('该后台作业不属于当前会话');
        if(op.method==='jobs.output'){
          const offset=p.offset??0;
          if(!Number.isSafeInteger(offset)||offset<0)throw Error('输出偏移须为非负整数');
          return {job,output:jobs.readAt(p.job_id,offset,a.id),session_id:expected,source:'NATIVE_JOB_OBSERVER'};
        }
        const status=jobs.kill(p.job_id,a.id,'Human requested cancellation from Workdesk');
        return {status,cancellation_requested:status==='requested',job:jobs.get(p.job_id,a.id),session_id:expected,source:'NATIVE_JOB_REGISTRY'};
      }
      case 'agents.list':return [...agents.values()].map(x=>({id:x.session.id,status:x.status,parentSession:x.session.header?.parentSession}));
      case 'agents.message':{
        const target=agents.get(p.agent_id);if(!target||!p.text)throw Error('请选择本会话已有子代理和指令');
        target.steer({id:randomUUID(),role:'user',content:[{type:'text',text:p.text}],source:{kind:'user'}});return {accepted:true,agent_id:p.agent_id};
      }
      case 'agent.interrupt':a.cancel({kind:'user'});return {interrupt_requested:true};
      case 'commands.list':if(!c.get('commands'))throw Error('当前安装未提供原生命令服务');return c.get('commands').list(a);
      case 'command.execute':if(!c.get('commands'))throw Error('当前安装未提供原生命令服务');return await c.get('commands').execute(a,p.line,[],new AbortController().signal);
      default:throw Error('此安装未暴露该专用接口；请先查看原生命令，通过工作端已注册的命令管理。');
    }
  };
  const poll=async()=>{
    if(polling||!nativeAgent()||!process.env.WORKDESK_GATE_URL)return;
    polling=true;
    try{for(const op of (await post('controls',{})).operations||[]){
      try{await post('control-result',{id:op.id,result:await execute(op)});}
      catch(error){await post('control-result',{id:op.id,error:op.method.startsWith('plugins.')?'原生插件操作失败；请重新查询插件身份和官方管理状态。':String(error)});}
    }}catch{}finally{polling=false;}
  };
  ctx.on('agent/created',async ({agent})=>{
    const id=agent.session.id,parent=agent.session.header?.parentSession||agent.session.parentSession;
    if(id!==expected){if(!sessions.has(parent))return;await post('session',{session_id:id,parent_session_id:parent});sessions.add(id);}
    agents.set(id,agent);
    if(id===expected&&process.env.WORKDESK_OFFICIAL==='1'){
      const preset=process.env.WORKDESK_PERMISSION_PRESET,c=agent.ctx||ctx;
      if(preset){
        if(!c.get('permissionPresets'))throw Error('原生权限预设服务不可用；未发送任务');
        await c.get('permissionPresets').set(agent.session,preset);
        await post('configuration',{session_id:id,requested:preset,effective:c.get('permissionPresets').current(agent.session),source:'NATIVE_PERMISSION_PRESETS'});
      }
      if(process.env.WORKDESK_NATIVE_COMMAND_CATALOG==='1'){
        try{
          const registry=c.get('commands');
          if(registry){
            const commands=registry.list(agent).map(command=>command.name);
            await post('configuration',{session_id:id,source:'NATIVE_COMMAND_REGISTRY',commands},AbortSignal.timeout(2000));
          }
        }catch{
          // A missing observation cannot prevent the native turn.
        }
      }
    }
  });
  ctx.on('agent/disposed',({agent})=>agents.delete(agent.session.id));
  if(process.env.WORKDESK_OFFICIAL==='1'&&process.env.WORKDESK_GATE_URL){const timer=setInterval(poll,250);ctx.on('dispose',()=>clearInterval(timer));}
  ctx.on('tools/pre-execute',async function(exec,next){
    if(!bound(exec.agent))return process.env.WORKDESK_OFFICIAL==='1'?next():{kind:'deny',reason:'Outside current Workdesk session'};
    const data={id:exec.callId,session_id:exec.agent.session.id,tool_name:exec.name,input:exec.arguments};calls.set(exec.callId,data);
    try{
      const reserved=await post('tool',data,exec.signal);
      if(reserved.approval_required){
        const response=await ask('sdk/tool/requestApproval',data,exec.signal);
        if(response.decision!=='accept')return {kind:'deny',reason:'Current host command not approved'};
      }
      return next();
    }
    catch(error){return {kind:'deny',reason:String(error)};}
  });
  const ask=async(method,params,signal)=>{
    const id=randomUUID();
    let result;
    try{
      result=await post('interaction',{id,method,params:{...params,session_id:params.session_id||expected}},signal);
    }catch(error){
      // This callback is no longer able to accept a reply. Use a separate
      // signal so an expired native callback can still retire its exact record.
      try{await post('cancelled',{id,reason:signal?.aborted?'native_callback_aborted':'native_reply_unavailable'},AbortSignal.timeout(1500));}catch{}
      throw error;
    }
    await post('applied',{id});return result;
  };
  ctx.on('approval/request',async function(req,next){
    if(!bound(req.agent))return next();
    try{
      const data=calls.get(req.callId)||{session_id:req.agent.session.id,tool_name:req.toolName,input:{}};
      const result=await ask('sdk/tool/requestApproval',{...data,reason:req.reason,call_id:req.callId},req.signal);
      return result.decision==='accept'?'allowed-once':result.decision==='decline'?'rejected':'cancelled';
    }catch{return 'unavailable';}
  });
  ctx.on('user-questions/request',async function(req,next){
    if(!bound(req.agent))return next();
    const result=await ask('sdk/tool/requestUserInput',{questions:req.questions,session_id:req.agent.session.id},req.signal);
    if(result.cancelled)throw Error('Workdesk request cancelled; no answer');
    return {answers:req.questions.map(q=>{
      const values=result.answers[q.id].answers,labels=new Set((q.options||[]).map(o=>o.label));
      return {id:q.id,selected:values.filter(v=>labels.has(v)),...(values.some(v=>!labels.has(v))?{custom:values.filter(v=>!labels.has(v)).join(', ')}:{})};
    })};
  });
}
