/* Natural language management owns only its page and exact command receipts. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('management');
  const key='workdesk-management-pending';
  let pending='';try{pending=sessionStorage.getItem(key)||'';}catch{}
  let pollTimer=null;
  const v={data:null,busy:false,pending,error:'',sequence:0,draft:{kind:'scene',parser_provider:'',parser_model:'',target_provider:'',target_model:'',target_id:'',intent:'inspect',text:''}};
  v.onField=(field,value)=>{
    v.draft[field]=value;
    if(field==='kind'){v.draft.target_id='';v.draft.target_provider='';v.draft.target_model='';v.draft.intent=value==='skills'?'inspect':'change';}
    if(field==='parser_provider')v.draft.parser_model='';
    if(field==='target_provider'){v.draft.target_model='';v.draft.target_id='';}
    if(field==='target_model'){
      const matches=(v.data?.members||[]).filter(member=>member.provider===v.draft.target_provider&&member.model===value);
      v.draft.target_id=matches.length===1?matches[0].id:'';
    }
    paint();
  };
  let snapshot={...v,draft:{...v.draft}};
  const subscribers=new Set();
  const settings=()=>location.hash.split('/').slice(0,2).join('/')==='#settings/management';
  const active=()=>settings()||location.hash.split('/')[0]==='#management';
  const hostSelector=()=>settings()?'[data-ui-module="settings"]':'[data-ui-module="management"]';
  function paint(){
    if(!active())return;
    snapshot={...v,draft:{...v.draft}};
    if(settings()){for(const subscriber of subscribers)subscriber();return;}
    let focusRecord='';
    try{focusRecord=decodeURIComponent(location.hash.split('/')[1]||'');}catch{}
    window.WorkdeskReact.management($('#main'),{...snapshot,focusRecord});
  }
  function remember(value){v.pending=value;try{if(value)sessionStorage.setItem(key,value);else sessionStorage.removeItem(key);}catch{}}
  function stopPoll(){if(pollTimer!==null)clearTimeout(pollTimer);pollTimer=null;}
  function poll(){
    stopPoll();if(!active()||!v.data?.requests.some(row=>row.state==='PARSING'))return;
    pollTimer=setTimeout(async()=>{
      pollTimer=null;if(!active())return;
      if(v.busy){poll();return;}
      try{await load();}catch(error){if(active()){v.error=error.message;paint();poll();}}
    },2500);
  }
  async function load(){
    stopPoll();const sequence=++v.sequence;const data=await api('v1/management');
    if(sequence!==v.sequence||!active())return;
    const appearance=window.WorkdeskAppearance,revision=data.settings?.appearance?.revision;
    if(appearance&&Number.isInteger(revision)&&appearance.snapshot().saved?.revision!==revision)
      await appearance.load(); // Preserve the adapter's unsaved draft and conflict checks.
    if(sequence!==v.sequence||!active())return;
    v.data=data;paint();poll();
  }
  async function render(){try{await load();}catch(error){v.error=error.message;paint();poll();}}
  async function command(kind,payload){
    const identity=crypto.randomUUID();remember(identity);paint();
    try{const response=await api('v1/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:identity,kind,payload})});remember('');return response.receipt;}
    catch(error){const receipt=await api('v1/receipt?id='+encodeURIComponent(identity)).catch(()=>null);if(receipt?.state==='RECORDED'){remember('');return receipt.receipt;}if(error.status===409&&receipt?.state==='UNKNOWN_OR_NOT_COMMITTED'){remember('');throw error;}throw Error(error.message+'；未自动重发，请核对原命令。');}
  }
  document.addEventListener('submit',async event=>{
    if(!active()||event.target.id!=='management-request-form'||!event.target.closest(hostSelector()))return;
    event.preventDefault();if(v.busy||v.pending)return;v.error='';v.busy=true;
    try{
      const draft={...v.draft},scope={kind:draft.kind,intent:draft.kind==='skills'?draft.intent:'change'};
      const parser=v.data?.parser_models?.find(model=>model.provider===draft.parser_provider&&model.model===draft.parser_model&&model.available);
      if(!parser)throw Error('请选择当前可用的解析大模型和型号。');
      if(draft.kind==='settings'){if(!v.data.settings)throw Error('外观设置管理尚未接通。');scope.revision=v.data.settings.appearance.revision;}
      else if(draft.kind!=='development'){const target=(draft.kind==='member'?v.data.members:v.data.scenes).find(t=>t.id===draft.target_id);if(!target||target.revision==null)throw Error('请选择准确的生效目标。');scope.target_id=target.id;scope.revision=target.revision;}
      await command('management.submit',{text:draft.text,scope,parser_selection:structuredClone(parser.selection)});v.draft.text='';await load();
    }catch(error){v.error=error.message;}finally{v.busy=false;paint();poll();}
  });
  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-management]');if(!active()||!el?.closest(hostSelector())||v.busy)return;
    event.preventDefault();v.busy=true;v.error='';paint();
    try{
      const action=el.dataset.management;
      if(action==='receipt'){const receipt=await api('v1/receipt?id='+encodeURIComponent(v.pending));if(receipt.state!=='RECORDED')throw Error('原命令仍未确认提交；保持原身份，未重新发送。');remember('');await load();}
      else if(action==='refresh')await load();
      else if(action==='install'){
        if(v.pending)throw Error('先核对上一个操作的原命令回执。');
        const row=v.data.requests.find(r=>r.id===el.dataset.id),target=v.data.scenes.find(s=>s.id===row.scope.target_id);
        if(!target)throw Error('原来选择的场景已不存在，请重新选择。');
        await command('management.submit',{text:`安装公开 Skill ${el.dataset.skill}，使用提交版本 ${el.dataset.commit}，加载到所选场景。`,member_id:row.member_id,scope:{kind:'skills',intent:'install',target_id:target.id,revision:target.revision}});await load();
      }
      else if(action==='restore'){const row=v.data.requests.find(r=>r.id===el.dataset.id),target=v.data.members.find(member=>member.id===row.scope.target_id);v.draft={...v.draft,kind:row.scope.kind==='skills'?'scene':row.scope.kind,target_id:row.scope.target_id||'',target_provider:target?.provider||'',target_model:target?.model||'',intent:'change',text:`撤回管理请求 ${row.id} 的变化，恢复该请求生效前的配置。`};paint();$('#main').querySelector('textarea')?.focus();}
      else{if(v.pending)throw Error('先核对上一个操作的原命令回执。');await command('management.'+action,{id:el.dataset.id,revision:Number(el.dataset.revision)});await load();}
    }catch(error){v.error=error.message;}finally{v.busy=false;paint();poll();}
  });
  window.WorkdeskModules.onLeave('management',()=>{++v.sequence;stopPoll();});
  window.WorkdeskManagement={render,snapshot:()=>snapshot,subscribe(listener){
    subscribers.add(listener);
    return ()=>{subscribers.delete(listener);if(!active()){++v.sequence;stopPoll();}};
  }};
})();
