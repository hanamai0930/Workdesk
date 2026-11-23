/* Conversation actions use the public conversation projection and local commands. */
window.WorkdeskCooperationToolbar=(()=>{
  'use strict';
  const $=WorkdeskModules.dom('workitems');
  const mounted=new WeakMap();
  const stageNames={plan:'P · 计划',do:'D · 执行',check:'C · 检查',act:'A · 改进'};
  const modeNames={fixed:'固定流程',leader:'负责人带队',host_experts:'主持人—专家'};
  const glyph=(paths)=>`<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  const more=glyph('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>');
  const share=glyph('<path d="M12 15V3m-4 4 4-4 4 4M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/>');
  const tool=(type,label,icon)=>`<button type="button" class="wi-chat-toggle" data-cooperation-toolbar-trigger="${type}"
    popovertarget="wi-cooperation-${type}" aria-haspopup="dialog" aria-expanded="false" aria-label="${label}" title="${label}" disabled>${icon}</button>`;
  const action=(name,label)=>`<button type="button" data-cooperation-action="${name}">${label}</button>`;
  const href=data=>'#work-items/conversation/'+encodeURIComponent(data.id);
  function conversationUrl(data){
    const url=new URL(location.href);
    url.search='';
    url.hash=href(data);
    return url.href;
  }
  function transcript(data){
    const messages=(data.messages||[]).map(message=>({
      speaker:message.role==='user'?'User':data.mode==='host_experts'?'主持人':'负责人',
      text:message.text,
    }));
    for(const part of data.stages||[])messages.push({
      speaker:`第 ${part.round} 轮 · ${stageNames[part.stage]||part.stage}${part.model?' · '+part.model:''}`,
      text:part.text,
    });
    const handoff=(data.handoff?.units||[]).map(unit=>({summary:unit.summary,files:Object.keys(unit.manifest||{})}));
    return {title:data.title,mode:modeNames[data.mode]||data.mode,rounds:data.rounds,brief:data.brief,
      messages,resumeNotes:(data.resume_notes||[]).map(note=>note.text),handoff};
  }
  function markdown(data){
    const value=transcript(data);
    const sections=[`# ${value.title}`,`${value.mode} · ${value.rounds} 轮`];
    if(value.brief){
      const labels={goal:'任务目标',deliverables:'交付物',constraints:'约束',acceptance:'验收条件'};
      const brief=Object.keys(labels).filter(key=>value.brief[key]).map(key=>`- ${labels[key]}：${value.brief[key]}`);
      if(brief.length)sections.push('## 任务说明\n\n'+brief.join('\n'));
    }
    for(const message of value.messages)if(message.text)sections.push(`## ${message.speaker}\n\n${message.text}`);
    if(value.resumeNotes.length)sections.push('## 续跑说明\n\n'+value.resumeNotes.join('\n\n'));
    if(value.handoff.length)sections.push('## 交付\n\n'+value.handoff.map(unit=>[unit.summary,...unit.files.map(file=>'• '+file)].filter(Boolean).join('\n')).join('\n\n'));
    return sections.join('\n\n')+'\n';
  }
  function update(host){
    const state=mounted.get(host);
    if(!state||!$('#main').contains(host))return;
    const data=state.options.getData();
    const record=data?.record;
    for(const control of host.querySelectorAll('[data-cooperation-toolbar-trigger]'))control.disabled=!data||state.busy;
    const rename=host.querySelector('[data-cooperation-action="rename"]');
    const pin=host.querySelector('[data-cooperation-action="pin"]');
    const move=host.querySelector('[data-cooperation-action="move"]');
    const unfile=host.querySelector('[data-cooperation-action="unfile"]');
    const archive=host.querySelector('[data-cooperation-action="archive"]');
    rename.disabled=pin.disabled=!record;
    move.disabled=!record?.can_archive_or_move;
    unfile.hidden=!record?.project;
    unfile.disabled=move.disabled;
    archive.disabled=!record?.archived&&!record?.can_archive_or_move;
    pin.textContent=record?.pinned?'取消置顶':'置顶';
    archive.textContent=record?.archived?'取消归档':'归档';
  }
  function prompt(host,title,label,name,value,choices,description){
    const {h}=window.WorkdeskSupport;
    const dialog=host.querySelector('dialog');
    if(dialog.open)return Promise.resolve(null);
    const field=choices
      ?`<select class="wk-input" name="${name}">${choices.map(([key,caption])=>`<option value="${h(key)}" ${key===value?'selected':''}>${h(caption)}</option>`).join('')}</select>`
      :`<input class="wk-input" name="${name}" value="${h(value)}" required maxlength="160" spellcheck="false">`;
    dialog.innerHTML=`<form><h2>${h(title)}</h2>${description?`<p class="wk-note">${h(description)}</p>`:''}
      <label class="wk-field">${h(label)}${field}</label><div class="wk-dialog-actions">
        <button type="button" class="wk-button" data-cooperation-dialog-cancel>取消</button>
        <button type="submit" class="wk-button primary">保存</button></div></form>`;
    return new Promise(resolve=>{
      let submitted=null;
      const form=dialog.querySelector('form');
      form.onsubmit=event=>{event.preventDefault();submitted=new FormData(form).get(name);dialog.close();};
      dialog.querySelector('[data-cooperation-dialog-cancel]').onclick=()=>dialog.close();
      dialog.onclose=()=>{dialog.onclose=null;resolve(submitted);};
      dialog.showModal();
      const input=form.elements[name];
      input.focus();
      if(input.tagName==='INPUT')input.select();
    });
  }
  async function organize(state,data,changes){
    const options=state.options;
    if(!options.isCurrent())return;
    await WorkdeskSupport.command('work-item.cooperation-organize',{id:data.id,revision:data.record.revision,changes});
    if(!options.isCurrent())return;
    await Promise.all([options.refresh(),window.WorkdeskWorkbench.load()]);
    if(options.isCurrent())window.WorkdeskWorkbench.renderSidebar();
  }
  async function act(host,state,name,data){
    const {options}=state;
    const support=window.WorkdeskSupport;
    if(name==='link')return window.WorkdeskWorkbench.copyMessage(href(data));
    if(name==='copy')return window.WorkdeskWorkbench.copyMessage(markdown(data));
    if(name==='window'){window.open(conversationUrl(data),'_blank','noopener,noreferrer');return;}
    if(name==='markdown')return support.download(markdown(data),'workdesk-cooperation.md','text/markdown;charset=UTF-8');
    if(name==='json')return support.download(JSON.stringify(transcript(data),null,2),'workdesk-cooperation.json','application/json');
    if(name==='rename'){
      const title=await prompt(host,'重命名聊天','聊天名称','title',data.title);
      if(title!==null&&title.trim()!==data.title)await organize(state,data,{title:title.trim()});
      return;
    }
    if(name==='pin')return organize(state,data,{pinned:!data.record.pinned});
    if(name==='unfile')return organize(state,data,{project:null});
    if(name==='archive'){
      const archived=!data.record.archived;
      await organize(state,data,{archived});
      if(options.isCurrent())toast(archived?'已归档。可在原项目的工作任务列表中打开该聊天并取消归档。':'已取消归档。');
      return;
    }
    if(name==='move'){
      const result=await support.request('workspace?view=workbench');
      if(!options.isCurrent())return;
      const choices=[['','未归入项目'],...result.projects.map(project=>[project.id,project.name])];
      const project=await prompt(host,'移动到项目','目标项目','project',data.record.project||'',choices,
        '调整左侧列表中的归属。任务仍在原工作区执行和交付。');
      if(project!==null&&project!==(data.record.project||''))await organize(state,data,{project:project||null});
      return;
    }
    if(name==='fork-independent'||name==='fork-cooperation'){
      const cooperative=name==='fork-cooperation';
      const id='fork-'+crypto.randomUUID();
      const goal=data.brief?.goal||data.messages?.find(message=>message.role==='user')?.text||data.title;
      const context=markdown(data);
      const input=`${goal.slice(0,3800)}\n\n原合作聊天记录\n${context.length>11300?'…\n':''}${context.slice(-11300)}`;
      await support.command('draft.save',{id,revision:0,value:{mode:'work',type:cooperative?'cooperative':'independent',
        title:(data.title+' · 分叉').slice(0,160),project:data.project_id,input,files:'',attachments:[],workPlanOnly:false,workAccess:'workspace',
        source:{kind:'cooperation',id:data.id},formDraft:cooperative?{cooperation:{mode:data.mode,rounds:data.rounds}}:{}}});
      if(options.isCurrent())location.hash='#work/draft/'+encodeURIComponent(id);
    }
  }
  function mount(host,options){
    if(mounted.has(host))return;
    const state={options,busy:false};
    mounted.set(host,state);
    host.innerHTML=`${tool('menu','对话操作',more)}${tool('share','分享或导出',share)}
      <div id="wi-cooperation-menu" class="wi-cooperation-action-panel" popover="auto" role="dialog" aria-label="对话操作">
        ${action('rename','重命名')}${action('pin','置顶')}${action('move','移动到项目')}${action('unfile','移出项目')}${action('archive','归档')}<hr>
        ${action('fork-independent','分叉为独立工作草稿')}${action('fork-cooperation','分叉为合作工作草稿')}
        ${action('copy','复制聊天内容')}${action('link','复制本地聊天链接')}${action('window','在新窗口中打开')}
      </div>
      <div id="wi-cooperation-share" class="wi-cooperation-action-panel" popover="auto" role="dialog" aria-label="分享或导出">
        ${action('link','复制本地聊天链接')}${action('copy','复制聊天内容')}<hr>
        ${action('markdown','下载 Markdown')}${action('json','下载 JSON')}
      </div>
      <dialog class="wb-dialog wi-cooperation-action-dialog" data-ui-module="workitems"></dialog>`;
    for(const panel of host.querySelectorAll('[popover]')){
      const trigger=host.querySelector(`[popovertarget="${panel.id}"]`);
      panel.addEventListener('toggle',()=>trigger.setAttribute('aria-expanded',String(panel.matches(':popover-open'))));
    }
    host.addEventListener('click',async event=>{
      const button=event.target.closest('[data-cooperation-action]');
      const data=options.getData();
      if(!button||button.disabled||!data||state.busy||!options.isCurrent())return;
      button.closest('[popover]').hidePopover();
      state.busy=true;
      update(host);
      try{await act(host,state,button.dataset.cooperationAction,data);}
      catch(error){if(options.isCurrent())toast(error.message);}
      finally{
        state.busy=false;
        if(options.isCurrent())update(host);
      }
    });
    update(host);
  }
  return {mount,update};
})();
