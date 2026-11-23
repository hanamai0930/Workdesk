/* Concrete UI actions backed by durable, idempotent service commands. */
window.WorkdeskFeatures = function(B) {
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,call,command,modal,b,h,draw,go,renderSidebar,load}=B;
  let data={chats:[],drafts:[],schedules:[]},loaded=false,currentKey=null,saveTimer,saveChain=Promise.resolve(),side=null;
  let fileSession=null,fileUploads=Promise.resolve(),moveSession=null;
  const chat=()=>v.chats.find(c=>c.id===v.activeChat);
  const target=()=>v.activeChat?{kind:'chat',id:v.activeChat}:v.dialogue?{kind:'dialogue',id:v.dialogue.id}:v.detail?{kind:'task',id:v.detail.task.id}:null;
  const activeStates=new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
  const chatModule=window.WorkdeskChat({...B,refresh,persist:queueSave,captured,chatMessages,stopTask,saveDraftNow:()=>autosave(true),
    services:()=>data.chat_services?.services||[],
    apiServices:()=>data.chat_services?.api_services||[data.chat_services?.api].filter(Boolean),
    adopt:id=>{currentKey='composer:chat:'+id;}});
  const draftFields=['input','chatInput','scene','type','provider','model','workAccess','workEffort','workTier','workPlanOnly','project','files','attachments','roles','formDraft','chatProvider','chatModel','chatApiProvider','chatApiModel','chatThinking','chatSearch','chatSearchQuery','chatFiles'];
  const readDraft=()=>Object.fromEntries(draftFields.map(k=>[k,structuredClone(v[k])]));
  const localKey=key=>'composer-backup:'+key;
  async function refresh(){
    if(!loaded){
      const oldChats=getLocal('web-chats',[]),oldDrafts=getLocal('drafts',[]);
      if(oldChats.length||oldDrafts.length)await command('ui.import',{chats:oldChats,drafts:oldDrafts});
    }
    const next=await call('ui-state');
    // A GET begun before this window's draft save may return afterward. Keep
    // the newer acknowledged revision, without overriding another window's.
    next.drafts=next.drafts.map(d=>{const local=data.drafts.find(old=>old.id===d.id);return local?.revision>d.revision?local:d;});
    for(const d of data.drafts)if(!next.drafts.some(row=>row.id===d.id)&&d.id===currentKey)next.drafts.push(d);
    data=next;v.chats=data.chats;loaded=true;
  }
  function captured(){
    if(!currentKey)return null;
    const mode=currentKey.startsWith('composer:chat')?'chat':'work';
    const value={...readDraft(),mode,...(mode==='chat'?{input:''}:{chatInput:''})};
    return {key:currentKey,value};
  }
  async function persist(item){
    if(!item)return;
    const old=data.drafts.find(d=>d.id===item.key);
    if(JSON.stringify(old?.value)===JSON.stringify(item.value))return;
    const reply=await call('command',{id:crypto.randomUUID(),kind:'draft.save',payload:{id:item.key,value:item.value,revision:old?.revision||0}});
    const row=reply.receipt.draft;
    data.drafts=[row,...data.drafts.filter(d=>d.id!==row.id)];
    setLocal(localKey(item.key),null);
  }
  function autosave(immediate=false){
    const item=captured();if(!item)return;
    setLocal(localKey(item.key),item.value);clearTimeout(saveTimer);
    if(immediate===true)return queueSave(item);
    saveTimer=setTimeout(()=>queueSave(item),400);
  }
  function queueSave(item){const pending=saveChain.then(()=>persist(item));saveChain=pending.catch(error=>toast('草稿尚未同步：'+error.message));return pending;}
  async function beforeRender(){clearTimeout(saveTimer);const item=captured();currentKey=null;await queueSave(item);await refresh();}
  async function suspendChatDrafts(ids){
    clearTimeout(saveTimer);await queueSave(captured());const key=currentKey;
    if(key&&ids.includes(key.replace(/^composer:chat:/,'')))currentKey=null;
    return ()=>{if(!currentKey&&key==='composer:chat:'+v.activeChat)currentKey=key;};
  }
  async function suspendWorkDrafts(ids){
    clearTimeout(saveTimer);await queueSave(captured());const key=currentKey;
    if(key&&ids.includes(key.replace(/^composer:work:/,'')))currentKey=null;
    return ()=>{if(!currentKey&&key==='composer:work:'+(v.dialogue?.id||v.detail?.task.id))currentKey=key;};
  }
  function restore(mode,id){
    currentKey='composer:'+mode+':'+(id||'home');
    const saved=data.drafts.find(d=>d.id===currentKey)?.value;
    const backup=getLocal(localKey(currentKey),null),value=backup||saved;
    v.input='';v.chatInput='';v.chatFiles=[];v.chatSearchQuery='';
    if(mode==='work')v.workPlanOnly=value?.workPlanOnly??!!v.detail?.task.spec.plan_only;
    if(value){
      if(!id){for(const k of draftFields)if(value[k]!==undefined)v[k]=structuredClone(value[k]);}
      else {v.input=value.input||'';v.chatInput=value.chatInput||'';v.chatFiles=value.chatFiles||[];v.chatSearch=!!value.chatSearch;v.chatSearchQuery=value.chatSearchQuery||'';if(value.chatThinking!==undefined)v.chatThinking=value.chatThinking;}
    }
    if(backup)autosave();
    return !!value;
  }
  async function reset(){
    clearTimeout(saveTimer);
    await queueSave(captured());
    const input=v.mode==='chat'?v.chatInput:v.input;
    if(input.trim()){
      const row=(await command('draft.save',{id:'saved-'+crypto.randomUUID(),value:captured()?.value||{...readDraft(),mode:v.mode}})).draft;
      data.drafts.unshift(row);
    }
    currentKey='composer:'+v.mode+':home';v.input='';v.chatInput='';
    if(v.mode==='work'){v.files='';v.attachments=[];v.formDraft={};v.workPlanOnly=false;v.workAccess=window.WorkdeskSettingsPreferences?.get().workAccess[v.provider]||'workspace';}
    await queueSave(captured());
  }
  function special(){
    const root=location.hash.slice(1).split('/')[0];
    if(location.hash==='#chat/records'){
      currentKey=null;$('#main').innerHTML=`<section class="wk-content"><h2>聊天记录</h2>${v.chats.filter(c=>!c.archived&&!c.list_project).map(c=>`<a class="wk-list-row" href="#chat/${c.id}"><span>${h(c.title)}<small>${h(B.names[c.provider])} · ${c.route==='web'?'官方网页入口':'工作端文本对话'}</small></span></a>`).join('')||'<p class="wk-note">暂无聊天记录</p>'}</section>`;renderSidebar();return true;
    }
    if(root==='drafts'){
      currentKey=null;
      const drafts=data.drafts.filter(d=>d.value.input?.trim()||d.value.chatInput?.trim());
      $('#main').innerHTML=`<section class="wk-content">${window.WorkdeskSupport.subnav("drafts")}<div class="wk-page-title"><h2>草稿箱</h2>${b('新工作','new')}</div><p class="wk-note">草稿与配置保存在本地资料库。恢复只填入输入区，确认开始后才发送。</p>${drafts.map(d=>`<article class="wb-feature-card"><strong>${h(d.value.title||d.value.input?.slice(0,60)||d.value.chatInput?.slice(0,60))}</strong><small>${h(new Date(d.updated*1000).toLocaleString())}</small><pre>${h(d.value.input||d.value.chatInput||'')}</pre><div class="wb-feature-actions">${b('恢复草稿','restore-draft',`data-id="${h(d.id)}"`,true)}${b('复制','copy-draft',`data-id="${h(d.id)}"`)}${b('删除','delete-draft',`data-id="${h(d.id)}"`)}</div></article>`).join('')||'<p class="wk-note">暂无草稿</p>'}</section>`;
      renderSidebar();return true;
    }
    if(root==='archives'){
      currentKey=null;
      const work=B.records().filter(r=>r.archived),chats=v.chats.filter(c=>c.archived&&!c.list_project);
      $('#main').innerHTML=`<section class="wk-content"><div class="wk-page-title"><h2>已归档</h2></div>${[...chats.map(c=>({...c,kind:'chat'})),...work].map(r=>`<article class="wb-feature-card"><a href="${r.kind==='chat'?'#chat/'+r.id:'#work/'+r.kind+'/'+r.id}">${h(r.title)}</a><div>${b('取消归档','unarchive-record',`data-id="${h(r.id)}" data-kind="${r.kind}"`)}</div></article>`).join('')||'<p class="wk-note">暂无已归档记录</p>'}</section>`;renderSidebar();return true;
    }
    if(root==='schedules'){currentKey=null;$('#main').innerHTML=`<section class="wk-content"><h2>计划任务</h2><p class="wk-note">控制服务运行时按指定时间派发。服务重启后暂停，错过时间不补发；沿用原任务或聊天的累计预算。</p>${scheduleList()}</section>`;renderSidebar();return true;}
    return false;
  }
  async function applyDraft(id){
    const d=data.drafts.find(d=>d.id===id);if(!d)throw Error('草稿不存在。');
    if(d.value.mode==='chat'){
      v.chatProvider=d.value.chatProvider||d.value.provider||'codex';v.chatInput=d.value.chatInput||d.value.input||'';
    }else{
      for(const k of draftFields)if(d.value[k]!==undefined)v[k]=structuredClone(d.value[k]);
      if(!v.model)v.model=v.data?.profiles.find(p=>p.id===v.provider)?.model||'';
    }
    currentKey='composer:'+d.value.mode+':home';await queueSave(captured());
  }
  function chatMessages(c){
    const prefs=window.WorkdeskSettingsPreferences?.get(),user=prefs?.userNickname||'User',assistant=prefs?.assistantNickname||c.config?.model||B.names[c.provider];
    return c.messages.map(m=>m.role==='human'?`<div class="wk-user"><span class="wk-byline">${h(user)}</span><p>${h(m.content)}</p></div>`:
      `${m.content?B.answer(m.content,assistant):`<p class="wk-note">${activeStates.has(m.state)?'正在回复…':h(m.error||'本回合没有回复正文。')}</p>`}${m.content&&m.error?`<p class="wk-note">${h(m.error)}</p>`:''}`).join('');
  }
  function chatPage(){return chatModule.page();}
  function submitChat(){return chatModule.prepare();}
  function pollChat(){return chatModule.poll();}
  function leave(){moveSession=null;closeSide();chatModule.leave();}
  async function selectedTranscript(t=target()){if(!t)throw Error('先选择一段对话。');return call('conversation?kind='+t.kind+'&id='+encodeURIComponent(t.id));}
  function activeRecord(){
    const ref=target();if(!ref)throw Error('先选择一段对话。');
    const row=ref.kind==='chat'?chat():B.records().find(r=>r.kind===ref.kind&&r.id===ref.id);
    if(!row)throw Error('记录已变化，请重新打开对话。');
    return {...ref,revision:row.revision,project:(ref.kind==='chat'?row.list_project:row.project)||null};
  }
  async function moveProject(remove){
    const record=activeRecord(),route=location.hash,generation=v.generation;
    const {projects}=await call('projects');
    if(route!==location.hash||generation!==v.generation)return;
    if(remove&&!record.project)throw Error('这段对话已移出项目。');
    const available=projects.filter(project=>project.id!==record.project),title=remove?'移出项目':'移动到项目';
    if(!remove&&!available.length){
      modal(title,'<p class="wk-note">暂无其他可选项目。</p>','<a href="#projects" class="wk-button" data-wb-close-link>管理项目</a>'+b('关闭','close'));
      return;
    }
    moveSession={record,remove,route,generation};
    const field=remove?'<p class="wk-note">这段对话会回到未归入项目的记录列表。</p>':
      `<label class="wk-field">目标项目<select name="project" class="wk-input" required><option value="">选择项目</option>${available.map(project=>`<option value="${h(project.id)}">${h(project.name)}</option>`).join('')}</select></label>`;
    modal(title,`<form id="wb-move-conversation-form">${field}<button type="submit" class="wk-button primary">${title}</button></form>`,b('取消','close'));
  }
  async function saveProjectMove(form){
    const session=moveSession;if(!session||location.hash!==session.route||v.generation!==session.generation)throw Error('当前对话已变化，请重新选择。');
    const {record,remove,route,generation}=session,project=remove?null:new FormData(form).get('project');
    if(!remove&&!project)throw Error('请选择目标项目。');
    if(record.kind==='chat')await command('chat.move',{id:record.id,revision:record.revision,from_project:record.project,project_id:project});
    else await command('work.records-organize',{records:[{kind:record.kind,id:record.id,revision:record.revision}],changes:{project}});
    if(route!==location.hash||generation!==v.generation)return;
    $('#wb-dialog').close();moveSession=null;
    await load({generation});
    if(route===location.hash&&generation===v.generation){renderSidebar();toast(remove?'已移出项目。':'已移动到项目。');}
  }
  async function search(){await go('#records');window.WorkdeskRecordsPage?.focusSearch?.();}
  function searchResults(q){return (v.searchRows||[]).filter(r=>(r.title+'\n'+r.text).toLocaleLowerCase().includes(q.toLocaleLowerCase())).map(r=>`<a class="wk-list-row" href="${h(r.href)}" data-wb-close-link><span>${h(r.title)}<small>${h(r.text.replace(/\s+/g,' ').slice(0,110))}</small></span></a>`).join('')||'<p class="wk-note">没有匹配记录</p>';}
  async function saveChat(changes){await command('chat.record',{id:v.activeChat,...changes});await refresh();renderSidebar();}
  function scheduleList(){return data.schedules.map(s=>`<article class="wb-feature-card"><strong>${h(s.title)}</strong><p>${h(s.content)}</p><small>${h(new Date(s.due*1000).toLocaleString())} · ${h({ACTIVE:'已启用',PAUSED:'已暂停',COMPLETE:'已派发',CANCELLED:'已取消'}[s.state])} · 已派发 ${s.runs.length} 次</small>${s.reason?`<p class="wk-error">${h(s.reason)}</p>`:''}<div class="wb-feature-actions">${!['COMPLETE','CANCELLED'].includes(s.state)?`${b(s.state==='ACTIVE'?'暂停':'继续','schedule-state',`data-id="${s.id}" data-state="${s.state==='ACTIVE'?'PAUSED':'ACTIVE'}"`)}${b('取消计划','schedule-state',`data-id="${s.id}" data-state="CANCELLED"`)}`:''}</div></article>`).join('')||'<p class="wk-note">暂无计划任务</p>';}
  function scheduleDialog(){
    if(!target())throw Error('先选择要定时接续的对话。');
    const date=new Date(Date.now()+3600000),value=new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
    const official=v.detail?.task.spec.runtime_mode==='official';
    modal('计划任务',`<form id="wb-schedule-form"><label class="wk-field">执行时间<input class="wk-input" name="due" type="datetime-local" value="${value}" required></label><label class="wk-field">届时发送的消息<textarea class="wk-input" name="content" maxlength="4000" required>${h(v.input||v.chatInput||'')}</textarea></label><label class="wk-field">重复<select name="interval" class="wk-input"><option value="0">只执行一次</option><option value="3600">每小时</option></select></label><label class="wk-field">最多派发次数<input class="wk-input" type="number" name="max_runs" min="1" max="10" value="1"></label><p class="wk-note">${official?'计划在原生会话中续聊，沿用所选工作端配置。':'仅授权未来24小时内的计划，沿用原范围、模型及累计预算。'}控制服务须保持运行；重启后暂停，错过时间不补发。配置或上下文变化时暂停。</p><label class="wk-check"><input type="checkbox" name="confirm" required><span>${official?'确认按上述时间在原生会话中发送':'确认按上述时间和原累计预算发送'}</span></label><button class="wk-button primary" type="submit">保存并启用计划</button></form><details><summary>已有计划</summary>${scheduleList()}</details>`);
  }
  async function stopTask(id){
    const d=await call('task?id='+encodeURIComponent(id)),a=d.attempts.at(-1);
    if(!a)throw Error('没有运行尝试。');
    await command('task.stop',{task_id:id,spec_hash:d.task.spec_hash,attempt_id:a.id,owner:a.owner});
    toast('已登记停止请求，等待退出记录。');
  }
  function nativeActions(r){
    if(r.method.includes('UserInput'))return b('填写回答','runtime-input',`data-id="${r.id}"`);
    if(r.method.includes('elicitation')&&r.params.mode==='form')return b('填写并提交表单','runtime-mcp-form',`data-id="${r.id}"`)+b('拒绝','runtime-native',`data-id="${r.id}" data-decision="decline"`);
    const can=decision=>!r.params.availableDecisions||r.params.availableDecisions.includes(decision);
    const official=v.detail?.task.spec.runtime_mode==='official',session=official&&(!r.method.startsWith('sdk/'))&&!r.method.includes('elicitation')&&can('acceptForSession');
    return `${!r.method.includes('elicitation')&&can('accept')?b('批准本次','runtime-native',`data-id="${r.id}" data-decision="accept"`):''}${session?b('本会话允许','runtime-native',`data-id="${r.id}" data-decision="acceptForSession"`):''}${can('decline')?b('拒绝本次','runtime-native',`data-id="${r.id}" data-decision="decline"`):''}${can('cancel')?b('取消','runtime-native',`data-id="${r.id}" data-decision="cancel"`):''}`;
  }
  function runtimeControls(detail){
    if(!detail)return '';
    const a=detail.attempts.at(-1),live=a&&activeStates.has(a.state),unknown=a&&['SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN'].includes(a.state)&&detail.task.spec.runtime_mode==='official';
    return `<div class="wb-feature-actions">${live?b('停止工作','runtime-stop'):''}${unknown?b('核对状态','runtime-reconcile')+b('结束待核对回合','runtime-close-unknown'):''}${!a?b('确认开始此草稿','existing-start','',true):''}</div>${detail.approvals.filter(a=>a.state==='PENDING').map(a=>`<section class="wb-feature-card"><strong>等待检查审批</strong><pre>${h(a.description||a.id)}</pre>${b('允许本次','runtime-approval',`data-id="${a.id}" data-allow="true"`)}${b('拒绝本次','runtime-approval',`data-id="${a.id}" data-allow="false"`)}</section>`).join('')}${detail.native_requests.filter(r=>r.state==='PENDING').map(r=>`<section class="wb-feature-card"><strong>等待${r.method.includes('UserInput')?'补充输入':'原生审批'}</strong><pre>${h(JSON.stringify(r.params,null,2))}</pre>${nativeActions(r)}</section>`).join('')}`;
  }
  async function runtimeDetail(){return v.detail||await call('task?id='+encodeURIComponent(v.dialogue?.pending||chat()?.pending));}
  function currentFiles(session){return fileSession===session&&session?.origin===location.hash&&session.generation===v.generation&&$('#wb-dialog').open&&!!$('#wb-selected-files');}
  function fileList(){
    const session=fileSession;if(!currentFiles(session))return;
    $('#wb-selected-files').innerHTML=[...session.files].map(([name,directory])=>`<div class="wd-work-file" data-selected-file="${h(name)}">${icon(directory?'folder':'file')}<span><strong>${h(name.split('/').pop())}</strong><small title="${h(name)}">${h(name)}</small></span>${!session.frozen?b(icon('close'),'remove-work-file',`data-file="${h(name)}" aria-label="移除文件：${h(name)}"`):''}</div>`).join('')||'<p class="wk-note wd-work-file-empty">尚未添加文件</p>';
    const save=$('#wb-dialog [data-wb="save-files"]');if(save)save.disabled=session.pending>0;
  }
  function filesDialog(){
    const frozen=!!(v.detail||v.dialogue);
    fileSession={origin:location.hash,generation:v.generation,project:v.project,frozen,pending:0,files:new Map([...v.files.split(/\r?\n/).filter(Boolean),...v.attachments.map(a=>a.file)].map(name=>[name,false]))};
    const fileNote=frozen?'当前文件清单已固定。':v.type==='cooperative'?'本次材料将随合作任务交给各阶段成员。':
      '按文件格式交由 '+h(B.names[v.provider])+' 官方工作端处理，无需配置解析方式。';
    modal('工作文件',`${!frozen?`<div class="wb-feature-actions">${b('浏览工作区文件','browse-files')}<label class="wk-button">添加本机文件<input id="wb-upload-files" type="file" multiple hidden></label></div><p class="wk-note wd-work-file-drop" id="wb-drop-zone">选择文件，或拖放到这里。原文件保留。</p><div id="wb-file-browser"></div>`:''}<div id="wb-selected-files" class="wd-work-files" aria-label="已选择的工作文件"></div><p class="wk-note">${fileNote}</p><div id="wb-file-preview"></div>`,frozen?`${v.detail?`<a class="wk-button" href="#control/edit/${v.detail.task.id}" data-wb-close-link>修订材料</a>`:`<a class="wk-button" href="#collab/dialogue/${v.dialogue.id}" data-wb-close-link>讨论材料设置</a>`}${b('完成','close')}`:b('取消','close')+b('保存选择','save-files','',true));
    $('#wb-dialog').dataset.uiModule='workbench';fileList();
  }
  async function browseFiles(folder=''){
    const session=fileSession;if(!currentFiles(session))return;
    if(!session.project)throw Error('先选择工作区，再浏览其中的文件。');
    const result=await call('work-files?project='+session.project+'&folder='+encodeURIComponent(folder));
    if(!currentFiles(session))return;
    const parent=folder.split('/').slice(0,-1).join('/');
    $('#wb-file-browser').innerHTML=`<div class="wb-file-list"><div class="wb-feature-actions"><strong>${h(folder||'工作区根目录')}</strong>${folder?b('上一级','browse-folder',`data-folder="${h(parent)}"`):''}</div>${result.files.map(f=>`<div class="wb-file-row"><button type="button" data-wb="${f.directory?'browse-folder':'preview-file'}" data-folder="${h(f.file)}" data-file="${h(f.file)}">${icon(f.directory?'folder':'file')} ${h(f.name)}</button>${b(f.directory?'选择目录':'添加','choose-file',`data-file="${h(f.file)}" ${f.directory?'data-directory="true"':''}`)}</div>`).join('')}${result.limited?'<p class="wk-note">仅显示前500项，请缩小目录范围。</p>':''}</div>`;
  }
  function addFile(name,directory=false){
    if(!currentFiles(fileSession)||fileSession.frozen)return;
    fileSession.files.set(name,directory);fileList();
  }
  async function uploadFiles(files){
    const session=fileSession,batch=Array.from(files);if(!currentFiles(session)||session.frozen||!batch.length)return;
    session.pending++;fileList();
    const upload=fileUploads.catch(()=>{}).then(async()=>{
      if(!currentFiles(session))return;
      for(const file of batch)if(file.size>8*1024*1024)throw Error('单个文件不能超过8MB：'+file.name);
      if(!session.project){const receipt=await command('project.scratch',{});session.project=receipt.project_id;if(!currentFiles(session))return;v.project=session.project;await load();}
      for(const file of batch){
        if(!currentFiles(session))return;
        const raw=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});
        if(!currentFiles(session))return;
        const result=await command('work.upload',{project_id:session.project,name:file.name,data:raw});
        if(currentFiles(session)){session.files.set(result.file,false);fileList();}
      }
    });
    fileUploads=upload;
    try{await upload;}catch(exc){if(currentFiles(session))B.error(exc);}
    finally{session.pending--;if(currentFiles(session))fileList();}
  }
  async function saveFiles(){
    const session=fileSession;if(!currentFiles(session)||session.frozen)throw Error('当前工作的文件清单不能直接更改。');
    if(session.pending)throw Error('文件仍在加载，请稍候。');
    const selected=session.files.size?await command('work.files-select',{project_id:session.project,files:[...session.files.keys()]}):{files:[],attachments:[]};
    if(!currentFiles(session))return;
    v.files=selected.files.join('\n');v.attachments=selected.attachments;$('#wb-dialog').close();draw();autosave();
  }
  async function previewFile(file){
    const session=fileSession;if(!currentFiles(session))return;
    const result=await call('work-preview?project='+session.project+'&file='+encodeURIComponent(file));
    if(!currentFiles(session))return;
    $('#wb-file-preview').innerHTML=`<div class="wb-feature-card"><strong>${h(file)}</strong>${result.image?`<img class="wb-preview-image" src="${result.image}" alt="${h(file)}">`:`<pre>${h(result.content.slice(0,16000))}</pre>`}<small>${result.bytes} 字节 · ${h(result.sha256)}</small></div>`;
  }
  const sideScope=()=>location.hash.startsWith('#work-items/conversation/')?location.hash.split('/').slice(0,3).join('/'):location.hash;
  const activeSide=session=>!!session&&side===session&&session.scope===sideScope()&&session.panel.isConnected;
  function sideConversation(){
    if(!activeSide(side)||side.panel.closest('[hidden]'))return null;
    return {open:true,id:side.row?.id||null,title:side.row?.title||'',provider:side.provider,route:side.route};
  }
  function notifySide(){document.dispatchEvent(new CustomEvent('workdesk:sidechat-state',{detail:sideConversation()}));}
  const sideError=(session,error)=>{if(activeSide(session))session.panel.querySelector('#wb-side-error').textContent=error;};
  const isSideAction=action=>['sidechat','close-side','side-api-stop'].includes(action);
  async function sideCommand(kind,payload){
    const id=crypto.randomUUID();
    try{return (await call('command',{id,kind,payload})).receipt;}
    catch(error){throw Error(error.message+' · 回执 '+id);}
  }
  function openSide(){
    const root=window.WorkdeskModules.currentHost();
    const host=root?.querySelector('[data-wb-side-host]');
    let panel=side?.panel;
    if(!activeSide(side)){
      closeSide();
      panel=document.createElement('aside');panel.id='wb-sidechat';panel.className='wb-sidechat';panel.setAttribute('aria-label','侧边聊天');
      const source=window.WorkdeskModules.currentOwner()==='workbench'?target():null;
      panel.innerHTML=`<div class="wb-feature-actions"><strong>侧边聊天</strong>${b('关闭','close-side')}</div><div id="wb-side-thread" class="wb-side-thread"><p class="wk-note">独立保存为一段 Chat 对话。</p></div><form id="wb-side-form"><label class="wk-field">连接方式<select class="wk-input" name="route"><option value="web">官方网页</option><option value="api" disabled>API 连接</option></select></label><label class="wk-field">提供方<select class="wk-input" name="provider"></select></label><label class="wk-field" data-side-model hidden>模型<select class="wk-input" name="model"></select></label><label class="wk-field">消息<textarea class="wk-input" name="content" rows="3" required></textarea></label>${source?'<label class="wk-check"><input type="checkbox" name="context"><span>附上当前对话正文</span></label>':''}<label class="wk-check"><input type="checkbox" name="confirm" required><span data-side-confirm></span></label><div class="wb-feature-actions"><button class="wk-button primary" type="submit"></button>${b('停止侧边回复','side-api-stop','hidden')}</div></form><p id="wb-side-error" class="wk-error" role="alert"></p>`;
      const form=panel.querySelector('form');
      side={panel,form,source,scope:sideScope(),apiServices:[],route:'web',provider:'codex',row:null,busy:false,stopping:false,timer:null,readRevision:0};
      const session=side;
      form.addEventListener('change',event=>changeSideSelection(session,event.target));
      side.form.elements.provider.innerHTML=sideProviderOptions(side);
      paintSide(side);
      void loadSideServices(side);
    }
    const parent=host||document.body;
    if(panel.parentElement!==parent)parent.append(panel);
    panel.classList.toggle('is-docked',!!host);
    host?.dispatchEvent(new CustomEvent('workdesk:sidechat',{bubbles:true}));
    requestAnimationFrame(()=>{if(panel.isConnected){panel.querySelector('textarea')?.focus();notifySide();}});
  }
  function closeSide(){if(side){clearTimeout(side.timer);side.panel.remove();side=null;notifySide();}}
  function sideProviderOptions(session){
    const entries=session.route==='api'?session.apiServices.map(p=>[p.provider,p.name]):Object.entries(B.names);
    return entries.map(([provider,name])=>`<option value="${h(provider)}">${h(name)}</option>`).join('');
  }
  async function loadSideServices(session){
    try{
      const {chat_services}=await call('ui-state');
      if(!activeSide(session))return;
      session.apiServices=(chat_services?.api_services||[]).filter(item=>item.ready);
      session.form.elements.route.querySelector('[value="api"]').disabled=!session.apiServices.length;
    }catch(error){sideError(session,error.message);}
  }
  function changeSideSelection(session,control){
    if(!activeSide(session)||session.busy||session.row?.pending||!['route','provider'].includes(control.name))return;
    const f=session.form.elements,defaults=window.WorkdeskSettingsPreferences?.get()?.chatDefaults;
    if(control.name==='route'){
      session.route=f.route.value;
      f.provider.innerHTML=sideProviderOptions(session);
      const preferred=session.route==='api'?defaults?.apiProvider||'deepseek':defaults?.provider;
      if([...f.provider.options].some(option=>option.value===preferred))f.provider.value=preferred;
    }
    session.provider=f.provider.value;
    session.row=null;
    f.confirm.checked=false;
    const service=session.apiServices.find(p=>p.provider===session.provider);
    f.model.innerHTML=(service?.models||[]).map(model=>`<option value="${h(model)}">${h(model)}</option>`).join('');
    if(service?.models.includes(defaults?.model))f.model.value=defaults.model;
    sideError(session,'');paintSide(session);
  }
  function paintSide(session){
    const {row,route,busy,stopping,panel,form}=session;
    const isApi=route==='api',pending=!!row?.pending,inputs=form.elements;
    inputs.route.disabled=inputs.provider.disabled=busy||pending;
    inputs.model.disabled=busy||pending||!!row?.messages.length;
    inputs.content.maxLength=isApi?6000000:12000;
    form.querySelector('[data-side-model]').hidden=!isApi;
    form.querySelector('[data-side-confirm]').textContent=isApi?'确认按官方 API 用量计费':'仅在本地保存文字，不发送模型请求';
    const submit=form.querySelector('[type=submit]');
    submit.textContent=isApi?'发送侧边 API 消息':'保存侧边开场文字';submit.disabled=busy||pending;
    const stop=form.querySelector('[data-wb="side-api-stop"]');
    stop.hidden=!pending;stop.disabled=busy||stopping||row?.messages.at(-1)?.state==='STOP_REQUESTED';
    const thread=panel.querySelector('#wb-side-thread');
    const markup=row?.route==='web'&&row.prompt?`<p class="wk-note">开场文字已在本地保存，可到官网继续。</p><pre>${h(row.prompt)}</pre><a class="wk-button" href="${h(row.web_url||B.urls[row.provider])}" target="_blank" rel="noopener noreferrer">打开官方聊天</a>`:
      row?.messages.length?chatMessages(row):'<p class="wk-note">独立保存为一段 Chat 对话。</p>';
    const previous=thread.scrollTop,nearEnd=thread.scrollHeight-previous-thread.clientHeight<32;
    thread.innerHTML=markup;
    thread.scrollTop=nearEnd?thread.scrollHeight:previous;
    const state=JSON.stringify(sideConversation());
    if(state!==session.lastPublished){session.lastPublished=state;notifySide();}
  }
  async function readSide(session){
    const id=session.row?.id;if(!id)return;
    const revision=++session.readRevision;
    const {chats}=await call('ui-state');
    if(!activeSide(session)||session.row?.id!==id||session.readRevision!==revision)return;
    const row=chats.find(chat=>chat.id===id);
    if(!row)throw Error('这段侧边聊天记录已不可用。');
    session.row=row;paintSide(session);
  }
  function pollSide(session){
    clearTimeout(session.timer);
    if(!activeSide(session)||!session.row?.pending)return;
    session.timer=setTimeout(async()=>{
      try{await readSide(session);pollSide(session);}
      catch(error){sideError(session,error.message);pollSide(session);}
    },650);
  }
  async function stopSide(session){
    if(!activeSide(session)||!session.row?.pending||session.stopping)return;
    session.stopping=true;sideError(session,'');paintSide(session);
    try{await sideCommand('chat.api-stop',{id:session.row.id,turn_id:session.row.pending});await readSide(session);}
    catch(error){sideError(session,error.message);}
    finally{session.stopping=false;if(activeSide(session))paintSide(session);pollSide(session);}
  }
  async function submitSide(form){
    const session=side;if(!activeSide(session)||session.form!==form||session.busy||session.row?.pending)return;
    const f=new FormData(form),{route,provider}=session,model=form.elements.model.value,text=String(f.get('content')||'').trim();
    if(f.get('confirm')!=='on'||!text)return;
    const source=f.get('context')==='on'?session.source:null;
    session.busy=true;sideError(session,'');paintSide(session);
    try{
      const content=source?(await selectedTranscript(source)).text+'\n\n侧边问题：\n'+text:text;
      if(!activeSide(session))return;
      if(route==='web'&&content.length>12000)throw Error('开场文字和附加正文合计最多 12000 字。');
      if(!session.row)session.row=(await sideCommand('chat.create',{provider,route})).chat;
      if(!activeSide(session))return;
      const {id,revision}=session.row;
      const result=await sideCommand(route==='api'?'chat.api-send':'chat.send',{id,revision,content,
        ...(route==='api'?{model,billing:'official_api'}:{confirm:true})});
      session.row=result.chat;
      if(!activeSide(session))return;
      form.elements.content.value='';form.elements.confirm.checked=false;
      if(form.elements.context)form.elements.context.checked=false;
      if(window.WorkdeskModules.currentOwner()==='workbench'){
        await refresh();if(activeSide(session))renderSidebar();
      }
    }catch(error){
      if(activeSide(session)&&session.row){try{await readSide(session);}catch{}}
      sideError(session,error.message);
    }finally{session.busy=false;if(activeSide(session))paintSide(session);pollSide(session);}
  }
  async function act(action,el){
    if(action==='records'){go('#records');return true;}
    if(action==='files'){filesDialog();return true;}
    if(action==='save-files'){await saveFiles();return true;}
    if(action==='remove-work-file'){if(currentFiles(fileSession)&&!fileSession.frozen){fileSession.files.delete(el.dataset.file);fileList();}return true;}
    if(action==='new'){
      const origin=location.hash;
      const inputs=[...document.querySelectorAll('#main .wk-composer textarea')];inputs.forEach(el=>el.disabled=true);
      try{await reset();}finally{inputs.forEach(el=>el.disabled=false);}
      if(location.hash!==origin)return true;
      return false;
    }
    if(await chatModule.act(action,el))return true;
    if(action==='search'){await search();return true;}
    if(action==='copy'){await copyText((await selectedTranscript()).text);return true;}
    if(action==='copy-link'){
      const record=target();if(!record)throw Error('先选择一段对话。');
      await copyText((record.kind==='chat'?'#chat/':'#work/'+record.kind+'/')+encodeURIComponent(record.id));return true;
    }
    if(action==='move-project'||action==='remove-project'){await moveProject(action==='remove-project');return true;}
    if(action==='share'){
      const origin=location.hash,generation=v.generation,full=await selectedTranscript();
      if(origin!==location.hash||generation!==v.generation)return true;
      v.exportRecord=full;
      modal('分享 / 导出','<p class="wk-note">可复制 Workdesk 本地路径，或下载包含已保存正文的文件。</p>',b('取消','close')+b('复制链接','copy-link')+b('下载 Markdown','export-md')+b('下载 JSON','export','',true));return true;
    }
    if(action==='export'||action==='export-md'){
      const record=target(),saved=v.exportRecord;
      const full=saved&&saved.id===record?.id&&saved.kind===record.kind&&$('#wb-dialog').open?saved:await selectedTranscript(record),md=action==='export-md';
      const blob=new Blob([md?full.markdown:JSON.stringify(full,null,2)],{type:md?'text/markdown;charset=utf-8':'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='workdesk-conversation.'+(md?'md':'json');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('#wb-dialog').close();return true;
    }
    if(action==='window'){window.open(location.href,'_blank','noopener');B.closeMenu();return true;}
    if(action==='fork'){
      const t=target();if(!t)throw Error('先选择一段对话。');
      if(t.kind==='chat'){
        const origin=location.hash,generation=v.generation,current=chat();
        if(!current)throw Error('聊天已变化，请重新打开记录。');
        const child=await command('chat.fork',{id:current.id,revision:current.revision});
        if(location.hash!==origin||v.generation!==generation)return true;
        v.chatEmbedded=false;
        await go('#chat/'+encodeURIComponent(child.chat_id));
        return true;
      }
      const row=(await command('work.fork',t)).draft;await refresh();go('#work/draft/'+row.id);return true;
    }
    if(action==='sidechat'){openSide();B.closeMenu();return true;}
    if(action==='close-side'){
      side?.panel.closest('[data-wb-side-host]')?.dispatchEvent(new CustomEvent('workdesk:sidechat-close',{bubbles:true}));
      closeSide();return true;
    }
    if(action==='side-api-stop'){await stopSide(side);return true;}
    if(action==='schedule'){await refresh();scheduleDialog();return true;}
    if(action==='schedule-state'){await command('schedule.state',{id:el.dataset.id,state:el.dataset.state});await refresh();if(location.hash==='#schedules')special();else{ $('#wb-dialog').close();go('#schedules');}return true;}
    if(action==='unarchive-record'){await command(el.dataset.kind==='chat'?'chat.record':'work.record',{id:el.dataset.id,kind:el.dataset.kind,archived:false});await load();special();return true;}
    if(action==='unarchive'){await B.saveRecord({archived:false});draw();return true;}
    if(action==='restore-draft'){
      const d=data.drafts.find(d=>d.id===el.dataset.id);go(d.value.mode==='chat'?'#chat/draft/'+d.id:'#work/draft/'+d.id);return true;
    }
    if(action==='copy-draft'){const d=data.drafts.find(d=>d.id===el.dataset.id);await copyText(d.value.input||d.value.chatInput||'');return true;}
    if(action==='delete-draft'){const d=data.drafts.find(d=>d.id===el.dataset.id);await command('draft.delete',{id:d.id,revision:d.revision});await refresh();special();return true;}
    if(action==='browse-files'||action==='browse-folder'){await browseFiles(el.dataset.folder||'');return true;}
    if(action==='choose-file'){addFile(el.dataset.file,el.dataset.directory==='true');return true;}
    if(action==='preview-file'){await previewFile(el.dataset.file);return true;}
    if(action==='pick-directory'){
      const form=$('#wb-project-form'),origin=location.hash;if(!form||form.dataset.picking)return true;
      form.dataset.picking='true';el.disabled=true;
      let result;
      try{result=await api('pick-directory',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}
      catch(exc){if(form.isConnected&&$('#wb-project-form')===form&&$('#wb-dialog').open&&location.hash===origin)throw exc;return true;}
      finally{delete form.dataset.picking;if(el.isConnected)el.disabled=false;}
      if(!result.path||!form.isConnected||$('#wb-project-form')!==form||!$('#wb-dialog').open||location.hash!==origin)return true;
      const root=result.path,normalized=value=>value.replaceAll('\\','/').replace(/\/+$/,'').toLowerCase();
      const existing=v.data.projects.find(p=>normalized(p.root)===normalized(root));
      const name=form.querySelector('[name=name]'),folderName=root.replace(/[\\/]+$/,'').split(/[\\/]/).pop()||root;
      form.querySelector('[name=root]').value=existing?.root||root;name.readOnly=!!existing;
      if(existing)name.value=existing.name;else if(!name.value.trim()||form.dataset.existing)name.value=folderName;
      if(existing)form.querySelector('[name=scene]').value=existing.scene||'';
      form.dataset.existing=existing?.id||'';
      form.querySelector('#wb-project-empty').hidden=true;form.querySelector('#wb-project-folder').hidden=false;
      form.querySelector('#wb-project-folder-name').textContent=folderName;
      form.querySelector('#wb-project-folder-path').textContent=root;
      form.querySelector('#wb-project-pick-label').textContent='更换文件夹';
      const status=form.querySelector('#wb-project-folder-status');status.hidden=!existing;status.textContent=existing?'该文件夹已用于项目“'+existing.name+'”，将选择已有项目。':'';
      const submit=form.querySelector('[type=submit]');submit.disabled=false;submit.textContent=existing?'选择已有项目':form.dataset.create==='true'?'创建项目':'添加并选择';
      const error=$('#wb-modal-error');if(error)error.hidden=true;
      return true;
    }
    if(action==='runtime-stop'){await stopTask(v.detail?.task.id||v.dialogue.pending);return true;}
    if(action==='runtime-approval'){await command('approval.resolve',{approval_id:el.dataset.id,allow:el.dataset.allow==='true'});await B.rerender();return true;}
    if(action==='runtime-native'){
      const d=await runtimeDetail(),r=d.native_requests.find(r=>r.id===el.dataset.id),a=d.attempts.at(-1),decision=el.dataset.decision;
      if(!r)throw Error('审批请求已失效，请刷新。');
      const response=r.method.includes('permissions')?{allow:['accept','acceptForSession'].includes(decision),scope:decision==='acceptForSession'?'session':'turn'}:r.method.includes('elicitation')?{action:decision==='cancel'?'cancel':'decline'}:{decision};
      await command('native.resolve',{task_id:d.task.id,attempt_id:a.id,owner:a.owner,request_id:r.id,response,source:'HUMAN_UI'});await B.rerender();return true;
    }
    if(action==='runtime-mcp-form'){
      const d=await runtimeDetail(),r=d.native_requests.find(r=>r.id===el.dataset.id);if(!r)throw Error('表单已失效。');v.inputRequest={detail:d,request:r};
      const values=Object.fromEntries(Object.entries(r.params.requestedSchema?.properties||{}).map(([k,s])=>[k,s.default??(s.type==='boolean'?false:s.type==='number'||s.type==='integer'?0:s.type==='array'?[]:'')]));
      modal('MCP 请求表单',`<p class="wk-note">${h(r.params.message)} · ${h(r.params.serverName||'当前原生 MCP 服务')}</p><pre>${h(JSON.stringify(r.params.requestedSchema,null,2))}</pre><form id="wb-native-mcp-form"><label class="wk-field">提交内容<textarea class="wk-input" rows="6" name="content">${h(JSON.stringify(values,null,2))}</textarea></label><button class="wk-button primary" type="submit">提交给此 MCP 服务</button></form>`);return true;
    }
    if(action==='runtime-input'){
      const d=await runtimeDetail(),r=d.native_requests.find(r=>r.id===el.dataset.id);
      if(!r)throw Error('输入请求已失效。');v.inputRequest={detail:d,request:r};
      modal('补充输入',`<form id="wb-native-input-form">${r.params.questions.map((q,i)=>`<label class="wk-field">${h(q.question)}<small>${h((q.options||[]).map(o=>o.label+': '+o.description).join('；'))}</small><textarea class="wk-input" name="answer${i}" ${q.isSecret?'disabled':'required'}></textarea></label>`).join('')}<p class="wk-note">密码通过原生认证入口填写。</p><button type="submit" class="wk-button primary" ${r.params.questions.some(q=>q.isSecret)?'disabled':''}>发送回答</button></form>`);return true;
    }
    if(action==='existing-start'){
      modal('确认开始此草稿',`<p class="wb-goal">${h(v.detail.task.spec.goal)}</p><p class="wk-note">沿用此草稿已冻结的材料、可写路径、模型与预算。</p>`,b('返回','close')+b('确认并开始','existing-confirm','',true));return true;
    }
    if(action==='existing-confirm'){
      const t=v.detail.task,p={task_id:t.id,spec_hash:t.spec_hash,note:v.input};await command('task.approve',p);await command('task.start',p);v.input='';$('#wb-dialog').close();await B.rerender();return true;
    }
    if(action==='save-roles'&&v.dialogue){
      const d=v.dialogue;await command('dialogue.configure',{dialogue_id:d.id,work_roles:v.roleDraft,config:B.config(v.roleDraft.lead)});$('#wb-dialog').close();await B.rerender();return true;
    }
    return false;
  }
  async function submit(form){
    const f=new FormData(form);
    if(form.id==='wb-move-conversation-form'){await saveProjectMove(form);return true;}
    if(form.id==='wb-native-mcp-form'){
      const {detail:d,request:r}=v.inputRequest,a=d.attempts.at(-1);let content;try{content=JSON.parse(f.get('content'));}catch{throw Error('表单内容不是有效JSON。');}
      await command('native.resolve',{task_id:d.task.id,attempt_id:a.id,owner:a.owner,request_id:r.id,response:{action:'accept',content},source:'HUMAN_UI'});$('#wb-dialog').close();await B.rerender();return true;
    }
    if(form.id==='wb-schedule-form'){
      await command('schedule.save',{...target(),due:new Date(f.get('due')).getTime()/1000,content:f.get('content'),interval:Number(f.get('interval')),max_runs:Number(f.get('max_runs')),confirm:f.get('confirm')==='on'});
      $('#wb-dialog').close();await refresh();go('#schedules');return true;
    }
    if(await chatModule.submit(form))return true;
    if(form.id==='wb-native-input-form'){
      const {detail:d,request:r}=v.inputRequest,a=d.attempts.at(-1),answers=Object.fromEntries(r.params.questions.map((q,i)=>[q.id,{answers:[f.get('answer'+i)||'']}]));
      await command('native.resolve',{task_id:d.task.id,attempt_id:a.id,owner:a.owner,request_id:r.id,response:{answers},source:'HUMAN_UI'});$('#wb-dialog').close();await B.rerender();return true;
    }
    return false;
  }
  document.addEventListener('input',e=>{
    if(e.target.id==='wb-work-input'){v.input=e.target.value;autosave();}
    if(e.target.id==='wb-chat-input'){v.chatInput=e.target.value;autosave();}
    if(e.target.closest('#wb-start-form')){const f=new FormData(e.target.form);Object.assign(v.formDraft,Object.fromEntries(['acceptance','writable','checks'].map(k=>[k,f.get(k)||''])));autosave();}
  });
  document.addEventListener('change',e=>{
    if(e.target.id==='wb-upload-files'){const selected=Array.from(e.target.files);e.target.value='';uploadFiles(selected).catch(B.error);}
    if(['wb-chat-provider','wb-chat-model','wb-model','wb-provider'].includes(e.target.id)||e.target.dataset.wbRole)setTimeout(autosave,0);
  });
  document.addEventListener('click',e=>{if(e.target.closest('[data-wb-scene],[data-wb-type],[data-wb-project],[data-wb="save-files"],[data-wb="save-roles"]'))setTimeout(autosave,0);});
  document.addEventListener('dragover',e=>{if(e.target.closest('#wb-dialog')&&$('#wb-upload-files'))e.preventDefault();});
  document.addEventListener('drop',e=>{if(e.target.closest('#wb-dialog')&&$('#wb-upload-files')){e.preventDefault();uploadFiles(e.dataTransfer.files).catch(B.error);}});
  window.addEventListener('hashchange',()=>{if(side&&side.scope!==sideScope())closeSide();});
  window.addEventListener('pagehide',()=>{const item=captured();if(item)setLocal(localKey(item.key),item.value);});
  return {chatPresentation:chatModule.presentation,chatSelection:chatModule.selection,chatFiles:chatModule.upload,refresh,beforeRender,suspendChatDrafts,suspendWorkDrafts,restore,autosave,special,applyDraft,chatPage,submitChat,pollChat,leave,act,submit,isSideAction,submitSide,sideConversation,searchResults,saveChat,runtimeControls};
};
