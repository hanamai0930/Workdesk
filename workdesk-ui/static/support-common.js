/* Shared support-page controls; private page hosts remain independently owned. */
window.WorkdeskSupport=(()=>{
  'use strict';
  const h=esc;
  const request=(route,value)=>api('v1/'+route,value===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  async function command(kind,payload){const id=crypto.randomUUID();try{return (await request('command',{id,kind,payload})).receipt;}catch(e){throw Error(e.message+' · 回执 '+id+'；未自动重发。');}}
  const tabs={records:['#records','对话记录'],workitems:['#work-items','任务'],projects:['#projects','项目与工作区'],monitor:['#monitor','运行与计划']};
  const recordTabs={records:['#records','历史记录'],drafts:['#drafts','草稿箱']};
  const runTabs={monitor:['#monitor','运行'],planning:['#planning','计划']};
  const supportOwners=new Set(['records','workitems','projects','planning','files','results','monitor','libraries']);
  const monitorTools=new Set(['collab','control','history','operations']);
  function managementRoute(value=location.hash){
    const [page,view]=value.replace(/^#/,'').split('/');
    if(page==='work-items')return view&&view!=='all'?'':'workitems';
    if(page==='records'||page==='drafts'||page==='archives'||page==='chat'&&view==='records'||page==='work'&&['records','archives'].includes(view))return 'records';
    if(page==='work'&&view==='project')return 'projects';
    if(page==='planning'||page==='schedules'||monitorTools.has(page))return 'monitor';
    if(page==='results'&&!view)return 'workitems';
    return Object.hasOwn(tabs,page)?page:'';
  }
  const sectionLinks=active=>Object.entries(tabs).map(([id,[href,name]])=>
    `<a href="${href}" data-management-link="${id}"${id===active?' aria-current="page"':''}>${name}</a>`).join('');
  function subnav(active){
    const isRecord=Object.hasOwn(recordTabs,active),sections=isRecord?recordTabs:runTabs;
    return `<nav class="wd-management-subtabs" aria-label="${isRecord?'对话记录分类':'运行与计划分类'}">${Object.entries(sections).map(([id,[href,name]])=>
      `<a href="${href}"${id===active?' aria-current="page"':''}>${name}</a>`).join('')}</nav>`;
  }
  const nav=active=>document.querySelector('[data-management-navigation]')?'':
    `<nav class="sp-tabs" aria-label="工作与记录">${sectionLinks(active)}</nav>`;
  function renderManagementNavigation(value=location.hash){
    const host=document.querySelector('[data-management-navigation]');if(!host)return;
    const [page]=value.replace(/^#/,'').split('/'),active=managementRoute(value);
    const visible=!!active&&!monitorTools.has(page);
    const column=host.closest('.wk-main');
    host.hidden=!visible;
    if(!visible){if(column)delete column.dataset.managementSection;return;}
    if(column)column.dataset.managementSection=active;
    let navigation=host.querySelector('nav');
    if(!navigation){
      host.innerHTML=`<nav class="wd-management-tabs" aria-label="工作与记录">${sectionLinks(active)}</nav>`;
      navigation=host.querySelector('nav');
    }
    for(const link of navigation.querySelectorAll('[data-management-link]')){
      if(link.dataset.managementLink===active){
        link.setAttribute('aria-current','page');
        const left=link.offsetLeft-navigation.scrollLeft,right=left+link.offsetWidth;
        if(left<0||right>navigation.clientWidth)navigation.scrollLeft=link.offsetLeft-navigation.clientWidth/2+link.offsetWidth/2;
      }else link.removeAttribute('aria-current');
    }
  }
  const button=(text,attr)=>`<button type="button" ${attr}>${h(text)}</button>`;
  const field=(label,name,value='',extra='')=>`<label class="sp-field"><span>${h(label)}</span><input aria-label="${h(label)}" name="${name}" value="${h(value)}" ${extra}></label>`;
  const text=(label,name,value='',extra='')=>`<label class="sp-field"><span>${h(label)}</span><textarea aria-label="${h(label)}" name="${name}" rows="3" ${extra}>${h(value)}</textarea></label>`;
  const options=(rows,value)=>rows.map(([id,label])=>`<option value="${h(id)}" ${id===value?'selected':''}>${h(label)}</option>`).join('');
  const select=(label,name,rows,value)=>`<label class="sp-field"><span>${h(label)}</span><select aria-label="${h(label)}" name="${name}">${options(rows,value)}</select></label>`;
  const raw=value=>`<pre class="sp-raw">${h(JSON.stringify(value,null,2))}</pre>`;
  const when=value=>value?new Date(value*1000).toLocaleString():'—';
  const labels={INTERRUPTED:'已中断',COMPLETED:'已完成',RUNNING:'运行中',RESERVED:'准备中',SEND_INTENT:'正在发送',WAITING_APPROVAL:'等待审批',WAITING_INPUT:'等待补充',STOP_REQUESTED:'已请求停止',SEND_UNKNOWN:'发送状态未知',STOP_UNKNOWN:'停止状态未知',ORPHANED_UNKNOWN:'重启后待核对',STOPPED_RUNTIME:'本地运行时已退出',FAILED:'失败',FAILED_NO_SEND:'发送前失败',CANCELLED:'已取消',SAVED:'已保存',DISCUSSION:'讨论中'};
  const state=value=>labels[value]||value;
  const confirm=(title,message,details,options={})=>window.WorkdeskDialogs.confirm({title,message,details,...options});
  function download(value,name,type){const url=URL.createObjectURL(new Blob([value],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function refreshChrome(token){await window.WorkdeskWorkbench.load();if(token!==undefined&&!WorkdeskModules.isCurrent(token))return;if(!supportOwners.has(WorkdeskModules.currentOwner()))return;window.WorkdeskWorkbench.renderSidebar();document.getElementById('wb-header-actions').innerHTML='';document.getElementById('wb-title').textContent='';}
  async function directory(){return (await api('pick-directory',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).path;}
  return {directory,refreshChrome,h,request,command,nav,subnav,managementRoute,renderManagementNavigation,button,field,text,select,raw,when,state,confirm,download};
})();
