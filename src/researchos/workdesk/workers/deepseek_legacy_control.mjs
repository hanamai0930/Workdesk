// Workdesk presents native Cordis approval/input events; native tools stay intact.
import {randomUUID} from 'node:crypto';
export const name='workdesk-native-control';
export const inject=['tools','approval','userQuestions'];
export function apply(ctx){
  const expected=process.env.WORKDESK_SESSION_ID,calls=new Map();
  const post=async(path,body,signal)=>{
    const response=await fetch(process.env.WORKDESK_GATE_URL+'/native/'+path,{
      method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.WORKDESK_GATE_TOKEN},
      body:JSON.stringify(body),signal});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Native control refused');return result;
  };
  const bound=agent=>agent?.session?.id===expected;
  ctx.on('tools/pre-execute',async function(exec,next){
    if(!bound(exec.agent))return process.env.WORKDESK_OFFICIAL==='1'?next():{kind:'deny',reason:'Outside current Workdesk session'};
    const data={id:exec.callId,session_id:expected,tool_name:exec.name,input:exec.arguments};calls.set(exec.callId,data);
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
    const result=await post('interaction',{id,method,params:{...params,session_id:expected}},signal);
    await post('applied',{id});return result;
  };
  ctx.on('approval/request',async function(req,next){
    if(!bound(req.agent))return next();
    try{
      const data=calls.get(req.callId)||{tool_name:req.toolName,input:{}};
      const result=await ask('sdk/tool/requestApproval',{...data,reason:req.reason,call_id:req.callId},req.signal);
      return result.decision==='accept'?'allowed-once':result.decision==='decline'?'rejected':'cancelled';
    }catch{return 'unavailable';}
  });
  ctx.on('user-questions/request',async function(req,next){
    if(!bound(req.agent))return next();
    const result=await ask('sdk/tool/requestUserInput',{questions:req.questions},req.signal);
    if(result.cancelled)throw Error('Workdesk request cancelled; no answer');
    return {answers:req.questions.map(q=>{
      const values=result.answers[q.id].answers,labels=new Set((q.options||[]).map(o=>o.label));
      return {id:q.id,selected:values.filter(v=>labels.has(v)),...(values.some(v=>!labels.has(v))?{custom:values.filter(v=>!labels.has(v)).join(', ')}:{})};
    })};
  });
}
