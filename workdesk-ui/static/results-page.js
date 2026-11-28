/* Explicit Human acceptance of a frozen result, separate from quality evidence. */
(()=>{
  'use strict';
  const $=WorkdeskModules.dom('results'),S=WorkdeskSupport,{h}=S;
  let mounted=null;
  const active=v=>mounted===v&&v.host.isConnected&&v.route===location.hash;
  const ref=r=>({id:r.id,revision:r.revision,snapshot_hash:r.snapshot_hash});
  const latest=v=>v.detail?.attempts.at(-1);
  const results=v=>(v.data?.results||[]).filter(r=>!v.task||r.task_id===v.task);

  function contextBack(v,task){
    const [,id,origin]=v.route.split('/');
    if(!id||origin==='from-tasks')return '<a class="text-button" href="#work-items">‹ 返回任务列表</a>';
    const item=task?.spec?.cooperative?.item_id;
    const href=item?'#work-items/item/'+encodeURIComponent(item)+'/delivery':
      task?.spec?.work_mode==='independent'?'#work-items/view-work/'+encodeURIComponent(task.id)+(origin==='from-records'?'/from-records':''):'#monitor';
    return `<a class="text-button" href="${href}">‹ ${href==='#monitor'?'返回运行':'返回任务详情'}</a>`;
  }

  function captureForm(v,task){
    if(!task)return '';
    const a=latest(v);
    const name=v.embedded?'<h2>保存本回合成果</h2>':
      `<div class="sp-row"><h2>${h(task.title)}</h2><a href="#${task.spec.runtime_mode==='official'?'work':'control'}/task/${encodeURIComponent(task.id)}">查看工作</a></div>`;
    const description=a?.state==='COMPLETED'?'本回合已完成，可保存为成果版本。':
      a?'最近回合：'+S.state(a.state)+'；完成后可保存成果。':'尚无运行回合。';
    const status=[description,v.embedded?'':a?.id].filter(Boolean).join(' · ');
    const form=task.spec.runtime_mode==='official'? `<form id="results-capture">
      ${S.text('要保存的成果文件（项目相对路径，每行一个；可留空仅保存回复）','files',v.files)}
      <p class="sp-note">保存回复和所选文件的副本，项目原件保留在原处。</p>
      <button type="submit" ${a?.state==='COMPLETED'?'':'disabled'}>保存本回合成果</button>
    </form>`:'<p>此任务使用原有候选、检查和整合流程，请在工作详情中处理。</p>';
    return `<section class="sp-card">${name}<p class="sp-note">${h(status)}</p>${form}</section>`;
  }

  function replyPreview(v){
    const attempt=v.detail?.attempts.findLast(a=>typeof a.result?.answer==='string'&&a.result.answer.trim());
    if(!attempt)return '';
    return `<section class="sp-card"><h2>最近回合回复</h2>
      <p class="sp-note">${h(S.when(attempt.started))} · ${h(S.state(attempt.state))} · 可在保存成果后验收</p>
      <details><summary>查看已记录回复</summary><pre>${h(attempt.result.answer)}</pre></details>
    </section>`;
  }

  function preview(r,p){
    const source=new URLSearchParams({id:r.id,file:p.file,snapshot_hash:r.snapshot_hash,sha256:p.sha256});
    const baseline=p.baseline==='UNCHANGED'?'与开始前已选材料一致':
      p.baseline==='CHANGED'?'相对于开始前材料有变化':'该文件无可用的开始前材料基线';
    const content=p.kind==='text'?`<pre>${h(p.content)}</pre>`:p.kind==='image'?`<img src="${h(p.image)}" alt="成果原件">`:
      p.kind==='pdf'?`<iframe title="成果 PDF 原件" src="api/result-file-preview?${h(source.toString())}"></iframe>`:
      '<p>可下载原件，在对应应用中检查。</p>';
    const difference=p.difference?`<details><summary>与开始前材料的文字差异${p.diff_truncated?'（前200行）':''}</summary><pre>${h(p.difference)}</pre></details>`:'';
    return `<section class="sp-preview"><h2>快照原件：${h(p.file)}</h2><p class="sp-note">${baseline}</p>
      ${content}${difference}${S.button('下载成果原件',`data-result-action="download" data-id="${h(r.id)}"`)}
    </section>`;
  }

  function resultCard(v,r){
    const {verification:check,decision,snapshot}=r;
    const names=Object.keys(snapshot.manifest);
    const status=decision?(decision.value==='accept'?'人工已接受':'人工已退回'):'待人工验收';
    const verification=check?(check.state==='CURRENT'?'上次核对一致':'上次核对发现变化'):'尚未核对';
    const review=snapshot.independent_review==='NOT_RECORDED'?'未记录':snapshot.independent_review;
    const opened=v.preview?.id===r.id&&v.preview.hash===r.snapshot_hash;
    return `<article class="sp-card" data-result="${h(r.id)}">
      <div class="sp-row"><h2>${h(r.title)}</h2><span class="sp-badge">${status}</span></div>
      <p class="sp-note">保存于 ${h(S.when(r.created))} · ${names.length} 个文件</p>
      <p>版本核对：${verification}${check?' · '+h(S.when(check.checked_at)):''}</p>
      ${check?.problems?.length?`<p class="sp-error">${h(check.problems.join('；'))}</p>`:''}
      <p class="sp-note">版本核对只检查文件和配置。独立审查：${h(review)}；官方检查证据：${snapshot.native_checks?'已记录，见详情':'未记录'}。</p>
      ${decision?`<p>验收说明：${h(decision.note||'未填写')} · ${h(S.when(decision.at))}</p>`:''}
      <div class="sp-actions">${names.map(name=>S.button('查看 '+name,`data-result-action="preview" data-id="${h(r.id)}" data-file="${h(name)}"`)).join('')}</div>
      ${opened?preview(r,v.preview.data):''}
      <details><summary>回复与文件快照</summary><pre>${h(snapshot.answer)}</pre>
        ${S.raw({files:snapshot.manifest,configuration:snapshot.configuration,native_checks:snapshot.native_checks,snapshot_hash:r.snapshot_hash})}
      </details>
      <form data-result-form="${h(r.id)}">
        ${S.text('验收说明','note',v.notes.get(r.id)||'','maxlength="4000"')}
        <div class="sp-actions">
          ${S.button('重新核对版本',`data-result-action="verify" data-id="${h(r.id)}"`)}
          ${v.detail?.task.spec.work_mode==='independent'&&latest(v)?.id===r.attempt_id?'<button type="submit" name="decision" value="complete">登记任务完成</button>':''}
          ${decision?.value==='return'&&v.detail?.task.spec.work_mode==='independent'&&latest(v)?.id===r.attempt_id?S.button('在原任务修订此快照',`data-result-action="revise-native" data-id="${h(r.id)}"`):''}
          <button type="submit" name="decision" value="accept">接受此版本</button>
          <button type="submit" name="decision" value="return">退回此版本</button>
        </div>
      </form>
    </article>`;
  }

  function draw(v){
    if(!active(v))return;
    const task=v.detail?.task,rows=results(v);
    const title=v.embedded?'':`${S.nav('results')}${contextBack(v,task)}
      <header class="sp-heading"><div><h1>成果检查与验收</h1><p>保存本回合的回复和指定文件版本，再由你决定接受或退回。</p></div></header>`;
    const selector=v.catalog&&!v.pinned?S.select('选择工作','task',[['','选择一项工作'],
      ...v.catalog.tasks.map(t=>[t.id,t.title+' · '+S.state(t.state)])],v.task):'';
    const error=v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:'';
    const content=v.data?`${v.embedded&&!rows.length?replyPreview(v):''}${captureForm(v,task)}
      <h2>已保存成果</h2>${rows.map(r=>resultCard(v,r)).join('')||`<p class="sp-empty">${!task?'选择一项工作，保存成果版本后再登记验收。':latest(v)?.state==='COMPLETED'?'尚未保存成果版本。请在上方保存本回合回复和指定文件，再登记验收。':'尚未保存成果版本。回合完成后，可在上方保存回复和指定文件，再登记验收。'}</p>`}`:
      v.error?S.button('重新读取','data-result-action="reload"'):'<p class="sp-note" role="status">正在读取成果记录…</p>';
    v.host.innerHTML=`<section class="sp-page${v.embedded?' results-embedded':''}" ${v.embedded?'aria-label="成果与验收"':''}>
      ${title}${error}${selector}${content}
    </section>`;
  }

  async function load(v){
    const seq=++v.seq,task=v.task;
    try{
      const query=task?'?'+new URLSearchParams({task}):'';
      const [data,detail]=await Promise.all([S.request('result-catalog'+query),
        task?S.request('task?'+new URLSearchParams({id:task,after:Number.MAX_SAFE_INTEGER})):null]);
      if(!active(v)||seq!==v.seq)return;
      if(task&&detail?.task?.id!==task)throw new Error('成果记录与任务不匹配。');
      v.data=data;
      v.catalog=data;
      v.detail=detail;
      v.error='';
    }catch(error){
      if(!active(v)||seq!==v.seq)return;
      v.error=error.message;
    }
    draw(v);
  }

  async function operate(v,action){
    if(!active(v)||v.busy)return;
    v.busy=true;
    try{await action();}
    catch(error){if(active(v)){v.error=error.message;draw(v);}}
    finally{v.busy=false;}
  }

  async function handleClick(v,button){
    const action=button.dataset.resultAction;
    if(action==='reload'){await load(v);return;}
    const r=results(v).find(row=>row.id===button.dataset.id);
    if(!r)return;
    if(action==='preview'){
      const data=await S.request('result-preview?'+new URLSearchParams({id:r.id,file:button.dataset.file,snapshot_hash:r.snapshot_hash}));
      if(!active(v))return;
      v.preview={id:r.id,hash:r.snapshot_hash,data};
      draw(v);
    }else if(action==='download'){
      if(v.preview?.id!==r.id||v.preview.hash!==r.snapshot_hash)return;
      const p=v.preview.data,bytes=Uint8Array.from(atob(p.data),c=>c.charCodeAt(0));
      S.download(bytes,p.file.split('/').at(-1),p.mime||'application/octet-stream');
    }else if(action==='revise-native'){
      const a=latest(v);
      if(r.attempt_id!==a?.id||!await S.confirm('接续原工作端修订？',r.title,r.decision?.note||'沿用此成果版本和原会话。')||!active(v))return;
      await S.command('independent.send',{task_id:r.task_id,attempt_id:a.id,owner:a.owner,goal:'按原版本的退修意见修改。',feedback_snapshot:ref(r)});
      if(active(v))location.hash='#work/task/'+encodeURIComponent(r.task_id);
    }else if(action==='verify'){
      const forms=[...v.host.querySelectorAll('[data-result-form]')];
      for(const form of forms){
        for(const control of form.querySelectorAll('button'))control.disabled=true;
      }
      const status=document.createElement('p');
      status.setAttribute('role','status');status.textContent='正在核对成果版本，请稍候…';
      button.closest('form').prepend(status);
      await S.command('result.verify',ref(r));
      if(active(v))await load(v);
    }
  }

  async function handleSubmit(v,form,submitter){
    if(form.id==='results-capture'){
      const a=latest(v);
      if(!v.task||v.detail?.task?.id!==v.task||v.detail.task.spec.runtime_mode!=='official'||a?.state!=='COMPLETED')return;
      const files=v.files.split(/\r?\n/).map(path=>path.trim()).filter(Boolean);
      await S.command('result.capture',{task_id:v.task,attempt_id:a.id,owner:a.owner,files});
    }else{
      const r=results(v).find(row=>row.id===form.dataset.resultForm),decision=submitter?.value;
      if(r&&decision==='complete'){
        const criteria=String(new FormData(form).get('note')||v.detail?.task.spec.acceptance||v.detail?.task.spec.goal||'');
        if(!await S.confirm('确认任务已完成？',r.title,criteria,{confirmLabel:'确认任务完成'})||!active(v))return;
        await S.command('result.task-complete',{...ref(r),confirm:true,criteria});
        await load(v);return;
      }
      if(!r||!['accept','return'].includes(decision))return;
      const note=String(new FormData(form).get('note'));
      const confirmed=await S.confirm(decision==='accept'?'接受此成果版本？':'退回此成果版本？',r.title,'只记录你的验收决定，不会重新运行模型或更改项目文件。',{confirmLabel:decision==='accept'?'接受此版本':'退回此版本'});
      if(!confirmed||!active(v))return;
      await S.command('result.decide',{...ref(r),decision,note,confirm:true});
      v.notes.delete(r.id);
    }
    if(active(v))await load(v);
  }

  function unmount(){
    mounted?.listeners.abort();
    mounted=null;
  }

  function mount(host,task,embedded){
    unmount();
    const listeners=new AbortController(),options={signal:listeners.signal};
    const v={host,task,embedded,pinned:!!task,route:location.hash,listeners,seq:0,data:null,catalog:null,detail:null,error:'',files:'',preview:null,notes:new Map(),busy:false};
    mounted=v;
    host.addEventListener('change',event=>{
      const field=event.target;
      if(!active(v)||v.pinned||field.name!=='task')return;
      if(v.busy){field.value=v.task;return;}
      v.task=field.value;v.data=null;v.detail=null;v.preview=null;v.files='';v.error='';
      draw(v);
      void operate(v,()=>load(v));
    },options);
    host.addEventListener('input',event=>{
      const field=event.target;
      if(active(v)&&field.name==='note'&&field.form?.dataset.resultForm)v.notes.set(field.form.dataset.resultForm,field.value);
      if(active(v)&&field.form?.id==='results-capture'&&field.name==='files')v.files=field.value;
    },options);
    host.addEventListener('click',event=>{
      const button=event.target.closest('[data-result-action]');
      if(button)void operate(v,()=>handleClick(v,button));
    },options);
    host.addEventListener('submit',event=>{
      const form=event.target;
      if(form.id!=='results-capture'&&!form.dataset.resultForm)return;
      event.preventDefault();
      void operate(v,()=>handleSubmit(v,form,event.submitter));
    },options);
    draw(v);
    return load(v);
  }

  WorkdeskModules.onLeave('results',()=>{if(mounted&&!mounted.embedded)unmount();});
  window.WorkdeskResultsPage={
    render:()=>{
      const id=location.hash.split('/')[1];
      return mount($('#main'),id?decodeURIComponent(id):'',false);
    },
    mountTask:(host,id)=>mount(host,id,true),
    unmountTask:()=>{if(mounted?.embedded)unmount();}
  };
})();
