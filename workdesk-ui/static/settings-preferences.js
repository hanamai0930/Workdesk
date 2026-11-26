/* Desktop preferences are local to this Workdesk installation. */
(() => {
  'use strict';
  const endpoint='preferences';
  const defaults={launch:'chat',close:'keyboard',opener:'system',userNickname:'',assistantNickname:'',workDirectory:'',lastLocation:'chat',chatDefaults:null,workProvider:'codex',
    notifications:{approval:true,failure:true,review:true},connectionNoticeSeconds:10,workAccess:{codex:'workspace',deepseek:'workspace',qwen:'workspace',claude:'workspace',grok:'workspace',gemini:'workspace',kimi:'workspace',glm:'workspace'}};
  const launchRoute=/^(?:chat|work|work-items|resources|projects|records|results|files|monitor|control|operations|planning|libraries|extensions|management|collab|drafts|archives|schedules)(?:\/[A-Za-z0-9._:%-]+){0,5}$/;
  const failed=new Set(['FAILED','FAILED_NO_SEND']);
  let current=structuredClone(defaults),queue=Promise.resolve(),scheduled,ready=false,lastViewed='';
  function syncWindow(){
    if(window.WorkdeskDesktop?.windowPreferences)
      window.chrome.webview.postMessage({channel:'workdesk.window',action:'configure',close:current.close});
  }
  function save(key,value){
    const result=queue.then(async()=>{
      if(current.recovery)throw Error('请先在设置中恢复偏好。');
      current=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value})});
      if(key!=='lastLocation'){
        syncWindow();
        window.dispatchEvent(new CustomEvent('workdesk:preferences',{detail:structuredClone(current)}));
      }
      return structuredClone(current);
    });
    queue=result.catch(()=>{});
    return result;
  }
  function reload(){
    const result=queue.then(async()=>{
      current=await api(endpoint);
      ready=true;
      syncWindow();
      window.dispatchEvent(new CustomEvent('workdesk:preferences',{detail:structuredClone(current)}));
      return structuredClone(current);
    });
    queue=result.catch(()=>{});
    return result;
  }
  async function recover(source){
    const state=current.recovery;
    if(!state)throw Error('请刷新设置后重新核对。');
    current=await api('preferences/recover',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({source,revision:state.revision,backupRevision:state.backup?.revision||null})});
    ready=true;
    syncWindow();
    window.dispatchEvent(new CustomEvent('workdesk:preferences',{detail:structuredClone(current)}));
    return structuredClone(current);
  }
  async function start(){
    try{current=await api(endpoint);ready=true;}catch{/* Chat remains accessible if local preferences cannot be read. */}
    syncWindow();
    if(location.hash)return;
    const route=current.launch==='last'?current.lastLocation:current.launch;
    if(route!=='chat'&&launchRoute.test(route))history.replaceState(null,'','#'+route);
  }
  function remember(route){
    if(!launchRoute.test(route)||route.length>280)return;
    lastViewed=route;
    if(!ready||current.recovery)return;
    clearTimeout(scheduled);
    if(route===current.lastLocation)return;
    scheduled=setTimeout(async()=>{
      try {await save('lastLocation',route);}catch{/* A page visit does not need a settings write to succeed. */}
    },250);
  }
  function closeFromShortcut(event){
    if(window.WorkdeskDesktop?.windowPreferences&&event.ctrlKey&&!event.shiftKey&&!event.altKey&&event.key.toLowerCase()==='w'){
      event.preventDefault();
      window.chrome.webview.postMessage({channel:'workdesk.window',action:'shortcut-close'});
    }
  }
  async function getAttention(){
    const flags=current.notifications;
    const [runtime,taskData]=await Promise.all([
      flags.approval||flags.failure?api('v1/monitor?scope='+(flags.failure?'all':'attention')):null,
      flags.review?api('v1/work-items/acceptance-notices'):null
    ]);
    const items=[];
    for(const run of [...runtime?.runs||[],...runtime?.partner_runs||[]]){
      const id=run.partner_run_id||run.run_id||run.id;
      const href=run.partner_href||((run.kind==='partner_run'||run.partner_run_id)?run.href||'#work/partners/run/'+encodeURIComponent(id):'#control/task/'+encodeURIComponent(run.task_id));
      if(flags.approval&&(run.pending_approvals>0||run.pending_inputs>0||['WAITING_APPROVAL','WAITING_INPUT'].includes(run.state)))items.push({kind:'approval',title:run.title,href,at:run.started||run.created});
      else if(flags.approval&&['UNKNOWN','SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN'].includes(run.state))items.push({kind:'approval',title:run.title+' · 状态待核对',href,at:run.started||run.created});
      if(flags.failure&&failed.has(run.state))items.push({kind:'failure',title:run.title,href,at:run.started});
    }
    for(const chat of runtime?.api_chats||[]){
      if(flags.failure&&failed.has(chat.state))items.push({kind:'failure',title:chat.title,href:'#chat/'+encodeURIComponent(chat.chat_id),at:chat.created});
    }
    for(const item of taskData?.work_items||[]){
      if(item.state==='READY_FOR_ACCEPTANCE')items.push({kind:'review',title:item.title,href:'#work-items/item/'+encodeURIComponent(item.id)+'/delivery',at:item.updated});
    }
    return {items:items.sort((a,b)=>(b.at||0)-(a.at||0)).slice(0,24)};
  }
  document.addEventListener('keydown',closeFromShortcut);
  window.WorkdeskSettingsPreferences=Object.freeze({get:()=>structuredClone(current),lastPage:()=>lastViewed||current.lastLocation,save,recover,reload,start,remember,getAttention});
})();
