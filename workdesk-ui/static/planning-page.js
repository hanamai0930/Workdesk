(()=>{
  'use strict';const $=WorkdeskModules.dom('planning'),S=WorkdeskSupport,{h}=S;
  const v={data:null,error:'',busy:false,seq:0,timer:null,forms:{},open:{}};const active=()=>location.hash.split('/')[0]==='#planning';
  const state=r=>({RUNNING:'自动运行中',PAUSED:'已暂停',CANCELLED:'已取消',COMPLETE:'已结束',MODEL_COMPLETE_REVIEW:'模型报告完成，待验收',ACCEPTED:'Human 已验收'}[r.state]||r.state);
  function planSection(key,title,description,records,action,empty){
    return `<section class="wd-plan-section" aria-labelledby="wd-plan-${key}">
      <header><h2 id="wd-plan-${key}">${title}</h2><p>${description}</p></header>
      <div class="wd-plan-records">${records||`<p class="wd-plan-empty">${empty}</p>`}</div>
      ${action}
    </section>`;
  }
  function fixedSchedules(rows){
    const labels={SCHEDULED:'等待定时启动',RUNNING:'固定流程运行中',PAUSED:'已暂停',RESULTS_AVAILABLE:'成果已登记'};
    const cards=rows.map(row=>`<article class="sp-card" data-fixed-schedule="${h(row.state)}">
      <div class="sp-row"><h3>${h(row.title)}</h3><span class="sp-badge">${h(labels[row.state]||row.state)}</span></div>
      <p>预约时间 ${h(S.when(row.start_at))}</p><p>${h(row.reason)}</p>
      <a href="#work-items/item/${h(row.item_id)}${row.state==='RESULTS_AVAILABLE'?'/delivery':''}">${row.state==='RESULTS_AVAILABLE'?'查看固定交付':'打开原固定任务'}</a></article>`);
    return planSection('fixed','固定流程预约','给已授权的固定合作任务约一个时间，到点启动一次。',
      cards.join(''),(v.data.fixed_candidates||[]).length
        ? '<p>可预约的任务</p>'+v.data.fixed_candidates.map(item=>`<a class="wd-plan-link" href="#work-items/item/${encodeURIComponent(item.id)}/controls">${h(item.title)} · 设置预约</a>`).join(' ')
        : '<p class="sp-note">暂无可预约任务。请先准备固定方案、取得推进责任并保存派发授权。</p><a class="wd-plan-link" href="#work-items">查看任务</a>','暂无预约');
  }
  function cooperativePlans(){
    const jobs=v.data.cooperative_jobs||[],candidates=v.data.cooperative_candidates||[];
    const chosen=candidates.find(c=>c.id===v.forms['planning-cooperation']?.item_id);
    const cards=jobs.map(r=>`<article class="sp-card" data-cooperative-job="${h(r.state)}"><div class="sp-row"><h3>${h(r.title)}</h3><span class="sp-badge">${h(r.state==='RUNNING'?'已启用':state(r))}</span></div><p>${h(r.reason)}</p><p>${h({daily:'每天',weekly:'每周'}[r.frequency])} · ${h(r.timezone)} · 下次 ${h(S.when(r.due))} · 已交付 ${r.cycles.length}/${r.max_cycles} 个周期</p><p class="sp-note">全部周期累计最多 ${r.limits.max_attempts_total} 次尝试，原授权截止 ${h(S.when(r.limits.dispatch_before))}。</p><a href="#work-items/item/${h(r.item_id)}">打开原合作任务</a>${r.cycles.length?`<details><summary>各周期成果与版本</summary>${r.cycles.map(c=>`<p>第 ${c.index} 周期 · ${h(S.when(c.finished))} · ${c.delivery.length} 项结果</p>${S.raw({plan:c.plan,driver_generation:c.driver_generation,delivery:c.delivery})}`).join('')}</details>`:''}<div class="sp-actions">${!['COMPLETE','CANCELLED'].includes(r.state)?S.button(r.state==='RUNNING'?'暂停合作后续派发':'继续合作重复计划',`data-plan="cooperative-state" data-id="${h(r.id)}" data-state="${r.state==='RUNNING'?'PAUSED':'RUNNING'}"`)+S.button('取消合作重复计划',`data-plan="cooperative-state" data-id="${h(r.id)}" data-state="CANCELLED"`):''}</div></article>`).join('');
    const form=`<details class="wd-plan-create"><summary>新建合作重复计划</summary><form id="planning-cooperation">${S.select('合作任务','item_id',[['','选择已授权且就绪的固定任务'],...candidates.map(c=>[c.id,c.title])],'')}<p class="sp-note" data-cooperative-limits>${h(chosen?`原授权累计最多 ${chosen.max_attempts_total} 次尝试，截止 ${S.when(chosen.dispatch_before)}`:'选择任务后显示原授权累计上限与截止。')}</p>${S.select('重复方式','frequency',[['daily','每天'],['weekly','每周']],'daily')}${S.field('首次执行时间','local_time','','type="datetime-local" required')}${S.field('时区','timezone',Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai','required')}${S.field('最多交付周期','max_cycles','30','type="number" min="1" max="1000"')}<label><input type="checkbox" name="long_term" value="true"> 为本计划明确登记长期派发授权（最长365天）</label>${S.field('长期授权的最初截止（可选，按上方时区）','dispatch_before_local','','type="datetime-local"')}<p class="sp-note">先在合作任务中准备普通固定方案、取得推进责任并授权。本计划将重复同一方案，每周期保存新的方案、唯一责任和成果版本，保留同一业务任务。默认保留原授权。勾选长期授权并填写截止后，会明确登记新截止；累计次数沿用原值，不重置，每个周期保留这次最初截止。到期暂停，不自动续期。暂停仍保留计划，取消后可新建。服务需运行，错过不补发，重启和恢复后显式继续。</p><button type="submit">保存并启用合作重复计划</button></form></details>`;
    return planSection('cooperation','合作重复计划','每天或每周重跑同一套已授权的固定合作方案，每次分别保存成果。',
      cards,form,'暂无重复计划');
  }
  function goalControls(r){
    if(['MODEL_COMPLETE_REVIEW','COMPLETE'].includes(r.state))return `<div class="sp-actions">${S.button('确认验收',`data-plan="goal-review" data-id="${h(r.id)}" data-accepted="true"`)}${S.button('退回继续处理',`data-plan="goal-review" data-id="${h(r.id)}" data-accepted="false"`)}</div>`;
    if(['ACCEPTED','CANCELLED'].includes(r.state))return '';
    return `<details><summary>目标阻碍</summary>${S.text('当前阻碍','blocker',r.blocker||'','maxlength="1000"')}${S.button('保存阻碍并暂停',`data-plan="goal-blocker" data-id="${h(r.id)}"`)}${r.blocker?S.button('清除阻碍',`data-plan="goal-blocker" data-clear="true" data-id="${h(r.id)}"`):''}</details>`;
  }
  function card(r,kind){return `<article class="sp-card" data-plan-record="${h(r.id)}" tabindex="-1"><div class="sp-row"><h3>${h(r.title)}</h3><span class="sp-badge">${kind==='scheduled_job'?({COMPLETE:'计划已派发完',RUNNING:'已启用'})[r.state]||state(r):state(r)}</span></div><p>${h(r.reason||'')}</p>${kind==='goal_job'?`<p class="sp-note">${h(r.provider)} · ${h(r.config.model)} · ${r.access==='full'?'完全访问权限':'请求批准'}</p>`:''}<p class="sp-note">${kind==='goal_job'?`已派发 ${r.rounds.length} 回合 · 上限 ${r.max_rounds||'持续到完成'}`:`${h({once:'一次',daily:'每天',weekly:'每周'}[r.frequency])} · ${h(r.timezone)} · 下次 ${h(S.when(r.due))} · 已派发 ${r.runs.length}/${r.max_runs} 次`}</p>${kind==='goal_job'?`<details><summary>目标与完成依据</summary><p>${h(r.goal)}</p><p>${h(r.criteria)}</p>${S.raw(r.last_check||r.checks)}${r.dispatch_before?'<p>派发截止 '+h(S.when(r.dispatch_before))+'</p>':''}${r.origin_command_id?'<p>创建命令 '+h(r.origin_command_id)+'</p>':''}${r.source_handoff_id?'<p>接续材料 '+h(r.source_handoff_id)+'</p>':''}${r.result_id?'<p>待核成果版本 '+h(r.result_id)+'</p>':''}${r.task_usage?'<p>原任务累计已记录模型请求 '+h(r.task_usage.model_requests)+' 次 · 已知输入 / 输出 Token '+h(r.task_usage.api_known_input_tokens)+' / '+h(r.task_usage.api_known_output_tokens)+' · 缺少 Token 回执 '+h(r.task_usage.api_requests_without_token_receipt)+' 次 · 未知回执 '+h(r.task_usage.unknown_requests)+' 次</p>':''}${r.rounds.length?S.raw(r.rounds):''}</details>${goalControls(r)}`:''}<div class="sp-actions">${r.task_id?`<a href="#work/task/${h(r.task_id)}">查看工作</a><a href="#results/${h(r.task_id)}">成果与验收</a>`:''}${!['COMPLETE','MODEL_COMPLETE_REVIEW','CANCELLED','ACCEPTED'].includes(r.state)?S.button(r.state==='RUNNING'?'暂停后续派发':'启动 / 继续',`data-plan="state" data-kind="${kind}" data-id="${h(r.id)}" data-state="${r.state==='RUNNING'?'PAUSED':'RUNNING'}"`)+S.button('取消计划',`data-plan="state" data-kind="${kind}" data-id="${h(r.id)}" data-state="CANCELLED"`):''}</div></article>`;}
  function automaticGoals(){
    const provider=v.forms['planning-goal']?.provider||'codex',connection=v.forms['planning-goal']?.work_connection||'';
    const metadata=v.data.providers.find(row=>row.id===provider),connections=(v.data.partner_connections||[]).filter(row=>row.partner_id===provider);
    const connectionChoices=[['','请选择准确连接'],...(metadata?.legacy_auto_goal_supported?[['legacy','旧工作端默认配置']]:[]),...connections.map(row=>[row.id,(row.model_label||row.model||'官方默认模型')+' · '+row.name])];
    const selected=connections.find(row=>row.id===connection),allowed=!Array.isArray(v.data.partner_connections)||connection==='legacy'&&metadata?.legacy_auto_goal_supported||selected?.auto_goal_supported===true;
    const form=`<details class="wd-plan-create"><summary>新建自动目标</summary><form id="planning-goal">${S.field('目标名称','title','','required maxlength="160"')}${S.select('项目工作区','project_id',v.data.projects.map(p=>[p.id,p.name]),v.data.projects[0]?.id)}${S.select('官方工作端','provider',v.data.providers.map(p=>[p.id,p.name]),'codex')}${Array.isArray(v.data.partner_connections)?S.select('工作连接','work_connection',[['','请选择准确连接']],'')+'<p class="sp-note" data-goal-connection-note></p>':''}${S.select('访问权限','access',[['workspace','请求批准'],['full','完全访问权限']],'workspace')}${S.text('要完成的目标','goal','','required')}${S.text('完成标准','criteria','','required')}${S.field('完成文件（可选，项目相对路径）','file')}${S.field('完成文件应包含的文字（可选）','contains')}${S.field('派发截止（可选，本机时间）','dispatch_before_local','','type="datetime-local"')}${S.field('项目接续材料标识（可选）','source_handoff_id')}${S.field('最多自动回合（0 表示持续到完成）','max_rounds','20','type="number" min="0" max="1000"')}<p class="sp-note">沿用所选工作端当前模型和思考配置。先保存目标，再显式启动；失败、未知和手动接续造成的版本变化会暂停。模型报告完成会停止自动派发，是否验收通过由你决定。</p><button type="submit">保存自动目标</button></form></details>`;
    const accurateForm=form.replace(S.select('官方工作端','provider',v.data.providers.map(p=>[p.id,p.name]),'codex'),S.select('官方工作端','provider',v.data.providers.map(p=>[p.id,p.name]),provider))
      .replace(S.select('工作连接','work_connection',[['','请选择准确连接']],''),S.select('工作连接','work_connection',connectionChoices,connection))
      .replace('<p class="sp-note" data-goal-connection-note></p>','<p class="sp-note" data-goal-connection-note>'+h(selected?.continuation_reason||selected?.reason||(connection==='legacy'?'此处沿用旧工作端配置与原生接续。':'请选择要使用的准确连接。'))+'</p>')
      .replace('<button type="submit">保存自动目标</button>','<button type="submit" '+(allowed?'':'disabled')+'>保存自动目标</button>');
    return planSection('goal','自动目标','给出目标和完成标准，启动后由一个工作端分轮推进，等你验收结果。',
      v.data.goals.map(row=>card(row,'goal_job')).join(''),accurateForm,'暂无自动目标');
  }
  function scheduledMessages(){
    const form=`<details class="wd-plan-create"><summary>新建定时计划</summary><form id="planning-schedule">${S.select('接续工作','task_id',[['','选择已完成的官方工作'],...v.data.tasks.map(t=>[t.id,t.title])],'')}${S.select('重复方式','frequency',[['once','一次'],['daily','每天'],['weekly','每周']],'once')}${S.field('首次执行时间','due','','type="datetime-local" required')}${S.field('时区','timezone',Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai','required')}${S.field('最多执行次数','max_runs','30','type="number" min="1" max="1000"')}${S.text('计划发送的消息','content','','required')}<p class="sp-note">上面的时间按所选时区解释。保存即授权按本计划自动发送。每次沿用准确会话和配置；工作被其它操作接续后会暂停，请重新安排。关闭本程序或电脑休眠时不承诺唤醒。</p><button type="submit">保存并启用计划</button></form></details>`;
    const tasks=`<label class="sp-field"><span>接续工作</span><select aria-label="接续工作" name="task_id"><option value="">选择已完成的官方工作</option>${v.data.tasks.map(task=>`<option value="${h(task.id)}" ${task.can_schedule===false?'disabled':''}>${h(task.title+(task.can_schedule===false?' · 原会话自动接续尚未接通':''))}</option>`).join('')}</select></label>`;
    return planSection('schedule','定时计划','给已完成的独立工作定时发一条后续指令，沿用原对话。',
      v.data.schedules.map(row=>card(row,'scheduled_job')).join(''),form.replace(S.select('接续工作','task_id',[['','选择已完成的官方工作'],...v.data.tasks.map(t=>[t.id,t.title])],''),tasks),'暂无定时计划');
  }
  function initialSections(){
    return [
      ['fixed','固定流程预约','给已授权的固定合作任务约一个时间，到点启动一次。'],
      ['cooperation','合作重复计划','每天或每周重跑同一套已授权的固定合作方案，每次分别保存成果。'],
      ['goal','自动目标','给出目标和完成标准，启动后由一个工作端分轮推进，等你验收结果。'],
      ['schedule','定时计划','给已完成的独立工作定时发一条后续指令，沿用原对话。']
    ].map(([key,title,description])=>planSection(key,title,description,
      v.error?'<p class="wd-plan-empty">读取后可查看已有计划和创建入口。</p>':'<div class="wd-plan-skeleton" aria-hidden="true"></div>','','')).join('');
  }
  document.addEventListener('change',e=>{if(active()&&e.target.name==='tool_project')v.toolProject=e.target.value;});
  document.addEventListener('click',e=>{
    if(!active()||!e.target.closest('[data-tool-schedule-open]'))return;
    const id=$('#main [name=tool_project]')?.value;
    const p=v.data?.projects.find(item=>item.id===id);
    if(p)window.dispatchEvent(new CustomEvent('workdesk-tool-schedules',{detail:{project_id:p.id,project_root:p.root}}));
  });
  function syncPlanCapabilities(reset=false,restoreDraft=false){
    const form=$('#planning-goal'),selector=form?.elements.work_connection;
    if(selector){
      const provider=form.elements.provider.value,metadata=v.data.providers.find(row=>row.id===provider);
      const rows=v.data.partner_connections.filter(row=>row.partner_id===provider),selected=reset?'':restoreDraft?v.forms['planning-goal']?.work_connection||selector.value:selector.value;
      selector.replaceChildren(new Option('请选择准确连接',''),...(metadata?.legacy_auto_goal_supported?[new Option('旧工作端默认配置','legacy')]:[]),...rows.map(row=>new Option((row.model_label||row.model||'官方默认模型')+' · '+row.name,row.id)));
      selector.value=[...selector.options].some(option=>option.value===selected)?selected:'';
      const connection=rows.find(row=>row.id===selector.value),legacy=selector.value==='legacy'&&metadata?.legacy_auto_goal_supported;
      const allowed=legacy||connection?.auto_goal_supported===true;
      form.querySelector('[type=submit]').disabled=!allowed;
      const note=form.querySelector('[data-goal-connection-note]');
      note.innerHTML=legacy?'此处沿用旧工作端配置与原生接续。':connection?h(connection.continuation_reason||connection.reason||'原会话自动接续尚未接通。')+' <a href="#work">普通工作</a> · <a href="#planning">合作重复计划</a>':'请选择要使用的准确连接。';
    }
    const schedule=$('#planning-schedule');
    if(schedule){
      for(const option of schedule.elements.task_id.options){const task=v.data.tasks.find(row=>row.id===option.value);option.disabled=task?.can_schedule===false;if(task?.can_schedule===false)option.textContent=task.title+' · 原会话自动接续尚未接通';}
      const selected=v.data.tasks.find(row=>row.id===schedule.elements.task_id.value);
      schedule.querySelector('[type=submit]').disabled=selected?.can_schedule===false;
    }
  }
  let openedRound='';
  window.addEventListener('hashchange',()=>{openedRound='';});
  function openLinkedRound(){
    const match=/^#planning\/round\/([a-zA-Z0-9_:%.-]+)\?(.+)$/.exec(location.hash);
    if(!match||openedRound===location.hash||document.querySelector('dialog[data-project-tool]'))return;
    const project=new URLSearchParams(match[2]).get('project_id');
    if(!project)return;
    openedRound=location.hash;
    window.dispatchEvent(new CustomEvent('workdesk-tool-schedules',{detail:{project_id:project,round_id:decodeURIComponent(match[1])}}));
  }
  function draw(completedForm=''){
    if(!active())return;
    const root=$('#main'), focused=root.contains(document.activeElement)?document.activeElement:null;
    const field=focused?.form?.id!==completedForm && focused?.name && focused.form
      ? {form:focused.form.id,name:focused.name,start:focused.selectionStart,end:focused.selectionEnd} : null;
    const summary=focused?.closest('summary')?.parentElement.querySelector('form')?.id;
    for(const form of root.querySelectorAll('.wd-plan-create form')){
      if(form.id===completedForm)continue;
      v.open[form.id]=form.closest('details').open;
      if(v.open[form.id])v.forms[form.id]=Object.fromEntries(new FormData(form));
    }
    $('#main').innerHTML=`<section class="sp-page wd-planning-page">${S.nav('planning')}${S.subnav('planning')}
      <header class="sp-heading wd-plan-heading"><div><h1>计划与目标任务</h1>
        <p>预约启动工作、定时发后续指令，或按完成条件推进目标。</p></div>
        ${S.button(v.error?'重试读取':'刷新进度','data-plan="refresh"')}
      </header>
      <p class="wd-plan-service">定时派发需要 Workdesk 服务保持运行。重启后需继续计划，错过的时间不会补发。</p>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      ${!v.data&&!v.error?'<p class="wd-plan-loading" role="status">正在读取已有计划与可用任务…</p>':''}
      <div class="wd-plan-grid" aria-busy="${!v.data&&!v.error}">
        ${v.data?fixedSchedules(v.data.fixed_schedules||[])+cooperativePlans()+automaticGoals()+scheduledMessages():initialSections()}
      </div>
      ${document.documentElement.dataset.workdeskChannel&&v.data?.projects?.length?
        `<section class="wd-plan-section"><h2>项目工具预约</h2>
        ${S.select('预约项目','tool_project',v.data.projects.map(p=>[p.id,p.name]),v.toolProject||v.data.projects[0].id)}
        ${S.button('打开工具预约','data-tool-schedule-open')}</section>`:''}
      <div class="wd-plan-footer"><a href="#monitor">查看当前运行与待处理请求</a>
        ${v.data?.legacy.length?'<a href="#schedules">管理历史定时计划</a>':''}
      </div>
    </section>`;
    const goalRoute=location.hash.match(/^#planning\/goal\/([a-zA-Z0-9-]+)$/);
    if(goalRoute&&v.focusGoal!==location.hash){const target=root.querySelector('[data-plan-record="'+goalRoute[1]+'"]');if(target){target.focus();target.scrollIntoView({block:'center'});v.focusGoal=location.hash;}}
    for(const [id,values] of Object.entries(v.forms)){
      const form=$('#'+id);
      if(!form)continue;
      for(const [key,value] of Object.entries(values)){
        const input=form.elements.namedItem(key);
        if(!input)continue;
        if(input.type==='checkbox')input.checked=Boolean(value);
        else input.value=value;
      }
      if(v.open[id])form.closest('details').open=true;
    }
    if(v.data)syncPlanCapabilities(false,true);
    openLinkedRound();
    if(field){
      const control=root.querySelector('#'+field.form)?.elements.namedItem(field.name);
      control?.focus({preventScroll:true});
      if(control && field.start!=null && typeof control.setSelectionRange==='function')
        control.setSelectionRange(field.start,field.end);
    }else if(summary && summary!==completedForm){
      root.querySelector('#'+summary)?.closest('details')?.querySelector('summary')?.focus({preventScroll:true});
    }
  }
  async function load(completedForm=''){const seq=++v.seq;if(!v.data){v.error='';draw();}try{const data=await S.request('planning');if(seq!==v.seq||!active())return;v.data=data;v.error='';draw(completedForm);}catch(e){if(seq!==v.seq||!active())return;v.error=e.message;draw();}finally{if(seq===v.seq){clearTimeout(v.timer);if(active())v.timer=setTimeout(poll,5000);}}}
  function poll(){if(!active())return;if(v.busy||$('#main details[open]')||$('#main').contains(document.activeElement)&&document.activeElement.matches('input,textarea,select'))v.timer=setTimeout(poll,5000);else load();}
  document.addEventListener('input',e=>{const f=e.target.closest('form');if(active()&&f&&['planning-goal','planning-schedule','planning-cooperation'].includes(f.id)){v.forms[f.id]=Object.fromEntries(new FormData(f));v.open[f.id]=true;}});
  document.addEventListener('change',e=>{const f=e.target.closest('form');if(active()&&f&&['planning-goal','planning-schedule','planning-cooperation'].includes(f.id)){if(f.id==='planning-goal'||f.id==='planning-schedule')syncPlanCapabilities(e.target.name==='provider');v.forms[f.id]=Object.fromEntries(new FormData(f));v.open[f.id]=true;}});
  document.addEventListener('change',e=>{const f=e.target.closest('#planning-cooperation');if(!active()||!f)return;const c=v.data?.cooperative_candidates.find(c=>c.id===f.elements.namedItem('item_id').value),el=f.querySelector('[data-cooperative-limits]');if(el)el.textContent=c?`原授权累计最多 ${c.max_attempts_total} 次尝试，截止 ${S.when(c.dispatch_before)}`:'选择任务后显示原授权累计上限与截止。';});
  document.addEventListener('submit',async e=>{const f=e.target;if(!active()||!['planning-goal','planning-schedule','planning-cooperation'].includes(f.id))return;e.preventDefault();if(v.busy)return;v.busy=true;try{const p=Object.fromEntries(new FormData(f));v.forms[f.id]={...p};v.open[f.id]=true;if(f.id==='planning-cooperation'){const candidate=v.data.cooperative_candidates.find(c=>c.id===p.item_id);if(!candidate)throw Error('请选择当前已授权且就绪的固定任务。');await S.command('planning.cooperation-save',{...candidate.request,frequency:p.frequency,timezone:p.timezone,local_time:p.local_time,max_cycles:Number(p.max_cycles),confirm:true,...(p.long_term?{long_term:true,confirm_long_term:true,dispatch_before_local:p.dispatch_before_local}:p.dispatch_before_local?{dispatch_before_local:p.dispatch_before_local}:{})});}else if(f.id==='planning-goal'){p.checks=p.file?[{file:p.file,contains:p.contains}]:[];delete p.file;delete p.contains;p.max_rounds=Number(p.max_rounds);if(p.dispatch_before_local)p.dispatch_before=new Date(p.dispatch_before_local).getTime()/1000;delete p.dispatch_before_local;if(!p.source_handoff_id)delete p.source_handoff_id;if(Array.isArray(v.data.partner_connections)){const choice=p.work_connection,provider=v.data.providers.find(row=>row.id===p.provider),connection=v.data.partner_connections.find(row=>row.id===choice&&row.partner_id===p.provider);delete p.work_connection;if(choice==='legacy'&&provider?.legacy_auto_goal_supported){}else if(connection?.auto_goal_supported===true){p.connection_id=connection.id;p.connection_revision=connection.revision;}else throw Error(connection?.continuation_reason||connection?.reason||'原会话自动接续尚未接通，请选择旧工作端配置或使用普通工作。');}p.confirm=true;await S.command('planning.goal-save',p);}else{const task=v.data.tasks.find(row=>row.id===p.task_id);if(task?.can_schedule===false)throw Error(task.reason||'此工作的原会话自动接续尚未接通。');p.local_time=p.due;delete p.due;p.max_runs=Number(p.max_runs);p.confirm=true;await S.command('planning.schedule-save',p);}delete v.forms[f.id];delete v.open[f.id];v.error='';await load(f.id);}catch(e){v.error=e.message;draw();}finally{v.busy=false;}});
  document.addEventListener('click',async e=>{const el=e.target.closest('[data-plan]');if(!active()||!el||v.busy)return;v.busy=true;try{if(el.dataset.plan==='refresh'){el.textContent='正在刷新…';el.setAttribute('aria-busy','true');return await load();}if(el.dataset.plan==='goal-blocker'||el.dataset.plan==='goal-review'){
      const row=v.data.goals.find(r=>r.id===el.dataset.id);if(!row)return;
      const payload={id:row.id,revision:row.revision};
      if(el.dataset.plan==='goal-blocker'){
        payload.blocker=el.dataset.clear?'':el.closest('[data-plan-record]').querySelector('[name=blocker]').value.trim();
        if(!payload.blocker&&!el.dataset.clear)throw Error('请填写当前阻碍。');
      }else{payload.accepted=el.dataset.accepted==='true';if(!await S.confirm('确认目标验收决定？',row.title,payload.accepted?'将记录 Human 已确认完成。':'退回后等待手动启动。'))return;}
      await S.command('planning.'+el.dataset.plan,payload);v.error='';await load();return;
    }if(el.dataset.plan==='cooperative-state'){const r=v.data.cooperative_jobs.find(r=>r.id===el.dataset.id),state=el.dataset.state;if(state==='RUNNING'&&!await S.confirm('继续合作重复计划？',r.title,'将继续原任务的重复周期，沿用原累计次数和最初截止。未知回合须先核对。'))return;await S.command('planning.cooperation-state',{id:r.id,revision:r.revision,state,confirm:state==='RUNNING'});v.error='';await load();return;}const kind=el.dataset.kind,r=v.data[kind==='goal_job'?'goals':'schedules'].find(r=>r.id===el.dataset.id),state=el.dataset.state;if(state==='RUNNING'&&!await S.confirm('启动自动任务？',r.title,'将按已保存的目标或时间规则调用官方工作端。失败或未知时暂停，不自动重发。'))return;await S.command('planning.state',{kind,id:r.id,revision:r.revision,state,confirm:state==='RUNNING'});v.error='';await load();}catch(e){v.error=e.message;draw();}finally{v.busy=false;}});
  WorkdeskModules.onLeave('planning',()=>{++v.seq;v.focusGoal='';clearTimeout(v.timer);});window.WorkdeskPlanningPage={render:load};
})();
