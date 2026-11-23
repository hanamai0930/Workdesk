/* Runtime state and exact-scope controls for the current Work and API Chat records. */
(() => {
  'use strict';
  const $ = WorkdeskModules.dom('monitor'), S = WorkdeskSupport, {h} = S;
  const v = {data:null, scope:'attention', error:'', busy:false, seq:0, timer:null};
  const active = () => location.hash.split('/')[0] === '#monitor';
  const live = new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
  const unknown = new Set(['SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN']);
  const reconciliationReasons = new Set(['REQUEST_UNKNOWN','NATIVE_OPERATION','NATIVE_REPLY','REMOTE_UNKNOWN']);
  const reasonLabels = {
    CURRENT_TURN:'仍有在途回合', LOCAL_WORKER:'本地工作端尚未退出', EXIT_UNPROVED:'缺少本地退出证据',
    REMOTE_UNKNOWN:'远端状态待核对', REQUEST_UNKNOWN:'请求状态待核对',
    NATIVE_OPERATION:'原生操作回执待核对', NATIVE_REPLY:'审批或输入回执待核对'
  };
  const providerLabels = {codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen'};
  const remoteLabels = {UNKNOWN:'待核对',UNREPORTED:'未记录',NOT_SENT:'未发送',completed:'已完成',failed:'失败',interrupted:'已中断'};
  const flowLabels = {RUNNING:'运行中',PAUSED:'已暂停',RESULTS_AVAILABLE:'文字结果已具备'};
  const groups = [
    ['attention','需要处理','待审批、待补充输入或待核对的回合'],
    ['live','进行中','准备、执行或正在停止的回合'],
    ['manual','人工接管','已暂停程序派发，等待人工处理'],
    ['recent','已结束','最近一次已结束的回合']
  ];
  const ref = r => ({task_id:r.task_id,attempt_id:r.attempt_id,owner:r.owner});
  const hasManual = r => r.manual_control?.state === 'MANUAL' || (r.work_items || []).some(item => item.manual);

  function status(r) {
    if(hasManual(r))return ['manual','人工处理中'];
    if(r.state==='WAITING_APPROVAL')return ['attention','等待审批'];
    if(r.state==='WAITING_INPUT')return ['attention','等待补充输入'];
    if(r.pending_approvals>0)return ['attention','有待处理请求'];
    const reasons=r.block_reasons||[];
    if(unknown.has(r.state))return ['attention',S.state(r.state)];
    if(reasons.some(reason=>reconciliationReasons.has(reason))||(!live.has(r.state)&&reasons.length))
      return ['attention','状态待核对'];
    if(live.has(r.state))return ['live',S.state(r.state)];
    return ['recent',S.state(r.state)];
  }

  function runEvidence(r, shown) {
    const facts = [
      ['回合标识',r.attempt_id],
      ['本地运行线程',r.local_worker_alive?'仍在运行':'已退出'],
      ['已知本地工具退出证据',r.known_tools_exited?'已记录':'尚无证据'],
      ['远端状态回执',remoteLabels[r.remote_state]||r.remote_state||'未记录'],
      ['待审批／输入',r.pending_approvals]
    ];
    const c = r.cooperative, key = 'evidence:'+r.attempt_id;
    return `<details class="wd-monitor-evidence" data-monitor-disclosure="${h(key)}" ${shown.has(key)?'open':''}>
      <summary>运行证据与回执</summary>
      <dl class="sp-facts">${facts.map(([label,value])=>`<dt>${h(label)}</dt><dd>${h(value)}</dd>`).join('')}</dl>
      ${r.block_reasons?.length?`<p class="sp-note">${h(r.block_reasons.map(reason=>reasonLabels[reason]||reason).join(' · '))}</p>`:''}
      ${c?`<p class="sp-note">方案 ${h(c.plan_id)} · 版本 ${h(c.plan_version)}<br>
        单元 ${h(c.unit_key)} · 责任 ${h(c.driver_generation)}<br>会话 ${h(r.session_id)} · 回合 ${h(r.turn_id||'待回执')}
        ${c.flow?`<br>连续流程 ${h(c.flow.generation)}`:''}</p>`:''}
      ${r.reconciliation?`<h3>最近状态核对记录</h3>${S.raw(r.reconciliation)}`:''}
      ${r.native_operations?.length?`<h3>最近原生操作回执</h3>${S.raw(r.native_operations)}`:''}
    </details>`;
  }

  function workCard(r, state, shown, drafts) {
    const [group,label] = state;
    const manual = r.manual_control?.state==='MANUAL', c = r.cooperative;
    const working = live.has(r.state), unconfirmed = unknown.has(r.state), handling = hasManual(r);
    const steer = working&&r.can_steer&&!c&&!handling;
    const steerKey = 'message:'+r.attempt_id;
    const commandAttrs = action => `data-monitor="${action}" data-id="${h(r.task_id)}"`;
    const reviewLabel = r.state==='WAITING_APPROVAL'?'查看审批':r.state==='WAITING_INPUT'?'补充输入':'运行详情／审批';
    return `<article class="sp-card wd-monitor-card" data-run="${h(r.task_id)}">
      <div class="wd-monitor-row"><h3>${h(r.title)}</h3><span class="wd-monitor-badge" data-monitor-state="${group}">${h(label)}</span></div>
      <p class="wd-monitor-meta">${c?'合作 Work':'Work'} · ${h(providerLabels[r.provider]||r.provider||'工作端')} · ${h(S.when(r.started))}
        ${r.pending_approvals>0?` · ${h(r.pending_approvals)} 项待处理请求`:''}</p>
      ${c?`<p class="wd-monitor-meta" data-monitor-cooperative="${h(c.run_id)}">
        ${h(c.unit_title)} · ${h(c.member_name)} · ${c.flow?'连续串行':'明确单步'}${c.flow_state?' / '+h(flowLabels[c.flow_state]||c.flow_state):''}
        · ${c.exact_identity?'归属已核对':'归属待核对'} · <a href="#work-items/item/${h(c.item_id)}">返回合作业务任务</a>
      </p>`:''}
      ${manual?`<p class="wd-monitor-meta">人工处理说明：${h(r.manual_control.note||'未填写')}</p>`:''}
      ${(r.work_items||[]).map(item=>`<p class="wd-monitor-meta"><a href="#work-items/item/${h(item.id)}">
        任务：${h(item.title)}${item.manual?'（人工接管，进入任务页结束）':''}</a></p>`).join('')}
      <div class="sp-actions">
        <a class="wd-monitor-action" href="#control/task/${h(r.task_id)}">${reviewLabel}</a>
        <a href="#results/${h(r.task_id)}">成果与验收</a>
        ${working?S.button('停止此回合',commandAttrs('stop')):''}
        ${unconfirmed?S.button('核对此回合',commandAttrs('reconcile')):''}
        ${manual?S.button('结束人工接管',commandAttrs('release')):
          !r.driver_protected&&!handling&&!working&&!unconfirmed?S.button('转人工处理',commandAttrs('takeover')):''}
      </div>
      ${steer?`<details class="wd-monitor-evidence" data-monitor-disclosure="${h(steerKey)}" ${shown.has(steerKey)?'open':''}>
        <summary>运行中补充指令</summary>
        <form data-monitor-message="${h(r.task_id)}" data-monitor-attempt="${h(r.attempt_id)}">
          ${S.text('补充指令','message',drafts.get(r.attempt_id)||'','required maxlength="20000"')}
          <button type="submit">提交给官方工作端</button>
          <p class="sp-note">只提交一次。以原生操作回执为准，未知状态不会自动重发。</p>
        </form>
      </details>`:''}
      ${runEvidence(r,shown)}
    </article>`;
  }

  function apiCard(r, [group,label], shown) {
    const key = 'api:'+r.turn_id;
    return `<article class="sp-card wd-monitor-card" data-api-run="${h(r.turn_id)}">
      <div class="wd-monitor-row"><h3>${h(r.title)}</h3><span class="wd-monitor-badge" data-monitor-state="${group}">${h(label)}</span></div>
      <p class="wd-monitor-meta">API Chat · ${h(r.model)} · ${h(S.when(r.created))}</p>
      ${r.error?`<p class="sp-error">${h(r.error)}</p>`:''}
      <div class="sp-actions"><a class="wd-monitor-action" href="#chat/${h(r.chat_id)}">查看聊天和请求状态</a>
        ${r.can_stop?S.button('停止此 API 请求',`data-monitor="api-stop" data-id="${h(r.turn_id)}"`):''}</div>
      <details class="wd-monitor-evidence" data-monitor-disclosure="${h(key)}" ${shown.has(key)?'open':''}>
        <summary>请求记录</summary>
        <p class="sp-note">请求 ${h(r.turn_id)} · 本地请求线程${r.local_worker_alive?'运行中':'已退出'}<br>API 停止与计费状态以服务方回执为准。</p>
      </details>
    </article>`;
  }

  function draw() {
    if(!active()||!v.data)return;
    const main = $('#main');
    const shown = new Set([...main.querySelectorAll('[data-monitor-disclosure][open]')].map(node=>node.dataset.monitorDisclosure));
    const drafts = new Map([...main.querySelectorAll('[data-monitor-attempt] textarea')].map(node=>[node.form.dataset.monitorAttempt,node.value]));
    const rows = [
      ...(v.data.runs||[]).map(row=>({row,group:status(row),api:false})),
      ...(v.data.api_chats||[]).map(row=>({row,group:status(row),api:true}))
    ].sort((a,b)=>(b.row.started??b.row.created??0)-(a.row.started??a.row.created??0));
    const buckets = new Map(groups.map(([key])=>[key,[]]));
    for(const entry of rows)buckets.get(entry.group[0]).push(entry);
    const highlights = groups.slice(0,3).map(([key,title,description])=>`<div class="wd-monitor-count" data-monitor-state="${key}">
      <dt>${title}</dt><dd><strong>${buckets.get(key).length}</strong><span>${description}</span></dd>
    </div>`).join('');
    const lists = groups.map(([key,title])=>{
      const entries = buckets.get(key);
      if(!entries.length)return '';
      return `<section class="wd-monitor-group" aria-labelledby="wd-monitor-${key}"><h2 id="wd-monitor-${key}">${title} <span>${entries.length}</span></h2>
        ${entries.map(({row,group,api})=>api?apiCard(row,group,shown):workCard(row,group,shown,drafts)).join('')}
      </section>`;
    }).join('');
    const empty = v.scope==='all'?'暂无运行记录':'当前没有在途或待处理的回合';
    const emptyNote = v.scope==='all'?'开始一项 Work 或发送 API 聊天后，这里会显示最近的回合。':'可切到「全部最近回合」查看已经结束的工作和 API 请求。';
    main.innerHTML = `<section class="sp-page wd-monitor-page">${S.nav('monitor')}${S.subnav('monitor')}
      <header class="sp-heading"><div><h1>运行监控与人工接管</h1>
        <p>查看 Work 和 API 聊天的运行状态，处理审批、补充输入和待核对的回合。</p></div>
        ${S.button('刷新状态','data-monitor="refresh"')}</header>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      <dl class="wd-monitor-counts" aria-label="运行状态摘要">${highlights}</dl>
      <div class="wd-monitor-list-controls">
        <p class="sp-note">本页 ${rows.length} 条最近回合 · 更新于 ${h(S.when(v.data.checked_at))}
          ${v.data.limited?'<br>Work 与 API Chat 各最多显示最近 200 项。':''}</p>
        ${S.select('显示范围','scope',[['attention','需要关注'],['all','全部最近回合']],v.scope)}
      </div>
      ${lists||`<section class="sp-card wd-monitor-empty"><h2>${empty}</h2><p>${emptyNote}</p></section>`}
      <details class="wd-monitor-tools" data-monitor-tools data-monitor-disclosure="tools" ${shown.has('tools')?'open':''}>
        <summary>运行工具与历史</summary><div class="sp-actions">
          <a href="#control">后台任务</a><a href="#control/receipts">操作回执</a>
          <a href="#history">历史交接</a><a href="#settings/operations">运行与恢复</a><a href="#collab">旧版协作队列</a>
        </div><p class="sp-note">每 3 秒刷新，填写指令时暂停。人工接管暂停 Workdesk 派发，文件和网络权限由官方工作端控制。</p>
      </details>
    </section>`;
  }

  async function load() {
    const seq=++v.seq;
    try{
      const data=await S.request('monitor?scope='+v.scope);
      if(seq!==v.seq||!active())return;
      v.data=data;draw();
    }catch(e){
      if(seq!==v.seq||!active())return;
      v.error=e.message;
      if(v.data)draw();
      else $('#main').innerHTML=errorPage(e.message);
    }finally{
      if(seq===v.seq&&active()){
        clearTimeout(v.timer);v.timer=setTimeout(tick,3000);
      }
    }
  }
  function tick() {
    if(v.busy||$('#main').contains(document.activeElement)&&document.activeElement.matches('input,textarea,select'))
      v.timer=setTimeout(tick,3000);
    else load();
  }
  document.addEventListener('change',async e=>{
    if(active()&&e.target.name==='scope'){v.scope=e.target.value;await load();}
  });
  document.addEventListener('click',async e=>{
    const el=e.target.closest('[data-monitor]');
    if(!active()||!el||v.busy)return;
    v.busy=true;
    try{
      const action=el.dataset.monitor,r=v.data.runs.find(r=>r.task_id===el.dataset.id);
      if(action==='refresh'){v.error='';return await load();}
      if(action==='api-stop'){
        const c=v.data.api_chats.find(c=>c.turn_id===el.dataset.id);
        await S.command('chat.api-stop',{id:c.chat_id,turn_id:c.turn_id});
      }else if(action==='stop')await S.command('task.stop',ref(r));
      else if(action==='reconcile')await S.command('task.reconcile',ref(r));
      else if(action==='takeover'){
        if(!await S.confirm('转为人工处理？',r.title,'需先核对旧回合确已停止。接管期间 Workdesk 暂停继续派发。'))return;
        await S.command('monitor.takeover',{...ref(r),note:'由运行监控转为人工处理'});
      }else if(action==='release'){
        if(!await S.confirm('结束人工接管？',r.title,'结束后可手动继续工作。'))return;
        await S.command('monitor.release',{id:r.manual_control.id,revision:r.manual_control.revision});
      }
      v.error='';await load();
    }catch(e){v.error=e.message;draw();}
    finally{v.busy=false;}
  });
  document.addEventListener('submit',async e=>{
    const f=e.target;
    if(!active()||!f.dataset.monitorMessage)return;
    e.preventDefault();
    if(v.busy)return;
    v.busy=true;
    try{
      const r=v.data.runs.find(r=>r.task_id===f.dataset.monitorMessage);
      const text=String(new FormData(f).get('message'));
      const out=await S.command('native.control',{...ref(r),operation:'steer',params:{text}});
      f.reset();
      v.error='';await load();
      toast('已提交，原生操作状态：'+(out.operation?.state||out.state||'待查看回执'));
    }catch(e){v.error=e.message;draw();}
    finally{v.busy=false;}
  });
  WorkdeskModules.onLeave('monitor',()=>{++v.seq;clearTimeout(v.timer);});
  window.WorkdeskMonitorPage={render:load};
})();
