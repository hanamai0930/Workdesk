/* Independent-work controller. Vendor runtimes own planning and execution. */
window.WorkdeskIndependent = function(B) {
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,call,command,modal,b,h,profile,project,lines,names}=B;
  let catalog=null,cache=null;
  const terminal=new Set(['COMPLETED','FAILED','FAILED_NO_SEND','INTERRUPTED','STOPPED_RUNTIME','CANCELLED']);
  const partnerCatalog=()=>!v.detail?v.data?.partner_connections:null;
  const partnerConnection=()=>partnerCatalog()?.connections.find(row=>row.id===v.connectionId&&row.partner_id===v.provider);
  const partnerModel=connection=>connection.model||'official-default';
  function install(data){catalog=data.independent_work||null;if(!v.detail&&!v.dialogue&&v.type==='independent')normalize();}
  function normalize(){
    if(partnerCatalog()){
      if(!v.connectionId){const rows=partnerCatalog().connections.filter(row=>row.partner_id===v.provider),connection=rows.find(row=>row.available)||rows[0];if(connection){v.connectionId=connection.id;v.connectionRevision=String(connection.revision);}}
      const connection=partnerConnection();
      if(connection)v.model=partnerModel(connection);
      v.workEffort='default';v.workTier='standard';return;
    }
    const p=profile();if(!p)return;
    const models=p.enabled_models||p.models||[];
    if(!models.includes(v.model))v.model=models.includes(p.model)?p.model:models[0]||'';
  }
  const service=()=>v.detail?.independent_service||v.detail?.task.spec.independent_service||catalog?.services.find(s=>s.provider===v.provider)||null;
  function presentation(){
    const connection=partnerConnection();
    if(partnerCatalog())return {service:connection?{provider:v.provider,harness:names[v.provider]+' · '+(connection.route==='api'?(connection.api_name||connection.api_provider)+' API':'官方账号')+' · '+connection.account_label,access_modes:connection.permission_modes||[],partner_connection:true}:null,runtimePresent:!!connection?.available};
    return {service:service(),runtimePresent:catalog?.services.find(s=>s.provider===v.provider)?.runtime_present??false};
  }
  function requireService(){
    if(partnerCatalog()){
      const connection=partnerConnection();
      if(!connection)throw Error('请选择已保存的伙伴连接。');
      if(!connection.available)throw Error(connection.reason||'此连接当前不可用，请在账号与模型中核对。');
      if(String(connection.revision)!==String(v.connectionRevision))throw Error('所选连接已更新，请重新选择后发送。');
      return presentation().service;
    }
    const s=catalog?.services.find(s=>s.provider===v.provider);
    if(!s)throw Error('控制服务尚未载入独立工作接口，请先在设置中重载空闲控制服务。');
    if(!s.runtime_present)throw Error(s.harness+' 尚未安装或不可用，请检查工作端配置。');
    return s;
  }
  const row=(label,value)=>`<div class="wk-approval"><span>${label}</span><span>${h(value)}</span></div>`;
  function selectedConfiguration(){
    if(partnerCatalog())return {connection_id:v.connectionId,connection_revision:String(v.connectionRevision),model:v.model,effort:'default',tier:'standard',access:!v.workAccess||v.workAccess==='inherit'?'workspace':v.workAccess,plan_only:!!v.workPlanOnly};
    const p=profile(),entry=p.model_catalog?.models.find(m=>m.model===v.model),efforts=entry?.selection_efforts||entry?.efforts||[p.effort],tiers=entry?.tiers||[p.tier];
    return {model:v.model,effort:efforts.includes(v.workEffort)?v.workEffort:efforts.includes(p.effort)?p.effort:efforts[0],tier:tiers.includes(v.workTier)?v.workTier:tiers.includes(p.tier)?p.tier:tiers[0],access:!v.workAccess||v.workAccess==='inherit'?'workspace':v.workAccess,plan_only:!!v.workPlanOnly};
  }
  async function prepare(){
    if(!v.input.trim())return;
    normalize();requireService();
    if(!v.model)throw Error('请选择已接通的模型。');
    const saved=await command('independent.send',{project_id:v.project||undefined,goal:v.input,provider:v.provider,model:v.model,
      ...(!v.project&&!v.workPlanOnly&&v.workAccess==='full'?{work_directory_confirmation:window.WorkdeskSettingsPreferences?.get().workDirectory||''}:{}),
      ...selectedConfiguration(),
      work_scene:v.scene,files:lines(v.files),attachments:v.attachments});
    v.input='';window.history.replaceState(null,'','#work/task/'+saved.task_id);state.route=location.hash.slice(1);
    await B.rerender(saved.task_id);
  }
  const start=prepare;
  async function readTask(id,reset=false){
    let current=cache;
    if(reset||!current||current.id!==id)current={id,cursor:0,events:new Map()};
    for(let page=0;page<8;page++){
      const detail=await call('task?id='+encodeURIComponent(id)+'&after='+current.cursor);
      for(const e of detail.events)current.events.set(e.seq,e);
      const before=current.cursor;current.cursor=detail.cursor;
      current.detail={...detail,events:[...current.events.values()].sort((a,b)=>a.seq-b.seq)};
      if(!detail.has_more_events||current.cursor<=before)break;
    }
    cache=current;return current.detail;
  }
  async function send(){
    const last=v.detail.attempts.at(-1);
    if(v.detail.task.spec.runtime_mode==='official'){
      await command('independent.send',{task_id:v.detail.task.id,attempt_id:last?.id,owner:last?.owner,goal:v.input,...selectedConfiguration()});
      v.input='';await B.rerender(v.detail.task.id);return;
    }
    if(!last){await B.startExisting();return;}
    if(!terminal.has(last.state)){
      modal('继续这项工作','<p class="wk-note">当前回合仍在处理或状态待核对。补充文字已保留，可先处理审批、停止当前回合或等待结束。</p>',b('复制补充内容','copy-input')+`<a class="wk-button primary" href="#control/task/${v.detail.task.id}" data-wb-close-link>运行与控制</a>`);return;
    }
    const s=service(),native=!!s?.native_resume&&!!last.session_id&&v.detail.task.spec.execution===s.execution;
    modal('继续这项工作',`<p class="wb-goal">${h(v.input)}</p><p class="wk-note">${h(s?.harness||'原工作端')} · 使用原模型和累计预算继续。</p><label class="wk-field">接续方式<select class="wk-input" id="wb-resume-mode">${native?'<option value="native">恢复原生会话</option>':''}<option value="context">基于当前文件建立新上下文</option></select></label><p class="wk-note">${native?'原生恢复沿用已保存的运行身份；':'本机当前适配未接通原生恢复；'}新上下文不恢复旧聊天历史。</p>`,b('取消','close')+b('确认继续','resume-work','',true));
  }
  async function resume(){
    const t=v.detail.task,last=v.detail.attempts.at(-1),mode=$('#wb-resume-mode').value;
    if(mode==='native'&&(!service()?.native_resume||!last.session_id))throw Error('此运行服务没有可恢复的原生会话。');
    await command('task.resume',{task_id:t.id,spec_hash:t.spec_hash,attempt_id:last.id,owner:last.owner,mode,note:v.input});
    v.input='';$('#wb-dialog').close();await B.rerender(t.id);
  }
  function info(){
    if(partnerCatalog()){
      const connection=partnerConnection();
      if(!connection){modal('官方运行服务','<p class="wk-note">请在账号与模型中保存可用连接。</p>','<a class="wk-button" href="#settings/accounts" data-wb-close-link>账号与模型</a>');return;}
      modal('官方运行服务',row('工作端',names[v.provider])+row('连接',connection.route==='api'?(connection.api_name||connection.api_provider)+' API':'官方账号')+row('账号',connection.account_label)+row('模型',connection.model_label||connection.model||'官方默认模型')+row('版本',connection.version||'待核对')+(connection.reason?'<p class="wk-note">'+h(connection.reason)+'</p>':''),b('关闭','close'));return;
    }
    const s=service()||requireService(),selected=s.route_selection;
    const choice=!v.detail&&s.provider==='qwen'&&s.daemon_present&&selected;
    const select=choice?`<label class="wk-field">新 Qwen 工作使用<select class="wk-input" id="wb-qwen-route"><option value="query" ${selected.interface==='query'?'selected':''}>Qwen Code Query</option><option value="daemon" ${selected.interface==='daemon'?'selected':''}>Qwen Code Daemon</option></select></label><p class="wk-note">此选择用于新建独立工作和合作方案。继续已有会话时沿用记录的工作端接口。</p>`:'';
    modal('官方运行服务',`${row('服务',s.harness)}${select}<a href="${h(s.source)}" target="_blank" rel="noopener noreferrer">官方接口说明</a>`,choice?b('应用于新 Qwen 工作','native-qwen-route','',true):b('关闭','close'));
  }

  async function control(operation,params={}){
    const a=v.detail?.attempts.at(-1);if(!a)throw Error('请先开始独立工作。');
    const receipt=await command('native.control',{task_id:v.detail.task.id,attempt_id:a.id,owner:a.owner,operation,params});
    return receipt;
  }
  function access(){
    const s=service()||requireService();
    const live=v.detail?.attempts.at(-1)&&!terminal.has(v.detail.attempts.at(-1).state);
    modal('访问权限',`<p class="wk-note">${live&&v.provider!=='codex'?'权限模式通过官方接口应用到当前会话，收到回执后才算生效。':'应用到下一回合。当前回合的待批请求仍需逐项答复。'}采用${h(s.harness)}自己的权限模式。</p>${(s.access_modes||[]).filter(m=>m.id!=='inherit').map(m=>`<button class="wk-native-permission-option" data-wb="native-access-select" data-access="${h(m.id)}" ${m.supported===false?'disabled':''} aria-pressed="${(v.workAccess||'workspace')===m.id}"><strong>${h(m.name)}</strong><small>${h(m.description)}</small></button>`).join('')}`);
    $('#wb-dialog').dataset.uiModule='workbench';$('#wb-dialog').dataset.nativeControlDialog='';
  }
  const labels={history:'会话历史',items:'会话条目',fork:'原生分叉',revert:'回退会话记录',steer:'运行中补充指令',settings:'会话设置',commands:'可用原生命令',command:'执行原生命令',context:'上下文用量',usage:'官方用量',models:'可用模型',permissions:'权限模式','permissions.get':'当前权限','config.read':'读取官方配置','config.write':'修改官方配置','settings.model':'切换模型','settings.effort':'切换思考配置','goal.get':'当前目标','goal.set':'设置目标','goal.clear':'清除目标','skills.list':'原生 Skills','skills.configure':'启用或停用 Skill','plugins.list':'已安装插件','plugins.search':'搜索插件','plugins.install':'安装插件','plugins.remove':'卸载插件','apps.list':'原生 Apps','mcp.list':'MCP 状态','mcp.login':'MCP 登录','mcp.reload':'重新载入 MCP','background.list':'后台作业','background.stop':'停止后台作业','agents.list':'原生子代理','agents.message':'向子代理补充指令',interrupt:'原生中断','workflows.list':'原生工作流',directory:'当前原生工作目录','directory.set':'切换原生工作目录',review:'原生评审'};
  const templates={revert:{beforeTurnId:''},fork:{},steer:{text:''},settings:{},'settings.model':{model:''},'settings.effort':{effort:'high'},permissions:{mode:'danger-full-access'},'goal.set':{text:''},'skills.configure':{path:'',enabled:true},'plugins.search':{query:''},'plugins.install':{pluginId:''},'plugins.remove':{pluginId:''},'mcp.login':{name:''},'background.stop':{processId:''},command:{line:'/help'},'agents.message':{agent_id:'',text:''}};
  Object.assign(labels,{'hooks.list':'原生 Hooks','background.output':'后台作业输出','plugins.bundles':'原生插件包','plugins.bundle.enable':'启用插件包','plugins.bundle.disable':'停用插件包','plugins.enable':'启用插件','plugins.disable':'停用插件','mcp.add':'添加 MCP 服务','mcp.remove':'移除 MCP 服务','mcp.reconnect':'重连 MCP 服务','mcp.approve':'批准 MCP 服务','mcp.reject':'拒绝 MCP 服务'});
  Object.assign(labels,{'apps.installed':'已安装 Apps','rewind.snapshots':'会话回退点','workflows.history':'已保存的原生工作流与作业','background.history':'当前回合的后台作业记录','history.verify':'核对官方保存的回合'});
  labels['history.migrate']='将已结束的 Query 会话切换到 Daemon';
  const daemon=()=>service()?.interface==='daemon';
  function parameters(id){
    if(v.provider==='deepseek'&&['plugins.enable','plugins.disable'].includes(id))return {entry_id:'',selection_revision:''};
    if(v.provider==='deepseek'&&['plugins.bundle.enable','plugins.bundle.disable','plugins.remove'].includes(id))return {name:'',selection_revision:''};
    if(id==='settings.model')return {model:v.model};
    if(id==='permissions')return {mode:v.provider==='qwen'?'yolo':'danger-full-access'};
    if(id==='plugins.search')return {searchTerm:''};
    if(id==='plugins.install')return ['qwen','deepseek'].includes(v.provider)?{source:''}:{pluginName:'',remoteMarketplaceName:''};
    if(id==='plugins.remove')return v.provider==='qwen'?{name:''}:{pluginId:''};
    if(['plugins.enable','plugins.disable','mcp.remove','mcp.approve','mcp.reject'].includes(id))return {name:''};
    if(id==='mcp.add')return {name:'',commandOrUrl:'',transport:'http'};
    if(id==='goal.set')return {objective:'',origin:'user'};
    if(v.provider==='deepseek'&&['background.stop','background.output'].includes(id))return {job_id:''};
    if(daemon()&&id==='background.stop')return {job_id:'',kind:'shell'};
    if(daemon()&&id==='revert')return {beforeTurnId:''};
    return templates[id]||{};
  }
  function fields(id){
    const field=(key,label,kind='text',choices=null)=>({key,label,kind,choices});
    if(v.provider==='deepseek'&&['background.stop','background.output'].includes(id))return [field('job_id','原生后台作业标识')];
    if(daemon()&&id==='background.stop')return [field('job_id','原生后台作业标识'),field('kind','作业类型','select',['shell','agent','monitor','workflow'])];
    if(daemon()&&id==='revert')return [field('beforeTurnId','会话回退点的提示标识')];
    if(id==='history.migrate')return [];
    if(v.provider==='deepseek'&&['plugins.enable','plugins.disable'].includes(id))return [field('entry_id','原生插件条目标识'),field('selection_revision','查询回执中的配置版本')];
    if(v.provider==='deepseek'&&['plugins.bundle.enable','plugins.bundle.disable','plugins.remove'].includes(id))return [field('name','原生插件包名'),field('selection_revision','查询回执中的配置版本')];
    if(id==='plugins.bundles')return [];
    const simple={steer:[field('text','补充指令','textarea')],command:[field('line','官方命令，例如 /goal 或 /plan')],
      'goal.set':[field('objective','目标内容','textarea')],'skills.configure':[field('path','Skill 文件路径'),field('enabled','启用','checkbox')],
      'plugins.search':[field('searchTerm','搜索插件')],'plugins.remove':[field(v.provider==='qwen'?'name':'pluginId','插件名称 / 标识')],
      'mcp.login':[field('name','MCP 服务名称')],'background.stop':[field('processId','原生进程标识')],
      'agents.message':[field('agent_id','原生代理标识'),field('text','补充指令','textarea')],
      'settings.model':[field('model','模型名称')],'settings.effort':[field('effort','官方思考强度','select',['low','medium','high','xhigh','max'])],
      permissions:[field('mode','原生权限模式','select',v.provider==='qwen'?['default','plan','auto-edit','auto','yolo']:['workspace-write','read-only','danger-full-access'])],
      'mcp.add':[field('name','MCP 服务名称'),field('commandOrUrl','服务 URL 或程序路径'),field('transport','连接方式','select',['http','sse','stdio']),field('args','程序参数（JSON 数组，可留空）','textarea')]};
    if(id==='plugins.install')return ['qwen','deepseek'].includes(v.provider)?[field('source','官方插件来源（仓库或本地目录）')]:[field('pluginName','插件名称'),field('remoteMarketplaceName','官方市场名称（可留空）')];
    if(['plugins.enable','plugins.disable','mcp.remove','mcp.approve','mcp.reject','mcp.reconnect'].includes(id))return [field('name','名称'+(id==='mcp.reconnect'?'（留空重连全部）':''))];
    if(simple[id])return simple[id];
    if(['history','items','fork','commands','context','usage','models','permissions.get','goal.get','goal.clear','skills.list','hooks.list','plugins.list','apps.list','apps.installed','mcp.list','mcp.reload','background.list','agents.list','directory','interrupt','config.read','rewind.snapshots','workflows.history','background.history','history.verify'].includes(id))return [];
    return null;
  }
  function editor(id,values=parameters(id)){
    const f=fields(id);if(f===null)return `<label class="wk-field">高级原生参数<textarea class="wk-input" rows="5" id="wb-native-params">${h(JSON.stringify(values,null,2))}</textarea></label>`;
    if(!f.length)return `<p class="wk-note">${id==='fork'?'执行后创建原生分叉；也可在会话历史中选择准确位置。':'此操作无需额外填写参数。'}</p>`;
    return f.map(x=>`<label class="wk-field">${h(x.label)}${x.kind==='textarea'?`<textarea class="wk-input" rows="3" data-native-param="${x.key}">${h(values[x.key]||'')}</textarea>`:x.kind==='select'?`<select class="wk-input" data-native-param="${x.key}">${x.choices.map(c=>`<option value="${h(c)}" ${values[x.key]===c?'selected':''}>${h(c)}</option>`).join('')}</select>`:`<input class="wk-input" type="${x.kind}" data-native-param="${x.key}" ${x.kind==='checkbox'?(values[x.key]!==false?'checked':''):`value="${h(values[x.key]||'')}"`}>`}</label>`).join('');
  }
  function readParameters(id){
    const raw=$('#wb-native-params');if(raw){try{return JSON.parse(raw.value);}catch{throw Error('参数不是有效JSON。');}}
    const params={...parameters(id)};
    for(const input of $('#wb-native-editor').querySelectorAll('[data-native-param]')){
      const key=input.dataset.nativeParam,value=input.type==='checkbox'?input.checked:input.value.trim();
      if(value===''&&!['remoteMarketplaceName','args'].includes(key)&&!(id==='mcp.reconnect'&&key==='name'))throw Error('请填写'+fields(id).find(x=>x.key===key).label+'。');
      if(value==='')delete params[key];else params[key]=value;
    }
    if(id==='mcp.add'&&params.args){try{params.args=JSON.parse(params.args);}catch{throw Error('程序参数须为JSON数组，例如 ["--port", "3000"]。');}if(!Array.isArray(params.args)||params.args.some(x=>typeof x!=='string'))throw Error('程序参数须为文本数组。');}
    return params;
  }
  async function panel(operation){
    if(!v.detail){info();return;}
    const options=service().native_controls||[],selected=operation||options.find(o=>o.supported!==false)?.id;
    modal('官方工作端控制',`<p class="wk-note">操作使用当前会话的原生接口。带“运行中”的操作需要原生运行时在线。${v.provider==='codex'?'Codex 会话设置用于后续回合。':v.provider==='deepseek'?'插件管理修改当前官方 SDK profile，影响后续启动；以原生回执的生效或重启要求为准。':''}回退只修改会话历史，不撤销项目文件。插件安装、卸载与配置会修改官方工作端配置。</p><label class="wk-field">操作<select class="wk-input" id="wb-native-operation">${options.map(o=>`<option value="${h(o.id)}" ${o.id===selected?'selected':''} ${o.supported===false?'disabled':''} title="${h(o.reason||'')}">${h(labels[o.id]||o.id)}${o.supported===false?' · 暂不可用':o.requires_live?' · 运行中':''}</option>`).join('')}</select></label><div id="wb-native-editor">${editor(selected)}</div><div id="wb-native-receipt" class="wk-note"></div>`,b('执行操作','native-execute','',true)+b('刷新回执','native-receipts')+b('关闭','close'));
    $('#wb-dialog').dataset.uiModule='workbench';$('#wb-dialog').dataset.nativeControlDialog='';receipts();
  }
  async function receipts(){
    if(!v.detail)return;const identity=v.detail.task.id,origin=location.hash,d=await readTask(identity);
    if(v.detail?.task.id!==identity||location.hash!==origin)return;v.detail=d;
    const el=$('#wb-native-receipt');if(el)el.innerHTML=(d.native_controls||[]).slice(0,8).map(o=>{
      let url='';try{const u=new URL(o.result?.authorizationUrl);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password)url=u.href;}catch{}
      const pending=['QUEUED','SEND_INTENT'].includes(o.state),stop=pending&&(!o.live||o.state==='QUEUED');
      return `<section class="wk-native-receipt"><details><summary>${h(labels[o.operation]||o.operation)} · ${o.phase==='WAITING_AUTH'&&pending?'等待官方授权':h(o.state)}${o.effect_scope==='NEXT_TURN'?' · 后续回合':''}</summary><pre>${h(JSON.stringify(o.error||o.result||{operation_id:o.id},null,2))}</pre></details>${nativeView(o)}${url&&pending?`<a class="wk-button" href="${h(url)}" target="_blank" rel="noopener noreferrer">打开官方授权页面</a>`:''}${stop?b('取消此操作','native-control-stop',`data-operation="${h(o.id)}"`):''}${o.fork_task_id?`<a class="wk-button" href="#work/task/${h(o.fork_task_id)}" data-wb-close-link>打开分叉会话</a>`:''}</section>`;
    }).join('')||'尚无操作回执。';
  }
  function nativeView(o){
    if(o.state!=='COMPLETED')return '';
    const attrs=(i)=>`data-operation="${h(o.id)}" data-index="${i}"`;
    if(o.provider==='deepseek'&&['plugins.list','plugins.bundles'].includes(o.operation)&&Array.isArray(o.result?.data))return `<p class="wk-note">当前官方 SDK profile 的插件配置；变更的生效状态以原生回执为准。</p><div class="wk-native-history">${o.result.data.map((p,i)=>`<article><strong>${h(p.moduleName||p.name)}</strong><p>${h(p.version||p.bundles?.map(b=>b.version).filter(Boolean).join(', ')||'版本未返回')} · ${p.enabled?'启用':'停用'} · ${h(p.fiberPhase||p.availability||'')}</p>${p.readOnlyReason?`<small>${h(p.readOnlyReason)}</small>`:b(p.enabled?'停用':'启用','native-plugin-toggle',attrs(i))}${o.operation==='plugins.bundles'&&p.removable?b('移除此插件包','native-plugin-remove',attrs(i)):''}</article>`).join('')||'<p>官方管理服务未返回插件。</p>'}</div>`;
    if(o.operation==='history'&&Array.isArray(o.result?.data)){
      const latest=v.detail.native_controls.find(x=>x.operation==='history'&&x.state==='COMPLETED');
      if(latest?.id!==o.id)return '';
      return `<div class="wk-native-history">${o.result.data.map((t,i)=>{
        const prompt=(t.items||[]).filter(x=>x.type==='userMessage').map(x=>(x.content||[]).map(p=>p.text||'').join('')).join('\n');
        return `<article><strong>回合 ${h(t.id)}</strong><p>${h(prompt.slice(0,400)||t.label||'原生会话条目')}</p><small>${h(t.status||'')}</small>${o.interface==='Daemon'?'':`<div>${t.status!=='inProgress'?b('从此回合分叉','native-history-fork',attrs(i)):''}${b('回退到此回合之前','native-history-revert',attrs(i))}</div>`}</article>`;
      }).join('')||'<p>原生会话当前没有回合。</p>'}${o.result.nextCursor?b('载入更早的回合','native-history-more',`data-operation="${h(o.id)}"`):''}</div>`;
    }
    if(o.interface==='Daemon'&&o.operation==='rewind.snapshots'&&Array.isArray(o.result?.data))return `<div class="wk-native-history">${o.result.data.map((p,i)=>`<article><strong>${h(p.id)}</strong><p>${h(p.timestamp||'')}</p>${b('回退会话记录','native-history-revert',attrs(i))}</article>`).join('')||'<p>官方工作端没有返回回退点。</p>'}</div>`;
    if(o.interface==='Daemon'&&o.operation==='background.list'&&Array.isArray(o.result?.data))return `<div class="wk-native-history">${o.result.data.map((p,i)=>`<article><strong>${h(p.label||p.id)}</strong><p>${h(p.kind)} · ${h(p.status)}</p>${['running','queued','pending','pausing'].includes(p.status)?b('请求停止此作业','native-background-stop',attrs(i)):''}</article>`).join('')||'<p>当前原生会话没有返回后台作业。</p>'}</div>`;
    if(['background.history','workflows.history'].includes(o.operation)&&Array.isArray(o.result?.data))return `<div class="wk-native-history">${o.result.settlement==='NOT_OBSERVED'?'<p>尚无后台作业结算回执。</p>':''}${o.result.data.map(p=>`<article><strong>${h(p.label||p.name||p.id)}</strong><p>${h(p.kind)} · ${h(p.status==='TERMINAL_NOT_OBSERVED'?'终态未知':p.status)}</p></article>`).join('')||'<p>当前记录没有保存后台作业。</p>'}</div>`;
    if(o.operation==='background.list'&&Array.isArray(o.result?.data))return `<div class="wk-native-history">${o.result.data.map((p,i)=>o.provider==='deepseek'?`<article><strong>${h(p.label||p.id)}</strong><p>${h(p.status)} · ${h(p.progress||p.detail||'')}</p>${b('查看输出','native-background-output',attrs(i))}${['running','stopping'].includes(p.status)?b('请求停止此作业','native-background-stop',attrs(i)):''}</article>`:`<article><strong>${h(p.command||p.processId)}</strong><p>${h(p.cwd||'')}</p><small>PID ${h(p.osPid??'未返回')} · ${h(p.cpuPercent??'未知')}% CPU</small>${b('停止此后台终端','native-background-stop',attrs(i))}</article>`).join('')||'<p>此原生运行时未返回后台作业。</p>'}</div>`;
    if(o.operation==='agents.list'&&Array.isArray(o.result))return `<div class="wk-native-history">${o.result.map((a,i)=>`<article><strong>${h(a.id)}</strong><p>${h(a.status||'原生状态未返回')}${a.parentSession?' · 子代理':''}</p>${a.parentSession?b('补充指令','native-agent-message',attrs(i)):''}</article>`).join('')||'<p>此原生运行时未返回活动代理。</p>'}</div>`;
    return '';
  }
  async function watch(identity){
    const origin=location.hash;
    for(let i=0;i<80&&$('#wb-native-receipt')&&$('#wb-dialog').open&&location.hash===origin;i++){
      await receipts();const op=v.detail.native_controls.find(o=>o.id===identity);
      if(op&&!['QUEUED','SEND_INTENT'].includes(op.state))return;
      await new Promise(resolve=>setTimeout(resolve,250));
    }
  }
  async function act(action,el){
    if(action==='native-qwen-route'){
      const s=service(),route=$('#wb-qwen-route'),dialog=$('#wb-dialog');
      if(s?.provider!=='qwen'||v.detail||!route||route.disabled)throw Error('请从新 Qwen 工作选择工作端。');
      const origin=location.hash;route.disabled=true;
      try{
        await command('independent.qwen-runtime-select',{interface:route.value,revision:s.route_selection.revision,confirm:true});
        const data=await call('independent-work');
        if(location.hash===origin){catalog=data;dialog.close();B.changed();}
      }finally{if(route.isConnected)route.disabled=false;}
      return true;
    }
    if(action==='native-edit-last-message'){
      if(v.input.trim())throw Error('输入框已有内容，已保留；请先处理当前草稿。');
      const a=v.detail.attempts.at(-1);v.input=v.detail.events.find(e=>e.kind==='execution_reserved'&&e.attempt_id===a.id)?.payload.human_note||v.detail.task.spec.goal;
      B.changed();$('#wb-work-input').focus();return true;
    }
    if(action==='runtime-reconcile'){
      const a=v.detail.attempts.at(-1),r=await command('task.reconcile',{task_id:v.detail.task.id,attempt_id:a.id,owner:a.owner});
      modal('状态核对',`<p>${r.known_local_scopes_exited?'本地运行时已确认退出。可以结束待核对回合，再继续发送新问题。':'本地运行时退出状态仍未确认，请先停止或稍后核对。'}</p><p class="wk-note">没有重发上条消息；远端是否已执行仍按记录保留。</p>`,b('关闭','close'));return true;
    }
    if(action==='runtime-close-unknown'){
      modal('结束待核对回合','<p>确认本地运行时退出后，结束这个待核对回合，恢复后续聊天入口。</p><p class="wk-note">保留历史记录、操作和消耗，不重发上条消息，也不把未知结果改成成功。你可以随后发送新的问题。</p>',b('取消','close')+b('结束并恢复聊天入口','native-recover-confirm','',true));return true;
    }
    if(action==='native-recover-confirm'){
      const a=v.detail.attempts.at(-1);await command('independent.recover',{task_id:v.detail.task.id,attempt_id:a.id,owner:a.owner});$('#wb-dialog').close();await B.rerender(v.detail.task.id);return true;
    }
    if(action==='native-access'){access();return true;}
    if(action==='native-access-confirm'){
      const current=v.detail?.attempts.at(-1);
      if(v.detail?.task.id!==el.dataset.task||current?.id!==el.dataset.attempt||current?.owner!==el.dataset.owner)
        throw Error('运行身份已变化，请重新确认访问权限。');
      return act('native-access-select',{dataset:{access:'full',confirmed:'true'}});
    }
    if(action==='native-access-select'){
      const access=el.dataset.access,a=v.detail?.attempts.at(-1);
      if(partnerCatalog()&&!partnerConnection()?.permission_modes?.some(mode=>mode.id===access&&mode.supported))throw Error('所选工作端没有支持此权限模式。');
      if(a&&['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT'].includes(a.state)){
        if(access==='full'&&el.dataset.confirmed!=='true'){
          modal('确认当前回合的完全访问','<p>工作端可以访问账号有权访问的文件、运行命令和使用网络。确认后会向当前回合提交权限变更。</p>',
            b('返回','close')+b('确认变更','native-access-confirm','data-task="'+h(v.detail.task.id)+'" data-attempt="'+h(a.id)+'" data-owner="'+h(a.owner)+'"',true));
          return true;
        }
        const r=await command('native.access',{task_id:v.detail.task.id,attempt_id:a.id,owner:a.owner,access});let settled=false;
        for(let i=0;i<80;i++){const d=await readTask(v.detail.task.id),op=d.native_controls.find(o=>o.id===r.operation_id);v.detail=d;if(op&&!['QUEUED','SEND_INTENT'].includes(op.state)){if(op.state!=='COMPLETED')throw Error(op.error||op.state);settled=true;break;}await new Promise(resolve=>setTimeout(resolve,250));}
        if(!settled)throw Error('原生权限回执仍在等待，请刷新运行记录；没有自动重发。');
      }
      v.workAccess=access;$('#wb-dialog').close();B.changed();return true;
    }
    if(action==='native-panel'){await panel();return true;}
    if(action==='native-steer'){await control('steer',{text:v.input});v.input='';B.changed();return true;}
    if(action==='native-execute'){
      const id=$('#wb-native-operation').value;
      if(id==='history.migrate'){
        const d=v.detail,a=d.attempts.at(-1);
        modal('切换已保存的 Qwen 会话','<p>用 Qwen Code Daemon 接续这个已结束的会话。确认后核对原会话及项目身份，保存官方接续回执。</p>',b('取消','native-panel')+b('确认切换','native-qwen-adopt',`data-task="${h(d.task.id)}" data-attempt="${h(a.id)}" data-owner="${h(a.owner)}"`,true));
        $('#wb-dialog').dataset.uiModule='workbench';return true;
      }
      const r=await control(id,readParameters(id));await watch(r.operation_id);return true;
    }
    if(action==='native-qwen-adopt'){
      const a=v.detail?.attempts.at(-1);
      if(v.detail?.task.id!==el.dataset.task||a?.id!==el.dataset.attempt||a?.owner!==el.dataset.owner)throw Error('运行身份已变化，请重新核对。');
      el.disabled=true;
      try{
        const r=await control('history.migrate',{confirm:true});await panel('history.migrate');await watch(r.operation_id);
      }finally{if(el.isConnected)el.disabled=false;}
      return true;
    }
    if(action==='native-receipts'){await receipts();return true;}
    if(['native-plugin-toggle','native-plugin-remove'].includes(action)){
      const o=v.detail.native_controls.find(o=>o.id===el.dataset.operation),item=o?.result?.data?.[Number(el.dataset.index)];
      if(!item||o.provider!=='deepseek'||o.state!=='COMPLETED')throw Error('插件回执已变化，请重新查询。');
      const entry=o.operation==='plugins.list';
      const operation=action==='native-plugin-remove'?'plugins.remove':entry?(item.enabled?'plugins.disable':'plugins.enable'):(item.enabled?'plugins.bundle.disable':'plugins.bundle.enable');
      const r=await control(operation,{...(entry?{entry_id:item.entryId}:{name:item.name}),selection_revision:item.selection_revision});await watch(r.operation_id);return true;
    }
    if(action.startsWith('native-history-')||action==='native-background-stop'||action==='native-background-output'||action==='native-agent-message'){
      const o=v.detail.native_controls.find(o=>o.id===el.dataset.operation);
      if(!o||o.state!=='COMPLETED')throw Error('原生回执已变化，请重新载入。');
      if(action==='native-history-more'){const r=await control('history',{cursor:o.result.nextCursor,...(o.result.snapshot?{snapshot:o.result.snapshot}:{})});await watch(r.operation_id);return true;}
      const item=(o.operation==='agents.list'?o.result:o.result.data)?.[Number(el.dataset.index)];if(!item)throw Error('所选原生条目已变化。');
      if(action==='native-history-revert'){
        modal('回退会话历史',`<p>删除原生回合 <strong>${h(item.id)}</strong> 及其后的会话历史。</p><p class="wk-note">项目文件不会撤销；本地运行审计和已产生的用量保留。正在运行时须先结束回合。</p>`,b('取消','native-panel')+b('确认回退会话','native-history-confirm',`data-operation="${h(o.id)}" data-index="${Number(el.dataset.index)}"`,true));
        $('#wb-dialog').dataset.uiModule='workbench';return true;
      }
      if(action==='native-history-confirm'){
        await panel('revert');const r=await control('revert',{beforeTurnId:item.id});await watch(r.operation_id);await B.rerender(v.detail.task.id);return true;
      }
      if(action==='native-agent-message'){
        await panel('agents.message');$('#wb-native-editor').innerHTML=editor('agents.message',{agent_id:item.id,text:''});return true;
      }
      if(action==='native-background-output'){const r=await control('background.output',{job_id:item.id});await watch(r.operation_id);return true;}
      const r=await control(action==='native-history-fork'?'fork':'background.stop',action==='native-history-fork'?{lastTurnId:item.id}:o.interface==='Daemon'?{job_id:item.id,kind:item.kind}:o.provider==='deepseek'?{job_id:item.id}:{processId:item.processId});await watch(r.operation_id);return true;
    }
    if(action==='native-control-stop'){
      const op=v.detail.native_controls.find(o=>o.id===el.dataset.operation);if(!op)throw Error('操作回执已变化。');
      await command('native.control-stop',{task_id:op.task_id,attempt_id:op.attempt_id,owner:op.owner,operation_id:op.id});await receipts();return true;
    }
    return false;
  }
  document.addEventListener('change',e=>{if(e.target.id==='wb-native-operation')$('#wb-native-editor').innerHTML=editor(e.target.value);});
  return {install,normalize,presentation,prepare,start,readTask,send,resume,info,act,panel};
};
