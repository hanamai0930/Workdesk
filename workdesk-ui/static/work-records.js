/* Work-list actions target frozen record identities, independently of selection. */
window.WorkdeskWorkRecords=function(B){
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,h,b,command,closeMenu,renderSidebar,go,load,scenes}=B;
  function modal(...args){B.modal(...args);$('#wb-dialog').dataset.uiModule='workbench';}
  let selecting=false,selected=new Set(),context=null,pendingDelete=null,pendingMove=null;
  const key=r=>r.kind+':'+r.id;
  const rows=()=>(v.co?.sidebar_work_records||B.records()).filter(r=>!r.archived);
  const projects=()=>v.co?.work_projects||v.data?.projects||[];
  const snapshot=r=>({kind:r.kind,id:r.id,revision:r.revision});
  function find(k){const r=rows().find(r=>key(r)===k);if(!r)throw Error('记录已变化，请刷新列表。');return r;}
  function render(){
    const visible=rows();selected=new Set([...selected].filter(k=>visible.some(r=>key(r)===k)));
    const [page,kind,id,scene]=location.hash.split('/');
    const chats=v.chats.filter(chat=>chat.list_project&&!chat.archived).map(chat=>({
      kind:'chat',id:chat.id,project:chat.list_project,title:chat.title,pinned:chat.pinned,created:chat.created
    }));
    const cooperation=(v.co?.sidebar_cooperation_conversations||[]).map(r=>({...r,kind:'cooperation'}));
    const active=(page==='#chat'||page==='#work-items'&&kind==='view-chat')&&v.activeChat?'chat:'+v.activeChat:
      page==='#work-items'&&(kind==='conversation'||kind==='item'&&cooperation.some(row=>row.id===id))?'cooperation:'+id:
      page==='#work-items'&&kind==='view-work'?'task:'+id:
      page==='#work'&&(kind==='task'||kind==='dialogue')?kind+':'+id:null;
    const activeUnfiled=page==='#work'&&kind==='project'&&id==='unfiled'?scene:null;
    window.WorkdeskReact.workRecords($('#wb-sidebar'),{rows:visible,chats,cooperation,projects:projects(),scenes,
      active,activeUnfiled,activeProject:window.WorkdeskWorkItemsPage?.projectId?.(),selecting,selected:[...selected]});
  }
  function menu(anchor,item){
    const project=anchor.dataset.project,scene=anchor.dataset.scene;
    if(anchor.dataset.wbMenu==='work-project'){
      const group=rows().filter(r=>project?r.project===project:!r.project&&(r.group_scene||(scenes[r.scene]?r.scene:'legacy'))===scene);
      const p=projects().find(p=>p.id===project);
      if(!group.length&&!p)throw Error('此项目分组已变化。');
      context={type:'project',project:p||null,rows:group,refs:group.map(snapshot)};
    }else {const r=find(anchor.dataset.key);context={type:'record',rows:[r],refs:[snapshot(r)]};}
    const p=context.project,r=context.rows[0],attrs='role="menuitem"';
    const overview=p?item('项目概览','work-record-project-overview','folder',attrs):'';
    const rename=context.type==='record'||p?item('重命名','work-record-rename','edit',attrs):'';
    const pin=context.type==='record'||p?item((p?p.pinned:r.pinned)?'取消置顶':'置顶','work-record-pin','pin',attrs):'';
    const multi=context.rows.length?item(context.type==='project'?'选择本组记录（'+context.rows.length+'）':'多选','work-record-multi','list',attrs):'';
    const remove=context.type==='record'?'<div class="wk-menu-divider"></div>'+item('删除','work-record-delete','trash',attrs+' data-work-delete data-work-delete-key="'+h(key(r))+'" data-work-delete-revision="'+h(r.revision)+'"'):'';
    return overview+rename+pin+multi+item('归入场景','work-record-scene','research',attrs)+(context.rows.length?item('归入项目','work-record-project','folder',attrs):'')+remove;
  }
  function chosen(){return [...selected].map(find);}
  function askDelete(chosenRows,mode){
    if(!chosenRows.length)return;
    if(mode==='single'&&chosenRows.length!==1)throw Error('单条删除只能选择一条聊天，请使用该聊天自己的菜单。');
    pendingDelete={records:chosenRows.map(snapshot),mode};
    modal(mode==='single'?'删除这条Work聊天？':'删除已选的 '+chosenRows.length+' 条Work记录？',
      '<p class="wk-note">删除所选Work列表记录及其草稿，取消关联计划。项目文件、原执行证据和用量保留；此处不提供恢复入口。</p><ul class="wd-work-record-list">'+chosenRows.slice(0,8).map(r=>'<li>'+h(r.title)+'</li>').join('')+'</ul>'+(chosenRows.length>8?'<p class="wk-note">共 '+chosenRows.length+' 条</p>':''),
      b('取消','close')+b(mode==='single'?'删除这1条':'删除已选的'+chosenRows.length+'条','work-record-delete-confirm','data-work-delete'));
  }
  function askMove(chosenRows,field,project=null){
    if(!chosenRows.length&&!project)return;
    pendingMove={records:chosenRows.map(snapshot),field,project:project?{id:project.id,revision:project.revision}:null};
    const options=field==='scene'?[{id:'',name:project?'普通项目':'未分类'},...Object.entries(scenes).map(([id,s])=>({id,name:s.name}))]:[{id:'',name:'未归入项目'},...projects()];
    modal(field==='scene'?'归入场景':'归入项目','<form id="wb-work-record-move-form"><p class="wk-note">'+(project?'整理项目“'+h(project.name)+'”与本组 '+chosenRows.length+' 条记录的场景。':'整理所选 '+chosenRows.length+' 条Work记录的归属。')+'原执行工作区、模型配置和权限保持不变。</p><label class="wk-field">'+(field==='scene'?'目标场景':'目标项目')+'<select name="target" class="wk-input" aria-label="'+(field==='scene'?'目标场景':'目标项目')+'">'+options.map(p=>'<option value="'+h(p.id)+'">'+h(p.name)+'</option>').join('')+'</select></label><button type="submit" class="wk-button primary">确认归入</button></form>',b('取消','close'));
  }
  async function changed(){await load();renderSidebar();}
  function focusMoveDestination(move,target){
    const projectId=move.project?.id||(move.field==='project'&&target);
    const first=move.records[0];
    const row=projectId?$('#wb-sidebar [data-work-project="'+CSS.escape(projectId)+'"]'):
      first&&$('#wb-sidebar [data-work-key="'+CSS.escape(key(first))+'"]');
    if(!row)return;
    const scene=row.closest('details[data-work-scene]');
    if(scene)scene.open=true;
    const visibleRow=row.closest('[hidden]')?row.closest('.wd-work-branch')?.querySelector('[data-work-project]'):row;
    const control=visibleRow?.querySelector('[data-wb-menu]')||visibleRow?.querySelector('.wk-record');
    control?.focus();
  }
  async function act(action,el){
    if(action==='work-record-project-overview'){
      if(context?.project)await go('#work-items/project/'+encodeURIComponent(context.project.id));
      return true;
    }
    if(action==='work-record-toggle'){const k=el.dataset.key;find(k);selected.has(k)?selected.delete(k):selected.add(k);renderSidebar();return true;}
    if(action==='work-record-all'){const visible=rows();selected=visible.every(r=>selected.has(key(r)))?new Set():new Set(visible.map(key));renderSidebar();return true;}
    if(action==='work-record-finish'){selecting=false;selected.clear();renderSidebar();return true;}
    if(action==='work-record-multi'){selecting=true;selected=new Set(context.rows.map(key));closeMenu();renderSidebar();return true;}
    if(action==='work-record-rename'){
      const target=context.type==='project'?context.project:context.rows[0];
      modal(context.type==='project'?'重命名项目':'重命名Work记录','<form id="wb-work-record-rename-form"><label class="wk-field">名称<input class="wk-input" name="title" required maxlength="'+(context.type==='project'?100:160)+'" value="'+h(target.name||target.title)+'"></label><button type="submit" class="wk-button primary">保存</button></form>');return true;
    }
    if(action==='work-record-pin'){
      const p=context.project;
      if(context.type==='project')await command('work.project-record',{project_id:p.id,revision:p.revision,pinned:!p.pinned});
      else await command('work.records-organize',{records:context.refs,changes:{pinned:!context.rows[0].pinned}});
      closeMenu();await changed();return true;
    }
    if(action==='work-record-scene'||action==='work-record-project'){askMove(context.rows,action==='work-record-scene'?'scene':'project',action==='work-record-scene'&&context.type==='project'?context.project:null);return true;}
    if(action==='work-record-scene-selected'||action==='work-record-project-selected'){askMove(chosen(),action==='work-record-scene-selected'?'scene':'project');return true;}
    if(action==='work-record-delete'){
      const boundKey=el?.dataset.workDeleteKey,revision=el?.dataset.workDeleteRevision;
      if(!boundKey||!revision)throw Error('单条删除入口已失效，请重新打开这条聊天的菜单。');
      const r=find(boundKey);askDelete([{...r,revision}],'single');return true;
    }
    if(action==='work-record-delete-selected'){if(!selecting)throw Error('请先进入多选并勾选要删除的记录。');askDelete(chosen(),'selected');return true;}
    if(action==='work-record-delete-confirm'){
      if(!pendingDelete?.records.length)return true;
      const refs=pendingDelete.records,mode=pendingDelete.mode,ids=refs.map(r=>r.id),origin=location.hash;
      const restore=await B.suspendDrafts(ids);
      try {await command('work.records-delete',{records:refs,confirm:true,mode,...(mode==='selected'?{selection_count:refs.length}:{})});}catch(exc){restore();throw exc;}
      for(const id of ids)setLocal('composer-backup:composer:work:'+id,null);
      pendingDelete=null;selecting=false;selected.clear();$('#wb-dialog').close();await changed();
      if(location.hash===origin&&ids.includes(v.dialogue?.id||v.detail?.task.id)){v.input='';await go('#work');}
      toast('已删除 '+ids.length+' 条Work记录。项目文件和执行审计已保留。');return true;
    }
    return false;
  }
  async function submit(form){
    if(form.id==='wb-work-record-rename-form'){
      const title=new FormData(form).get('title');
      if(context.type==='project')await command('work.project-record',{project_id:context.project.id,revision:context.project.revision,title});
      else await command('work.records-organize',{records:context.refs,changes:{title}});
      $('#wb-dialog').close();await changed();return true;
    }
    if(form.id==='wb-work-record-move-form'){
      const move=pendingMove,dialog=form.closest('dialog');
      if(!move)throw Error('归入清单已失效，请重新选择。');
      const field=form.elements.namedItem('target'),target=field.value;
      const button=form.querySelector('[type=submit]');
      field.disabled=true;
      if(!move.saved){
        try{
          if(move.project)await command('work.project-record',{project_id:move.project.id,revision:move.project.revision,scene:target||null,records:move.records});
          else await command('work.records-organize',{records:move.records,changes:{[move.field]:target||null}});
          move.saved=true;
        }catch(error){field.disabled=false;throw error;}
      }
      button.textContent='正在刷新列表…';
      try{await changed();}catch(error){
        button.textContent='重新刷新列表';
        throw Error('归属已保存，列表刷新失败。'+error.message);
      }
      if(pendingMove===move)pendingMove=null;
      if(form.isConnected&&dialog.open){
        dialog.close();focusMoveDestination(move,target);
        toast('记录归属已更新；原执行工作区与权限保持不变。');
      }
      return true;
    }
    return false;
  }
  function leave(){selecting=false;selected.clear();}
  return {render,menu,act,submit,leave};
};
