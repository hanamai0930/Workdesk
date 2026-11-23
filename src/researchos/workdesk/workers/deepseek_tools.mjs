import { randomUUID } from 'node:crypto';
export const name='workdesk-task-tools';
export const inject=['tools'];
export function apply(ctx){
  for(const def of JSON.parse(process.env.WORKDESK_TOOL_DEFINITIONS)){
    ctx.tools.register({name:def.name,description:def.description,parameters:def.schema,
      output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
      async execute(args,exec){
        const response=await fetch(process.env.WORKDESK_GATE_URL+'/tool',{
          method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.WORKDESK_GATE_TOKEN},
          body:JSON.stringify({name:def.name,arguments:args,call_id:exec.toolCallId||exec.callId||randomUUID()}),signal:exec.signal});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error||'Scoped tool refused');
        return JSON.stringify(result);
      }
    });
  }
}
