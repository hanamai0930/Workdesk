/* Continuous work tasks and project-local conversations. Reads never dispatch. */
(()=>{
  'use strict';const $=WorkdeskModules.dom('workitems'),S=WorkdeskSupport,{h}=S;
  const v={data:null,projects:[],records:[],available:[],recordsLoad:0,resources:null,presets:[],teamDraft:null,handoffDraft:null,handoffOpen:false,textDraft:null,textOpen:false,unitDraft:null,unitOpen:false,fileDraft:null,fileOpen:false,deliveryPreview:null,taskUsage:null,error:'',busy:false,loadingControls:null,controls:null,seq:0};
  let compactProject=null,compactHeading=null,compactSettings=null;
  const independentTabs={overview:'概览',runtime:'运行与控制',delivery:'成果与验收',settings:'任务设置',usage:'额度消耗'};
  const taskFilters={project:'all',state:'all'};
  const states=[['OPEN','进行中'],['PAUSED','已暂停'],['READY_FOR_ACCEPTANCE','待使用验收'],['ACCEPTED','已接受'],['CANCELLED','已取消']];
  const current=()=>{
    const p=location.hash.split('/');
    const origin=['item','view-chat','view-work'].includes(p[1])&&/^from-(records|tasks|project)$/.test(p.at(-1))
      ?p.pop().slice(5):null;
    let id=p[2];
    try{if(id)id=decodeURIComponent(id);}catch{ /* leave invalid route for the normal missing-record response */ }
    const tab=['controls','notes','delivery','usage'].includes(p[3])?p[3]:'trace';
    const conversationFile=p[1]==='conversation'&&p[3]==='file';
    let file=null;
    if((tab==='delivery'||conversationFile)&&p.length===6&&p[4]&&p[5]){
      try{file={run:decodeURIComponent(p[4]),name:decodeURIComponent(p[5])};}catch{}
    }
    const fromWork=p[3]==='from-work'&&/^[a-zA-Z0-9_-]+$/.test(p[4]||'')?p[4]:null;
    const stageTask=['trace','thoughts'].includes(p[3])&&/^[a-zA-Z0-9_-]+$/.test(p[4]||'')?p[4]:null;
    const detail=p[1]==='view-chat'?'chat':p[1]==='view-work'?'work':null;
    const detailTab=detail==='work'&&Object.hasOwn(independentTabs,p[3])?p[3]:'overview';
    return {active:p[0]==='#work-items',project:p[1]==='project'?id:null,openProject:p[1]==='project'&&p[3]==='open',
      detail,detailId:detail?id:null,detailTab,detailItem:detailTab==='settings'?p[4]:null,origin,
      id:p[1]==='item'?id:null,conversation:p[1]==='conversation'?id:null,
      delivery:p[1]==='item'&&tab==='delivery',deliveryFile:tab==='delivery'?file:null,
      cooperationFile:conversationFile?file:null,cooperationTools:p[1]==='conversation'&&p[3]==='tools',
      tab,fromWork,stageTask,thoughts:p[3]==='thoughts'};
  };
  const workOrigin=task=>task?'/from-work/'+encodeURIComponent(task):'';
  const projectHref=(id,task)=>'#work-items/project/'+encodeURIComponent(id)+workOrigin(task);
  const workConversation=id=>'#work/task/'+encodeURIComponent(id);
  function workReturnTask(records, source){
    const rows=records.filter(r=>r.kind==='task'&&!r.unavailable&&r.href===workConversation(r.id));
    return source?rows.find(row=>row.id===source):rows.length===1?rows[0]:null;
  }
  const workReturnLink=record=>record?`<a class="wi-work-return" href="${workConversation(record.id)}"><span aria-hidden="true">‹</span>返回 Work 对话</a>`:'';
  const projectName=id=>v.projects.find(p=>p.id===id)?.name||'普通任务';
  const record=r=>r.unavailable?`<p class="sp-note">${h(r.title)}</p>`:`<p><a href="${h(r.href)}">${h(r.title)}</a> <small>${r.kind==='task'?'执行记录':r.kind==='chat'?'Chat':'合作讨论'}</small></p>`;
  function resourceRef(id){const m=v.resources.members.find(m=>m.id===id),c=v.resources.connections.find(c=>c.id===m?.connection_id),q=v.resources.quota_sources.find(q=>q.id===c?.quota_id);return m&&c&&q?{member_id:m.id,member_revision:m.revision,connection_id:c.id,connection_revision:c.revision,quota_id:q.id,quota_revision:q.revision}:null;}
  const presetValue=p=>p?JSON.stringify({id:p.id,revision:p.revision}):'';
  function presetOptions(selected){const options=[['','工作端现有配置（无任务附加预设）'],...v.presets.filter(p=>p.enabled).map(p=>[presetValue(p),p.name])];if(selected&&!options.some(o=>o[0]===presetValue(selected)))options.push([presetValue(selected),'原预设版本已变化，请重新选择']);return options;}
  const fixedAccessModes=[['workspace','请求批准'],['auto','官方自动审核'],['full','完全访问权限']];
  const accessLabel=mode=>fixedAccessModes.find(([value])=>value===mode)?.[1]||'请求批准';
  const memberAccessDefault=(provider,modes)=>{
    const preferred=window.WorkdeskSettingsPreferences?.get?.()?.workAccess?.[provider];
    return preferred==='auto'&&modes.some(mode=>mode.supported&&mode.id===preferred)?preferred:'';
  };
  const confirmFullAccess=members=>!members.length||S.confirm('确认本次完全访问',
    '这些工作端可以在其账号范围内编辑文件、执行命令并使用网络。',members.join('、'));
  function frozenFullAccess(item,unitKey){
    const plan=item.teamwork.plan;
    const saved=item.teamwork_history?.plans.find(row=>row.id===plan.id&&row.version===plan.version);
    return [...new Set((saved?.units||plan.units).filter(unit=>
      (!unitKey||unit.key===unitKey)&&(unit.access||plan.member_access?.[unit.member.reference?.member_id]||plan.access)==='full'
    ).map(unit=>unit.member.name))];
  }
  const fixedAccessField=(value,label='固定流程工作端权限')=>S.select(label,'access',fixedAccessModes,value);
  function teamDraft(item){if(v.teamDraft?.itemId===item.id){v.teamDraft.handoff_locked=!!item.teamwork?.handoff_configuration_locked;return v.teamDraft;}
    const p=item.teamwork?.plan,options=(v.resources?.members||[]).filter(m=>m.available).map(m=>({id:m.id,label:m.name+' · '+m.model,ref:resourceRef(m.id)})),first=options[0]?.id||'';
    return v.teamDraft={itemId:item.id,revision:item.revision,current_plan:p?{id:p.id,version:p.version}:null,options,
      mode:p?.mode||'fixed',coordinator:p?.coordinator.reference.member_id||first,integrator:p?.integrator.reference.member_id||first,
      max_parallel:p?.max_parallel||1,hard_cash_limit_cny:p?.hard_cash_limit_cny??'',access:p?.access||'workspace',
      handoff_locked:!!item.teamwork?.handoff_configuration_locked,handoff_enabled:!!p?.file_handoffs?.length,handoff_source:p?.file_handoffs?.[0]?.source_unit||'',handoff_target:p?.file_handoffs?.[0]?.target_unit||'',handoff_paths:(p?.file_handoffs?.[0]?.paths||[]).join('\n'),
      units:p?p.units.map(u=>({...u,member_id:u.member.reference.member_id,check_preset:u.checks?.length?'saved':'none'})): [{key:'u1',title:'完成任务',goal:item.goal.slice(0,4000),acceptance:item.acceptance.slice(0,4000)||'按任务要求保存成果并留下检查记录',role:'execute',member_id:first,depends:[],write_paths:[],outputs:[],checks:[],dependency_gate:'result',check_preset:'none'}]};
  }
  function captureTeam(form){const d=v.teamDraft,values=new FormData(form);for(const k of ['mode','coordinator','integrator','max_parallel','hard_cash_limit_cny','access'])d[k]=values.get(k);
    if(!d.handoff_locked){d.handoff_enabled=values.get('handoff_enabled')==='yes';for(const k of ['handoff_source','handoff_target','handoff_paths'])d[k]=String(values.get(k)||'');}
    d.units=d.units.map((u,i)=>({...u,...Object.fromEntries(['title','goal','acceptance','role','member_id'].map(k=>[k,String(values.get(`unit_${i}_${k}`)||'')])),
      scene_preset:values.get(`unit_${i}_scene_preset`)?JSON.parse(String(values.get(`unit_${i}_scene_preset`))):null,depends:values.getAll(`unit_${i}_depends`).map(String),write_paths:String(values.get(`unit_${i}_write_paths`)||'').split('\n').map(p=>p.trim()).filter(Boolean),
      outputs:String(values.get(`unit_${i}_outputs`)||'').split('\n').map(p=>p.trim()).filter(Boolean),dependency_gate:String(values.get(`unit_${i}_dependency_gate`)||'result'),check_preset:String(values.get(`unit_${i}_check_preset`)||'none'),check_text:String(values.get(`unit_${i}_check_text`)||'')}));
    for(const u of d.units)if(u.check_preset!=='saved')u.checks=u.check_preset==='none'?[]:u.outputs.map(file=>({file,kind:u.check_preset,...(u.check_preset==='utf8_contains'?{text:u.check_text}:{})}));return d;
  }
  function driverView(item){const d=item.driver;if(!d)return '';if(item.cooperation?.closed_reason)return `<div class="wi-driver" data-driver-state="${h(d.record?.state||'UNKNOWN')}"><h3>合作推进责任</h3><p>${h(item.cooperation.closed_reason)}</p><div class="sp-row">${['pause','resume','release'].map((action,i)=>S.button(['暂停合作责任','重新选定合作责任','释放合作责任'][i],`data-driver-action="${action}" disabled`)).join('')}</div></div>`;const row=d.record,labels={RESERVED:'合作责任已选定',PAUSED:'合作责任已暂停',RELEASED:'合作责任已释放'},button=(label,action,enabled)=>S.button(label,`data-driver-action="${action}" ${enabled?'':'disabled'}`);
    return `<div class="wi-driver" data-driver-state="${h(row?.state||'NONE')}"><h3>合作推进责任</h3><p>${h(row?labels[row.state]:'尚未选定合作责任')}${d.stale&&d.held?' · 原要求或方案版本已变化':''}${row?.reason==='CONTROLLER_RESTART'?' · 服务重启后等待重新核对':''}</p><p class="sp-note">选定使用已保存方案，只登记本任务的推进责任，不发送模型；未保存修改不参与本次选定。暂停保留责任，释放后可准备独立 Work 或人工接管。</p><div class="sp-row">${d.held?button('暂停合作责任','pause',d.can_pause)+button('重新选定合作责任','resume',d.can_resume)+button('释放合作责任','release',d.can_release):button('选定合作推进','acquire',d.can_acquire)}</div>${d.held?'<p class="sp-note">持有责任期间不能用普通保存替换方案。已检查的依赖调整可在下方应用；若要求已变化，先释放责任并修订。</p>':''}${d.issues.length?`<details class="wi-driver-issues"><summary>需核对的条件（${d.issues.length}）</summary>${d.issues.slice(0,12).map(i=>`<p>${h(i.message)}${i.task_id?` · <a href="#control/task/${h(i.task_id)}">打开关联执行核对</a>`:''}</p>`).join('')}${d.issues.length>12?'<p>其余条件见关联执行的完整控制记录。</p>':''}</details>`:''}<p class="sp-note">在下方授权后按当前模式准入派发；选定、暂停和释放本身均不启动工作。</p></div>`;
  }
  function handoffConfig(d){const options=[['','请选择单元'],...d.units.map(u=>[u.key,u.title||u.key])];
    return `<fieldset data-handoff-config ${d.handoff_locked?'disabled':''}><legend>同路径交接配置</legend>${S.select('是否配置同路径交接','handoff_enabled',[['no','不配置'],['yes','明确配置一项交接']],d.handoff_enabled?'yes':'no')}${S.select('交接前版单元','handoff_source',options,d.handoff_source)}${S.select('交接后继单元','handoff_target',options,d.handoff_target)}${S.text('交接完整文件路径（每行一项）','handoff_paths',d.handoff_paths,'maxlength="4800"')}<p class="sp-note">新任务可为固定流程、负责人或主持人保存一项关系；负责人／主持人须声明提案单元。两端须明确声明相同的完整 outputs 和写入范围；后继为整合职责、直接依赖前版，并使用文件或检查门槛，最多同时工作数为1。上面的配置不会自动修改工作单元。${d.handoff_locked?'当前交接身份已锁定；普通保存和释放责任均不能变更。':''}</p></fieldset>`;
  }
  function handoffDraft(item,key){
    const r=item.file_handoff_revision;
    if(!r?.edit_units.length)return null;
    const c=item.cooperation,scope={id:item.id,revision:item.revision,current_driver:item.driver.reference,
      plan:{id:item.teamwork.plan.id,version:item.teamwork.plan.version},authorization:c.authorization_reference,frontier:c.frontier};
    if(!key&&v.handoffDraft?.itemId===item.id&&(v.handoffDraft.touched||JSON.stringify(v.handoffDraft.scope)===JSON.stringify(scope)))return v.handoffDraft;
    const request=r.state==='VALID'&&r.candidate_type==='DEFINITION_REVISION'?r.record.snapshot.preview.request:null;
    const saved=!key&&request?.changes.length===1?request.changes[0]:null;
    const u=r.edit_units.find(u=>u.key===(key||saved?.unit_key))||r.edit_units[0];
    return v.handoffDraft={itemId:item.id,scope,
      original:u,requirements:r.work_requirements_version,key:u.key,title:u.title,goal:u.goal,acceptance:u.acceptance,
      checkedReference:saved?r.reference:null,depends:[...u.depends],basis_reason:'',reason:'',dirty:!saved,touched:false,
      ...(saved?{...saved.definition,member:undefined,basis_reason:saved.basis.reason,reason:request.reason}: {})};
  }
  function captureHandoff(form){
    const d=v.handoffDraft;if(!d||!form)return;
    const f=new FormData(form);
    for(const k of ['title','goal','acceptance','basis_reason','reason'])d[k]=String(f.get('revision_'+k)||'');
    d.depends=f.getAll('revision_depends').map(String);
  }
  function handoffRevisionView(item){
    const r=item.file_handoff_revision;if(!r)return '';
    const p=r.candidate_type==='DEFINITION_REVISION'?r.record?.snapshot.preview:null,labels={NOT_CHECKED:'尚未检查明确候选',VALID:'已保存明确候选',STALE:'候选已失效，请重新核对',CONSUMED:'明确候选已应用'};
    const d=handoffDraft(item),names=keys=>keys.map(k=>item.teamwork.plan.units.find(u=>u.key===k)?.title||k).join('、');
    const fresh=!d||JSON.stringify(d.scope)===JSON.stringify({...cooperationScope(),authorization:item.cooperation.authorization_reference,frontier:item.cooperation.frontier});
    return `<details class="wi-artifacts" data-handoff-revision ${v.handoffOpen?'open':''}><summary>交接后的局部文字职责修订</summary><p data-handoff-revision-state="${h(r.state)}">${h(labels[r.state])}</p><p>保留原交接、两端及完整上游、成员、文件声明、权限和原累计授权。细化范围外的已有文字工作可先检查候选，再明确应用；应用后暂停，恢复后按完整依赖逐项派发。</p><p>受保护工作：${h(names(r.protected_keys))}。</p>${r.chain_depth!==null?`<p data-handoff-chain-depth="${h(r.chain_depth)}">已保存 ${h(r.chain_depth)} 次职责修订。${r.chain_depth?'明确恢复合作责任后，可继续检查下一次局部修订。':''}</p><details data-handoff-path><summary>当前方案与历史来源</summary><p>当前消费者方案 ${h(r.consumer_plan.id)}<br>版本 ${h(r.consumer_plan.version)}</p>${r.source_plans.map(p=>`<p>历史来源方案 ${h(p.id)}<br>版本 ${h(p.version)}</p>`).join('')}</details>`:''}${r.problems.map(x=>`<p>${h(x)}</p>`).join('')}${d?`<form id="work-item-handoff-revision">${S.select('原文字工作','revision_unit',r.edit_units.map(u=>[u.key,u.title]),d.key)}<p data-handoff-original>原目标：${h(d.original.goal)}<br>原完成要求：${h(d.original.acceptance)}</p><details><summary>准确原职责依据</summary><p>原定义版本 ${h(d.original.revision)}<br>原任务要求版本 ${h(d.requirements)}</p></details>${S.field('修订后的工作名称','revision_title',d.title,'required maxlength="160"')}${S.text('局部目标','revision_goal',d.goal,'required maxlength="4000"')}${S.text('局部完成要求','revision_acceptance',d.acceptance,'required maxlength="4000"')}<div class="wi-dependencies"><p>完整前置工作</p>${item.teamwork.plan.units.filter(u=>u.key!==d.key).map(u=>`<label><input type="checkbox" name="revision_depends" value="${h(u.key)}" ${d.depends.includes(u.key)?'checked':''}>${h(u.title)}</label>`).join('')}</div>${S.text('原职责细化依据','revision_basis_reason',d.basis_reason,'required maxlength="2000"')}${S.text('本次修订说明','revision_reason',d.reason,'required maxlength="2000"')}<p class="sp-note">检查使用本表单保存的准确原版本；刷新保留输入，版本变化时须明确重新读取原职责。</p>${!fresh?'<p data-handoff-draft-stale>方案、责任或执行前沿已变化。输入已保留，请重新读取原职责后检查。</p>':''}<div class="sp-row"><button type="submit" ${r.can_check&&fresh?'':'disabled'}>检查局部文字候选</button>${S.button('重新读取原职责','type="button" data-handoff-revision-reload')}</div></form>`:''}${p?`<div data-handoff-candidate="${h(r.reference.version)}"><h4>${r.state==='CONSUMED'?'已应用的历史文字候选':'已保存的局部文字候选'}</h4><p>${h(p.reason)}</p><p>修订：${h(names(p.direct_changes))}。受影响：${h(names(p.affected_units))}。</p><p>历史退役前版：${h(p.retired_source)}；当前完成后继：${h(p.completed_target)}。</p>${p.units.filter(u=>u.basis).map(u=>`<details data-handoff-candidate-unit="${h(u.key)}" open><summary>${h(u.candidate_definition.title)}</summary><p>原目标：${h(u.original_definition.goal)}</p><p>候选目标：${h(u.candidate_definition.goal)}</p><p>候选完成要求：${h(u.candidate_definition.acceptance)}</p><p>原职责依据：${h(u.basis.reason)}</p><p>完整前置：${h(names(u.candidate_depends))}</p></details>`).join('')}<p>候选检查时累计 ${h(p.usage.attempts)} 次；原上限 ${h(p.authorization.max_attempts_total)} 次，原新增派发截止 ${h(S.when(p.authorization.dispatch_before))}。</p>${p.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}<details><summary>准确候选记录</summary><p>候选版本 ${h(r.reference.version)}<br>原方案 ${h(p.base_plan.version)}</p></details>${r.state!=='CONSUMED'?S.button('应用已检查的文字候选',`data-handoff-revision-apply ${r.can_apply&&fresh&&!d?.dirty&&sameHandoff(d?.checkedReference,r.reference)?'':'disabled'}`):`<p>新方案已保存；${item.driver.record?.state==='PAUSED'?'原责任已暂停，请在合作责任区明确恢复。':'当前合作责任状态见上方责任区。'}</p>`}</div>`:''}</details>`;
  }
  const sameHandoff=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  function handoffTextScope(item){return {id:item.id,revision:item.revision,current_driver:item.driver.reference,
    plan:{id:item.teamwork.plan.id,version:item.teamwork.plan.version},authorization:item.cooperation.authorization_reference,frontier:item.cooperation.frontier};}
  function handoffTextDraft(item){
    const r=item.file_handoff_revision,t=r?.text_rework;if(!t?.sources)return v.textDraft?.itemId===item.id?v.textDraft:null;
    const scope=handoffTextScope(item);
    if(v.textDraft?.itemId===item.id&&(v.textDraft.touched||sameHandoff(v.textDraft.scope,scope)))return v.textDraft;
    const saved=r.candidate_type==='TEXT_REWORK'&&r.state==='VALID'?r.record.snapshot.preview.request:null;
    const hydrate=saved&&sameHandoff(scope,Object.fromEntries(Object.keys(scope).map(k=>[k,saved[k]])));
    return v.textDraft={itemId:item.id,scope,options:structuredClone(t.sources.units.filter(u=>u.selectable)),
      selected:hydrate?saved.selections.map(u=>u.unit_key):[],reason:hydrate?saved.reason:'',
      touched:false,dirty:!hydrate,checkedReference:hydrate?r.reference:null};
  }
  function captureHandoffText(form){
    const d=v.textDraft;if(!d||!form)return;
    const f=new FormData(form);d.selected=f.getAll('text_rework_source').map(String);d.reason=String(f.get('text_rework_reason')||'');
  }
  function handoffTextSelections(d){return d.options.filter(u=>d.selected.includes(u.key)).map(u=>({unit_key:u.key,
    source_run_id:u.evidence_reference.run_id,source_version:u.evidence_reference.version}));}
  function handoffTextIdentity(source,version){return source?`<details data-text-source="${h(source.run_id)}"><summary>准确原运行与证据</summary><p>原方案 ${h(source.plan_id)}<br>方案版本 ${h(source.plan_version)}<br>原运行 ${h(source.run_id)}<br>完整证据版本 ${h(version||'')}</p></details>`:'';}
  function handoffTextView(item){
    const r=item.file_handoff_revision,t=r?.text_rework;if(!t||!item.teamwork?.plan.file_handoff_lineage)return '';
    const d=handoffTextDraft(item),p=r.candidate_type==='TEXT_REWORK'?r.record?.snapshot.preview:null;
    const names=keys=>keys.map(k=>item.teamwork.plan.units.find(u=>u.key===k)?.title||k).join('、');
    const fresh=!!d&&sameHandoff(d.scope,handoffTextScope(item));
    const applicable=!!(p&&d&&r.state==='VALID'&&r.can_apply&&fresh&&!d.dirty&&sameHandoff(d.checkedReference,r.reference));
    const states={HISTORICAL_RETIRED:'保留退役历史',AFFECTED:'新方案重新执行',ELIGIBLE_RESULT:'沿用准确原来源',NOT_RUN:'尚未运行'};
    return `<details class="wi-artifacts" data-handoff-text-rework ${v.textOpen?'open':''}><summary>重做已运行文字工作</summary>
      <p>选择准确原成果，保留工作定义和成员。检查将列出所有无结果种子及下游影响；应用后保存新方案并暂停合作责任，明确恢复后逐项执行。</p>
      ${r.candidate_type==='DEFINITION_REVISION'?'<p data-text-other-candidate>当前候选是职责修订，可在上方查看；检查文字返工会替换当前待应用候选。</p>':''}
      ${t.problems.map(x=>`<p>${h(x)}</p>`).join('')}
      ${d?`<form id="work-item-handoff-text-rework"><div class="wi-dependencies"><p>选择原文字来源（最多20项）</p>
        ${d.options.map(u=>`<div data-text-option="${h(u.key)}"><label><input type="checkbox" name="text_rework_source" value="${h(u.key)}" ${d.selected.includes(u.key)?'checked':''}>${h(u.definition.title)} · ${h(u.qualification==='KNOWN_TERMINAL_WITHOUT_RESULT'?'终态无可用结果':'已有准确结果')}</label>${handoffTextIdentity(u.source,u.evidence_reference.version)}</div>`).join('')||'<p>当前没有可选择的已运行普通文字来源。</p>'}</div>
        ${S.text('本次文字返工原因','text_rework_reason',d.reason,'required maxlength="2000"')}
        <p class="sp-note">输入与选择绑定本次准确任务、方案、责任、授权和执行前沿。刷新保留已编辑内容，版本变化后须明确重新读取来源。</p>
        ${!fresh?'<p data-text-draft-stale>任务、方案、责任、授权或执行前沿已变化。请重新读取准确来源。</p>':''}
        <div class="sp-row"><button type="submit" ${t.can_check&&fresh&&d.options.length?'':'disabled'}>检查文字返工候选</button>${S.button('重新读取准确来源','type="button" data-text-rework-reload')}</div></form>`:''}
      ${p?`<div data-text-candidate="${h(r.reference.version)}"><h4>${r.state==='CONSUMED'?'已应用的文字返工候选':'已检查的文字返工候选'}</h4>
        <p data-text-candidate-state="${h(r.state)}">${h({VALID:'候选有效',STALE:'候选已失效',CONSUMED:'候选已应用'}[r.state]||r.state)}</p><p>${h(p.reason)}</p>
        <p data-text-direct>直接选择：${h(names(p.selections.map(u=>u.unit_key)))}。</p>
        <p data-text-seeds>全部无结果种子：${h(names(p.units.filter(u=>u.reasons.includes('KNOWN_TERMINAL_WITHOUT_RESULT')).map(u=>u.key)))||'无'}。</p>
        <p data-text-affected>完整影响：${h(names(p.affected_units))}。</p><p>沿用来源：${h(names(p.eligible_results))}。</p>
        ${p.units.map(u=>{const source=u.prior_source?.source_ref||u.source;
          const evidence=u.prior_source?.evidence.version||p.ancestry_audit.frontier.find(e=>e.run_id===source?.run_id)?.evidence.version;
          return `<details data-text-candidate-unit="${h(u.key)}"><summary>${h(u.original_definition.title)} · ${h(states[u.state])}</summary><p>原目标：${h(u.original_definition.goal)}<br>原完成要求：${h(u.original_definition.acceptance)}</p>${u.prior_source?'<p>此原来源保留为返工历史依据，新执行仍读取新方案依赖。</p>':''}${handoffTextIdentity(source,evidence)}</details>`;}).join('')}
        <details><summary>本次候选和原方案</summary><p>候选版本 ${h(r.reference.version)}<br>原方案 ${h(p.base_plan.id)}<br>原方案版本 ${h(p.base_plan.version)}</p></details>
        <p>累计 ${h(p.usage.attempts)} 次，原上限 ${h(p.authorization.max_attempts_total)} 次；原派发截止 ${h(S.when(p.authorization.dispatch_before))}。</p>
        ${p.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}
        ${r.state==='CONSUMED'?'<p data-text-applied-paused>新方案已保存。合作责任状态见上方；暂停时须明确重新选定合作责任后执行。</p>':S.button('应用已检查的文字返工',`data-text-rework-apply ${applicable?'':'disabled'}`)}</div>`:''}</details>`;
  }

  function handoffFileScope(item){return {...handoffTextScope(item),material:item.file_handoff_revision?.file_rework.scope_version};}
  function handoffFileDraft(item){
    const r=item.file_handoff_revision,f=r?.file_rework;if(!f?.scope_version)return v.fileDraft?.itemId===item.id?v.fileDraft:null;
    const scope=handoffFileScope(item);if(v.fileDraft?.itemId===item.id&&(v.fileDraft.touched||sameHandoff(v.fileDraft.scope,scope)))return v.fileDraft;
    const saved=r.candidate_type==='FILE_REWORK'&&r.state==='VALID'?r.record.snapshot.preview.request:null;
    const hydrate=saved&&sameHandoff(handoffTextScope(item),Object.fromEntries(Object.keys(handoffTextScope(item)).map(k=>[k,saved[k]])));
    return v.fileDraft={itemId:item.id,scope,options:structuredClone(f.sources.units.filter(u=>u.selectable)),
      selected:hydrate?saved.selections.map(u=>u.unit_key):[],reason:hydrate?saved.reason:'',touched:false,dirty:!hydrate,
      checkedReference:hydrate?r.reference:null,checkedRequest:hydrate?structuredClone(saved):null};
  }
  function captureHandoffFile(form){const d=v.fileDraft;if(!d||!form)return;const f=new FormData(form);
    d.selected=f.getAll('file_rework_source').map(String);d.reason=String(f.get('file_rework_reason')||'');}
  function handoffFileRequest(d){const {material,...scope}=d.scope;return {...scope,reason:d.reason,
    selections:d.options.filter(u=>d.selected.includes(u.key)).map(u=>({unit_key:u.key,source_run_id:u.evidence_reference.run_id,source_version:u.evidence_reference.version}))};}
  function handoffFileView(item){
    const r=item.file_handoff_revision,f=r?.file_rework;if(!f||!item.teamwork?.plan.file_handoff_lineage)return '';
    const d=handoffFileDraft(item),p=r.candidate_type==='FILE_REWORK'?r.record?.snapshot.preview:null;
    const fresh=!!d&&sameHandoff(d.scope,handoffFileScope(item)),applicable=p&&r.can_apply&&fresh&&!d.dirty&&
      ![v.handoffDraft,v.textDraft,v.unitDraft].some(x=>x?.touched&&x.dirty)&&sameHandoff(d.checkedReference,r.reference)&&sameUnitRequest(d.checkedRequest,p.request);
    const states={AFFECTED:'重新执行并取得新产物',ELIGIBLE_RESULT:'沿用准确原来源',NOT_RUN:'尚未运行',HISTORICAL_RETIRED:'保留退役历史'};
    return `<details class="wi-artifacts" data-handoff-file-rework ${v.fileOpen?'open':''}><summary>重做已捕获文件工作</summary>
      <p>选择根交接完成后已运行并捕获的普通文件来源，保留完整工作定义、成员和声明路径。检查列出全部下游影响；应用后暂停，明确恢复后执行并捕获新文件。</p>
      ${f.problems.map(x=>`<p>${h(x)}</p>`).join('')}${r.reference&&r.candidate_type!=='FILE_REWORK'?'<p>检查本表单会替换当前待应用候选。</p>':''}
      ${d?`<form id="work-item-handoff-file-rework"><div class="wi-dependencies"><p>选择准确原来源（至少一项文件工作，最多20项）</p>
        ${d.options.map(u=>`<div data-file-option="${h(u.key)}"><label><input type="checkbox" name="file_rework_source" value="${h(u.key)}" ${d.selected.includes(u.key)?'checked':''}>${h(u.definition.title)} · ${h(u.definition.outputs.length?'已捕获文件':'补充文字来源')}</label>${handoffTextIdentity(u.source,u.evidence_reference.version)}<details><summary>完整原定义与冻结成员</summary>${S.raw(u.definition)}</details></div>`).join('')||'<p>当前没有可选择的准确文件来源。</p>'}</div>
        ${S.text('本次文件返工原因','file_rework_reason',d.reason,'required maxlength="2000"')}
        ${!fresh?'<p data-file-draft-stale>任务、方案、责任、授权、前沿或文件材料已变化。输入已保留，请明确重新读取。</p>':''}
        <div class="sp-row"><button type="submit" ${f.can_check&&fresh?'':'disabled'}>检查文件返工候选</button>${S.button('重新读取文件来源与材料','type="button" data-file-rework-reload')}</div></form>`:''}
      ${f.original_files?`<details data-file-originals><summary>全部原输入、文件快照与当前观察</summary>${S.raw({original_files:f.original_files,current_project_observations:f.current_project_observations})}</details>`:''}
      ${p?`<div data-file-candidate="${h(r.reference.version)}"><h4>${r.state==='CONSUMED'?'已应用的文件返工候选':'已检查的文件返工候选'}</h4><p data-file-candidate-state="${h(r.state)}">${h(r.state)}</p><p>${h(p.reason)}</p><p data-file-affected>完整影响：${h(p.affected_units.join('、'))}。沿用来源：${h(p.eligible_results.join('、'))}。</p>
        <details data-file-write-scope open><summary>准确替代路径与写前基线</summary>${Object.entries(p.write_scope).map(([key,paths])=>`<p>${h(key)}：${h(paths.join("、"))}</p>`).join('')}<p>写前已保存 ${h(Object.keys(p.ancestry_audit.file_audit.write_before_observation.manifest).length)} 项准确文件。</p><details><summary>查看完整写前文件版本</summary>${S.raw(p.ancestry_audit.file_audit.write_before_observation)}</details></details>
        ${p.units.map(u=>`<details data-file-candidate-key="${h(u.key)}"><summary>${h(u.original_definition.title)} · ${h(states[u.state])}</summary>${S.raw(u.original_definition)}${handoffTextIdentity(u.prior_source?.source_ref||u.source,u.prior_source?.evidence.version)}${u.prior_source?`<details><summary>准确原输入与文件证据</summary>${S.raw(u.prior_source)}</details>`:''}</details>`).join('')}
        <details><summary>完整旧快照与替代审计</summary>${S.raw(p.ancestry_audit.file_audit)}</details><p>原累计 ${h(p.usage.attempts)} 次，上限 ${h(p.authorization.max_attempts_total)} 次；原派发截止 ${h(S.when(p.authorization.dispatch_before))}。</p>${p.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}
        ${r.state==='CONSUMED'?'<p data-file-applied-paused>新方案已保存。暂停时须明确重新选定合作责任；新运行成功后还须捕获文件。</p>':S.button('应用已检查的文件返工候选',`data-handoff-file-rework-apply ${applicable?'':'disabled'}`)}</div>`:''}
      ${(f.capture_states||[]).map(c=>`<p data-file-capture="${h(c.key)}" data-capture-state="${h(c.state)}">${h(c.key)} · ${h(c.state==='READY_TO_CAPTURE'?'新运行已完成，等待捕获文件':c.state)} · ${h(c.check_status)}<br>准确运行 ${h(c.run_id)}</p>`).join('')}
    </details>`;
  }

  const unitStable=value=>Array.isArray(value)?value.map(unitStable):value&&typeof value==='object'?
    Object.fromEntries(Object.keys(value).sort().map(key=>[key,unitStable(value[key])])):value;
  const sameUnitRequest=(a,b)=>JSON.stringify(unitStable(a))===JSON.stringify(unitStable(b));
  function handoffUnitScope(item){return {...handoffTextScope(item),material:item.file_handoff_revision?.unit_revision.scope_version};}
  function handoffUnitRow(d,basis=d.bases[0],operation='add'){
    let key=basis.key;if(operation==='add'){let i=1;while(d.history.includes('new'+i)||d.rows.some(r=>r.key==='new'+i))i++;key='new'+i;}
    return {operation,key,basis:basis.key,title:basis.title,goal:basis.goal,acceptance:basis.acceptance,
      member:d.members.findIndex(m=>sameHandoff(m.reference,basis.member.reference)),depends:[...basis.depends],basis_reason:''};
  }
  function handoffUnitDraft(item){
    const r=item.file_handoff_revision,u=r?.unit_revision;
    if(!u?.scope_version)return v.unitDraft?.itemId===item.id?v.unitDraft:null;
    const scope=handoffUnitScope(item);
    if(v.unitDraft?.itemId===item.id&&(v.unitDraft.touched||sameHandoff(v.unitDraft.scope,scope)))return v.unitDraft;
    const saved=r.candidate_type==='UNIT_REVISION'&&r.state==='VALID'?r.record.snapshot.preview.request:null;
    const hydrate=saved&&sameHandoff(handoffTextScope(item),Object.fromEntries(Object.keys(handoffTextScope(item)).map(k=>[k,saved[k]])));
    const d={itemId:item.id,scope,bases:structuredClone(u.basis_units),members:structuredClone(u.members),
      history:[...u.historical_keys],units:structuredClone(item.teamwork.plan.units),requirements:u.work_requirements_version,
      rows:[],reason:hydrate?saved.reason:'',dirty:!hydrate,touched:false,checkedReference:hydrate?r.reference:null,
      checkedRequest:hydrate?structuredClone(saved):null};
    d.rows=hydrate?saved.changes.map(c=>({operation:c.operation,key:c.unit_key,basis:c.basis.unit_key,
      ...c.definition,member:d.members.findIndex(m=>sameHandoff(m.reference,c.definition.member)),basis_reason:c.basis.reason})):
      d.bases.length?[handoffUnitRow(d)]:[];
    return v.unitDraft=d;
  }
  function captureHandoffUnit(form){
    const d=v.unitDraft;if(!d||!form)return;const f=new FormData(form);
    d.reason=String(f.get('unit_revision_reason')||'');
    d.rows.forEach((r,i)=>{for(const k of ['operation','key','basis','title','goal','acceptance','member','basis_reason'])r[k]=String(f.get(`ur_${i}_${k}`)||'');r.depends=f.getAll(`ur_${i}_depends`).map(String);});
  }
  function handoffUnitRequest(d){const {material,...scope}=d.scope;return {...scope,reason:d.reason,changes:d.rows.map(r=>{
    const b=d.bases.find(b=>b.key===r.basis);if(!b)throw Error('请重新读取准确原职责。');
    return {operation:r.operation,unit_key:r.operation==='revise'?b.key:r.key,
      basis:{unit_key:b.key,unit_revision:b.revision,work_requirements_version:d.requirements,reason:r.basis_reason},
      definition:{title:r.title,goal:r.goal,acceptance:r.acceptance,member:d.members[Number(r.member)]?.reference,depends:r.depends}};
  })};}
  function handoffUnitFresh(item,d){return !!d&&sameHandoff(d.scope,handoffUnitScope(item));}
  function handoffUnitOtherDirty(){return [v.handoffDraft,v.textDraft,v.fileDraft].some(d=>d?.touched&&d.dirty);}
  function handoffUnitView(item){
    const r=item.file_handoff_revision,u=r?.unit_revision;if(!u||!item.teamwork?.plan.file_handoff_lineage)return '';
    const d=handoffUnitDraft(item),p=r.candidate_type==='UNIT_REVISION'?r.record?.snapshot.preview:null;
    const fresh=handoffUnitFresh(item,d),canApply=p&&r.can_apply&&fresh&&!d.dirty&&!handoffUnitOtherDirty()&&sameHandoff(d.checkedReference,r.reference)&&sameUnitRequest(d.checkedRequest,p.request);
    const states={ADDED:'新增工作',AFFECTED:'新方案重新执行',ELIGIBLE_RESULT:'沿用准确原来源',NOT_RUN:'尚未运行',HISTORICAL_RETIRED:'保留退役历史'};
    return `<details class="wi-artifacts" data-handoff-unit-revision ${v.unitOpen?'open':''}><summary>新增与修订普通文字工作</summary>
      <p>从原普通职责派生新工作，或修订原工作。选择原方案冻结的成员和完整前置；检查后保存新方案并暂停合作责任，明确恢复后执行。</p>
      ${u.problems.map(x=>`<p>${h(x)}</p>`).join('')}${r.reference&&r.candidate_type!=='UNIT_REVISION'?'<p data-unit-other-candidate>当前还有其他类型候选；检查本表单会替换当前候选。</p>':''}
      ${d?`<form id="work-item-handoff-unit-revision">${d.rows.map((row,i)=>{
        const b=d.bases.find(b=>b.key===row.basis),deps=[...d.units,...d.rows.filter((x,j)=>j!==i&&x.operation==='add').map(x=>({key:x.key,title:x.title}))].filter(x=>x.key!==(row.operation==='revise'?row.basis:row.key));
        return `<fieldset data-unit-change="${i}"><legend>改动 ${i+1}</legend>
          ${S.select('操作',`ur_${i}_operation`,[['add','新增'],['revise','修订']],row.operation)}
          ${S.select('准确原职责依据',`ur_${i}_basis`,d.bases.map(b=>[b.key,b.title]),row.basis)}
          <p data-unit-basis>原目标：${h(b?.goal)}<br>原完成要求：${h(b?.acceptance)}<br>原定义版本 ${h(b?.revision)}<br>原任务要求版本 ${h(d.requirements)}</p>
          ${S.field('新增工作身份',`ur_${i}_key`,row.key,`required maxlength="64" pattern="[A-Za-z0-9_-]+" ${row.operation==='revise'?'readonly':''}`)}
          ${S.field('工作名称',`ur_${i}_title`,row.title,'required maxlength="160"')}${S.text('局部目标',`ur_${i}_goal`,row.goal,'required maxlength="4000"')}${S.text('局部完成要求',`ur_${i}_acceptance`,row.acceptance,'required maxlength="4000"')}
          ${S.select('原方案冻结成员',`ur_${i}_member`,d.members.map((m,n)=>[String(n),m.name+' · '+m.model]),String(row.member))}
          <details data-unit-member="${i}"><summary>原成员完整引用</summary>${S.raw(d.members[Number(row.member)])}</details>
          <div class="wi-dependencies" data-unit-deps="${i}"><p>完整前置工作（含本次新增身份）</p>${deps.map(x=>`<label><input type="checkbox" name="ur_${i}_depends" value="${h(x.key)}" ${row.depends.includes(x.key)?'checked':''}>${h(x.title)} · ${h(x.key)}</label>`).join('')}</div>
          ${S.text('原职责与派生范围依据',`ur_${i}_basis_reason`,row.basis_reason,'required maxlength="2000"')}
          ${S.button('移除此项',`type="button" data-unit-remove="${i}" ${d.rows.length===1?'disabled':''}`)}</fieldset>`;
      }).join('')}${S.text('本次普通文字修订说明','unit_revision_reason',d.reason,'required maxlength="2000"')}
        <details><summary>已使用的全部历史身份</summary><p>${h(d.history.join('、'))}</p></details>
        ${!fresh?'<p data-unit-draft-stale>任务、方案、责任、授权、前沿或实际材料已变化。输入已保留，请明确重新读取。</p>':''}
        <div class="sp-row">${S.button('再加一项改动',`type="button" data-unit-add ${d.rows.length>=20||!fresh?'disabled':''}`)}<button type="submit" ${u.can_check&&fresh&&d.rows.length?'':'disabled'}>检查普通文字候选</button>${S.button('重新读取原方案与材料','type="button" data-unit-reload')}</div></form>`:''}
      ${p?`<div data-unit-candidate="${h(r.reference.version)}"><h4>${r.state==='CONSUMED'?'已应用的普通文字候选':'已检查的普通文字候选'}</h4><p data-unit-candidate-state="${h(r.state)}">${h(r.state)}</p><p>${h(p.reason)}</p><p>完整影响：${h(p.affected_units.join('、'))}；沿用来源：${h(p.eligible_results.join('、'))}。</p>
        ${p.units.map(x=>{const source=x.prior_source?.source_ref||x.source;return `<details data-unit-candidate-key="${h(x.key)}"><summary>${h((x.candidate_definition||x.original_definition).title)} · ${h(states[x.state])}</summary><p>原目标：${h(x.original_definition?.goal||'新增，无原定义')}<br>候选目标：${h(x.candidate_definition.goal)}<br>候选完成要求：${h(x.candidate_definition.acceptance)}<br>完整前置：${h(x.candidate_definition.depends.join('、'))}<br>操作：${h(x.operation||'保持定义')}<br>依据：${h(x.basis?.reason||'')}</p>${S.raw(x.candidate_definition.member)}${handoffTextIdentity(source,x.prior_source?.evidence.version||p.ancestry_audit.frontier.find(e=>e.run_id===source?.run_id)?.evidence.version)}${x.prior_source?`<details><summary>准确旧输入与证据</summary>${S.raw(x.prior_source)}</details>`:''}${x.historical_source?`<details><summary>退役前版准确历史</summary>${S.raw(x.historical_source)}</details>`:''}</details>`;}).join('')}
        <details><summary>全部旧输入、快照与来源证据</summary>${S.raw(p.ancestry_audit.protected_history)}</details><p>原累计 ${h(p.usage.attempts)} 次，上限 ${h(p.authorization.max_attempts_total)} 次；原派发截止 ${h(S.when(p.authorization.dispatch_before))}。</p><details><summary>准确候选与原方案</summary><p>${h(r.reference.version)}<br>${h(p.base_plan.id)}<br>${h(p.base_plan.version)}</p></details>${p.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}
        ${r.state==='CONSUMED'?'<p data-unit-applied-paused>新方案已保存。暂停时须明确重新选定合作责任后执行。</p>':S.button('应用已检查的普通文字候选',`data-unit-apply ${canApply?'':'disabled'}`)}</div>`:''}</details>`;
  }

  document.addEventListener('input',e=>{
    if(!current().active||!e.target.closest('#work-item-handoff-unit-revision'))return;
    captureHandoffUnit(e.target.form);v.unitDraft.dirty=true;v.unitDraft.touched=true;
    const b=$('#main [data-unit-apply]');if(b)b.disabled=true;
  });
  document.addEventListener('change',e=>{
    if(!current().active||!e.target.closest('#work-item-handoff-unit-revision'))return;
    v.unitOpen=!!$('#main [data-handoff-unit-revision]')?.open;
    captureHandoffUnit(e.target.form);const d=v.unitDraft;d.dirty=true;d.touched=true;
    const m=e.target.name.match(/^ur_(\d+)_(operation|basis|key|member)$/);if(!m)return;
    if(!handoffUnitFresh(v.data.work_items[0],d)){draw();return;}
    const i=Number(m[1]),r=d.rows[i];
    if(m[2]==='member'){
      $('#main [data-unit-member="'+i+'"]').innerHTML='<summary>原成员完整引用</summary>'+S.raw(d.members[Number(r.member)]);return;
    }
    if(m[2]==='key'){
      d.rows.forEach((row,n)=>{
        const deps=[...d.units,...d.rows.filter((x,j)=>j!==n&&x.operation==='add').map(x=>({key:x.key,title:x.title}))].filter(x=>x.key!==(row.operation==='revise'?row.basis:row.key));
        $('#main [data-unit-deps="'+n+'"]').innerHTML='<p>完整前置工作（含本次新增身份）</p>'+deps.map(x=>`<label><input type="checkbox" name="ur_${n}_depends" value="${h(x.key)}" ${row.depends.includes(x.key)?'checked':''}>${h(x.title)} · ${h(x.key)}</label>`).join('');
      });return;
    }
    if(['operation','basis'].includes(m[2]))d.rows[i]=handoffUnitRow(d,d.bases.find(b=>b.key===r.basis),r.operation);
    draw();
  });
  document.addEventListener('click',e=>{
    if(!current().active)return;const b=e.target.closest('[data-unit-add],[data-unit-remove],[data-unit-reload]');if(!b)return;
    act(async()=>{if(b.hasAttribute('data-unit-reload')){v.unitDraft=null;v.fileDraft=null;await load();return;}
      const d=v.unitDraft;if(!handoffUnitFresh(v.data.work_items[0],d))throw Error('请先重新读取原方案与材料。');
      if(b.hasAttribute('data-unit-add')&&d.rows.length<20)d.rows.push(handoffUnitRow(d));
      else if(b.hasAttribute('data-unit-remove')&&d.rows.length>1)d.rows.splice(Number(b.dataset.unitRemove),1);
      d.dirty=true;d.touched=true;draw();});
  });
  document.addEventListener('submit',e=>{
    if(!current().active||e.target.id!=='work-item-handoff-unit-revision')return;e.preventDefault();
    act(async()=>{const d=v.unitDraft;if(!handoffUnitFresh(v.data.work_items[0],d))throw Error('准确原方案或材料已变化，请明确重新读取。');
      const request=handoffUnitRequest(d),receipt=await S.command('work-item.file-handoff-unit-revision-check',request);
      d.checkedReference=receipt.candidate;d.checkedRequest=structuredClone(request);d.dirty=false;d.touched=true;await load();});
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-unit-apply]'))return;
    act(async()=>{const item=v.data.work_items[0],r=item.file_handoff_revision,d=v.unitDraft,p=r.record?.snapshot.preview;
      if(r.candidate_type!=='UNIT_REVISION'||!r.can_apply||!handoffUnitFresh(item,d)||d.dirty||handoffUnitOtherDirty()||!sameHandoff(d.checkedReference,r.reference)||!sameUnitRequest(d.checkedRequest,p.request))return;
      const {id,revision,current_driver,plan,authorization,frontier}=p.request;
      const payload={id,revision,current_driver,plan,authorization,frontier,candidate:r.reference};
      if(!await S.confirm('应用普通文字候选','保存所示完整新方案并暂停合作责任；保留原累计上限、截止和权限。明确恢复后执行。',JSON.stringify({changes:p.request.changes,affected:p.affected_units,candidate:r.reference},null,2)))return;
      await S.command('work-item.file-handoff-unit-revision-apply',payload);v.teamDraft=null;v.handoffDraft=null;v.textDraft=null;v.unitDraft=null;v.fileDraft=null;await load();});
  });

  function teamwork(item,compact=false){const d=teamDraft(item),t=item.teamwork,options=[['','请选择成员'],...d.options.map(m=>[m.id,m.label])],labels={fixed:'固定流程',leader:'负责人带队',host_experts:'主持人—专家'},blocks={COOPERATIVE_DISPATCH_NOT_CONNECTED:'请先配置由负责人／主持人承担的提案单元',RESOURCE_CHANGED_OR_UNAVAILABLE:'成员或连接已变化／不可用',WORK_ITEM_CHANGED:'任务要求或版本已变化',WORK_ITEM_NOT_OPEN:'任务当前未处于进行中',HARD_CASH_LIMIT_UNSUPPORTED:'当前无法强制落实硬费用上限'};
    const operations=`${driverView(item)}${cooperation(item,compact)}${fileHandoffView(item)}${handoffRevisionView(item)}${handoffTextView(item)}${handoffUnitView(item)}${handoffFileView(item)}`;
    const editor=`${t?`<p data-team-plan="${h(t.plan.id)}">已保存：${h(labels[t.plan.mode])} · ${t.plan.units.length} 项工作${t.stale?' · 要求已变化，请修订':''}</p><p>待满足：${t.blockers.length?t.blockers.map(b=>h(blocks[b]||b)).join('；'):'方案静态条件已具备，实际运行另行核对'}。</p><p>结构批次：${t.structure_batches.map(w=>w.map(k=>h(t.plan.units.find(u=>u.key===k)?.title||k)).join('、')).join(' → ')}。</p><p class="sp-note">按依赖、写入范围和同连接限制推演；实际派发仍需运行、资源和权限核对。共享额度来源 ${t.quota_sources.length} 个，余额信息不作为硬保证。</p>`:'<p>尚未保存合作方案。</p>'}<form id="work-item-teamwork">${S.select('合作模式','mode',Object.entries(labels),d.mode)}<div data-team-fixed-access ${d.mode==='fixed'?'':'hidden'}>${fixedAccessField(d.access)}</div>${S.select('负责人／主持人','coordinator',options,d.coordinator)}${S.select('成果整合者','integrator',options,d.integrator)}${S.select('最多同时工作数','max_parallel',[1,2,3,4].map(n=>[String(n),String(n)]),String(d.max_parallel))}${S.field('硬费用上限（元，可留空）','hard_cash_limit_cny',d.hard_cash_limit_cny,'type="number" min="0" max="1000000" step="0.01"')}<p class="sp-note">填写硬上限后，无法强制落实的执行路线将保持阻塞。</p>${handoffConfig(d)}${d.units.map((u,i)=>`<fieldset class="wi-unit" data-team-unit="${i}"><legend>工作 ${i+1}</legend>${S.field('工作名称',`unit_${i}_title`,u.title,'required maxlength="160"')}${S.text('单元目标',`unit_${i}_goal`,u.goal,'required maxlength="4000"')}${S.text('单元完成要求',`unit_${i}_acceptance`,u.acceptance,'required maxlength="4000"')}${S.select('职责',`unit_${i}_role`,[['plan','方案'],['execute','执行'],['review','检查审查'],['integrate','整合'],['coordinate','负责人／主持人提案']],u.role)}${S.select('承担成员',`unit_${i}_member_id`,options,u.member_id)}${S.select('任务能力预设',`unit_${i}_scene_preset`,presetOptions(u.scene_preset),presetValue(u.scene_preset))}<p class="sp-note">所选预设及依赖版本绑定到本单元；派发前发生变化时停止新派发。加载记录见扩展页。</p><div class="wi-dependencies"><p class="sp-note">前置工作</p>${d.units.filter(other=>other.key!==u.key).map(other=>`<label><input type="checkbox" name="unit_${i}_depends" value="${h(other.key)}" ${u.depends.includes(other.key)?'checked':''}>${h(other.title||'未命名工作')}</label>`).join('')||'<p class="sp-note">目前只有这一项。</p>'}</div>${S.text('产物文件相对路径（每行一项）',`unit_${i}_outputs`,(u.outputs||[]).join('\n'),'maxlength="4800"')}${S.select('前置依赖解除条件',`unit_${i}_dependency_gate`,[['result','准确回答可用'],['files','准确文件版本齐备'],['checked','准确文件及本地检查通过']],u.dependency_gate||'result')}${S.select('产物本地检查',`unit_${i}_check_preset`,[['none','不设置'],['saved','保留已保存检查'],['nonempty','每项文件非空'],['json_parse','每项文件可解析为 JSON'],['utf8_contains','每项 UTF-8 文件包含指定文字']],u.check_preset||'none')}${S.text('检查所需包含文字',`unit_${i}_check_text`,u.check_text||'','maxlength="4000"')}${u.checks?.length?`<details><summary>已声明检查</summary>${S.raw(u.checks)}</details>`:''}<p class="sp-note">不同单元使用不同产物文件名；明确交接关系的准确两端可复用已核对的完整文件集合。文件检查作用于准确字节版本；独立审查与使用验收另行记录。</p>${S.text('允许写入的相对路径（每行一项，空为只读）',`unit_${i}_write_paths`,u.write_paths.join('\n'),'maxlength="20000"')}<p class="sp-note">“.” 表示整个工作目录；路径声明用于安排工作，实际限制由执行路线落实。提案单元须由所选负责人／主持人承担，写入意图为空，无文件产物和检查。</p></fieldset>`).join('')}<div class="sp-row">${S.button('增加工作单元',`type="button" data-team-action="add" ${d.units.length>=20?'disabled':''}`)}${S.button('移除末项',`type="button" data-team-action="remove" ${d.units.length<=1?'disabled':''}`)}<button type="submit" ${d.options.length&&!item.driver?.held&&!(d.handoff_locked&&(t?.plan.file_handoffs?.length||d.handoff_enabled))?'':'disabled'}>保存合作方案</button>${S.button('重新载入方案','type="button" data-team-action="reload"')}</div>${!d.options.length?'<p>请先在成员与连接中登记可用成员。</p>':''}</form>`;
    if(compact)return `${controlDisclosure('cooperation','运行合作与派发授权',operations)}
      ${controlDisclosure('plan-editor','编辑合作方案',editor)}`;
    return `<section class="sp-card wi-teamwork"><h2>合作方案准备</h2><p class="sp-note">${item.cooperation?.continuous_supported&&item.teamwork.plan.mode==='host_experts'?'专家共同形成方案，主持人收束分工与交付；异议、协助请求和修订均保留准确记录。':item.cooperation?.continuous_supported&&item.teamwork.plan.mode==='leader'?'负责人根据任务目标和准确成果持续组织工作；修订沿用原授权，并保留成果来源。':'保存分工和依赖，不启动成员。固定流程支持逐项或连续串行派发；负责人和主持人模式可配置提案单元，先检查提案再单步派发。'}</p>${operations}${editor}</section>`;
  }
  function fileAppliedView(item){const r=item.teamwork?.plan?.file_rework_lineage;if(!r)return '';
    return `<details class="wi-artifacts" data-file-rework-applied><summary>已应用文件返工</summary><p>${h(r.reason)}</p><p>受影响工作：${h(r.affected_units.join('、'))}。同责任暂停后须明确恢复，按新提案和完整依赖执行；首次新方案运行建立新文件版本。</p><p>已保存声明路径的写前基线：${h(r.baseline.paths.join('、'))}；原缺失：${h(r.baseline.missing.join('、')||'无')}。原输入及全部旧快照分别核对，原累计上限、截止和权限保留。</p><p>记录版本 ${h(r.version)}</p><p class="sp-note">基线覆盖所列文件，原生访问限制沿用工作端配置。同路径整合另行处理。</p></details>`;
  }
  function fileHandoffView(item){const a=item.file_handoff;if(!a?.sources.length)return '';const r=a.record?.snapshot,c=item.cooperation,enabled=item.driver?.record?.state==='RESERVED'&&!c.flow.running;
    return `<details class="wi-artifacts" data-file-handoff><summary>同路径交接预览</summary><p>选择已保存的准确前版和拟接续单元，核对整合职责、直接依赖及原写入范围。</p><form id="work-item-file-handoff">${S.select('准确前版','source',a.sources.map((s,i)=>[String(i),s.title]),'0')}${S.select('后继单元','target',a.targets.map(t=>[t.key,t.title]),a.targets.find(t=>t.key!==a.sources[0].unit_key)?.key||'')}<button type="submit" ${enabled?'':'disabled'}>核对同路径交接</button></form>${a.problems.map(x=>`<p>${h(x)}</p>`).join('')}${r?`<div data-file-handoff-preview="${h(a.reference.version)}"><h4>${a.state==='STALE'?'原交接预览已失效':r.state==='BLOCKED'?'交接存在阻塞':'已完成只读核对'}</h4><p>${h(r.source.unit.title)} → ${h(r.target.title)}；完整路径：${h(r.paths.join('、'))}。</p><p>准确前版运行 ${h(r.source.run_id)}；文件快照 ${h(r.current_delivery.reference?.version||'未登记')}；当前交付 ${h(r.current_delivery.state)}。</p>${r.blockers.map(x=>`<p>${h(x)}</p>`).join('')}${r.required_configuration.map(x=>`<p>后继 ${h(x.field)} 需要明确方案配置：${h(x.required.join('、'))}；当前：${h(x.current.join('、')||'空')}。</p>`).join('')}<p>原累计 ${h(r.usage.attempts)} 次；原授权上限 ${h(r.authorization?.max_attempts_total??'未授权')}；原派发截止 ${h(r.authorization?S.when(r.authorization.dispatch_before):'未授权')}。</p><details><summary>前版实际输入 manifest</summary>${S.raw(r.actual_source_input)}</details><details><summary>全部旧输入与保存快照</summary>${S.raw(r.original_files)}</details></div>`:''}<p class="sp-note">预览只保存核对记录。前版实际输入、全部旧快照与当前项目交付分别核对；正式交接须在运行前由 Human 在方案中明确配置；本预览不批准配置或派发。原要求、成员、累计上限、截止及权限保持。</p></details>`;
  }
  function roundAssetLabel(protocol){const labels={CHECKPOINT:'保存前核对',SAVED_CANDIDATE:'已核方案',APPLICATION:'正式保存',REGISTRATION:'明确登记'};for(const [key,label] of Object.entries(labels))if(protocol.endsWith('_'+key+'_V1'))return label;return '接续记录';}
  function coordinatorRoundRecords(s){
    if(!s.round_edges?.length)return '';
    return `<section data-round-records><h4>已保存接续回合</h4><p>已明确登记 ${h(s.round_edges.length)} 次；当前方案 ${h(s.plan.id)}</p>${s.round_source?`<p data-round-source>前源运行 ${h(s.round_source.run_id)}<br>来源方案 ${h(s.round_source.plan_id)}</p>`:''}${s.round_edges.map(e=>`<details data-round-edge><summary>接续登记 ${h(e.registration.version.slice(0,12))}</summary><p>原方案 ${h(e.parent_plan.id)}<br>新方案 ${h(e.child_plan.id)}<br>应用 ${h(e.application.version)}</p></details>`).join('')}${s.round_records.map(r=>`<details data-round-asset><summary>${h(roundAssetLabel(r.snapshot.protocol))}</summary><p>${h(r.id)}<br>版本 ${h(r.version)}</p><p>${h(r.snapshot.reason)}</p></details>`).join('')}<p>接续回合只支持派发或暂停；后续依赖调整待接入。</p></section>`;
  }
  const postSaveKey=item=>'workdesk.post-round-v3.save:'+item.id;
  const postSaveProofKey=item=>postSaveKey(item)+':last-cleared';
  const postSaveRefusalKey=item=>postSaveKey(item)+':refusal';
  const postSaveOrdered=x=>x&&typeof x==='object'?Array.isArray(x)?x.map(postSaveOrdered):Object.fromEntries(Object.keys(x).sort().map(k=>[k,postSaveOrdered(x[k])])):x;
  const samePostSave=(a,b)=>JSON.stringify(postSaveOrdered(a))===JSON.stringify(postSaveOrdered(b));
  function postSaveRecord(key){try{return JSON.parse(sessionStorage.getItem(key));}catch{return null;}}
  function postSavePending(item){const raw=sessionStorage.getItem(postSaveKey(item));if(!raw)return null;try{const command=JSON.parse(raw);if(command.kind!=='internal.post-round.v3.save'||typeof command.id!=='string'||command.payload?.id!==item.id)throw Error();return command;}catch{return {invalid:true};}}
  function postSaveRecoveryProof(result,command){
    if(!result||!samePostSave(result.command,command)||result.no_dispatch!==true||typeof result.can_clear_local!=='boolean'||!Array.isArray(result.problems)||!['BLOCKED','RECORDED','CURRENT_REQUEST','STALE_LOCAL_REQUEST'].includes(result.state)||result.can_clear_local!==(result.state==='STALE_LOCAL_REQUEST')||(result.can_clear_local&&(!result.current_request||result.current_request.id!==command.payload.id||samePostSave(result.current_request,command.payload))))throw Error('清除条件回执不完整，原保存请求已保留。');
    return result;
  }
  function postSaveRecoveryView(item,pending){
    const proof=v.postSaveRecovery,valid=proof&&samePostSave(proof.command,pending),refusal=postSaveRecord(postSaveRefusalKey(item));
    return `${refusal&&samePostSave(refusal.command,pending)?`<p data-post-save-refusal>${h(refusal.message)}</p>`:''}${S.button('核对本页请求清除条件','data-post-round-v3-inspect')}${valid?`<div data-post-save-recovery="${h(proof.state)}">${proof.problems.map(x=>`<p>${h(x)}</p>`).join('')}${proof.can_clear_local?`<p>原命令尚无提交记录；当前完整来源已核验，原请求已失效。</p>${S.button('清除本页失效请求','data-post-round-v3-clear')}`:''}<details><summary>本次清除条件核对</summary>${S.raw(proof)}</details></div>`:''}`;
  }
  function postRoundSaveView(item){const action=item.cooperation?.post_round_v3_save,pending=postSavePending(item),cleared=postSaveRecord(postSaveProofKey(item));if(!action&&!pending&&!cleared)return '';
    return `<section data-post-round-v3-save><h4>保存本次依赖调整</h4><p>保存候选方案并保留原暂停责任、累计授权和截止；保存后可在专属责任区操作，后继运行另行接入。</p>${(action?.problems||[]).map(x=>`<p>${h(x)}</p>`).join('')}${pending?.invalid?'<p>本页保存核对记录损坏，请保留原命令记录核对。</p>':pending?`<p data-post-save-pending>保存回执尚待核对。命令 ${h(pending.id)}</p>${S.button('核对原保存回执','data-post-round-v3-reconcile')}${postSaveRecoveryView(item,pending)}<details><summary>待核对的原保存请求</summary>${S.raw(pending)}</details>`:S.button('保存依赖调整并保持暂停',`data-post-round-v3-save-action ${action?.can_save?'':'disabled'}`)}${cleared?`<details data-post-save-cleared><summary>本页最近一次清除记录</summary><p>已清除本标签页的失效请求记录。服务端命令与保存状态请按实际回执核对。</p>${S.raw(cleared)}</details>`:''}</section>`;
  }
  const responsibilityKey=item=>'workdesk.post-round-v3.responsibility:'+item.id;
  function responsibilityProof(result,command){
    const states=['BLOCKED','RECORDED','CURRENT_REQUEST','STALE_LOCAL_REQUEST'],r=result?.current;
    if(!result||result.protocol!=='COORDINATOR_FILE_HANDOFF_RESPONSIBILITY_RECOVERY_V3'||!samePostSave(result.command,command)||result.no_dispatch!==true||!Number.isFinite(result.checked_at)||!Array.isArray(result.problems)||!states.includes(result.state)||result.can_clear_local!==['RECORDED','STALE_LOCAL_REQUEST'].includes(result.state))throw Error('责任清除条件回执不完整，原请求已保留。');
    if(result.state==='BLOCKED'){if(result.full_current_tree_verified!==false||r!==null||result.receipt!==null)throw Error('受阻责任证明不完整');return result;}
    if(result.full_current_tree_verified!==true||r?.protocol!=='COORDINATOR_FILE_HANDOFF_PUBLIC_RESPONSIBILITY_V3'||r.full_current_tree_verified!==true||r.no_dispatch!==true||r.successor_runtime_open!==false||r.driver?.item_id!==command.payload.id||!Array.isArray(r.entries)||!r.actions?.[command.payload.action]?.request)throw Error('完整当前责任尚未核对');
    const entries=r.entries.filter(e=>e.command?.id===command.id),request=r.actions[command.payload.action].request;
    if(result.state==='RECORDED'){
      if(entries.length!==1||!samePostSave(entries[0].command,command)||!result.receipt||!samePostSave(entries[0].receipt,result.receipt))throw Error('原责任完整提交回执未绑定');
      const receipt=result.receipt,t=receipt.transition;
      if(Object.keys(receipt).sort().join(',')!==['transition','driver','authorization','saved_registration','no_dispatch','automatically_resumed'].sort().join(',')||receipt.no_dispatch!==true||receipt.automatically_resumed!==false||t?.command_id!==command.id||t.snapshot?.command_id!==command.id||!samePostSave(t.snapshot?.request,command.payload)||!samePostSave(entries[0].transition,t)||!samePostSave(receipt.driver,t.snapshot?.driver_after)||!samePostSave(receipt.authorization,t.snapshot?.authorization)||receipt.saved_registration?.id!==command.payload.saved_registration.id||receipt.saved_registration?.version!==command.payload.saved_registration.version)throw Error('原责任完整回执字段或原请求绑定不符');
    }else if(entries.length||result.receipt!==null||request.id!==command.payload.id||r.actions[command.payload.action].kind!==command.kind||(result.state==='CURRENT_REQUEST')!==samePostSave(request,command.payload))throw Error('原责任请求的当前性证明不符');
    return result;
  }
  function responsibilityRecoveryView(item,command){
    const p=v.responsibilityRecovery,valid=p&&samePostSave(p.command,command);
    return `${S.button('核对本页责任请求','data-v3-responsibility-inspect')}${valid?`<div data-v3-responsibility-recovery="${h(p.state)}">${p.problems.map(x=>`<p>${h(x)}</p>`).join('')}${p.can_clear_local?`<p>${p.state==='RECORDED'?'原命令与完整提交回执已核对。':'原命令尚无提交记录；完整当前责任证明原请求已过时。'}</p>${S.button('清除本页已核对责任请求','data-v3-responsibility-clear')}`:''}<details><summary>本次责任核对证明</summary>${S.raw(p)}</details></div>`:''}`;
  }
  function responsibilityPending(item){const raw=sessionStorage.getItem(responsibilityKey(item));if(!raw)return null;try{const c=JSON.parse(raw);if(c.kind!=='internal.post-round.v3.responsibility'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).test(c.id)||c.payload?.id!==item.id||!['resume','pause','release'].includes(c.payload.action)||Object.keys(c.payload).sort().join(',')!==['id','revision','plan','current_driver','authorization','frontier','saved_registration','action'].sort().join(','))throw Error();return c;}catch{return {invalid:true};}}
  function responsibilityView(item,r){const pending=responsibilityPending(item),last=postSaveRecord(responsibilityKey(item)+':receipt'),refusal=postSaveRecord(responsibilityKey(item)+':refusal'),cleared=postSaveRecord(responsibilityKey(item)+':cleared-log');if(!r&&!pending&&!last&&!cleared)return '';
    const labels={PAUSED:'已暂停',RESERVED:'已恢复责任',RELEASED:'已释放'},actions={resume:'恢复合作责任',pause:'暂停合作责任',release:'释放合作责任',restart_pause:'实例重启后暂停'};
    return `<section data-v3-responsibility><h4>依赖调整后的合作责任</h4><p data-v3-responsibility-state="${h(r?.state||'BLOCKED')}">${r?h(labels[r.state]||r.state):'完整当前责任待核对'}。后继运行另行接入；恢复责任后仍不会派发。</p>${pending?.invalid?'<p>原责任请求记录损坏，请保留记录核对。</p>':pending?`<p data-v3-responsibility-pending>责任回执尚待核对：${h(pending.id)}</p>${S.button('重放原命令并核对完整回执','data-v3-responsibility-replay')}${responsibilityRecoveryView(item,pending)}<details><summary>待核对的原责任请求</summary>${S.raw(pending)}</details>${refusal&&samePostSave(refusal.command,pending)?`<p>${h(refusal.message)}</p>`:''}`:r?Object.entries(r.actions).map(([action,a])=>`<div>${S.button(actions[action],`data-v3-responsibility-action="${h(action)}" ${a.enabled?'':'disabled'}`)}${a.problems.map(p=>`<p>${h(p)}</p>`).join('')}</div>`).join(''):''}${r?.entries.map(e=>`<details data-v3-responsibility-entry="${h(e.transition.version)}"><summary>${h(actions[e.transition.snapshot.action])}${e.command?' · '+h(e.command.id):' · 内部重启记录'}</summary>${e.command?S.raw(e.command)+S.raw(e.receipt):`<p>重启身份 ${h(e.transition.snapshot.restart_id)}；无 Human 命令。</p>`}${S.raw(e.transition)}</details>`).join('')||''}${cleared?`<details data-v3-responsibility-cleared><summary>本页责任请求清除记录</summary>${S.raw(cleared)}</details>`:''}${last?`<details data-v3-responsibility-receipt><summary>本页已核对的完整责任回执</summary>${S.raw(last)}</details>`:''}</section>`;
  }
  const successorKey=item=>'workdesk.post-round-v3.successor:'+item.id;
  function successorPending(item){const raw=sessionStorage.getItem(successorKey(item));if(!raw)return null;try{const c=JSON.parse(raw);if(c.kind!=='work-item.unit-send'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).test(c.id)||c.payload?.id!==item.id||Object.keys(c.payload).sort().join(',')!==['id','revision','plan','current_driver','authorization','frontier','unit','retry_of','proposal'].sort().join(',')||c.payload.retry_of!==null||(c.payload.proposal!==null&&(!c.payload.proposal?.id||!c.payload.proposal?.version)))throw Error();return c;}catch{return {invalid:true};}}
  const proposalKey=item=>successorKey(item)+':proposal-check';
  function proposalPending(item){const raw=sessionStorage.getItem(proposalKey(item));if(!raw)return null;try{const c=JSON.parse(raw);if(c.kind!=='work-item.proposal-check'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).test(c.id)||c.payload?.id!==item.id||Object.keys(c.payload).sort().join(',')!==['id','revision','plan','current_driver','authorization','frontier','run_id','result_version','proposal'].sort().join(','))throw Error();return c;}catch{return {invalid:true};}}
  async function checkV3Proposal(item){const key=proposalKey(item),samePage=()=>current().active&&current().id===item.id&&v.data?.work_items[0]?.id===item.id;
    let command=proposalPending(item);if(command?.invalid)throw Error('提案原请求记录损坏，请保留记录核对。');
    if(!command){if(successorPending(item))throw Error('请先核对原派发请求。');const a=item.cooperation?.handoff_stage?.v3_successor?.proposal_action;if(!a?.enabled||!a.request)return;command={id:crypto.randomUUID(),kind:a.kind,payload:structuredClone(a.request)};sessionStorage.setItem(key,JSON.stringify(command));}
    const raw=sessionStorage.getItem(key);
    try{const result=await S.request('command',command);if(result.command_id!==command.id||!result.receipt?.proposal||result.receipt.no_dispatch!==true)throw Error('提案原完整回执不符。');
      const data=await S.request('work-items?'+new URLSearchParams({id:item.id})),proof=data.work_items?.[0]?.cooperation?.handoff_stage?.v3_successor;
      const entries=proof?.entries?.filter(e=>e.command?.id===command.id)||[];
      if(proof?.full_current_tree_verified!==true||entries.length!==1||!samePostSave(entries[0].command,command)||!samePostSave(entries[0].receipt,result.receipt))throw Error('提案完整当前记录尚未核对，原请求已保留。');
      if(!samePage()||sessionStorage.getItem(key)!==raw)return;
      sessionStorage.setItem(key+':receipt',JSON.stringify({command,receipt:result.receipt}));sessionStorage.removeItem(key);await load();await S.refreshChrome();
    }catch(error){if(samePage()&&sessionStorage.getItem(key)===raw)sessionStorage.setItem(key+':refusal',JSON.stringify({command,message:String(error.message||error)}));throw error;}
  }
  document.addEventListener('click',e=>{const el=e.target.closest('[data-v3-proposal-replay]');if(!el||!current().active)return;act(()=>checkV3Proposal(v.data.work_items[0]));});
  function successorView(item,r){const pending=successorPending(item),last=postSaveRecord(successorKey(item)+':receipt');if(!r&&!pending&&!last)return '';
    return `<section data-v3-successor><h4>原声明单步接续</h4><p>沿用原累计授权和截止，检查协调提案后派发所选普通单元；按材料阶段捕获A与B。</p><p>当前提案：${h(item.cooperation?.proposal?.state||'NONE')}</p>${proposalPending(item)?.invalid?'<p>提案请求记录损坏，请保留记录核对。</p>':proposalPending(item)?`<p data-v3-proposal-pending>提案原请求待核对</p>${S.button('核对原提案回执','data-v3-proposal-replay')}<details><summary>原提案完整请求</summary>${S.raw(proposalPending(item))}</details>`:''}${(r?.execution_observation?.runs||[]).map(run=>`<p data-v3-successor-execution>运行记录：${h(run.state)}；完整来源：${r.full_current_tree_verified?'已核对':'待核对'}。未知或在途保留原命令，不自动重发。</p>`).join('')}${pending?.invalid?'<p>首次协调请求记录损坏，请保留原记录核对。</p>':pending?`<p data-v3-successor-pending>原命令 ${h(pending.id)} 回执待核对</p>${S.button('核对原首次协调回执','data-v3-successor-replay')}<details><summary>原完整请求</summary>${S.raw(pending)}</details>`:''}${Object.entries(r?.actions||{}).map(([key,a])=>`<div>${S.button('单步派发 '+key,`data-v3-successor-send="${h(key)}" ${a.enabled&&!pending&&!proposalPending(item)?'':'disabled'}`)}${a.problems.map(x=>`<p>${h(x)}</p>`).join('')}</div>`).join('')}${(r?.entries||[]).map(e=>`<details data-v3-successor-receipt><summary>原命令完整回执</summary>${S.raw(e.command)}${S.raw(e.receipt)}</details>`).join('')}${last?`<details><summary>本页已核对回执</summary>${S.raw(last)}</details>`:''}</section>`;
  }
  async function sendV3Successor(item,unitKey){const key=successorKey(item),samePage=()=>current().active&&current().id===item.id&&v.data?.work_items[0]?.id===item.id;
    let command=successorPending(item);if(command?.invalid)throw Error('首次协调请求记录损坏，请保留原记录核对。');
    if(!command){if(proposalPending(item))throw Error('请先核对原提案请求。');const a=item.cooperation?.handoff_stage?.v3_successor?.actions?.[unitKey];if(!a?.enabled||!a.request)return;command={id:crypto.randomUUID(),kind:a.kind,payload:structuredClone(a.request)};sessionStorage.setItem(key,JSON.stringify(command));}
    const raw=sessionStorage.getItem(key);
    try{const result=await S.request('command',command);if(result.command_id!==command.id||!result.receipt||!Object.keys(result.receipt).length)throw Error('首次协调原回执身份不符或不完整。');
      const data=await S.request('work-items?'+new URLSearchParams({id:item.id})),detail=data.work_items?.[0],proof=detail?.cooperation?.handoff_stage?.v3_successor;
      const entries=proof?.entries?.filter(e=>e.command?.id===command.id)||[];
      if(proof?.full_current_tree_verified!==true||entries.length!==1||!samePostSave(entries[0].command,command)||!samePostSave(entries[0].receipt,result.receipt))throw Error('首次协调完整当前提交记录尚未核对，原请求已保留。');
      if(!samePage()||sessionStorage.getItem(key)!==raw)return;
      sessionStorage.setItem(key+':receipt',JSON.stringify({command,receipt:result.receipt}));sessionStorage.removeItem(key);await load();await S.refreshChrome();
    }catch(error){if(samePage()&&sessionStorage.getItem(key)===raw)sessionStorage.setItem(key+':refusal',JSON.stringify({command,message:String(error.message||error)}));throw error;}
  }
  document.addEventListener('click',e=>{const el=e.target.closest('[data-v3-successor-send],[data-v3-successor-replay]');if(!el||!current().active)return;act(()=>sendV3Successor(v.data.work_items[0],el.dataset.v3SuccessorSend));});
  function coordinatorSavedV3View(s){const saved=s.v3_saved,phases={BEFORE_A:'等待前版运行与捕获',A_CAPTURED:'前版已捕获',B_COMPLETED:'后继已准确完成',BLOCKED:'当前文件或记录受阻'};
    return `<section data-coordinator-stage="${h(s.phase)}" data-v3-saved-stage><h4>已保存的依赖调整</h4><p data-v3-integrity="${h(s.historical_integrity)}">完整当前记录：${s.full_current_tree_verified?'已核对':'受阻'}；当前文件阶段：${h(phases[s.phase]||s.phase)}</p>${s.problems.map(x=>`<p>${h(x)}</p>`).join('')}${saved?`<details data-v3-saved-records><summary>原保存命令、完整回执与有序祖先</summary><p>命令身份 ${h(saved.command.id)}<br>应用 ${h(saved.application.version)}<br>登记 ${h(saved.registration.version)}</p>${S.raw(saved.command)}${S.raw(saved.receipt)}${S.raw(saved.saved_path)}</details>`:''}${responsibilityView(v.data.work_items[0],s.v3_responsibility)}${successorView(v.data.work_items[0],s.v3_successor)}</section>`;
  }
  function coordinatorStageView(s){if(!s)return '';
    if(s.protocol==='COORDINATOR_FILE_HANDOFF_TEXT_REWORK_CURRENT_V1')return `<section data-coordinator-stage="${h(s.phase)}" data-text-rework-file-history><h4>原文件交接记录</h4><p>${s.historical_integrity==='VERIFIED'?'原交付与完成记录已核对':'原文件交接记录核对受阻'}</p>${(s.problems||[]).map(h).join('；')}<details><summary>原A/B正式记录</summary>${(s.delivery||[]).map(r=>`<p>正式交付 ${h(r.id)}<br>版本 ${h(r.version)}</p>`).join('')}${(s.completion||[]).map(r=>`<p>正式完成 ${h(r.id)}<br>版本 ${h(r.version)}</p>`).join('')}</details></section>`;
    if(s.saved_v3)return coordinatorSavedV3View(s);const labels={BEFORE_A:'等待前版运行与捕获',A_CAPTURED:'前版已捕获，可按提案交付后继',B_IN_FLIGHT:'后继在途，等待准确退出',B_EXITED_UNCAPTURED:'后继已退出，等待保存并检查文件',B_COMPLETED:'后继已准确完成，前版保留为历史',BLOCKED:'交接受阻，核对准确记录'};
    return `<section data-coordinator-stage="${h(s.phase)}"><h4>同路径交接阶段</h4><p>${h(labels[s.phase]||s.phase)}</p><p>历史完整性：${s.historical_integrity==='VERIFIED'?'已核对':'受阻'}</p>${s.problems.map(h).join('；')}<details data-coordinator-records><summary>交接原方案与正式记录</summary><p>原方案 ${h(s.root.id)}<br>版本 ${h(s.root.version)}</p>${s.plan?`<p data-current-adjustment-plan>当前方案 ${h(s.plan.id)}<br>版本 ${h(s.plan.version)}</p><p>已保存调整 ${h((s.adjustment_saved_path||s.saved_path)?.edges?.length||0)} 条；调整能力 ${h(s.post_round_adjustment==='NOT_OPEN'?'后续调整待接入':s.adjustment_capability?.version||'尚未启用')}</p>${((s.adjustment_saved_path||s.saved_path)?.edges||[]).map(e=>`<p data-adjustment-edge>原方案 ${h(e.parent_plan.id)} → ${h(e.child_plan.id)}<br>应用 ${h(e.application.version)}<br>消费 ${h(e.consumption.version)}</p>`).join('')}${(s.adjustment_records||[]).map(r=>`<details data-adjustment-asset><summary>${h(({COORDINATOR_FILE_HANDOFF_ADJUSTMENT_PREVIEW_V1:'调整预览',COORDINATOR_FILE_HANDOFF_ADJUSTMENT_CHECKPOINT_V1:'保存前检查点',COORDINATOR_FILE_HANDOFF_ADJUSTMENT_SAVED_CANDIDATE_V1:'已核保存候选',COORDINATOR_FILE_HANDOFF_ADJUSTMENT_APPLICATION_V1:'正式应用',COORDINATOR_FILE_HANDOFF_ADJUSTMENT_CONSUMPTION_V1:'提案消费'})[r.snapshot.protocol]||r.snapshot.protocol)}</summary><p>${h(r.id)}<br>版本 ${h(r.version)}</p></details>`).join('')}`:'<p>下一次可派发的首次协调回合可提出依赖调整；应用另行确认。</p>'}${coordinatorRoundRecords(s)}${s.delivery.map(r=>`<p>正式交付 ${h(r.id)}<br>版本 ${h(r.version)}</p>`).join('')}${s.completion.map(r=>`<p>正式完成 ${h(r.id)}<br>版本 ${h(r.version)}</p>`).join('')}</details></section>`;
  }
  const textReworkKey=item=>'workdesk.text-rework.pending.'+item.id;
  function textReworkPending(item){const raw=sessionStorage.getItem(textReworkKey(item));if(!raw)return null;try{return JSON.parse(raw);}catch{return {invalid:true};}}
  function textReworkView(item){const text=item.cooperation?.text_rework,pending=textReworkPending(item);if(!text&&!pending)return '';
    const proof=v.textReworkRecovery,matching=proof&&samePostSave(proof.command,pending);
    return `<section data-text-rework><h4>普通文字返工</h4><p>${h(text?.state||'待核对')}</p>${(text?.problems||[]).map(p=>`<p>${h(p)}</p>`).join('')}${pending?`<p data-text-rework-pending>保留原命令，先核对回执。</p>${S.button('只读核对原命令','data-text-rework-inspect')}${matching?`${(proof.problems||[]).map(p=>`<p>${h(p)}</p>`).join('')}${proof.can_clear_local?S.button('清除本页未提交请求','data-text-rework-clear'):''}`:''}`:Object.entries(text?.actions||{}).map(([key,a])=>`<div>${S.button(a.label,`data-text-rework-action="${h(key)}" ${a.enabled?'':'disabled'}`)}${!a.enabled?(a.problems||[]).map(p=>`<small>${h(p)}</small>`).join(''):''}</div>`).join('')}${text?.preview?`<details><summary>完整返工预览</summary>${S.raw(text.preview)}</details>`:''}</section>`;
  }
  document.addEventListener('click',e=>{const el=e.target.closest('[data-text-rework-action],[data-text-rework-inspect],[data-text-rework-clear]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],key=textReworkKey(item),original=sessionStorage.getItem(key);
    const unchanged=()=>current().active&&current().id===item.id&&v.data?.work_items?.[0]?.id===item.id&&sessionStorage.getItem(key)===original;
    if(el.hasAttribute('data-text-rework-inspect')||el.hasAttribute('data-text-rework-clear')){
      const command=textReworkPending(item);if(!command||command.invalid)throw Error('原命令记录损坏，请保留并核对。');
      const proof=await S.request('coordinator-text-rework-recovery',structuredClone(command));
      if(!samePostSave(proof.command,command)||proof.no_dispatch!==true)throw Error('核对响应与原命令不符。');
      if(!unchanged())return;
      if(proof.state==='RECORDED'&&proof.receipt){sessionStorage.removeItem(key);v.textReworkRecovery=null;await load();return;}
      v.textReworkRecovery=proof;
      if(el.hasAttribute('data-text-rework-clear')&&proof.can_clear_local){
        if(!await S.confirm('清除本页未提交请求','已核验当前完整历史，原命令没有提交记录。下一步仍需明确点击。',JSON.stringify(proof,null,2)))return;
        if(!unchanged())return;
        const again=await S.request('coordinator-text-rework-recovery',structuredClone(command));
        if(!unchanged()||again.can_clear_local!==true||again.state!=='NOT_COMMITTED_CURRENT_VERIFIED'||!samePostSave(again.command,command))throw Error('当前核对条件已变化，保留原命令。');
        sessionStorage.removeItem(key);v.textReworkRecovery=null;await load();return;
      }
      draw();return;
    }
    if(textReworkPending(item))throw Error('先只读核对本页原命令。');
    const a=item.cooperation?.text_rework?.actions?.[el.dataset.textReworkAction];if(!a?.enabled||!a.request)return;
    const command={id:crypto.randomUUID(),kind:a.kind,payload:structuredClone(a.request)};
    sessionStorage.setItem(key,JSON.stringify(command));v.textReworkRecovery=null;
    try{const result=await S.request('command',command);if(result.command_id!==command.id||!result.receipt)throw Error('命令回执不完整，保留原UUID核对。');
      const proof=await S.request('coordinator-text-rework-recovery',structuredClone(command));
      if(proof.state!=='RECORDED'||!samePostSave(proof.command,command)||!samePostSave(proof.receipt,result.receipt))throw Error('完整回执尚未由真实历史核对，保留原UUID。');
      if(current().active&&current().id===item.id&&sessionStorage.getItem(key)===JSON.stringify(command)){sessionStorage.removeItem(key);v.teamDraft=null;await load();}
    }catch(error){v.error=error.message;draw();}
  });});
  function cooperation(item,compact=false){const c=item.cooperation;if(!c?.supported)return '';const a=c.authorization,used=c.usage.attempts,fixed=item.teamwork.plan.mode==='fixed'||c.continuous_supported;
    const unitControls=`${c.units.map((u,i)=>`<article class="wi-cooperative-unit" data-cooperative-unit="${h(u.key)}"><h4>${h(u.title)} · ${h(u.member_name)}</h4>${u.qualification?`<p data-stage-qualification="${h(u.qualification)}">${h({HISTORICAL_RETIRED:'历史退役前版：保留原快照，当前附件资格已退役',CURRENT_COMPLETED:'后继已准确完成：当前文件来源',HISTORICAL_COORDINATOR:'已登记准确协调结果',ELIGIBLE_RESULT:'准确成果可沿用',RECORDED:'已登记准确运行',NOT_RUN:'尚未运行',UNVERIFIED:'历史核对受阻'}[u.qualification]||u.qualification)}</p>`:''}${u.lineage?.inherited?`<p data-result-lineage="${h(u.key)}">${u.result?.state==='HISTORICAL_RETIRED'?'历史退役前版':'沿用原成果'} · ${u.result?.state==='HISTORICAL_RETIRED'?'保留原历史，当前输入资格已退役':u.lineage.problems.length?'来源已失效':'来源仍有效'} · 原尝试 ${h(u.lineage.source?.attempt_id||u.result?.attempt_id||'无准确回执')}</p>${u.lineage.source?`<details data-lineage-identity="${h(u.key)}"><summary>准确原成果与当前消费者</summary><p>来源方案 ${h(u.lineage.source.plan_id)}<br>版本 ${h(u.lineage.source.plan_version)}</p><p>原运行 ${h(u.lineage.source.run_id)}</p><p>当前消费者方案 ${h(u.lineage.consumer_plan.id)}<br>版本 ${h(u.lineage.consumer_plan.version)}</p></details>`:''}`:''}<p>${u.result?h(u.result.state==='HISTORICAL_RETIRED'?'历史退役前版':S.state(u.result.state))+' · '+(u.result.result_available?'准确文字结果可用':'结果尚不可用'):u.ready?'前置结果已具备':'等待前置准确结果'} · ${h(u.qualification==='HISTORICAL_RETIRED'?'历史快照已核对':u.result?.check_status||'NOT_RECORDED')}</p>${u.blocked_reason&&(!c.handoff_stage||!u.latest_run_id)?`<p>${h(u.blocked_reason)}</p>`:''}${u.frozen_history?`<p data-frozen-history="${h(u.key)}">冻结历史核对：${u.frozen_history.state==='VERIFIED'?'已通过':'受阻'}。${u.frozen_history.problems.map(h).join('；')}</p>${u.frozen_history.state==='VERIFIED'?`<details data-handoff-records><summary>正式交付与完成记录</summary><p>交付版本 ${h(u.frozen_history.delivery.version)}</p><p>完成版本 ${h(u.frozen_history.completion?.version||'尚未捕获完成')}</p></details>`:''}`:''}${u.dispatch_blocked_reason&&(!c.handoff_stage||!u.latest_run_id)&&u.dispatch_blocked_reason!==u.blocked_reason?`<p data-dispatch-blocked="${h(u.key)}">新派发条件：${h(u.dispatch_blocked_reason)}</p>`:''}${artifactView(u,i)}${u.result?`<p><a href="#control/task/${h(u.result.task_id)}">核对准确执行</a> · 尝试 ${h(u.result.attempt_id)}</p><details><summary>本次文字结果</summary><pre class="wi-unit-answer">${h(u.result.answer)}</pre><p class="sp-note">会话 ${h(u.result.session_id)} · 回合 ${h(u.result.turn_id||'尚无回执')} · 独立审查未开展 · Human 未验收</p></details>`:''}<div class="sp-row">${u.role==='coordinate'?S.button('检查提案',`data-proposal-check="${i}" ${u.can_check_proposal?'':'disabled'}`):''}${S.button(u.role==='coordinate'?'派发提案回合':'派发本单元',`data-coop-send="${i}" ${u.can_send&&!(c.handoff_stage?.saved_v3&&(successorPending(item)||proposalPending(item)))?'':'disabled'}`)}${u.latest_run_id&&!item.teamwork.plan.file_handoffs?.length?S.button('明确重试本单元',`data-coop-send="${i}" data-coop-retry="true" ${u.can_retry?'':'disabled'}`):''}</div></article>`).join('')}`;
    return `<div class="wi-cooperation"><h3>${c.organization_mode==='host_experts'&&c.continuous_supported?'主持人—专家连续合作':c.organization_mode==='leader'&&c.continuous_supported?'负责人连续组织':fixed?'固定流程单元派发':'提案与单步派发'}</h3>${hostDiscussionView(c)}${leaderRevisionHistory(c)}${coordinatorStageView(c.handoff_stage)}${postRoundSaveView(item)}${textReworkView(item)}${fixed?(c.continuous_supported?flowView(c):'<p data-handoff-limits>同路径交接支持明确单步派发；完成后可在下方细化保护范围外的文字职责。连续流程和同修订重试尚未开放。</p>'):(c.closed_reason?'':fileAppliedView(item)+unitRevisionAppliedView(item)+reworkRevisionView(item)+proposalView(c)+roundRevisionView(c)+diagnosticView(c)+fileDiagnosticView(c))}<p class="sp-note">${fixed?(c.continuous_supported?(c.organization_mode==='host_experts'?'专家共同讨论方案与分工；主持人回应异议、协调协助并核对交付，讨论和执行共用累计授权。':c.organization_mode==='leader'?'负责人根据当前准确成果安排下一项工作；协调和执行共用累计授权。':'可逐项派发或明确启动连续串行流程。'):'按前版、后继、下游依次单步派发并保存文件。'):(c.closed_reason?'原累计授权、准确输入与历史记录已保留。':c.handoff_stage?'先检查负责人／主持人提案，再单步派发；A捕获后交付B，B准确完成后作为当前文件来源。依赖调整可检查预览并明确应用；保存后暂停，须恢复责任并取得新提案。可在接续提案回合区明确登记；保存后须恢复责任。':'提案单元返回安排，程序检查后单步派发所选工作。依赖调整经影响预览后可明确应用，切换后暂停并重新核对责任。连续组织后续接入。')}前置准确回答随输入版本交接；回答可用、检查、独立审查和使用验收分别记录。</p><p data-cooperative-usage="${used}">本任务合作累计 ${used} 次尝试 · 费用与订阅余额未知。释放责任、修订方案或重试均保留累计次数。</p>${a?`<p>累计授权上限 ${h(a.max_attempts_total)} 次；新增派发截止 ${h(S.when(a.dispatch_before))}。</p>`:'<p>尚无本驱动的明确派发授权。</p>'}<form id="work-item-cooperation-auth">${S.field('累计尝试上限','max_attempts_total',a?.max_attempts_total||Math.min(1000,used+Math.max(2,c.units.length)),'type="number" min="1" max="1000" required')}${S.field('允许新增派发的时长（分钟）','minutes','30','type="number" min="1" max="10080" required')}${c.continuous_supported?fixedReworkOption(a?.auto_rework_checks):''}<button type="submit" ${c.can_authorize?'':'disabled'}>保存合作派发授权</button><p class="sp-note">保存授权不发送模型。截止时间只阻止新增派发；在途回合沿用官方工作端配置，可在运行区停止。尚不支持硬费用上限。</p></form>${compact?controlDisclosure('unit-controls','单步操作与文件检查',unitControls):unitControls}</div>`;
  }
  function adjustmentView(a,p){
    if(a.protocol==='COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_ADJUSTMENT_PREVIEW_V2'){
      const names=keys=>keys.map(k=>a.candidate_units.find(u=>u.key===k)?.title||k).join('、')||'无';
      return `<div data-v8-adjustment-preview="${h(a.version)}"><h4>依赖调整影响预览</h4><p>直接调整：${h(names(a.direct_changes))}。受影响：${h(names(a.affected_keys))}。保护工作：${h(names(a.protected_keys))}。</p>${a.candidate_units.filter(u=>a.direct_changes.includes(u.key)).map(u=>`<p>候选前置：${h(u.title)} ← ${h(names(u.depends))}</p>`).join('')}<p>沿用成果：${h(names(Object.keys(a.carry_sources)))}。历史协调：${h(names(a.historical_coordinators))}。</p><p>当前累计 ${h(a.usage.attempts)} 次尝试。保存保留原授权与截止，后继运行另行接入。</p></div>`;
    }
    if(a.protocol==='COORDINATOR_FILE_HANDOFF_ADJUSTMENT_PREVIEW_V1'){
      const names=keys=>keys.map(k=>a.candidate_units.find(u=>u.key===k)?.title||k).join('、')||'无';
      return `<div data-adjustment-preview="${h(a.version)}"><h4>依赖调整影响预览</h4><p>直接调整：${h(names(a.direct_changes))}。受影响：${h(names(a.affected_keys))}。保护工作：${h(names(a.protected_keys))}。</p><p>沿用结果：${h(names(Object.keys(a.carry_sources)))}。历史协调：${h(names(a.historical_coordinators))}。</p><p>恢复后可先运行协调：${h(names(a.resume_coordinate_keys))}。</p>${a.qualifications.map(u=>`<p data-adjustment-qualification="${h(u.key)}">${h(names([u.key]))} · ${h(({NOT_RUN:'尚未运行',HISTORICAL_COORDINATOR:'历史协调安排',HISTORICAL_RETIRED:'历史退役前版',ELIGIBLE_RESULT:'具备沿用条件',RECORDED:'已登记准确运行'})[u.qualification]||u.qualification)}</p>`).join('')}<p>本任务累计 ${h(a.usage.attempts)} 次尝试；保留原授权上限和最初派发截止。</p><p>${p.transition?'调整已应用；须明确恢复合作责任，再运行新的协调提案。':p.can_apply?'应用后保存新方案并暂停合作责任；实际执行须明确恢复。':'此预览保留为历史；当前不能应用。'}</p></div>`;
    }
    const labels={AFFECTED:'需要重新处理',ELIGIBLE_RESULT:'具备沿用条件',NOT_RUN:'尚无运行',HISTORICAL_COORDINATOR:'历史提案来源'},reasons={DEPENDENCIES_CHANGED:'前置关系调整',RESULT_UNAVAILABLE:'准确结果不可用',ARTIFACT_UNAVAILABLE:'文件或检查证据不足',FROZEN_INPUT_CHANGED:'原冻结前置版本已变化',AFFECTED_DEPENDENCY:'依赖受影响工作'};
    const name=key=>a.candidate_units.find(u=>u.key===key)?.title||key,names=keys=>keys.map(name).join('、')||'无';
    return `<div data-adjustment-preview="${h(a.candidate_version)}"><h4>依赖调整影响预览</h4><p>直接调整：${h(names(a.direct_changes))}。受影响：${h(names(a.affected_units))}。具备沿用条件：${h(names(a.eligible_results))}。</p><p>本任务合作累计 ${h(a.usage.attempts)} 次尝试；授权上限 ${h(a.authorization.max_attempts_total)} 次，新增派发截止 ${h(S.when(a.authorization.dispatch_before))}。费用及原生内部请求总量未知。</p>${a.units.map(u=>`<details data-adjustment-unit="${h(u.key)}"><summary>${h(u.title)} · ${h(labels[u.state])}</summary><p>原前置：${h(names(u.old_depends))}。候选前置：${h(names(u.candidate_depends))}。</p>${u.reasons.map(r=>`<p>${h(reasons[r]||r)}</p>`).join('')}${u.source?`<p>准确尝试 ${h(u.source.attempt_id)} · 文件检查 ${h(u.source.check_status)}</p><p>结果版本 ${h(u.source.version)}</p>${u.source.artifact?`<p>产物版本 ${h(u.source.artifact.version)}</p>`:''}`:''}</details>`).join('')}<p class="sp-note">${p.transition?'调整已应用；原方案和来源保留为历史。继续前须明确重新选定合作责任。':'当前保存影响预览。应用将保存新方案并暂停责任，保留原累计次数与截止。'}历史负责人／主持人安排在切换后失效；沿用条件须在实际使用时重新核对。独立审查未开展，Human 未验收。</p></div>`;
  }
  function reworkView(a){const labels={AFFECTED:'需要重新处理',ELIGIBLE_RESULT:'具备沿用条件',NOT_RUN:'尚无运行',HISTORICAL_COORDINATOR:'历史提案来源'},reasons={EXPLICIT_REWORK:'明确提出返工',RESULT_UNAVAILABLE:'原准确结果不可用',LINEAGE_INVALID:'原成果沿用已失效',FROZEN_INPUT_CHANGED:'原冻结前置版本已变化',AFFECTED_DEPENDENCY:'依赖受影响工作',ARTIFACT_UNAVAILABLE:'文件或检查证据不足'};
    const names=keys=>keys.map(key=>a.candidate_units.find(u=>u.key===key)?.title||key).join('、')||'无';
    return `<div data-rework-preview="${h(a.candidate_version)}"><h4>文字返工影响预览</h4><p>直接返工：${h(names(a.direct_changes))}。受影响：${h(names(a.affected_units))}。具备沿用条件：${h(names(a.eligible_results))}。</p><p>本任务合作累计 ${h(a.usage.attempts)} 次尝试；原授权上限 ${h(a.authorization?.max_attempts_total??'尚无')} 次，原新增派发截止 ${h(a.authorization?S.when(a.authorization.dispatch_before):'尚无')}。</p>${a.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}${a.units.map(u=>`<details data-rework-unit="${h(u.key)}"><summary>${h(u.title)} · ${h(labels[u.state])}</summary>${u.reasons.map(r=>`<p>${h(reasons[r]||r)}</p>`).join('')}${u.source?`<p>原状态 ${h(S.state(u.diagnostic.state))} · 准确尝试 ${h(u.source.attempt_id)}</p><p>原运行 ${h(u.source.run_id)} · 原输入 ${h(u.source.input_version)}</p><p>准确证据版本 ${h(u.source.version)}</p>${[...u.diagnostic.lineage_problems,...u.diagnostic.frozen_input_problems].map(x=>`<p>${h(x)}</p>`).join('')}`:''}</details>`).join('')}<p class="sp-note">已保存诊断与影响预览。应用文字返工会保存新方案并暂停，继续沿用原累计上限和截止；恢复后按新提案执行，原失败保留。独立审查未开展，Human 未验收。</p></div>`;
  }
  function fileSourceView(a){if(!a)return '';return `<details data-file-source-audit="${h(a.state)}"><summary>历史完整性与文件替代候选</summary><p>${a.state==='BLOCKED'?'历史来源核对受阻':'历史来源核对通过；替代候选尚未应用'}</p><p>旧运行、原输入与保存快照分别核对；当前依赖仍须满足实际文件版本及检查。本次没有执行许可。</p>${a.problems.map(x=>`<p>${h(x)}</p>`).join('')}${a.replacement_candidates.map(c=>`<p data-file-replacement-candidate>${h(c.unit_key)} · 文件 ${h(c.declarations.outputs.join('、'))}<br>准确原运行 ${h(c.run_id)} · 旧快照 ${c.saved_versions.length}版</p>`).join('')}${a.original_frontier_checks.map(c=>`<p>原方案 ${h(c.plan.id)} · ${c.state==='MATCHED'?'准确原前沿匹配':'原前沿受阻'}</p>`).join('')}</details>`;}
  function fileReworkView(a){const labels={AFFECTED:'需要重新处理',ELIGIBLE_RESULT:'具备沿用条件',NOT_RUN:'尚无运行',HISTORICAL_COORDINATOR:'历史提案来源'},reasons={EXPLICIT_FILE_REWORK:'明确提出文件返工',RESULT_UNAVAILABLE:'原准确结果不可用',LINEAGE_INVALID:'原成果沿用已失效',FROZEN_INPUT_CHANGED:'原冻结前置版本已变化',AFFECTED_DEPENDENCY:'依赖受影响工作',ARTIFACT_UNAVAILABLE:'当前项目文件或已声明检查不可用；旧保存版本另行核对'},names=keys=>keys.map(k=>a.units.find(u=>u.key===k)?.title||k).join('、')||'无';
    return `<div data-file-rework-preview="${h(a.candidate_version)}"><h4>文件返工影响预览</h4><p>直接返工：${h(names(a.direct_changes))}。受影响：${h(names(a.affected_units))}。具备沿用条件：${h(names(a.eligible_results))}。</p><p>本任务合作累计 ${h(a.usage.attempts)} 次尝试；原授权上限 ${h(a.authorization?.max_attempts_total??'尚无')} 次，原新增派发截止 ${h(a.authorization?S.when(a.authorization.dispatch_before):'尚无')}。</p>${a.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}${fileSourceView(a.history_source_audit)}${a.units.map(u=>`<details data-file-rework-unit="${h(u.key)}"><summary>${h(u.title)} · ${h(labels[u.state])}</summary>${u.reasons.map(r=>`<p>${h(reasons[r]||r)}</p>`).join('')}${u.source?`<p>原状态 ${h(S.state(u.diagnostic.state))} · 准确尝试 ${h(u.source.attempt_id)}</p><p>原输入版本 ${h(u.source.input_version)}</p>${[...u.diagnostic.lineage_problems,...u.diagnostic.frozen_input_problems].map(x=>`<p>${h(x)}</p>`).join('')}`:''}${u.original_files?`<p>原实际输入：${u.original_files.input_state==='INTACT'?'保存字节核对通过':'需恢复准确历史'}；旧文件快照：${u.original_files.output_state==='INTACT'?'全部已登记版本完整':u.original_files.output_state==='NOT_RECORDED'?'尚无历史快照':'需恢复准确历史'}。</p><details><summary>原实际 input manifest</summary>${S.raw(u.original_files.actual_input_manifest)}</details>${u.original_files.snapshots.map(f=>`<p>旧快照版本 ${h(f.reference.version)} · ${h(f.state)} · 原检查 ${h(f.snapshot.check_status)}</p><details><summary>旧文件 manifest</summary>${S.raw(f.snapshot.manifest)}</details>`).join('')}`:''}${u.output_intents.map(o=>`<p data-file-output-intent>文件 ${h(o.path)} · 新身份待新方案和运行建立 · 新版本尚未产生</p>`).join('')}<p>下游依赖条件：${h(u.dependency_gate)}；按新方案完整依赖核对准确结果、文件与已声明检查。</p></details>`).join('')}<p class="sp-note">已保存只读文件返工预览，原文件声明、资源、累计上限和截止继续适用。变化的当前文件仅作诊断；旧快照与原输入分别保存，文件观察不能证明创作归属。应用后保存新方案并暂停；明确恢复后须按新提案执行，首次建立新运行和新文件版本。同路径整合另行处理。独立审查未开展，Human 未验收。</p></div>`;
  }
  function unitRevisionView(a){const labels={AFFECTED:'需要重新处理',ELIGIBLE_RESULT:'具备沿用条件',NOT_RUN:'尚无运行',HISTORICAL_COORDINATOR:'历史提案来源'},reasons={UNIT_ADDED:'原职责内新增',DEFINITION_CHANGED:'局部定义修订',RESULT_UNAVAILABLE:'原准确结果不可用',LINEAGE_INVALID:'原成果沿用已失效',FROZEN_INPUT_CHANGED:'原冻结前置版本已变化',AFFECTED_DEPENDENCY:'依赖受影响工作',ARTIFACT_UNAVAILABLE:'文件或检查证据不足'};
    const names=keys=>keys.map(k=>a.candidate_units.find(u=>u.key===k)?.title||k).join('、')||'无';
    return `<div data-unit-revision-preview="${h(a.candidate_version)}"><h4>普通单元局部修订预览</h4><p>新增：${h(names(a.added_units))}。修订：${h(names(a.revised_units))}。受影响：${h(names(a.affected_units))}。具备沿用条件：${h(names(a.eligible_results))}。</p><p>本任务合作累计 ${h(a.usage.attempts)} 次尝试；原授权上限 ${h(a.authorization?.max_attempts_total??'尚无')} 次，原新增派发截止 ${h(a.authorization?S.when(a.authorization.dispatch_before):'尚无')}。</p>${a.execution_blockers.map(x=>`<p>${h(x)}</p>`).join('')}${a.units.map(u=>`<details data-unit-revision-unit="${h(u.key)}"><summary>${h(u.title)} · ${h(labels[u.state])}</summary>${u.reasons.map(r=>`<p>${h(reasons[r]||r)}</p>`).join('')}${u.basis?`<p>原职责依据：${h(u.basis.unit_key)} · ${h(u.basis.reason)}</p><p>原定义版本 ${h(u.basis.unit_revision)} · 原任务要求版本 ${h(u.basis.work_requirements_version)}</p><p>原局部目标：${h(u.original_definition?.goal||'新增单元，职责继承原依据')}</p><p>建议局部目标：${h(u.candidate_definition.goal)}</p><p>建议完成要求：${h(u.candidate_definition.acceptance)}</p><p>原资源成员：${h(u.candidate_definition.member.name)} · 职责 ${h(u.candidate_definition.role)}</p>`:''}<p>原前置：${h(names(u.old_depends))}。候选前置：${h(names(u.candidate_depends))}。</p>${u.source?`<p>原状态 ${h(S.state(u.diagnostic.state))} · 准确尝试 ${h(u.source.attempt_id)}</p><p>原运行 ${h(u.source.run_id)} · 原输入 ${h(u.source.input_version)}</p><p>准确证据版本 ${h(u.source.version)}</p>${[...u.diagnostic.lineage_problems,...u.diagnostic.frozen_input_problems].map(x=>`<p>${h(x)}</p>`).join('')}`:''}</details>`).join('')}<p class="sp-note">已保存原职责内的模型建议及完整影响预览；应用后保存新方案并暂停，恢复后先取得新提案。程序已核对结构与原资源，关键目标、范围、写入、资源、权限、预算或原截止变化须由 Human 明确管理。原累计上限和截止继续适用。独立审查未开展，Human 未验收。</p></div>`;
  }
  function unitRevisionAppliedView(item){const r=item.teamwork?.plan?.unit_revision_lineage;if(!r)return '';const labels={REVISED_EXISTING:'原单元局部修订',NEW_FROM_DECLARED_DUTY:'原职责内新增',DEPENDENCY_AFFECTED:'依赖受影响'};
    return `<details class="wi-artifacts" data-unit-revision-applied><summary>已应用普通单元局部修订 · 原来源</summary><p>${h(r.reason)}</p><p>重新选定合作责任后，先取得新提案，再按新方案完整依赖执行。原任务要求、累计上限、截止和权限继续适用。</p>${r.units.map(u=>`<details data-unit-revision-origin="${h(u.key)}"><summary>${h(u.title)} · ${h(labels[u.origin_kind])}</summary><p>原目标：${h(u.original_definition?.goal||'新增单元，无旧同名运行')}</p><p>当前局部目标：${h(u.candidate_definition.goal)}</p><p>当前完成要求：${h(u.candidate_definition.acceptance)}</p>${u.basis_origin?`<p>原职责依据：${h(u.basis_origin.basis.unit_key)} · ${h(u.basis_origin.basis.reason)}</p><p>依据单元原目标：${h(u.basis_origin.definition.goal)}</p>${u.basis_origin.source?`<p>依据单元原尝试 ${h(u.basis_origin.source.attempt_id)}</p>`:''}`:''}${u.source?`<p>原状态 ${h(S.state(u.diagnostic.state))} · 原尝试 ${h(u.source.attempt_id)}</p><p>原输入 ${h(u.source.input_version)}</p>`:'<p>无旧同名运行；来源独立记录，首次新方案执行不引用旧运行作为重试。</p>'}</details>`).join('')}</details>`;
  }
  function reworkRevisionView(item){const r=item.teamwork?.plan?.rework_lineage;if(!r)return '';
    return `<details class="wi-artifacts" data-rework-revision><summary>已应用文字返工 · 原来源</summary><p>${h(r.reason)}</p><p>合作责任按新方案恢复后，先派发新提案，再按正常依赖执行。原累计上限和截止继续适用。</p>${r.units.map(u=>`<p data-rework-origin="${h(u.key)}">${h(u.title)} · ${u.source?`原状态 ${h(S.state(u.diagnostic.state))} · 原尝试 ${h(u.source.attempt_id)}<br>原输入 ${h(u.source.input_version)}`:'原先尚未运行，随受影响依赖重新处理'}</p>`).join('')}</details>`;
  }
  function proposalView(c){const p=c.proposal;if(!p)return '';const s=p.record?.snapshot,labels={POST_ROUND_ADJUSTMENT_PREVIEW:'本轮依赖调整预览已核对',NONE:'尚无已检查提案',VALID:'提案通过程序准入',DELIVERY_READY:'负责人已核对整合交付',INVALID:'提案未通过程序准入',ADJUSTMENT_READY:'依赖调整预览已保存',ADJUSTMENT_PREVIEW:'历史依赖调整预览',REWORK_PREVIEW:'文字返工预览已保存',FILE_REWORK_PREVIEW:'文件返工预览已保存',UNIT_REVISION_PREVIEW:'普通单元修订预览已保存',PAUSE_SUGGESTED:'负责人／主持人建议暂停',STALE:'提案当前证据已失效',CONSUMED:'提案已使用'};
    return `<div class="wi-artifacts" data-proposal-state="${h(p.state)}"><h4>提案准入</h4><p>${h(labels[p.state]||p.state)}</p>${[...(s?.problems||[]),...p.problems].map(x=>`<p>${h(x)}</p>`).join('')}${s?`<p>${h(s.decision?.reason||'请查看准确来源回复')}</p>${s.adjustment?adjustmentView(s.adjustment,p):''}${s.rework?reworkView(s.rework):''}${s.file_rework?fileReworkView(s.file_rework):''}${s.unit_revision?unitRevisionView(s.unit_revision):''}${s.target?`<p>所选单元：${h(c.units.find(u=>u.key===s.target.key)?.title||s.target.key)}</p>`:''}<details><summary>提案来源与版本</summary><p>尝试 ${h(s.source.attempt_id)} · 会话 ${h(s.source.session_id)} · 回合 ${h(s.source.turn_id)}</p><p>检查版本 ${h(p.reference.version)}</p>${p.consumed_by?`<p>准确派发 ${h(p.consumed_by)}</p>`:''}${p.transition?`<p data-plan-transition>方案切换 ${h(p.transition.to_plan.id)} · 原累计授权已转移</p>`:''}</details>${p.can_apply?S.button('应用依赖调整并暂停','data-proposal-apply'):''}${p.can_apply_rework?S.button('应用文字返工并暂停','data-rework-apply'):''}${p.can_apply_unit_revision?S.button('应用局部修订并暂停','data-unit-revision-apply'):''}${p.can_apply_file_rework?S.button('应用文件返工并暂停','data-file-rework-apply'):''}`:''}<p class="sp-note">先派发提案回合，收到回复后检查提案，再按有效提案派发所选单元。任务、责任、授权或文件证据变化后须重新检查。建议暂停时可用上方责任操作暂停。</p></div>`;
  }
  function roundRevisionView(c){const r=c.coordination_revision;if(!r)return '';const previous=r.record;
    return `<details class="wi-artifacts" data-round-revision><summary>接续提案回合</summary>${previous?`<p data-round-revision-saved>已登记新提案回合 · 原尝试 ${h(previous.source.attempt_id)}</p><p>${h(previous.reason)}</p>`:''}<p>为最新已完成的协调单元登记新回合，保留旧回复和普通成果。登记后暂停，在合作责任区明确恢复后派发；次数及截止继续沿用。</p>${r.source?`<p>准确来源 ${h(r.source.attempt_id)} · ${h(c.units.find(u=>u.key===r.unit_key)?.title||r.unit_key)}</p>`:''}${r.problems.map(x=>`<p>${h(x)}</p>`).join('')}<form id="work-item-coordination-revision">${S.text('新提案回合依据','reason','读取当前成果并重新形成安排','required maxlength="2000"')}<button type="submit" ${r.can_revise?'':'disabled'}>登记新提案回合</button></form></details>`;
  }
  function diagnosticView(c){const r=c.rework_diagnostic;if(!r)return '';const d=r.snapshot||r.record;
    return `<details class="wi-artifacts" data-rework-diagnostic><summary>文字返工诊断接续</summary>${r.record?`<p data-diagnostic-saved>已登记诊断回合 · ${h(r.record.reason)}</p>`:''}<p>保留原失败及依赖图，登记后暂停；重新选定责任并明确派发诊断回合。诊断只提出返工或暂停建议，累计上限与原截止继续沿用。</p>${d?`<p>诊断协调单元：${h(c.units.find(u=>u.key===d.unit_key)?.title||d.unit_key)}</p>${d.diagnostics.filter(x=>x.source).map(x=>`<p data-diagnostic-source="${h(x.key)}">${h(x.key)} · 原状态 ${h(S.state(x.state))} · 原尝试 ${h(x.source.attempt_id)}<br>原输入 ${h(x.source.input_version)} · 准确证据 ${h(x.source.version)}</p>`).join('')}`:''}${r.problems.map(x=>`<p>${h(x)}</p>`).join('')}<form id="work-item-rework-diagnostic">${S.text('文字诊断依据','reason','读取准确原失败并提出文字返工安排','required maxlength="2000"')}<button type="submit" ${r.can_register?'':'disabled'}>登记文字诊断回合</button></form></details>`;
  }
  function fileDiagnosticView(c){const r=c.file_rework_diagnostic;if(!r)return '';const d=r.snapshot||r.record,files=d?.file_protection?.original_files||[];
    return `<details class="wi-artifacts" data-file-rework-diagnostic><summary>文件返工诊断接续</summary>${r.record?`<p data-file-diagnostic-saved>已登记文件诊断回合 · ${h(r.record.reason)}</p>`:''}<p>核对原实际输入及全部已登记旧快照，登记后暂停；重新选定责任并明确派发诊断回合。只提出文件返工或暂停建议，原累计上限和截止继续沿用。</p><p>变化的当前文件与旧保存版本只作诊断；本次没有普通文件执行或替代许可。</p>${d?`<p>诊断协调单元：${h(c.units.find(u=>u.key===d.unit_key)?.title||d.unit_key)}</p>${d.diagnostics.filter(x=>x.source).map(x=>`<p data-file-diagnostic-source="${h(x.key)}">${h(x.key)} · 原状态 ${h(S.state(x.state))} · 原尝试 ${h(x.source.attempt_id)}<br>原输入 ${h(x.source.input_version)} · 准确证据 ${h(x.source.version)}</p>`).join('')}${files.map(f=>`<p>原运行 ${h(f.run_id)} · 实际输入 ${h(f.input_state)} · 旧快照 ${h(f.output_state)}（${f.snapshots.length}版）</p>`).join('')}`:''}${r.problems.map(x=>`<p>${h(x)}</p>`).join('')}<form id="work-item-file-rework-diagnostic">${S.text('文件诊断依据','reason','读取准确原文件不足并提出返工安排','required maxlength="2000"')}<button type="submit" ${r.can_register?'':'disabled'}>登记文件诊断回合</button></form></details>`;
  }
  function artifactView(u,i){const f=u.result?.file_artifacts;
    if(u.result?.state==='HISTORICAL_RETIRED'||u.qualification==='HISTORICAL_RETIRED')return `<div class="wi-artifacts" data-retired-artifact><p>原文件快照保留为历史，已退出当前输入。</p><details><summary>前版历史文件快照</summary><p>原快照版本 ${h(f?.reference?.version||'需核对准确历史记录')}</p>${Object.entries(f?.manifest||{}).map(([name,r])=>`<p>${h(name)} · ${h(r.bytes)} 字节<br>SHA-256 ${h(r.sha256)}</p>`).join('')}</details></div>`;
    if(!f?.required_outputs?.length)return '';const labels={MISSING:'尚未保存文件证据',CURRENT:'文件版本仍有效',STALE:'文件证据已失效',HISTORICAL_RETIRED:'历史退役前版，保留原快照'};
    return `<div class="wi-artifacts" data-artifact-state="${h(f.state)}"><p>${h(labels[f.state]||f.state)} · 本地检查 ${h(f.check_status)}</p>${f.problems.map(p=>`<p>${h(p)}</p>`).join('')}<details><summary>产物版本与检查记录</summary>${(f.missing_outputs||[]).map(name=>`<p>缺失声明产物：${h(name)}</p>`).join('')}${Object.entries(f.manifest).map(([name,r])=>`<p>${h(name)} · ${r.bytes} 字节<br>SHA-256 ${h(r.sha256)}</p>`).join('')}${f.checks.map(c=>`<p>${h(c.definition.file)} · ${c.definition.kind==='utf8_section_length'?'正文长度':h(c.definition.kind)} · ${c.passed?'通过':'未通过'}${c.definition.kind==='utf8_section_length'?` · 要求 ${h(c.definition.min_chars)}–${h(c.definition.max_chars)} 字符${Number.isInteger(c.observed_chars)?'，实测 '+h(c.observed_chars):'，无法统计'}`:''}${c.body_repeated_outside?' · 检测到边界外完整重复正文':''}${c.definition.kind==='utf8_section_length'?' · 计数口径：'+(c.definition.counting==='han_digits'?'汉字和数字':'字母和数字'):''}${c.definition.text?' · '+h(c.definition.text):''}</p>`).join('')}<p class="sp-note">保存的是准确回合后的所选字节；独立审查未开展，Human 未验收。</p></details>${u.can_check?S.button('保存并检查文件',`data-artifact-check="${i}"`):'<p class="sp-note">当前运行不能再次保存产物；原快照与检查记录仍保留。</p>'}<p class="sp-note">连续流程自动保存声明产物并执行本地检查，通过后再派发下游；手动保存须先暂停流程。</p></div>`;
  }
  const deliveryAction=(action,unit,name)=>`data-delivery-action="${action}" data-delivery-run="${h(unit.run_id)}" data-delivery-file="${h(name)}"`;
  const locateDeliveryButton=(unit,name)=>S.button('在资源管理器中显示',
    deliveryAction('locate',unit,name)+` aria-label="在资源管理器中显示 ${h(name)}"`);
  function deliveryUnit(unit){
    const files=Object.keys(unit.manifest);
    return `<article data-delivery-unit="${h(unit.key)}"><h3>${h(unit.title)}</h3>
      ${files.length?`<p class="sp-note">${unit.check_status==='PASS'?'声明检查通过':'文件已登记'} · ${files.length} 个文件</p>
        <div class="sp-row">${files.map(name=>S.button('预览 '+name,deliveryAction('preview',unit,name))).join('')}</div>`:''}
      <p><a href="#control/task/${h(unit.task_id)}">${files.length?'查看原运行':'查看文字成果与原运行'}</a></p></article>`;
  }
  function deliveryPreview(item,units){
    const preview=v.deliveryPreview;
    if(preview?.item!==item.id)return '';
    const unit=units.find(entry=>entry.run_id===preview.run&&entry.artifact?.version===preview.version);
    if(!unit)return '';
    const file=preview.data;
    const content=file.kind==='text'?
      (/\.md$/i.test(file.file)?`<div class="markdown wi-delivery-markdown">${markdown(file.content)}</div>`:`<pre>${h(file.content)}</pre>`):
      file.kind==='image'?`<img src="${h(file.image)}" alt="${h(file.file)}">`:
      file.kind==='pdf'?`<iframe title="固定成果 PDF 原件" src="api/fixed-artifact-file-preview?${h(new URLSearchParams({...preview.query,sha256:file.sha256}))}"></iframe>`:
      '<p>下载原件后可在对应应用中打开。</p>';
    return `<section class="wi-artifacts" data-delivery-preview tabindex="-1"><h3>${h(file.file)}</h3>
      <div class="sp-row">${S.button('下载原件',deliveryAction('download',unit,file.file))}
      ${item.project_id?locateDeliveryButton(unit,file.file):''}
      ${S.button('关闭预览','data-delivery-action="close"')}</div>${content}
      <details><summary>保存版本</summary><p>快照 ${h(preview.version)}<br>SHA-256 ${h(file.sha256)}</p></details></section>`;
  }
  function fixedDelivery(item){
    const flow=item.cooperation?.flow,delivery=flow?.delivery;
    if(!delivery)return '';
    const materials=delivery.units.filter(unit=>!unit.final);
    return `<section class="sp-card wi-delivery" data-fixed-delivery="${h(delivery.state)}">
      <div class="sp-row"><h2>${item.teamwork?.plan.mode==='host_experts'?'主持人—专家交付':item.teamwork?.plan.mode==='leader'?'负责人流程交付':'固定流程交付'}</h2><span class="sp-badge">${({CURRENT:'当前交付',SAVED:'已保存交付'})[delivery.state]||'历史交付'}</span></div>
      <p class="sp-note">保存于 ${h(S.when(delivery.recorded_at))}</p>
      ${delivery.state==='HISTORICAL'?`<p>这里保留当时交付的版本。${h(flow.result_evidence_issues.join('；'))}</p>`:''}
      ${delivery.units.filter(unit=>unit.final).map(deliveryUnit).join('')}
      ${materials.length?`<details><summary>过程材料与运行</summary>${materials.map(deliveryUnit).join('')}</details>`:''}
      ${deliveryPreview(item,delivery.units)}</section>`;
  }
  function fixedReworkOption(enabled=false){
    return `<div class="wi-dependencies"><label><input type="checkbox" name="auto_rework_checks" ${enabled?'checked':''}> 允许每个单元自动返工一次</label><p class="sp-note">修正未通过的文件检查或补齐缺失的声明产物。返工使用原成员，计入原累计次数，并受新增派发截止限制。</p></div>`;
  }
  function checkReworkView(run){
    const source=run.input.check_rework,result=run.result;
    const status={PASS:'复查通过',FAIL:'复查未通过',STALE:'复查证据已失效'}[result?.check_status]
      ||(result?.state==='COMPLETED'?'待复查':S.state(result?.state||'UNKNOWN'));
    const checks=source.checks.filter(check=>!check.passed).map(check=>check.definition);
    const names=[...new Set([...checks.map(check=>check.file),...(source.missing_outputs||[])])].join('、');
    const labels={nonempty:'文件须非空',json_parse:'文件须为有效 JSON',utf8_contains:'文件须包含',utf8_section_length:'正文长度须在指定范围内'};
    return `<details data-check-rework-history><summary>${h(run.input.unit.title)} · 检查返工 · ${h(status)}</summary>
      <p>原检查失败的文件：${h(names)}。</p>
      ${(source.missing_outputs||[]).map(name=>`<p>缺失声明产物：${h(name)}</p>`).join('')}
      ${checks.map(check=>`<p>${h(check.file)}：${h(labels[check.kind])}${check.text?'「'+h(check.text)+'」':''}</p>`).join('')}
      <p><a href="#control/task/${h(source.identity.task_id)}">打开原运行</a> · <a href="#control/task/${h(run.task_id)}">打开返工运行</a></p>
      <details><summary>准确失败记录</summary>${S.raw(source)}</details></details>`;
  }
  function hostDiscussionView(c){
    const d=c.host_discussion;if(!d)return '';
    return `<section data-host-discussion><h4>专家讨论与主持人收束</h4>${d.requires_human?'<p>专家提出关键取舍，等待你处理后明确安排。</p>':''}${d.human_response?`<p>你的关键取舍回复：${h(d.human_response.note)}</p>`:''}${d.can_respond?`<form id="work-item-host-human-response">${S.text('原任务范围内的关键取舍回复','note','','required maxlength="4000"')}<p class="sp-note">保存回复后明确启动合作，保留原累计次数和最初截止；更改目标、成员授权或预算需另行管理。</p><button type="submit">保存关键取舍回复</button></form>`:''}${d.experts.map(e=>`<article><strong>${h(e.member_name)}</strong> · ${h(e.unit_key)}${e.feedback?`<p>${h(e.feedback.proposal)}</p><p>异议：${e.feedback.objections.map(h).join('；')||'无'}</p><p>协助请求：${e.feedback.assistance.map(h).join('；')||'无'}</p>`:`<p>${h(e.problem||'等待准确讨论意见')}</p>`}</article>`).join('')}<details><summary>主持人决策记录（${d.decisions.length}）</summary>${d.decisions.map(r=>`<p>${h(r.decision?.discussion?.resolution||r.problems.join('；'))}</p><p class="sp-note">${h(({dispatch:'安排下一工作',pause:'暂停等待',deliver:'核对交付',adjust_dependencies:'调整依赖',revise_units:'修订职责',rework_units:'文字返工',file_rework_units:'文件返工'})[r.decision?.action]||'待核对')} · 已考虑 ${(r.decision?.discussion?.considered||[]).map(e=>h(e.unit_key)).join('、')||'等待专家讨论'}</p>`).join('')}</details></section>`;
  }
  function leaderRevisionHistory(c){
    const rows=c.leader_revision_history||[];
    const labels={adjust_dependencies:'调整工作顺序',revise_units:'细化分工',rework_units:'文字返工',file_rework_units:'文件返工'};
    return rows.length?`<details data-leader-revision-history><summary>${c.organization_mode==='host_experts'?'主持人':'负责人'}修订记录（${rows.length}）</summary>${rows.map(row=>`<p><strong>${h(labels[row.action]||row.action)}</strong> · ${h(row.reason)}</p>`).join('')}<p class="sp-note">修订沿用原累计次数和最初截止；未受影响的成果保留来源。</p></details>`:'';
  }
  function flowView(c){const f=c.flow,row=f.record,labels={SCHEDULED:'等待定时启动',RUNNING:'连续流程运行中',PAUSED:'连续流程已暂停',RESULTS_AVAILABLE:'全部配置所需结果证据已具备'};
    const repairs=c.runs.filter(run=>run.input?.check_rework);
    return `<div data-flow-state="${h(row?.state||'NONE')}"><h4>${c.organization_mode==='host_experts'?'主持人—专家连续流程':c.organization_mode==='leader'?'负责人连续流程':'连续固定流程'}</h4><p>${h(row?(f.result_evidence_current===false?'已保存结果的当前证据已失效':labels[row.state]):'尚未启动连续流程')}${row?' · '+h(f.result_evidence_current===false?f.result_evidence_issues.join('；'):row.reason):''}</p>
      <p data-check-rework-policy>${c.authorization?.auto_rework_checks?'已授权每个单元自动返工一次。':'检查失败时暂停。'}</p>
      ${row?.start_at?`<p>预约时间 ${h(S.when(row.start_at))} · <a href="#planning">查看计划</a></p>`:''}
      <div class="sp-row">${S.button(c.organization_mode==='host_experts'?'启动主持人连续合作':c.organization_mode==='leader'?'启动负责人连续流程':'启动连续固定流程',`data-flow-action="start" ${f.can_start?'':'disabled'}`)}${S.button(row?.state==='SCHEDULED'?'暂停预约':'暂停连续新派发',`data-flow-action="pause" ${f.can_pause?'':'disabled'}`)}${S.button('刷新流程状态','data-item-action="refresh"')}</div>
      ${f.can_start?`<details><summary>预约定时启动</summary><form id="work-item-flow-schedule">
        ${S.field('启动时间','start_at','','type="datetime-local" required')}
        <p class="sp-note">按本机时区 ${h(Intl.DateTimeFormat().resolvedOptions().timeZone)}，早于派发截止 ${h(S.when(c.authorization.dispatch_before))}。服务运行时会在预约后一分钟内启动。重启或错过窗口后，请核对并重新安排。</p>
        <button type="submit">保存一次预约</button></form></details>`:''}
      <p class="sp-note">流程自动保存声明产物并检查，末步检查通过后交付。返工后再次检查。产物失效、回合状态未知、再次失败或无法按原授权返工时暂停。重启后核对责任，再明确启动。</p>
      ${repairs.map(checkReworkView).join('')}</div>`;
  }
  const runButton=(label,action,index,extra='',disabled=false)=>S.button(label,`data-item-run="${action}" data-run-index="${index}" ${extra} ${disabled?'disabled':''}`);
  function requestView(request,run,index){
    const attr=`data-request-id="${h(request.id)}"`,enabled=run.owned&&request.state==='PENDING'&&!run.stop_requested;
    let buttons='';
    if(request.kind==='approval')buttons=runButton('批准本次','approval',index,attr+' data-allow="true"',!enabled)+runButton('拒绝本次','approval',index,attr+' data-allow="false"',!enabled);
    else if(['item/commandExecution/requestApproval','item/fileChange/requestApproval','sdk/tool/requestApproval'].includes(request.method))buttons=(request.params.availableDecisions||['accept','decline','cancel']).filter(d=>['accept','decline','cancel'].includes(d)).map(d=>runButton(({accept:'批准本次',decline:'拒绝本次',cancel:'取消请求'})[d],'native-response',index,attr+` data-decision="${d}"`,!enabled)).join('');
    else if(request.method==='item/permissions/requestApproval')buttons=[true,false].map(allow=>runButton(allow?'批准本次权限':'拒绝本次权限','native-response',index,attr+` data-allow="${allow}"`,!enabled)).join('');
    return `<div class="wi-request"><p>${h(request.description||request.method)} · ${h(request.state==='READY'?'答复已登记，等待传输':request.state==='REPLY_INTENT'?'答复传输待核对':'待处理')}</p>${request.params?`<details><summary>请求详情</summary>${S.raw(request.params)}</details>`:''}<div class="sp-row">${buttons}<a href="#control/task/${h(run.task_id)}">完整审批与输入</a></div></div>`;
  }
  function constraintsView(run){const c=run.constraints;if(!c)return '';const official=c.mode==='OFFICIAL',e=c.policy_evidence||{};
    return `<details class="wi-constraints"><summary>执行约束与依据</summary><p>${c.source==='DISPATCH_SNAPSHOT'?'本次派发快照':'历史记录：按现有资料推导'} · ${official?'官方原生工作':'有限任务'}</p><p>工作目录：${h(c.workspace)}</p><p>访问权限：${h(c.access?.name||c.execution)}${c.access?.native_mode?' · '+h(c.access.native_mode):''}</p><p>${official?(run.cooperative?'本单元的原生回合沿用官方配置；合作累计尝试和新增派发截止见合作授权。':'次数、费用和期限：未设置 Workdesk 上限，沿用工作端配置。'):`原任务预算：${h(JSON.stringify(c.budget))}；本回合截止：${h(S.when(c.deadline))}；累计已用 ${h(c.usage.attempts)} 次尝试。`}</p><p>工具限制：${h(({VENDOR_CONFIGURATION_NO_WORKDESK_CAP:'沿用官方配置',OBSERVE_THEN_INTERRUPT:'原生工具开始后观察计数并中断',WORKDESK_TOOL_GATE:'Workdesk 工具入口检查'})[c.tool_enforcement]||c.tool_enforcement)}</p><p>任务文字要求随指令交给模型；目录本身不建立隔离。${e.status==='NATIVE_CONFIGURATION_RETURNED'?'官方已返回沙箱配置，实际访问限制以工作端落实机制为准。':'本回合尚无可核对的沙箱配置回执。'}官方余额为观察信息，尚未落实硬余额限制。</p></details>`;
  }
  function recoveryView(run,index){const r=run.recovery;if(!r)return '';const labels={CURRENT_TURN:'当前回合仍活动或待核对',LOCAL_WORKER:'本地线程尚未退出',EXIT_UNPROVED:'退出证据不足',REMOTE_UNKNOWN:'远端结果未知',REQUEST_UNKNOWN:'模型请求回执未知',NATIVE_OPERATION:'原生控制在途或回执未知',NATIVE_REPLY:'审批答复尚未确认传输'};
    return `${run.block_reasons?.length?`<p class="sp-note">待核对：${run.block_reasons.map(k=>h(labels[k]||k)).join('；')}。</p>`:''}<div class="sp-row">${r.can_probe_codex?runButton('只读核对官方回合','probe',index):''}${run.runtime_mode==='official'&&r.can_close_local?runButton('关闭本地未知回合','close-local',index):''}</div>${run.remote_state==='UNKNOWN'?'<p class="sp-note">本地退出后仍保留远端未知；核对不会自动发送。当前不能确认远端的工作端保持等待。</p>':''}${r.last_report?`<details><summary>最近核对记录</summary>${S.raw(r.last_report)}</details>`:''}`;
  }
  const publicSkillOptions=new WeakMap();
  const capabilityCategories=[['research','科研探索'],['documents','文档处理'],['computer','电脑操控'],['development','应用开发']];
  function publicSkillPicker(form,skills){
    publicSkillOptions.set(form,skills);
    form.innerHTML=`${S.field('筛选公开 Skill','filter','','type="search" data-public-skill-filter')}
      ${S.select('选择公开 Skill','skill',[['','请选择'],...skills.map(skill=>[skill.name,skill.name])],'')}
      ${S.select('预设分类','scene',capabilityCategories,'research')}
      <p data-public-skill-description class="sp-note"></p>
      <button type="submit">查看来源并采用</button>`;
  }
  function fixedCapabilityRequests(row,presets,editable,used){
    if(!row.requests.length)return '';
    const eligible=presets.filter(preset=>preset.capabilities.length||preset.extensions.length);
    const options=[['','请选择预设'],...eligible.map(preset=>[presetValue(preset),preset.name])];
    const bound=row.capability_bindings;
    const allBound=row.requests.every((_,index)=>bound[index]);
    const required=new Set([...row.required_presets,...Object.values(bound).map(binding=>binding.reference.id)]);
    const remaining=Math.min(20,row.max_attempts_total-used-1);
    const inBudget=required.size<=remaining&&Date.now()/1000<row.dispatch_before;
    return `<h3>本轮需要的能力</h3>
      ${row.requests.map((request,index)=>`<section class="wi-unit" data-fixed-capability-request="${index}">
        <p>${h(request)}</p>
        ${bound[index]?`<p>已选预设 ${h(bound[index].name)}</p>
          ${bound[index].local_bundle?`<p class="sp-note">本机来源 ${h(bound[index].local_bundle.source)} · 文件版本 ${h(bound[index].local_bundle.content_hash.slice(0,12))}</p>`:''}
          ${(bound[index].public_skills||[]).map(skill=>`<p class="sp-note">公开来源 ${h(skill.repository)} · ${h(skill.commit.slice(0,12))} · <a href="${h(skill.url)}" target="_blank" rel="noopener noreferrer">查看源文件</a></p>`).join('')}`:''}
        ${editable?`<form data-fixed-capability="preset" data-index="${index}">
          ${S.field('查找已有预设','filter','','type="search" data-fixed-capability-filter')}
          ${S.select('使用已有预设','preset',options,'')}
          <button type="submit" ${eligible.length?'':'disabled'}>采用所选预设</button>
          ${bound[index]?S.button('撤回本次选择',`type="button" data-fixed-capability-clear="${index}"`):''}
        </form>
        <details><summary>查找公开 Skill</summary>
          <p class="sp-note">从 Hugging Face 公开目录查找。筛选在本机进行。</p>
          ${S.button('读取公开目录','data-public-skill-catalog')}
          <form data-fixed-capability="public" data-index="${index}"></form>
        </details>
        <details><summary>查看本机 Skill 或插件</summary>
          <form data-fixed-capability="import" data-index="${index}">
            ${S.field('能力包文件夹','path','','required')}
            ${S.field('能力包来源','source','','required maxlength="1000"')}
            ${S.select('能力包类型','kind',[['skill','Skill'],['plugin','插件']],'skill')}
            ${S.select('预设分类','scene',capabilityCategories,'research')}
            <p class="sp-note">预览后可确认文件版本和声明的能力。确认会保存并启用本任务使用的预设。</p>
            <button type="submit">预览本机能力</button>
            <div data-local-bundle-preview></div>
          </form>
        </details>`:''}
      </section>`).join('')}
      ${editable?S.button('使用所选能力继续准备',`data-fixed-action="continue" ${allBound&&inBudget?'':'disabled'}`):''}
      ${editable&&!inBudget?'<p class="sp-note">原授权的剩余次数或派发时间不足。可释放本次准备并重新授权。</p>':''}
      <p><a href="#extensions">管理能力预设</a></p>`;
  }
  function activePreparation(item){
    const host=item.host_preparation,leader=item.leader_preparation,fixed=item.fixed_preparation;
    if(host?.record&&host.record.state!=='RELEASED')return host;
    if(leader?.record&&leader.record.state!=='RELEASED')return leader;
    if(fixed?.record)return fixed;
    return host?.record?host:leader?.record?leader:fixed;
  }
  const preparationMode=item=>activePreparation(item)?.record?.organization||'fixed';
  function preparationBudget(event){
    const input=event.target,form=input.form;
    if(!current().active||form?.id!=='work-item-fixed-prepare')return;
    if(input.name==='limit'){input.dataset.userEdited='true';return;}
    if(input.name!=='organization')return;
    const limit=form.querySelector('[name="limit"]');
    if(!limit||limit.dataset.userEdited==='true')return;
    if(!limit.dataset.baseBudget)limit.dataset.baseBudget=limit.value;
    limit.value=input.value==='host_experts'?String(Math.min(1000,Number(limit.dataset.baseBudget)+8)):limit.dataset.baseBudget;
  }
  document.addEventListener('input',preparationBudget);
  document.addEventListener('change',preparationBudget);
  function fixedPreparation(item,{allowStart=true}={}){
    const preparation=activePreparation(item), row=preparation?.record;
    const runs=item.runtime?.runs||[], own=runs.find(run=>run.attempt_id===row?.attempt_id);
    const held=row&&!['APPLIED','RELEASED'].includes(row.state);
    const canStart=allowStart&&!held&&!item.teamwork&&item.state==='OPEN'&&!item.runtime.driver_protected
      &&item.runtime.state!=='MANUAL'&&!runs.some(run=>run.blocked);
    const members=(v.resources?.members||[]).filter(member=>member.available);
    const presets=v.presets.filter(preset=>preset.enabled);
    const labels={PREPARING:'正在准备方案',APPLIED:'方案已交给连续合作流程',BLOCKED:'方案待处理',
      NEEDS_CAPABILITY:'需要补充能力',NEEDS_RECONCILIATION:'准备回合待核对',
      PAUSED_AFTER_RESTART:'服务重启后等待核对',RELEASED:'本次准备已释放'};
    if(!row&&!canStart)return '';
    const used=preparation.attempts_used;
    const finished=own&&!own.local_worker_alive&&!own.blocked;
    const canCheck=held&&finished&&['PAUSED_AFTER_RESTART','NEEDS_RECONCILIATION'].includes(row.state);
    return `<section class="sp-card" data-fixed-preparation="${h(row?.state||'READY')}">
      <div class="sp-row"><h2>按任务目标安排合作</h2>${S.button('刷新准备状态','data-item-action="refresh"')}</div>
      ${row?`<p>${h(labels[row.state]||row.state)} · ${h(row.runtime_route.harness)}</p>
        ${item.cooperation?.flow?.result_evidence_current?`<p>成果与检查记录已齐备。</p>`:''}
        <p class="sp-note">已用准备回合 ${used} 次。该次授权共 ${row.max_attempts_total} 次，派发截止 ${h(S.when(row.dispatch_before))}。${row.auto_rework_checks?'已授权每个单元自动返工一次。':''}</p>
        <p>默认审批方式：${h(fixedAccessModes.find(([mode])=>mode===(row.access||'workspace'))?.[1])}</p>
        ${(row.member_permissions||[]).map(member=>`<p data-preparation-member-access="${h(member.id)}">${h(member.name)}：${h(fixedAccessModes.find(([mode])=>mode===member.access)?.[1])}</p>`).join('')}
        <p><a href="#control/task/${h(row.task_id)}">打开准备回合</a></p>
        ${row.reason?`<p class="sp-note">${h(row.reason)}</p>`:''}
        ${fixedCapabilityRequests(row,presets,finished&&row.state==='NEEDS_CAPABILITY'&&row.capability_task_current,used)}
        ${canCheck?S.button('核对并应用原方案','data-fixed-action="check"'):''}
        ${held&&finished?S.button('释放本次准备','data-fixed-action="release"'):''}`:''}
      ${canStart?`<form id="work-item-fixed-prepare">
        ${S.select('组织方式','organization',[['fixed','固定流程'],['leader','负责人带队'],['host_experts','主持人—专家']],row?.organization||'fixed')}
        ${S.select('准备成员','planner',[['','请选择成员'],...members.map(member=>[member.id,member.name+' · '+member.model])],'')}
        ${members.length>1?`<fieldset class="wi-unit wi-dependencies"><legend>允许参与的其他成员</legend>
          ${members.map(member=>`<label><input type="checkbox" name="member" value="${h(member.id)}"> ${h(member.name)}</label>`).join(' ')}
          </fieldset>`:''}
        ${presets.length?`<fieldset class="wi-unit wi-dependencies"><legend>允许使用的能力预设</legend>
          ${presets.map(preset=>`<label><input type="checkbox" name="preset" value="${h(presetValue(preset))}"> ${h(preset.name)}</label>`).join(' ')}
          </fieldset>`:''}
        ${S.field('累计尝试上限（含准备）','limit',String(Math.min(1000,used+8)),`type="number" min="${used+2}" max="1000" required`)}
        ${S.field('允许新增派发的分钟数','minutes','60','type="number" min="1" max="10080" required')}
        ${fixedAccessField('workspace','默认审批方式')}
        <details class="wi-unit"><summary>按成员设置审批方式</summary>
          <p class="sp-note">只应用于本次参与的成员。各工作端显示自己支持的选项。</p>
          ${members.map(member=>{
            const modes=(v.resources?.endpoints||[]).find(endpoint=>endpoint.id===member.provider)?.access_modes||[];
            return S.select(member.name,'member_access_'+member.id,[['','使用本任务默认方式'],...modes.filter(mode=>mode.supported).map(mode=>[mode.id,mode.name])],memberAccessDefault(member.provider,modes));
          }).join('')}
        </details>
        ${fixedReworkOption()}
        <p class="sp-note">准备成员也可参与执行。方案通过核对后自动推进。负责人及主持人每次协调也计入累计次数，须留出最终交付协调。主持人先组织专家讨论，再依据意见收束分工；关键取舍暂停交给你。成员的审批方式在本次授权中固定。</p>
        <button type="submit" ${members.length?'':'disabled'}>开始合作任务</button>
      </form>`:''}</section>`;
  }
  function nativeExtensionInventory(run,index){
    if(run.provider!=='qwen'||!run.owned||!run.cooperative)return '';
    const record=run.native_extension_inventory;
    const result=record?.state==='COMPLETED'?record.result:null;
    const projection=result?.source==='QWEN_OFFICIAL_WORKSPACE'?result.projection:null;
    const change=run.native_extension_activation;
    const activation={enabled:'启用',disabled:'停用'};
    const sources={cli_override:'本次命令选择',workspace_override:'工作区设置',legacy_path_rule:'旧路径规则',default:'默认设置'};
    return `<details data-native-extension-inventory><summary>Qwen 工作区扩展</summary>
      <p class="sp-note">启动临时官方工作端读取本任务目录的扩展配置。执行会话中的目录与工具回执见官方 Agent 证据。</p>
      ${runButton('查询工作区扩展','native-extensions',index,'',!run.can_query_extensions)}
      ${record?`<p>查询 ${h(S.state(record.state))} · 操作 ${h(record.id)}</p>`:''}
      ${record?.error?`<p>${h(record.error)}</p>`:''}
      ${projection?`<p>官方工作端 ${h(result.version||'未报告版本')}<br>工作区 ${h(projection.workspaceCwd)}
        <br>信任 ${projection.trusted?'已信任':'未信任'} · 期望配置 ${h(projection.desiredGeneration)} · 生效配置 ${h(projection.appliedGeneration)}</p>
        ${projection.extensions.map(extension=>`<p>${h(extension.name)} · ${h(extension.version)}
          <br>标识 ${h(extension.extensionId)}
          <br>${h(activation[extension.effectiveActivation]||extension.effectiveActivation)} · 来源 ${h(sources[extension.activationSource]||extension.activationSource)}
          · 默认 ${h(activation[extension.defaultActivation]||extension.defaultActivation)}
          · 工作区 ${h(activation[extension.workspaceActivation]||'继承')}</p>
          ${runButton(extension.effectiveActivation==='enabled'?'在此工作区停用':'在此工作区启用',
            'native-extension-activation',index,`data-inventory-id="${h(record.id)}" data-extension-id="${h(extension.extensionId)}"
            data-activation="${extension.effectiveActivation==='enabled'?'disabled':'enabled'}"`,!run.can_change_extensions)}`).join('')||'<p>官方工作区扩展目录为空。</p>'}`:''}
      ${change?`<p>启停操作 ${h(S.state(change.state))} · ${h(change.id)}</p>
        ${change.error?`<p>${h(change.error)}</p>`:''}
        ${change.result?.command_result?`<p>官方 CLI 退出码 ${h(change.result.command_result.exit_code)} · ${h(change.result.command_result.scope)}</p>`:''}
        ${change.result?.after?`<p>核对后期望配置 ${h(change.result.after.desiredGeneration)} · 生效配置 ${h(change.result.after.appliedGeneration)}</p>`:''}`:''}
      <p class="sp-note">启停作用于此工作区的后续原生执行。固定流程暂停或完成后，查询当前版本再配置。</p></details>`;
  }
  function agentEvidence(run){
    const evidence=run.agent_evidence;if(!evidence)return '';
    const {route,capability,native_version:observed,delegation=[],lifecycle=[],command_catalog:commands}=evidence;
    const catalogSources={QWEN_QUERY_INIT:'Qwen Query 初始化回执',NATIVE_COMMAND_REGISTRY:'DeepSeek 原生命令注册表'};
    const packages=Object.entries(route?.local_packages||{}).map(([name,version])=>name+' '+version);
    const loads={FROZEN:'已冻结预设，待原生回执',NATIVE_INPUT_ACCEPTED:'工作端已接收带预设的原生输入',
      NATIVE_OBSERVED:'已收到原生目录回执',FAILED:'加载或执行失败',UNKNOWN:'回执状态待核对',UNRECORDED:'此尝试无可匹配的加载回执'};
    const components={INPUT_ACCEPTED:'工作端已接收明确的 Skill 输入',CONFIG_SUBMITTED:'已提交原生线程配置',
      NAME_IN_CATALOG:'原生 Skill 目录报告同名项',NAME_IN_COMMAND_CATALOG:'原生命令目录报告同名项',
      SERVER_CONNECTED:'原生回执显示已连接',TOOLS_IN_REGISTRY:'原生注册表报告工具',
      MCP_STARTING:'工作端报告启动中',SERVER_READY:'工作端报告已就绪',MCP_START_FAILED:'工作端报告启动失败',
      MCP_START_CANCELLED:'工作端报告启动已取消',MCP_REAUTHENTICATION_REQUIRED:'工作端报告需要重新认证',
      REPORTED_MISSING:'原生回执报告缺少',NOT_CONNECTED:'原生回执未确认连接',NOT_REPORTED:'本次无匹配的逐项回执'};
    const tools={COMPLETED:'已返回工具结果',INTENT:'已登记调用，结果待核对',REJECTED:'调用被拒绝'};
    const outcomes={REPORTED_COMPLETED:'工作端报告工具执行完成',REPORTED_ERROR:'工作端报告工具执行失败'};
    const delegated={OBSERVED:'已观测关联',TOOL_RETURNED:'委派工具已返回结果',RESULT_REPORTED:'已收到子 Agent 结果',
      REPORTED_ERROR:'子 Agent 报告错误',TURN_OBSERVED:'已观测子回合',TURN_ENDED:'子回合已结束',FINISH_REPORTED:'已收到子 Agent 结束回执'};
    const phases={INIT_ACKNOWLEDGED:'初始化已确认',MODEL_ACKNOWLEDGED:'工作端已确认模型选择',
      RESUME_ACKNOWLEDGED:'工作端已确认恢复原会话',RESUME_TARGET_OBSERVED:'已观测到指定的原会话',
      MAIN_RESULT:'已收到主回合结果',WAIT_REQUESTED:'已请求工作端收尾核对',WAIT_ACKNOWLEDGED:'已收到收尾核对回执',
      STREAM_ENDED:'结果流已结束',CLOSE_RETURNED:'工作端关闭接口已返回',STOP_ACKNOWLEDGED:'已收到停止请求回执'};
    const effort=receipt=>`思考强度 ${h(receipt.requested)} · ${receipt.applied?'工作端确认已应用':'工作端未应用该请求'}`;
    return `<details data-agent-evidence><summary>官方 Agent 与能力证据</summary>
      ${route?`<p>派发接口 ${h(route.interface)}</p>
        ${route.profile?`<p>派发配置 ${h(route.profile)}</p>`:''}
        ${packages.length?`<p>派发时本机软件包 ${h(packages.join('、'))}</p>`:''}
        ${route.local_binary_id?`<p>派发时本机安装标识 ${h(route.local_binary_id)}</p>`:''}
        <p>${observed?.user_agent?'本次工作端身份 '+h(observed.user_agent)
          +(observed.binary_id?' · 本机安装标识 '+h(observed.binary_id):'')
          +' · 事件 '+h(observed.event_seq):observed?.version?'本次工作端报告版本 '+h(observed.version):'本次工作端版本无独立回执'}</p>`:''}
      ${commands?`<details data-native-command-catalog><summary>本次原生命令目录</summary>
        <p>${h(catalogSources[commands.source]||commands.source)} · 事件 ${h(commands.event_seq)}</p>
        <p>${commands.names.length?h(commands.names.join('、')):'工作端报告目录为空'}</p></details>`:''}
      ${lifecycle.length?`<details data-native-lifecycle><summary>原生运行回执</summary>
        ${lifecycle.map(receipt=>`<p>${receipt.phase==='EFFORT_REPORTED'?effort(receipt):h(phases[receipt.phase]||receipt.phase)}
          ${receipt.outcome?' · '+h(receipt.outcome):''} · ${h(receipt.source)} · 事件 ${h(receipt.event_seq)}</p>`).join('')}</details>`:''}
      ${delegation.length?`<section data-native-delegation><h4>子 Agent 记录</h4>
        ${delegation.map(entry=>`<p>${entry.identity_kind==='PARENT_TOOL_USE'?'父工具调用':'子会话'} ${h(entry.id)}
          · ${h(delegated[entry.state]||entry.state)}${typeof entry.native_status==='string'?' · '+h(S.state(entry.native_status.toUpperCase())):''}
          · 事件 ${h(entry.event_seq)}${entry.parent_id?`<br>父会话 ${h(entry.parent_id)}`:''}</p>`).join('')}</section>`:''}
      ${capability?`<p>冻结预设 ${h(capability.preset.name)} · 版本 ${h(capability.preset.snapshot_hash.slice(0,12))}</p>
        <p>加载 ${h(loads[capability.load_state]||capability.load_state)}${capability.load_source?' · '+h(capability.load_source):''}</p>
        ${(capability.components||[]).map(entry=>`<p data-native-component="${h(entry.state)}">${entry.kind==='skill'?'Skill':'MCP'} ${h(entry.label)} · ${h(components[entry.state]||entry.state)}${entry.event_seq?' · 事件 '+h(entry.event_seq):''}</p>`).join('')}
        ${capability.legacy_missing?.length?`<p>原回执列出的缺项 ${h(capability.legacy_missing.join('、'))}</p>`:''}
        ${capability.tool_activity.map(call=>`<p>${h(call.label)} · ${call.kind==='skill_by_name'?'原生按名称请求 Skill':'原生 MCP '+h(call.tool)}
          · ${h(outcomes[call.outcome]||tools[call.state]||call.state)}
          ${call.plugin_id?' · 工作端报告插件 '+h(call.plugin_id):''} · 事件 ${h(call.event_seq)}</p>`).join('')||'<p class="sp-note">本次无可归属到所选能力的工具调用记录。</p>'}`:''}
    </details>`;
  }
  function runtimeAttempt(run,index,title,compact=false){
    const metadata=`<p class="sp-note">${h(run.model)} · ${h(run.effort||'')} · ${h(run.tier||'')}<br>${run.owned?'本任务派发':'关联历史，查看原记录'} · 尝试 ${h(run.attempt_id)}<br>本地线程${run.local_worker_alive?'仍在运行':'已退出或不归本实例'} · ${run.known_tools_exited?'已知进程退出已核对':'退出证据不足'} · 远端 ${h(run.remote_state==='UNREPORTED'?'未单独报告':run.remote_state)}</p>`;
    const details=`${constraintsView(run)}${agentEvidence(run)}${nativeExtensionInventory(run,index)}${run.native_operations.map(o=>`<p class="sp-note">原生操作 ${h(o.operation)} · ${h(o.state)}</p>`).join('')}`;
    return `<article class="wi-run" data-runtime-attempt="${h(run.attempt_id)}"><div class="sp-row"><a href="#${run.cooperative?'control':'work'}/task/${h(run.task_id)}">${h(title||run.provider+' 执行记录')}</a><strong>${h(S.state(run.state))}</strong></div>${run.stop_requested?`<p>${run.known_tools_exited&&!run.blocked?'停止请求已记录，本次执行已保存终态。':'已请求停止，等待准确回执。'}</p>`:''}${compact?'':metadata}<div class="sp-row">${run.owned?runButton('停止本次执行','stop',index,'',!['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED','SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN'].includes(run.state))+runButton('核对本次执行','reconcile',index):''}<a href="#control/task/${h(run.task_id)}">打开完整控制</a></div>${recoveryView(run,index)}${compact?controlDisclosure('attempt:'+run.attempt_id,'运行详情与扩展',metadata+details):details}${run.pending_requests.map(q=>requestView(q,run,index)).join('')}${run.can_resume?`<form class="wi-resume" data-run-index="${index}">${S.text('接续指令','note','','required maxlength="4000"')}${run.runtime_mode==='official'?'<p class="sp-note">使用原会话、原连接和当前有效配置接续。</p>':S.select('接续方式','mode',[['native','恢复原会话'],['context','准确材料的新会话']],'native')}<button type="submit">发送接续指令</button></form>`:''}</article>`;
  }

  function needsRuntimeAction(run){
    return run.blocked||run.local_worker_alive||run.remote_state==='UNKNOWN'||run.pending_requests.length||run.native_operations.length;
  }
  function runtimeAttemptTitle(item,run){
    return cooperativeRuns(item).findLast(row=>row.task_id===run.task_id)?.input.unit.title||run.provider+' 执行';
  }
  function selectedRuntimeAttempt(item){
    const index=item.runtime.runs.findIndex(run=>run.attempt_id===v.controls?.run&&!needsRuntimeAction(run));
    if(index===-1)return '';
    const run=item.runtime.runs[index];
    return runtimeAttempt(run,index,runtimeAttemptTitle(item,run),true);
  }
  function runtime(item,compact=false){
    const r=item.runtime;
    if(!r)return '';
    const labels={IDLE:compact?'暂无在途回合':'已知执行已结束',RUNNING:'运行中',WAITING_INPUT:'等待审批或补充',
      NEEDS_RECONCILIATION:'需要核对',EXIT_UNPROVED:'退出证据不足',MANUAL:'人工接管中'};
    const held=r.manual_control?.state==='MANUAL';
    const runControl=r.runs.map((run,index)=>!compact||needsRuntimeAction(run)
      ?runtimeAttempt(run,index,compact?runtimeAttemptTitle(item,run):'',compact):'').join('');
    const otherRuns=compact?r.runs.filter(run=>!needsRuntimeAction(run)):[];
    const runPicker=otherRuns.length?S.select('选择需复核或接续的回合','team-control-attempt',
      [['','选择回合'],...otherRuns.map((run,index)=>[run.attempt_id,
        (index+1)+'. '+runtimeAttemptTitle(item,run)+' · '+(S.state(run.state)||run.state)])],v.controls.run):'';
    const takeover=held?S.button('结束人工接管','data-item-action="release"'):
      S.button('人工接管本任务',`data-item-action="takeover" ${r.can_takeover?'':'disabled'}`);
    return `<section class="${compact?'wi-control-card wi-control-runtime':'sp-card'} wi-runtime">
      <div class="wi-section-heading"><div class="wi-runtime-heading"><h2>${compact?'当前运行':'任务运行与控制'}</h2>
        ${compact?`<span class="wi-control-status" data-runtime-state="${h(r.state)}">${h(labels[r.state]||r.state)}</span>`:''}</div>
        <div class="wi-runtime-actions">${S.button('刷新运行状态','data-item-action="refresh"')}${compact?takeover:''}</div></div>
      ${compact?`<p class="wi-control-meta">最后核对 ${h(S.when(r.checked_at))}</p>`:
        `<p data-runtime-state="${h(r.state)}">${h(labels[r.state]||r.state)}</p>
        <p class="sp-note">核对时间：${h(S.when(r.checked_at))}。回合结束与任务验收分别记录。停止后的实际状态请刷新核对。</p>`}
      ${held?`<p>人工处理：${h(r.manual_control.note||'已接管')}</p>`:''}${compact?'':takeover}
      ${runControl||(!compact?'<p class="sp-empty">本任务尚无执行尝试。</p>':'')}
      ${compact?`<div class="wi-runtime-options">
        ${otherRuns.length?controlDisclosure('run-picker','复核或接续指定回合',runPicker+`<div data-control-selected-run>${selectedRuntimeAttempt(item)}</div>`):''}
        ${controlDisclosure('independent','从任务准备独立 Work',continuation(item,true))}</div>`:
        '<p class="sp-note">人工接管约束本任务及关联执行的 Workdesk 派发。结束接管后由你明确安排下一步。</p>'}
    </section>`;
  }
  function continuation(item,compact=false){
    return `<section ${compact?'':'class="sp-card"'}>${compact?'':'<h2>继续工作</h2>'}
      ${S.select('执行成员','execution_member',[['','发送时自行选择模型'],...(v.resources?.members||[]).filter(m=>m.available).map(m=>[m.id,m.name+' · '+m.model])],'')}
      <p><a href="#control/profiles">管理成员与连接</a></p>
      ${S.button('准备独立 Work',`data-item-action="prepare" ${item.runtime?.driver_protected||item.driver?.held||item.state!=='OPEN'||item.runtime?.state==='MANUAL'||item.runtime?.runs.some(r=>r.blocked)?'disabled':''}`)}
      <p class="sp-note">把本任务的目标、要求、进度及来源带入工作草稿，选择工作端后发送。</p>
      ${compact?'':(item.dispatches||[]).map(d=>`<p><a href="#${d.cooperative?'control':'work'}/task/${h(d.task_id)}">${h(d.execution_resource?.member?.name||d.provider)} 执行</a>
        · ${h(d.state)} · ${d.known_tools_exited===true?'已知工具已退出':'退出情况见运行记录'}</p>`).join('')}
    </section>`;
  }
  function busy(on){for(const button of $('#main').querySelectorAll('button')){if(on&&!button.hasAttribute('data-item-busy')){button.dataset.itemBusy=button.disabled?'1':'0';button.disabled=true;}else if(!on&&button.hasAttribute('data-item-busy')){button.disabled=button.dataset.itemBusy==='1';delete button.dataset.itemBusy;}}}
  const teamworkModes={fixed:'固定流程',leader:'负责人带队',host_experts:'主持人—专家'};
  const pdcaRounds=rounds=>rounds===1?'PDCA · 单轮':'PDCA · '+rounds+' 轮';
  function cooperationMaterials(config){
    const attachments=new Set((config?.attachments||[]).map(item=>item.file).filter(Boolean));
    const files=[...new Set([...(config?.files||[]),...attachments])];
    return files.length?`<ul class="wi-material-list">${files.map(file=>
      `<li>${h(file)}${attachments.has(file)?' <small>附件</small>':''}</li>`).join('')}</ul>`:'';
  }
  function pdcaPreparation(item,row){
    const status={PREPARING:'正在准备方案',BLOCKED:'方案待处理',NEEDS_CAPABILITY:'需要补充能力',
      NEEDS_RECONCILIATION:'准备回合待核对',PAUSED_AFTER_RESTART:'服务重启后等待核对',RELEASED:'本次准备已释放'};
    const {stages,coordinator}=row.pdca;
    const leader=row.organization==='leader',host=row.organization==='host_experts';
    const leaderPlans=leader&&!item.cooperation_conversation&&coordinator&&coordinator.member_id===stages.plan?.member_id;
    const staffing=[
      ...(leader||host?[[host?'主持人':leaderPlans?'负责人 · 计划与协调':'负责人 · 协调',coordinator]]:[]),
      ...(leaderPlans?[]:[[host&&!item.cooperation_conversation?'讨论专家':'P · 计划',stages.plan]]),
      [leader||host?'执行成员':'D · 执行',stages.do],
      [host?'成果复核':leader?'审查成员':'C · 检查',stages.check],
      [host?'定稿与改进':leader?'整合与改进':'A · 改进',stages.act]
    ];
    return `<section class="sp-page wi-team-page"><section class="wi-team-view" aria-label="准备合作任务">
      ${cooperationPath(item)}
      <header class="wi-team-heading"><div><h1>${h(item.title)}</h1><div><span>${h(teamworkModes[row.organization])}</span>
      <span>${h(pdcaRounds(row.pdca.rounds))}</span><span class="wi-state" role="status">${h(status[row.state]||row.state)}</span></div></div>
      <div>${S.button('刷新状态','data-item-action="refresh"')}</div></header>
      <div class="wi-team-pane"><section class="wi-note-card"><h2>任务目标</h2><p class="wi-note-text">${h(item.goal)}</p>
        ${cooperationMaterials(row.pdca)}</section>
        <section class="wi-note-card"><h2>阶段分工</h2><dl class="wi-pdca-stages">
          ${staffing.map(([label,member])=>`<div><dt>${h(label)}</dt><dd>${h(member?.name||'')}</dd></div>`).join('')}
        </dl></section>
        <section class="wi-note-card">${row.reason?`<p>${h(row.reason)}</p>`:''}
          <a href="#control/task/${h(row.task_id)}">查看准备回合</a>
          <details><summary>准备与恢复</summary>${fixedPreparation(item,{allowStart:false})}</details></section></div>
    </section></section>`;
  }
  const workTabs=[['controls','操作与配置'],['notes','执行纪要'],['trace','执行轨迹'],['delivery','成果与验收'],['usage','额度消耗']];
  function teamworkHref(item,tab='trace',origin){
    const route=current();
    const source=origin??(route.id===item.id?route.origin:null);
    return '#work-items/item/'+encodeURIComponent(item.id)+(tab==='trace'?'':'/'+tab)+(source?'/from-'+source:'');
  }
  const cooperationHref=id=>'#work-items/conversation/'+encodeURIComponent(id);
  const cooperationFileHref=(id,run,name)=>cooperationHref(id)+'/file/'+encodeURIComponent(run)+'/'+encodeURIComponent(name);
  const cooperationToolsHref=id=>cooperationHref(id)+'/tools';
  const taskEntry=item=>item.cooperation_conversation?cooperationHref(item.id):teamworkHref(item);
  const chatLink=item=>item.cooperation_conversation
    ?`<a class="wi-work-return" href="${cooperationHref(item.id)}">返回合作聊天</a>`:'';
  const cooperationStageNames={plan:'P · 计划',do:'D · 执行',check:'C · 检查',act:'A · 改进'};
  const cooperationBriefFields={goal:'任务目标',deliverables:'交付物',constraints:'约束',acceptance:'验收条件'};
  const cooperationDrafts=new Map();
  let cooperationView=null;
  const activeCooperation=view=>!!view&&view===cooperationView&&current().conversation===view.id;
  const discussionRole=data=>({leader:'负责人',host_experts:'主持人'})[data.mode]||'Workdesk';
  const cooperationUser=prefs=>prefs.userNickname||'User';
  const cooperationAI=(prefs,role,model)=>prefs.assistantNickname||[role,model].filter(Boolean).join(' · ');

  function cooperationDeliveries(data){
    const history=data?.handoff_history||[];
    const current=data?.handoff;
    const shown=current&&history.some(handoff=>handoff.after_round===(current.after_round??data.rounds));
    return !current||shown?history:[...history,current];
  }
  function cooperationDeliveryUnits(data){
    return Array.from(new Map(cooperationDeliveries(data).flatMap(handoff=>
      handoff.units.map(unit=>[unit.run_id,unit]))).values());
  }
  function cooperationHandoff(data,prefs,handoff){
    const rows=handoff.units.flatMap(unit=>Object.keys(unit.manifest).map(name=>({unit,name})));
    const summaries=handoff.units.filter(unit=>unit.summary);
    const destination=teamworkHref(data,'delivery');
    const speaker=cooperationAI(prefs,discussionRole(data),data.discussion_model);
    const past=handoff!==data.handoff;
    const round=past&&handoff.after_round?`第 ${h(handoff.after_round)} 轮`:'';
    return `<article class="wi-chat-message wi-chat-handoff" data-cooperation-handoff data-cooperation-message-key="handoff:${h(handoff.id||handoff.after_round||data.rounds)}" data-reading-reply="true">
      <h2>${h(speaker)} · ${round}交付说明</h2>
      <div class="wi-chat-bubble">
        <p><strong>${round?`已保存${round}交付。`:'已完成本次合作。'}${rows.length?`为你保存了 ${rows.length} 份最终文件。`:''}</strong></p>
        ${summaries.map(unit=>`<div class="markdown wi-handoff-summary">${summaries.length>1?`<h3>${h(unit.title)}</h3>`:''}${markdown(unit.summary)}</div>`).join('')}
        ${rows.length?`<h3>交付文件${handoff.project_name?` <small>· ${h(handoff.project_name)} 项目</small>`:''}</h3>
          <ul class="wi-handoff-files">${rows.map(({unit,name})=>`<li>
            <div><a href="${cooperationFileHref(data.id,unit.run_id,name)}">${h(unit.file_labels[name]||name.split('/').at(-1))}</a>
              <small>${h(name)}</small></div>${data.project_id?locateDeliveryButton(unit,name):''}</li>`).join('')}</ul>`:''}
        <a class="wi-handoff-more" href="${destination}">查看全部成果</a>
      </div></article>`;
  }

  function cooperationTranscript(data,prefs){
    const messages=(data.messages||[]).filter(message=>['user','host'].includes(message.role)).map((message,index)=>{
      const user=message.role==='user';
      return `<article class="wi-chat-message" data-chat-role="${user?'user':'host'}" data-cooperation-message-key="discussion:${h(message.id||index)}" data-reading-reply="${!user&&!!message.text?.trim()}">
        <h2>${h(user?cooperationUser(prefs):cooperationAI(prefs,discussionRole(data),message.model||data.discussion_model))}</h2>
        <div class="wi-chat-bubble ${user?'':'markdown'}">
          ${user?`<p>${h(message.text)}</p>`:markdown(message.text)}</div></article>`;
    }).join('');
    const brief=data.brief&&data.state!=='DISCUSSING'?`<section class="wi-chat-card wi-chat-brief" aria-label="已确认的任务说明">
      <h2>已确认的任务说明</h2><dl>${Object.entries(cooperationBriefFields).filter(([name])=>data.brief[name]).map(([name,label])=>
        `<div><dt>${label}</dt><dd>${h(data.brief[name])}</dd></div>`).join('')}</dl></section>`:'';
    const timeline=new Map(),notesWithoutRound=[];
    function append(round,body){
      if(!timeline.has(round))timeline.set(round,[]);
      timeline.get(round).push(body);
    }
    for(const stage of (data.stages||[]).filter(stage=>Object.hasOwn(cooperationStageNames,stage.stage))){
      const body=stage.text||'本阶段成果已保存，可打开任务详情查看。';
      const target=teamworkHref(data),task=encodeURIComponent(stage.task_id);
      const member=stage.model||({codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen'})[stage.provider]||'';
      append(stage.round,`<article class="wi-chat-card wi-chat-stage" data-cooperation-stage="${h(stage.id)}" data-cooperation-message-key="stage:${h(stage.id)}" data-reading-reply="true">
        <header><div><p>第 ${h(stage.round)} 轮</p><h2>${cooperationStageNames[stage.stage]}</h2></div>
        ${member?`<span title="${h(member)}">${h(cooperationAI(prefs,'',member))}</span>`:''}</header>
        <div class="markdown">${markdown(body.length>1000?body.slice(0,670)+'…':body)}</div>
        ${body.length>1000?`<details><summary>查看完整结果</summary><div class="markdown">${markdown(body)}</div></details>`:''}
        <footer><a href="${target+'/trace/'+task}">执行轨迹</a><a href="${target+'/thoughts/'+task}">公开思考</a></footer></article>`);
    }
    for(const handoff of cooperationDeliveries(data))
      append(handoff.after_round??data.rounds,cooperationHandoff(data,prefs,handoff));
    const discussion=messages&&data.mode!=='fixed'?'<h2 class="wi-chat-section-label">第 0 轮 · 需求讨论</h2>':'';
    for(const note of (data.resume_notes||[]).filter(note=>note.text)){
      const body=`<article class="wi-chat-message" data-chat-role="user" data-cooperation-note="${h(note.id)}" data-cooperation-message-key="note:${h(note.id)}">
        <h2>${h(cooperationUser(prefs))} · ${h(note.title||'续跑说明')}</h2><div class="wi-chat-bubble"><p>${h(note.text)}</p></div></article>`;
      if(note.after_round==null)notesWithoutRound.push(body);
      else append(note.after_round,body);
    }
    const work=Array.from(timeline.keys()).sort((a,b)=>a-b).map(round=>timeline.get(round).join('')).join('');
    return discussion+messages+brief+work+notesWithoutRound.join('');
  }

  function cooperationDraft(view){
    if(!cooperationDrafts.has(view.id))cooperationDrafts.set(view.id,{text:'',brief:null,note:''});
    return cooperationDrafts.get(view.id);
  }
  const cooperationComposers={DISCUSSING:'message',RUNNING:'intervene',PAUSED:'resume'};
  const cooperationFormKinds={
    'wi-cooperation-message':'message','wi-cooperation-intervene':'intervene',
    'wi-cooperation-resume':'resume','wi-cooperation-confirm':'confirm',
    'wi-cooperation-followup':'followup','wi-cooperation-followup-confirm':'followup-confirm'
  };
  function updateCooperationActions(view,form){
    const data=view.data,draft=cooperationDraft(view),kind=cooperationFormKinds[form.id];
    const disabled=view.submitting;
    if(kind==='message')form.querySelector('button').disabled=disabled||!data.can_send||!draft.text.trim();
    if(kind==='intervene'){
      form.querySelector('[value=adjust]').disabled=disabled||!draft.note.trim();
      form.querySelector('[value=interrupt]').disabled=disabled;
    }
    if(kind==='resume')form.querySelector('[value=resume]').disabled=disabled||!data.recovery?.can_resume;
    const preview=form.querySelector('[value=preview]');
    if(preview){
      const available=kind==='followup'?data.can_follow_up:data.can_adjust_budget;
      preview.hidden=!available;
      preview.disabled=disabled||!available||!draft.note.trim();
    }
  }
  function paintCooperationComposer(view,composer){
    const data=view.data,draft=cooperationDraft(view);
    const kind=cooperationComposers[data.state]||(data.can_follow_up?'followup':'');
    if(composer.dataset.kind!==kind){
      const previous=composer.querySelector('textarea[name=note]');
      const caret=previous&&document.activeElement===previous?[previous.selectionStart,previous.selectionEnd]:null;
      if(kind==='message'){
        composer.innerHTML=`<form id="wi-cooperation-message" class="wi-chat-composer">
          <textarea name="message" rows="2" required maxlength="16000" aria-label="需求讨论消息" placeholder="补充你的想法或要求…">${h(draft.text)}</textarea>
          <button type="submit" aria-label="发送讨论消息">${icon('up')}</button></form>`;
      }else if(kind){
        const paused=kind==='resume';
        const followup=kind==='followup';
        const title=followup?'继续这项合作':paused?'续跑说明':'运行调整说明';
        const placeholder=followup?'告诉我下一轮想补充、修改或核对什么…':paused?'可补充需要重做或核对的事项…':'可说明下一步如何调整…';
        composer.innerHTML=`<form id="wi-cooperation-${kind}" class="wi-chat-intervention" aria-label="${title}">
          <div class="wi-chat-intervention-heading">
            <label for="wi-cooperation-note">${title}</label>
            <span data-cooperation-recovery-target hidden></span>
          </div>
          <textarea id="wi-cooperation-note" name="note" rows="2" maxlength="4000" aria-describedby="wi-cooperation-action-help"
            placeholder="${placeholder}">${h(draft.note)}</textarea>
          <p id="wi-cooperation-action-help" class="wi-chat-intervention-help"></p>
          <p data-cooperation-action-error class="wi-chat-error" role="alert" hidden></p>
          <div class="wi-chat-intervention-actions">
            <button type="submit" name="action" value="preview" class="${followup?'wi-chat-action-primary':'wi-chat-action-secondary'}"
              ${followup?'':'hidden'}>${followup?'查看续作安排':'调整派发设置'}</button>
            ${followup?'':paused?'<button type="submit" name="action" value="resume" class="wi-chat-action-primary">从中断处继续</button>':
              '<button type="submit" name="action" value="interrupt" class="wi-chat-action-secondary">打断并暂停</button>'+
              '<button type="submit" name="action" value="adjust" class="wi-chat-action-primary">调整下一步</button>'}
          </div>
        </form>`;
        if(caret){
          const replacement=composer.querySelector('textarea');
          replacement.focus({preventScroll:true});
          replacement.setSelectionRange(...caret);
        }
      }else composer.innerHTML='';
      composer.dataset.kind=kind;
    }
    const form=composer.querySelector('form');
    if(!form)return;
    updateCooperationActions(view,form);
    if(kind==='message')return;
    const target=form.querySelector('[data-cooperation-recovery-target]');
    const recovery=data.recovery;
    target.textContent=kind==='resume'?recovery?.title||'':'';
    target.hidden=!target.textContent;
    form.querySelector('#wi-cooperation-action-help').textContent=kind==='followup'
      ?'先查看续作范围和派发安排，确认后再开始。'
      :kind==='intervene'?('调整用于下一次派发。打断会暂停派发，并向当前回合请求停止。'+
        (data.can_adjust_budget?' 要增加轮次或修改上限，请选择「调整派发设置」。':''))
      :recovery?.message||(recovery?.can_resume
        ?'从中断处接续，继续使用已保存的成果。'
        :'请在任务详情核对暂停原因，续跑说明会保留在这里。');
    const error=form.querySelector('[data-cooperation-action-error]');
    if(error.textContent!==view.actionError)error.textContent=view.actionError||'';
    error.hidden=!view.actionError;
  }
  function paintCooperationProposal(view,host){
    const proposal=view.proposal;
    if(!proposal){
      if(host.firstElementChild)host.innerHTML='';
      return;
    }
    const continuing=proposal.kind==='continue',data=view.data;
    if(host.dataset.proposal!==proposal.id||!host.firstElementChild){
      const change=(before,after)=>{
        if(after===undefined||after===null)return '';
        return before===after||before==null?h(after):h(before)+' → '+h(after);
      };
      const rows=[
        ['PDCA 轮数',change(proposal.rounds_before,proposal.rounds_after)],
        ['工作回合上限',change(proposal.limit_before,proposal.limit_after)],
        ['已派发',proposal.used==null?'':h(proposal.used)+' 次'],
        ['新增派发截止',proposal.deadline?h(S.when(proposal.deadline)):'']
      ].filter(([,value])=>value);
      host.innerHTML=`<form id="wi-cooperation-followup-confirm" class="wi-chat-card wi-chat-brief wi-chat-followup"
          aria-labelledby="wi-followup-heading">
        <h2 id="wi-followup-heading" tabindex="-1">${continuing?'确认续作安排':'确认派发设置'}</h2>
        ${continuing&&proposal.feedback?`<p class="wi-chat-followup-goal">${h(proposal.feedback)}</p>`:''}
        <dl>${rows.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>
        ${proposal.renew_hours?`<p class="wi-chat-intervention-help">原派发已到期，本次安排预留 ${h(proposal.renew_hours)} 小时。</p>`:''}
        ${proposal.queue?'<p class="wi-chat-intervention-help">已安排的轮次完成后，再开始这次续作。</p>':''}
        ${(proposal.requires_full_confirmation??(continuing&&data.access==='full'))?`<label class="wi-chat-full-access"><input name="confirm_full" type="checkbox" required>
          <span>允许本次续作使用完全访问权限。所选工作端可在本机账号权限范围内读写文件、运行命令和使用网络。</span></label>`:''}
        <p class="wi-chat-intervention-help" data-followup-stale hidden>输入或任务已有更新，请重新查看安排后再确认。</p>
        <div class="wi-chat-intervention-actions"><button type="button" data-cooperation-followup-cancel>取消</button>
          <button type="submit" class="wi-chat-action-primary">${proposal.queue?'确认续作安排':continuing?'确认并开始续作':'应用派发设置'}</button></div>
      </form>`;
      host.dataset.proposal=proposal.id;
    }
    const edited=cooperationDraft(view).note.trim()!==view.proposalText||data.revision!==proposal.revision;
    host.querySelector('[data-followup-stale]').hidden=!edited;
    host.querySelector('[type=submit]').disabled=view.submitting||edited;
    host.querySelector('[data-cooperation-followup-cancel]').disabled=view.submitting;
  }
  function cooperationReadingAnchor(viewport){
    const top=viewport.getBoundingClientRect().top;
    const message=Array.from(viewport.querySelectorAll('[data-cooperation-message-key]')).find(node=>node.getBoundingClientRect().bottom>top+3);
    return {top:viewport.scrollTop,key:message?.dataset.cooperationMessageKey,offset:message?message.getBoundingClientRect().top-top:0};
  }
  function restoreCooperationReading(viewport,anchor){
    const message=anchor?.key&&Array.from(viewport.querySelectorAll('[data-cooperation-message-key]')).find(node=>node.dataset.cooperationMessageKey===anchor.key);
    if(message){
      viewport.scrollTop+=message.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset;
      return Math.abs(message.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset)<3;
    }
    if(anchor)viewport.scrollTop=anchor.top;
    return true;
  }
  function cooperationReadingControl(view,page){
    const reading=view.reading,viewport=page.querySelector('.wi-chat-scroll');
    if(!reading||!viewport)return;
    const atEnd=viewport.scrollHeight-viewport.clientHeight-viewport.scrollTop<=2;
    if(atEnd&&reading.follow)reading.unread.clear();
    const host=page.querySelector('[data-cooperation-reading]'),button=host.querySelector('button');
    host.dataset.follow=String(reading.follow);host.dataset.relayout=String(reading.relayout);
    const count=reading.unread.size,label=count?`有 ${count} 条新回复，回到最新`:'回到最新';
    host.hidden=atEnd;host.dataset.newReplies=String(count);
    if(button.textContent!==label)button.textContent=label;
    button.setAttribute('aria-label',label);
    const announcement=host.querySelector('[role=status]'),notice=count?`有 ${count} 条新回复`:'';
    if(announcement.textContent!==notice)announcement.textContent=notice;
  }
  function mountCooperationReading(view,page){
    const viewport=page.querySelector('.wi-chat-scroll');
    const reading=view.reading={initialized:false,follow:true,seen:new Set(),unread:new Set(),lastTop:0,lastHeight:0,anchor:null,relayout:false,observer:null,frame:null};
    viewport.addEventListener('scroll',()=>{
      if(!activeCooperation(view))return;
      const delta=viewport.scrollHeight-viewport.clientHeight-viewport.scrollTop;
      if(!reading.relayout){
        if(delta<=2&&viewport.scrollHeight===reading.lastHeight&&viewport.scrollTop>reading.lastTop+1)reading.follow=true;
        else if(viewport.scrollTop<reading.lastTop-1&&viewport.scrollHeight>=reading.lastHeight)reading.follow=false;
      }
      reading.lastTop=viewport.scrollTop;reading.lastHeight=viewport.scrollHeight;
      if(!reading.relayout)reading.anchor=cooperationReadingAnchor(viewport);
      cooperationReadingControl(view,page);
    },{passive:true});
    viewport.addEventListener('wheel',event=>{reading.relayout=false;if(event.deltaY<0)reading.follow=false;},{passive:true});
    viewport.addEventListener('pointerdown',()=>{reading.relayout=false;});
    viewport.addEventListener('keydown',event=>{if(['PageUp','Home','ArrowUp'].includes(event.key)&&!event.target.matches('input,textarea,select')){reading.relayout=false;reading.follow=false;}});
    page.querySelector('[data-cooperation-latest]').addEventListener('click',()=>{
      if(!activeCooperation(view))return;
      reading.follow=true;reading.relayout=false;reading.unread.clear();viewport.scrollTop=viewport.scrollHeight;
      cooperationReadingControl(view,page);
    });
    if(typeof ResizeObserver==='function'){
      reading.observer=new ResizeObserver(()=>{
        if(reading.frame!==null||!activeCooperation(view)||!reading.initialized)return;
        reading.frame=requestAnimationFrame(()=>{
          reading.frame=null;if(!activeCooperation(view)||!viewport.isConnected)return;
          if(reading.follow){viewport.scrollTop=viewport.scrollHeight;reading.relayout=false;}
          else{
            const restored=restoreCooperationReading(viewport,reading.anchor);
            const ready=Array.from(viewport.querySelectorAll('workdesk-markdown')).every(node=>node.querySelector('[data-safe-markdown]'));
            if(restored&&ready)reading.relayout=false;
          }
          reading.lastTop=viewport.scrollTop;reading.lastHeight=viewport.scrollHeight;
          if(!reading.relayout)reading.anchor=cooperationReadingAnchor(viewport);
          cooperationReadingControl(view,page);
        });
      });
      for(const element of [viewport,...viewport.querySelectorAll('[data-cooperation-transcript],[data-cooperation-brief],[data-cooperation-proposal]')])reading.observer.observe(element);
    }
  }
  function stopCooperationReading(view){
    view?.reading?.observer?.disconnect();
    if(view?.reading?.frame!=null)cancelAnimationFrame(view.reading.frame);
  }
  function updateCooperationReading(view,page,anchor){
    const reading=view.reading,viewport=page.querySelector('.wi-chat-scroll');
    const replies=new Set(Array.from(viewport.querySelectorAll('[data-reading-reply="true"]'),node=>node.dataset.cooperationMessageKey));
    if(reading.initialized&&!reading.follow){for(const id of replies)if(!reading.seen.has(id))reading.unread.add(id);}
    else reading.unread.clear();
    reading.seen=replies;reading.initialized=true;
    reading.lastTop=viewport.scrollTop;reading.lastHeight=viewport.scrollHeight;
    reading.anchor=!reading.follow&&anchor?anchor:cooperationReadingAnchor(viewport);
    cooperationReadingControl(view,page);
  }
  function paintCooperation(view){
    const main=$('#main'),data=view.data;
    if(!main||!activeCooperation(view))return;
    let page=main.querySelector('.wi-cooperation-surface');
    if(!page){
      main.innerHTML=`<section class="wi-cooperation-surface" aria-label="合作聊天">
        <header class="wi-conversation-bar">
          <div class="wi-conversation-name">${icon('chat')}<h1 data-cooperation-title>正在打开聊天…</h1></div>
          <nav class="wi-conversation-actions" aria-label="当前聊天操作">
            <div class="wi-cooperation-toolbar" data-cooperation-toolbar></div>
            <div data-cooperation-summary-host></div>
            <button type="button" class="wi-chat-toggle" data-cooperation-tools-toggle
              aria-controls="wi-cooperation-side" aria-expanded="false" aria-keyshortcuts="Control+Shift+B"
              aria-label="打开右侧边栏" title="显示标签页（Ctrl+Shift+B）">${icon('panel')}</button>
          </nav>
        </header>
        <div class="wi-cooperation-layout">
          <div class="wi-chat-lane"><div class="wi-chat-grid">
            <aside class="wi-chat-task-card" aria-label="任务状态" hidden>
              <p data-cooperation-mode></p><p data-cooperation-rounds></p>
              <p data-cooperation-auth hidden></p><p data-cooperation-deadline hidden></p>
              <a class="wi-chat-detail" href="${teamworkHref(view,'controls')}">任务详情</a>
            </aside>
            <div class="sp-page wi-cooperation-chat">
              <div class="wi-chat-scroll"><div data-cooperation-transcript></div><div data-cooperation-brief></div>
                <div data-cooperation-proposal></div>
                <p class="wi-chat-status" data-cooperation-status role="status" hidden></p>
                <p class="wi-chat-error" data-cooperation-problem role="alert" hidden></p><p data-cooperation-discussion-run hidden></p>
                <div class="wd-reading-position" data-cooperation-reading hidden><span class="wd-reading-announcement" role="status" aria-live="polite"></span><button type="button" class="wd-reading-latest" data-cooperation-latest>回到最新</button></div>
              </div><footer data-cooperation-composer></footer>
            </div>
          </div></div>
          <aside id="wi-cooperation-side" class="wi-cooperation-preview" data-cooperation-file-preview aria-label="文件预览" hidden></aside>
        </div>
      </section>`;
      page=main.querySelector('.wi-cooperation-surface');
      mountCooperationReading(view,page);
      window.WorkdeskCooperationToolbar.mount(page.querySelector('[data-cooperation-toolbar]'),{
        getData:()=>view.data,isCurrent:()=>activeCooperation(view),refresh:()=>refreshCooperation(view)});
    }
    const problem=page.querySelector('[data-cooperation-problem]');
    const error=view.error||data?.problem||'';
    if(problem.textContent!==error)problem.textContent=error;
    problem.hidden=!error;
    if(!data){
      page.querySelector('[data-cooperation-title]').textContent=error?'暂时无法打开聊天':'正在打开聊天…';
      return;
    }
    const title=page.querySelector('[data-cooperation-title]');
    title.textContent=title.title=data.title;
    window.WorkdeskCooperationToolbar.update(page.querySelector('[data-cooperation-toolbar]'));
    renderCooperationSummary(view,page.querySelector('[data-cooperation-summary-host]'));
    page.querySelector('.wi-chat-task-card').hidden=false;
    page.querySelector('[data-cooperation-mode]').textContent=teamworkModes[data.mode]||'合作任务';
    page.querySelector('[data-cooperation-rounds]').textContent=data.rounds===1?'单轮':'共 '+data.rounds+' 轮';
    const authorization=page.querySelector('[data-cooperation-auth]'),deadline=page.querySelector('[data-cooperation-deadline]');
    const auth=data.authorization;
    authorization.hidden=!auth;
    deadline.hidden=!auth;
    if(auth){
      authorization.textContent=auth.deadline
        ?`派发 ${auth.used??0} / ${auth.limit} 次`
        :`派发上限 ${auth.limit} 次`;
      deadline.textContent=auth.deadline?`截止 ${S.when(auth.deadline)}`:`开始后 ${auth.hours} 小时`;
    }
    const viewport=page.querySelector('.wi-chat-scroll');
    const nearEnd=view.reading.follow;
    const readingAnchor=nearEnd?null:view.reading.relayout&&view.reading.anchor?view.reading.anchor:cooperationReadingAnchor(viewport);
    const prefs=window.WorkdeskSettingsPreferences?.get()||{};
    const signature=JSON.stringify([data.messages,data.discussion_model,data.state==='DISCUSSING'?null:data.brief,
      data.stages,data.mode,data.resume_notes,data.handoff,data.handoff_history,prefs.userNickname,prefs.assistantNickname]);
    if(signature!==view.transcript){
      const transcript=page.querySelector('[data-cooperation-transcript]');
      const expanded=new Set(Array.from(transcript.querySelectorAll('[data-cooperation-stage] details[open]'),
        details=>details.closest('[data-cooperation-stage]').dataset.cooperationStage));
      if(!view.reading.follow)view.reading.relayout=true;
      transcript.innerHTML=cooperationTranscript(data,prefs);
      for(const card of transcript.querySelectorAll('[data-cooperation-stage]')){
        const details=card.querySelector('details');
        if(details&&expanded.has(card.dataset.cooperationStage))details.open=true;
      }
      view.transcript=signature;
    }
    const discussing=data.state==='DISCUSSING';
    const draft=cooperationDraft(view);
    const brief=page.querySelector('[data-cooperation-brief]');
    if(discussing&&data.brief&&data.can_confirm){
      const source=JSON.stringify(data.brief);
      if(!draft.brief||(!draft.brief.edited&&draft.brief.source!==source))
        draft.brief={source,values:{...data.brief},edited:false};
      if(!brief.firstElementChild||view.briefSource!==draft.brief.source){
        brief.innerHTML=`<form id="wi-cooperation-confirm" class="wi-chat-card wi-chat-brief">
          <h2>确认任务说明</h2>
          <div class="wi-chat-brief-fields">${Object.entries(cooperationBriefFields).map(([name,label])=>
            S.text(label,name,draft.brief.values[name]||'',`${name==='goal'?'required ':''}maxlength="4000"`)).join('')}</div>
          ${data.access==='full'&&!data.plan_only?`<label class="wi-chat-full-access"><input name="confirm_full" type="checkbox" required>
            <span>允许本次正式执行使用完全访问权限。所选工作端可在本机账号权限范围内读写文件、运行命令和使用网络。</span></label>`:''}
          <button type="submit">${data.plan_only?'确认任务说明并生成方案':'确认任务说明并开始 PDCA'}</button></form>`;
        view.briefSource=draft.brief.source;
      }
      brief.querySelector('button').disabled=view.submitting||!!data.pending;
    }else if(brief.firstElementChild)brief.innerHTML='';
    paintCooperationProposal(view,page.querySelector('[data-cooperation-proposal]'));
    paintCooperationComposer(view,page.querySelector('[data-cooperation-composer]'));
    const status=page.querySelector('[data-cooperation-status]');
    const queued=data.followup_pending;
    const label=(queued?.problem|| (queued&&data.state==='RUNNING'?'已保存续作安排，当前计划完成后接续。':'')||
      (data.pending?'正在整理讨论内容…':{
      RUNNING:'PDCA 进行中，阶段完成后会在这里显示结果。',
      PLAN_READY:'方案已保存，可在任务详情查看。',
      COMPLETED:data.handoff?'':'本次合作已完成。',
      PAUSED:'工作已暂停。'
    }[data.state]))||'';
    if(status.textContent!==label)status.textContent=label;
    status.hidden=!label;
    const discussionRun=page.querySelector('[data-cooperation-discussion-run]');
    const discussionTask=discussing?(data.pending||data.problem?data.discussion_task_id:null):
      data.attention_task_id||data.recovery?.task_id;
    const linkText=discussing?'查看讨论回合与审批':data.attention_task_id?'打开工作端审批与输入':'查看中断回合';
    const linkKey=discussionTask?discussionTask+':'+linkText:'';
    if(discussionRun.dataset.task!==linkKey){
      discussionRun.dataset.task=linkKey;
      discussionRun.innerHTML=discussionTask?`<a href="${runHref({task_id:discussionTask})}">${linkText}</a>`:'';
      discussionRun.hidden=!discussionTask;
    }
    if(nearEnd||!view.reading.initialized)viewport.scrollTop=viewport.scrollHeight;
    else restoreCooperationReading(viewport,readingAnchor);
    updateCooperationReading(view,page,readingAnchor);
  }

  async function refreshCooperation(view){
    if(!activeCooperation(view))return;
    const request=++view.request;
    clearTimeout(view.timer);
    if(!document.hidden){
      try{
        const data=await S.request('cooperation-conversation?'+new URLSearchParams({id:view.id}));
        if(!activeCooperation(view)||request!==view.request)return;
        if(data.id!==view.id)throw Error('合作聊天与当前任务不匹配。');
        view.data=data;
        if(view.error&&view.refreshError)view.error='';
        view.refreshError=false;
      }catch(error){
        if(!activeCooperation(view)||request!==view.request)return;
        if(!view.error||view.refreshError){view.error=error.message;view.refreshError=true;}
      }
      paintCooperation(view);
      void setCooperationSide(view,current());
    }
    if(activeCooperation(view)&&request===view.request&&(view.data?.state!=='COMPLETED'||view.data?.followup_pending))
      view.timer=setTimeout(()=>refreshCooperation(view),2800);
  }
  function openCooperation(route){
    const id=route.conversation;
    const view=cooperationView={id,data:null,error:'',actionError:'',request:0,submitting:false,proposal:null,proposalText:'',
      transcript:'',briefSource:'',refreshError:false,preview:null,tools:false,expanded:false,toolsSignature:null};
    v.data=null;v.loadingControls=null;v.error='';
    $('#main').innerHTML='';
    paintCooperation(view);
    void setCooperationSide(view,route);
    return refreshCooperation(view);
  }
  function renderCooperationTools(view,panel){
    const data=view.data;
    const fingerprint=JSON.stringify([data?.project_id,cooperationDeliveries(data),view.expanded]);
    if(view.toolsSignature===fingerprint&&view.renderedTool===view.requestedTool)return;
    const filter=panel.querySelector('[data-cooperation-file-search]')?.value||'';
    const files=cooperationDeliveryUnits(data).flatMap(unit=>Object.keys(unit.manifest).map(name=>({unit,name})));
    const extra=`
        <section class="wi-tools-files"><h3>交付文件</h3>
          ${files.length?`<input type="search" data-cooperation-file-search value="${h(filter)}" placeholder="搜索交付文件" aria-label="搜索交付文件">
            <ul>${files.map(({unit,name})=>`<li><a href="${cooperationFileHref(view.id,unit.run_id,name)}">
              ${icon('file')}<span>${h(unit.file_labels[name]||name.split('/').at(-1))}<small>${h(name)}</small></span>
            </a></li>`).join('')}</ul><p data-cooperation-no-match hidden>没有匹配的文件。</p>`
            :`<p>${data?'本任务暂无交付文件。':'正在读取交付文件…'}</p>`}
        </section>`;
    window.WorkdeskReact.quickWorkspace(panel,{stateKey:`cooperation:${view.id}:${data?.project_id||''}`,projectId:data?.project_id,extra,expanded:view.expanded,
      requestedTool:view.requestedTool,onToggleExpanded:()=>toggleCooperationExpanded(view),
      onClose:()=>{if(activeCooperation(view))location.hash=cooperationHref(view.id);}});
    view.toolsSignature=fingerprint;
    view.renderedTool=view.requestedTool;
    if(filter)filterCooperationFiles(panel,filter);
  }
  function renderCooperationSummary(view,host){
    const data=view.data;
    const fingerprint=JSON.stringify([data.title,cooperationDeliveries(data)]);
    if(view.summarySignature===fingerprint)return;
    view.summarySignature=fingerprint;
    const outputs=cooperationDeliveryUnits(data).flatMap(unit=>Object.keys(unit.manifest).map(name=>({
      id:unit.run_id+':'+name,title:unit.file_labels[name]||name.split('/').at(-1),detail:name,
      href:cooperationFileHref(view.id,unit.run_id,name)
    })));
    window.WorkdeskReact.conversationSummary(host,{title:data.title,outputs,onOpenTab:(tool,entry)=>{
      if(!activeCooperation(view))return;
      view.requestedTool={tool,file:entry?.file};
      if(view.tools)renderCooperationSide(view);
      else location.hash=cooperationToolsHref(view.id);
    }});
  }
  function toggleCooperationExpanded(view){
    if(!activeCooperation(view)||!(view.tools||view.preview))return;
    view.expanded=!view.expanded;
    renderCooperationSide(view);
  }
  function filterCooperationFiles(panel,query){
    const value=query.trim().toLocaleLowerCase();
    let found=false;
    for(const entry of panel.querySelectorAll('.wi-tools-files li')){
      entry.hidden=!entry.textContent.toLocaleLowerCase().includes(value);
      if(!entry.hidden)found=true;
    }
    const empty=panel.querySelector('[data-cooperation-no-match]');
    if(empty)empty.hidden=found;
  }
  function renderCooperationSide(view){
    const panel=$('#main [data-cooperation-file-preview]');
    if(!panel)return;
    const viewport=$('#main .wi-chat-scroll');
    const nearEnd=view.reading?.follow;
    const readingAnchor=nearEnd?null:view.reading?.relayout&&view.reading.anchor?view.reading.anchor:cooperationReadingAnchor(viewport);
    const toggle=$('#main [data-cooperation-tools-toggle]');
    panel.hidden=!view.preview&&!view.tools;
    panel.parentElement.dataset.workspaceExpanded=String(view.expanded);
    panel.parentElement.querySelector('.wi-chat-lane').inert=view.expanded;
    panel.setAttribute('aria-label',view.tools?'右侧快捷工作区':'文件预览');
    panel.dataset.view=view.tools?'tools':'preview';
    const toggleName=panel.hidden?'打开右侧边栏':'关闭右侧边栏';
    toggle.setAttribute('aria-expanded',String(!panel.hidden));
    toggle.setAttribute('aria-label',toggleName);
    toggle.title=panel.hidden?'显示标签页（Ctrl+Shift+B）':'收起标签页（Ctrl+Shift+B）';
    if(view.preview){
      const {target,receipt,error}=view.preview;
      const file=receipt?.data;
      const pdfSrc=file?.kind==='pdf'?'api/fixed-artifact-file-preview?'+
        new URLSearchParams({...receipt.query,sha256:file.sha256}):undefined;
      window.WorkdeskReact.workItemFilePreview(panel,{name:target.name,run:target.run,file,pdfSrc,error,
        canLocate:!!view.data?.project_id,expanded:view.expanded,onToggleExpanded:()=>toggleCooperationExpanded(view)});
    }else if(view.tools)renderCooperationTools(view,panel);
    else{
      window.WorkdeskReact.release(panel);
    }
    if(nearEnd)viewport.scrollTop=viewport.scrollHeight;
    else restoreCooperationReading(viewport,readingAnchor);
    cooperationReadingControl(view,$('#main .wi-cooperation-surface'));
  }
  async function setCooperationSide(view,route){
    if(!activeCooperation(view))return;
    const target=route.cooperationFile,previous=view.preview,wasTools=view.tools;
    view.tools=route.cooperationTools;
    if(!view.tools)view.toolsSignature=null;
    if(!target){
      view.preview=null;
      if(!view.tools){view.expanded=false;view.requestedTool=undefined;}
      if(previous||wasTools!==view.tools||view.tools)renderCooperationSide(view);
      if(!view.tools&&(previous||wasTools)){
        const href=previous&&cooperationFileHref(view.id,previous.target.run,previous.target.name);
        const anchor=href&&Array.from($('#main').querySelectorAll('[data-cooperation-handoff] a'))
          .find(link=>link.getAttribute('href')===href);
        (anchor||$('#main [data-cooperation-tools-toggle]'))?.focus({preventScroll:true});
      }
      return;
    }
    if(!previous||previous.target.run!==target.run||previous.target.name!==target.name){
      view.preview={target};
      renderCooperationSide(view);
    }
    const preview=view.preview;
    if(!view.data||preview.started)return;
    preview.started=true;
    try{
      const unit=cooperationDeliveryUnits(view.data).find(entry=>entry.run_id===target.run);
      preview.receipt=await readDeliveryPreview(view.data,unit,target.name);
    }catch(error){preview.error=error.message;}
    const selected=current().cooperationFile;
    if(activeCooperation(view)&&view.preview===preview&&selected?.run===target.run&&selected.name===target.name)
      renderCooperationSide(view);
  }
  document.addEventListener('click',event=>{
    if(!activeCooperation(cooperationView)||!$('#main').contains(event.target))return;
    const view=cooperationView;
    if(event.target.closest('[data-cooperation-tools-toggle]')){
      location.hash=view.tools||view.preview?cooperationHref(view.id):cooperationToolsHref(view.id);
    }else if($('#main [data-cooperation-file-preview]')?.contains(event.target)&&event.target.closest('[data-cooperation-preview-close]')){
      location.hash=cooperationHref(view.id);
    }
  });
  document.addEventListener('keydown',event=>{
    const view=cooperationView;
    if(event.defaultPrevented||!activeCooperation(view)||
        document.querySelector('dialog[open],[data-cooperation-toolbar] :popover-open'))return;
    const open=view.preview||view.tools;
    if(event.key==='Escape'&&open&&!$('#main [data-conversation-summary-panel]')){
      event.preventDefault();
      if(view.expanded)toggleCooperationExpanded(view);
      else location.hash=cooperationHref(view.id);
    }else if(event.ctrlKey&&event.shiftKey&&!event.altKey){
      const key=event.key.toLowerCase();
      if(key==='b'){
        event.preventDefault();
        location.hash=open?cooperationHref(view.id):cooperationToolsHref(view.id);
      }else if(key==='f'&&open){
        event.preventDefault();
        toggleCooperationExpanded(view);
      }
    }
  });
  document.addEventListener('input',event=>{
    const view=cooperationView,input=event.target,form=input.form;
    if(!activeCooperation(view))return;
    if(input.matches('[data-cooperation-file-search]'))
      filterCooperationFiles(input.closest('[data-cooperation-file-preview]'),input.value);
    if(!form)return;
    const draft=cooperationDraft(view);
    const kind=cooperationFormKinds[form.id];
    if(kind==='message'){
      draft.text=input.value;
      updateCooperationActions(view,form);
    }else if((kind==='intervene'||kind==='resume'||kind==='followup')&&input.name==='note'){
      draft.note=input.value;
      updateCooperationActions(view,form);
      if(view.proposal)paintCooperationProposal(view,$('#main [data-cooperation-proposal]'));
    }else if(kind==='confirm'&&draft.brief&&Object.hasOwn(cooperationBriefFields,input.name)){
      draft.brief.values[input.name]=input.value;
      draft.brief.edited=true;
    }
  });
  document.addEventListener('click',event=>{
    const view=cooperationView,button=event.target.closest('[data-cooperation-followup-cancel]');
    if(!activeCooperation(view)||!button||view.submitting||!$('#main').contains(button))return;
    view.proposal=null;
    paintCooperationProposal(view,$('#main [data-cooperation-proposal]'));
    $('#main [data-cooperation-composer] textarea')?.focus({preventScroll:true});
  });
  document.addEventListener('submit',async event=>{
    const view=cooperationView,form=event.target;
    const kind=cooperationFormKinds[form.id];
    if(!view||!activeCooperation(view)||!kind)return;
    event.preventDefault();
    const data=view.data,draft=cooperationDraft(view);
    if(view.submitting||!data)return;
    const values=new FormData(form);
    const scope={id:view.id,revision:data.revision};
    const text=String(values.get('message')||'').trim(),note=String(values.get('note')||'').trim();
    const action=event.submitter?.value;
    let preview=kind==='followup'||((kind==='resume'||kind==='intervene')&&action==='preview');
    let command,payload;
    if(preview){
      if(!(kind==='followup'?data.can_follow_up:data.can_adjust_budget)||!note)return;
      command='work-item.cooperation-followup-preview';payload={...scope,text:note};
    }else if(kind==='followup-confirm'){
      const proposal=view.proposal;
      if(!proposal||proposal.revision!==data.revision||draft.note.trim()!==view.proposalText||
        ((proposal.requires_full_confirmation??(proposal.kind==='continue'&&data.access==='full'))&&!values.has('confirm_full')))return;
      command='work-item.cooperation-followup-confirm';
      payload={...scope,proposal_id:proposal.id,confirm_full:values.has('confirm_full')};
    }else if(kind==='message'){
      if(!data.can_send||data.pending||!text)return;
      command='work-item.cooperation-message';payload={...scope,text};
    }else if(kind==='confirm'){
      if(!data.can_confirm||data.pending||(data.access==='full'&&!data.plan_only&&!values.has('confirm_full')))return;
      const brief=Object.fromEntries(Object.keys(cooperationBriefFields).map(name=>[name,String(values.get(name)||'').trim()]));
      command='work-item.cooperation-confirm';payload={...scope,brief};
    }else if(kind==='resume'){
      if(data.state!=='PAUSED'||!data.recovery?.can_resume)return;
      command='work-item.cooperation-resume';payload={...scope,recovery_id:data.recovery.id,note};
    }else{
      if(data.state!=='RUNNING'||!['adjust','interrupt'].includes(action)||(action==='adjust'&&!note))return;
      command='work-item.cooperation-intervene';payload={...scope,action,note};
    }
    view.submitting=true;view.error='';view.actionError='';view.refreshError=false;
    clearTimeout(view.timer);view.request++;
    paintCooperation(view);
    try{
      const receipt=await S.command(command,payload);
      if(kind==='intervene'&&action==='adjust'&&receipt?.proposal)preview=true;
      if(preview){
        const proposal=receipt?.proposal;
        if(receipt?.no_dispatch!==true||typeof proposal?.id!=='string'||
          proposal.revision!==scope.revision||!['continue','budget'].includes(proposal.kind))
          throw Error('未能读取本次调整的预览。');
        view.proposal=proposal;
        view.proposalText=note;
      }else if(kind==='followup-confirm'){
        if(draft.note.trim()===view.proposalText){
          draft.note='';
          const input=$('#main [data-cooperation-composer] textarea[name=note]');
          if(input&&activeCooperation(view))input.value='';
        }
        view.proposal=null;
      }else if(kind==='message'&&draft.text.trim()===text){
        draft.text='';
        if(form.isConnected)form.elements.message.value='';
      }else if((kind==='intervene'||kind==='resume')&&draft.note.trim()===note){
        draft.note='';
        if(form.isConnected)form.elements.note.value='';
      }
      if(kind==='confirm')draft.brief=null;
    }catch(error){
      if(kind==='intervene'||kind==='resume'||kind==='followup'||kind==='followup-confirm')view.actionError=error.message;
      else view.error=error.message;
    }
    finally{view.submitting=false;}
    if(activeCooperation(view)){
      await refreshCooperation(view);
      if(preview&&view.proposal&&view.proposalText===note){
        $('#main #wi-cooperation-followup-confirm')?.scrollIntoView({block:'end'});
        $('#main #wi-followup-heading')?.focus({preventScroll:true});
      }
    }
  });
  const itemHref=item=>item.project_id?projectHref(item.project_id):'#work-items';
  const runHref=run=>'#control/task/'+encodeURIComponent(run.task_id);
  const cooperativeRuns=item=>item.cooperation?.runs||[];
  const runningStepStates=new Set(['RUNNING','RESERVED','SEND_INTENT','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
  const toolLabels={commandExecution:'执行命令',pwsh:'执行命令',run_shell_command:'执行命令',
    fileChange:'修改文件',edit:'修改文件',apply_patch:'修改文件',write:'写入文件',write_file:'写入文件',
    read:'读取文件',read_file:'读取文件',imageView:'查看图片',read_image:'查看图片',imageGeneration:'生成图片',
    webSearch:'网页搜索',web_search:'网页搜索','search_service.web_run':'网页搜索',web_fetch:'读取网页',
    glob:'查找文件',grep:'搜索文件内容',grep_search:'搜索文件内容',todo_write:'更新待办',
    skill:'加载 Skill',mcpToolCall:'使用扩展工具',dynamicToolCall:'使用工具',
    collabAgentToolCall:'协作代理操作',browserUse:'浏览器操作'};
  const toolLabel=tool=>Object.hasOwn(toolLabels,tool.label)?toolLabels[tool.label]:'工具操作';
  const toolState=tool=>{
    const outcome=tool.outcome||tool.state;
    return ({STARTED:'运行中',UNKNOWN:'状态待核对',REJECTED:'已拒绝'})[outcome]||S.state(outcome)||'状态待核对';
  };
  function teamworkPlans(item){
    const plan=item.teamwork.plan, recorded=(item.teamwork_history?.plans||[]).filter(row=>row.id!==plan.id||row.version===plan.version);
    return recorded.some(row=>row.id===plan.id)?recorded:[...recorded,plan];
  }
  function attemptLabel(result){
    if(result?.state==='HISTORICAL_RETIRED')return '已归入历史';
    if(result?.check_status==='FAIL')return '检查未通过';
    if(result?.check_status==='PASS')return '检查通过';
    if(result?.result_available)return '文字结果已保存';
    return result?.state==='COMPLETED'?'回合已结束':S.state(result?.state||'待核对');
  }
  function stepState(unit){
    if(!unit)return '尚未运行';
    if(unit.lineage?.inherited&&unit.result?.result_available&&!unit.lineage.problems?.length)return '沿用成果';
    if(!unit.result)return unit.blocked_reason?'等待前置':'尚未开始';
    return attemptLabel(unit.result);
  }
  function historyState(item){
    const runtime=item.runtime?.state, flow=item.cooperation?.flow;
    if(item.state==='ACCEPTED')return '已验收';
    if(item.state!=='OPEN')return states.find(([id])=>id===item.state)?.[1]||item.state;
    if(runtime==='NEEDS_RECONCILIATION'||runtime==='EXIT_UNPROVED')return '运行待核对';
    if(runtime==='WAITING_INPUT')return '等待审批或补充';
    if(runtime==='RUNNING'||flow?.running)return '执行中';
    if(flow?.record?.state==='SCHEDULED')return '等待预约启动';
    if(flow?.delivery?.state==='SAVED'||flow?.result_evidence_current&&flow.delivery?.state==='CURRENT')return '交付已保存';
    if(flow?.record?.state==='PAUSED')return '流程已暂停';
    return '方案已保存';
  }

  const teamHistoryFilter={item:null,plan:'',step:'',kind:'',limit:12,earlier:new Map()};
  const attentionStates=new Set(['WAITING_APPROVAL','WAITING_INPUT','FAILED','FAILED_NO_SEND','INTERRUPTED',
    'SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN','UNKNOWN','ERROR','DECLINED','DENIED','REJECTED']);
  const hasDelivery=item=>!!item.cooperation?.flow?.delivery;
  const needsAttention=result=>attentionStates.has(result?.state)||result?.check_status==='FAIL';
  const waitingRun=item=>cooperativeRuns(item).findLast(run=>['WAITING_APPROVAL','WAITING_INPUT'].includes(run.result?.state));
  const traceTime=at=>at?new Date(at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';

  function compactNote(text){
    if(!text)return '';
    const short=text.length>220?text.slice(0,220).trimEnd()+'…':text;
    return `<p>${h(short)}</p>${text.length>220?`<details><summary>查看原文</summary>
      <div class="wi-note-original">${h(text)}</div></details>`:''}`;
  }

  function noteTime(at){
    const date=new Date(at*1000);
    return Number.isFinite(at)&&at>0&&Number.isFinite(date.getTime())
      ?`<time datetime="${date.toISOString()}" data-note-time="${at}">${h(S.when(at))}</time>`:'';
  }

  function noteRequirements(item){
    const dialogue=item.records.find(row=>row.kind==='dialogue'&&!row.unavailable);
    return `<section class="wi-note-card" data-note-section="requirements">
      <div class="wi-section-heading"><h2>任务要求</h2>${dialogue?`<a href="${h(dialogue.href)}">打开关联对话</a>`:''}</div>
      ${compactNote(item.goal)}
      ${item.acceptance?`<details><summary>完成标准</summary><div class="wi-note-original">${h(item.acceptance)}</div></details>`:''}
      ${item.constraints_text?`<details><summary>约束与已确认要求</summary><div class="wi-note-original">${h(item.constraints_text)}</div></details>`:''}
    </section>`;
  }

  function noteSteps(item,steps,title,ordered=false){
    const plan=item.teamwork.plan;
    const units=new Map((item.cooperation?.units||[]).map(unit=>[unit.key,unit]));
    const saved=noteTime(plan.created);
    return `<section class="wi-note-card wi-note-steps" data-note-section="steps">
      <div class="wi-section-heading"><h2>${h(title)}</h2>
        ${saved?`<span class="wi-note-source">方案保存于 ${saved}</span>`:''}</div>
      <ol class="wi-note-step-list" ${ordered?'data-ordered':''}>${steps.map((step,index)=>{
        const unit=units.get(step.key);
        const state=!unit?.result&&unit?.lineage?.source&&!unit.lineage.problems?.length?'沿用成果':stepState(unit);
        return `<li><button type="button" class="wi-note-step" data-team-step="${h(step.key)}"
            aria-label="${h(step.title)}，${h(step.member.name)}，${h(state)}，查看执行轨迹">
          ${ordered?`<span class="wi-note-step-number" aria-hidden="true">${index+1}</span>`:''}
          <span class="wi-note-step-name"><strong>${h(step.title)}</strong><span>${h(step.member.name)}</span></span>
          <span class="wi-note-status">${h(state)}</span><span class="wi-note-step-arrow" aria-hidden="true">›</span>
        </button></li>`;
      }).join('')}</ol>
    </section>`;
  }

  function hostNotes(item){
    const plan=item.teamwork.plan, data=item.cooperation?.host_discussion;
    const experts=(data?.experts||[]).filter(row=>row.feedback);
    const decisions=(data?.decisions||[]).filter(row=>row.decision?.discussion?.resolution);
    const saved=noteTime(plan.created);
    const discussion=`${experts.length?`<section class="wi-note-card wi-expert-notes" data-note-section="experts"><h2>专家意见</h2>${experts.map(({member_name:name,feedback})=>
        `<details class="wi-expert-entry"><summary>${h(name)}</summary>${compactNote(feedback.proposal)}
          ${feedback.objections?.length||feedback.assistance?.length?`<details><summary>异议与协助请求</summary>
            ${feedback.objections?.length?`<p><strong>异议</strong> · ${h(feedback.objections.join('；'))}</p>`:''}
            ${feedback.assistance?.length?`<p><strong>协助请求</strong> · ${h(feedback.assistance.join('；'))}</p>`:''}</details>`:''}
        </details>`).join('')}</section>`:''}
      ${decisions.length?`<section class="wi-note-card wi-decision-card" data-note-section="host"><h2>主持人决定</h2>
        <ol class="wi-note-timeline">${decisions.map((row,index)=>
          `<li data-note-entry="host" data-note-order="${index+1}">${compactNote(row.decision.discussion.resolution)}</li>`).join('')}</ol>
        ${data?.human_response?.note?`<p><strong>你的确认</strong> · ${h(data.human_response.note)}</p>`:''}</section>`:''}`;
    const assignments=`<section class="wi-note-card wi-assignments" data-note-section="assignments"><h2>任务分工</h2>
        ${plan.units.map(unit=>`<div class="wi-assignment"><strong>${h(unit.title)}</strong><span>${h(unit.member.name)}</span></div>`).join('')}
        ${saved?`<p class="wi-note-source">方案保存于 ${saved}</p>`:''}
      </section>`;
    return `${noteRequirements(item)}${decisions.length||experts.length?
      `<div class="wi-host-notes"><div class="wi-note-main">${discussion}</div>${assignments}</div>`:assignments}`;
  }

  const leaderActions={dispatch:'安排下一步',deliver:'核对交付',pause:'建议暂停',adjust_dependencies:'调整工作顺序',
    revise_units:'细化分工',rework_units:'文字返工',file_rework_units:'文件返工'};
  function leaderDecision(record,index){
    const source=[h(record.member_name),noteTime(record.at)].filter(Boolean).join(' · ');
    return `<li class="wi-leader-decision" data-note-entry="leader" data-note-order="${index+1}">
      <div class="wi-section-heading"><div><strong>${h(leaderActions[record.action]||'协调决定')}</strong>
        ${record.unit_titles?.length?`<span class="wi-leader-target"> · ${h(record.unit_titles.join('、'))}</span>`:''}</div>
        ${source?`<span class="wi-note-source">${source}</span>`:''}</div>
      ${compactNote(record.reason)}
      ${S.button('查看协调回合','data-team-run="'+h(record.run_id)+'" aria-haspopup="dialog"')}
    </li>`;
  }

  function leaderNotes(item){
    const plan=item.teamwork.plan;
    const decisions=(item.cooperation?.leader_decisions||[]).slice().sort((a,b)=>a.at-b.at);
    const lead=plan.units.find(unit=>unit.role==='coordinate');
    const steps=plan.units.filter(unit=>unit.role!=='coordinate');
    return `<div class="wi-leader-notes">
      <section class="wi-note-card wi-leader-card" data-note-section="leader">
        <div class="wi-section-heading"><h2>负责人协调</h2>${!decisions.length&&lead?`<span class="wi-note-source">${h(lead.member.name)}</span>`:''}</div>
        ${decisions.length?`<ol class="wi-note-timeline">${decisions.map(leaderDecision).join('')}</ol>`
          :'<p class="wi-empty">暂无已保存的协调决定。</p>'}
        ${!decisions.length&&lead?S.button('查看协调轨迹','data-team-step="'+h(lead.key)+'"'):''}
      </section>
      ${noteRequirements(item)}
    </div>
    ${steps.length?noteSteps(item,steps,'执行分工'):''}`;
  }

  function coordination(item){
    const plan=item.teamwork.plan;
    let content;
    if(plan.mode==='host_experts')content=hostNotes(item);
    else if(plan.mode==='leader')content=leaderNotes(item);
    else content=`${noteRequirements(item)}${noteSteps(item,plan.units,'流程步骤',true)}`;
    return `<section class="wi-note-layout" aria-label="执行纪要">${content}</section>`;
  }

  function traceSelection(item){
    const filter=teamHistoryFilter, plans=teamworkPlans(item), plan=item.teamwork.plan;
    const selected=plans.find(row=>row.id===filter.plan)||plan;
    const runs=cooperativeRuns(item).filter(run=>run.plan_id===selected.id&&run.plan_version===selected.version
      &&(!filter.step||run.unit_key===filter.step)
      &&(filter.kind!=='review'||run.input.unit.role==='review'||run.retry_of||run.input.check_rework));
    return {plans,plan,selected,runs};
  }

  function savedTools(item,run){
    const recent=item.teamwork_history?.evidence?.[run.id]?.tools||[];
    const earlier=teamHistoryFilter.earlier.get(run.id)?.tools||[];
    return [...new Map([...earlier,...recent].map(tool=>[tool.id||tool.seq||tool.at,tool])).values()];
  }

  function olderTraceRun(item,runs){
    return runs.map(run=>{
      const trace=item.teamwork_history?.evidence?.[run.id];
      const earlier=teamHistoryFilter.earlier.get(run.id);
      return {run, before:earlier?earlier.next_before:trace?.more_tools?trace.tools[0]?.seq:null,
        at:earlier?.tools[0]?.at||trace?.tools[0]?.at||0};
    }).filter(row=>Number.isInteger(row.before)).sort((a,b)=>b.at-a.at)[0];
  }

  function actionStatus(item,tab=current().tab){
    const pending=waitingRun(item);
    const pendingTask=item.runtime?.pending_task_id||pending?.task_id;
    if(pendingTask)return `<a class="wi-primary-action" href="${runHref({task_id:pendingTask})}">查看待处理</a>`;
    if(['NEEDS_RECONCILIATION','EXIT_UNPROVED'].includes(item.runtime?.state))
      return '<a class="wi-primary-action" href="#monitor">查看运行状态</a>';
    if(hasDelivery(item)&&tab!=='delivery')return `<a class="wi-primary-action" data-team-tab="delivery" href="${teamworkHref(item,'delivery')}">查看成果</a>`;
    return '';
  }

  function traceOverview(item){
    const units=item.cooperation?.units||[];
    const active=units.find(unit=>runningStepStates.has(unit.result?.state));
    const currentTool=item.teamwork_history?.evidence?.[active?.latest_run_id]?.tools?.findLast(tool=>tool.state==='STARTED');
    const planned=item.teamwork.plan.units;
    return `<section class="wi-execution-overview" aria-label="任务进展">
      <div class="wi-section-heading"><div><h2>${h(active?'当前步骤 · '+active.title:'合作步骤')}</h2>
        ${active?`<p>${h(active.member_name)} · ${h(attemptLabel(active.result))}${currentTool?' · '+h(toolLabel(currentTool)):''}</p>`:
          item.next_step?`<p>${h(item.next_step)}</p>`:''}</div>
        ${v.data?.history_only?`<button type="button" data-team-refresh aria-label="刷新执行轨迹">刷新</button>`:''}</div>
      <div class="wi-stage-list" aria-label="按合作步骤查看">
        ${planned.map(step=>{
          const unit=units.find(row=>row.key===step.key), checked=teamHistoryFilter.step===step.key
            &&(!teamHistoryFilter.plan||teamHistoryFilter.plan===item.teamwork.plan.id);
          return `<button type="button" data-team-step="${h(step.key)}" aria-pressed="${checked}">
            <span class="wi-stage-marker" data-state="${needsAttention(unit?.result)?'attention':active?.key===step.key?'running':unit?.result?.state==='COMPLETED'?'done':'waiting'}"></span>
            <strong>${h(step.title)}</strong><span>${h(stepState(unit))}</span></button>`;
        }).join('')}
      </div></section>`;
  }

  function executionTrace(item){
    const filter=teamHistoryFilter,{plans,plan,selected,runs}=traceSelection(item);
    const versionChoices=plans.map((row,i)=>[row.id,'第 '+(i+1)+' 版'+(row.id===plan.id?' · 当前':'')]);
    const rows=runs.flatMap(run=>{
      const tools=savedTools(item,run).filter(tool=>filter.kind!=='attention'||attentionStates.has(tool.outcome||tool.state));
      const events=tools.map(tool=>({run,tool,at:tool.at}));
      if(filter.kind!=='attention'||needsAttention(run.result))events.push({run,at:run.created});
      return events;
    }).sort((a,b)=>b.at-a.at||(b.tool?.seq||0)-(a.tool?.seq||0));
    const older=olderTraceRun(item,runs);
    const stage=selected.units.find(unit=>unit.key===filter.step);
    const historicalSource=selected.id===plan.id&&stage?item.cooperation?.units.find(row=>row.key===stage.key)?.lineage?.source:null;
    const sourceRun=historicalSource?cooperativeRuns(item).find(run=>run.id===historicalSource.run_id&&
      run.plan_id===historicalSource.plan_id&&run.plan_version===historicalSource.plan_version):null;
    return `${traceOverview(item)}<section class="wi-ledger-card">
      <div class="wi-section-heading"><div><h2>执行记录</h2><p>点开一条记录可看详情，也可以按步骤筛选。</p></div></div>
      <div class="wi-ledger-filter">${plans.length>1?S.select('方案版本','team-history-plan',versionChoices,selected.id):''}
        ${S.select('合作步骤','team-history-step',[['','全部步骤'],...selected.units.map(row=>[row.key,row.title])],filter.step)}
        ${S.select('显示','team-history-kind',[['','全部执行'],['review','评审与返工'],['attention','待处理与异常']],filter.kind)}</div>
      ${sourceRun?`<div class="wi-ledger-note">此步骤沿用已保存的成果。${S.button('查看来源','data-team-run="'+h(sourceRun.id)+'"')}</div>`:''}
      ${rows.length?`<div class="wi-ledger-head" aria-hidden="true"><span>时间</span><span>动作</span><span>步骤与成员</span><span>结果</span><span></span></div>
      <ol class="wi-ledger" aria-label="按时间倒序排列的执行记录">${rows.slice(0,filter.limit).map(({run,tool,at})=>{
        const unit=run.input.unit, state=tool?toolState(tool):attemptLabel(run.result);
        const tone=(tool?attentionStates.has(tool.outcome||tool.state):needsAttention(run.result))?'attention':
          (tool?tool.state==='STARTED':runningStepStates.has(run.result?.state))?'running':'neutral';
        return `<li><button type="button" class="wi-ledger-row" data-team-run="${h(run.id)}"${tool?.id?` data-team-call="${h(tool.id)}"`:''} aria-haspopup="dialog">
          <time title="${h(S.when(at))}">${h(traceTime(at))}</time>
          <strong>${h(tool?toolLabel(tool):run.retry_of?'返工回合':'回合开始')}</strong>
          <span class="wi-ledger-member"><span>${h(unit.title)}</span><small>${h(unit.member.name)}</small></span>
          <span class="wi-ledger-state" data-tone="${tone}">${h(state)}</span><span class="wi-ledger-arrow" aria-hidden="true">›</span>
        </button></li>`;
      }).join('')}</ol>`:`<p class="wi-empty">${filter.kind==='attention'?'当前筛选下没有待处理或异常记录。':
        stage?'此步骤暂无已加载的执行记录。':'方案已经保存，执行动作会在开始后出现在这里。'}</p>`}
      ${rows.length>filter.limit||older?`<div class="wi-ledger-more">${S.button(rows.length>filter.limit?'显示更多记录':'加载更早动作',
        'data-team-more="'+(rows.length>filter.limit?'visible':'older')+'"')}</div>`:''}
    </section>`;
  }

  function teamDelivery(item){
    const delivery=fixedDelivery(item), units=(item.cooperation?.units||[]).filter(unit=>
      unit.result?.result_available||Object.keys(unit.result?.file_artifacts?.manifest||{}).length);
    const accepted=item.state==='ACCEPTED';
    return `<section class="wi-delivery-intro"><div><h2>${accepted?'已验收':hasDelivery(item)?'交付已保存':'任务成果'}</h2>
      <p>${accepted?'这项任务已标记验收完成。':hasDelivery(item)?'可预览或下载交付文件，检查后登记验收状态。':'已保存的步骤成果会出现在这里。'}</p></div>
      ${['OPEN','READY_FOR_ACCEPTANCE'].includes(item.state)&&!waitingRun(item)&&
        !['RUNNING','WAITING_INPUT','NEEDS_RECONCILIATION','EXIT_UNPROVED'].includes(item.runtime?.state)&&!item.cooperation?.flow?.running&&
        (hasDelivery(item)||item.state==='READY_FOR_ACCEPTANCE')?S.button('登记验收','data-team-acceptance'):''}</section>
      ${delivery||`<section class="wi-note-card"><h2>已保存步骤成果</h2>${units.length?units.map(unit=>
        `<div class="wi-row-line"><strong>${h(unit.title)}</strong><span>${h(attemptLabel(unit.result))}</span>${S.button('查看来源','data-team-run="'+h(unit.latest_run_id||unit.lineage?.source?.run_id||'')+'"')}</div>`).join(''):
        '<p class="wi-empty">暂无已保存的文件或文字成果。</p>'}</section>`}`;
  }

  function teamPanel(item,tab){
    if(tab==='usage')return taskUsagePanel(item.id);
    if(tab==='controls'){
      if(v.error&&(v.data.history_only||v.loadingControls!==null))
        return `<div class="wi-note-card">${S.button('重新读取操作与配置','data-team-load-retry')}</div>`;
      if(v.loadingControls!==null||v.data.history_only)
        return '<div class="wi-note-card" role="status">正在读取任务设置和运行状态…</div>';
      return teamControls(item);
    }
    return tab==='notes'?coordination(item):tab==='delivery'?teamDelivery(item):executionTrace(item);
  }
  const usageDate=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  const usageInteger=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0});
  const usageCurrency=new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY',maximumFractionDigits:9});
  const usageColumns=[['input','输入'],['cache_hit','命中缓存输入'],['cache_miss','未命中缓存输入'],['output','输出'],['tokens','总 Token']];
  const usageHeadings='<th scope="col" class="wi-usage-model">模型</th>'+usageColumns.map(([,label])=>'<th scope="col">'+label+'</th>').join('')+'<th scope="col">估算费用</th>';
  const usageProviders={codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen'};
  const usageNumber=value=>Number.isFinite(value)?usageInteger.format(value):'—';
  function usageTime(epoch){
    if(!Number.isFinite(epoch))return '—';
    const moment=new Date(epoch*1000);
    return `<time datetime="${moment.toISOString()}">${h(usageDate.format(moment))}</time>`;
  }
  function usageCells(row){
    const provider=usageProviders[row.provider]||row.provider;
    const cost=row.estimated_records
      ?[row.low,row.high].filter((value,index,values)=>index===0||value!==values[0]).map(value=>usageCurrency.format(Number(value))).join(' – ')
      :'—';
    return `<td><strong>${h(row.model)}</strong>${provider?`<small>${h(provider)}</small>`:''}</td>${
      usageColumns.map(([key])=>`<td${key==='tokens'?' class="wi-usage-total"':''}>${h(usageNumber(row[key]))}</td>`).join('')
    }<td>${h(cost)}</td>`;
  }
  function taskUsagePanel(id){
    const usage=v.taskUsage?.id===id?v.taskUsage:null;
    const task=usage?.data;
    const toolbar=`<div class="wi-section-heading"><h2>按模型汇总</h2>
      ${S.button('刷新用量','data-team-usage-refresh'+(usage?.loading?' disabled':''))}</div>`;
    const error=usage?.error?`<p class="wi-usage-error" role="alert">${h(usage.error)}</p>`:'';
    if(!task)return `<section class="wi-note-card wi-usage">${toolbar}
      ${error||'<p class="sp-note" role="status">正在读取任务用量…</p>'}</section>`;
    const tokens=task.models.map(row=>row.tokens).filter(Number.isFinite);
    const total=tokens.length?usageNumber(tokens.reduce((sum,value)=>sum+value,0)):'—';
    const missing=task.steps.filter(step=>step.models.every(row=>row.tokens==null)).length;
    const groups=task.steps.map((step,index)=>{
      const duration=Number.isFinite(step.ended)?usageNumber(Math.round(step.ended-step.started))+' 秒':'—';
      return `<tbody>${step.models.map((row,i)=>`<tr>${i===0?`<th scope="rowgroup" rowspan="${step.models.length}">
        <strong>${h(step.title||'第 '+(index+1)+' 次执行')}</strong>
        <small>开始 ${usageTime(step.started)}</small><small>结束 ${usageTime(step.ended)}</small>
        <small>耗时 ${h(duration)}</small>
        <a href="#control/task/${encodeURIComponent(step.task_id)}">查看执行记录</a></th>`:''}${usageCells(row)}</tr>`).join('')}</tbody>`;
    }).join('');
    return `<section class="wi-note-card wi-usage">${toolbar}${error}
      ${task.models.length?`<div class="wi-usage-overview"><span>已记录 <strong>${h(total)}</strong> Token</span>
        <span>${h(usageNumber(task.steps.length))} 次执行</span>${missing?`<span>${missing} 次暂无 Token 回执</span>`:''}</div>
        <div class="wi-usage-scroll" tabindex="0" role="region" aria-label="按模型汇总"><table>
          <thead><tr>${usageHeadings}</tr></thead><tbody>${task.models.map(row=>'<tr>'+usageCells(row)+'</tr>').join('')}</tbody></table></div>`:
        '<p class="sp-note">本任务暂无用量记录。</p>'}
      <p class="sp-note">Token 按已保存的回执统计。「—」表示暂无对应数据，费用列显示已有的估算。</p></section>
      ${groups?`<section class="wi-note-card wi-usage"><h2>逐步消耗</h2>
        <div class="wi-usage-scroll" tabindex="0" role="region" aria-label="逐步消耗"><table>
          <thead><tr><th scope="col">工作步骤</th>${usageHeadings}</tr></thead>${groups}</table></div></section>`:''}`;
  }
  async function loadTaskUsage(id,refresh=false){
    const previous=v.taskUsage?.id===id?v.taskUsage:null;
    if(previous&&(previous.loading||(!refresh&&previous.data)))return;
    const panel=()=>{
      const route=current();
      return route.id===id&&route.tab==='usage'?$('#main [data-team-view="usage"]'):null;
    };
    if(!panel())return;
    const restoreFocus=!!document.activeElement?.matches('[data-team-usage-refresh]');
    const request=v.taskUsage={id,data:previous?.data,loading:true};
    panel().innerHTML=taskUsagePanel(id);
    try{
      const result=await S.request('work-usage?'+new URLSearchParams({id}));
      if(result.task?.id!==id)throw Error('用量记录与当前任务不一致。');
      request.data=result.task;
    }catch(error){request.error=error.message;}
    request.loading=false;
    if(v.taskUsage!==request)return;
    const currentPanel=panel();
    if(currentPanel){
      currentPanel.innerHTML=taskUsagePanel(id);
      if(restoreFocus)currentPanel.querySelector('[data-team-usage-refresh]')?.focus({preventScroll:true});
    }
  }
  function cooperationPath(item){
    const {origin}=current();
    const back=origin==='records'?['#records','返回对话记录']:origin==='tasks'?['#work-items','返回任务列表']:
      [itemHref(item),item.project_id?'返回项目概览':'返回任务列表'];
    const fromConversation=!origin&&item.cooperation_conversation;
    const link=`<a${fromConversation?'':' class="wi-work-return"'} href="${h(back[0])}">${back[1]}</a>`;
    const chat=chatLink(item);
    return `<div class="wi-path">${fromConversation?chat+link:link+chat}</div>`;
  }
  function teamworkOverview(item,tab){
    return `<section class="wi-team-view" aria-label="合作任务记录">${cooperationPath(item)}
      <header class="wi-team-heading"><div><h1>${h(item.title)}</h1><div><span>${h(teamworkModes[item.teamwork.plan.mode]||'合作任务')}</span>${item.teamwork.plan.pdca?`<span>${h(pdcaRounds(item.teamwork.plan.pdca.rounds))}</span>`:''}<span class="wi-state" data-team-live-state>${h(historyState(item))}</span></div></div>
      <div><span data-team-status-action>${actionStatus(item)}</span>
      ${S.button('公开思考','data-team-thoughts="" aria-haspopup="dialog"'+(cooperativeRuns(item).length?'':' hidden'))}</div></header>
      <nav class="wi-team-tabs" aria-label="合作任务视图">${workTabs.map(([id,title])=>`<a href="${teamworkHref(item,id)}" data-team-tab="${id}" aria-current="${tab===id?'page':'false'}">${title}</a>`).join('')}</nav>
      <div class="wi-team-pane" data-team-history-panel data-team-view="${tab}">${teamPanel(item,tab)}</div>
      <dialog class="wi-trace-dialog" data-team-trace-dialog aria-labelledby="wi-trace-title"></dialog>
      <dialog class="wi-thought-dialog" data-team-thought-dialog aria-labelledby="wi-thought-title"></dialog></section>`;
  }
  function cooperationBeforePlan(item){
    return `<section class="sp-page wi-team-page"><section class="wi-team-view" aria-label="合作任务记录">
      ${cooperationPath(item)}
      <header class="wi-team-heading"><div><h1>${h(item.title)}</h1><span class="wi-state">需求讨论</span></div></header>
      <div class="wi-team-pane"><section class="wi-note-card"><h2>任务目标</h2>
        <p class="wi-note-text">${h(item.goal)}</p><p>确认任务说明并保存方案后，可在这里查看各阶段记录。</p>
      </section></div></section></section>`;
  }
  function workItemControls(item,project,sourceTask,origin){
    return `${chatLink(item)}<p class="sp-note">${h(projectName(item.project_id))} · ${h(states.find(s=>s[0]===item.state)?.[1])} · ${item.version_count} 个版本</p>
      ${taskEditor(item)}${fixedPreparation(item)}${runtime(item)}${continuation(item)}${teamwork(item)}
      <section class="sp-card"><h2>关联对话与执行</h2>${recordEditor(item)}</section>
      ${origin?'':`<a href="${project?projectHref(project,sourceTask?.id):'#work-items'}">返回任务列表</a>`}`;
  }
  function taskEditor(item,controls=false,{formId='work-item-edit',projects=v.projects}={}){
    const draft=controls?controlState(item).draft:null, fields={...item,...draft?.values};
    const stale=!!draft&&draft.revision!==item.revision;
    return `<form id="${formId}" class="${controls?'wi-task-editor':'sp-card'}"
      ${controls?`data-task-editor-revision="${h(draft?.revision??item.revision)}"`:''}>
      ${S.field('任务名称','title',fields.title,'required maxlength="160"')}
      ${S.text('任务目标','goal',fields.goal,'required maxlength="16000"')}
      ${S.text('约束与已确认要求','constraints_text',fields.constraints_text,'maxlength="16000"')}
      ${S.text('完成要求','acceptance',fields.acceptance,'maxlength="16000"')}
      ${S.text('已有进展','progress',fields.progress,'maxlength="16000"')}
      ${S.text('下一步','next_step',fields.next_step,'maxlength="16000"')}
      <div class="wi-task-fields">${S.select('项目归属','project_id',[['','普通任务'],...projects.map(p=>[p.id,p.name])],fields.project_id||'')}
        ${S.select('任务状态','state',states,fields.state)}</div>
      ${stale?`<p role="status">任务已在别处更新，本次编辑已保留。请先核对修改。</p>
        ${S.button('放弃本次修改并读取最新设置','data-task-draft-reset')}`:''}
      <button type="submit" ${stale?'disabled':''}>保存任务</button>
      <p class="sp-note">执行回复与任务验收分别记录。调整项目归属会保留历史执行与文件位置。</p>
    </form>`;
  }
  function recordEditor(item){
    return `<div class="wi-linked-records">${item.records.map(record).join('')||'<p class="sp-empty">尚未关联记录。</p>'}</div>
      <form id="work-item-link">${S.select('选择已有记录','record',v.available.map(r=>[r.kind+':'+r.id,r.title+' · '+(r.kind==='task'?'执行记录':r.kind==='chat'?'Chat':'合作讨论')]),'')}
        <span role="status" data-record-options-status>${v.available.length?'':'正在读取可关联记录…'}</span><button type="submit" ${v.available.length?'':'disabled'}>关联到任务</button></form>
      ${S.button('新建关联对话','data-item-action="chat"')}`;
  }
  async function loadRecordOptions(){
    const seq=v.seq,id=current().id;
    if(!id||v.recordsLoad===seq)return;
    v.recordsLoad=seq;
    const form=()=>seq===v.seq&&current().id===id&&$('#main #work-item-link');
    const rows=[];
    try{
      let cursor='';
      do{
        const page=await S.request('record-catalog?'+new URLSearchParams({scope:'all',limit:'100',...(cursor?{cursor}:{})}));
        if(!form())return;
        rows.push(...page.records);
        cursor=page.next_cursor;
      }while(cursor);
      v.available=rows;
      const selector=form().elements.record;
      selector.replaceChildren(...rows.map(r=>new Option(r.title+' · '+(r.kind==='task'?'执行记录':r.kind==='chat'?'Chat':'合作讨论'),r.kind+':'+r.id)));
      form().querySelector('[type=submit]').disabled=!rows.length;
      form().querySelector('[data-record-options-status]').textContent=rows.length?'':'暂无可关联的记录。';
    }catch(error){
      if(!form())return;
      v.recordsLoad=0;
      const status=form().querySelector('[data-record-options-status]');
      status.textContent=error.message+' ';
      const retry=document.createElement('button');
      retry.type='button';retry.textContent='重新读取记录';retry.onclick=()=>void loadRecordOptions();status.append(retry);
    }
  }
  function controlState(item){
    if(v.controls?.item!==item.id)v.controls={item:item.id,open:new Set(),run:'',draft:null};
    return v.controls;
  }
  function controlDisclosure(key,title,content){
    return `<details class="wi-control-disclosure" data-control-disclosure="${h(key)}" ${v.controls?.open.has(key)?'open':''}>
      <summary>${h(title)}</summary><div>${content}</div></details>`;
  }
  function teamControls(item){
    controlState(item);
    const plan=item.teamwork.plan, authorization=item.cooperation?.authorization, flow=item.cooperation?.flow;
    const lightweight=v.data.compact_controls;
    const preparation=lightweight?'':fixedPreparation(item);
    const operations=lightweight?[
      ['cooperation','运行合作与派发授权'],['plan-editor','编辑合作方案'],['preparation','方案准备与修订']
    ].map(([key,title])=>controlDisclosure(key,title,'<p role="status">正在读取完整操作与配置…</p>')).join(''):teamwork(item,true);
    const planStatus=flow?.can_start?'可启动':flow?.running?'运行中':flow?.record?.state==='SCHEDULED'?'已预约':
      item.teamwork.stale?'任务要求已更新':authorization?.dispatch_before<=Date.now()/1000?'派发授权已到期':
      authorization?'已保存派发授权':'待授权';
    const project=item.project_name||projectName(item.project_id);
    const materials=cooperationMaterials(plan.pdca);
    return `<div class="wi-controls-layout" data-control-item="${h(item.id)}">
      ${runtime(item,true)}
      <section class="wi-control-card">
        <div class="wi-section-heading"><h2>任务设置</h2>
          <span class="wi-control-meta">${h(item.version_count)} 个版本</span></div>
        <p class="wi-control-summary">${h(project)}</p>
        ${controlDisclosure('task','编辑任务设置',taskEditor(item,true))}
        ${controlDisclosure('records','管理关联对话与记录',recordEditor(item))}
        ${materials?controlDisclosure('materials','任务材料与附件',materials):''}
      </section>
      <section class="wi-control-card">
        <div class="wi-section-heading"><h2>方案与授权</h2><span class="wi-control-status">${h(planStatus)}</span></div>
        <p class="wi-control-summary">${h(teamworkModes[plan.mode]||'合作任务')} · ${plan.units.length} 步
          ${authorization?` · 派发上限 ${h(authorization.max_attempts_total)} 次`:''}</p>
        ${operations}
        ${preparation?controlDisclosure('preparation','方案准备与修订',preparation):''}
      </section>
    </div>`;
  }
  function projectTaskStatus(item){
    if(item.state==='ACCEPTED')return '已验收';
    if(item.state==='OPEN')return item.delivery_saved?'已交付':item.mode?'方案已保存':'已登记';
    return states.find(([key])=>key===item.state)?.[1]||'待查看';
  }
  function contextualTaskHref(item,origin,tab='controls'){
    return item.mode||item.cooperation_conversation?teamworkHref(item,tab,origin):
      `#work-items/item/${encodeURIComponent(item.id)}/from-${origin}`;
  }
  function taskDirectoryRows(){
    const {project,state}=taskFilters;
    const rows=v.data.work_items.filter(item=>(project==='all'||(item.project_id||'unfiled')===project)&&
      (state==='all'||(state==='acceptance'?item.needs_acceptance:item.state===state)));
    return `<p class="wi-directory-count" role="status">${rows.length} 项任务</p>${rows.length?
      `<div class="wi-directory-list">${rows.map(item=>{
        const mode=teamworkModes[item.mode]||'';
        const goal=item.next_step||item.goal;
        const acceptance=item.result_task_id?`#results/${encodeURIComponent(item.result_task_id)}/from-tasks`:
          item.delivery_saved?teamworkHref(item,'delivery','tasks'):'';
        return `<article class="wi-directory-row" data-task-directory-item="${h(item.id)}">
          <div><a class="wi-directory-title" href="${contextualTaskHref(item,'tasks')}">${h(item.title)}</a>
          <p class="wi-directory-meta">${h(projectName(item.project_id))}${mode?` · ${h(mode)}`:''}</p>
          ${goal?`<p class="wi-directory-goal">${h(goal)}</p>`:''}</div>
          <div class="wi-directory-status"><span class="wi-project-state">${h(item.needs_acceptance?'待验收':projectTaskStatus(item))}</span>
          ${acceptance?`<a href="${acceptance}">核对成果</a>`:''}</div>
        </article>`;
      }).join('')}</div>`:'<p class="sp-empty">没有匹配的任务。</p>'}`;
  }
  function taskDirectory(){
    const activeProjects=new Set(v.projects.map(p=>p.id));
    if(!['all','unfiled'].includes(taskFilters.project)&&!activeProjects.has(taskFilters.project))taskFilters.project='all';
    return `<section class="sp-page wi-directory" aria-label="任务总览">${S.nav('workitems')}
      <header class="sp-heading"><div><h1>任务</h1><p>查看普通任务和各个项目的进度及验收状态。</p></div></header>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      <div class="wi-directory-filters" data-task-directory-filters>
        ${S.select('项目','project',[['all','全部项目'],['unfiled','普通任务'],...v.projects.map(p=>[p.id,p.name])],taskFilters.project)}
        ${S.select('任务状态','state',[['all','全部状态'],['acceptance','待验收'],...states.filter(([key])=>key!=='READY_FOR_ACCEPTANCE')],taskFilters.state)}
      </div>
      <div data-task-directory-results>${taskDirectoryRows()}</div>
      <details class="sp-card wi-directory-create" ${v.data.work_items.length?'':'open'}><summary>新建任务</summary>
        <form id="work-item-create">${S.field('任务名称','title','','required maxlength="160"')}
          ${S.text('任务目标','goal','','required maxlength="16000"')}${S.text('完成要求','acceptance','','maxlength="16000"')}
          <button type="submit">保存新任务</button>
        </form>
      </details>
    </section>`;
  }
  function projectOverview(project,source){
    const tasks=v.data.work_items, name=projectName(project);
    const discussions=v.records.filter(row=>row.kind!=='task');
    const executions=v.records.filter(row=>row.kind==='task');
    const returnTo=source?workReturnTask(executions.filter(row=>row.independent_work),source):null;
    const taskLinks=tasks.map(task=>`<div class="wi-project-task-row"><a class="wi-project-task"
      href="${task.cooperation_conversation?cooperationHref(task.id):contextualTaskHref(task,'project')}" aria-label="${h(task.title)}">
      <div><strong>${h(task.title)}</strong>${task.mode?`<span class="wi-project-mode">${h(teamworkModes[task.mode]||'合作任务')}</span>`:''}
        ${task.next_step||task.goal?`<p>${h(task.next_step||task.goal)}</p>`:''}</div>
      <span class="wi-project-state" data-delivered="${task.delivery_saved||task.state==='ACCEPTED'}">${h(projectTaskStatus(task))}</span>
      <span class="wi-project-arrow" aria-hidden="true">›</span></a>
      ${task.cooperation_conversation?`<a class="wi-project-task-detail" href="${contextualTaskHref(task,'project')}"
        aria-label="任务详情 · ${h(task.title)}">任务详情</a>`:''}</div>`).join('');
    return `<section class="sp-page wi-project-page" aria-label="项目概览">
      <div class="wi-project-links"><a class="wi-project-back" href="#projects">‹ 所有项目</a>${workReturnLink(returnTo)}</div>
      <header class="wi-project-hero"><div><p>项目概览</p><h1>${h(name)}</h1></div><div class="wi-project-actions">
        <a class="wi-work-return" href="#files/${encodeURIComponent(project)}">项目文件</a>
        ${S.button('新建任务','data-project-create aria-haspopup="dialog" class="wi-project-new"')}</div></header>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      <div class="wi-project-layout">
        <section class="wi-project-tasks"><div class="wi-project-panel-title"><h2>项目任务</h2>
          ${tasks.length?`<span>${tasks.length} 项</span>`:''}</div>
          ${taskLinks||'<p class="wi-project-empty">项目里还没有任务。</p>'}
        </section>
        <section class="wi-project-records"><div class="wi-project-panel-title"><h2>项目记录</h2></div>
          ${discussions.length?`<div class="wi-project-record-list">${discussions.map(record).join('')}</div>`:''}
          ${executions.length?`<details ${tasks.length?'':'open'}><summary>执行记录（${executions.length}）</summary>
            <div class="wi-project-record-list">${executions.map(record).join('')}</div></details>`:''}
          ${discussions.length||executions.length?'':'<p class="wi-project-empty">暂无对话或执行记录。</p>'}
          ${S.button('新建项目对话','data-item-action="chat" class="wi-project-chat"')}
        </section>
      </div>
      <dialog class="wi-project-dialog" data-project-create-dialog aria-labelledby="wi-project-dialog-title">
        <h2 id="wi-project-dialog-title">新建任务</h2>
        <form id="work-item-create">
          ${S.field('任务名称','title','','required maxlength="160" autofocus')}
          ${S.text('任务目标','goal','','required maxlength="16000"')}
          ${S.text('完成要求','acceptance','','maxlength="16000"')}
          <p role="alert" class="sp-error" data-project-create-error hidden></p>
          <div class="wi-project-form-actions">
            ${S.button('取消','data-project-create-close')}<button type="submit">保存新任务</button>
          </div>
        </form>
      </dialog>
    </section>`;
  }
  function draw(){const c=current();if(!c.active||!v.data)return;
    const item=c.id?v.data.work_items[0]:null,project=item?.project_id||c.project;
    if(item?.teamwork?.plan&&item.id===c.id){
      if(teamHistoryFilter.item!==item.id)Object.assign(teamHistoryFilter,{item:item.id,plan:'',step:'',kind:'',limit:12,earlier:new Map()});
      const stage=cooperativeRuns(item).findLast(run=>run.task_id===c.stageTask);
      if(stage)Object.assign(teamHistoryFilter,{plan:stage.plan_id,step:stage.input.unit.key,kind:'',limit:12});
      $('#main').innerHTML=`<section class="sp-page wi-team-page">${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
        ${teamworkOverview(item,c.tab)}</section>`;
      busy(v.busy);
      if(c.tab==='usage')void loadTaskUsage(item.id);
      if(stage&&c.thoughts)openThoughts(item,$('#main [data-team-thoughts]'),stage.id);
      return;
    }
    const preparation=item&&activePreparation(item)?.record;
    if(preparation?.pdca){
      $('#main').innerHTML=(v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:'')+pdcaPreparation(item,preparation);
      busy(v.busy);
      return;
    }
    if(item?.cooperation_conversation){
      $('#main').innerHTML=cooperationBeforePlan(item);
      return;
    }
    if(c.project){
      $('#main').innerHTML=projectOverview(c.project,c.fromWork);
      busy(v.busy);
      return;
    }
    if(!item){
      $('#main').innerHTML=taskDirectory();
      busy(v.busy);
      return;
    }
    const sourceTask=workReturnTask(item.records||[],c.fromWork);
    const back=c.origin==='tasks'?['#work-items','返回任务列表']:
      c.origin==='project'&&project?[projectHref(project),'返回项目概览']:null;
    const links=[back?`<a class="wi-work-return" href="${back[0]}">${back[1]}</a>`:'',workReturnLink(sourceTask)].filter(Boolean);
    $('#main').innerHTML=`<section class="sp-page" aria-label="任务详情">
      ${links.length?`<div class="wi-task-links">${links.join('')}</div>`:''}
      <header class="sp-heading"><div><h1>${h(item.title)}</h1><p>保存目标、要求和进度，关联对话与执行记录。</p></div></header>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      ${workItemControls(item,project,sourceTask,c.origin)}
    </section>`;
    busy(v.busy);
  }
  function historyPane(item,tab){
    const panel=$('#main [data-team-history-panel]');
    if(!panel||current().id!==item.id)return;
    const href=teamworkHref(item,tab);
    const switching=current().tab!==tab;
    if(switching){
      if(current().tab==='controls')captureEditors();
      history.pushState(null,'',href);
      window.WorkdeskSettingsPreferences?.remember(href.slice(1));
    }
    panel.dataset.teamView=tab;
    panel.innerHTML=teamPanel(item,tab);
    $('#main [data-team-status-action]').innerHTML=actionStatus(item,tab);
    for(const link of $('#main').querySelectorAll('.wi-team-tabs [data-team-tab]'))
      link.setAttribute('aria-current',link.dataset.teamTab===tab?'page':'false');
    if(tab==='usage')void loadTaskUsage(item.id,switching);
  }
  async function acceptDelivery(item){
    const seq=v.seq;
    if(!await S.confirm('登记任务验收','确认你已检查交付成果，并将这个任务标为已验收。',item.title))return;
    if(seq!==v.seq||current().id!==item.id||v.busy)return;
    v.busy=true;busy(true);
    try{
      await S.command('work-item.update',{id:item.id,revision:item.revision,changes:{state:'ACCEPTED'}});
    }catch(e){
      if(seq===v.seq&&current().id===item.id)toast(e.message);
      return;
    }finally{
      v.busy=false;
      if(seq===v.seq&&current().id===item.id)busy(false);
    }
    if(seq===v.seq&&current().id===item.id)await load();
  }

  function runDetails(item,run){
    const {unit,dependencies=[],check_rework:rework}=run.input, result=run.result||{};
    const files=Object.keys(result.file_artifacts?.manifest||{});
    const related=cooperativeRuns(item).filter(row=>(row.input.dependencies||[]).some(ref=>ref.run_id===run.id));
    const runs=cooperativeRuns(item);
    const previous=run.retry_of&&runs.find(row=>row.id===run.retry_of);
    const plan=item.teamwork_history?.evidence?.[run.id]?.plan;
    const checks=result.file_artifacts?.checks||[];
    const missing=rework?.missing_outputs||[];
    return `<section><h3>任务与材料</h3>${compactNote(unit.goal)}
      ${unit.acceptance?`<details><summary>完成标准</summary><div class="wi-note-original">${h(unit.acceptance)}</div></details>`:''}
      ${dependencies.length?`<ul>${dependencies.map(ref=>{
        const source=runs.find(row=>row.id===ref.run_id),title=source?.input.unit.title||ref.unit_key||'前置步骤';
        return `<li>${ref.task_id?`<a href="${runHref(ref)}">${h(title)}</a>`:h(title)}</li>`;
      }).join('')}</ul>`:''}
      ${plan?`<details><summary>工作端计划</summary><div class="markdown">${markdown(plan)}</div></details>`:''}
      ${previous?`<p><a href="${runHref(previous)}">查看此前尝试</a></p>`:''}
      ${missing.length?`<p>本次返工需补齐 ${h(missing.join('、'))}</p>`:''}</section>
      <section><h3>结果与交接</h3>
      ${files.length?`<ul>${files.map(file=>`<li>${h(file)}</li>`).join('')}</ul>`:''}
      ${checks.length?`<ul>${checks.map(check=>`<li>${h(check.definition.file)} · ${check.passed?'通过':'未通过'}</li>`).join('')}</ul>`:''}
      ${result.result_available&&result.answer?`<details><summary>查看文字成果</summary><div class="wi-note-original">${h(result.answer)}</div></details>`:''}
      ${related.length?`<p>已交接给 ${related.map(row=>`<a href="${runHref(row)}">${h(row.input.unit.title)}</a>`).join('、')}</p>`:''}
      ${!files.length&&!result.result_available&&!related.length?'<p>暂无已保存的交付内容。</p>':''}</section>`;
  }

  async function openTraceDetails(item,trigger){
    const run=cooperativeRuns(item).find(row=>row.id===trigger.dataset.teamRun);
    const dialog=$('#main [data-team-trace-dialog]');
    if(!run||!dialog||dialog.open)return;
    const call=trigger.dataset.teamCall;
    const tool=call?savedTools(item,run).find(row=>row.id===call):null;
    if(call&&!tool)return;
    const unit=run.input.unit, at=tool?.at||run.created;
    dialog.innerHTML=`<header class="wi-trace-header"><div><p>${tool?'工具动作':'工作回合'}</p>
      <h2 id="wi-trace-title">${h(tool?toolLabel(tool):unit.title)}</h2></div>
      <form method="dialog"><button type="submit" autofocus>关闭</button></form></header>
      <div class="wi-trace-meta"><strong data-trace-status>${h(tool?toolState(tool):attemptLabel(run.result))}</strong>
        <span>${h(tool?unit.title+' · '+unit.member.name:unit.member.name)}</span><time>${h(S.when(at))}</time></div>
      <div class="wi-trace-detail-content" data-trace-content>${tool?'<p role="status">读取已保存的记录…</p>':runDetails(item,run)}</div>
      <footer class="wi-trace-footer"><a href="${runHref(run)}">打开本次执行与回执</a></footer>`;
    dialog.addEventListener('close',()=>{(trigger.isConnected?trigger:$('#main [data-team-tab="trace"]'))?.focus({preventScroll:true});},{once:true});
    dialog.showModal();
    if(!tool)return;
    const content=dialog.querySelector('[data-trace-content]');
    const stillOpen=()=>dialog.open&&content.isConnected&&current().id===item.id;
    try{
      const record=await S.request('work-items/tool-record?'+new URLSearchParams({id:item.id,run:run.id,call}));
      if(!stillOpen())return;
      if(record.call_id!==call)throw new Error('Wrong tool record');
      const receipt=record.receipt||{};
      dialog.querySelector('[data-trace-status]').textContent=toolState(record);
      const receiptSummary=[toolState(record),
        receipt.completed_at_ms>1e12?S.when(receipt.completed_at_ms/1000):'',
        Number.isInteger(receipt.duration_ms)&&receipt.duration_ms>=0?'耗时 '+(receipt.duration_ms/1000).toFixed(1)+' 秒':'',
        Number.isInteger(receipt.exit_code)?'退出码 '+receipt.exit_code:''].filter(Boolean).join(' · ');
      content.innerHTML=`<section><h3>输入</h3>${record.input?`<pre>${h(record.input)}</pre>`:'<p>暂无已保存的输入摘要。</p>'}</section>
        <section><h3>输出</h3>${record.output?`<pre>${h(record.output)}</pre>`:'<p>暂无已保存的输出摘要。</p>'}</section>
        <section><h3>回执</h3><p>${h(receiptSummary)}</p></section>`;
    }catch{
      if(stillOpen())content.innerHTML='<p role="alert">暂时无法读取这条工具记录。可打开本次执行与回执继续查看。</p>';
    }
  }

  let traceLoading=false;
  async function showMoreTrace(item,button){
    if(traceLoading)return;
    if(button.dataset.teamMore==='visible'){
      teamHistoryFilter.limit+=12;
    }else{
      const {runs}=traceSelection(item), older=olderTraceRun(item,runs);
      if(!older)return;
      const previous=teamHistoryFilter.earlier.get(older.run.id)?.tools||[];
      const seq=v.seq;
      traceLoading=true;button.disabled=true;
      try{
        const page=await S.request('work-items/trajectory?'+new URLSearchParams({id:item.id,run:older.run.id,before:older.before}));
        if(seq!==v.seq||current().id!==item.id)return;
        teamHistoryFilter.earlier.set(older.run.id,{tools:[...page.tools,...previous],next_before:page.next_before});
        teamHistoryFilter.limit+=12;
      }catch{
        if(button.isConnected)toast('暂时无法加载更早的执行动作');
        return;
      }finally{
        traceLoading=false;
        if(button.isConnected)button.disabled=false;
      }
    }
    if(current().tab==='trace'){
      historyPane(v.data?.work_items[0]||item,'trace');
      $('#main [data-team-more]')?.focus({preventScroll:true});
    }
  }
  let thoughtRequest=0;
  async function showThoughts(dialog,item,runId){
    const request=++thoughtRequest;
    const content=dialog.querySelector('[data-thought-content]');
    content.innerHTML='<p role="status">正在读取本回合记录…</p>';
    const isSelected=()=>request===thoughtRequest&&dialog.isConnected&&dialog.open&&current().id===item.id;
    try{
      const result=await S.request('work-items/public-thoughts?'+new URLSearchParams({id:item.id,run:runId}));
      if(!isSelected()||result.run_id!==runId)return;
      const label={codex:'Codex 公开摘要',deepseek:'DeepSeek 公开思考',qwen:'Qwen Code 公开思考'}[result.provider];
      content.innerHTML=result.notes.length
        ?`<p class="wi-thought-source">${h(label)}${result.more?' · 显示最近 80 段':''}</p>${result.notes.map(note=>
          `<article class="markdown">${markdown(note.text)}</article>`).join('')}`
        :'<p>这个回合暂无已保存的公开思考。</p>';
    }catch{
      if(isSelected())content.innerHTML='<p role="alert">暂时无法读取这个回合。请关闭后重试。</p>';
    }
  }
  function openThoughts(item,trigger,requestedRun=trigger.dataset.teamThoughts){
    const dialog=$('#main [data-team-thought-dialog]'),runs=cooperativeRuns(item).slice().reverse();
    if(!dialog||dialog.open||!runs.length)return;
    const selected=runs.find(run=>run.id===requestedRun)
      ||runs.find(run=>runningStepStates.has(run.result?.state))||runs[0];
    const choices=runs.map(run=>[run.id,run.input.unit.title+' · '+run.input.unit.member.name+' · '+S.when(run.created)]);
    dialog.innerHTML=`<header class="wi-thought-header"><h2 id="wi-thought-title">公开思考</h2>
      <form method="dialog"><button type="submit" autofocus>关闭</button></form></header>
      ${S.select('查看回合','team-thought-run',choices,selected.id)}
      <div class="wi-thought-content" data-thought-content aria-live="polite"></div>`;
    dialog.onchange=event=>{if(event.target.name==='team-thought-run')showThoughts(dialog,item,event.target.value);};
    dialog.addEventListener('close',()=>{(trigger.isConnected?trigger:$('#main .wi-team-heading [data-team-thoughts]'))?.focus({preventScroll:true});},{once:true});
    dialog.showModal();
    showThoughts(dialog,item,selected.id);
  }
  let teamRefreshTimer, teamRefreshing=false;
  function scheduleTeamRefresh(){
    clearTimeout(teamRefreshTimer);
    const route=current(),item=v.data?.work_items[0];
    const preparation=item&&activePreparation(item)?.record;
    if(route.id===item?.id&&preparation?.pdca&&preparation.state==='PREPARING'){
      teamRefreshTimer=setTimeout(refreshPdcaPreparation,4000);
      return;
    }
    if(v.data?.history_only&&route.id===item?.id&&
      (['RUNNING','WAITING_INPUT'].includes(item.runtime?.state)||item.cooperation?.flow?.running))
      teamRefreshTimer=setTimeout(()=>refreshTeamHistory(),4000);
  }
  async function refreshPdcaPreparation(){
    const route=current(),seq=v.seq;
    if(document.hidden||v.busy){scheduleTeamRefresh();return;}
    try{
      const detail=await S.request('work-items?'+new URLSearchParams({id:route.id}));
      if(seq!==v.seq||current().id!==route.id)return;
      const item=detail.work_items[0],row=item&&activePreparation(item)?.record;
      if(item?.teamwork?.plan){await load();return;}
      if(row?.state!=='PREPARING'){v.data=detail;draw();return;}
    }catch{/* The manual refresh button stays available. */}
    if(seq===v.seq)scheduleTeamRefresh();
  }
  async function refreshTeamHistory(trigger){
    clearTimeout(teamRefreshTimer);
    const route=current(),id=route.id, seq=v.seq;
    if(!id||!v.data?.history_only)return;
    if(teamRefreshing||traceLoading||v.busy||v.loadingControls||document.hidden||
       route.tab==='controls'||$('#main [data-team-thought-dialog][open]')||$('#main [data-team-trace-dialog][open]')||
       (!trigger&&document.activeElement?.closest('[data-team-history-panel]')&&document.activeElement.matches('select'))){
      scheduleTeamRefresh();
      return;
    }
    teamRefreshing=true;
    if(trigger)trigger.disabled=true;
    try{
      const updated=await S.request('work-items/history?'+new URLSearchParams({id}));
      const item=updated.work_items[0];
      if(current().id!==id||seq!==v.seq||!v.data?.history_only||!item?.teamwork)return;
      if(JSON.stringify(updated)===JSON.stringify(v.data))return;
      v.data=updated;
      $('#main [data-team-live-state]').textContent=historyState(item);
      $('#main .wi-team-heading [data-team-thoughts]').hidden=!cooperativeRuns(item).length;
      const tab=current().tab;
      const statusAction=$('#main [data-team-status-action]');
      statusAction.innerHTML=actionStatus(item,tab);
      if(tab==='trace')historyPane(item,tab);
      if(trigger)$('#main [data-team-refresh]')?.focus({preventScroll:true});
    }catch{
      if(trigger&&current().id===id&&seq===v.seq)toast('暂时无法刷新执行记录');
    }finally{
      if(trigger?.isConnected)trigger.disabled=false;
      teamRefreshing=false;
      scheduleTeamRefresh();
    }
  }
  document.addEventListener('click',event=>{
    const target=event.target.closest('[data-team-tab], [data-team-step], [data-team-run], [data-team-thoughts], [data-team-refresh], [data-team-more], [data-team-acceptance], [data-team-usage-refresh]');
    const route=current(),item=v.data?.work_items[0];
    if(!target||!route.active||route.id!==item?.id||!item?.teamwork||!$('#main').contains(target)||v.busy)return;
    if(target.hasAttribute('data-team-usage-refresh')){
      void loadTaskUsage(item.id,true);
      return;
    }
    if(target.hasAttribute('data-team-thoughts')){
      openThoughts(item,target);
      return;
    }
    if(target.hasAttribute('data-team-refresh')){
      refreshTeamHistory(target);
      return;
    }
    if(target.hasAttribute('data-team-more')){
      showMoreTrace(item,target);
      return;
    }
    if(target.hasAttribute('data-team-acceptance')){
      acceptDelivery(item);
      return;
    }
    if(target.dataset.teamTab){
      if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
      event.preventDefault();
      const tab=target.dataset.teamTab;
      if(route.tab!==tab){
        historyPane(item,tab);
        if(tab==='controls'||route.tab==='controls')load();
        $('#main').scrollTop=0;
        $('#main .wi-team-tabs [aria-current="page"]')?.focus({preventScroll:true});
      }
      return;
    }
    if(target.dataset.teamStep){
      const toggled=target.getAttribute('aria-pressed')==='true';
      Object.assign(teamHistoryFilter,{plan:item.teamwork.plan.id,step:toggled?'':target.dataset.teamStep,limit:12});
      if(route.tab==='notes')teamHistoryFilter.kind='';
      historyPane(item,'trace');
      if(route.tab==='notes')$('#main').scrollTop=0;
      $('#main [name="team-history-step"]')?.focus({preventScroll:true});
      return;
    }
    if(target.dataset.teamRun)openTraceDetails(item,target);
  });
  document.addEventListener('change',event=>{
    const route=current(),item=v.data?.work_items[0],control=event.target;
    const field={'team-history-plan':'plan','team-history-step':'step','team-history-kind':'kind'}[control.name];
    if(!field||route.tab!=='trace'||!route.active||route.id!==item?.id||!control.closest('[data-team-history-panel]'))return;
    teamHistoryFilter[field]=control.value;
    if(field==='plan')teamHistoryFilter.step='';
    teamHistoryFilter.limit=12;
    historyPane(item,'trace');
    $('#main [name="'+control.name+'"]')?.focus({preventScroll:true});
  });
  document.addEventListener('click',event=>{
    if(event.target.closest('#main [data-team-load-retry]')){
      load();
    }
  });
  document.addEventListener('toggle',event=>{
    const section=event.target, key=section.dataset?.controlDisclosure;
    if(!key||!v.controls||!$('#main').contains(section)||section.closest('[data-control-item]')?.dataset.controlItem!==v.controls.item)return;
    if(section.open){
      v.controls.open.add(key);
      if(key==='records')void loadRecordOptions();
      if(v.data?.compact_controls&&['cooperation','plan-editor','preparation'].includes(key)&&!v.loadingControls){
        captureEditors();
        void load(true);
      }
    }
    else v.controls.open.delete(key);
  },true);
  function saveTaskDraft(event){
    const form=event.target.form;
    if(current().tab!=='controls'||!form?.hasAttribute('data-task-editor-revision')||v.controls?.item!==current().id)return;
    v.controls.draft={revision:Number(form.dataset.taskEditorRevision),values:Object.fromEntries(new FormData(form))};
  }
  document.addEventListener('input',saveTaskDraft);
  document.addEventListener('change',saveTaskDraft);
  document.addEventListener('change',event=>{
    const input=event.target,item=v.data?.work_items[0];
    if(input.name!=='team-control-attempt'||current().tab!=='controls'||current().id!==item?.id||
      v.controls?.item!==item?.id||!$('#main').contains(input))return;
    v.controls.run=input.value;
    $('#main [data-control-selected-run]').innerHTML=selectedRuntimeAttempt(item);
    busy(v.busy);
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-task-draft-reset]');
    if(!button||!$('#main').contains(button)||current().tab!=='controls'||v.controls?.item!==current().id)return;
    v.controls.draft=null;
    draw();
    $('#main #work-item-edit [name="title"]')?.focus();
  });
  function independentHref(route,tab,item){
    return '#work-items/view-work/'+encodeURIComponent(route.detailId)+(tab==='overview'?'':'/'+tab)
      +(item?'/'+encodeURIComponent(item):'')+(route.origin==='records'?'/from-records':'');
  }
  function unmountCompact(){
    window.WorkdeskControl?.unmountTask?.();
    window.WorkdeskResultsPage?.unmountTask?.();
    compactSettings=null;
  }
  async function loadCompactSettings(route,detail){
    const rows=detail.workItems;
    if(!rows.length)return {markup:`<section class="wd-detail-panel"><p>此工作尚未关联任务。</p>
      <a href="#work-items">打开任务总览</a></section>`};
    const selected=route.detailItem?rows.find(item=>item.id===route.detailItem):rows[0];
    if(!selected)throw Error('这条任务与当前独立 Work 没有关联。');
    const [response,projects]=await Promise.all([S.request('work-items?'+new URLSearchParams({id:selected.id})),S.request('projects')]);
    const item=response.work_items.find(row=>row.id===selected.id);
    if(!item)throw Error('找不到已关联的任务。');
    return {item,markup:`<section class="wd-detail-panel wd-independent-settings" data-independent-item-id="${h(item.id)}">
      ${rows.length>1?`<nav class="wd-independent-settings-actions" aria-label="选择关联任务">${rows.map(row=>
        `<a href="${h(independentHref(route,'settings',row.id===item.id?route.detailItem:row.id))}"${row.id===item.id?' aria-current="page"':''}>${h(row.title)}</a>`).join('')}</nav>`:''}
      ${taskEditor(item,false,{formId:'independent-task-edit',projects:projects.projects})}
      <p data-independent-settings-result role="status" hidden></p>
      <a class="wd-independent-manage-link" href="#work-items/item/${encodeURIComponent(item.id)}/from-work/${encodeURIComponent(route.detailId)}">完整任务管理</a>
    </section>`};
  }
  async function loadCompact(c,seq){
    const kind=c.detail,id=c.detailId;
    const work=kind==='work';
    const backHref=work?(id?workConversation(id):'#work'):(id?'#chat/'+encodeURIComponent(id):'#chat');
    const fallback={kind,backHref,title:work?'独立任务详情':'对话详情'};
    const back=c.origin==='records'?{backHref:'#records',backLabel:'返回对话记录',
      ...(work&&id?{alternateBack:{label:'打开所属工作对话',href:backHref}}:{})}:{};
    const cached=compactHeading?.id===id&&compactHeading.kind===kind?compactHeading:null;
    const tabs=work&&id?Object.entries(independentTabs).map(([key,label])=>({
      href:independentHref(c,key,key==='settings'?c.detailItem:null),label,active:key===c.detailTab
    })):[];
    const render=data=>{$('#main').innerHTML=window.WorkdeskConversationDetail.render({...fallback,...cached,...data,...back,tabs});};
    const origin=location.hash;
    const active=()=>seq===v.seq&&location.hash===origin;
    $('#main').scrollTop=0;
    render({status:'正在读取已保存的记录…'});
    try{
      if(!id)throw new Error('链接缺少记录编号。');
      const adapter=work?window.WorkdeskWorkDetail:window.WorkdeskChatDetail;
      const data=await adapter.load(id,c.detailTab);
      if(!active())return;
      compactProject=data.projectId||null;
      compactHeading={id,kind,title:data.title,subtitle:data.subtitle};
      if(c.detailTab==='settings'){
        render({...data,status:'正在读取任务设置…'});
        const panel=await loadCompactSettings(c,data);
        if(!active())return;
        if(panel.item)compactSettings={item:panel.item,origin};
        render({...data,panelContent:panel.markup});
      }else if(c.detailTab==='runtime'||c.detailTab==='delivery'){
        const runtime=c.detailTab==='runtime';
        render({...data,panelContent:`<div data-independent-panel data-ui-module="${runtime?'control':'results'}"></div>`});
        const controller=runtime?window.WorkdeskControl:window.WorkdeskResultsPage;
        await controller.mountTask($('#main [data-independent-panel]'),id);
      }else{
        render(c.detailTab==='usage'?{...data,metadata:[]}:data);
      }
    }catch(error){
      if(active())render({error:error.message||'读取记录失败。'});
    }
  }
  async function load(full=false){const seq=++v.seq,c=current();try{
    unmountCompact();
    compactProject=null;
    const previous=v.data?.work_items[0];
    clearTimeout(teamRefreshTimer);
    if(c.conversation&&cooperationView?.id===c.conversation)return await setCooperationSide(cooperationView,c);
    clearTimeout(cooperationView?.timer);stopCooperationReading(cooperationView);
    $('#main').querySelectorAll('[data-cooperation-file-preview],[data-cooperation-summary-host]')
      .forEach(window.WorkdeskReact.release);
    if(c.conversation)return await openCooperation(c);
    cooperationView=null;
    if(c.detail){
      v.data=null;v.loadingControls=null;
      return await loadCompact(c,seq);
    }
    if(c.deliveryFile&&(v.deliveryPreview?.run!==c.deliveryFile.run||v.deliveryPreview?.file!==c.deliveryFile.name))
      v.deliveryPreview=null;
    const controls=!!c.id&&c.tab==='controls';
    v.loadingControls=controls?seq:null;
    if(c.id&&c.id!==previous?.id){
      v.data=null;
      $('#main').innerHTML='<section class="sp-page wi-project-loading" role="status">正在打开任务…</section>';
    }
    if(c.id===previous?.id&&previous?.teamwork?.plan){
      if(!controls&&$('#main [data-control-item]')&&!v.busy)captureEditors();
      if(controls&&!v.busy){v.error='';draw();}
    }
    const params=new URLSearchParams(c.id?{id:c.id}:c.project?{project:c.project}:{});
    let projectTasks;
    if(c.project){
      v.data=null;
    }
    if(c.project&&c.openProject){
      $('#main').innerHTML='<section class="sp-page wi-project-loading" role="status">正在打开项目…</section>';
      projectTasks=await S.request('work-items/history?'+params);
      const route=current();
      if(seq!==v.seq||route.project!==c.project||!route.openProject)return;
      if(projectTasks.open_item_id){
        const item=projectTasks.work_items.find(row=>row.id===projectTasks.open_item_id);
        location.replace(taskEntry(item||{id:projectTasks.open_item_id}));
        return;
      }
      const href=projectHref(c.project);
      history.replaceState(history.state,'',href);
      window.WorkdeskSettingsPreferences?.remember(href.slice(1));
    }
    const fullDetail=full||v.busy||controls;
    if(c.id&&(!fullDetail||(controls&&!full&&!v.busy&&previous?.id!==c.id))){
      const overview=await S.request('work-items/history?'+params);
      if(seq!==v.seq||current().id!==c.id)return;
      if(overview.work_items.length){
        v.data=overview;v.error='';draw();
        if(!controls){
          scheduleTeamRefresh();
          await openLinkedDelivery(c,seq,overview.work_items[0]);
          return;
        }
      }
    }
    const lightweight=controls&&!full&&!v.busy&&v.data?.work_items[0]?.id===c.id&&
      v.data.work_items[0].cooperation_conversation&&v.data.work_items[0].teamwork?.plan?.pdca&&
      !(v.controls?.item===c.id&&['cooperation','plan-editor','preparation'].some(key=>v.controls.open.has(key)));
    if(lightweight)params.set('view','controls');
    const [data,projects,records,resources,scenes]=await Promise.all([
      projectTasks||S.request(c.id?'work-items?'+params:c.project?'work-items/history?'+params:'work-items/history?scope=all'),
      S.request('projects'),
      c.project?S.request('record-catalog?'+new URLSearchParams({scope:'project',project:c.project,limit:'100'})):null,
      c.id?S.request('execution-resources?'+new URLSearchParams({item:c.id})):null,
      c.id&&!lightweight?S.request('scenes'):null
    ]);
    if(seq!==v.seq||current().id!==c.id||current().project!==c.project||current().tab!==c.tab||!current().active)return;
    const item=data.work_items[0];
    if(c.id&&activePreparation(item)?.record?.state==='APPLIED'&&v.teamDraft?.itemId===item.id&&!v.teamDraft.current_plan)
      v.teamDraft=null;
    v.data=data;v.resources=resources;v.presets=scenes?.presets||[];v.projects=projects.projects;v.records=records?.records||[];v.error='';
    v.available=[];
    if(v.deliveryPreview?.item!==item?.id)v.deliveryPreview=null;
    v.loadingControls=null;
    if(v.busy&&item?.id===c.id&&item?.teamwork?.plan&&!previous?.teamwork?.plan&&c.tab!=='controls'&&$('#main #work-item-edit')){
      const preparation=activePreparation(item)?.record?.state==='APPLIED';
      controlState(item).open.add(preparation?'preparation':'plan-editor');
      const href=teamworkHref(item,'controls');
      history.replaceState(history.state,'',href);
      window.WorkdeskSettingsPreferences?.remember(href.slice(1));
    }
    draw();
    const association=$('#main #work-item-link');
    if(association&&!association.closest('details:not([open])'))void loadRecordOptions();
    scheduleTeamRefresh();
    await openLinkedDelivery(c,seq,item);
  }catch(e){if(seq!==v.seq||!current().active)return;v.error=e.message;
    if(current().id===v.data?.work_items[0]?.id&&v.data)draw();
    else{v.loadingControls=null;$('#main').innerHTML=errorPage(e.message);}
  }}
  function captureEditors(){
    const form=$('#main #work-item-teamwork');
    if(v.teamDraft&&form)captureTeam(form);
    captureHandoff($('#main #work-item-handoff-revision'));
    captureHandoffText($('#main #work-item-handoff-text-rework'));
    captureHandoffUnit($('#main #work-item-handoff-unit-revision'));
    captureHandoffFile($('#main #work-item-handoff-file-rework'));
    v.fileOpen=!!$('#main [data-handoff-file-rework]')?.open;v.unitOpen=!!$('#main [data-handoff-unit-revision]')?.open;
    v.textOpen=!!$('#main [data-handoff-text-rework]')?.open;v.handoffOpen=!!$('#main [data-handoff-revision]')?.open;
  }
  async function act(fn){if(v.busy)return;captureEditors();v.busy=true;v.error='';busy(true);try{await fn();}catch(e){v.error=e.message;draw();}finally{v.busy=false;busy(false);}}
  async function readDeliveryPreview(item,unit,name){
    if(!unit?.artifact||!Object.hasOwn(unit.manifest,name))throw Error('该文件不在已保存的交付里。');
    const query={item:item.id,run:unit.run_id,version:unit.artifact.version,file:name};
    const data=await S.request('fixed-artifact-preview?'+new URLSearchParams(query));
    if(data.sha256!==unit.manifest[name].sha256)throw Error('返回的文件与交付版本不一致。');
    return {...query,query,data};
  }
  function downloadDeliveryFile(preview){
    const file=preview.data;
    S.download(Uint8Array.from(atob(file.data),c=>c.charCodeAt(0)),preview.file.split('/').at(-1),file.mime||'application/octet-stream');
  }
  async function openLinkedDelivery(route,seq,item){
    if(!route.delivery)return;
    const target=route.deliveryFile;
    if(!target){$('#main [data-fixed-delivery]')?.scrollIntoView();return;}
    const unit=item?.cooperation?.flow?.delivery?.units.find(entry=>entry.run_id===target.run);
    const preview=await readDeliveryPreview(item,unit,target.name);
    if(seq!==v.seq)return;
    v.deliveryPreview=preview;draw();
    const section=$('#main [data-delivery-preview]');
    section?.focus({preventScroll:true});
    section?.scrollIntoView({block:'start'});
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-delivery-action]');
    const route=current();
    if(!button||!route.active||v.busy||!$('#main')?.contains(button))return;
    const chat=route.conversation&&cooperationView&&activeCooperation(cooperationView)?cooperationView.data:null;
    const item=chat||v.data?.work_items[0],seq=v.seq;
    if(!item||item.id!==(route.conversation||route.id))return;
    const action=button.dataset.deliveryAction;
    if(action==='close'){
      v.deliveryPreview=null;
      if(route.deliveryFile)location.hash=teamworkHref(item,'delivery');
      else draw();
      return;
    }
    const units=chat?cooperationDeliveryUnits(item):item.cooperation?.flow?.delivery?.units;
    const unit=units?.find(entry=>entry.run_id===button.dataset.deliveryRun);
    const name=button.dataset.deliveryFile;
    if(!unit?.artifact||!Object.hasOwn(unit.manifest,name))return;
    if(action==='locate'){
      button.disabled=true;
      try{
        await api('open-project-file',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({project:item.project_id,file:name,sha256:unit.manifest[name].sha256,mode:'explorer'})});
      }catch(error){if(button.isConnected)toast(error.message);}
      finally{if(button.isConnected)button.disabled=false;}
      return;
    }
    if(chat){
      const preview=cooperationView.preview?.receipt;
      if(action==='download'&&preview?.run===unit.run_id&&preview.file===name)downloadDeliveryFile(preview);
      return;
    }
    act(async()=>{
      const preview=await readDeliveryPreview(item,unit,name);
      if(seq!==v.seq||current().id!==item.id)return;
      if(action==='download'){
        downloadDeliveryFile(preview);
      }else{
        v.deliveryPreview=preview;
        draw();
        $('#main [data-delivery-preview]')?.scrollIntoView({block:'nearest'});
      }
    });
  });
  document.addEventListener('change',event=>{
    const select=event.target.closest('[data-task-directory-filters] select');
    const results=$('#main [data-task-directory-results]');
    if(!current().active||!select||!results)return;
    taskFilters[select.name]=select.value;
    results.innerHTML=taskDirectoryRows();
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-project-create],[data-project-create-close]');
    const dialog=$('#main [data-project-create-dialog]');
    if(!button||!dialog||!current().project)return;
    if(button.hasAttribute('data-project-create'))dialog.showModal();
    else dialog.close();
  });
  async function createProjectTask(form,project){
    if(v.busy)return;
    const seq=v.seq,error=form.querySelector('[data-project-create-error]');
    const data=Object.fromEntries(new FormData(form));
    error.hidden=true;
    v.busy=true;
    busy(true);
    try{
      const saved=await S.command('work-item.create',{...data,project_id:project});
      if(seq===v.seq&&current().project===project){
        form.closest('dialog')?.close();
        location.hash=teamworkHref(saved.work_item);
      }
    }catch(e){
      if(form.isConnected){
        error.textContent=e.message;
        error.hidden=false;
      }
    }finally{
      v.busy=false;
      if(current().active)busy(false);
    }
  }
  document.addEventListener('submit',e=>{const f=e.target,c=current();if(!c.active||!['work-item-create','work-item-edit','work-item-link'].includes(f.id))return;e.preventDefault();
    if(c.project&&f.closest('[data-project-create-dialog]')){
      createProjectTask(f,c.project);
      return;
    }
    act(async()=>{
    const data=Object.fromEntries(new FormData(f)),c=current(),item=v.data.work_items[0];
    if(f.id==='work-item-create'){const r=await S.command('work-item.create',{...data,project_id:c.project});location.hash='#work-items/item/'+r.work_item.id;return;}
    if(f.id==='work-item-edit'){
      const revision=Number(f.dataset.taskEditorRevision??item.revision);
      await S.command('work-item.update',{id:item.id,revision,changes:{...data,project_id:data.project_id||null}});
      if(v.controls?.item===item.id)v.controls.draft=null;
    }
    if(f.id==='work-item-link'){const r=v.available.find(r=>r.kind+':'+r.id===data.record);if(!r)throw Error('请先选择记录。');await S.command('work-item.link',{id:item.id,revision:item.revision,kind:r.kind,record_id:r.id,record_revision:r.revision});}
    await load();await S.refreshChrome();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-item-action]');if(!el||!current().active)return;act(async()=>{const c=current(),item=c.id?v.data.work_items[0]:null;if(el.dataset.itemAction==='refresh'){await load();return;}if(['takeover','release'].includes(el.dataset.itemAction)){const action=el.dataset.itemAction,payload={id:item.id,revision:item.revision,action,...(action==='release'?{control_revision:item.runtime.manual_control.revision}:{note:'从业务任务页转人工处理'})};if(!await S.confirm(action==='takeover'?'人工接管本任务':'结束人工接管','作用于本任务及关联执行；结束接管后由你明确安排下一步。',item.title))return;await S.command('work-item.control',payload);await load();return;}if(el.dataset.itemAction==='prepare'){const r=await S.command('work-item.prepare',{id:item.id,revision:item.revision,member_id:$('#main [name=execution_member]').value||undefined});location.hash='#work/draft/'+r.draft.id;return;}const r=await S.command('work-item.chat-create',{project_id:item?.project_id||c.project,...(item?{work_item_id:item.id,revision:item.revision}:{})});await S.refreshChrome();location.hash='#chat/'+r.chat.id;});});
  document.addEventListener('submit',event=>{
    if(!current().active||event.target.id!=='work-item-host-human-response')return;
    event.preventDefault();const data=new FormData(event.target),item=v.data.work_items[0],c=item.cooperation;
    act(async()=>{await S.command('work-item.host_experts-human-response',{
      id:item.id,revision:item.revision,current_driver:item.driver.reference,
      plan:{id:item.teamwork.plan.id,version:item.teamwork.plan.version},authorization:c.authorization_reference,
      frontier:c.frontier,flow:c.flow.reference,sources:c.host_discussion.sources,note:String(data.get('note'))
    });await load();});
  });
  document.addEventListener('submit',event=>{
    if(!current().active||event.target.id!=='work-item-fixed-prepare')return;
    event.preventDefault();
    const data=new FormData(event.target),item=v.data.work_items[0];
    act(async()=>{
      const planner=resourceRef(data.get('planner'));
      if(!planner)throw Error('请选择准备成员。');
      const memberIds=data.getAll('member');
      const accessOverrides=Object.fromEntries([...new Set([planner.member_id,...memberIds])]
        .map(id=>[id,data.get('member_access_'+id)]).filter(([,access])=>access));
      const selected=[...new Set([planner.member_id,...memberIds])];
      const full=selected.filter(id=>(accessOverrides[id]||data.get('access'))==='full')
        .map(id=>v.resources.members.find(member=>member.id===id)?.name||'所选工作端');
      if(!await confirmFullAccess(full)||current().id!==item.id)return;
      await S.command('work-item.'+(['leader','host_experts'].includes(data.get('organization'))?data.get('organization'):'fixed')+'-prepare',{
        id:item.id,revision:item.revision,planner,
        members:memberIds.map(resourceRef),
        presets:data.getAll('preset').map(value=>JSON.parse(value)),
        max_attempts_total:Number(data.get('limit')),
        access:data.get('access'),
        ...(Object.keys(accessOverrides).length?{member_access:accessOverrides}:{}),
        auto_rework_checks:data.has('auto_rework_checks'),
        dispatch_before:Date.now()/1000+Number(data.get('minutes'))*60
      });
      await load();
    });
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-fixed-action]');
    if(!current().active||!button||!$('#main').contains(button))return;
    const item=v.data.work_items[0],action=button.dataset.fixedAction;
    act(async()=>{
      const preparation=activePreparation(item).record.reference;
      if(action==='continue'){
        await S.command('work-item.'+preparationMode(item)+'-prepare',{id:item.id,revision:item.revision,continue_from:preparation});
      }else{
        await S.command('work-item.'+preparationMode(item)+'-prepare-'+action,{
          id:item.id,preparation,...(action==='check'?{recheck:true}:{})
        });
      }
      await load();
    });
  });
  function resolveFixedCapability(index,selection){
    const item=v.data.work_items[0];
    return act(async()=>{
      await S.command('work-item.'+preparationMode(item)+'-capability-resolve',{
        id:item.id,preparation:activePreparation(item).record.reference,request_index:Number(index),...selection
      });
      await load();
    });
  }
  const localBundlePreviews=new WeakMap();
  function localBundlePreview(preview){
    const servers=Object.entries(preview.mcp);
    return `<section class="wi-artifacts" data-local-preview-version="${h(preview.version)}">
      <h4>${h(preview.name)} · ${preview.kind==='skill'?'Skill':preview.warnings.length?'需原生配置的插件':'声明式插件'}</h4>
      <p>来源 ${h(preview.source)}<br>目录 ${h(preview.path)}</p>
      ${preview.skills.length?`<p>Skills ${h(preview.skills.join('、'))}</p>`:''}
      ${servers.map(([name,server])=>`<p>MCP ${h(name)} · ${h(server.transport)}
        ${server.url?`<br>${h(server.url)}`:`<br>程序 ${h(server.command)}
          <br>参数 ${h(JSON.stringify(server.args))}<br>工作目录 ${h(server.cwd)}`}</p>`).join('')}
      ${servers.some(([,server])=>server.transport==='stdio')?'<p class="sp-note">预览中的包内路径来自源目录。运行时使用保存版本中的对应文件。</p>':''}
      ${preview.warnings.map(warning=>`<p>${h(warning)}</p>`).join('')}
      ${preview.warnings.length?'<p class="sp-note">此处只核对文件和原生声明。此任务可另外选择可移植的 Skill 或 MCP。</p>':''}
      <details><summary>文件清单 · ${preview.files.length} 项</summary>
        <p>保存内容指纹 ${h(preview.content_hash)}</p>
        ${preview.files.map(file=>`<p>${h(file.path)} · ${h(file.bytes)} 字节</p>`).join('')}</details>
      ${preview.warnings.length?'':S.button('确认版本并加入任务','data-local-bundle-apply')}</section>`;
  }
  function invalidateLocalBundlePreview(event){
    const form=event.target.form;
    if(form?.dataset.fixedCapability!=='import')return;
    localBundlePreviews.delete(form);
    form.querySelector('[data-local-bundle-preview]').replaceChildren();
  }
  document.addEventListener('input',invalidateLocalBundlePreview);
  document.addEventListener('change',invalidateLocalBundlePreview);
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-local-bundle-apply]');
    if(!button||!current().active||!$('#main').contains(button))return;
    const form=button.closest('form'),saved=localBundlePreviews.get(form);
    if(!saved||JSON.stringify(Object.fromEntries(new FormData(form)))!==saved.values)return;
    act(async()=>{
      await S.command('work-item.'+preparationMode(item)+'-capability-resolve',{...saved.payload,preview_version:saved.version});
      await load();
    });
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-public-skill-catalog]');
    if(!current().active||!button||!$('#main').contains(button))return;
    const form=button.parentElement.querySelector('[data-fixed-capability="public"]');
    act(async()=>{
      const {skills}=await S.request('fixed-public-skills',{});
      if(current().active&&$('#main').contains(form))publicSkillPicker(form,skills);
    });
  });
  document.addEventListener('change',event=>{
    const input=event.target,form=input.form,skills=publicSkillOptions.get(form);
    if(!current().active||!skills||input.name!=='skill'||!$('#main').contains(form))return;
    form.querySelector('[data-public-skill-description]').textContent=skills.find(skill=>skill.name===input.value)?.description||'';
  });
  document.addEventListener('submit',event=>{
    const form=event.target;
    if(!current().active||!form.dataset.fixedCapability||!$('#main').contains(form))return;
    event.preventDefault();
    const values=new FormData(form);
    if(form.dataset.fixedCapability==='public'){
      const skill=publicSkillOptions.get(form)?.find(skill=>skill.name===values.get('skill'));
      const item=v.data.work_items[0];
      act(async()=>{
        if(!skill)throw Error('请先选择一项公开 Skill。');
        if(!await S.confirm('采用 '+skill.name,
          '导入所示提交的完整 Skill，创建可选预设并绑定本任务。继续准备时沿用原有授权。',
          [skill.description,skill.url,'仓库许可证 '+skill.repository_license,...skill.files].join('\n')))return;
        await S.command('work-item.'+preparationMode(item)+'-capability-resolve',{
          id:item.id,preparation:activePreparation(item).record.reference,request_index:Number(form.dataset.index),
          public_skill:{name:skill.name,commit:skill.commit,scene:values.get('scene')}
        });
        await load();
      });
      return;
    }
    if(form.dataset.fixedCapability==='import'){
      const item=v.data.work_items[0];
      const payload={id:item.id,preparation:activePreparation(item).record.reference,
        request_index:Number(form.dataset.index),local_bundle:Object.fromEntries(values)};
      const snapshot=JSON.stringify(payload.local_bundle);
      act(async()=>{
        const {preview}=await S.request('fixed-capability-preview',payload);
        if(!form.isConnected||current().id!==item.id||JSON.stringify(Object.fromEntries(new FormData(form)))!==snapshot)return;
        localBundlePreviews.set(form,{payload,values:snapshot,version:preview.version});
        form.querySelector('[data-local-bundle-preview]').innerHTML=localBundlePreview(preview);
      });
      return;
    }
    if(!values.get('preset')){act(async()=>{throw Error('请选择一个已有预设。');});return;}
    resolveFixedCapability(form.dataset.index,{preset:JSON.parse(values.get('preset'))});
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-fixed-capability-clear]');
    if(!current().active||!button||!$('#main').contains(button))return;
    resolveFixedCapability(button.dataset.fixedCapabilityClear,{preset:null});
  });
  document.addEventListener('input',event=>{
    const input=event.target;
    if(!current().active||!input.matches('[data-fixed-capability-filter],[data-public-skill-filter]')||!$('#main').contains(input))return;
    const publicSkills=publicSkillOptions.get(input.form);
    const query=input.value.trim().toLowerCase(), select=publicSkills?input.form.elements.skill:input.form.elements.preset;
    for(const option of select.options){
      if(!option.value)continue;
      const description=publicSkills?publicSkills.find(row=>row.name===option.value)?.description:
        v.presets.find(row=>presetValue(row)===option.value)?.output;
      const searchable=[option.text,description||''].join(' ').toLowerCase();
      option.hidden=!!query&&!searchable.includes(query);
      if(option.hidden&&option.selected){select.value='';select.dispatchEvent(new Event('change',{bubbles:true}));}
    }
  });
  function runScope(index){const item=v.data.work_items[0],run=item.runtime.runs[Number(index)];if(!run?.owned)throw Error('本任务没有该次派发。');return {id:item.id,revision:item.revision,...Object.fromEntries(['dispatch_id','task_id','attempt_id','owner'].map(k=>[k,run[k]]))};}
  document.addEventListener('click',e=>{const el=e.target.closest('[data-item-run]');if(!el||!current().active)return;act(async()=>{
    const action=el.dataset.itemRun,payload={...runScope(el.dataset.runIndex),action},run=v.data.work_items[0].runtime.runs[Number(el.dataset.runIndex)];
    if(action==='native-extension-activation'){
      payload.inventory_id=el.dataset.inventoryId;payload.extension_id=el.dataset.extensionId;payload.activation=el.dataset.activation;
    }
    if(['probe','close-local'].includes(action)&&!await S.confirm(action==='probe'?'只读核对准确官方回合':'关闭本地未知回合',action==='probe'?'使用原连接核对账号及准确回合，不恢复会话、不发送模型。':'仅登记本地运行时已退出；远端未知继续保留，不解除派发保护。',run.attempt_id))return;
    if(action==='approval'){payload.approval_id=el.dataset.requestId;payload.allow=el.dataset.allow==='true';}
    if(action==='native-response'){payload.request_id=el.dataset.requestId;payload.response=el.dataset.allow!==undefined?{allow:el.dataset.allow==='true'}:{decision:el.dataset.decision};}
    if(['approval','native-response'].includes(action)&&!await S.confirm('答复这一次请求','只处理所示运行的当前请求。',JSON.stringify({request:run.pending_requests.find(q=>q.id===el.dataset.requestId),response:payload.response??payload.allow},null,2)))return;
    await S.command('work-item.control',payload);await load();await S.refreshChrome();
  });});
  document.addEventListener('submit',e=>{const form=e.target;if(!current().active||!form.matches('.wi-resume'))return;e.preventDefault();act(async()=>{
    const data=new FormData(form),run=v.data.work_items[0].runtime.runs[Number(form.dataset.runIndex)],payload={...runScope(form.dataset.runIndex),spec_hash:run.spec_hash,action:'resume',note:String(data.get('note')||''),mode:String(data.get('mode')||'native')};
    await S.command('work-item.control',payload);await load();await S.refreshChrome();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-team-action]');if(!el||!current().active||v.busy)return;
    const action=el.dataset.teamAction;if(action==='reload'){v.teamDraft=null;act(load);return;}
    const d=captureTeam($('#main #work-item-teamwork'));
    if(action==='add'&&d.units.length<20){let n=1;while(d.units.some(u=>u.key==='u'+n))n++;d.units.push({key:'u'+n,title:'',goal:'',acceptance:'',role:'execute',member_id:d.options[0]?.id||'',depends:[],write_paths:[]});}
    if(action==='remove'&&d.units.length>1){const last=d.units.pop();for(const u of d.units)u.depends=u.depends.filter(k=>k!==last.key);}
    draw();
  });
  document.addEventListener('click',e=>{const el=e.target.closest('[data-driver-action]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],d=item.driver,action=el.dataset.driverAction,row=d.record;
    const plan=action==='acquire'?{id:item.teamwork.plan.id,version:item.teamwork.plan.version}:{id:row.plan_id,version:row.plan_version};
    await S.command('work-item.driver',{id:item.id,revision:item.revision,action,current_driver:d.reference,plan});await load();
  });});
  document.addEventListener('change',event=>{
    const input=event.target;
    if(!current().active||input.form?.id!=='work-item-teamwork'||input.name!=='mode')return;
    input.form.querySelector('[data-team-fixed-access]').hidden=input.value!=='fixed';
  });
  document.addEventListener('submit',e=>{const form=e.target;if(!current().active||form.id!=='work-item-teamwork')return;e.preventDefault();act(async()=>{
    const d=captureTeam(form),ref=id=>d.options.find(m=>m.id===id)?.ref;
    await S.command('work-item.teamwork-save',{id:d.itemId,revision:d.revision,current_plan:d.current_plan,mode:d.mode,
      coordinator:ref(d.coordinator),integrator:ref(d.integrator),max_parallel:Number(d.max_parallel),
      ...(d.mode==='fixed'?{access:d.access}:{}),
      hard_cash_limit_cny:d.hard_cash_limit_cny===''?null:Number(d.hard_cash_limit_cny),
      ...(d.handoff_enabled?{file_handoffs:[{source_unit:d.handoff_source,target_unit:d.handoff_target,paths:d.handoff_paths.split('\n').map(p=>p.trim()).filter(Boolean)}]}:{}),
      units:d.units.map(u=>({key:u.key,title:u.title,goal:u.goal,acceptance:u.acceptance,role:u.role,member:ref(u.member_id),depends:u.depends,write_paths:u.write_paths,outputs:u.outputs||[],checks:u.checks||[],dependency_gate:u.dependency_gate||'result',...(u.scene_preset?{scene_preset:u.scene_preset}:{})}))});
    v.teamDraft=null;await load();
  });});
  document.addEventListener('submit',e=>{const form=e.target;if(!current().active||form.id!=='work-item-file-handoff')return;e.preventDefault();const f=new FormData(form);act(async()=>{
    const item=v.data.work_items[0],a=item.file_handoff,c=item.cooperation,source=a.sources[Number(f.get('source'))],target=a.targets.find(t=>t.key===f.get('target'));
    if(!source||!target)throw Error('请选择准确前版和后继单元。');
    await S.command('work-item.file-handoff-preview',{...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,source:source.reference,target:{key:target.key,revision:target.revision},paths:source.paths,preview:a.reference});await load();
  });});
  document.addEventListener('input',e=>{
    if(!current().active||!e.target.closest('#work-item-handoff-revision'))return;
    captureHandoff(e.target.form);v.handoffDraft.dirty=true;v.handoffDraft.touched=true;const unitApply=$('#main [data-unit-apply]');if(unitApply)unitApply.disabled=true;
    const apply=$('#main [data-handoff-revision-apply]');if(apply)apply.disabled=true;
  });
  document.addEventListener('change',e=>{
    if(!current().active||e.target.name!=='revision_unit')return;
    v.handoffOpen=!!$('#main [data-handoff-revision]')?.open;v.textOpen=!!$('#main [data-handoff-text-rework]')?.open;
    if(v.handoffDraft&&!sameHandoff(v.handoffDraft.scope,handoffTextScope(v.data.work_items[0]))){
      v.error='原职责版本已变化，请重新读取原职责。';draw();return;
    }
    handoffDraft(v.data.work_items[0],e.target.value);v.handoffDraft.touched=true;draw();
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-handoff-revision-reload]'))return;
    act(async()=>{v.handoffDraft=null;await load();});
  });
  document.addEventListener('submit',e=>{
    if(!current().active||e.target.id!=='work-item-handoff-revision')return;e.preventDefault();
    act(async()=>{
      const d=v.handoffDraft;
      const receipt=await S.command('work-item.file-handoff-revision-check',{...d.scope,
        changes:[{operation:'revise',unit_key:d.key,basis:{unit_key:d.key,unit_revision:d.original.revision,
          work_requirements_version:d.requirements,reason:d.basis_reason},
          definition:{title:d.title,goal:d.goal,acceptance:d.acceptance,depends:d.depends,member:d.original.member}}],reason:d.reason});
      d.checkedReference=receipt.candidate;d.dirty=false;await load();
    });
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-handoff-revision-apply]'))return;
    act(async()=>{
      const item=v.data.work_items[0],r=item.file_handoff_revision,c=item.cooperation;
      if(r?.candidate_type!=='DEFINITION_REVISION'||!r.can_apply||v.handoffDraft?.dirty||!sameHandoff(v.handoffDraft?.checkedReference,r.reference))return;
      const p=r.record.snapshot.preview;
      const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,candidate:r.reference};
      if(!await S.confirm('应用局部文字候选','保存所示新方案并暂停原合作责任，保留原累计上限、截止和权限。明确恢复后再执行；本次不发送模型。',
        JSON.stringify({reason:p.reason,changes:p.direct_changes,affected:p.affected_units,candidate:r.reference},null,2)))return;
      await S.command('work-item.file-handoff-revision-apply',payload);v.teamDraft=null;v.handoffDraft=null;v.textDraft=null;v.unitDraft=null;v.fileDraft=null;await load();
    });
  });
  document.addEventListener('input',e=>{
    if(!current().active||!e.target.closest('#work-item-handoff-text-rework'))return;
    captureHandoffText(e.target.form);v.textDraft.dirty=true;v.textDraft.touched=true;const unitApply=$('#main [data-unit-apply]');if(unitApply)unitApply.disabled=true;
    const apply=$('#main [data-text-rework-apply]');if(apply)apply.disabled=true;
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-text-rework-reload]'))return;
    act(async()=>{v.textDraft=null;await load();});
  });
  document.addEventListener('submit',e=>{
    if(!current().active||e.target.id!=='work-item-handoff-text-rework')return;e.preventDefault();
    act(async()=>{
      const d=v.textDraft,item=v.data.work_items[0];
      if(!sameHandoff(d.scope,handoffTextScope(item)))throw Error('准确来源已变化，请明确重新读取。');
      const receipt=await S.command('work-item.file-handoff-text-rework-check',{...d.scope,selections:handoffTextSelections(d),reason:d.reason});
      d.checkedReference=receipt.candidate;d.dirty=false;d.touched=true;await load();
    });
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-text-rework-apply]'))return;
    act(async()=>{
      const item=v.data.work_items[0],r=item.file_handoff_revision,d=v.textDraft;
      if(r?.candidate_type!=='TEXT_REWORK'||!r.can_apply||!d||d.dirty||
          !sameHandoff(d.scope,handoffTextScope(item))||!sameHandoff(d.checkedReference,r.reference))return;
      const p=r.record.snapshot.preview,payload={...d.scope,candidate:r.reference};
      if(!await S.confirm('应用文字返工候选','保存所示新方案并暂停合作责任。原工作定义、成员、累计上限、截止和权限保持；明确恢复后再执行。',
        JSON.stringify({reason:p.reason,selections:p.selections.map(u=>({unit_key:u.unit_key,source_run_id:u.source_run_id,source_version:u.source_version})),
          missing_result_seeds:p.units.filter(u=>u.reasons.includes('KNOWN_TERMINAL_WITHOUT_RESULT')).map(u=>u.key),affected:p.affected_units,candidate:r.reference},null,2)))return;
      await S.command('work-item.file-handoff-text-rework-apply',payload);v.teamDraft=null;v.handoffDraft=null;v.textDraft=null;v.unitDraft=null;v.fileDraft=null;await load();
    });
  });

  document.addEventListener('input',e=>{
    if(!current().active||!e.target.closest('#work-item-handoff-file-rework'))return;
    captureHandoffFile(e.target.form);v.fileDraft.dirty=true;v.fileDraft.touched=true;
    for(const selector of ['[data-handoff-file-rework-apply]','[data-unit-apply]']){const b=$('#main '+selector);if(b)b.disabled=true;}
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-file-rework-reload]'))return;
    act(async()=>{v.fileDraft=null;await load();});
  });
  document.addEventListener('submit',e=>{
    if(!current().active||e.target.id!=='work-item-handoff-file-rework')return;e.preventDefault();
    act(async()=>{const d=v.fileDraft,item=v.data.work_items[0];
      if(!sameHandoff(d.scope,handoffFileScope(item)))throw Error('文件来源与材料已变化，请明确重新读取。');
      const request=handoffFileRequest(d),receipt=await S.command('work-item.file-handoff-file-rework-check',request);
      d.checkedReference=receipt.candidate;d.checkedRequest=structuredClone(request);d.dirty=false;d.touched=true;await load();});
  });
  document.addEventListener('click',e=>{
    if(!current().active||!e.target.closest('[data-handoff-file-rework-apply]'))return;
    act(async()=>{const item=v.data.work_items[0],r=item.file_handoff_revision,d=v.fileDraft;
      if(r?.candidate_type!=='FILE_REWORK'||!r.can_apply||!d||d.dirty||
        [v.handoffDraft,v.textDraft,v.unitDraft].some(x=>x?.touched&&x.dirty)||
        !sameHandoff(d.scope,handoffFileScope(item))||!sameHandoff(d.checkedReference,r.reference)||!sameUnitRequest(d.checkedRequest,r.record.snapshot.preview.request))return;
      const p=r.record.snapshot.preview,{material,...scope}=d.scope,payload={...scope,candidate:r.reference};
      if(!await S.confirm('应用文件返工候选','保存新方案并暂停合作责任，保留原完整定义、成员、路径、累计授权和截止。明确恢复后取得新运行并捕获文件。',
        JSON.stringify({reason:p.reason,selected:p.selections.map(u=>u.unit_key),affected:p.affected_units,write_scope:p.write_scope,candidate:r.reference},null,2)))return;
      await S.command('work-item.file-handoff-file-rework-apply',payload);v.teamDraft=null;v.handoffDraft=null;v.textDraft=null;v.unitDraft=null;v.fileDraft=null;await load();});
  });

  function cooperationScope(){const item=v.data.work_items[0];return {id:item.id,revision:item.revision,current_driver:item.driver.reference,plan:{id:item.teamwork.plan.id,version:item.teamwork.plan.version}};}
  document.addEventListener('submit',e=>{const form=e.target;if(!current().active||form.id!=='work-item-cooperation-auth')return;e.preventDefault();act(async()=>{
    const data=new FormData(form),c=v.data.work_items[0].cooperation;
    await S.command('work-item.cooperation-authorize',{...cooperationScope(),authorization:c.authorization_reference,max_attempts_total:Number(data.get('max_attempts_total')),dispatch_before:Date.now()/1000+Number(data.get('minutes'))*60,...(c.continuous_supported?{auto_rework_checks:data.has('auto_rework_checks')}:{})});await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-coop-send]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],c=item.cooperation,u=c.units[Number(el.dataset.coopSend)],retry=el.dataset.coopRetry==='true';
    const scope=cooperationScope();
    if(!await confirmFullAccess(frozenFullAccess(item,u.key))||current().id!==item.id)return;
    if(c.handoff_stage?.saved_v3){await sendV3Successor(item,u.key);return;}
    if(retry&&!await S.confirm('明确重试本单元','新增尝试继续累计；本次使用当前前置准确结果，不重发未知回合。',u.title+' / '+u.latest_run_id))return;
    if(current().id!==item.id)return;
    await S.command('work-item.unit-send',{...scope,authorization:c.authorization_reference,frontier:c.frontier,unit:{key:u.key,revision:u.revision},...(retry?{retry_of:u.latest_run_id}:{}),...(item.teamwork.plan.mode!=='fixed'&&u.role!=='coordinate'?{proposal:c.proposal.reference}:{})});v.teamDraft=null;await load();await S.refreshChrome();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-flow-action]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],c=item.cooperation,scope=cooperationScope(),action=el.dataset.flowAction;
    if(action==='start'&&(!await confirmFullAccess(frozenFullAccess(item))||current().id!==item.id))return;
    await S.command('work-item.flow',{...scope,authorization:c.authorization_reference,frontier:c.frontier,flow:c.flow.reference,action});await load();
  });});
  document.addEventListener('submit',e=>{
    if(!current().active||e.target.id!=='work-item-flow-schedule')return;
    e.preventDefault();
    const startAt=new Date(new FormData(e.target).get('start_at')).getTime()/1000;
    act(async()=>{
      if(!Number.isFinite(startAt))throw Error('请选择启动时间。');
      const item=v.data.work_items[0],c=item.cooperation,scope=cooperationScope();
      if(!await confirmFullAccess(frozenFullAccess(item))||current().id!==item.id)return;
      await S.command('work-item.flow',{...scope,authorization:c.authorization_reference,
        frontier:c.frontier,flow:c.flow.reference,action:'schedule',start_at:startAt});
      await load();
    });
  });
  document.addEventListener('click',e=>{const el=e.target.closest('[data-v3-responsibility-action],[data-v3-responsibility-replay]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],key=responsibilityKey(item),samePage=()=>current().active&&current().id===item.id&&v.data?.work_items[0]?.id===item.id;
    let command=responsibilityPending(item);if(command?.invalid)throw Error('原责任请求记录损坏，请保留记录核对。');
    if(!command){if(el.hasAttribute('data-v3-responsibility-replay'))return;const a=item.cooperation?.handoff_stage?.v3_responsibility?.actions[el.dataset.v3ResponsibilityAction];if(!a?.enabled||!a.request)return;command={id:crypto.randomUUID(),kind:a.kind,payload:structuredClone(a.request)};sessionStorage.setItem(key,JSON.stringify(command));}
    const raw=sessionStorage.getItem(key);v.responsibilityRecovery=null;
    try{
      const result=await S.request('command',command);
      if(result.command_id!==command.id||!result.receipt)throw Error('原责任命令回执身份不符或不完整');
      // Only the complete committed Current ledger can clear this pending UUID.
      // A save recovery response or a shape-only success cannot establish it.
      const data=await S.request('work-items?'+new URLSearchParams({id:item.id})),detail=data.work_items?.[0],r=detail?.cooperation?.handoff_stage?.v3_responsibility;
      const entry=r?.entries.find(e=>samePostSave(e.command,command));
      if(detail?.id!==item.id||r?.full_current_tree_verified!==true||!entry||!samePostSave(entry.receipt,result.receipt))throw Error('原责任完整提交回执尚未核对');
      if(sessionStorage.getItem(key)!==raw)throw Error('本页待核对的原责任请求已变化');
      sessionStorage.setItem(key+':receipt',JSON.stringify({command,receipt:entry.receipt,checked_at:Date.now()}));sessionStorage.removeItem(key);sessionStorage.removeItem(key+':refusal');
      if(samePage()){++v.seq;v.data=data;v.teamDraft=null;draw();}
    }catch(error){sessionStorage.setItem(key+':refusal',JSON.stringify({command,message:error.message}));if(samePage())throw Error(error.message+'；原责任命令 '+command.id+' 已保留，请显式重放核对。');}
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-v3-responsibility-inspect],[data-v3-responsibility-clear]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],key=responsibilityKey(item),command=responsibilityPending(item),raw=sessionStorage.getItem(key);
    if(!command||command.invalid)throw Error('请保留原责任请求核对。');
    const unchanged=()=>current().active&&current().id===item.id&&v.data?.work_items[0]?.id===item.id&&sessionStorage.getItem(key)===raw;
    const inspect=async()=>responsibilityProof(await S.request('post-round-v3-responsibility-recovery',command),command);
    if(el.hasAttribute('data-v3-responsibility-inspect')){const proof=await inspect();if(unchanged()){v.responsibilityRecovery=proof;draw();}return;}
    const proof=v.responsibilityRecovery;if(!proof?.can_clear_local||!samePostSave(proof.command,command))return;
    if(!await S.confirm('清除本页已核对责任请求','保留原请求、拒绝信息与两次完整核对证明。下一次责任操作仍需明确点击。',JSON.stringify(proof,null,2)))return;
    if(!unchanged())throw Error('页面或原责任请求已变化，请重新核对。');
    const checked=await inspect();if(!unchanged())throw Error('页面或原责任请求已变化，原请求已保留。');
    v.responsibilityRecovery=checked;
    if(!checked.can_clear_local||checked.state!==proof.state||!samePostSave(checked.current,proof.current)||!samePostSave(checked.receipt,proof.receipt))throw Error('当前责任清除条件已变化，原请求已保留。');
    const log=sessionStorage.getItem(key+':cleared-log')===null?[]:postSaveRecord(key+':cleared-log');if(!Array.isArray(log))throw Error('本页清除日志损坏，原请求已保留。');
    sessionStorage.setItem(key+':cleared-log',JSON.stringify([...(log||[]),{command,refusal:postSaveRecord(key+':refusal'),proof,checked,cleared_at:Date.now()}]));
    if(checked.state==='RECORDED')sessionStorage.setItem(key+':receipt',JSON.stringify({command,receipt:checked.receipt,checked_at:Date.now()}));
    sessionStorage.removeItem(key);sessionStorage.removeItem(key+':refusal');v.responsibilityRecovery=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-post-round-v3-save-action],[data-post-round-v3-reconcile]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],action=item.cooperation.post_round_v3_save;
    let command=postSavePending(item);if(command?.invalid)throw Error('保存核对记录损坏，请核对原命令。');
    if(!command){if(!action?.can_save||!action.request)return;command={id:crypto.randomUUID(),kind:action.kind,payload:structuredClone(action.request)};sessionStorage.setItem(postSaveKey(item),JSON.stringify(command));}v.postSaveRecovery=null;
    try{const result=await S.request('command',command);if(result.command_id!==command.id||!result.receipt?.application||!result.receipt?.registration||!result.receipt?.child_plan||!result.receipt?.authorization||result.receipt?.driver?.state!=='PAUSED'||Object.keys(result.receipt).sort().join(',')!==['application','registration','child_plan','driver','authorization','no_dispatch','automatically_resumed'].sort().join(',')||result.receipt.no_dispatch!==true||result.receipt.automatically_resumed!==false)throw Error('保存回执不完整');sessionStorage.removeItem(postSaveKey(item));v.teamDraft=null;await load();}
    catch(error){sessionStorage.setItem(postSaveRefusalKey(item),JSON.stringify({command,message:error.message}));throw Error(error.message+'；原保存命令 '+command.id+' 已保留，请明确核对原保存回执。');}
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-post-round-v3-inspect],[data-post-round-v3-clear]');if(!el||!current().active)return;act(async()=>{
    const item=v.data.work_items[0],command=postSavePending(item),raw=sessionStorage.getItem(postSaveKey(item));
    if(!command||command.invalid)throw Error('请保留原保存请求核对。');
    const unchanged=()=>current().active&&current().id===item.id&&sessionStorage.getItem(postSaveKey(item))===raw;
    const inspect=async()=>postSaveRecoveryProof(await S.request('post-round-v3-save-recovery',command),command);
    if(el.hasAttribute('data-post-round-v3-inspect')){const proof=await inspect();if(unchanged()){v.postSaveRecovery=proof;draw();}return;}
    const proof=v.postSaveRecovery;if(!proof?.can_clear_local||!samePostSave(proof.command,command))return;
    if(!await S.confirm('清除本页失效请求','仅清除本标签页待核对记录，保留原请求和核对证据。下一次保存仍需你明确点击。',JSON.stringify(proof,null,2)))return;
    const checked=await inspect();if(!unchanged())throw Error('本页待核对请求已变化，请重新核对。');
    v.postSaveRecovery=checked;
    if(!checked.can_clear_local||!samePostSave(checked.current_request,proof.current_request))throw Error('当前清除条件已变化，原保存请求已保留。');
    sessionStorage.setItem(postSaveProofKey(item),JSON.stringify({command,refusal:postSaveRecord(postSaveRefusalKey(item)),checked,cleared_at:Date.now()}));
    sessionStorage.removeItem(postSaveKey(item));sessionStorage.removeItem(postSaveRefusalKey(item));v.postSaveRecovery=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-proposal-apply]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation,scope=cooperationScope(),payload={...scope,authorization:c.authorization_reference,frontier:c.frontier,proposal:c.proposal.reference};
    if(!await S.confirm('应用依赖调整','保存新方案并暂停合作责任；原成果来源、累计次数及原截止继续保留，不发送模型。继续前须明确恢复合作责任。','受影响工作：'+(c.proposal.record.snapshot.adjustment.affected_keys||c.proposal.record.snapshot.adjustment.affected_units).join('、')))return;
    await S.command('work-item.proposal-apply',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-file-rework-apply]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation;if(!c.proposal.can_apply_file_rework)return;
    const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,proposal:c.proposal.reference};
    await S.command('work-item.file-rework-apply',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-unit-revision-apply]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation,payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,proposal:c.proposal.reference};
    await S.command('work-item.unit-revision-apply',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-rework-apply]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation;if(!c.proposal.can_apply_rework)return;
    const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,proposal:c.proposal.reference};
    if(!await S.confirm('应用文字返工','保存新方案并暂停责任，保留原失败来源、累计上限和截止。恢复后须按新方案提案执行，当前不发送模型。','受影响工作：'+c.proposal.record.snapshot.rework.affected_units.join('、')))return;
    await S.command('work-item.rework-apply',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('submit',e=>{if(e.target.id!=='work-item-coordination-revision'||!current().active)return;e.preventDefault();const reason=new FormData(e.target).get('reason');act(async()=>{
    const c=v.data.work_items[0].cooperation,r=c.coordination_revision;if(!r?.can_revise)return;
    const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,run_id:r.source.run_id,result_version:r.source.version,reason};
    if(!await S.confirm('登记新提案回合','保存新方案并暂停责任；原回复与普通成果来源保留，继续使用原累计上限及截止，不发送模型。','原尝试：'+r.source.attempt_id))return;
    await S.command('work-item.coordination-revise',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('submit',e=>{if(e.target.id!=='work-item-rework-diagnostic'||!current().active)return;e.preventDefault();const reason=new FormData(e.target).get('reason');act(async()=>{
    const c=v.data.work_items[0].cooperation,r=c.rework_diagnostic;if(!r?.can_register)return;
    const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,diagnostic_version:r.version,reason};
    if(!await S.confirm('登记文字诊断回合','保存新方案并暂停责任；保留准确原失败，沿用原累计上限和截止。登记不发送模型。','诊断单元：'+r.snapshot.unit_key))return;
    await S.command('work-item.rework-diagnose',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('submit',e=>{if(e.target.id!=='work-item-file-rework-diagnostic'||!current().active)return;e.preventDefault();const reason=new FormData(e.target).get('reason');act(async()=>{
    const c=v.data.work_items[0].cooperation,r=c.file_rework_diagnostic;if(!r?.can_register)return;
    const payload={...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,diagnostic_version:r.version,reason};
    if(!await S.confirm('登记文件诊断回合','核对原输入和全部旧快照，保存新方案并暂停责任；沿用原累计上限和截止。登记不发送模型。','诊断单元：'+r.snapshot.unit_key))return;
    await S.command('work-item.file-rework-diagnose',payload);v.teamDraft=null;await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-proposal-check]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation,u=c.units[Number(el.dataset.proposalCheck)];
    if(c.handoff_stage?.saved_v3){await checkV3Proposal(v.data.work_items[0]);return;}
    await S.command('work-item.proposal-check',{...cooperationScope(),authorization:c.authorization_reference,frontier:c.frontier,run_id:u.latest_run_id,result_version:u.result.version,proposal:c.proposal.reference});await load();
  });});
  document.addEventListener('click',e=>{const el=e.target.closest('[data-artifact-check]');if(!el||!current().active)return;act(async()=>{
    const c=v.data.work_items[0].cooperation,u=c.units[Number(el.dataset.artifactCheck)];
    await S.command('work-item.unit-check',{...cooperationScope(),run_id:u.latest_run_id,result_version:u.capture_result_version||u.result.result_version,artifact:u.result.file_artifacts.reference});await load();
  });});
  WorkdeskModules.onLeave('workitems',()=>{
    unmountCompact();
    $('#main')?.querySelectorAll('.wi-team-view dialog[open],[data-project-create-dialog][open]').forEach(dialog=>dialog.close());
    clearTimeout(teamRefreshTimer);++v.seq;v.data=null;v.loadingControls=null;v.teamDraft=null;v.handoffDraft=null;
    $('#main')?.querySelectorAll('[data-cooperation-file-preview],[data-cooperation-summary-host]')
      .forEach(window.WorkdeskReact.release);
    clearTimeout(cooperationView?.timer);stopCooperationReading(cooperationView);cooperationView=null;
    v.handoffOpen=false;v.textDraft=null;v.textOpen=false;v.unitDraft=null;v.unitOpen=false;
    v.fileDraft=null;v.fileOpen=false;v.controls=null;v.error='';
    v.taskUsage=null;compactProject=null;compactHeading=null;
  });
  $('#main').addEventListener('submit',async event=>{
    const form=event.target,settings=compactSettings;
    if(form.id!=='independent-task-edit'||!settings||settings.origin!==location.hash)return;
    event.preventDefault();
    if(settings.saving)return;
    const changes=Object.fromEntries(new FormData(form));
    const button=form.querySelector('[type="submit"]');
    const status=$('#main [data-independent-settings-result]');
    settings.saving=true;button.disabled=true;status.hidden=true;
    try{
      const receipt=await S.command('work-item.update',{id:settings.item.id,revision:settings.item.revision,
        changes:{...changes,project_id:changes.project_id||null}});
      if(compactSettings!==settings||settings.origin!==location.hash)return;
      settings.item=receipt.work_item;
      status.textContent='任务设置已保存。';
      S.refreshChrome().catch(()=>{});
    }catch(error){
      status.textContent=error.message;
    }finally{
      if(compactSettings===settings){settings.saving=false;button.disabled=false;status.hidden=false;}
    }
  });
  $('#main').addEventListener('click',event=>{
    if(current().detail&&event.target.closest('[data-conversation-detail-retry]'))
      void load();
  });
  window.WorkdeskWorkItemsPage={render:load,projectId:()=>{
    const route=current(),item=v.data?.work_items[0];
    if(!route.active)return null;
    if(route.detail)return compactProject;
    return route.project||(route.id===item?.id?item?.project_id:null)||
      (route.conversation===cooperationView?.id?cooperationView?.data?.project_id:null);
  }};
})();
