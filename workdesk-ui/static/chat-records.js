/* Chat-list ownership: menu targets never change the selected conversation. */
window.WorkdeskChatRecords=function(B){
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,h,b,command,modal,closeMenu,renderSidebar,go}=B;
  let selecting=false,selected=new Set(),menuId=null,pendingDelete=null;
  const rows=()=>v.chats.filter(c=>!c.archived&&!c.list_project).slice().sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||b.created-a.created);
  const find=id=>{const c=v.chats.find(c=>c.id===id);if(!c)throw Error('聊天已变化，请刷新列表。');return c;};
  function render(){
    selected=new Set([...selected].filter(id=>rows().some(c=>c.id===id)));
    window.WorkdeskReact.chatRecords($('#wb-sidebar'),{rows:rows(),active:v.activeChat,selecting,selected:[...selected],menuId:v.menu==='chat-record'?menuId:null});
  }
  function menu(id,item){
    const c=find(id);menuId=id;
    const attrs='data-id="'+h(id)+'" role="menuitem"';
    return item('重命名','chat-record-rename','edit',attrs)+item(c.pinned?'取消置顶':'置顶','chat-record-pin','pin',attrs)+item('多选','chat-record-multi','list',attrs)+'<div class="wk-menu-divider"></div>'+item('删除','chat-record-delete','trash',attrs+' data-chat-delete');
  }
  function askDelete(ids){
    const chosen=ids.map(find);if(!chosen.length)return;
    if(chosen.some(c=>c.pending))throw Error('所选聊天仍在回复，请先停止或等待结束后再删除。');
    pendingDelete=chosen.map(c=>({id:c.id,revision:c.revision}));
    modal(chosen.length===1?'删除聊天？':'删除 '+chosen.length+' 条聊天？',
      '<p class="wk-note">删除所选本地聊天及其草稿，无法在此恢复。官网中的会话不受影响。</p><ul class="wd-chat-delete-list">'+chosen.slice(0,8).map(c=>'<li>'+h(c.title)+'</li>').join('')+'</ul>'+(chosen.length>8?'<p class="wk-note">共 '+chosen.length+' 条</p>':''),b('取消','close')+b('确认删除','chat-record-delete-confirm','data-chat-delete'));
  }
  async function act(action,el){
    const id=el?.dataset.id;
    if(action==='chat-record-rename'){
      const c=find(id);modal('重命名聊天','<form id="wb-chat-record-rename-form" data-id="'+h(id)+'"><label class="wk-field">名称<input class="wk-input" name="title" required maxlength="160" value="'+h(c.title)+'"></label><button type="submit" class="wk-button primary">保存</button></form>');return true;
    }
    if(action==='chat-record-pin'){const c=find(id);await command('chat.record',{id,pinned:!c.pinned});closeMenu();await B.refresh();renderSidebar();return true;}
    if(action==='chat-record-multi'){selecting=true;selected=new Set([id]);closeMenu();renderSidebar();return true;}
    if(action==='chat-record-toggle'){if(selected.has(id))selected.delete(id);else selected.add(id);renderSidebar();return true;}
    if(action==='chat-record-all'){const visible=rows();selected=visible.every(c=>selected.has(c.id))?new Set():new Set(visible.map(c=>c.id));renderSidebar();return true;}
    if(action==='chat-record-finish'){selecting=false;selected.clear();renderSidebar();return true;}
    if(action==='chat-record-delete'){askDelete([id]);return true;}
    if(action==='chat-record-delete-selected'){askDelete([...selected]);return true;}
    if(action==='chat-record-delete-confirm'){
      if(!pendingDelete?.length)return true;
      const chosen=pendingDelete,ids=chosen.map(c=>c.id),origin=location.hash;
      const restore=await B.suspendDrafts(ids);
      try{await command('chat.delete',{chats:chosen,confirm:true});}catch(exc){restore();throw exc;}
      for(const deleted of ids)setLocal('composer-backup:composer:chat:'+deleted,null);
      const legacy=v.chats.filter(c=>ids.includes(c.id)).map(c=>c.legacy_id).filter(Boolean);
      if(legacy.length)setLocal('web-chats',getLocal('web-chats',[]).filter(c=>!legacy.includes(c.id)));
      pendingDelete=null;selecting=false;selected.clear();$('#wb-dialog').close();await B.refresh();
      if(location.hash===origin&&ids.includes(v.activeChat)){v.chatInput='';v.chatEmbedded=false;await go('#chat');}else renderSidebar();
      toast('已删除 '+ids.length+' 条聊天。');return true;
    }
    return false;
  }
  async function submit(form){
    if(form.id!=='wb-chat-record-rename-form')return false;
    await command('chat.record',{id:form.dataset.id,title:new FormData(form).get('title')});$('#wb-dialog').close();await B.refresh();renderSidebar();return true;
  }
  function leave(){selecting=false;selected.clear();}
  return {render,menu,act,submit,leave};
};
