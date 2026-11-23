// Observe native registry state; this plugin supplies no tools or agent loop.
export const name='workdesk-scene-observer';
export const inject=['agents','skills','tools'];
export function apply(ctx,config){
  ctx.on('agent/created',async ({agent})=>{
    if(agent.session.id!==process.env.WORKDESK_SESSION_ID)return;
    const catalog=await ctx.skills.list({cwd:agent.session.header?.cwd||process.cwd(),scope:agent});
    const selected=(config.skills||[]).map(name=>({name,present:catalog.some(s=>s.name===name)}));
    const tools=ctx.tools.schemas(agent).map(t=>t.name).filter(n=>(config.mcp||[]).some(m=>n.startsWith('mcp__'+m+'__')));
    const details={source:'NATIVE_SKILL_AND_TOOL_REGISTRIES',session_id:agent.session.id,
      skills:selected,tools,missing:selected.filter(s=>!s.present).map(s=>s.name)};
    const response=await fetch(process.env.WORKDESK_GATE_URL+'/native/scene',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+process.env.WORKDESK_GATE_TOKEN},body:JSON.stringify(details)});
    if(!response.ok)throw Error('Scene load receipt was not saved');
    if(details.missing.length)throw Error('Selected native Skills are unavailable: '+details.missing.join(', '));
  });
}
