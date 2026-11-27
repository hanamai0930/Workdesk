/* Dialogue-first Workdesk. All execution remains in the existing control service. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const scenes={research:{name:'科研探索',icon:'research',hint:'检索资料、提出问题、验证想法'},documents:{name:'文档处理',icon:'file',hint:'阅读、整理与制作各类文档'},computer:{name:'电脑操控',icon:'pointer',hint:'浏览网页与操作桌面应用'},development:{name:'应用开发',icon:'terminal',hint:'编写代码、调试和构建应用'}};
  const roleNames={lead:['统筹 AI','规划目标、分配任务、整合结果','compass'],execute:['执行 AI','编写代码、处理材料、完成工作','terminal'],review:['评审 AI','审计过程、检查质量、评阅成果','review']};
  const names={codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen',claude:'Claude',grok:'Grok',gemini:'Gemini',kimi:'Kimi',glm:'GLM'};
  const urls={codex:'https://chatgpt.com/',deepseek:'https://chat.deepseek.com/',qwen:'https://chat.qwen.ai/',claude:'https://claude.ai/',grok:'https://grok.com/',gemini:'https://gemini.google.com/',kimi:'https://www.kimi.com/',glm:'https://chat.z.ai/'};
  const v={mode:'chat',scene:'research',type:'independent',provider:'codex',model:'',project:'',input:'',files:'',attachments:[],roles:{lead:'codex',execute:'deepseek',review:'qwen'},roleDraft:null,
    workAccess:'workspace',workEffort:'',workTier:'',connectionId:'',connectionRevision:'',workPlanOnly:false,data:null,co:null,extensions:null,detail:null,dialogue:null,pending:null,busy:false,timer:null,generation:0,menu:null,anchor:null,projectQuery:'',activeChat:null,
    chats:getLocal('web-chats',[]),chatProvider:getLocal('chat-provider','codex'),chatRoute:'api',chatApiProvider:'deepseek',chatApiModel:'deepseek-flash',chatThinking:false,chatSearch:false,chatSearchQuery:'',chatFiles:[],chatModel:'',chatInput:'',chatEmbedded:false,formDraft:{},workLoading:false,side:false,sidebar:getLocal('sidebar-open',true),mobileSidebar:false};
  if(!Array.isArray(v.chats))v.chats=[];
  const h=esc;
  let fullSend=null,cooperationSend=null,capabilityOptions=[];
  const defaultAccess=provider=>window.WorkdeskSettingsPreferences?.get().workAccess[provider]||'workspace';
  function chatDefaults(){
    const choice=window.WorkdeskSettingsPreferences?.get().chatDefaults;
    return choice?{chatRoute:choice.route,chatApiProvider:choice.apiProvider||'deepseek',chatApiModel:choice.model,chatProvider:choice.provider}:null;
  }
  const b=(label,action,attrs='',primary=false)=>`<button type="button" class="wk-button ${primary?'primary':''}" data-wb="${action}" ${attrs}>${label}</button>`;
  const ib=(label,action,glyph,attrs='')=>`<button type="button" class="wk-icon" data-wb="${action}" aria-label="${h(label)}" title="${h(label)}" ${attrs}>${icon(glyph)}</button>`;
  const call=(path,value)=>api('v1/'+path,value===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const lines=value=>String(value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  const project=()=>v.data?.projects.find(p=>p.id===v.project);
  const profile=(id=v.provider)=>v.data?.profiles.find(p=>p.id===id);
  const partnerConnections=()=>v.data?.partner_connections;
  const selectedPartnerConnection=()=>partnerConnections()?.connections.find(row=>row.id===v.connectionId&&row.partner_id===v.provider);
  const isWorkRoute=()=>location.hash.startsWith('#work');
  const task=()=>v.detail?.task;
  const composerCapabilities=()=>v.formDraft.composerCapabilities||[];
  const partnerDocumentSkills=()=>!!partnerConnections()&&!task()&&!v.dialogue;
  function partnerMcpUnavailable(){
    if(!partnerDocumentSkills())return false;
    if(v.type!=='cooperative')return !selectedPartnerConnection()?.capabilities?.per_task_mcp;
    const {mode,assignments,expertCount}=cooperationConfig();
    const refs=Object.entries(assignments[mode]).filter(([key])=>!key.startsWith('expert_')||Number(key.slice(7))<=expertCount).map(([,ref])=>ref);
    return !refs.length||refs.some(ref=>!partnerConnections().connections.find(row=>row.id===ref.connection_id&&String(row.revision)===String(ref.connection_revision))?.capabilities?.per_task_mcp);
  }
  function checkPartnerCapabilities(){if(partnerMcpUnavailable()&&composerCapabilities().some(ref=>ref.kind==='mcp'))throw Error('所选连接暂不支持本次加载 MCP，请更换连接或移除 MCP 选择。');}
  const canPlan=()=>!v.dialogue&&(!task()||task().spec.runtime_mode==='official'&&
    !['RESERVED','RUNNING','SEND_INTENT','STOP_REQUESTED','WAITING_APPROVAL','WAITING_INPUT'].includes(task().state));
  const locked=()=>!!(v.detail?.attempts.length||v.dialogue?.pending||v.dialogue?.messages.some(m=>m.task_id));
  const display=(kind,id)=>v.co?.display_records?.find(r=>r.kind===kind&&r.record_id===id)||{};
  const title=()=>v.activeChat?v.chats.find(c=>c.id===v.activeChat)?.title:v.dialogue?display('dialogue',v.dialogue.id).title||v.dialogue.title:task()?display('task',task().id).title||task().title:'';
  function href(kind,id){return '#work/'+kind+'/'+encodeURIComponent(id);}
  function go(target,newWork=false){if(['files','records','results','monitor','projects','planning','libraries','settings','work-items'].includes(target.slice(1).split('/')[0])){closeMenu();if(location.hash===target)return route();location.hash=target;return;}if(location.hash!==target){window.history.pushState(null,'',target);state.route=target.slice(1);}closeMenu();if($('#wb-dialog').open)$('#wb-dialog').close();return render(target.slice(1).split('/').slice(1),undefined,undefined,newWork);}
  function leave(){fullSend=null;cooperationSend=null;clearTimeout(v.timer);v.timer=null;v.generation++;closeMenu();features.leave();chatRecords.leave();workRecords.leave();}
  async function command(kind,payload){
    if(kind==='independent.send'&&!payload.task_id){
      checkPartnerCapabilities();
      const latest=await call('scenes'),preset=latest.presets.find(p=>p.scene===payload.work_scene&&p.default&&p.enabled);
      payload={...payload,scene_preset:preset?{id:preset.id,revision:preset.revision}:null};
      if(composerCapabilities().length)payload.composer_capabilities=structuredClone(composerCapabilities());
      const ref=v.formDraft?.fileSelection,current=[...new Set([...lines(v.files),...v.attachments.map(x=>x.file)])].sort();
      if(ref&&JSON.stringify([...ref.files].sort())===JSON.stringify(current))payload.file_selection={id:ref.id,revision:ref.revision};
      if(v.formDraft?.workItem)payload.work_item=structuredClone(v.formDraft.workItem);
      if(v.formDraft?.resourceSelection)payload.resource_selection=structuredClone(v.formDraft.resourceSelection);
    }
    const id=crypto.randomUUID();v.pending={id,kind};
    try{const r=await call('command',{id,kind,payload});v.pending=null;return r.receipt;}
    catch(error){if(error.status===409||error.status===400)v.pending=null;throw Error(error.message+' · 命令 '+id+'；请查操作回执，未自动重发。');}
  }
  function config(id){const p=profile(id);if(!p)throw Error('尚未读取工作端配置。');return {provider:id,model:p.model,effort:p.effort,tier:p.tier,execution:id+'_native'};}
  function useWorkDefault(provider){
    if(partnerConnections()){
      const rows=partnerConnections().connections.filter(row=>row.partner_id===provider),connection=rows.find(row=>row.available)||rows[0];
      Object.assign(v,{provider,connectionId:connection?.id||'',connectionRevision:connection?String(connection.revision):'',model:connection?.model||'official-default',workEffort:'default',workTier:'standard',workAccess:defaultAccess(provider)});
      return;
    }
    const {model,effort,tier}=config(provider);
    Object.assign(v,{provider,model,workEffort:effort,workTier:tier,workAccess:defaultAccess(provider)});
  }
  const independent=window.WorkdeskIndependent({v,call,command,modal,b,h,profile,project,lines,scenes,names,
    startExisting:()=>features.act('existing-start'),rerender:id=>render(['task',id]),changed:()=>{draw();features.autosave();}});
  const features=window.WorkdeskFeatures({v,call,command,modal,b,h,draw,go,renderSidebar,load,names,urls,answer,records,closeMenu,saveRecord,config,error,rerender:()=>render(location.hash.slice(1).split('/').slice(1))});
  const chatRecords=window.WorkdeskChatRecords({v,h,b,command,modal,closeMenu,renderSidebar,go,refresh:()=>features.refresh(),suspendDrafts:ids=>features.suspendChatDrafts(ids)});
  const workRecords=window.WorkdeskWorkRecords({v,h,b,command,modal,closeMenu,renderSidebar,go,load,scenes,records,suspendDrafts:ids=>features.suspendWorkDrafts(ids)});
  async function load({refresh=true,generation}={}){
    const [data,co]=await Promise.all([call('workspace?view=workbench'),call('collaboration?view=workbench'),...(refresh?[features.refresh()]:[])]);
    if(generation!==undefined&&generation!==v.generation)return;
    v.data=data;v.co=co;for(const row of data.partner_connections?.partners||[])names[row.id]=row.name;if(!v.model)v.model=profile()?.model||'';independent.install(data);
  }
  const composerSizes=new WeakMap();
  function sizeComposer(el){
    if(!el||!el.clientWidth)return;
    const style=getComputedStyle(el),signature=[el.clientWidth,style.fontSize,el.value].join('|');
    if(composerSizes.get(el)===signature)return;
    el.style.height='auto';
    const min=parseFloat(style.minHeight)||64,max=parseFloat(style.maxHeight)||240;
    el.style.height=Math.min(max,Math.max(min,el.scrollHeight))+'px';
    el.style.overflowY=el.scrollHeight>max?'auto':'hidden';
    composerSizes.set(el,signature);
  }
  function shell(){
    $('#wb-new-label').textContent=v.mode==='chat'?'新聊天':'新工作';
    const showing=['work','chat','home'].includes(location.hash.slice(1).split('/')[0]);
    const conversationPage=showing||/^#work-items\/view-(?:chat|work)\//.test(location.hash);
    for(const mode of ['chat','work']){
      const tab=$('#wb-'+mode+'-tab');
      if(conversationPage&&v.mode===mode)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');
    }
    const localHeader=showing&&window.WorkdeskReact&&
      (v.mode==='chat'||(v.mode==='work'&&v.type==='independent'&&!!v.detail));
    const shellTitle=showing&&!localHeader?(title()||''):'';
    $('#wb-title').textContent=shellTitle;$('#wb-title').title=shellTitle;
    const active=showing&&(v.activeChat||v.detail||v.dialogue);
    const itemLinks=showing&&v.mode==='work'?(v.detail?.work_items||[]).map(i=>`<a class="wk-button" href="#work-items/item/${encodeURIComponent(i.id)}/from-work/${encodeURIComponent(v.detail.task.id)}" title="${h(i.title)}">工作任务</a>`).join(''):'';
    const resourceName=showing&&v.mode==='work'?(v.detail?.task.spec.execution_resource?.member?.name||(!v.detail?v.formDraft?.resourceName:'')):'';
    const resourceLabel=resourceName?(v.detail?`<a class="wk-button" href="#control/profiles">成员：${h(resourceName)}</a>`:b('成员：'+h(resourceName)+' ×','clear-resource','title="清除成员选择，使用当前模型配置"')):'';
    $('#wb-header-actions').innerHTML=document.body.dataset.activePage==='settings'?ib('退出设置','exit-settings','close'):
      localHeader?'':resourceLabel+(active?`${itemLinks}<button class="wk-icon" data-wb-menu="conversation" aria-label="对话操作">${icon('more')}</button>${v.mode==='work'?ib('工作详情','details','panel'):''}`:ib('新建对话','new','plus'));
    document.body.dataset.sidebar=(matchMedia('(max-width:640px)').matches?v.mobileSidebar:v.sidebar)?'open':'closed';
    document.querySelectorAll('[data-wb="sidebar"]').forEach(button=>button.setAttribute('aria-expanded',String(document.body.dataset.sidebar==='open')));
    const managementSection=window.WorkdeskSupport?.managementRoute();
    const inResources=/^#resources(?:\/|$)/.test(location.hash);
    const inSettings=/^#settings(?:\/|$)/.test(location.hash);
    $('#sidebar').setAttribute('aria-label',inSettings?'设置分类':inResources?'资料导航':'项目与对话');
    const section=$('#wb-section-label');
    const page=location.hash.slice(1).split('/')[0];
    const sectionNames={settings:'设置',resources:'资料空间',operations:'恢复工具与诊断',control:'工作端与执行控制',collab:'协作队列',history:'历史交接',libraries:'资料库管理',extensions:'扩展管理'};
    if(section){section.textContent=sectionNames[page]||(managementSection?'工作与记录':'');section.hidden=!section.textContent;}
    const railLinks=[...document.querySelectorAll('.wk-rail a')];
    const matching=railLinks.filter(a=>{const href=a.getAttribute('href');return href&&(location.hash===href||location.hash.startsWith(href+'/'));}).sort((a,b)=>b.getAttribute('href').length-a.getAttribute('href').length)[0];
    document.querySelectorAll('.wk-rail a').forEach(a=>{
      const selected=a.hasAttribute('data-management-entry')?!!managementSection:
        a.hasAttribute('data-resource-entry')?inResources:a===matching;
      a.setAttribute('aria-current',selected?'page':'false');
    });
    document.querySelectorAll('.wk-composer textarea').forEach(sizeComposer);
  }
  function records(){
    if(!v.data)return [];
    if(v.co?.work_records)return v.co.work_records;
    const owned=new Set(v.co?.dialogues.flatMap(d=>d.messages.map(m=>m.task_id).filter(Boolean))||[]);
    for(const d of v.co?.dialogues||[])if(d.pending)owned.add(d.pending);
    return [...v.data.tasks.filter(t=>!owned.has(t.id)&&t.spec.interaction!=='chat').map(t=>({id:t.id,kind:'task',title:t.title,scene:t.spec.work_scene,project:t.project_id,state:t.state,created:t.created})),...(v.co?.dialogues||[]).map(d=>({id:d.id,kind:'dialogue',title:d.title,scene:d.work_scene,project:d.project_id,state:d.pending?'RUNNING':'DISCUSSION',created:d.created}))].map(r=>({...r,...display(r.kind,r.id),id:r.id})).sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||b.created-a.created);
  }
  const insideProjectChat=()=>v.chats.some(c=>c.id===v.activeChat&&c.list_project);
  function renderSidebar(){
    if(!$('#wb-sidebar'))return;
    const projectTree=v.mode==='work'||insideProjectChat();
    $('#wb-sidebar').title=projectTree?'项目与对话；全部记录目录支持全量搜索和分页。':'';
    shell();
    if(!projectTree){
      chatRecords.render();return;
    }
    workRecords.render();
  }
  async function render(parts=[],connections,chatEntry,newWork=false){
    const previousChat=v.activeChat;leave();const generation=v.generation;
    document.body.dataset.activePage=location.hash.slice(1).split('/')[0]||'chat';
    window.WorkdeskSupport?.renderManagementNavigation();
    window.WorkdeskSettingsPreferences?.remember(location.hash.slice(1)||'chat');
    try{
      const mode=location.hash.startsWith('#chat')||!location.hash||location.hash==='#home'?'chat':'work';
      if(mode==='work'&&parts[0]==='partners'){
        v.mode='work';v.detail=null;v.dialogue=null;v.activeChat=null;v.mobileSidebar=false;v.workLoading=false;
        shell();window.WorkdeskReact.partnerWork($('#main'),{runId:parts[1]==='run'?parts[2]:undefined});renderSidebar();return;
      }
      // Capture the previous composer before changing modes. Refresh drafts and
      // read lightweight Work catalogs in parallel; never dispatch from a read.
      const ready=mode==='work'?Promise.all([features.beforeRender(),load({refresh:false,generation})]):features.beforeRender();
      if(mode==='work'){
        v.mode=mode;v.detail=null;v.dialogue=null;v.activeChat=null;v.mobileSidebar=false;v.workLoading=true;v.workPlanOnly=false;
        if(!parts.length){
          if(!scenes[v.scene])v.scene='research';v.input='';v.files='';v.attachments=[];v.formDraft={};
          draw();
        }else {shell();$('#main').innerHTML='<div class="loading"><span class="loader"></span>正在读取工作记录…</div>';}
      }
      await ready;if(generation!==v.generation)return;
      v.mode=mode;v.detail=null;v.dialogue=null;v.activeChat=null;v.mobileSidebar=false;v.workLoading=false;
      if(mode==='chat'){
        if(features.special())return;
        if(parts[0]==='draft'){await features.applyDraft(parts[1]);v.activeChat=null;draw();return;}
        const id=location.hash.slice(1).split('/')[1];v.activeChat=id?(v.chats.find(c=>c.id===id||c.legacy_id===id)?.id||null):null;
        if(v.activeChat){
          const c=v.chats.find(row=>row.id===v.activeChat);
          v.chatProvider=c.provider;v.chatModel=c.config?.model||'';v.chatRoute=c.route==='api'?'api':'web';
          if(c.route==='api'){v.chatApiProvider=c.provider;v.chatApiModel=c.config.model;v.chatThinking=!!c.config.thinking;}
          if(c.id!==previousChat&&c.forked_from&&c.route==='web'&&!c.web_url)v.chatEmbedded=false;
        }
        if(insideProjectChat()&&!v.co){
          await load({refresh:false,generation});
          if(generation!==v.generation)return;
        }
        features.restore('chat',v.activeChat);
        if(v.activeChat&&!v.chatInput)v.chatInput=v.chats.find(c=>c.id===v.activeChat)?.prompt||'';
        if(!v.activeChat){
          if(!v.chatInput)Object.assign(v,chatDefaults());
          if(chatEntry?.route==='web')Object.assign(v,{chatRoute:'web',chatProvider:chatEntry.provider,chatEmbedded:false});
          if(chatEntry?.route==='api'){
            const apiService=features.chatPresentation().apiServices.find(row=>row.provider===chatEntry.provider);
            if(apiService)Object.assign(v,{chatRoute:'api',chatApiProvider:apiService.provider,chatApiModel:apiService.models.includes(v.chatApiModel)?v.chatApiModel:apiService.default_model,chatEmbedded:false});
          }
        }
        draw();features.pollChat();return;
      }
      if(features.special())return;
      const [kind,id,scene]=parts;
      if((kind==='task'||kind==='dialogue')&&display(kind,id).deleted){toast('该Work记录已删除，执行审计仍保留。');await go('#work');return;}
      if(kind==='task'){
        const detail=await independent.readTask(id,true);if(generation!==v.generation)return;if(detail.task.spec.cooperative&&detail.task.spec.execution!=='partner_native'&&!detail.partner_run_id&&!detail.task.partner_run_id&&!detail.task.spec.partner_run_id){location.hash='#control/task/'+encodeURIComponent(id);return;}v.detail=detail;const t=detail.task;v.scene=t.spec.work_scene||'legacy';v.project=t.project_id;v.provider=t.spec.provider;v.model=t.spec.model;v.workAccess=!t.spec.access||t.spec.access==='inherit'?'workspace':t.spec.access;v.workEffort=t.spec.effort;v.workTier=t.spec.tier;v.type=t.spec.work_mode||'independent';v.files=Object.keys(t.spec.materials||{}).join('\n');v.attachments=t.spec.attachments||[];
      }else if(kind==='dialogue'){
        v.dialogue=v.co.dialogues.find(d=>d.id===id);if(!v.dialogue)throw Error('讨论记录不存在。');v.scene=v.dialogue.work_scene||'research';v.project=v.dialogue.project_id;v.type='cooperative';v.roles={...v.roles,...v.dialogue.work_roles};v.provider=v.dialogue.config.provider;v.files=v.dialogue.files.join('\n');v.attachments=v.dialogue.attachments||[];v.dialogueRuntime=v.dialogue.pending?await call('task?id='+encodeURIComponent(v.dialogue.pending)):null;
      }else if(kind==='records'||kind==='project'||kind==='archives'){
        $('#main').innerHTML=recordPage(kind,id,scene);renderSidebar();return;
      }else if(kind==='draft'){await features.applyDraft(id);}
      else{
        if(!scenes[v.scene])v.scene='research';v.input='';v.files='';v.attachments=[];v.formDraft={};v.workAccess=defaultAccess(v.provider);
        const restored=features.restore('work');
        const instance=document.documentElement.dataset;
        const developmentProject=!restored&&!v.project&&v.type==='independent'&&instance.workdeskChannel==='development'?
          v.data.projects.find(row=>row.id===instance.workdeskDefaultProject&&!row.removed):null;
        if(developmentProject){
          v.project=developmentProject.id;
          if(scenes[developmentProject.scene])v.scene=developmentProject.scene;
        }
        if(v.type==='independent'&&!v.input&&!v.files&&!v.attachments.length&&!v.formDraft.resourceSelection){
          const defaults=v.data.execution_resources?.connections||[];
          const available=partnerConnections()?Object.fromEntries(partnerConnections().connections.filter(row=>row.available).map(row=>[row.partner_id,true])):connections||Object.fromEntries(defaults.filter(row=>row.is_default&&row.available).map(row=>[row.provider,true]));
          const preferred=window.WorkdeskSettingsPreferences?.get().workProvider||'codex';
          const fallback=Object.keys(names).find(id=>available[id]);
          if(newWork||!restored)useWorkDefault(available[preferred]||!fallback?preferred:fallback);
          else if((partnerConnections()?!available[v.provider]:connections&&!connections[v.provider])&&fallback)useWorkDefault(fallback);
        }
      }
      if(kind==='task'||kind==='dialogue')features.restore('work',id);
      if(generation!==v.generation)return;draw();poll();
    }catch(error){if(generation===v.generation){v.workLoading=false;$('#main').innerHTML=errorPage(error.message)+'<p class="wk-content"><a href="#settings">设置与服务状态</a></p>';renderSidebar();}}
  }
  function draw(){
    if(v.mode==='work'&&v.type==='independent'&&!v.detail)independent.normalize();
    const c=v.chats.find(c=>c.id===v.activeChat);
    if(window.WorkdeskReact&&v.mode==='chat'){
      const chat=features.chatPresentation();
      window.WorkdeskReact.chat($('#main'),{v:{...v},chat});renderSidebar();return;
    }
    if(window.WorkdeskReact&&!(v.mode==='chat'&&c?.route==='web')){
      const chat=features.chatPresentation();
      window.WorkdeskReact.conversation($('#main'),{v:{...v},scenes,names,isLocked:locked(),controls:features.runtimeControls(v.detail||v.dialogueRuntime),independent:independent.presentation(),cooperation:v.type==='cooperative'?cooperationSummary():null,chat});
    }else $('#main').innerHTML=v.mode==='chat'?chatPage():workPage();
    renderSidebar();
  }
  function syncComposer(){
    if(window.WorkdeskReact&&window.WorkdeskModules.currentOwner()==='workbench'&&$('#main [data-assistant-ui],#main [data-chat-surface]'))draw();
  }
  function chatPage(){
    return features.chatPage();
  }
  function options(list,value){return list.map(x=>`<option value="${h(x)}" ${x===value?'selected':''}>${h(x)}</option>`).join('');}
  function sceneControl(){return `<button class="wk-chip" data-wb-menu="scene" aria-label="选择工作场景" ${locked()?'disabled':''}>${icon(scenes[v.scene]?.icon||'folder')}<span>${scenes[v.scene]?.name||'未分类'}</span>${icon(locked()?'lock':'chevron')}</button>`;}
  function modelControl(){
    if(v.type==='cooperative')return `<button type="button" class="wk-chip" data-wb="${v.dialogue?'roles':'cooperation-settings'}" aria-label="合作方式与分工">${icon('workflow')}<span>${v.dialogue?'协作分工':h(cooperationSummary())}</span>${icon('chevron')}</button>`;
    if(!v.detail&&partnerConnections())return `<select class="wk-model-select" id="wb-provider" aria-label="选择工作端">${partnerConnections().partners.map(row=>`<option value="${h(row.id)}" ${v.provider===row.id?'selected':''}>${h(row.name)}</option>`).join('')}</select><select class="wk-model-select" id="wb-connection" aria-label="选择工作连接与模型"><option value="">选择连接</option>${partnerConnections().connections.filter(row=>row.partner_id===v.provider).map(row=>`<option value="${h(row.id)}" ${v.connectionId===row.id?'selected':''} ${row.available?'':'disabled'}>${h((row.model_label||row.model||'官方默认模型')+' · '+(row.source_label||'官方账号')+' · '+row.account_label+(row.available?'':' · '+row.reason))}</option>`).join('')}</select>`;
    const p=profile();return `<select class="wk-model-select" id="wb-provider" aria-label="选择工作端" ${locked()?'disabled':''}>${Object.entries(names).map(([k,n])=>`<option value="${k}" ${v.provider===k?'selected':''}>${n}</option>`).join('')}</select><select class="wk-model-select" id="wb-model" aria-label="选择工作模型" ${locked()?'disabled':''}>${options(p?.enabled_models||p?.models||[],v.model)}</select>${locked()?`<span class="wk-lock" title="独立工作已开始，工作端和模型固定">${icon('lock')}</span>`:''}`;
  }
  function thread(){
    if(v.dialogue)return features.runtimeControls(v.dialogueRuntime)+v.dialogue.messages.map(m=>m.role==='human'?`<div class="wk-user"><p>${h(m.content)}</p></div>`:answer(m.content,names[v.dialogue.config.provider])).join('')+(v.dialogue.pending?'<p class="wk-note">统筹 AI 正在处理。<a href="#control/task/'+v.dialogue.pending+'">查看运行与审批</a></p>':'');
    const d=v.detail,t=d.task;
    const messages=d.attempts.map(attempt=>{
      const note=d.events.find(e=>e.kind==='execution_reserved'&&e.attempt_id===attempt.id)?.payload.human_note;
      const output=attempt.result?.answer||d.events.filter(e=>e.kind==='model_delta'&&e.attempt_id===attempt.id).map(e=>e.payload.text||'').join('');
      return `${note?`<div class="wk-user"><p>${h(note)}</p></div>`:''}${output?answer(typeof output==='string'?output:JSON.stringify(output),names[t.spec.provider]):`<p class="wk-note">等待可展示的回复 · ${h(attempt.state)}</p>`}`;
    }).join('');
    return `<div class="wk-user"><p>${h(t.spec.goal)}</p></div>${messages||'<p class="wk-note">任务草稿已保存，尚未开始。</p>'}${features.runtimeControls(d)}${d.approvals.some(a=>a.state==='PENDING')||d.native_requests.some(a=>a.state==='PENDING')?`<div class="wb-attention">${icon('circle')}此任务需要处理审批或补充输入。<a href="#control/task/${t.id}" class="wk-button">处理</a></div>`:''}`;
  }
  function answer(text,name){return `<div class="wk-answer"><span class="wk-avatar">${icon('sparkles')}</span><div><div class="wk-byline">${h(name||'AI')}</div><div class="markdown wb-answer">${markdown(text)}</div></div></div>`;}
  function workPage(){
    const active=v.detail||v.dialogue;const isLocked=locked();
    return `<section class="wk-conversation ${active?'':'is-empty'}" aria-label="Work 对话区">${active?`<div class="wk-active-bar">${sceneControl()}<span class="wk-faint">${v.type==='independent'?'独立工作':'合作工作'}</span>${b('工作详情','details')}${v.dialogue?`<a class="wk-button" href="#collab/dialogue/${v.dialogue.id}">方案与队列</a>`:`<a class="wk-button" href="#control/task/${task().id}">运行与控制</a>`}</div><div class="wk-thread">${thread()}</div>`:''}<div class="wk-home">${active?'':`<div class="wk-greeting"><h1>我们要做什么？</h1><p>从一个目标开始，把事情一步步完成。</p></div><div class="wk-setup">${sceneControl()}<div class="wk-work-type" aria-label="工作方式"><button data-wb-type="independent" aria-pressed="${v.type==='independent'}">独立</button><button data-wb-type="cooperative" aria-pressed="${v.type==='cooperative'}">合作</button></div></div>`}<form id="wb-work-form"><div class="wk-composer-group"><div class="wk-composer"><textarea id="wb-work-input" rows="2" aria-label="工作目标" placeholder="${active?'补充要求，继续推进这项工作…':'描述目标，或说说你想做什么…'}">${h(v.input)}</textarea><div class="wk-composer-tools">${ib('添加工作文件','files','plus')}<div class="wk-model-controls">${modelControl()}</div><button type="submit" class="wk-send" aria-label="${active?'发送工作消息':'准备开始工作'}" ${v.busy?'disabled':''}>${icon('up')}</button></div></div><div class="wk-shelf"><button class="wk-chip wk-project-chip" data-wb-menu="project" type="button" aria-label="选择工作区或项目" ${active?'disabled':''}>${icon('folder')}<span>${h(project()?.name||'选择工作区')}</span>${icon('chevron')}</button><button type="button" class="wk-chip" data-wb="files">${icon('file')}文件${lines(v.files).length+v.attachments.length?' · '+(lines(v.files).length+v.attachments.length):''}</button><button type="button" class="wk-chip" data-wb="skills">${icon('plug')}插件与 Skills</button></div></div></form><p class="wk-hint">${isLocked?'当前工作场景已固定'+(v.type==='independent'?' · 当前模型已固定':''):active?'草稿尚未开始，可修订工作配置':'开始前可调整场景与分工'}</p>${v.pending?`<p class="wk-note">最近命令 ${h(v.pending.id)} · <a href="#control/receipts">查询回执</a></p>`:''}</div></section>`;
  }
  function recordPage(kind,id,scene){
    let rows=records().filter(r=>kind==='archives'?r.archived:!r.archived);
    if(kind==='project')rows=rows.filter(r=>(r.project||'unfiled')===id&&(!scene||(r.group_scene||(scenes[r.scene]?r.scene:'legacy'))===scene));
    const title=kind==='project'?v.data.projects.find(p=>p.id===id)?.name||'项目':kind==='archives'?'已归档':'工作记录';
    return `<section class="wk-content"><div class="wk-page-title"><h2>${h(title)}</h2>${b('新工作',kind==='project'&&id!=='unfiled'?'project-new-work':'new',kind==='project'?'data-project="'+h(id)+'"':'')}</div>${[...Object.entries(scenes),['general',{name:'普通项目',icon:'folder'}],['legacy',{name:'未分类的旧记录',icon:'history'}]].map(([key,s])=>{const list=rows.filter(r=>(r.group_scene||(scenes[r.scene]?r.scene:'legacy'))===key);return list.length?`<h3>${icon(s.icon)} ${s.name}</h3>${list.map(r=>`<a class="wk-list-row" href="${href(r.kind,r.id)}">${icon(r.kind==='dialogue'?'workflow':'chat')}<span>${h(r.title)}<small>${h(v.data.projects.find(p=>p.id===r.project)?.name||'未归入项目')} · ${h(r.state)}</small></span>${icon('arrow')}</a>`).join('')}`:'';}).join('')||'<p class="wk-note">暂无记录</p>'}</section>`;
  }
  function poll(){
    const id=task()?.id||v.dialogue?.pending;if(!id)return;
    const activeStates=new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
    if(v.detail&&!activeStates.has(v.detail.task.state)&&!v.detail.has_more_events)return;
    const generation=v.generation;
    v.timer=setTimeout(async()=>{try{
      if(v.dialogue){const data=await call('collaboration?view=workbench');if(generation!==v.generation)return;v.co=data;v.dialogue=data.dialogues.find(d=>d.id===v.dialogue.id);v.dialogueRuntime=v.dialogue.pending?await call('task?id='+encodeURIComponent(v.dialogue.pending)):null;}
      else{const detail=await independent.readTask(id);if(generation!==v.generation)return;v.detail=detail;}
      // Keep a composing user input and open dialog intact during progress updates.
      if(!$('#wb-dialog').open&&!v.menu){if(window.WorkdeskReact)draw();else{const threadElement=$('#main .wk-thread');if(threadElement)threadElement.innerHTML=thread();renderSidebar();}}
      poll();
    }catch(error){if(generation===v.generation)toast('更新失败，状态保持待核对：'+error.message);}},1500);
  }
  function closeMenu(){v.anchor?.setAttribute('aria-expanded','false');v.menu=null;v.anchor=null;$('#wb-overlays')?.replaceChildren();}
  function item(label,action,glyph,attrs=''){return `<button class="wk-menu-item" data-wb="${action}" ${attrs}>${icon(glyph)}<span>${label}</span></button>`;}
  function link(label,target,glyph){return `<a class="wk-menu-item" href="${target}">${icon(glyph)}<span>${label}</span></a>`;}
  const topMenus={file:'文件',edit:'编辑',view:'视图',help:'帮助',more:'更多功能',app:'应用与设置'};
  const diagnosticsLink=()=>link('帮助与诊断','#settings/diagnostics','circle');
  function openMenu(kind,anchor){
    if(v.menu===kind&&v.anchor===anchor){closeMenu();return;}closeMenu();v.menu=kind;v.anchor=anchor;anchor.setAttribute('aria-expanded','true');v.projectQuery='';let body='';
    if(kind==='scene')body=`<div class="wk-popover-title">选择工作场景</div>${Object.entries(scenes).map(([k,s])=>`<button class="wk-menu-item" data-wb-scene="${k}" ${locked()?'disabled':''}>${icon(s.icon)}<span>${s.name}<small>${s.hint}</small></span>${v.scene===k?icon('check'):''}</button>`).join('')}<div class="wk-menu-divider"></div><a class="wk-menu-item" href="#extensions/scenes">${icon('settings')}管理场景与能力预设</a>`;
    else if(kind==='chat-record')body=chatRecords.menu(anchor.dataset.id,item);
    else if(kind==='work-record'||kind==='work-project')body=workRecords.menu(anchor,item);
    else if(kind==='project')body=`<label class="wk-search">${icon('search')}<input id="wb-project-search" aria-label="搜索项目" placeholder="搜索项目"></label>${item('新建项目','new-project','plus')}${item('添加已有工作区','register-project','folder')}<div class="wk-menu-divider"></div><div id="wb-project-results">${projectResults()}</div><div class="wk-menu-divider"></div>${item('不在项目中工作','unfiled','close')}`;
    else if(kind==='work-tools'){
      const count=lines(v.files).length+v.attachments.length;
      const documents=partnerDocumentSkills(),noMcp=partnerMcpUnavailable(),capabilityHint=documents?'随本次工作读取文档':task()||v.dialogue?'查看工作已加载的能力':'随本次工作加载';
      body=item('文件和文件夹'+(count?' · '+count:''),'files','file')+
        item((documents?'技能文档':'Skills')+'<small>'+capabilityHint+'</small>','work-capabilities','plug','data-kind="skill"')+
        item('MCP<small>'+(noMcp?'当前连接暂不支持本次加载 MCP':task()||v.dialogue?'查看已选 MCP':'随本次工作加载')+'</small>','work-capabilities','plug','data-kind="mcp"'+(noMcp?' disabled':''))+
        '<div class="wk-menu-divider"></div>'+
        item('计划模式<small>'+(v.workPlanOnly?'已开启 · 点击关闭':'先出方案，再决定是否执行')+'</small>',
          'work-plan-mode','workflow',canPlan()?'':'disabled')+
        item('引用其他聊天<small>预览并插入正文</small>','work-context','chat');
    }
    else if(kind==='app')body=`<div class="wk-menu-item">${icon('cpu')}<span>Workdesk<small>本地工作台</small></span></div><div class="wk-menu-divider"></div>${link('连接账号与 API','#connect','cpu')}${link('账号与模型','#settings/accounts','cpu')}${link('用量与预算','#settings/usage','history')}${link('插件与 Skills','#settings/extensions','plug')}${item('切换明暗主题','theme','sun')}${link('设置','#settings','settings')}<div class="wk-menu-divider"></div>${diagnosticsLink()}${link('运行与恢复','#settings/operations','refresh')}`;
    else if(kind==='more')body=item('新建页面','more-tool','edit','data-tool="page"')+
      item('新建演示文稿','more-tool','panel','data-tool="presentation"')+item('打开文件','more-tool','folder','data-tool="files"')+
      item('侧边聊天','sidechat','chat');
    else if(kind==='conversation'){
      const chat=v.chats.find(c=>c.id===v.activeChat),record=chat||records().find(r=>r.id===(v.dialogue?.id||task()?.id));
      body=item('重命名','rename','edit')+item(record?.pinned?'取消置顶':'置顶','pin','pin')+
        item('移动到项目','move-project','folder')+(record?.list_project||record?.project?item('移出项目','remove-project','close'):'')+
        '<div class="wk-menu-divider"></div>'+(v.mode==='work'&&v.type==='independent'&&v.detail?.task.spec.runtime_mode==='official'?item('工作端控制','native-panel','panel'):'')+item('新建侧边聊天','sidechat','chat')+
        item(chat?'在新聊天中开启分支':'分叉为新工作草稿','fork','workflow',chat?.pending?'disabled':'')+item('计划任务','schedule','history')+
        '<div class="wk-menu-divider"></div>'+item('分享 / 导出','share','file')+item('复制链接','copy-link','link')+
        item('复制对话文字','copy','copy')+item('在新窗口中打开','window','panel')+
        '<div class="wk-menu-divider"></div>'+item(record?.archived?'取消归档':'归档',record?.archived?'unarchive':'archive','save');
    }
    else if(kind==='file')body=`${item('新建对话','new','edit')}${item('新建项目','new-project','plus')}${item('添加已有工作区','register-project','folder')}${link('草稿箱','#drafts','file')}`;
    else if(kind==='edit')body=`${item('搜索对话','search','search')}${item('重命名当前对话','rename','edit')}${item('复制当前对话','copy','copy')}`;
    else if(kind==='view')body=`${item('显示或收起侧栏','sidebar','panel')}${item('切换明暗主题','theme','sun')}`;
    else body=`${item('使用说明','help','circle')}${diagnosticsLink()}`;
    const menuClass=({project:'wd-project-picker','work-tools':'wd-composer-menu',more:'wd-more-features'})[kind]||'';
    const label=({'work-tools':'工作功能',scene:'选择工作场景',project:'选择项目',conversation:'对话操作','chat-record':'聊天记录操作','work-record':'工作记录操作','work-project':'项目操作',...topMenus})[kind]||'更多操作';
    const menuHtml=`<div class="wk-popover wb-floating ${menuClass}" role="${kind==='project'?'dialog':'menu'}" aria-label="${label}">${body}</div>`;
    $('#wb-overlays').innerHTML=['project','work-record','work-project','work-tools'].includes(kind)?`<div data-ui-module="workbench" style="display:contents">${menuHtml}</div>`:menuHtml;
    const p=$('#wb-overlays .wk-popover'),r=anchor.getBoundingClientRect();p.style.visibility='hidden';
    requestAnimationFrame(()=>{if(!p.isConnected)return;const height=Math.min(p.scrollHeight,innerHeight-24),left=Math.max(8,Math.min(r.left,innerWidth-p.offsetWidth-8));p.style.left=left+'px';p.style.top=(r.bottom+height+8<innerHeight?r.bottom+5:Math.max(8,r.top-height-5))+'px';p.style.visibility='visible';});
    const entries=()=>[...p.querySelectorAll('button:not(:disabled),a[href]')].filter(entry=>entry.getClientRects().length);
    if(kind!=='project')entries().forEach(entry=>entry.setAttribute('role','menuitem'));
    p.addEventListener('keydown',event=>{
      if(event.target.matches('input,textarea,select')&&!['ArrowDown','ArrowUp'].includes(event.key))return;
      const items=entries(),i=items.indexOf(document.activeElement),last=items.length-1;
      if(!items.length)return;
      const next={ArrowDown:(i+1)%items.length,ArrowUp:i<0?last:(i-1+items.length)%items.length,Home:0,End:last}[event.key];
      if(next===undefined)return;
      event.preventDefault();items[next]?.focus();
    });
    p.addEventListener('focusout',event=>{if(event.relatedTarget&&!p.contains(event.relatedTarget))closeMenu();});
    requestAnimationFrame(()=>{if(p.isConnected)(p.querySelector('input')||entries()[0])?.focus();});
  }
  function projectResults(){return (v.data?.projects||[]).filter(p=>(p.name+' '+p.root).toLowerCase().includes(v.projectQuery.toLowerCase())).map(p=>`<button class="wk-menu-item" data-wb-project="${p.id}" title="${h(p.name+'\n'+p.root)}">${icon('folder')}<span class="wd-project-option-copy"><strong>${h(p.name)}</strong><small>${h(p.root)}</small></span>${v.project===p.id?icon('check'):''}</button>`).join('')||'<p class="wk-note">没有匹配的项目</p>';}
  function modal(title,body,actions){
    closeMenu();const d=$('#wb-dialog');if(d.open)d.close();
    delete d.dataset.uiModule;delete d.dataset.workProjectDialog;delete d.dataset.nativeControlDialog;
    delete d.dataset.workspaceTool;d.removeAttribute('aria-label');
    d.setAttribute('aria-labelledby','wb-dialog-title');
    d.innerHTML=`<div class="wk-dialog-head"><h2 id="wb-dialog-title">${title}</h2>${ib('关闭弹窗','close','close')}</div>${body}<p id="wb-modal-error" role="alert" class="wk-error" hidden></p><div class="wk-dialog-actions">${actions||b('关闭','close')}</div>`;d.showModal();
  }
  function openWorkspaceTool(tool,onSaved){
    const titles={page:'新建页面',presentation:'新建演示文稿',files:'打开文件'};
    if(!Object.hasOwn(titles,tool))return;
    const opener=v.anchor;
    const projectId=WorkdeskModules.currentOwner()==='workbench'?v.project:window.WorkdeskWorkItemsPage?.projectId();
    modal(titles[tool],'<div data-wb-workspace-tool></div>',b('完成','close'));
    const dialog=$('#wb-dialog');
    const host=dialog.querySelector('[data-wb-workspace-tool]');
    dialog.dataset.uiModule='workbench';
    dialog.dataset.workspaceTool=tool;
    dialog.setAttribute('aria-label',titles[tool]);
    dialog.addEventListener('close',()=>{
      window.WorkdeskReact.release(host);
      if(!dialog.open){
        dialog.removeAttribute('aria-label');
        delete dialog.dataset.workspaceTool;
        if(opener?.isConnected)opener.focus();
      }
    },{once:true});
    window.WorkdeskReact.workspaceTool(host,{tool,projectId,
      onSaved:onSaved&&((item)=>{
        if(!dialog.open||!host.isConnected)return;
        dialog.close();onSaved(item);
      })});
  }
  async function capabilityDialog(kind){
    const documents=partnerDocumentSkills(),noMcp=partnerMcpUnavailable(),heading=kind==='skill'?(documents?'选择技能文档':'选择 Skills'):kind==='mcp'?'选择 MCP':'本次工作能力';
    if(noMcp&&kind==='mcp'){modal('本次加载 MCP','<p class="wk-note">当前连接暂不支持本次加载 MCP。</p>',b('完成','close'));return;}
    if(task()||v.dialogue){
      const snapshot=task()?.spec.scene_snapshot;
      const skillNames=snapshot?.bundles.flatMap(bundle=>bundle.skills.map(skill=>skill.name))||[];
      const mcpNames=snapshot?.mcp.map(server=>server.label)||[];
      const entries=(kind==='skill'?skillNames:kind==='mcp'?mcpNames:[...skillNames,...mcpNames]);
      modal(heading,entries.length?entries.map(name=>'<p class="wk-note">'+h(name)+'</p>').join(''):
        '<p class="wk-note">这段工作未通过 Workdesk 加载该类能力。</p>',
        (task()?.spec.runtime_mode==='official'?b('官方工作端控制','native-panel'):'')+b('完成','close'));
      return;
    }
    const generation=v.generation;
    const data=await call('extensions');
    if(generation!==v.generation)return;
    const skills=data.capabilities.filter(bundle=>bundle.enabled&&bundle.kind==='skill').flatMap(bundle=>
      bundle.skills.map(skill=>({kind:'skill',id:bundle.id,revision:bundle.revision,name:skill.name,description:skill.description})));
    const servers=data.extensions.filter(server=>server.enabled).map(server=>
      ({kind:'mcp',id:server.id,revision:server.revision,name:server.name,description:server.source}));
    capabilityOptions=[...skills,...(noMcp?[]:servers)].filter(row=>kind==='all'||row.kind===kind);
    const rows=capabilityOptions.map((row,index)=>{
      const chosen=composerCapabilities().some(ref=>ref.kind===row.kind&&ref.id===row.id&&ref.name===row.name&&ref.revision===row.revision);
      return '<label class="wk-check wd-composer-option"><input type="checkbox" name="capability" value="'+index+'" '+(chosen?'checked':'')+
        '><span><strong>'+h(row.name)+'</strong><small>'+h(row.description||'')+'</small></span></label>';
    }).join('');
    modal(heading,(documents&&kind!=='mcp'?'<p class="wk-note">技能文档由所选工作端的文件工具读取。</p>':'')+'<form id="wb-composer-capabilities" data-kind="'+kind+'">'+(rows||'<p class="wk-note">暂无已启用的'+(kind==='mcp'?' MCP。':documents?'技能文档。':' Skills。')+'</p>')+'</form>',
      '<a class="wk-button" href="#settings/extensions" data-wb-close-link>管理 Skills 与 MCP</a>'+b('取消','close')+
      '<button class="wk-button primary" type="submit" form="wb-composer-capabilities">保存选择</button>');
    $('#wb-dialog').dataset.uiModule='workbench';
  }
  function saveCapabilities(form){
    const kind=form.dataset.kind;
    const choices=new FormData(form).getAll('capability').map(index=>capabilityOptions[Number(index)]).filter(Boolean);
    const preserved=composerCapabilities().filter(ref=>kind!=='all'&&ref.kind!==kind);
    v.formDraft.composerCapabilities=[...preserved,...choices.map(({kind,id,revision,name})=>({kind,id,revision,name}))];
    features.autosave();
    $('#wb-dialog').close();
    draw();
  }
  function contextResults(value){
    const needle=value.trim().toLocaleLowerCase();
    const candidates=[...v.chats.filter(c=>!c.archived&&c.id!==v.activeChat).map(c=>({...c,kind:'chat'})),
      ...records().filter(row=>['task','dialogue'].includes(row.kind)&&!row.archived&&!row.deleted&&row.id!==task()?.id&&row.id!==v.dialogue?.id)];
    return candidates.filter(row=>row.title.toLocaleLowerCase().includes(needle)).slice(0,30).map(row=>
      item(h(row.title)+'<small>'+h(row.kind==='chat'?names[row.provider]+' · 聊天':'工作对话')+'</small>',
        'work-context-preview','chat','data-id="'+h(row.id)+'" data-kind="'+row.kind+'"')).join('')||'<p class="wk-note">没有匹配的聊天。</p>';
  }
  function contextDialog(){
    modal('引用其他聊天','<label class="wk-field">搜索聊天<input id="wb-context-search" class="wk-input" placeholder="按标题搜索"></label>'+
      '<div id="wb-context-results" class="wd-composer-context-list">'+contextResults('')+'</div>');
    $('#wb-dialog').dataset.uiModule='workbench';
    $('#wb-context-search').focus();
  }
  async function contextPreview(el){
    const generation=v.generation, dialog=$('#wb-dialog');
    const row=await call('conversation?kind='+encodeURIComponent(el.dataset.kind)+'&id='+encodeURIComponent(el.dataset.id));
    if(generation!==v.generation||!dialog.open||!el.isConnected)return;
    const content=(row.messages||[]).filter(message=>message.content?.trim()).map(message=>
      (message.role==='human'?'我':'回复')+'：'+message.content).join('\n\n');
    if(!content)throw Error('此聊天没有可引用的本地正文。');
    const excerpt=content.slice(-10000);
    modal('预览聊天引用','<form id="wb-context-quote"><label class="wk-field">'+h(row.title)+
      '<textarea name="quote" class="wk-input" rows="9" maxlength="12000" required>'+h('【引用聊天：'+row.title+'】\n'+excerpt)+'</textarea></label>'+
      (excerpt.length<content.length?'<p class="wk-note">已载入最近 10,000 字，可以编辑后加入。</p>':'')+'</form>',
      b('返回列表','work-context')+'<button class="wk-button primary" type="submit" form="wb-context-quote">插入输入框</button>');
  }
  function rolesDialog(){v.roleDraft={...v.roles};const legacyNames=Object.fromEntries(['codex','deepseek','qwen'].map(id=>[id,names[id]]));modal('协作分工',Object.entries(roleNames).map(([key,[name,hint,glyph]])=>`<div class="wk-role-row"><div class="wk-role-copy"><div class="wk-role-name">${icon(glyph)}<label for="wb-role-${key}">${name}</label></div><p>${hint}</p></div><select class="wk-role-select" id="wb-role-${key}" data-wb-role="${key}" ${locked()?'disabled':''}>${Object.entries(legacyNames).map(([k,n])=>`<option value="${k}" ${v.roles[key]===k?'selected':''}>${n}</option>`).join('')}</select></div>`).join('')+'<p class="wk-role-hint">'+icon('circle')+'此处沿用原版讨论的分工。新合作可在主 Work 选择伙伴连接。</p>',b('取消','close')+b(locked()?'完成':'保存分工',locked()?'close':'save-roles','',true));}
  const cooperationModes={fixed:'固定流程',leader:'负责人带队',host_experts:'主持人—专家'};
  const pdcaStages=['plan','do','check','act'];
  const pdcaGroup={title:'PDCA 阶段',fields:[['plan','P · 计划'],['do','D · 执行'],['check','C · 检查'],['act','A · 改进']]};
  const discussionExperts=Array.from({length:4},(_,index)=>['expert_'+(index+1),'专家 '+(index+1)]);
  const cooperationStaffing={
    fixed:[pdcaGroup],
    leader:[{title:'第 0 轮 · 需求讨论',fields:[['coordinator','负责人']]},pdcaGroup],
    host_experts:[{title:'第 0 轮 · 需求讨论',fields:[['coordinator','主持人'],...discussionExperts]},pdcaGroup]
  };
  const staffingFields=mode=>cooperationStaffing[mode].flatMap(group=>group.fields);
  const cooperationFamilies=names;
  const connectionModelKey=ref=>ref?.connection_id?JSON.stringify([ref.connection_id,String(ref.connection_revision),ref.model]):ref?.model||'';
  const cooperationModelName=entry=>entry.display_name||entry.model
    .replace(/^gpt-/i,'GPT-').replace(/-(sol|luna|astra|terra)$/i,(_,name)=>' '+name[0].toUpperCase()+name.slice(1));
  function cooperationModels(){
    if(partnerConnections())return partnerConnections().partners.map(partner=>{
      const rows=partnerConnections().connections.filter(row=>row.partner_id===partner.id);
      const models=rows.filter(row=>row.available).map(row=>{
        const model=row.model||'official-default',ref={provider:partner.id,model,effort:'default',tier:'standard',connection_id:row.id,connection_revision:String(row.revision)};
        return {model,key:connectionModelKey(ref),label:(row.model_label||row.model||'官方默认模型')+' · '+(row.route==='api'?(row.api_name||row.api_provider)+' API':'官方账号')+' · '+row.account_label,ref};
      });
      return {id:partner.id,label:partner.name,models,defaultModel:models[0],connected:models.length>0,reason:rows.find(row=>row.reason)?.reason};
    });
    const resources=v.data?.execution_resources;
    return Object.entries(cooperationFamilies).map(([id,label])=>{
      const p=profile(id);
      const connection=resources?.connections.find(c=>c.provider===id&&c.is_default);
      const models=connection?.available?(p?.model_catalog?.models||[]).filter(entry=>
        entry.runnable&&(entry.selection_efforts??entry.efforts)?.length&&entry.tiers?.length
      ).map(entry=>{
        const efforts=entry.selection_efforts??entry.efforts;
        return {model:entry.model,label:cooperationModelName(entry),
          ref:{provider:id,model:entry.model,effort:efforts.includes(p.effort)?p.effort:efforts[0],
            tier:entry.tiers.includes(p.tier)?p.tier:entry.tiers[0],connection_id:connection.id,connection_revision:connection.revision}};
      }):[];
      const defaultModel=models.find(entry=>entry.model===p?.model)||models[0];
      return {id,label,models,defaultModel,connectionId:connection?.id,connected:!!connection?.available};
    });
  }
  function cooperationConfig(){
    const families=cooperationModels(),first=families.find(family=>family.models.length);
    const blank={provider:'',model:''};
    const defaultChoice=first?{...first.defaultModel.ref}:blank;
    const saved=v.formDraft.cooperation||{};
    const mode=Object.hasOwn(cooperationModes,saved.mode)?saved.mode:'fixed';
    const rounds=[1,2,3,4].includes(saved.rounds)?saved.rounds:1;
    const expertCount=[2,3,4].includes(saved.expertCount)?saved.expertCount:2;
    const choice=value=>{
      if(value===undefined)return defaultChoice;
      if(value&&typeof value==='object'&&Object.hasOwn(cooperationFamilies,value.provider)&&typeof value.model==='string'){
        if(value.connection_id)return {...value};
        const matches=families.find(row=>row.id===value.provider)?.models.filter(row=>row.model===value.model)||[];
        return matches.length===1?{...matches[0].ref}:{provider:value.provider,model:value.model};
      }
      const old=v.data?.execution_resources?.members.find(member=>member.id===value);
      if(!old)return blank;
      const family=families.find(row=>row.id===old.provider);
      return {...(family?.models.find(row=>row.model===old.model&&row.ref.connection_id===old.connection_id)?.ref||{provider:old.provider,model:''})};
    };
    const assignments=Object.fromEntries(Object.keys(cooperationModes).map(key=>{
      const previous=key===mode?{...saved.stages,coordinator:saved.coordinator??saved.stages?.plan}:{};
      const staff=saved.assignments?.[key]||previous;
      const legacy=key==='leader'?{plan:staff.coordinator}:key==='host_experts'?{expert_1:staff.plan}:{};
      const source=saved.expertCount===undefined?{...legacy,...staff}:staff;
      return [key,Object.fromEntries(staffingFields(key).map(([field])=>[field,choice(source[field])]))];
    }));
    return {families,mode,rounds,expertCount,assignments};
  }
  const loopLabel=rounds=>rounds===1?'单轮':'循环 '+rounds+' 轮';
  function cooperationSummary(){
    const {mode,rounds}=cooperationConfig();
    return cooperationModes[mode]+' · '+loopLabel(rounds);
  }
  function modelOptions(family,selected={}){
    const models=family?.models||[];
    const key=connectionModelKey(selected);
    const unavailable=key&&!models.some(entry=>connectionModelKey(entry.ref)===key);
    const placeholder=unavailable?'原型号不可用':models.length?'选择型号':'先选模型系列';
    return '<option value="">'+placeholder+'</option>'+models.map(entry=>
      '<option value="'+h(connectionModelKey(entry.ref))+'" '+(connectionModelKey(entry.ref)===key?'selected':'')+'>'+h(entry.label)+'</option>').join('');
  }
  function roleModelSelect(label,key,selected,families,expertIndex){
    const family=families.find(row=>row.id===selected.provider&&row.models.length);
    const options=families.map(row=>{
      const status=row.models.length?'':row.connected?' · 暂无可用型号':' · 未连接';
      return '<option value="'+row.id+'" '+(row.models.length===0?'disabled':row===family?'selected':'')+'>'+h(row.label+status)+'</option>';
    }).join('');
    return '<div class="wk-pdca-model-row" data-pdca-role="'+key+'" '+(expertIndex?'data-pdca-expert="'+expertIndex+'"':'')+'><span class="wk-pdca-role-label">'+h(label)+'</span>'+
      '<select aria-label="'+h(label+' · 模型系列')+'" class="wk-input" data-pdca-family required>'+
        '<option value="">选择系列</option>'+options+'</select>'+
      '<select aria-label="'+h(label+' · 具体型号')+'" class="wk-input" data-pdca-model required '+(family?'':'disabled')+'>'+
        modelOptions(family,selected)+'</select></div>';
  }
  function staffingPanel(mode,staff,families,active,expertCount){
    const host=mode==='host_experts';
    const sections=host?[{title:'主持人',fields:[cooperationStaffing[mode][0].fields[0]]},
      {title:'专家讨论',fields:discussionExperts},pdcaGroup]:cooperationStaffing[mode];
    const groups=sections.map(({title,fields},index)=>{
      const inputs=fields.map(([key,label])=>roleModelSelect(label,key,staff[key],families,discussionExperts.findIndex(([expert])=>expert===key)+1)).join('');
      const experts=host&&fields===discussionExperts?
        '<label class="wk-pdca-expert-count"><span>专家人数</span><select class="wk-input" name="expert_count">'+
        [2,3,4].map(n=>'<option value="'+n+'" '+(expertCount===n?'selected':'')+'>'+n+' 位</option>').join('')+'</select></label>':'';
      const roles=experts+'<div class="wk-pdca-roles">'+inputs+'</div>';
      if(!host)return '<fieldset class="wk-pdca-stages"><legend>'+h(title)+'</legend>'+roles+'</fieldset>';
      const open=index===0||!window.matchMedia('(max-width:460px)').matches;
      return '<details class="wk-pdca-stages wk-pdca-group" '+(open?'open':'')+'><summary>'+h(title)+
        '<small data-pdca-group-models></small></summary>'+roles+'</details>';
    });
    return '<fieldset class="wk-pdca-mode" data-pdca-mode="'+mode+'" '+(active?'':'hidden disabled')+'>'+groups.join('')+'</fieldset>';
  }
  function cooperationFields({families,mode,rounds,expertCount,assignments}){
    const modes=Object.entries(cooperationModes).map(([key,label])=>'<option value="'+key+'" '+(mode===key?'selected':'')+'>'+label+'</option>').join('');
    return '<div class="wk-pdca-settings"><div class="wk-field"><div class="wk-pdca-mode-heading"><label for="wb-pdca-mode">合作方式</label>'+
      '<button type="button" class="wk-pdca-guide-button" aria-label="合作方式选择指南" popovertarget="wb-pdca-guide">?</button></div>'+
      '<dl id="wb-pdca-guide" class="wk-pdca-guide" popover="auto">'+
      '<div><dt>固定流程</dt><dd>目标、交付物和验收条件已明确，可以直接交给计划阶段。</dd></div>'+
      '<div><dt>负责人带队</dt><dd>目标已明确，交付形式或优先级还需梳理。先与负责人讨论，再交给计划阶段。</dd></div>'+
      '<div><dt>主持人—专家</dt><dd>方向或成功标准还没定，先和主持人讨论，参考专家意见共同确定任务说明。</dd></div>'+
      '</dl><select id="wb-pdca-mode" class="wk-input" name="mode">'+modes+'</select></div>'+
      '<label class="wk-field"><span>PDCA 轮数</span><select class="wk-input" name="rounds">'+
      [1,2,3,4].map(n=>'<option value="'+n+'" '+(n===rounds?'selected':'')+'>'+(n===1?'单轮':n+' 轮')+'</option>').join('')+'</select></label></div>'+
      '<div class="wk-pdca-columns" aria-hidden="true"><span>角色</span><span>模型系列</span><span>具体型号</span></div>'+
      Object.keys(cooperationModes).map(key=>staffingPanel(key,assignments[key],families,key===mode,expertCount)).join('');
  }
  function cooperationDialog(){
    const config=cooperationConfig(),hasModels=config.families.some(family=>family.models.length);
    modal('合作方式与分工', '<form id="wb-cooperation-form" class="wk-cooperation-form">'+cooperationFields(config)+
      (!hasModels?'<p class="wk-note">请先连接一个工作端。<a href="#settings/accounts" data-wb-close-link>账号与模型</a></p>':'')+
      '</form>',b('取消','close')+'<button class="wk-button primary" type="submit" form="wb-cooperation-form" '+(hasModels?'':'disabled')+'>保存分工</button>');
    const dialog=$('#wb-dialog');
    dialog.dataset.uiModule='workbench';
    syncCooperationForm(dialog.querySelector('#wb-cooperation-form'));
  }
  function syncCooperationForm(form,input){
    if(input?.hasAttribute('data-pdca-family')){
      const families=form.id==='wb-start-form'?cooperationSend.families:cooperationModels();
      const family=families.find(row=>row.id===input.value&&row.models.length);
      const model=input.closest('[data-pdca-role]').querySelector('[data-pdca-model]');
      model.innerHTML=modelOptions(family);
    }
    const mode=form.elements.mode.value;
    const expertCount=Number(form.elements.expert_count.value);
    for(const panel of form.querySelectorAll('[data-pdca-mode]')){
      panel.hidden=panel.disabled=panel.dataset.pdcaMode!==mode;
    }
    for(const row of form.querySelectorAll('[data-pdca-role]')){
      row.hidden=Number(row.dataset.pdcaExpert)>expertCount;
      const family=row.querySelector('[data-pdca-family]');
      family.disabled=row.hidden;
      row.querySelector('[data-pdca-model]').disabled=row.hidden||!family.value;
    }
    for(const group of form.querySelectorAll('.wk-pdca-group')){
      const roles=[...group.querySelectorAll('[data-pdca-role]:not([hidden]) [data-pdca-model]')];
      const models=roles.map(el=>el.value?el.selectedOptions[0].textContent:'待选');
      group.querySelector('[data-pdca-group-models]').textContent=models.join('、');
    }
    if(form.id==='wb-start-form')syncCooperationAuthorization(form);
  }
  document.addEventListener('invalid',event=>{
    const group=event.target.closest?.('.wk-pdca-group');
    if(group)group.open=true;
  },true);
  function readCooperation(form){
    const families=form.id==='wb-start-form'?cooperationSend.families:cooperationModels();
    const assignments=Object.fromEntries([...form.querySelectorAll('[data-pdca-mode]')].map(panel=>[
      panel.dataset.pdcaMode,Object.fromEntries([...panel.querySelectorAll('[data-pdca-role]')].map(row=>{
        const provider=row.querySelector('[data-pdca-family]').value,key=row.querySelector('[data-pdca-model]').value;
        const ref=families.find(family=>family.id===provider)?.models.find(entry=>connectionModelKey(entry.ref)===key)?.ref;
        return [row.dataset.pdcaRole,ref?{...ref}:{provider,model:''}];
      }))
    ]));
    return {mode:form.elements.mode.value,rounds:Number(form.elements.rounds.value),
      expertCount:Number(form.elements.expert_count.value),assignments};
  }
  function saveCooperation(form){
    v.formDraft.cooperation=readCooperation(form);
    $('#wb-dialog').close();
    features.autosave();
    draw();
  }
  function workRoundEstimate(mode,rounds){
    const stages=4*rounds;
    const terms=[[1,'准备'],[stages,'阶段执行'],...(mode==='fixed'?[]:[[stages,'协调'],[1,'最终核对']])];
    const minimum=terms.reduce((total,[count])=>total+count,0);
    const suggested=mode==='fixed'?minimum+5:20+(rounds-1)*10;
    return {terms,minimum,suggested};
  }
  function cooperationAuthorization(){
    return '<fieldset class="wk-pdca-stages wk-pdca-authorization"><legend>正式执行授权</legend>'+
      '<div class="wk-pdca-auth-grid">'+
      '<label class="wk-field"><span>派发时限（小时）</span><input type="number" min="0" max="168" step="any" class="wk-input" name="dispatch_hours" placeholder="24" aria-describedby="wb-pdca-deadline"></label>'+
      '<div class="wk-field"><div class="wk-pdca-budget-heading"><label for="wb-pdca-max-rounds">工作回合上限</label>'+
      '<button type="button" class="wk-pdca-guide-button" aria-label="工作回合设置指南" popovertarget="wb-pdca-budget-guide">?</button></div>'+
      '<input type="number" max="1000" step="1" class="wk-input" name="max_work_rounds" id="wb-pdca-max-rounds" aria-describedby="wb-pdca-estimate"></div></div>'+
      '<section id="wb-pdca-budget-guide" class="wk-pdca-guide wk-pdca-budget-guide" popover="auto" aria-label="工作回合设置指南"><header><strong>工作回合设置指南</strong><button type="button" popovertarget="wb-pdca-budget-guide" popovertargetaction="hide" aria-label="关闭工作回合设置指南">关闭</button></header><dl>'+
      '<div><dt>为什么要设上限</dt><dd>每派给工作端一次工作，计一个工作回合。总数包括安排好的工作以及给补做、重试和额外核对留的余量。达到上限后保留进度，等你决定是否追加。</dd></div>'+
      '<div><dt>准备</dt><dd>整理材料、固定分工，备好交给计划阶段的输入。只有单独派出的准备才计入这里。</dd></div>'+
      '<div><dt>阶段执行</dt><dd>每轮包含计划、执行、检查和改进，通常每个阶段各派发一次。第 0 轮需求讨论另计。</dd></div>'+
      '<div><dt>协调</dt><dd>负责人或主持人根据上一步结果判断要继续、补做还是暂停。所选方式安排了协调才列出这一项。</dd></div>'+
      '<div><dt>最终核对</dt><dd>交付前检查成品是否齐全、版本是否正确，以及是否满足你确认过的验收条件。</dd></div>'+
      '<div><dt>派发时限</dt><dd>默认 24 小时，从正式工作开始计算。到期后暂停新增派发，已开始的回合可以继续。</dd></div></dl></section>'+
      '<p class="wk-pdca-estimate" id="wb-pdca-estimate" role="status"></p><p class="wk-pdca-hint" id="wb-pdca-deadline"></p>'+
      '<label class="wk-field"><span>本机工具权限</span><select class="wk-input" name="access">'+
      '<option value="workspace">请求批准</option><option value="full">完全访问权限</option></select></label><p class="wk-pdca-hint" data-pdca-access-note></p></fieldset>';
  }
  function syncCooperationAuthorization(form){
    const mode=form.elements.mode.value;
    const planOnly=cooperationSend.context.plan_only;
    const discussion=mode!=='fixed';
    const label=discussion?'开始预讨论':planOnly?'生成方案':'确认并开始';
    form.ownerDocument.querySelector('button[form="wb-start-form"]').textContent=label;
    form.closest('dialog').querySelector('[data-pdca-next-step]').textContent=discussion
      ?(planOnly?'先讨论并确认任务说明，再生成方案供查看。':'先讨论并确认任务说明。下方设置用于正式执行。')
      :(planOnly?'生成方案后可在合作聊天里查看，再决定何时开始执行。':'确认后开始准备和执行。');
    form.querySelector('[data-pdca-confirmation]').textContent=discussion?'允许工作端读取任务资料，开始预讨论':
      planOnly?'允许工作端读取任务资料以生成方案':'我确认本次合作的工作范围与工具权限';
    if(planOnly)return;
    const {terms,minimum,suggested}=workRoundEstimate(mode,Number(form.elements.rounds.value));
    const limit=form.elements.max_work_rounds,duration=form.elements.dispatch_hours;
    limit.min=String(minimum);
    limit.placeholder=String(suggested);
    const chosen=limit.value===''?suggested:Number(limit.value);
    const estimate=terms.map(([count,kind])=>count+' 次'+kind);
    if(chosen>minimum)estimate.push((chosen-minimum)+' 次预留');
    form.querySelector('#wb-pdca-estimate').textContent=limit.validity.valid
      ?estimate.join(' + ')+' = '+chosen+' 个工作回合'
      :estimate.join(' + ')+' = 至少 '+minimum+' 个工作回合。可授权至 1000 次。';
    const hours=duration.value===''?24:Number(duration.value);
    duration.setCustomValidity(hours>0?'':'请输入大于 0 且不超过 168 的小时数。');
    const deadline=duration.validity.valid?new Date(Date.now()+hours*3600000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}):'';
    form.querySelector('#wb-pdca-deadline').textContent=deadline
      ?(discussion?'正式开始 PDCA 后 '+hours+' 小时停止新增派发。':'若现在开始，预计 '+deadline+' 停止新增派发。')
      :'请输入大于 0 且不超过 168 的小时数。';
    form.querySelector('[data-pdca-access-note]').textContent=form.elements.access.value==='full'
      ?'工作端可在本机账号有权访问的范围内直接读写文件、运行命令和使用网络。'
      :'工作端按自身规则使用工具，需要额外批准时会询问你。';
  }
  function prepare(){
    if(v.type==='independent')return independent.prepare();
    checkPartnerCapabilities();
    const config=cooperationConfig(),hasModels=config.families.some(family=>family.models.length);
    const context={project_id:v.project||undefined,goal:v.input.trim(),work_scene:v.scene,
      files:lines(v.files),attachments:structuredClone(v.attachments)};
    if(v.workPlanOnly)context.plan_only=true;
    if(composerCapabilities().length)context.composer_capabilities=structuredClone(composerCapabilities());
    const fileSelection=v.formDraft.fileSelection,files=[...new Set([...context.files,...context.attachments.map(a=>a.file)])].sort();
    if(fileSelection&&JSON.stringify([...fileSelection.files].sort())===JSON.stringify(files)){
      context.file_selection={id:fileSelection.id,revision:fileSelection.revision};
    }
    const pinned=Boolean(files.length||context.file_selection);
    const projects=[{id:'',name:'本地临时工作区'},...(v.data?.projects||[])];
    const projectField='<label class="wk-field"><span>工作区</span><select class="wk-input" name="project" '+(pinned?'disabled':'')+'>'+
      projects.map(row=>'<option value="'+h(row.id)+'" '+(row.id===(context.project_id||'')?'selected':'')+'>'+h(row.name)+'</option>').join('')+
      '</select>'+(pinned?'<small class="wk-pdca-hint">已选材料属于此工作区。</small>':'')+'</label>';
    modal(context.plan_only?'确认生成合作方案':'确认开始合作',
      '<div class="wk-pdca-request"><p class="wb-goal">'+h(context.goal)+'</p><p class="wk-pdca-hint" data-pdca-next-step></p></div>'+
      '<form id="wb-start-form" class="wk-cooperation-form">'+projectField+cooperationFields(config)+
      '<label class="wk-field"><span>验收条件</span><textarea class="wk-input" name="acceptance" rows="2" maxlength="16000" placeholder="需要交付什么，如何确认完成">'+h(v.formDraft.acceptance||'')+'</textarea></label>'+
      (context.plan_only?'':cooperationAuthorization())+
      (!hasModels?'<p class="wk-note">请先连接一个工作端。<a href="#settings/accounts" data-wb-close-link>账号与模型</a></p>':'')+
      '<label class="wk-check wk-pdca-confirmation"><input type="checkbox" name="host_permission" required><span data-pdca-confirmation></span></label></form>',
      b('返回修改','close')+'<button class="wk-button primary" type="submit" form="wb-start-form" '+(hasModels?'':'disabled')+'>确认并开始</button>');
    const dialog=$('#wb-dialog');
    dialog.dataset.uiModule='workbench';
    cooperationSend={generation:v.generation,context,families:config.families};
    const form=dialog.querySelector('#wb-start-form');
    form.addEventListener('input',({target})=>{
      if(['dispatch_hours','max_work_rounds'].includes(target.name))syncCooperationAuthorization(form);
    });
    syncCooperationForm(form);
  }
  async function start(form){
    const ready=cooperationSend,fields=new FormData(form);
    if(!ready||ready.generation!==v.generation||!form.isConnected||!$('#wb-dialog').open)throw Error('合作配置已变化，请重新确认。');
    if(!fields.has('host_permission'))throw Error('请确认本次本机工具权限。');
    const {mode,rounds,expertCount,assignments}=readCooperation(form);
    const resolve=key=>{
      const selected=assignments[mode][key];
      const ref=ready.families.find(row=>row.id===selected.provider)?.models.find(row=>connectionModelKey(row.ref)===connectionModelKey(selected))?.ref;
      if(!ref)throw Error('请选择每个角色可用的模型。');
      return ref;
    };
    const stages=Object.fromEntries(pdcaStages.map(key=>[key,resolve(key)]));
    const coordinator=mode==='fixed'?{}:{coordinator:resolve('coordinator')};
    const experts=mode==='host_experts'?{discussion_experts:discussionExperts.slice(0,expertCount).map(([key])=>resolve(key))}:{};
    const estimate=workRoundEstimate(mode,rounds);
    const maxWorkRounds=fields.get('max_work_rounds')?Number(fields.get('max_work_rounds')):estimate.suggested;
    if(!Number.isInteger(maxWorkRounds)||maxWorkRounds<estimate.minimum||maxWorkRounds>1000)
      throw Error('授权回合须覆盖 '+estimate.minimum+' 次计划工作，最多 1000 次。');
    const dispatchHours=fields.get('dispatch_hours')?Number(fields.get('dispatch_hours')):24;
    if(!Number.isFinite(dispatchHours)||dispatchHours<=0||dispatchHours>168)throw Error('请输入大于 0 且不超过 168 的小时数。');
    const selectedProject=form.elements.project.value||undefined;
    const workspace=selectedProject===ready.context.project_id?{}:{project_id:selectedProject,
      work_scene:(v.data?.projects||[]).find(p=>p.id===selectedProject)?.scene||ready.context.work_scene};
    const receipt=await command('work-item.cooperation-open',{...ready.context,...workspace,mode,rounds,stages,...coordinator,...experts,
      dispatch_hours:dispatchHours,max_work_rounds:maxWorkRounds,access:fields.get('access')||'workspace',
      acceptance:String(fields.get('acceptance')||'')});
    if(ready.generation!==v.generation)return;
    cooperationSend=null;
    $('#wb-dialog').close();
    v.input='';
    features.autosave();
    await go('#work-items/conversation/'+encodeURIComponent(receipt.work_item.id));
  }
  function fullSendContext(){
    return JSON.stringify({generation:v.generation,task:task()?.id,attempt:v.detail?.attempts.at(-1)?.id,
      provider:v.provider,model:v.model,connection_id:v.connectionId,connection_revision:v.connectionRevision,effort:v.workEffort,tier:v.workTier,access:v.workAccess,scene:v.scene,
      input:v.input,project:v.project,files:v.files,attachments:v.attachments,formDraft:v.formDraft});
  }
  async function sendWork(confirmed=false){
    if(v.workLoading){toast('工作配置仍在读取，请稍候。');return;}
    if(!v.input.trim())return;
    if(v.dialogue){
      const d=v.dialogue;
      modal('发送协作消息',`<p class="wb-goal">${h(v.input)}</p><p class="wk-note">向 ${names[d.config.provider]} 的当前协作讨论发送消息，使用原问题链预算。</p>`,b('取消','close')+b('确认发送','dialogue-send','',true));return;
    }
    if(!v.detail && !v.project && v.type==='independent' && window.WorkdeskSettingsPreferences?.get().recovery){
      modal('先确认工作目录','<p>项目外目录的偏好文件已损坏。请选择本次使用的已登记项目，或恢复偏好后再发送。</p>'+
        (v.data?.projects||[]).map(p=>'<button class="wk-menu-item" data-wb-close-link data-wb-project="'+h(p.id)+'">'+h(p.name)+' · '+h(p.root)+'</button>').join(''),
        b('返回选择项目','close')+'<a class="wk-button" href="#settings/records" data-wb-close-link>恢复工作目录偏好</a>');
      return;
    }
    if(v.type==='independent'&&!v.workPlanOnly&&(!v.detail||task()?.spec.runtime_mode==='official')&&v.workAccess==='full'){
      const context=fullSendContext();
      if(!confirmed){
        fullSend=context;
        const directory=project()?.name||(!v.detail?window.WorkdeskSettingsPreferences?.get().workDirectory:null)||'项目工作区';
        modal('确认本次完全访问', '<p>工作端可以访问账号有权访问的文件、运行命令和使用网络。相关操作可能直接执行。</p>'+
          '<div class="wk-approval"><span>工作端</span><span>'+h(names[v.provider])+'</span></div>'+
          (selectedPartnerConnection()?'<div class="wk-approval"><span>连接</span><span>'+h(selectedPartnerConnection().name||selectedPartnerConnection().account_label)+'</span></div>':'')+
          '<div class="wk-approval"><span>工作位置</span><span>'+h(directory)+'</span></div>'+
          '<p class="wb-goal">'+h(v.input)+'</p>',b('返回修改','close')+b('确认并发送','confirm-full-send','',true));
        return;
      }
      if(fullSend!==context)throw Error('工作配置已变化，请重新确认后发送。');
      fullSend=null;$('#wb-dialog').close();
    }else if(confirmed)throw Error('工作权限已变化，请重新发送。');
    if(v.detail){await independent.send();return;}
    await prepare();
  }
  async function saveRecord(changes){
    if(v.activeChat){await features.saveChat(changes);return;}
    const id=v.dialogue?.id||task()?.id;if(!id)throw Error('先选择一段对话。');
    await command('work.record',{id,kind:v.dialogue?'dialogue':'task',...changes});await load();renderSidebar();
  }
  async function configureDraft(changes){
    if(!task())return;
    const current=task();const result=await command('work.configure',{task_id:current.id,spec_hash:current.spec_hash,...changes});
    v.detail.task=result.task;v.scene=result.task.spec.work_scene||'legacy';v.provider=result.task.spec.provider;v.model=result.task.spec.model;
    if(current.id!==result.task.id){history.replaceState(null,'',href('task',result.task.id));state.route=location.hash.slice(1);}
    await load();toast('草稿配置已更新，开始前需重新确认范围。');
  }
  function projectDialog(create){
    modal(create?'创建项目':'添加已有工作区',`<form id="wb-project-form" data-create="${create}"><label class="wd-project-name">${icon('folder')}<input class="wk-input" name="name" aria-label="项目名称" placeholder="项目名称" required maxlength="100"></label><label class="wk-field wd-project-scene">工作场景（可选）<select class="wk-input" name="scene" aria-label="项目工作场景"><option value="">普通项目（不指定场景）</option>${Object.entries(scenes).map(([id,s])=>`<option value="${id}">${s.name}</option>`).join('')}</select></label><input type="hidden" name="root"><div class="wd-project-source-label">源文件夹</div><section class="wd-project-source" aria-label="项目源文件夹"><div id="wb-project-empty">在此电脑上添加文件夹</div><div id="wb-project-folder" class="wd-project-folder" hidden>${icon('folder')}<div><strong id="wb-project-folder-name"></strong><p id="wb-project-folder-path"></p></div></div>${b(icon('folder')+'<span id="wb-project-pick-label">添加</span>','pick-directory','aria-label="选择项目文件夹"')}<p id="wb-project-folder-status" role="status" hidden></p></section><div class="wd-project-actions">${b('取消','close')}<button type="submit" class="wk-button primary" disabled>${create?'创建项目':'添加并选择'}</button></div></form>`,' ');
    const dialog=$('#wb-dialog');dialog.dataset.uiModule='workbench';dialog.dataset.workProjectDialog='';
    $('#wb-project-form [name=name]').focus();
  }
  async function act(action,el){
    if(action==='more-tool'){openWorkspaceTool(el?.dataset.tool);return;}
    if(action==='work-capabilities'){closeMenu();await capabilityDialog(el.dataset.kind);return;}
    if(action==='work-context'){contextDialog();return;}
    if(action==='work-context-preview'){await contextPreview(el);return;}
    if(action==='work-plan-mode'){
      closeMenu();
      if(!canPlan())return;
      v.workPlanOnly=!v.workPlanOnly;
      features.autosave();
      draw();
      return;
    }
    if(action==='confirm-full-send'){await sendWork(true);return;}
    if(await independent.act(action,el))return;
    if(await chatRecords.act(action,el))return;
    if(await workRecords.act(action,el))return;
    closeMenu();
    if(await features.act(action,el))return;
    if(action==='close'){fullSend=null;$('#wb-dialog').close();return;}
    if(action==='new'&&v.mode==='chat'){
      const parent=v.chats.find(c=>c.id===v.activeChat);
      if(parent?.list_project){const r=await command('work-item.chat-create',{project_id:parent.list_project,provider:parent.provider,route:parent.route});await features.refresh();await go('#chat/'+r.chat.id);return;}
    }
    if(action==='clear-resource'){delete v.formDraft.resourceSelection;delete v.formDraft.resourceName;features.autosave();draw();return;}
    if(action==='new'){const selected=v.mode==='chat'?{chatProvider:v.chatProvider,chatRoute:v.chatRoute,chatApiProvider:v.chatApiProvider,chatApiModel:v.chatApiModel,chatThinking:v.chatThinking,chatSearch:v.chatSearch}:null;v.input='';v.chatInput='';v.detail=null;v.dialogue=null;v.activeChat=null;if(v.mode==='chat'){v.chatEmbedded=false;v.chatFiles=[];v.chatSearchQuery='';}await go(v.mode==='work'?'#work':'#chat',v.mode==='work');if(selected){Object.assign(v,selected,chatDefaults());v.chatInput='';draw();}return;}
    if(action==='back'){history.back();return;}if(action==='forward'){history.forward();return;}
    if(action==='sidebar'){
      const narrow=matchMedia('(max-width:640px)').matches;
      if(narrow)v.mobileSidebar=!v.mobileSidebar;else{v.sidebar=!v.sidebar;setLocal('sidebar-open',v.sidebar);}
      shell();
      if(narrow)requestAnimationFrame(()=>{
        const target=v.mobileSidebar?[...$('#sidebar').querySelectorAll('a,button,input')].find(node=>node.getClientRects().length&&!node.disabled):document.querySelector('.wk-topbar [data-wb="sidebar"]');
        target?.focus();
      });
      return;
    }
    if(action==='theme'){theme(state.theme==='dark'?'light':'dark');return;}
    if(action==='records'){go('#records');return;}
    if(action==='exit-settings'){const target=window.WorkdeskSettingsPreferences?.lastPage?.()||v.mode;location.hash='#'+target;return;}
    if(action==='cooperation-settings'){cooperationDialog();return;}
    if(action==='roles'){rolesDialog();return;}if(action==='save-roles'){if(locked())throw Error('当前工作已开始，分工已固定。');v.roles={...v.roleDraft};$('#wb-dialog').close();draw();return;}
    if(action==='new-project'||action==='register-project'){if(!v.data)await load();projectDialog(action==='new-project');return;}
    if(action==='project-new-work'){const id=el.dataset.project;await go('#work',true);if(!v.data.projects.some(p=>p.id===id))throw Error('项目登记已变化，请重新选择。');v.project=id;if(scenes[project()?.scene])v.scene=project().scene;draw();features.autosave();return;}
    if(action==='unfiled'){if(v.detail||v.dialogue)throw Error('已有记录保留原工作区。');v.project='';closeMenu();draw();return;}
    if(action==='skills'){
      if(v.detail?.task.spec.runtime_mode==='official'){await independent.panel();return;}
      const data=await call('extensions'),preset=data.scenes.presets.find(p=>p.scene===v.scene&&p.default&&p.enabled);modal((scenes[v.scene]?.name||'当前工作')+' · 能力预设',`<p class="wk-note">${preset?'新工作会使用预设：'+h(preset.name)+' · 版本 '+h(preset.revision.slice(0,8)):'当前场景没有启用的默认预设，使用工作端原有配置。'}</p>${preset?`<h3>提示词</h3><p class="wk-note">${h(preset.prompt)}</p><h3>输出要求</h3><p class="wk-note">${h(preset.output)}</p><p>能力组合：${preset.capabilities.length+preset.extensions.length} 项</p>`:''}<p class="wk-note">新伙伴连接通过文件工具读取技能文档，暂不支持本次加载 MCP。兼容工作沿用原有能力机制。已有工作保留发送时的版本；保存和浏览不调用模型。</p>`,`<a class="wk-button" href="#extensions/scenes" data-wb-close-link>编辑场景预设</a><a class="wk-button" href="#extensions/skills" data-wb-close-link>管理 Skills 与插件</a><a class="wk-button" href="#extensions/records" data-wb-close-link>查看加载记录</a>`+b('完成','close'));return;
    }
    if(action==='details'){
      const organized=records().find(r=>r.kind===(v.dialogue?'dialogue':'task')&&r.id===(v.dialogue?.id||task()?.id));
      const grouping=organized?`<div class="wk-approval"><span>记录归入</span><span>${h(scenes[organized.scene]?.name||'未分类')} · ${h(v.data.projects.find(p=>p.id===organized.project)?.name||'未归入项目')}</span></div><p class="wk-note">记录归属用于整理列表；以下工作区与配置是原执行范围，移动记录不会移动文件或改变执行权限。</p>`:'';
      const service=v.type==='independent'&&v.detail?.attempts.length&&independent.presentation().service?b('官方运行服务','independent-service'):'';
      modal('工作详情',`${grouping}<div class="wk-approval"><span>执行场景</span><span>${scenes[v.scene]?.name||'旧记录未分类'}${locked()?' · 已锁定':''}</span></div><div class="wk-approval"><span>执行工作区</span><span>${h(project()?.name||'未归入项目')}</span></div><div class="wk-approval"><span>工作方式</span><span>${v.type==='cooperative'?'合作':'独立'}</span></div><div class="wk-approval"><span>材料</span><span>${h(v.files||'未选择')}</span></div>${v.type==='cooperative'?Object.entries(roleNames).map(([k,[name]])=>`<div class="wk-approval"><span>${name}</span><span>${names[v.roles[k]]}</span></div>`).join(''):`<div class="wk-approval"><span>模型</span><span>${names[v.provider]} · ${h(v.model)}</span></div>`}`,`${task()?`<a class="wk-button" href="#control/task/${task().id}" data-wb-close-link>运行、授权与材料</a>`:''}${v.dialogue?`<a class="wk-button" href="#collab/dialogue/${v.dialogue.id}" data-wb-close-link>协作方案与队列</a>`:''}${service}${b('关闭','close')}`);return;
    }
    if(action==='dialogue-send'){const msg=await command('dialogue.message',{dialogue_id:v.dialogue.id,content:v.input});await command('dialogue.send',{dialogue_id:v.dialogue.id,revision:msg.dialogue.revision});v.input='';$('#wb-dialog').close();await render(['dialogue',v.dialogue.id]);return;}
    if(action==='resume-work'){await independent.resume();return;}
    if(action==='independent-service'){independent.info();return;}
    if(action==='copy-chat'){await copyText(v.chats.find(c=>c.id===v.activeChat)?.prompt||'');return;}
    if(action==='copy-input'){await copyText(v.input);return;}
    if(action==='rename'){modal('重命名',`<form id="wb-rename-form"><label class="wk-field">名称<input class="wk-input" name="title" required maxlength="160" value="${h(title())}"></label><button type="submit" class="wk-button primary">保存</button></form>`);return;}
    if(action==='pin'){const flag=v.activeChat?v.chats.find(c=>c.id===v.activeChat)?.pinned:display(v.dialogue?'dialogue':'task',v.dialogue?.id||task()?.id).pinned;await saveRecord({pinned:!flag});closeMenu();return;}
    if(action==='archive'){await saveRecord({archived:true});closeMenu();go(v.mode==='work'?'#work':'#chat');return;}
    if(action==='help'){modal('Workdesk',`<p class="wk-note">Chat：默认通过已配置工作端发送文本对话，各段聊天独立保存正文。也可使用官方网页入口；网页会话由官方管理。</p><p class="wk-note">Work：先选择场景、工作方式和项目，再确认开始。独立工作开始后模型固定；工作场景同样固定。</p><p class="wk-note">会话保存对话历史；工作区表示项目文件位置。目录独立不等于执行沙箱。</p><p class="wk-note"><a href="#settings" data-wb-close-link>设置与服务状态</a></p>`);return;}
  }
  function searchResults(query){return features.searchResults(query);}
  function error(error){if($('#wb-dialog').open){const el=$('#wb-modal-error');el.hidden=false;el.textContent=error.message;}else toast(error.message);}
  document.addEventListener('click',async e=>{
    const el=e.target.closest('button,a');if(!el)return;
    if(el.matches('[data-wb-close-link]'))$('#wb-dialog').close();
    if(el.matches('#wb-overlays a'))closeMenu();
    if(el.dataset.wbMenu){e.preventDefault();if(el.dataset.wbMenu==='project'&&!v.data)await load();openMenu(el.dataset.wbMenu,el);return;}
    if(el.dataset.wbScene){if(!locked()){try{if(task())await configureDraft({work_scene:el.dataset.wbScene});else v.scene=el.dataset.wbScene;closeMenu();draw();}catch(exc){error(exc);}}return;}
    if(el.dataset.wbType){if(!locked()){v.type=el.dataset.wbType;draw();}return;}
    if(el.dataset.wbProject){if(v.detail||v.dialogue)return;v.project=el.dataset.wbProject;const selected=project();if(scenes[selected?.scene])v.scene=selected.scene;closeMenu();draw();return;}
    if(!el.dataset.wb)return;e.preventDefault();
    if(features.isSideAction(el.dataset.wb)){try{await features.act(el.dataset.wb,el);}catch(exc){error(exc);}return;}
    if(v.busy&&el.dataset.wb!=='close')return;
    const blocking=new Set(['confirm-full-send','native-access-confirm','native-access-select','native-execute','native-steer','new','dialogue-send','resume-work','save-roles','pin','archive','new-project','register-project','skills','chat-confirm','chat-stop','official-web','chat-enter','chat-window','fork','schedule','schedule-state','unarchive','unarchive-record','delete-draft','runtime-stop','runtime-approval','runtime-native','existing-confirm','chat-record-pin','chat-record-delete-confirm','work-record-pin','work-record-delete-confirm']);
    if(blocking.has(el.dataset.wb)||['runtime-reconcile','native-recover-confirm','native-history-fork','native-history-confirm','native-history-more','native-background-stop','native-control-stop'].includes(el.dataset.wb)){v.busy=true;el.disabled=true;}
    document.body.setAttribute('aria-busy',String(v.busy));
    try{await act(el.dataset.wb,el);}catch(exc){error(exc);}finally{v.busy=false;document.body.setAttribute('aria-busy','false');if(el.isConnected)el.disabled=false;const send=$('#main .wk-send');if(send)send.disabled=!!v.pending||!!v.chats.find(c=>c.id===v.activeChat)?.pending;syncComposer();}
  });
  document.addEventListener('click',e=>{if(v.menu&&!e.target.closest('#wb-overlays,[data-wb-menu]'))closeMenu();});
  document.addEventListener('contextmenu',e=>{const row=e.target.closest('[data-chat-id],[data-work-key],[data-work-project]'),anchor=row?.querySelector('[data-wb-menu="chat-record"],[data-wb-menu="work-record"],[data-wb-menu="work-project"]');if(anchor){e.preventDefault();openMenu(anchor.dataset.wbMenu,anchor);}});
  document.addEventListener('input',e=>{const el=e.target;if(el.id==='wb-work-input'){v.input=el.value;sizeComposer(el);}else if(el.id==='wb-chat-input'){v.chatInput=el.value;sizeComposer(el);}else if(el.id==='wb-project-search'){v.projectQuery=el.value;$('#wb-project-results').innerHTML=projectResults();}else if(el.id==='wb-context-search')$('#wb-context-results').innerHTML=contextResults(el.value);else if(el.id==='wb-record-search')$('#wb-record-results').innerHTML=searchResults(el.value);});
  document.addEventListener('change',async e=>{
    const el=e.target;
    if(['wb-cooperation-form','wb-start-form'].includes(el.form?.id)){syncCooperationForm(el.form,el);return;}
    if(el.dataset.wbRole){v.roleDraft[el.dataset.wbRole]=el.value;return;}
    if(features.chatSelection(el.id,el.value)){features.autosave();return;}
    if(el.id==='wb-connection'&&!v.detail&&partnerConnections()){
      const connection=partnerConnections().connections.find(row=>row.id===el.value&&row.partner_id===v.provider&&row.available);
      if(connection){Object.assign(v,{connectionId:connection.id,connectionRevision:String(connection.revision),model:connection.model||'official-default',workEffort:'default',workTier:'standard'});draw();features.autosave();}return;
    }
    if(el.id==='wb-work-effort'){v.workEffort=el.value;features.autosave();return;}
    if(el.id==='wb-model'&&v.detail?.task.spec.runtime_mode==='official'){v.model=el.value;v.workEffort='';draw();features.autosave();return;}
    if(el.id==='wb-provider'||el.id==='wb-model'){if(locked()){draw();return;}try{if(task()){const selected=el.id==='wb-provider'?config(el.value):{model:el.value};delete selected.execution;await configureDraft(selected);}else if(el.id==='wb-provider')useWorkDefault(el.value);else {v.model=el.value;v.workEffort='';}draw();}catch(exc){error(exc);draw();}}
  });
  document.addEventListener('submit',async e=>{
    const f=e.target;if(!f.id.startsWith('wb-'))return;e.preventDefault();
    if(f.id==='wb-side-form'){await features.submitSide(f);return;}
    if(v.busy)return;v.busy=true;document.body.setAttribute('aria-busy','true');const submit=e.submitter||f.querySelector('[type=submit]');if(submit)submit.disabled=true;
    try{
      if(await chatRecords.submit(f))return;
      if(await workRecords.submit(f))return;
      if(await features.submit(f))return;
      if(f.id==='wb-chat-form'){
        await features.submitChat();
      }else if(f.id==='wb-work-form')await sendWork();
      else if(f.id==='wb-start-form')await start(f);
      else if(f.id==='wb-composer-capabilities')saveCapabilities(f);
      else if(f.id==='wb-context-quote'){
        const quote=String(new FormData(f).get('quote')||'').trim();
        if(!quote)throw Error('引用内容不能为空。');
        v.input=[v.input.trimEnd(),quote].filter(Boolean).join('\n\n');
        $('#wb-dialog').close();
        features.autosave();
        draw();
        requestAnimationFrame(()=>$('#wb-work-input')?.focus());
      }
      else if(f.id==='wb-cooperation-form')saveCooperation(f);
      else if(f.id==='wb-project-form'){
        const data=new FormData(f),root=data.get('root');if(!root)throw Error('请先选择项目文件夹。');
        const existing=v.data.projects.find(p=>p.id===f.dataset.existing),scene=data.get('scene')||null;
        const origin=location.hash;const row=await command('project.create',{name:data.get('name'),root,...(!existing||existing.scene!==scene?{scene}:{}),create_directory:false});
        await load();if(f.isConnected&&$('#wb-dialog').open&&location.hash===origin){v.project=row.project_id;if(scenes[project()?.scene])v.scene=project().scene;$('#wb-dialog').close();if(isWorkRoute())draw();else renderSidebar();toast('项目已选择。');}
      }else if(f.id==='wb-rename-form'){await saveRecord({title:new FormData(f).get('title')});$('#wb-dialog').close();shell();}
    }catch(exc){error(exc);}finally{v.busy=false;document.body.setAttribute('aria-busy','false');if(submit?.isConnected)submit.disabled=!!v.pending;const confirm=$('#wb-dialog form [type=submit]');if(confirm)confirm.disabled=!!v.pending;const send=$('#main .wk-send');if(send)send.disabled=!!v.pending||!!v.chats.find(c=>c.id===v.activeChat)?.pending;syncComposer();}
  });
  document.addEventListener('keydown',e=>{if(document.body.hasAttribute('data-connection-required'))return;
    if(e.key==='Escape'){
      if(v.menu){e.preventDefault();const anchor=v.anchor;closeMenu();anchor?.focus();return;}
      if(v.mobileSidebar&&matchMedia('(max-width:640px)').matches&&!document.querySelector('dialog[open],:popover-open')){e.preventDefault();act('sidebar');return;}
    }
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();e.stopImmediatePropagation();if(!window.WorkdeskSettings?.focusSearch?.())act('search');}
    if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.target.matches('#wb-chat-input,#wb-work-input')){e.preventDefault();e.target.closest('form').requestSubmit();}
  },true);
  window.addEventListener('hashchange',()=>{closeMenu();if($('#wb-dialog').open)$('#wb-dialog').close();if(v.mobileSidebar){v.mobileSidebar=false;shell();}});
  window.addEventListener('resize',()=>{closeMenu();shell();});
  window.WorkdeskWorkbench={render:async(parts=[],connections,chatEntry)=>{await render(parts,connections,chatEntry);if(location.hash==='#work'){const draft=getLocal('fork-draft','');if(draft){v.input=draft;setLocal('fork-draft','');draw();}}},leave,renderSidebar,setWorkMode:()=>{v.mode='work';shell();},load,
    setChatDetailMode:id=>{v.mode='chat';v.activeChat=id||null;shell();},
    chatSearchQuery:text=>{v.chatSearchQuery=text;features.autosave();},
    chatFiles:async files=>{if(v.busy)return;v.busy=true;try{await features.chatFiles(files);}catch(exc){error(exc);}finally{v.busy=false;syncComposer();}},
    inputText:()=>v.mode==='chat'?v.chatInput:v.input,
    copyMessage:copyText,
    editWorkMessage:(taskId,text)=>{
      if(location.hash!=='#work/task/'+taskId||v.mode!=='work'||v.type!=='independent'||v.detail?.task.id!==taskId||v.workLoading)return false;
      if(v.input.trim()&&v.input!==text){toast('输入框已有草稿，请先处理后再重新编辑。');return false;}
      v.input=text;features.autosave();draw();$('#wb-work-input')?.focus();return true;
    },
    selectWorkAccess:async access=>{if(v.busy)return;v.busy=true;try{await independent.act('native-access-select',{dataset:{access}});}catch(exc){error(exc);}finally{v.busy=false;draw();}},
    selectIndependentConfig:values=>{if(v.busy||v.workLoading||v.mode==='chat')return;if(!v.detail&&values.provider&&values.provider!==v.provider)v.workAccess=defaultAccess(values.provider);Object.assign(v,values);draw();features.autosave();},
    submitText:async text=>{
      if(v.busy||v.pending)return;
      if(v.mode==='chat')v.chatInput=text;else v.input=text;
      features.autosave();v.busy=true;
      try{if(v.mode==='chat')await features.submitChat();else await sendWork();}catch(exc){error(exc);}finally{
        v.busy=false;const confirm=$('#wb-dialog form [type=submit]');if(confirm)confirm.disabled=!!v.pending;syncComposer();
      }
    },
    perform:async action=>{if(v.busy)return;v.busy=true;try{await act(action);}catch(exc){error(exc);}finally{v.busy=false;syncComposer();}},
    forgetRecord:(kind,id)=>{
      setLocal('composer-backup:composer:'+(kind==='chat'?'chat:':'work:')+id,null);
      if(kind==='chat'){
        const legacy=v.chats.find(c=>c.id===id)?.legacy_id;
        if(legacy)setLocal('web-chats',getLocal('web-chats',[]).filter(c=>c.id!==legacy));
        if(v.activeChat===id){v.activeChat=null;v.chatInput='';v.chatFiles=[];}
      }else if(v.detail?.task.id===id||v.dialogue?.id===id){v.detail=null;v.dialogue=null;v.input='';v.formDraft={};}
    },
    suspendRecordDrafts:(kind,ids)=>kind==='chat'?features.suspendChatDrafts(ids):features.suspendWorkDrafts(ids),
    redraw:draw,sideConversation:features.sideConversation,openWorkspaceTool};
})();
