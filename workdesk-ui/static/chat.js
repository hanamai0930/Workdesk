/* Official website Chat; no agent or paid API dispatch. */
window.WorkdeskChat=function(B){
  'use strict';
  const $=window.WorkdeskModules.dom('workbench');
  const {v,command,modal,b,h,draw,go,refresh,persist,captured}=B;
  const apiChat=window.WorkdeskChatAPI(B);
  const chat=()=>v.chats.find(c=>c.id===v.activeChat);
  function service(){return B.services().find(s=>s.provider===v.chatProvider);}
  function presentation(){
    const apiServices=B.apiServices(),api=apiServices.find(row=>row.provider===v.chatApiProvider)||apiServices[0];
    if(api&&!chat()?.messages?.length&&!api.models.includes(v.chatApiModel))v.chatApiModel=api.default_model||api.models[0];
    return {service:service(),services:B.services(),api,apiServices};
  }
  function selection(id,value){
    if(id==='wb-api-model'){if(!chat()?.messages?.length)v.chatApiModel=value;draw();return true;}
    if(id==='wb-api-provider'){
      const next=B.apiServices().find(row=>row.provider===value);
      if(next&&!chat()?.messages?.length){
        if(chat()?.provider!==value&&chat()){
          v.activeChat=null;B.adopt('home');window.history.replaceState(null,'','#chat');state.route='chat';
        }
        v.chatApiProvider=value;v.chatApiModel=next.default_model||next.models[0];v.chatSearch=false;v.chatSearchQuery='';
      }
      draw();return true;
    }
    if(id==='wb-chat-route'){apiChat.leave();v.chatRoute=value;v.chatEmbedded=false;v.activeChat=null;B.adopt('home');window.history.replaceState(null,'','#chat');state.route='chat';v.chatInput='';draw();return true;}
    if(id!=='wb-chat-provider')return false;if(chat()?.provider!==value){v.activeChat=null;B.adopt('home');window.history.replaceState(null,'','#chat');state.route='chat';}v.chatProvider=value;setLocal('chat-provider',value);draw();return true;
  }
  async function open(provider,url){await api('open-chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,...(url?{url}:{})})});}
  async function create(openAfter=false){
    const provider=v.chatProvider,content=v.chatInput,origin=location.hash;
    const c=(await command('chat.create',{provider,route:'web'})).chat;
    if(content.trim())await command('chat.send',{id:c.id,revision:1,content});
    await refresh();if(location.hash===origin&&v.chatProvider===provider){$('#wb-dialog').close();v.chatInput='';await persist(captured());if(location.hash===origin&&v.chatProvider===provider)await go('#chat/'+c.id);}
    if(openAfter)await open(provider);
  }
  async function act(action,el){
    if(await apiChat.act(action,el))return true;
    if(action==='chat-enter'){
      if(!window.WorkdeskDesktop?.chatViews){modal('内置聊天','<p>请重新打开 Workdesk 桌面窗口，再使用内置聊天。</p>',b('关闭','close'));return true;}
      const current=chat(),freshFork=current?.forked_from&&!current.web_url&&!v.chatEmbedded;
      v.chatEmbedded=true;
      if(!current||current.route!=='web'||current.provider!==v.chatProvider||freshFork)window.chrome?.webview?.postMessage({channel:'workdesk.chat',action:'reset',provider:v.chatProvider});
      if(!current||current.route!=='web'||current.provider!==v.chatProvider)await create(false);else draw();
      return true;
    }
    if(action==='chat-reload'){window.chrome?.webview?.postMessage({channel:'workdesk.chat',action:'reload',provider:v.chatProvider});return true;}
    if(action==='official-web'){await create();return true;}
    if(action==='chat-save-opening'){
      const c=chat();await command('chat.send',{id:c.id,revision:c.revision,content:v.chatInput});await refresh();draw();toast('开场文字已保存在本地，需在官网发送。');return true;
    }
    if(action==='chat-stop'){const c=chat();if(c?.pending)await B.stopTask(c.pending);return true;}
    if(action==='chat-window'){const c=chat();await open(c?.provider||v.chatProvider,c?.web_url);return true;}
    if(action==='chat-service'){modal('官方网页聊天','<p>登录、模型、聊天正文和免费额度由官方网页管理。Workdesk只保存入口、开场文字和你提供的聊天链接，不调用agent或付费API。</p>',b('关闭','close'));return true;}
    if(action==='bind-web'){
      modal('绑定官方聊天链接',`<form id="wb-bind-web-form"><label class="wk-field">具体聊天链接<input class="wk-input" name="url" type="url" required value="${h(chat()?.web_url||'')}"></label><p class="wk-note">仅保存官网地址，不自动读取聊天正文或登录凭据。</p><button class="wk-button primary" type="submit">保存链接</button></form>`);return true;
    }
    return false;
  }
  async function submit(form){if(form.id!=='wb-bind-web-form')return false;await command('chat.bind-web',{id:v.activeChat,url:new FormData(form).get('url')});$('#wb-dialog').close();await refresh();draw();return true;}
  async function prepare(){if(v.chatRoute==='api')return apiChat.send();await act('chat-enter');}
  function leave(){apiChat.leave();window.chrome?.webview?.postMessage({channel:'workdesk.chat',action:'hide'});}
  return {page:()=>'',prepare,poll:apiChat.poll,upload:apiChat.upload,leave,act,submit,presentation,selection};
};
