"use strict";

const paths={home:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',stack:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',edit:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14v6Z"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',refresh:'<path d="M20 7a9 9 0 0 0-15-1L3 9m0-6v6h6m-5 8a9 9 0 0 0 15 1l2-3m0 6v-6h-6"/>',settings:'<path d="m9 3-1 3-3 1v4l-2 1 2 2v3l3 1 1 3h6l1-3 3-1v-3l2-2-2-1V7l-3-1-1-3H9Z"/><circle cx="12" cy="12" r="3"/>',folder:'<path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z"/>',sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',moon:'<path d="M20 15.5A8.5 8.5 0 0 1 8.5 4a8.5 8.5 0 1 0 11.5 11.5Z"/>',panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',back:'<path d="M19 12H5m5-5-5 5 5 5"/>',check:'<path d="m5 12 4 4L19 6"/>',circle:'<circle cx="12" cy="12" r="7"/>',plus:'<path d="M12 4v16M4 12h16"/>',copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',stop:'<rect x="5" y="5" width="14" height="14" rx="3"/>',file:'<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h5"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',save:'<path d="M4 3h13l3 3v15H4V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>'};
const $=s=>s==='#main'?(window.WorkdeskModules?.currentHost()||document.querySelector(s)):document.querySelector(s);
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.file}</svg>`;
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function markdown(value){
  if(window.WorkdeskReact?.markdownTemplate)return window.WorkdeskReact.markdownTemplate(value);
  // Escape first; model text never becomes HTML, scripts, images or active links.
  const lines=esc(value).split('\n');let output='',code=false,buffer=[];
  const inline=text=>text.replace(/`([^`]+)`|\[([^\]]+)\]\(([^)\n]+)\)|\*\*([^*]+)\*\*/g,(_,code,label,path,bold)=>code?'<code>'+code+'</code>':label?'<code title="'+path+'">'+label+'</code>':'<strong>'+bold+'</strong>');
  for(const line of lines){
    if(line.startsWith('```')){if(code){output+='<pre><code>'+buffer.join('\n')+'</code></pre>';buffer=[];}code=!code;continue;}
    if(code){buffer.push(line);continue;}
    const heading=line.match(/^(#{1,4})\s+(.+)$/);
    if(heading){output+='<h3>'+inline(heading[2])+'</h3>';continue;}
    if(/^[-*] /.test(line)){output+='<div class="markdown-list">• '+inline(line.slice(2))+'</div>';continue;}
    output+=line?'<p>'+inline(line)+'</p>':'<div class="markdown-space"></div>';
  }
  if(code)output+='<pre><code>'+buffer.join('\n')+'</code></pre>';
  return output;
}
function icons(scope=document){scope.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
function getLocal(key,fallback){try{return JSON.parse(localStorage.getItem('workdesk.02.'+key))??fallback;}catch{return fallback;}}
function setLocal(key,value){try{localStorage.setItem('workdesk.02.'+key,JSON.stringify(value));return true;}catch{toast('当前浏览器无法保存本地设置。');return false;}}
const state={drawer:false,data:null,detail:null,history:null,filter:'all',tab:'result',route:'',generation:0,inspector:getLocal('inspector',true),theme:getLocal('theme','light'),drafts:getLocal('drafts',[]),recent:getLocal('recent',[]),pendingStop:false};
if(!Array.isArray(state.drafts))state.drafts=[];
if(!Array.isArray(state.recent))state.recent=[];
const narrow=window.matchMedia('(max-width:1050px)');
function inspectorClasses(){return narrow.matches?(state.drawer?'force-visible':'hidden'):(state.inspector?'':'hidden');}
function syncInspector(){const el=$('.inspector');if(el)el.className='inspector '+inspectorClasses();}
narrow.addEventListener('change',syncInspector);
const systemTheme=matchMedia('(prefers-color-scheme: dark)');
function theme(value){
  const choice=['light','dark','system'].includes(value)?value:'light';
  state.themePreference=choice;
  state.theme=choice==='system'?(systemTheme.matches?'dark':'light'):choice;
  document.documentElement.dataset.theme=state.theme;setLocal('theme',choice);
  const el=$('[data-action="theme"]');el.innerHTML=icon(state.theme==='dark'?'moon':'sun');
}
systemTheme.addEventListener('change',()=>{if(state.themePreference==='system')theme('system');});
function toast(message){const el=$('#toast');el.textContent=message;el.dataset.placement=!location.hash||/^#(?:chat|work|home)(?:\/|$)|^#work-items\/(?:conversation|view-chat|view-work)\//.test(location.hash)?'conversation':'page';el.classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('visible'),message==='已复制。'?2200:4500);}
async function api(route,options){if(window.WorkdeskCore)return window.WorkdeskCore.request(route,options);const response=await fetch('api/'+route,{cache:'no-store',credentials:'omit',...options});const data=await response.json();if(!response.ok){const error=new Error(data.error||'记录读取失败，状态未知。');error.status=response.status;throw error;}return data;}
const provider=id=>state.data.providers.find(p=>p.id===id)||{name:id,mark:'?',engine:'未知',auth:'未知'};
const mark=id=>`<span class="provider-mark ${esc(id)}">${esc(provider(id).mark)}</span>`;
const badge=row=>`<span class="status ${esc(row.tone)}" title="${esc(row.state_label)}；后台当前存活未知">${esc(row.display_status||row.state_label)}</span>`;
const empty=text=>`<div class="empty">${icon('stack')}${esc(text)}</div>`;
const heading=(title,sub,actions='',eyebrow='WORKDESK / WORKSPACE')=>`<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${sub}</p></div><div class="heading-actions">${actions}</div></div>`;
function errorBanner(){return state.data.errors.length?`<div class="warning-banner">${state.data.errors.length} 个来源读取失败，相关状态保持未知。<details><summary>查看来源</summary><pre>${esc(JSON.stringify(state.data.errors,null,2))}</pre></details></div>`:'';}
function refreshButton(){return `<button class="button secondary" data-action="refresh">${icon('refresh')}刷新记录</button>`;}
function row(item,i){return `<a class="record-row" href="#task/${esc(item.id)}"><span class="record-index">${String(i+1).padStart(2,'0')}</span><div><span class="record-title">${esc(item.title)}</span><div class="record-meta"><span>${esc(provider(item.provider).name)}</span><span class="sep">/</span><span>${esc(item.time)}</span>${item.closeout_only?'<span>独立小包</span>':''}</div></div>${badge(item)}</a>`;}
function composer(){return `<div class="draft-composer"><textarea id="draft-input" aria-label="任务草稿" placeholder="记下接下来想做的事…"></textarea><div class="composer-bottom"><span>仅保存为本地草稿，尚不派发执行</span><div class="composer-actions"><select id="draft-provider" aria-label="草稿拟用工作端"><option value="codex">Codex</option><option value="deepseek">DeepSeek</option><option value="qwen">Qwen</option></select><button class="button primary" data-action="save-draft">${icon('plus')}保存草稿</button></div></div></div>`;}
function home(){const data=state.data;return `<div class="page">${heading('工作台','查看任务进展，把注意力留给手边的工作。',`<button class="button primary" data-action="focus-draft">${icon('plus')}新建任务</button>`,'WORKDESK / 工作台')}${errorBanner()}
  <div class="section-heading"><h2>工作端</h2><a class="text-button" href="#providers">查看接入情况${icon('arrow')}</a></div>
  <div class="provider-grid">${data.providers.map(p=>`<a class="provider-card" href="#tasks/${p.id}"><div class="provider-head">${mark(p.id)}<div><div class="provider-name">${p.name}</div><small>${p.auth}</small></div><span class="arrow">${icon('arrow')}</span></div><span class="provider-model">${p.model}</span><p>${p.summary}</p><div class="card-foot"><span class="mini-tag">有限接入已验证</span><span>${data.records.filter(r=>r.provider===p.id).length} 份记录</span></div></a>`).join('')}</div>
  <div class="home-columns"><div><div class="section-heading"><h2>最近任务</h2><a class="text-button" href="#tasks">全部记录${icon('arrow')}</a></div><div class="record-list">${data.records.slice(0,5).map(row).join('')||empty('还没有后台任务记录。')}</div><div class="section-space"><div class="section-heading"><h2>下一步，先记下来</h2><span>草稿箱</span></div>${composer()}</div></div>
  <aside class="home-aside"><div class="side-note"><span class="stage-label">STAGE C</span><h3>接入样板已落地</h3><p>三种工作端已完成有限后台验证，统一操作仍在建设中。</p><div class="progress-line">${icon('check')}后台程序化运行</div><div class="progress-line">${icon('check')}任务、材料与预算记录</div><div class="progress-line pending">${icon('circle')}统一派发与控制</div><div class="progress-line pending">${icon('circle')}独立审查与体验验收</div></div><div class="side-note"><h3>此刻看到的是什么</h3><p>这里展示已保存的执行记录。保存的完成状态与后台当前存活分别表达；刷新页面只更新显示。</p><a class="text-button" href="#settings">了解当前能力${icon('arrow')}</a></div></aside></div></div>`;}
function tasks(filter){if(filter)state.filter=filter;const all=state.data.records;const shown=all.filter(r=>state.filter==='all'||r.provider===state.filter||state.filter==='attention'&&(r.tone==='danger'||r.tone==='warning'));return `<div class="page">${heading('后台任务','每次尝试各自保留，完成、失败与未知都可追溯。',refreshButton(),'WORKSPACE / TASKS')}${errorBanner()}<div class="filterbar">${[['all','全部'],['codex','Codex'],['deepseek','DeepSeek'],['qwen','Qwen'],['attention','失败与未知']].map(([key,label])=>`<button class="filter-chip ${state.filter===key?'active':''}" data-filter="${key}">${label}</button>`).join('')}<span class="filter-label">${shown.length} 份记录 · 后台存活未知</span></div><div class="task-table"><div class="table-head"><span>任务 / 尝试</span><span>模型</span><span>保存状态</span><span>开始时间</span></div>${shown.map(r=>`<a class="table-row" href="#task/${esc(r.id)}"><div class="table-task">${mark(r.provider)}<div><span class="table-title">${esc(r.title)}</span><span class="table-sub">${esc(r.attempt_id.slice(0,8))} · ${r.closeout_only?'独立收尾包':esc(provider(r.provider).name)}${r.closed?' · 包已封闭':''}</span></div></div><span class="model">${esc(r.model)}</span>${badge(r)}<time>${esc(r.time)}</time></a>`).join('')||empty('当前筛选下没有记录。')}</div></div>`;}
function rawBlock(title,text,open=false){return `<details class="history-page" ${open?'open':''}><summary>${esc(title)}</summary><pre class="raw-page">${esc(text)}</pre></details>`;}
function resultView(item){return `${item.checks?.length?`<div class="check-strip"><span>${icon('check')}最近保存的检查器结果</span>${item.checks.slice(-1).map(c=>`<strong>${esc(c.passed)} / ${esc(c.total)}</strong><small>${c.all_passed?'用例通过':'有未通过用例'}</small>`).join('')}<small>与回合终态分别记录</small></div>`:''}${item.prompt&&item.prompt!=='{}'?`<section class="message"><div class="message-label"><span class="mini-avatar">H</span>任务说明<span class="message-time">保存的输入</span></div><pre class="message-content prompt">${esc(item.prompt)}</pre></section>`:''}<section class="message"><div class="message-label"><span class="mini-avatar">${esc(provider(item.provider).mark)}</span>${esc(provider(item.provider).name)}<span class="message-time">保存的最终回复</span></div>${item.answer?`<div class="message-content answer markdown">${markdown(item.answer)}</div>`:`<div class="result-empty">此尝试没有可展示的最终回复正文。<br>保存状态为「${esc(item.state_label)}」，请结合执行过程与原始结果核对。</div>`}</section><div class="callout ${item.state==='COMPLETED'?'':'muted-callout'}">${item.closeout_only?'本记录只验证检查器与正常结束，不覆盖原工程回合的失败。':item.provider==='qwen'?'工程修补检查通过与回合正常结束分别记录；原失败和约 39 秒本地收尾超时保留。':'完成状态只反映本次有限验证；工具退出、独立审查与 Human 验收分别记录。'}</div>${rawBlock('结果与审查 · 原始记录',item.pages['结果与审查'])}`;}
function traceView(item){return `<p class="section-subtitle">${item.events.length} 条保存事件。展开查看原始数据；这些时间线不证明后台当前连接。</p><div class="trace">${item.events.map(event=>`<details class="trace-item"><summary><span>${esc(event.label)}</span><time>${esc(event.time)}</time></summary><div class="trace-kind">${esc(event.kind)}</div><pre class="raw-page">${esc(JSON.stringify(event.data,null,2))}</pre></details>`).join('')||empty('没有保存的事件。')}</div>`;}
function detailContent(item){if(state.tab==='trace')return traceView(item);if(state.tab==='materials')return `<p class="section-subtitle">按保存的版本与内容展示，不额外打开引用文件。</p><div class="source-block"><h3>准确材料</h3><pre class="raw-page">${esc(item.pages['准确材料'])}</pre></div>`;if(state.tab==='budget')return `<p class="section-subtitle">预算预留、实际用量与账单实扣分别表达；这里不刷新或扩充授权。</p><div class="source-block"><h3>授权与预算</h3><pre class="raw-page">${esc(item.pages['授权与预算'])}</pre></div>`;if(state.tab==='source')return `<div class="source-block"><h3>任务状态与准确身份</h3><pre class="raw-page">${esc(item.pages['任务状态'])}</pre></div>${rawBlock('工具过程与差异 · 原始记录',item.pages['工具过程与差异'])}`;return resultView(item);}
function inspector(item){return `<aside class="inspector ${inspectorClasses()}"><div class="inspector-title"><h2>任务信息</h2><span>DETAILS</span></div><div class="info-group"><div class="info-label">执行工作端</div><div class="info-value">${esc(provider(item.provider).name)}<br>${esc(item.harness)}</div><div class="info-label section-space">模型</div><div class="info-value mono">${esc(item.model)}</div><div class="info-value">${esc(item.effort)} / ${esc(item.service_tier)}</div></div><div class="info-group"><div class="info-pair"><span>后台当前存活</span><span>未知</span></div><div class="info-pair"><span>停止请求</span><span>${item.stop_requested?'已登记':'未登记'}</span></div><div class="info-pair"><span>任务包</span><span>${item.closed?'已封闭':'见授权记录'}</span></div><div class="info-pair"><span>审查</span><span>本聊天自审</span></div><div class="info-pair"><span>独立审查</span><span>未进行</span></div><div class="info-pair"><span>Human 验收</span><span>未记录</span></div></div><div class="info-group"><div class="info-label">尝试身份</div><div class="copy-row"><div class="info-value mono">${esc(item.attempt_id)}</div><button class="icon-button" data-action="copy-id" aria-label="复制尝试身份">${icon('copy')}</button></div><div class="info-label section-space">记录来源</div><div class="info-value mono">${esc(item.source)}</div></div><button class="button secondary" data-action="request-stop" ${item.can_request_stop?'':'disabled'} title="${item.can_request_stop?'登记请求后仍需核对中断与退出记录':'此来源或当前状态不支持界面停止'}">${icon('stop')}请求停止</button><p class="inspector-note">停止请求、协议中断与工具退出分别确认。${item.provider!=='codex'?'此工作端尚未接通界面停止。':''}</p></aside>`;}
function detailPage(item){return `<div class="detail-layout"><article class="detail-main"><a class="detail-back" href="#tasks/${esc(item.provider)}">${icon('back')}后台任务 / ${esc(provider(item.provider).name)}</a><div class="detail-heading">${mark(item.provider)}<div><h1>${esc(item.title)}</h1><div class="record-meta"><span>${esc(item.time)}</span><span class="sep">/</span><span>${esc(item.attempt_id.slice(0,8))}</span>${item.closeout_only?'<span>独立收尾包</span>':''}</div></div>${badge(item)}</div><div class="detail-tabs" role="tablist">${[['result','结果'],['trace','执行过程'],['materials','材料'],['budget','授权与预算'],['source','技术信息']].map(([key,title])=>`<button class="${state.tab===key?'active':''}" role="tab" aria-selected="${state.tab===key}" data-tab="${key}">${title}</button>`).join('')}</div><div id="detail-content">${detailContent(item)}</div></article>${inspector(item)}</div>`;}
function providers(){return `<div class="page">${heading('工作端','角色、模型与运行环境分别表达。这里展示有限验证的结果。','','WORKSPACE / PROVIDERS')}${state.data.providers.map(p=>`<article class="provider-detail"><div class="provider-detail-top">${mark(p.id)}<div><h2>${p.name}</h2><p>${p.engine}</p></div><span class="mini-tag">有限接入已验证</span></div><div class="provider-detail-body"><div class="provider-facts"><div class="info-pair"><span>认证路径</span><span>${p.auth}</span></div><div class="info-pair"><span>最近验证模型</span><span>${p.model}</span></div><div class="info-pair"><span>后台当前连接</span><span>未知</span></div><div class="info-pair"><span>账户剩余额度</span><span>未知</span></div></div><div class="provider-limits"><p>${p.summary}</p><p>${p.reservation}</p></div></div><div class="provider-detail-bottom"><span>历史验证 · ${esc(p.verified_at||'验证时间未记录')}</span><a class="text-button" href="#settings/accounts/${encodeURIComponent(p.id)}">查看当前账号与模型</a><a class="text-button" href="#tasks/${p.id}">查看任务${icon('arrow')}</a></div></article>`).join('')}<div class="callout muted-callout">Codex 保持订阅认证；DeepSeek 与 Qwen 使用官方 API。界面不读取或展示密钥。Kimi 交叉审查继续由 Human 在外部聊天中手动进行。</div></div>`;}
function historyPage(){return `<div class="page">${heading('历史交接','保留 A / B 的材料、候选与分工依据，按原始来源查看。',refreshButton(),'WORKSPACE / HISTORY')}${errorBanner()}${state.data.history.map(r=>`<a class="history-card" href="#history/${esc(r.id)}"><h2>${esc(r.title)}</h2><p>${esc(r.label)}</p><div class="record-meta"><span>${r.candidates} 份候选</span><span class="sep">/</span><span>${esc(r.batch)}</span>${icon('arrow')}</div></a>`).join('')||empty('没有可读取的历史交接。')}</div>`;}
function historyDetail(item){return `<div class="page"><a class="detail-back" href="#history">${icon('back')}返回历史交接</a>${heading(esc(item.title),esc(item.label),'','WORKSPACE / HISTORY')}<div class="callout muted-callout">以下为原始候选与交接记录，未据此作新的接受或科研状态变更。</div>${Object.entries(item.pages).map(([title,text],i)=>rawBlock(title,text,i===0)).join('')}</div>`;}
function drafts(){return `<div class="page">${heading('草稿箱','先把想法留下来。草稿保存在当前浏览器，不发送给任何工作端。','','WORKSPACE / DRAFTS')}${composer()}<div class="section-space"><div class="section-heading"><h2>已保存草稿</h2><span>${state.drafts.length} 份</span></div>${state.drafts.map(d=>`<article class="draft-card"><div class="draft-card-head"><span>${icon('edit')}</span><span>拟用 ${esc(provider(d.provider).name)}</span><time>${esc(d.time)}</time></div><pre>${esc(d.text)}</pre><div class="draft-actions"><button class="text-button" data-copy-draft="${esc(d.id)}">${icon('copy')}复制内容</button><button class="text-button" data-delete-draft="${esc(d.id)}">删除草稿</button></div></article>`).join('')||empty('还没有保存的草稿。')}</div></div>`;}
function settings(){return window.WorkdeskSettings.view();}
function legacyUpdateSidebar(){const data=state.data;$('#task-count').textContent=data.records.length;const recent=state.recent.map(id=>data.records.find(r=>r.id===id)).filter(Boolean);const fallback=data.records.slice(0,5);$('#recent-nav').innerHTML=(recent.length?recent:fallback).slice(0,6).map(r=>`<a href="#task/${esc(r.id)}" class="${state.detail?.id===r.id?'active':''}"><span class="tiny-provider">${esc(provider(r.provider).mark)}</span><span class="recent-title" title="${esc(r.title)}">${esc(r.title)}</span></a>`).join('');}
async function loadWorkspace(){const data=await api('workspace');state.data=data;$('#load-status').textContent='记录读取于 '+data.loaded_at+' · 后台存活未知';updateSidebar();}
function errorPage(message){return `<div class="error-page"><h1>暂时无法读取记录</h1><p>${esc(message)}<br>旧内容已清除，状态保持未知。</p><button class="button" data-action="refresh">${icon('refresh')}重新读取</button></div>`;}
async function legacyRoute(){const current=location.hash.slice(1)||'control';const [page,id]=current.split('/');const generation=++state.generation;const previous=state.route;state.route=current;state.detail=null;state.history=null;$('#sidebar').classList.remove('open');document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===(page==='task'?'tasks':page)));$('#breadcrumb').textContent=({home:'工作台',control:'任务执行',collab:'旧版协作队列',operations:'恢复工具与诊断',tasks:'后台任务',task:'后台任务',providers:'工作端',history:'历史交接',settings:'设置',extensions:'用户扩展',drafts:'草稿箱'})[page]||'工作台';try{if(!state.data)await loadWorkspace();if(generation!==state.generation)return;if(page==='extensions'){await window.WorkdeskExtensions.render();return;}if(page==='settings'){await window.WorkdeskSettings.render();return;}if(page==='operations'){await window.WorkdeskOperations.render();updateSidebar();return;}if(page==='collab'){await window.WorkdeskCollaboration.render(id||'',current.split('/')[2]||'');updateSidebar();return;}if(page==='control'){await window.WorkdeskControl.render(id||'',current.split('/')[2]||'');updateSidebar();return;}if(page==='task'){if(previous!==current)state.tab='result';$('#main').innerHTML='<div class="loading"><span class="loader"></span>正在读取任务…</div>';const detail=await api('attempt?id='+encodeURIComponent(id));if(generation!==state.generation)return;state.detail=detail;state.recent=[id,...state.recent.filter(v=>v!==id)].slice(0,6);setLocal('recent',state.recent);$('#main').innerHTML=detailPage(detail);}else if(page==='history'&&id){$('#main').innerHTML='<div class="loading"><span class="loader"></span>正在读取历史…</div>';const detail=await api('history?id='+encodeURIComponent(id));if(generation!==state.generation)return;state.history=detail;$('#main').innerHTML=historyDetail(detail);}else{$('#main').innerHTML=page==='tasks'?tasks(id||'all'):page==='providers'?providers():page==='history'?historyPage():page==='settings'?settings():page==='drafts'?drafts():home();}if(previous!==current)$('#main').scrollTop=0;updateSidebar();}catch(error){if(generation===state.generation){state.detail=null;state.history=null;$('#main').innerHTML=errorPage(error.message);$('#load-status').textContent='读取失败 · 当前状态未知';}}}
function openSearch(){if(!state.data)return;$('#search-dialog').showModal();$('#search-input').value='';search('');$('#search-input').focus();}
function search(query){const q=query.trim().toLowerCase();const items=[...state.data.records.map(r=>({title:r.title,sub:provider(r.provider).name+' · '+r.model+' · '+r.state_label,href:'#task/'+r.id,provider:r.provider})),...state.data.history.map(r=>({title:r.title,sub:'历史交接 · '+r.label,href:'#history/'+r.id}))];const shown=items.filter(i=>(i.title+' '+i.sub).toLowerCase().includes(q)).slice(0,20);$('#search-results').innerHTML=shown.map(i=>`<a href="${esc(i.href)}" data-search-result>${i.provider?mark(i.provider):icon('history')}<div><strong>${esc(i.title)}</strong><small>${esc(i.sub)}</small></div></a>`).join('')||empty('没有找到匹配的本地记录。');}
async function copyText(value){try{await navigator.clipboard.writeText(value);toast('已复制。');}catch{toast('复制不可用，请在正文中选中文本复制。');}}
async function action(name){if(name==='theme'){theme(state.theme==='light'?'dark':'light');if(state.route==='settings')$('#main').innerHTML=settings();}else if(name==='refresh'){const button=$('[data-action="refresh"]');if(button)button.disabled=true;try{await loadWorkspace();await route();toast('已刷新本地记录，未派发任务。');}catch(error){state.data=null;state.detail=null;state.history=null;$('#recent-nav').innerHTML='';$('#task-count').textContent='—';$('#main').innerHTML=errorPage(error.message);$('#load-status').textContent='读取失败 · 状态未知';}finally{if(button?.isConnected)button.disabled=false;}}else if(name==='search')openSearch();else if(name==='menu')$('#sidebar').classList.toggle('open');else if(name==='project')toast('当前工作区：Workdesk。项目目录保持不变。');else if(name==='focus-draft'){location.hash='#control/new';}else if(name==='save-draft'){const text=$('#draft-input')?.value.trim();if(!text){toast('先写下任务内容。');$('#draft-input')?.focus();return;}if(text.length>16000){toast('草稿过长，请缩短到 16000 字以内。');return;}if(state.drafts.length>=50){toast('草稿箱已达到 50 份，请先整理。');return;}const draft={id:crypto.randomUUID(),text,provider:$('#draft-provider').value,time:new Date().toLocaleString('zh-CN',{hour12:false})};const next=[draft,...state.drafts];if(setLocal('drafts',next)){state.drafts=next;$('#draft-input').value='';toast('已保存到当前浏览器的草稿箱，未发送给模型。');if(state.route==='drafts')$('#main').innerHTML=drafts();}}else if(name==='inspector'){if(state.detail){if(narrow.matches)state.drawer=!state.drawer;else{state.inspector=!state.inspector;setLocal('inspector',state.inspector);}syncInspector();}else toast('任务详情页可查看材料、身份与保存状态。');}else if(name==='copy-id'&&state.detail)copyText(state.detail.attempt_id);else if(name==='request-stop'&&state.detail?.can_request_stop){$('#confirm-dialog').showModal();}else if(name==='cancel-stop')$('#confirm-dialog').close();else if(name==='confirm-stop'){if(state.pendingStop||!state.detail?.can_request_stop)return;const item=state.detail;state.pendingStop=true;$('#confirm-dialog').close();try{const value=await api('stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:item.id,spec_hash:item.spec_hash,owner:item.owner})});toast(value.registered?'已登记停止请求；是否已停止仍待核对。':value.reason);await loadWorkspace();await route();}catch{toast('停止请求回执未知，请先刷新并核对原任务记录；不会自动重发。');}finally{state.pendingStop=false;}}}
document.addEventListener('click',event=>{const el=event.target.closest('button,a');if(!el)return;if(el.matches('[data-action]'))action(el.dataset.action);if(el.matches('[data-filter]')){location.hash='#tasks/'+el.dataset.filter;}if(el.matches('[data-tab]')&&state.detail){state.tab=el.dataset.tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b===el);b.setAttribute('aria-selected',String(b===el));});$('#detail-content').innerHTML=detailContent(state.detail);}if(el.matches('[data-theme]')){theme(el.dataset.theme);$('#main').innerHTML=settings();}if(el.matches('[data-search-result]'))$('#search-dialog').close();if(el.matches('[data-copy-draft]')){const draft=state.drafts.find(d=>d.id===el.dataset.copyDraft);if(draft)copyText(draft.text);}if(el.matches('[data-delete-draft]')){const next=state.drafts.filter(d=>d.id!==el.dataset.deleteDraft);if(setLocal('drafts',next)){state.drafts=next;$('#main').innerHTML=drafts();toast('草稿已删除。');}}});
$('#search-input').addEventListener('input',event=>search(event.target.value));
document.addEventListener('keydown',event=>{if(document.body.hasAttribute('data-connection-required'))return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openSearch();}if(event.key==='Escape')$('#sidebar').classList.remove('open');});
window.addEventListener('hashchange',()=>route());
icons();theme(state.theme);
window.WorkdeskLegacy={start:route,startPreferences:()=>window.WorkdeskSettingsPreferences?.start(),markdown};
if(!window.WorkdeskReact)window.addEventListener('DOMContentLoaded',()=>route());

function updateSidebar(){
  if(window.WorkdeskWorkbench){window.WorkdeskWorkbench.renderSidebar();return;}
  if(state.data)legacyUpdateSidebar();
}
let routeRequest=0, connectionReturn='';
const chatAccount=status=>['codex','deepseek','qwen'].find(id=>status.chat_accounts?.[id]);
const chatApi=status=>status.api_connections?.deepseek??status.connections.deepseek;
async function route(){
  const request=++routeRequest;
  const originalRequest=location.hash.slice(1)||'chat';
  const requested=/^connect(?:\/|$)/.test(originalRequest)?'settings/accounts':originalRequest.replace(/^settings\/partners(?=\/|$)/,'settings/accounts');
  if(requested!==originalRequest)history.replaceState(history.state,'','#'+requested);
  const localResources=/^resources(?:\/|$)/.test(requested);
  let current=requested,connectionStatus=null,connectionRequired=false;
  if(window.WorkdeskReact?.connectionSetup){
    try{connectionStatus=await api('v1/connection-setup');}catch{/* Render the setup retry page. */}
    if(request!==routeRequest)return;
    connectionRequired=!connectionStatus?.ready;
    // A registered development instance can inspect local projects/settings
    // without importing a daily login. Dispatch still validates real credentials.
    const developmentLocal=document.documentElement.dataset.workdeskChannel==='development'&&!!connectionStatus;
    const partnerAccess=/^settings\/accounts(?:\/|$)|^work\/partners(?:\/|$)/.test(requested);
    if(connectionRequired&&!localResources&&!developmentLocal&&!partnerAccess){
      document.body.dataset.connectionRequired='true';
      if(requested!=='connect')connectionReturn=requested;
      current='settings/accounts';
      history.replaceState(history.state,'','#settings/accounts');
    }else{
      delete document.body.dataset.connectionRequired;
      if(requested==='connect'&&state.route&&state.route!=='connect')connectionReturn=state.route;
      if(!localResources&&!location.hash&&!chatApi(connectionStatus)&&!chatAccount(connectionStatus)){
        current='work';
        history.replaceState(history.state,'','#work');
      }
    }
  }
  const parts=current.split('/');
  try{await window.WorkdeskLegacyModules?.(parts[0]);}catch(error){
    if(request===routeRequest){window.WorkdeskModules.activate(parts[0]);$('#main').innerHTML=errorPage(error.message);}
    return;
  }
  if(request!==routeRequest)return;
  document.body.dataset.activePage=parts[0];
  window.WorkdeskSupport?.renderManagementNavigation(location.hash);
  window.WorkdeskSettingsPreferences?.remember(current);
  const token=window.WorkdeskModules.activate(parts[0]);await Promise.resolve();if(!window.WorkdeskModules.isCurrent(token))return;
  if(parts[0]==='resources'){
    state.route=current;state.generation++;
    window.WorkdeskWorkbench?.leave();
    window.WorkdeskControl?.leave();window.WorkdeskCollaboration?.leave();
    window.WorkdeskWorkbench?.renderSidebar();
    window.WorkdeskReact.resourceSpace(window.WorkdeskModules.currentHost(),{routeKey:current});
    return;
  }
  if(parts[0]==='connect'&&window.WorkdeskReact?.connectionSetup){
    state.route='connect';state.generation++;
    window.WorkdeskWorkbench?.leave();
    window.WorkdeskControl?.leave();window.WorkdeskCollaboration?.leave();
    window.WorkdeskReact.connectionSetup($('#main'),{routeKey:request,initialStatus:connectionStatus,onEnter:status=>{
      let next=connectionReturn;
      connectionReturn='';
      const canChat=chatApi(status)||chatAccount(status);
      if(!next||next==='connect'||(connectionRequired&&!canChat&&['home','chat'].includes(next))||
          (connectionRequired&&next==='work'&&!Object.values(status.connections).some(Boolean)&&canChat)){
        next=canChat?'chat':'work';
      }
      location.hash='#'+next;
    }});
    return;
  }
  if(['chat','work','home','drafts','archives','schedules'].includes(parts[0])){
    state.route=current;state.generation++;
    window.WorkdeskControl?.leave();window.WorkdeskCollaboration?.leave();
    await window.WorkdeskWorkbench.render(parts[0]==='home'?[]:parts.slice(1), connectionStatus?.connections,
      connectionStatus&&!chatApi(connectionStatus)?chatAccount(connectionStatus):undefined);return;
  }
  window.WorkdeskWorkbench?.leave();
  if(parts[0]==='settings')window.WorkdeskWorkbench?.renderSidebar();
  else if(parts[0]==='work-items'&&parts[1]==='view-chat')window.WorkdeskWorkbench?.setChatDetailMode(parts[2]);
  else window.WorkdeskWorkbench?.setWorkMode();
  const supportPages={management:window.WorkdeskManagement,'work-items':window.WorkdeskWorkItemsPage,projects:window.WorkdeskProjectsPage,planning:window.WorkdeskPlanningPage,libraries:window.WorkdeskLibrariesPage,files:window.WorkdeskFilesPage,records:window.WorkdeskRecordsPage,results:window.WorkdeskResultsPage,monitor:window.WorkdeskMonitorPage};
  if(supportPages[parts[0]]){state.route=current;state.generation++;await supportPages[parts[0]].render();await window.WorkdeskSupport.refreshChrome(token);return;}
  await legacyRoute();
  window.WorkdeskWorkbench?.load().then(()=>updateSidebar()).catch(()=>{});
}
Object.assign(paths,{
 list:'<path d="m3 6 2 2 4-4m-6 13 2 2 4-4M12 6h9M12 12h9M12 18h9"/>',
 trash:'<path d="M3 6h18M19 6v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M10 10v7M14 10v7"/>',
 house:'<path d="m3 10 9-7 9 7v10H3V10Z"/><path d="M9 20v-7h6v7"/>',
 chat:'<path d="M21 11a9 9 0 0 1-9 9H4l-2 2v-9a9 9 0 1 1 19-2Z"/>',
 workflow:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M6 9v9h9M9 6h9v9"/>',
 plug:'<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
 more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 chevron:'<path d="m7 10 5 5 5-5"/>',up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
 lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
 research:'<path d="m3 10 15-6 3 7-15 6-3-7ZM9 16l-3 5m8-7 4 7M5 11l2 4"/>',
 pointer:'<path d="m5 3 4 17 3-6 7-3L5 3Z"/>',terminal:'<path d="m4 5 6 6-6 6m9 0h7"/>',
 compass:'<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5 5-3Z"/>',
 review:'<path d="M3 7V3h4m10 0h4v4M3 17v4h4m10 0h4v-4M3 12s3-5 9-5 9 5 9 5-3 5-9 5-9-5-9-5Z"/><circle cx="12" cy="12" r="2"/>',
 pin:'<path d="m14 3 7 7-4 1-4 4-1 4-7-7 4-1 4-4 1-4ZM3 21l5-5"/>',
 sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>'
});
icons();
