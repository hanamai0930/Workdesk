/* Cross-mode conversation directory. Commands operate on the selected record identity. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('records'),S=window.WorkdeskSupport,{h}=S;
  const modes={all:'全部',chat:'Chat',independent:'独立 Work',cooperation:'合作 Work'};
  const cooperationModes={fixed:'固定流程',leader:'负责人带队',host_experts:'主持人—专家'};
  const cooperationStates={DISCUSSING:'讨论中',PREPARING:'准备方案',PLAN_READY:'方案已就绪',PAUSED:'已暂停',SCHEDULED:'等待派发',ABANDONED:'已结束'};
  const archives=[['all','全部'],['active','未归档'],['archived','已归档']];
  const v={rows:[],data:null,q:'',mode:'all',archive:'all',input:{q:'',archive:'all'},seq:0,busy:false,error:'',edit:null,focusSearch:false};
  const active=()=>location.hash.split('/')[0]==='#records';
  const isCooperation=r=>r.kind==='cooperation';
  const recordKey=r=>r.kind+':'+r.id;

  function detailHref(r){
    const id=encodeURIComponent(r.id);
    if(r.kind==='chat')return '#work-items/view-chat/'+id+'/from-records';
    if(r.kind==='task'&&r.independent_work)return '#work-items/view-work/'+id+'/from-records';
    if(isCooperation(r)&&r.detail_href)return r.detail_href+'/from-records';
    return '';
  }

  function recordMetadata(r){
    const progress=r.progress;
    const parts=[r.project_name,r.provider,cooperationModes[r.cooperation_mode],cooperationStates[r.state]||S.state(r.state)];
    if(isCooperation(r)&&progress){
      if(Number.isInteger(progress.round_count)&&progress.round_count>0)parts.push('计划 '+progress.round_count+' 轮');
      if(Number.isInteger(progress.dispatches)&&Number.isInteger(progress.limit)&&progress.limit>0){
        parts.push('派发 '+progress.dispatches+' / '+progress.limit+' 次');
      }
    }
    if(r.created)parts.push(S.when(r.created));
    if(r.archived)parts.push('已归档');
    if(r.pinned)parts.push('已置顶');
    return parts.filter(Boolean).join(' · ');
  }

  function recordActions(r){
    const actions=[['rename','重命名'],['pin',r.pinned?'取消置顶':'置顶']];
    if(!isCooperation(r)||r.archived||r.can_archive)actions.push(['archive',r.archived?'取消归档':'归档']);
    if(!isCooperation(r))actions.push(['md','导出 Markdown'],['json','导出 JSON'],['delete','删除']);
    return `<details class="records-more"><summary aria-haspopup="menu" aria-label="${h(r.title)}的更多操作">更多操作</summary>
      <div class="records-menu" role="menu" aria-label="记录操作">${actions.map(([action,label])=>S.button(label,
        `role="menuitem" data-rec="${action}" data-kind="${h(r.kind)}" data-id="${h(r.id)}"${action==='delete'?' class="records-delete"':''}`)).join('')}</div>
    </details>`;
  }

  function recordCard(r){
    const type=r.kind==='chat'?modes.chat:isCooperation(r)?modes.cooperation:r.kind==='dialogue'?'合作 Work · 历史讨论':
      r.independent_work?modes.independent:'独立／后台工作';
    const detail=detailHref(r);
    return `<article class="sp-card records-card" data-record-kind="${h(r.kind)}">
      <div class="sp-row records-card-heading">
        <a class="sp-title" href="${h(r.href)}">${h(r.title)}</a>
        <div class="records-card-aside"><span class="sp-badge">${h(type)}</span>
          ${detail?`<a class="records-detail-link" href="${h(detail)}">查看详情</a>`:''}</div>
      </div>
      <p class="sp-note">${h(recordMetadata(r))}</p>
      <div class="sp-actions">${recordActions(r)}</div>
    </article>`;
  }

  function draw(){
    if(!active())return;
    const previous=$('#records-filter [name="q"]');
    const searchSelection=previous===document.activeElement?{start:previous.selectionStart,end:previous.selectionEnd}:null;
    $('#main').innerHTML=`<section class="sp-page">
      ${S.nav('records')}
      <header class="sp-heading"><div><h1>全部对话记录</h1><p>查找 Chat、独立 Work 和合作 Work 的历史对话。</p></div></header>
      ${S.subnav('records')}
      <div class="records-modes" role="group" aria-label="记录类型">
        ${Object.entries(modes).map(([mode,label])=>`<button type="button" data-record-mode="${mode}" aria-pressed="${mode===v.mode}">${label}</button>`).join('')}
      </div>
      <form id="records-filter" class="sp-filters">
        ${S.field('搜索名称或正文','q',v.input.q,'maxlength="200" placeholder="查找所有已保存记录"')}
        ${S.select('归档状态','archive',archives,v.input.archive)}
        <button type="submit">搜索</button>
        ${(v.q||v.mode!=='all'||v.archive!=='all')&&(!v.data||v.rows.length)?S.button('清除筛选','data-rec="clear"'):''}
      </form>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      ${v.edit?`<form id="records-rename">${S.field('记录名称','title',v.edit.title,'required maxlength="160"')}
        <div class="records-edit-actions"><button type="submit">保存名称</button>${S.button('取消','data-rec="cancel"')}</div>
      </form>`:''}
      ${v.data?`<p class="sp-note" aria-live="polite">共 ${h(v.data.total)} 条，已显示 ${v.rows.length} 条。</p>
        <div class="sp-list">${v.rows.map(recordCard).join('')||`<div class="sp-empty">${v.q||v.mode!=='all'||v.archive!=='all'?
          `没有匹配记录。${S.button('清除筛选','data-rec="clear"')}`:
          '暂无保存的对话。<a href="#chat">新建 Chat</a> · <a href="#work">开始工作</a>'}</div>`}</div>
        ${v.data.has_more?S.button('加载更多','data-rec="more"'):''}`:
        v.error?'':'<p class="sp-note" role="status">正在读取记录…</p>'}
    </section>`;
    if(v.focusSearch||searchSelection){
      const input=$('#records-filter [name="q"]');
      if(input?.isConnected){input.focus();v.focusSearch=false;if(searchSelection)input.setSelectionRange(searchSelection.start,searchSelection.end);}
    }
  }

  async function load(more=false){
    const seq=++v.seq;
    const params=new URLSearchParams({scope:'directory',q:v.q,mode:v.mode,archive:v.archive});
    if(more&&v.data?.next_cursor)params.set('cursor',v.data.next_cursor);
    try{
      const data=await S.request('record-catalog?'+params);
      if(seq!==v.seq||!active())return;
      v.rows=more?[...new Map([...v.rows,...data.records].map(r=>[recordKey(r),r])).values()]:data.records;
      v.data=data;
    }catch(error){
      if(seq!==v.seq||!active())return;
      v.error=error.message;
    }
    draw();
  }

  async function changedFilters(mode=v.mode){
    v.mode=mode;
    v.q=v.input.q;
    v.archive=v.input.archive;
    v.rows=[];
    v.data=null;
    v.edit=null;
    draw();
    await load();
  }

  async function changeRecord(r,changes){
    if(isCooperation(r))return S.command('work-item.cooperation-organize',{id:r.id,revision:r.revision,changes});
    if(r.kind==='chat')return S.command('chat.record',{id:r.id,...changes});
    return S.command('work.record',{id:r.id,kind:r.kind,...changes});
  }

  async function operate(action,r){
    if(action==='rename'){
      v.edit=r;
      draw();
      $('#records-rename input')?.focus();
      return;
    }
    if(action==='md'||action==='json'){
      const full=await S.request('conversation?'+new URLSearchParams({kind:r.kind,id:r.id}));
      S.download(action==='md'?full.markdown:JSON.stringify(full,null,2),'workdesk-conversation.'+(action==='md'?'md':'json'),action==='md'?'text/markdown;charset=utf-8':'application/json');
      return;
    }
    if(action==='delete'){
      if(!await S.confirm('删除这一条记录？',r.title,r.kind==='chat'?'删除本地聊天与草稿；官网会话保留。':'删除Work聊天入口与草稿，保留项目文件和执行审计。',{tone:'danger',confirmLabel:'删除记录'}))return;
      const restore=await window.WorkdeskWorkbench.suspendRecordDrafts(r.kind,[r.id]);
      const reference={id:r.id,revision:r.revision};
      try{
        if(r.kind==='chat')await S.command('chat.delete',{chats:[reference],confirm:true});
        else await S.command('work.records-delete',{records:[{kind:r.kind,...reference}],confirm:true,mode:'single'});
      }catch(error){restore();throw error;}
      window.WorkdeskWorkbench.forgetRecord(r.kind,r.id);
    }else{
      await changeRecord(r,action==='pin'?{pinned:!r.pinned}:{archived:!r.archived});
    }
    if(!active())return;
    await S.refreshChrome();
    if(active())await load();
  }

  const host=$('#main');
  function closeMenus(except=null){
    host.querySelectorAll('.records-more[open]').forEach(menu=>{if(menu!==except)menu.open=false;});
  }
  host.addEventListener('toggle',event=>{
    const menu=event.target;
    if(!menu.matches?.('.records-more')||!menu.open)return;
    closeMenus(menu);
    requestAnimationFrame(()=>{if(menu.isConnected&&menu.open&&!document.activeElement?.matches('[role="menuitem"]'))menu.querySelector('[role="menuitem"]')?.focus();});
  },true);
  host.addEventListener('keydown',event=>{
    const menu=event.target.closest('.records-more');
    if(!menu)return;
    if(event.key==='Escape'){
      event.preventDefault();event.stopPropagation();menu.open=false;menu.querySelector('summary')?.focus();return;
    }
    const entries=[...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
    if(!entries.length)return;
    const index=entries.indexOf(document.activeElement);
    const target={ArrowDown:(index+1)%entries.length,ArrowUp:(index-1+entries.length)%entries.length,Home:0,End:entries.length-1}[event.key];
    if(target===undefined)return;
    event.preventDefault();menu.open=true;entries[target]?.focus();
  });
  host.addEventListener('focusout',event=>{
    const menu=event.target.closest('.records-more');
    if(menu&&!menu.contains(event.relatedTarget))menu.open=false;
  });
  document.addEventListener('click',event=>{if(active()&&!event.target.closest('.records-more'))closeMenus();});
  host.addEventListener('input',event=>{
    const field=event.target;
    if(field.form?.id==='records-filter'&&Object.hasOwn(v.input,field.name))v.input[field.name]=field.value;
  });
  host.addEventListener('click',async event=>{
    const button=event.target.closest('[data-record-mode],[data-rec]');
    if(!button||!active()||v.busy)return;
    v.busy=true;
    v.error='';
    try{
      const mode=button.dataset.recordMode;
      if(mode){if(mode!==v.mode)await changedFilters(mode);return;}
      const action=button.dataset.rec;
      if(action==='clear'){v.input={q:'',archive:'all'};return await changedFilters('all');}
      if(action==='more')return await load(true);
      if(action==='cancel'){v.edit=null;draw();return;}
      const r=v.rows.find(r=>r.id===button.dataset.id&&r.kind===button.dataset.kind);
      if(r){const menu=button.closest('.records-more');if(menu){menu.open=false;menu.querySelector('summary')?.focus();}await operate(action,r);}
    }catch(error){if(active()){v.error=error.message;draw();}}
    finally{v.busy=false;}
  });
  host.addEventListener('submit',async event=>{
    const form=event.target;
    if(!['records-filter','records-rename'].includes(form.id))return;
    event.preventDefault();
    if(!active()||v.busy)return;
    v.busy=true;
    v.error='';
    try{
      const data=new FormData(form);
      if(form.id==='records-filter'){
        v.input={q:String(data.get('q')),archive:String(data.get('archive'))};
        return await changedFilters();
      }
      const record=v.edit;
      if(!record)return;
      await changeRecord(record,{title:data.get('title')});
      if(!active())return;
      v.edit=null;
      await S.refreshChrome();
      if(active())await load();
    }catch(error){if(active()){v.error=error.message;draw();}}
    finally{v.busy=false;}
  });
  window.WorkdeskModules.onLeave('records',()=>{++v.seq;v.edit=null;v.focusSearch=false;});
  window.WorkdeskRecordsPage={
    render:()=>{v.edit=null;v.error='';draw();return load();},
    focusSearch:()=>{v.focusSearch=true;const input=$('#records-filter [name="q"]');if(active()&&input?.isConnected){input.focus();v.focusSearch=false;return true;}return false;}
  };
})();
