/* Settings owns its view, requests and events. Extension management is separate. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('settings'),h=esc;
  const stateSettings={data:null,sequence:0,busy:false,pending:null,diagnostics:false,route:null};
  const isSettings=()=>location.hash.split('/')[0]==='#settings';
  function paint(){
    const [,page='',focus]=location.hash.split('/');
    if(window.WorkdeskReact)window.WorkdeskReact.settings($('#main'),{...stateSettings,page,focus,theme:state.themePreference||state.theme,appearance:window.WorkdeskAppearance?.snapshot()});
    else $('#main').innerHTML=view();
  }
  function focusSearch(){
    if(!isSettings())return false;
    const wide=document.querySelector('#wb-settings-sidebar [data-settings-search]');
    if(wide?.getClientRects().length){wide.focus();return true;}
    const narrow=$('.wd-settings-mobile-nav');
    if(!narrow?.getClientRects().length)return false;
    narrow.open=true;
    const input=narrow.querySelector('[data-settings-search]');
    input?.focus();
    return !!input;
  }
  const iconBox=name=>`<span class="wd-settings-icon">${icon(name)}</span>`;
  const linkRow=(name,description,label,href,glyph)=>`<div class="wd-settings-row">${iconBox(glyph)}<div class="wd-settings-copy"><h3>${h(name)}</h3><p>${h(description)}</p></div><a class="wd-settings-button" href="${href}">${h(label)}${icon('arrow')}</a></div>`;
  function view(){
    const s=stateSettings.data;
    return `<section class="wd-settings" aria-labelledby="wd-settings-title">
      <header class="wd-settings-header"><h1 id="wd-settings-title" class="wd-settings-title">设置</h1><p class="wd-settings-intro">管理工作端、本地资料与界面偏好。</p></header>
      <section class="wd-settings-group" aria-labelledby="wd-settings-general"><h2 id="wd-settings-general" class="wd-settings-group-title">常规</h2><div class="wd-settings-card"><div class="wd-settings-row">${iconBox(state.theme==='dark'?'moon':'sun')}<div class="wd-settings-copy"><h3>界面主题</h3><p>选择你习惯的显示方式。</p></div><div class="wd-settings-theme" role="group" aria-label="界面主题"><button type="button" data-settings-theme="light" aria-pressed="${state.theme==='light'}">浅色</button><button type="button" data-settings-theme="dark" aria-pressed="${state.theme==='dark'}">深色</button></div></div></div></section>
      <section class="wd-settings-group" aria-labelledby="wd-settings-work"><h2 id="wd-settings-work" class="wd-settings-group-title">工作端与用量</h2><div class="wd-settings-card">${linkRow('账号与模型','查看认证状态，选择工作端的默认模型。','账号与模型','#settings/accounts','cpu')}${linkRow('用量与预算','查看任务用量与预算。','用量与预算','#settings/usage','history')}</div></section>
      <section class="wd-settings-group" aria-labelledby="wd-settings-local"><h2 id="wd-settings-local" class="wd-settings-group-title">本地资料与扩展</h2><div class="wd-settings-card">${linkRow('本地存储与恢复','管理资料保存位置、备份与恢复。','本地存储与恢复','#settings/storage','folder')}${linkRow('插件与 Skills','管理 Skills、MCP 与场景。','插件与 Skills','#settings/extensions','plug')}</div></section>
      <details class="wd-settings-diagnostics" ${stateSettings.diagnostics?'open':''}><summary>${icon('settings')}<span>服务与诊断</span>${icon('chevron')}</summary><div class="wd-settings-diagnostics-body"><h3>控制服务来源与版本</h3><dl><dt>工程位置</dt><dd>${h(s?.health?.program_base||'未读取')}</dd><dt>实现版本</dt><dd class="wd-settings-version">${h(s?.health?.implementation||'未读取')}</dd><dt>资料位置</dt><dd>${h(s?.storage?.library||'未读取')}</dd></dl><p>更新代码后，可重载空闲控制服务。活动任务会阻止重载，历史记录会保留。</p><div class="wd-settings-diagnostic-actions"><button class="wd-settings-button" type="button" data-settings="reload" ${stateSettings.busy?'disabled':''}>重载空闲控制服务</button><button class="wd-settings-button" type="button" data-settings="refresh" ${stateSettings.busy?'disabled':''}>刷新状态</button></div>${stateSettings.pending?`<p>最近命令：<code>${h(stateSettings.pending)}</code> · <a class="wd-settings-text-link" href="#control/receipts">查询回执</a></p>`:''}</div></details>
      <p class="wd-settings-footnote">模型账号与本地资料分开保存。账户剩余额度和实际账单仍以提供方为准。</p>
    </section>`;
  }
  async function render(refresh=false){
    const route=location.hash,sequence=++stateSettings.sequence;
    if(stateSettings.route!==route){$('#main').scrollTop=0;stateSettings.diagnostics=false;stateSettings.route=route;}
    if(stateSettings.data&&!refresh){paint();return;}
    try{
      const [data]=await Promise.all([api('v1/settings'),window.WorkdeskAppearance?.load()]);
      if(sequence!==stateSettings.sequence||location.hash!==route)return;
      stateSettings.data=data;paint();
    }catch(error){if(sequence===stateSettings.sequence&&location.hash===route)$('#main').innerHTML=errorPage(error.message);}
  }
  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-settings],[data-settings-theme],[data-appearance]');
    if(!el||!el.closest('[data-ui-module="settings"]'))return;
    event.preventDefault();if(stateSettings.busy)return;
    if(el.dataset.appearance){
      const a=window.WorkdeskAppearance;if(!a)return;
      const action=el.dataset.appearance;
      if(a.snapshot().busy)return;
      if(action==='preview')a.preview();
      if(action==='save')await a.save();
      if(action==='cancel')a.cancel();
      if(action==='reset-region')a.reset(false);
      if(action==='reset-all')a.reset(true);
      if(action==='reload')try{await a.load(true);}catch(error){toast(error.message);}
      return;
    }
    if(el.dataset.settingsTheme){theme(el.dataset.settingsTheme);window.WorkdeskAppearance?.selectTheme(state.theme);paint();return;}
    if(el.dataset.settings==='refresh'){try{await window.WorkdeskSettingsPreferences?.reload();}catch(error){toast(error.message);}await render(true);return;}
    if(el.dataset.settings==='reload'){
      stateSettings.busy=true;
      try{
        const identity=await api('v1/instance'),goCore=identity.core_engine==='go';
        if(!await window.WorkdeskDialogs.confirm({title:goCore?'停止后重新打开开发工作台':'重载空闲控制服务',message:goCore?'本次会停止开发工作台，请随后从“Workdesk-开发”入口重新打开。原记录保留；活动任务或扩展会阻止停止。':'仅重载空闲服务以载入当前代码。活动任务或扩展会阻止重载，不会终止任务或发送模型请求。'}))return;
        const id=crypto.randomUUID();stateSettings.pending=id;
        await api('v1/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,kind:'service.reload',payload:{}})});
        toast(goCore?'已停止开发工作台，请从“Workdesk-开发”入口重新打开。':'已登记重载。刷新状态可连接当前版本。');
      }catch(error){toast(error.message+'；未自动重发，请查询操作回执。');}
      finally{stateSettings.busy=false;if(isSettings())paint();}
    }
  });
  document.addEventListener('toggle',event=>{if(event.target.matches?.('.wd-settings-diagnostics'))stateSettings.diagnostics=event.target.open;},true);
  document.addEventListener('input',event=>{
    const el=event.target;if(!el.closest?.('[data-ui-module="settings"]')||!el.dataset.appearanceField)return;
    window.WorkdeskAppearance?.edit(el.dataset.appearanceField,el.value);
  });
  document.addEventListener('change',event=>{
    const el=event.target;if(!el.closest?.('[data-ui-module="settings"]')||!el.dataset.appearanceSelect)return;
    if(el.dataset.appearanceSelect==='region')window.WorkdeskAppearance?.selectRegion(el.value);
    if(el.dataset.appearanceSelect==='theme'){theme(el.value);window.WorkdeskAppearance?.selectTheme(el.value);paint();}
  });
  document.addEventListener('workdesk:appearance-select',event=>{const {field,value}=event.detail;if(field==='region')window.WorkdeskAppearance?.selectRegion(value);if(field==='theme'){theme(value);window.WorkdeskAppearance?.selectTheme(value);paint();}});
  document.addEventListener('workdesk:appearance',()=>{if(isSettings())paint();});
  window.WorkdeskModules.onLeave('settings',()=>{++stateSettings.sequence;stateSettings.route=null;stateSettings.diagnostics=false;const a=window.WorkdeskAppearance;if(a?.snapshot().preview)a.cancel();});
  window.WorkdeskSettings={render,view,focusSearch};
})();
