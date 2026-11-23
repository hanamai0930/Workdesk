/* Functional controls for scoped transfer, two-copy concurrency and recovery. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('operations');
  const v={seq:0,workspace:null,operations:null,persistence:null,verification:null,handoff:null,monitor:null,monitorError:null};
  const h=esc,field=(label,name,value='')=>`<label class="ctl-field"><span>${h(label)}</span><input name="${name}" value="${h(value)}"></label>`;
  const button=(label,action,id='')=>`<button type="button" class="button secondary" data-op="${action}" data-id="${h(id)}">${h(label)}</button>`;
  const raw=x=>rawBlock('准确范围与记录',JSON.stringify(x,null,2),false);
  const confirm=async message=>window.WorkdeskCollaboration.confirm(message);
  async function call(route,p){return api('v1/'+route,p===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});}
  async function cmd(kind,payload){const id=crypto.randomUUID();try{return (await call('command',{id,kind,payload})).receipt;}catch(error){throw Error(error.message+' · 命令 '+id+'；先核对回执，不自动重发。');}}
  const taskOptions=filter=>v.workspace.tasks.filter(filter||(()=>true)).map(t=>`<option value="${h(t.id)}">${h(t.title)} · ${h(t.spec?.provider||'')} · ${h(WorkdeskSupport.state(t.state))}</option>`).join('');
  const link=(label,href)=>`<a class="button secondary" href="${h(href)}">${h(label)}</a>`;
  const section=(title,body)=>`<section class="op-card"><h2>${h(title)}</h2>${body}</section>`;
  const detail=(title,body,key)=>`<details class="op-disclosure" data-op-section="${h(key)}"><summary>${h(title)}</summary><div class="op-disclosure-content">${body}</div></details>`;
  const row=(title,action,subtitle='')=>`<div class="op-row"><div><strong>${h(title)}</strong>${subtitle?`<p>${h(subtitle)}</p>`:''}</div><div class="op-actions">${action}</div></div>`;
  const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'';
  const shell=(title,body)=>`<div class="page op-page"><a class="op-back" href="#settings/operations">返回设置</a><div class="op-heading"><h1>${h(title)}</h1><div class="op-actions">${link('运行监控','#monitor')}${button('刷新','refresh')}</div></div>${body}</div>`;
  const activeStates=new Set(['RESERVED','SEND_INTENT','RUNNING']);
  const attentionStates=new Set(['WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED','SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN']);
  const incidentalReasons=new Set(['CURRENT_TURN','LOCAL_WORKER','EXIT_UNPROVED']);
  function runtimeOverview(){
    if(v.monitorError)return section('任务状态',`<p role="status" class="op-muted">暂时无法读取运行状态。</p>${link('查看运行监控','#monitor')}`);
    const monitor=v.monitor||{},rows=[
      ...(monitor.runs||[]).map(r=>({...r,href:'#control/task/'+r.task_id,tag:r.cooperative?.member_name||r.provider})),
      ...(monitor.api_chats||[]).map(c=>({...c,href:'#chat/'+c.chat_id,tag:'Chat API'}))];
    const pending=[],running=[];
    for(const r of rows){
      const manual=r.manual_control?.state==='MANUAL'||(r.work_items||[]).some(item=>item.manual);
      const isRunning=activeStates.has(r.state);
      const blocked=(r.block_reasons||[]).some(reason=>!isRunning||!incidentalReasons.has(reason));
      if(manual||r.pending_approvals||attentionStates.has(r.state)||blocked){
        const label=manual?'人工处理中':r.pending_approvals>0&&(r.state==='WAITING_APPROVAL'||r.state==='WAITING_INPUT')?WorkdeskSupport.state(r.state):attentionStates.has(r.state)?WorkdeskSupport.state(r.state):'待核对';
        pending.push(row(r.title,`<span class="op-status attention">${h(label)}</span>${link('查看',r.href)}`,r.tag));
      }else if(isRunning||r.local_worker_alive){
        running.push(row(r.title,`<span class="op-status active">${h(isRunning?WorkdeskSupport.state(r.state):'本地收尾中')}</span>${link('查看',r.href)}`,r.tag));
      }
    }
    const group=(title,items,message)=>section(title,items.slice(0,6).join('')+(items.length>6?row('其余 '+(items.length-6)+' 项',link('查看运行监控','#monitor')):items.length?'':`<p class="op-muted">${message}</p>`));
    return `<div class="op-runtime" data-op-runtime>${group('待核对的工作',pending,'当前没有待核对的工作。')}${group('正在运行',running,'当前没有运行中的工作。')}</div>`;
  }
  function handoffTools(){
    const groups=v.operations.parallel_groups.map(g=>detail('并行组 · '+WorkdeskSupport.state(g.state),`<div class="op-actions">${button('开始已授权回合','parallel-start',g.id)}${button('暂停后续派发','parallel-pause',g.id)}</div>${g.tasks.map(id=>`<p><a href="#control/task/${h(id)}">${h(v.workspace.tasks.find(t=>t.id===id)?.title||'查看关联工作')}</a></p>`).join('')}${raw(g)}`,'parallel-'+g.id)).join('');
    return detail('人工接管与并行安排',`<form id="op-handoff" class="op-form"><label class="ctl-field"><span>预览接管范围</span><select name="task" required><option value="">选择原任务</option>${taskOptions()}</select></label><button class="button secondary" type="submit">查看交接范围</button></form><form id="op-parallel" class="op-form"><h3>设置并行任务</h3><label class="ctl-field"><span>任务一</span><select name="a" required><option value="">选择任务</option>${taskOptions(t=>t.state==='APPROVED')}</select></label><label class="ctl-field"><span>任务二</span><select name="b" required><option value="">选择任务</option>${taskOptions(t=>t.state==='APPROVED')}</select></label><label class="ctl-field"><span>任务三（可选）</span><select name="c"><option value="">只运行两个任务</option>${taskOptions(t=>t.state==='APPROVED')}</select></label><button class="button secondary" type="submit">保存并行组</button></form>${groups}`,'handoff');
  }
  function recoveryTools(){
    const backups=v.persistence.backups.map(b=>detail('阶段快照'+(stamp(b.created)?' · '+stamp(b.created):''),`<p>${h(b.root)}</p>${button('核对并暂存恢复','restore',b.id)}${raw(b)}`,'backup-'+b.id)).join('');
    const restores=v.persistence.restores.map(r=>detail((r.state==='STAGED'?'待切换的恢复':'恢复记录')+(stamp(r.created)?' · '+stamp(r.created):''),`<p>${h(r.target)}</p>${r.state==='STAGED'?button('确认切换资料库','activate',r.id):''}${raw(r)}`,'restore-'+r.id)).join('');
    const relocations=v.persistence.relocations.filter(r=>r.state==='PREPARED').map(r=>`<form id="op-relocate-commit-${h(r.id)}" data-relocation="${h(r.id)}" class="op-form"><h3>更新已核对项目的位置</h3>${field('新项目目录','new_root')}<button class="button secondary" type="submit">核对并更新项目路径</button>${raw(r)}</form>`).join('');
    return detail('阶段快照与项目位置',`${button('创建阶段快照','backup')}${backups}${restores}<form id="op-import" class="op-form"><h3>导入同一台账的备份副本</h3>${field('备份目录','source')}${field('备份清单校验值','manifest_hash')}<button class="button secondary" type="submit">验证并暂存</button></form><form id="op-relocation" class="op-form"><h3>同一台电脑上的项目重新定位</h3><label class="ctl-field"><span>项目</span><select name="project" required><option value="">选择项目</option>${v.workspace.projects.map(p=>`<option value="${h(p.id)}">${h(p.name)}</option>`).join('')}</select></label><button class="button secondary" type="submit">保存移动前核对点</button></form>${relocations}`,'recovery-tools');
  }
  function diagnosticTools(){
    const audits=(v.verification?.audits||[]).map(a=>detail('独立审查 · '+a.verdict,`${raw(a)}${button('核对并封闭本轮实施验证','seal',a.id)}`,'audit-'+a.id)).join('');
    return detail('技术诊断与操作回执',`${row('操作回执',link('查看','#control/receipts'))}${row('服务与连接',link('打开诊断','#settings/diagnostics'))}${detail('数据位置与转交记录',`<p>当前数据：${h(v.persistence.current_data)}</p>${v.operations.handoffs.map(raw).join('')}${raw(v.persistence.dependencies)}`,'technical-records')}${detail('独立审查与工程批次',v.verification?`<form id="op-audit" class="op-form">${field('已完成的审查任务标识','task_id')}<label class="ctl-field"><span>审查类型</span><select name="review_kind"><option value="full">全部代码</option><option value="section">分段范围</option><option value="integration">跨模块衔接</option></select></label>${field('分段文件清单（JSON）','scope_files')}<button class="button secondary" type="submit">核对审查记录</button></form>${audits}<form id="op-audit-seal" class="op-form">${field('适用审查标识清单（JSON）','audit_ids')}<button class="button secondary" type="submit">核对并封闭整组审查</button></form>${raw(v.verification.rollout)}`:button('读取审查记录','load-verification'),'legacy-audit')}${storageSettings()}${storageRecovery()}`,'diagnostics');
  }
  function home(){
    return shell('恢复工具与诊断',`${runtimeOverview()}${section('恢复工具',`${row('本地资料与备份',link('打开备份与迁移','#libraries'))}${handoffTools()}${recoveryTools()}`)}${diagnosticTools()}`);
  }
  function storageSettings(){
    const s=v.persistence.storage;if(!s)return '';const r=s.credential_references||{};
    return detail('本地保存与原账号引用',`<p>资料库：${h(s.library)}<br>保存位置：${h(s.root||'沿用当前目录')}</p>${s.root&&!s.independent?button('准备独立本地保存','storage-prepare'):''}<form id="op-credentials" class="op-form"><h3>模型账号引用</h3><p>这里选择已有登录目录或本地凭据文件。更改引用会撤销旧执行授权，并暂停后续队列。</p><p>Codex目录：${h(r.codex_home||'未设置独立引用')}<br>API凭据文件：${h(r.api_env||'未设置独立引用')}</p>${field('更改Codex登录目录（可留空）','codex_home')}${field('更改API凭据文件路径（可留空）','api_env')}<button class="button secondary" type="submit">保存引用</button></form><div class="op-actions">${button('断开Codex引用','credential-disconnect','codex_home')}${button('断开API引用','credential-disconnect','api_env')}</div>`,'legacy-storage');
  }
  function storageRecovery(){
    const records=v.persistence.storage?.credential_switches||[];
    return records.some(r=>r.state!=='COMMITTED')?detail('账号引用待核对',`<p>请核对当前账号引用，再重新确认任务范围。</p>${raw(records)}`,'credential-recovery'):'';
  }
  async function render(){
    const seq=++v.seq;
    const open=new Set([...$('#main').querySelectorAll('details[data-op-section][open]')].map(el=>el.dataset.opSection));
    try{
      const [workspace,operations,persistence,monitor]=await Promise.all([
        call('workspace?view=workbench'),call('operations'),call('persistence'),
        call('monitor?scope=attention').then(value=>({value}),error=>({error}))]);
      if(seq!==v.seq||location.hash.split('/')[0]!=='#operations')return;
      Object.assign(v,{workspace,operations,persistence,monitor:monitor.value,monitorError:monitor.error,handoff:null});
      $('#main').innerHTML=home();
      for(const el of $('#main').querySelectorAll('details[data-op-section]'))if(open.has(el.dataset.opSection))el.open=true;
    }catch(error){if(seq===v.seq&&location.hash.split('/')[0]==='#operations')$('#main').innerHTML=errorPage(error.message);}
  }
  async function action(a,e){
    if(a==='refresh')return render();
    if(a==='load-verification'){
      const seq=v.seq,verify=await call('verification');
      if(seq!==v.seq||location.hash.split('/')[0]!=='#operations')return;
      v.verification=verify;
      return render();
    }
    if(a==='storage-prepare'){if(!await confirm('将当前资料制作不含认证的快照并暂存到独立本地目录。原资料保留，尚不切换或发送任务。'))return;await cmd('storage.prepare',{});return render();}
    if(a==='credential-disconnect'){if(!await confirm('仅断开这个认证引用并撤销旧执行授权；本地历史保留，不退出官方账号，不发送任务。'))return;await cmd('storage.credentials.select',{[e.dataset.id]:null});return render();}
    if(a==='seal'){if(!await confirm('只有当前完整代码独立审查、必需检查及所有工作完成后才封闭本轮有限预算；保留登录及历史。'))return;await cmd('rollout.close',{audit_id:e.dataset.id});v.verification=null;return render();}
    if(a==='handoff-commit'){const choice=Number($('#op-alternative').value),note=$('#op-unresolved').value;
      if(!await confirm('按完整预览转移归属；仅原批准路线，沿用剩余预算；旧任务成为历史。此动作不发模型消息。'))return;
      const r=await cmd('task.handoff',{task_id:v.handoff.task_id,preview_hash:v.handoff.preview_hash,alternative:choice,unresolved:note});location.hash='#control/task/'+r.task_id;return;}
    if(a==='parallel-start'||a==='parallel-pause'){const g=v.operations.parallel_groups.find(g=>g.id===e.dataset.id);if(a==='parallel-start'&&!await confirm('开始以下已保存名额的有限回合：\n'+JSON.stringify(g.scope,null,2)))return;await cmd(a==='parallel-start'?'parallel.start':'parallel.pause',{group_id:g.id,version:g.version});return render();}
    if(a==='backup'){await cmd('backup.create',{});return render();}
    if(a==='restore'){if(!await confirm('验证此快照并在新目录暂存；不切换当前状态，不派发任务。'))return;await cmd('backup.restore',{backup_id:e.dataset.id});return render();}
    if(a==='activate'){const r=v.persistence.restores.find(r=>r.id===e.dataset.id);if(!await confirm('将数据切换到 '+r.target+'。原数据保留；授权撤销、队列暂停、累计预算保留。控制器在回复后空闲退出，下一次读取连接新实例。'))return;await cmd('backup.activate',{restore_id:r.id});toast('恢复切换已登记；刷新核对新实例，不重发旧动作。');return render();}
  }
  document.addEventListener('click',async event=>{const e=event.target.closest('[data-op]');if(!e)return;event.preventDefault();e.disabled=true;try{await action(e.dataset.op,e);}catch(error){toast(error.message);}finally{if(e.isConnected)e.disabled=false;}});
  document.addEventListener('submit',async event=>{const f=event.target;if(!f.id.startsWith('op-'))return;event.preventDefault();const b=f.querySelector('[type=submit]');b.disabled=true;try{const d=new FormData(f);
    if(f.id==='op-credentials'){const selected=Object.fromEntries(['codex_home','api_env'].map(k=>[k,String(d.get(k)||'').trim()]).filter(([,p])=>p));if(!Object.keys(selected).length)throw Error('请填写需要更改的目录或文件路径。');if(!await confirm('更改认证引用、撤销旧执行授权并暂停后续队列；保留历史和用量，不发送任务。'))return;await cmd('storage.credentials.select',selected);await render();}
    else if(f.id==='op-handoff'){const seq=v.seq,preview=await call('handoff-preview?id='+encodeURIComponent(d.get('task')));if(seq!==v.seq||!location.hash.startsWith('#operations'))return;v.handoff=preview;$('#main').innerHTML=shell('准确接管预览',`<p>完整原材料、候选、原规格/结果/事件和未决事项随交接包提供；摘要不代替原文。剩余预算 ${h(JSON.stringify(v.handoff.remaining_task_budget))}。</p><label class="ctl-field"><span>原授权替代路线</span><select id="op-alternative">${v.handoff.alternatives.map((c,i)=>`<option value="${i}">${h(c.provider+' / '+c.model+' / '+c.effort+' / '+c.tier+' / '+c.role)}</option>`).join('')}</select></label><label class="ctl-field"><span>未决事项与接续说明</span><textarea id="op-unresolved"></textarea></label>${button('确认接管范围（暂不派发）','handoff-commit')}${raw(v.handoff)}`);}
    else if(f.id==='op-audit'){await cmd('audit.record',{task_id:d.get('task_id'),review_kind:d.get('review_kind')||'full',...(d.get('scope_files')?{scope_files:JSON.parse(d.get('scope_files'))}:{})});v.verification=null;await render();}
    else if(f.id==='op-audit-seal'){if(!await confirm('核对整组审查、完整代码覆盖、跨模块审查及检查版本，满足后封闭本轮。'))return;await cmd('rollout.close',{audit_ids:JSON.parse(d.get('audit_ids'))});v.verification=null;await render();}
    else if(f.id==='op-parallel'){await cmd('parallel.create',{tasks:[d.get('a'),d.get('b'),...(d.get('c')?[d.get('c')]:[])]});await render();}
    else if(f.id==='op-import'){await cmd('backup.restore',{source:d.get('source'),manifest_hash:d.get('manifest_hash')});await render();}
    else if(f.id==='op-relocation'){await cmd('project.relocate.prepare',{project_id:d.get('project')});await render();}
    else if(f.dataset.relocation){const r=v.persistence.relocations.find(r=>r.id===f.dataset.relocation);if(!await confirm('只更新同一项目的当前操作路径，核对准确文件及缺失状态，历史不改写；旧授权撤销。'))return;await cmd('project.relocate.commit',{relocation_id:r.id,preview_hash:r.preview_hash,new_root:d.get('new_root')});await render();}
  }catch(error){toast(error.message);}finally{if(b.isConnected)b.disabled=false;}});
  window.WorkdeskModules.onLeave('operations',()=>{v.seq++;});
  window.WorkdeskOperations={render};
})();
