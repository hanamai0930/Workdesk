/* Direct DeepSeek API Chat controller; website chat remains a separate route. */
window.WorkdeskChatAPI=function(B){
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,command,refresh,draw,go,captured,persist}=B;let timer;
  const chat=()=>v.chats.find(c=>c.id===v.activeChat);
  const current=(id,generation)=>v.mode==='chat'&&v.chatRoute==='api'&&v.activeChat===id&&v.generation===generation&&location.hash==='#chat/'+id;
  function leave(){clearTimeout(timer);timer=null;}
  function poll(){
    leave();const id=v.activeChat,generation=v.generation;if(chat()?.route!=='api'||!chat().pending)return;
    timer=setTimeout(async()=>{try{await refresh();if(!current(id,generation))return;draw();poll();}catch(exc){if(current(id,generation))toast('聊天状态暂未刷新：'+exc.message);}},400);
  }
  async function send(){
    const content=v.chatInput,generation=v.generation,model=v.chatApiModel,thinking=v.chatThinking,provider=v.chatApiProvider;let origin=location.hash;
    const attachments=[...(v.chatFiles||[])],search=provider==='deepseek'&&!!v.chatSearch,search_query=v.chatSearchQuery||'';
    if(!content.trim()&&!attachments.length)return;
    let row=chat();
    const created=!row||row.route!=='api';
    if(created)row=(await command('chat.create',{provider,route:'api'})).chat;
    if(row.provider!==provider)throw Error('当前聊天使用另一家 API，请新建聊天。');
    if(location.hash!==origin||v.generation!==generation||v.chatRoute!=='api')return;
    if(created){
      const home=captured();if(home)await persist({...home,value:{...home.value,chatInput:'',chatFiles:[],chatSearchQuery:''}});
      if(location.hash!==origin||v.generation!==generation)return;
      v.activeChat=row.id;B.adopt(row.id);window.history.replaceState(null,'','#chat/'+row.id);state.route='chat/'+row.id;origin=location.hash;
      await persist(captured());await refresh();if(location.hash!==origin||v.generation!==generation)return;draw();
    }
    const result=await command('chat.api-send',{id:row.id,revision:row.revision,content,model,thinking,search,search_query,attachments:attachments.map(f=>f.id),billing:'official_api'});
    await refresh();
    if(location.hash!==origin||v.generation!==generation||v.chatRoute!=='api')return;
    if(v.chatInput===content){v.chatInput='';v.chatFiles=[];v.chatSearchQuery='';}
    await persist(captured());
    if(location.hash!==origin||v.generation!==generation||v.chatRoute!=='api')return;
    if(v.activeChat!==row.id)await go('#chat/'+row.id);else draw();
    poll();return result;
  }
  async function upload(files){
    const item=captured(),origin=location.hash,generation=v.generation,existing=[...(v.chatFiles||[])];
    if(!item)return;
    if(existing.length+files.length>10)throw Error('每条消息最多10个附件。');
    const added=[];let failure;
    for(const file of files){
      try{
        if(file.size>8*1024*1024)throw Error('每个文件最多8 MiB。');
        const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=32768)raw+=String.fromCharCode(...bytes.subarray(i,i+32768));
        const reply=await command('chat.api-attach',{name:file.name,data:btoa(raw)});added.push(reply.attachment);
      }catch(e){failure=e;break;}
    }
    const value={...item.value,chatFiles:[...existing,...added]};
    if(location.hash===origin&&v.generation===generation){v.chatFiles=value.chatFiles;draw();await B.saveDraftNow();}
    else await persist({...item,value});
    if(failure)throw failure;
  }
  async function act(action,el){
    if(action==='chat-api-stop'){const c=chat();await command('chat.api-stop',{id:c.id,turn_id:c.pending});await refresh();draw();poll();return true;}
    if(action==='chat-api-thinking'){v.chatThinking=!v.chatThinking;await persist(captured());draw();return true;}
    if(action==='chat-api-search'){v.chatSearch=!v.chatSearch;await persist(captured());draw();return true;}
    if(action==='chat-api-upload'){$('#wb-chat-file-input').click();return true;}
    if(action==='chat-api-remove-file'){
      await command('chat.api-remove-attachment',{id:el.dataset.id});v.chatFiles=(v.chatFiles||[]).filter(f=>f.id!==el.dataset.id);await B.saveDraftNow();draw();return true;
    }
    return false;
  }
  return {send,poll,leave,act,upload};
};
