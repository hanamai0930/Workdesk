/* Versioned scene/extension management; reads and saves never dispatch models. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('extensions'),h=esc;
  const v={data:null,edit:null,busy:false,timer:null,seq:0,path:'',error:''};
  const call=(route,options)=>api('v1/'+route,options),tab=()=>location.hash.split('/')[1]||'scenes';
  const tabs={scenes:'场景与预设',skills:'Skills',plugins:'插件',mcp:'MCP 工具',records:'加载与操作记录'};
  const raw=value=>`<pre class="ex-raw">${h(JSON.stringify(value,null,2))}</pre>`;
  const btn=(label,action,id='',extra='')=>`<button type="button" data-ext="${action}" data-id="${h(id)}" ${extra}>${h(label)}</button>`;
  const input=(label,name,value='',extra='')=>`<label class="ex-field"><span>${h(label)}</span><input aria-label="${h(label)}" name="${name}" value="${h(value)}" ${extra}></label>`;
  const text=(label,name,value='',rows=4)=>`<label class="ex-field"><span>${h(label)}</span><textarea aria-label="${h(label)}" name="${name}" rows="${rows}">${h(value)}</textarea></label>`;
  const status=yes=>`<span class="ex-status ${yes?'is-on':''}">${yes?'已启用':'已停用'}</span>`;
  const short=value=>String(value||'').slice(0,8),empty=m=>`<div class="ex-empty">${h(m)}</div>`,note=m=>`<p class="ex-note">${h(m)}</p>`;
  const caps=()=>v.data.capabilities||[],options=(rows,value)=>rows.map(([id,label])=>`<option value="${h(id)}" ${id===value?'selected':''}>${h(label)}</option>`).join('');
  function sceneEditor(){
    if(!v.edit)return '';const e=v.edit.row;
    return `<section class="ex-editor"><div class="ex-card-title"><h2>${e?'编辑预设':'新建预设'}</h2>${btn('取消','cancel')}</div><form id="ex-scene-form"><div class="ex-two">${input('预设名称','name',e?.name||'','required maxlength="100"')}<label class="ex-field"><span>所属场景</span><select name="scene">${options(v.data.scenes.categories.map(c=>[c.id,c.name]),e?.scene||'research')}</select></label></div>${text('提示词','prompt',e?.prompt||'',5)}${text('输出要求','output',e?.output||'',3)}<fieldset><legend>组合能力</legend>${note('选定能力随此预设加载；缺失或停用时会要求修复，不会静默跳过。')}${[...caps().map(c=>({...c,field:'capabilities',description:(c.kind==='skill'?'Skill':'插件')+' · '+c.skills.length+' Skills / '+Object.keys(c.servers).length+' MCP'})),...v.data.extensions.map(c=>({...c,field:'extensions',description:'MCP'}))].map(c=>`<label class="ex-check"><input type="checkbox" name="${c.field}" value="${h(c.id)}" ${(e?.[c.field]||[]).includes(c.id)?'checked':''}><span>${h(c.name)}<small>${h(c.description)} · ${c.enabled?'启用':'停用'}</small></span></label>`).join('')||note('尚无能力包。先到 Skills、插件或 MCP 页添加。')}</fieldset><div class="ex-actions"><button type="submit" class="primary">保存预设</button>${btn('取消','cancel')}</div>${note('只影响之后新建的工作。已有会话使用开始时保存的版本；场景不更改访问权限。')}</form></section>`;
  }
  function scenes(){
    return `<div class="ex-intro"><div><h1>场景与能力预设</h1><p>保存提示词、输出要求和能力组合。</p></div>${btn('新建预设','new-scene')}</div>${sceneEditor()}<div class="ex-grid">${v.data.scenes.categories.map(c=>`<section><h2>${h(c.name)}</h2>${v.data.scenes.presets.filter(p=>p.scene===c.id).map(p=>{
      const bindings=[...p.capabilities.map(id=>caps().find(b=>b.id===id)),...p.extensions.map(id=>v.data.extensions.find(b=>b.id===id))],missing=bindings.filter(b=>!b?.enabled).length;
      return `<article class="ex-card"><div class="ex-card-title"><h3>${h(p.name)}</h3>${status(p.enabled)}</div><div class="ex-tags">${p.default?'<span>当前场景默认</span>':''}<span>${bindings.length} 项能力</span><span>版本 ${short(p.revision)}</span></div><p class="ex-description">${h(p.prompt||'未填写提示词')}</p>${missing?`<p class="ex-error">${missing} 项能力缺失或已停用，需调整后才能使用。</p>`:''}<details><summary>输出要求与组合</summary><p>${h(p.output||'未填写')}</p>${bindings.map(b=>`<span class="ex-inline-tag">${h(b?.name||'已移除能力')}</span>`).join('')}</details><div class="ex-actions">${btn('编辑','edit-scene',p.id)}${!p.default&&p.enabled?btn('设为默认','default-scene',p.id):''}${btn(p.enabled?'停用':'启用','toggle-scene',p.id)}${btn('移除','remove-scene',p.id)}</div></article>`;
    }).join('')||empty('没有预设，将使用工作端原有配置。')}</section>`).join('')}</div><div class="ex-footnote">Work 开始页选择场景后，应用该场景的已启用默认预设。普通项目仍可不归入场景；列表分类与实际执行配置分别保存。</div>`;
  }
  function bundleEditor(kind){
    if(!v.edit)return '';const e=v.edit.row;
    return `<section class="ex-editor"><h2>${e?'更新已导入版本':'导入'+(kind==='skill'?' Skill':'插件包')}</h2><form id="ex-bundle-form" data-kind="${kind}"><label class="ex-field"><span>${kind==='skill'?'含 SKILL.md 的文件夹':'插件包文件夹'}</span><div class="ex-path"><input name="path" value="${h(v.path)}" readonly required placeholder="选择本机文件夹">${btn('选择文件夹','pick-folder')}</div></label>${input('来源','source',e?.source||'','required placeholder="官方仓库、作者或本地来源"')}${note(kind==='skill'?'保留完整资源、脚本和引用，导入时不执行。':'适配 portable、Codex 和 Qwen 声明包中的 Skills / MCP；其他组件会列为未适配。')}<div class="ex-actions"><button type="submit" class="primary">${e?'保存新版本':'导入并查看'}</button>${btn('取消','cancel')}</div>${note('导入后默认停用。原文件不变；旧任务仍使用旧版本。')}</form></section>`;
  }
  function bundles(kind){
    return `<div class="ex-intro"><div><h1>${kind==='skill'?'Skills':'插件'}</h1><p>${kind==='skill'?'管理工作方法及配套资源。':'组合管理 Skills 和 MCP 配置。'}</p></div>${btn('从文件夹导入','new-bundle',kind)}</div>${bundleEditor(kind)}<div class="ex-grid">${caps().filter(c=>c.kind===kind).map(c=>`<article class="ex-card" data-ext-record="${h(c.id)}"><div class="ex-card-title"><h2>${h(c.name)}</h2>${status(c.enabled)}</div><p class="ex-description">${h(c.skills[0]?.description||c.source)}</p><div class="ex-tags"><span>${c.skills.length} Skills</span><span>${Object.keys(c.servers).length} MCP</span><span>${short(c.content_hash)}</span></div>${note('来源：'+c.source)}${c.warnings.map(w=>`<p class="ex-warning">${h(w)}</p>`).join('')}<details><summary>内容和版本</summary>${c.skills.map(s=>`<h3>${h(s.name)}</h3><p>${h(s.description)}</p><pre class="ex-skill-body">${h(s.body)}</pre>`).join('')}${Object.entries(c.servers).map(([name,config])=>`<h3>${h(name)}</h3>${raw(config)}${btn('添加到 MCP 管理','register-server',c.id,'data-server="'+h(name)+'"')}`).join('')}${note('版本：'+c.revision)}${note('原文件夹：'+c.source_path)}${note('保存位置：'+c.path)}</details><div class="ex-actions">${btn(c.enabled?'停用':'启用','toggle-bundle',c.id)}${btn('更新版本','edit-bundle',c.id)}${btn('移除','remove-bundle',c.id)}</div></article>`).join('')||empty('尚未导入。')}</div>${note('启用后可在预设中选择，不会自动安装到全局工作端。原生工作端自身的插件配置保持独立。')}`;
  }
  function mcpEditor(){
    if(!v.edit)return '';const e=v.edit.row,c=e?.config||{},http=c.transport==='http';
    return `<section class="ex-editor"><h2>${e?'编辑 MCP':'添加 MCP 服务'}</h2><form id="ex-mcp-form"><div class="ex-two">${input('唯一名称','name',e?.name||'','required pattern="[A-Za-z][A-Za-z0-9_-]{0,63}"')}${input('来源','source',e?.source||'','required')}</div><label class="ex-field"><span>连接方式</span><select name="transport" id="ex-transport">${options([['stdio','本地程序（stdio）'],['http','远程服务（HTTP）']],http?'http':'stdio')}</select></label><div data-ex-transport="stdio" ${http?'hidden':''}>${input('程序绝对路径','command',c.command||'')}${input('工作目录','cwd',c.cwd||'')}${text('程序参数（每行一个）','args',(c.args||[]).join('\n'),3)}</div><div data-ex-transport="http" ${http?'':'hidden'}>${input('服务地址','url',c.url||'','placeholder="https://example.com/mcp"')}${note('支持 Streamable HTTP。需认证的服务请先在官方工作端配置和登录，由工作端自身加载；此入口不保存令牌或导入登录状态。')}</div><div class="ex-actions"><button type="submit" class="primary">保存配置</button>${btn('取消','cancel')}</div>${note('保存后默认停用。检查只读取目录；调用工具需另行确认准确名称和参数。')}</form></section>`;
  }
  function mcp(){
    return `<div class="ex-intro"><div><h1>MCP 工具</h1><p>连接外部能力，查看实际工具目录。</p></div>${btn('添加 MCP','new-mcp')}</div>${mcpEditor()}<div class="ex-grid">${v.data.extensions.map(e=>{
      const last=v.data.runs.find(r=>r.extension.id===e.id&&r.extension.revision===e.revision&&r.state==='SUCCEEDED');
      return `<article class="ex-card"><div class="ex-card-title"><h2>${h(e.name)}</h2>${status(e.enabled)}</div>${note(e.source)}<p class="ex-endpoint">${h(e.config.url||e.config.command)}</p><div class="ex-tags"><span>${e.config.transport==='http'?'HTTP':'本地 stdio'}</span><span>${last?last.tools.length+' 个工具 · 检查通过':'尚未检查'}</span><span>版本 ${short(e.revision)}</span></div><div class="ex-actions">${btn(e.enabled?'停用':'启用','toggle-mcp',e.id)}${btn('编辑','edit-mcp',e.id)}${e.enabled?btn('连接检查','check-mcp',e.id):''}${btn('移除','remove-mcp',e.id)}</div>${last?`<details><summary>工具目录 / 调用一次</summary>${last.tools.map(t=>`<div class="ex-tool"><h3>${h(t.title||t.name)}</h3><code>${h(t.name)}</code><p>${h(t.description||'未提供说明')}</p><details><summary>参数格式</summary>${raw(t.inputSchema||{})}</details></div>`).join('')}${e.enabled?`<form data-ex-call="${h(e.id)}"><label class="ex-field"><span>工具</span><select name="tool" aria-label="工具">${options(last.tools.map(t=>[t.name,t.name]),last.tools[0]?.name)}</select></label>${text('调用参数（JSON 对象）','arguments','{}',3)}<button type="submit">调用一次</button></form>`:''}</details>`:''}<details><summary>连接配置</summary>${raw(e.config)}</details></article>`;
    }).join('')||empty('尚未添加 MCP 服务。')}</div><div class="ex-footnote">本地程序以当前系统用户运行，连接检查不是执行隔离。手动调用只发送填写的参数，不附带聊天历史或模型凭据。</div>`;
  }
  function records(){
    const labels={FROZEN:'配置已保存，等待工作端',NATIVE_INPUT_ACCEPTED:'原生输入已接收',NATIVE_OBSERVED:'已收到原生目录',FAILED:'失败',SUCCEEDED:'成功',UNKNOWN:'状态待核对',RUNNING:'正在连接',QUEUED:'等待运行',STOP_REQUESTED:'正在停止'};
    return `<div class="ex-intro"><div><h1>加载与操作记录</h1><p>配置保存、原生加载、工具执行分别记录。</p></div>${btn('刷新','refresh')}</div><h2>场景能力加载</h2>${v.data.scenes.loads.map(r=>`<article class="ex-card"><div class="ex-card-title"><h3>${h(r.preset.name)} · ${h(r.provider)}</h3><span>${h(labels[r.state]||r.state)}</span></div>${note('预设版本 '+short(r.preset.revision)+' · '+new Date(r.updated*1000).toLocaleString())}<p>${r.skills.length} Skills / ${r.mcp.length} MCP</p><div class="ex-actions"><a href="#work/task/${h(r.task_id)}">打开工作</a><a href="#control/task/${h(r.task_id)}">原生控制与事件</a></div><details><summary>实际回执</summary>${raw(r)}</details></article>`).join('')||empty('尚无加载记录。')}<h2>MCP 检查与调用</h2>${v.data.runs.map(r=>`<article class="ex-card"><div class="ex-card-title"><h3>${h(r.extension.name)} · ${r.operation==='call'?'调用工具':'连接检查'}</h3><span>${h(labels[r.state]||r.state)}</span></div>${r.error?`<p class="ex-error">${h(r.error)}</p>`:''}${['QUEUED','RUNNING','STOP_REQUESTED'].includes(r.state)?btn('停止','stop',r.id):''}<details><summary>准确输入、工具目录与结果</summary>${raw(r)}</details></article>`).join('')||empty('尚无操作记录。')}`;
  }
  function showRecord(id){
    const row=[...$('#main').querySelectorAll('[data-ext-record]')].find(node=>node.dataset.extRecord===id);
    const details=row?.querySelector('details');
    if(details)details.open=true;
    row?.scrollIntoView({block:'nearest'});
  }
  function draw(){if(location.hash.startsWith('#extensions'))$('#main').innerHTML=`<div class="ex-page">${tab()==='skills'?'<a class="detail-back" href="#settings/extensions">返回设置 · Skills</a>':''}<nav class="ex-tabs" aria-label="能力管理">${Object.entries(tabs).map(([id,name])=>`<a href="#extensions/${id}" aria-current="${id===tab()?'page':'false'}">${name}</a>`).join('')}</nav>${v.error?`<p class="ex-error" role="alert">${h(v.error)}</p>`:''}${tab()==='scenes'?scenes():tab()==='skills'?bundles('skill'):tab()==='plugins'?bundles('plugin'):tab()==='mcp'?mcp():records()}</div>`;}
  function applyEditorLink(data, route){
    const [,category,action,encodedId]=route.split('/');
    const type={skills:'skill',plugins:'plugin',mcp:'mcp',scenes:'scene'}[category];
    if(!type||!['add','edit','view'].includes(action))return;
    let identity;
    try{identity=decodeURIComponent(encodedId||'');}catch{identity='';}
    const entries=type==='scene'?data.scenes.presets:type==='mcp'?data.extensions:data.capabilities.filter(entry=>entry.kind===type);
    const row=entries.find(entry=>entry.id===identity);
    const next='#extensions/'+category;
    history.replaceState(history.state,'',next);
    if(action==='add'){v.edit={type,row:null};return;}
    if(!row){v.error='此条设置已不存在，请重新选择。';return;}
    if(action==='edit'){v.edit={type,row};v.path=row.source_path||'';return;}
    return row.id;
  }
  async function render(){
    clearTimeout(v.timer);
    const seq=++v.seq, route=location.hash;
    try{
      const data=await call('extensions');
      if(seq!==v.seq||route!==location.hash||!route.startsWith('#extensions'))return;
      v.data=data;
      const record=applyEditorLink(data,route);
      draw();
      if(record)showRecord(record);
      if(tab()==='records'&&data.runs.some(r=>['QUEUED','RUNNING','STOP_REQUESTED'].includes(r.state)))v.timer=setTimeout(render,1000);
    }catch(e){if(seq===v.seq&&location.hash.startsWith('#extensions'))$('#main').innerHTML=errorPage(e.message);}
  }
  async function command(kind,payload,confirm){
    if(v.busy)return;v.busy=true;v.error='';
    try{if(confirm&&!await window.WorkdeskDialogs.confirm({title:'确认操作',message:confirm,details:JSON.stringify(payload,null,2)}))return;const origin=location.hash,id=crypto.randomUUID();const result=await call('command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,kind,payload})});v.edit=null;v.path='';await render();if(origin===location.hash&&kind==='capability.import'&&result.receipt?.capability?.id)showRecord(result.receipt.capability.id);return true;}
    catch(e){v.error=e.message+'。未自动重发；可到运行与控制查看回执。';draw();return false;}finally{v.busy=false;}
  }
  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-ext]');if(!el||v.busy||!location.hash.startsWith('#extensions'))return;event.preventDefault();const action=el.dataset.ext,id=el.dataset.id;
    if(action==='refresh')return render();if(action==='cancel'){v.edit=null;v.path='';v.error='';return draw();}
    if(action.startsWith('new-')){v.edit={type:action.slice(4),row:null};v.path='';v.error='';return draw();}
    if(action==='pick-folder'){const origin=location.hash;v.busy=true;el.disabled=true;try{const r=await api('pick-directory',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(origin===location.hash&&v.edit&&r.path){v.path=r.path;$('[name=path]').value=r.path;}}catch(e){toast(e.message);}finally{v.busy=false;if(el.isConnected)el.disabled=false;}return;}
    if(action==='stop')return command('extension.stop',{run_id:id});
    const row=v.data.scenes.presets.find(p=>p.id===id)||caps().find(c=>c.id===id)||v.data.extensions.find(c=>c.id===id);if(!row)return;const version={id,revision:row.revision};
    if(action.startsWith('edit-')){v.edit={type:action.slice(5),row};v.path=row.source_path||'';return draw();}
    if(action==='default-scene')return command('scene.default',version);
    if(action.startsWith('toggle-'))return command(action==='toggle-scene'?'scene.enable':action==='toggle-bundle'?'capability.enable':'extension.enable',{...version,enabled:!row.enabled});
    if(action.startsWith('remove-'))return command(action==='remove-scene'?'scene.remove':action==='remove-bundle'?'capability.remove':'extension.remove',version,'从列表移除；已有工作版本、历史记录和原文件保留。');
    if(action==='register-server')return command('extension.save',{name:(row.name+'-'+el.dataset.server).slice(0,64),source:row.source,config:row.servers[el.dataset.server]});
    if(action==='check-mcp'){if(await command('extension.run',{...version,operation:'check'},'连接此服务并读取工具目录；不调用工具、不发送模型请求。'))location.hash='#extensions/records';}
  });
  document.addEventListener('change',e=>{if(e.target.id==='ex-transport')for(const box of $('#main').querySelectorAll('[data-ex-transport]'))box.hidden=box.dataset.exTransport!==e.target.value;});
  document.addEventListener('submit',async event=>{
    const form=event.target;if(!form.id?.startsWith('ex-')&&!form.dataset?.exCall)return;event.preventDefault();if(v.busy)return;
    try{const f=new FormData(form),old=v.edit?.row,version=old?{id:old.id,revision:old.revision}:{};
      if(form.id==='ex-scene-form')return command('scene.save',{...version,name:f.get('name'),scene:f.get('scene'),prompt:f.get('prompt'),output:f.get('output'),capabilities:f.getAll('capabilities'),extensions:f.getAll('extensions')});
      if(form.id==='ex-bundle-form')return command('capability.import',{...version,kind:form.dataset.kind,path:f.get('path'),source:f.get('source')});
      if(form.id==='ex-mcp-form')return command('extension.save',{...version,name:f.get('name'),source:f.get('source'),config:f.get('transport')==='http'?{transport:'http',url:f.get('url')}:{transport:'stdio',command:f.get('command'),cwd:f.get('cwd'),args:String(f.get('args')).split('\n').filter(a=>a.length)}});
      const e=v.data.extensions.find(e=>e.id===form.dataset.exCall);if(await command('extension.run',{id:e.id,revision:e.revision,operation:'call',tool:f.get('tool'),arguments:JSON.parse(f.get('arguments'))},'只发送填写的准确参数，调用此工具一次。'))location.hash='#extensions/records';
    }catch(e){v.error=e.message;draw();}
  });
  window.addEventListener('hashchange',()=>{v.edit=null;v.path='';v.error='';});
  window.WorkdeskModules.onLeave('extensions',()=>{clearTimeout(v.timer);++v.seq;v.edit=null;v.path='';v.error='';});
  window.WorkdeskExtensions={render};
})();
