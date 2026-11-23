/* Minimal functional controls. Business rules and execution remain in the independent service. */
(() => {
  'use strict';
  const $=window.WorkdeskModules.dom('control');
  const view={catalog:null,detail:null,timer:null,seq:0,taskRead:0,events:[],cursor:0,tab:'progress',pending:null,usageTasks:null,usageQuery:'',usagePage:0};
  let embedded=null;
  const controlActive=()=>location.hash.split('/')[0]==='#control'&&$('#main').isConnected;
  const embeddedActive=()=>embedded?.host.isConnected&&location.hash===embedded.hash;
  const context=()=>({seq:view.seq,hash:location.hash,embedded});
  const current=origin=>origin&&origin.seq===view.seq&&origin.hash===location.hash&&
    origin.embedded===embedded&&(embedded?embeddedActive():controlActive());
  const active=new Set(['RESERVED','SEND_INTENT','RUNNING','WAITING_APPROVAL','WAITING_INPUT','STOP_REQUESTED']);
  const labels={DRAFT:'草稿',APPROVED:'范围已确认',RESERVED:'已登记执行意图',SEND_INTENT:'发送待核对',RUNNING:'已保存运行记录',WAITING_APPROVAL:'等待审批',WAITING_INPUT:'等待补充输入',STOP_REQUESTED:'停止请求待确认',COMPLETED:'回合结束（待验收）',FAILED:'失败',FAILED_NO_SEND:'发送前失败',SEND_UNKNOWN:'发送或执行未知',STOP_UNKNOWN:'停止未知',ORPHANED_UNKNOWN:'重启后待核对',INTERRUPTED:'协议已中断',STOPPED_RUNTIME:'专属运行时已停止',PAUSED:'已暂停',CANCELLED:'已取消'};
  const h=esc;
  const field=(label,name,type='text',value='',extra='')=>`<label class="ctl-field"><span>${label}</span><input name="${name}" type="${type}" value="${h(value)}" ${extra}></label>`;
  const text=(label,name,value='',placeholder='')=>`<label class="ctl-field"><span>${label}</span><textarea name="${name}" placeholder="${h(placeholder)}">${h(value)}</textarea></label>`;
  const button=(title,action,extra='',primary=false)=>`<button class="button ${primary?'primary':'secondary'}" data-ctl="${action}" ${extra}>${title}</button>`;
  const status=row=>`<span class="status ${row.state==='COMPLETED'?'neutral':row.state.includes('FAILED')?'danger':row.state.includes('UNKNOWN')?'warning':'neutral'}">${h(labels[row.state]||row.state)}</span>`;
  const date=epoch=>new Date(epoch*1000).toLocaleString('zh-CN',{hour12:false});
  function attachmentRequests(data){
    const lines=name=>String(data.get(name)||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    const visual=lines('visual_attachments').map(line=>{const f=line.split('|').map(x=>x.trim());if(f.length>2)throw Error('PDF视觉材料格式为路径，可选加 | 页码范围。');return {file:f[0],route:'pdf_visual',...(f[1]?{pages:f[1]}:{})};});
    return [...visual,...lines('native_attachments').map(file=>({file,route:'native'})),...lines('extracted_attachments').map(line=>{
      const fields=line.split('|').map(x=>x.trim());if(fields.length>2)throw Error('文字提取材料格式为路径，可选加 | 页码范围。');
      return {file:fields[0],route:'extract_text',...(fields[1]?{pages:fields[1]}:{})};
    })];
  }
  function attachmentList(detail){
    const files=detail.task.spec.attachments||[];if(!files.length)return '';
    const reports=detail.attempts.at(-1)?.result?.attachments||[];
    const names={SAVED_NOT_READ:'已保存，尚未读取',AWAITING_NATIVE_TOOL_READ:'等待原生工具读取',NATIVE_INPUT_PREPARED_NOT_COMPREHENSION:'原生输入已准备，理解未核验',LOCAL_TEXT_EXTRACTED_NOT_MODEL_READ:'本地文字已提取，模型读取未核验',NATIVE_TOOL_REPORTED_ERROR:'原生工具读取失败',NATIVE_TOOL_RESULT_RETURNED_NOT_COMPREHENSION:'原生工具已返回，理解未核验',PDF_PAGES_RENDERED_NOT_MODEL_READ:'PDF页面已渲染，模型理解未核验',PDF_PAGE_TOOL_RESULT_RETURNED_NOT_COMPREHENSION:'PDF页图工具已返回，理解未核验',READ_FAILED_BEFORE_MODEL_SEND:'本地读取失败，未发送模型'};
    return `<div class="source-block"><h3>本任务附件</h3>${files.map(file=>{
      const report=reports.find(r=>r.file===file.file&&r.sha256===file.sha256)||file;
      return `<p><strong>${h(file.file)}</strong> · ${h(file.mime)} · ${file.bytes} 字节<br>${file.route==='pdf_visual'?'明确选择：PDF页面视觉（本机渲染PNG→所选模型看图，非PDF直传/文字提取；144 DPI）':file.route==='extract_text'?'明确选择：本机文字提取（不含OCR，版式/图像不保留）':file.delivery==='NATIVE_READ_FILE_IMAGE'?'Qwen原生图像读取工具':file.delivery==='NATIVE_DOCUMENT_TOOL'?'Qwen原生PDF读取（可能本地提取/渲染，缺少运行时会失败）':'原生模型图像输入'}<br>${h(names[report.status]||report.status)}${file.route==='pdf_visual'?`<br>选页：${h(file.pages||'全部（最多10页）')}${report.rendering?` · 已渲染 ${h(report.rendering.pages.join('-'))} / 总 ${report.rendering.total_pages} 页 · 未提供 ${report.rendering.remaining_pages_not_read} 页`:''}${report.tool_returned_pages?` · 工具已返回页面 ${h(report.tool_returned_pages.join(', '))}`:''}`:''}${report.error?`<br><span class="danger">${h(report.error)}</span>`:''}</p>`;
    }).join('')}<p>文件保存、通道接收、工具读取、模型理解分别核对；不会自动OCR、改换模型或交给另一家。</p></div>`;
  }
  function leave(){
    clearTimeout(view.timer);view.timer=null;view.usageTasks=null;view.seq++;
    if(!embedded)return;
    embedded=null;view.detail=null;view.pending=null;
    const dialog=$('#ctl-dialog');
    if(dialog?.controlContext?.embedded){
      dialog.effect=null;dialog.controlContext=null;
      if(dialog.open)dialog.close();
    }
  }
  function unmountTask(){if(embedded)leave();}
  function fail(error){toast(error.message||String(error));}
  async function call(route,value){return api('v1/'+route,value===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});}
  async function command(kind,payload){
    const id=crypto.randomUUID(),pending={id,kind,payload};view.pending=pending;
    try{const value=await call('command',{id,kind,payload});if(view.pending===pending)view.pending=null;return value.receipt;}
    catch(error){toast('命令未完成或回执未知：'+error.message+'。命令身份 '+id+'；先刷新核对，不会自动重发。');throw error;}
  }
  async function catalog(){view.catalog=await call('workspace');return view.catalog;}
  function actions(){return `<div class="heading-actions">${button('刷新后端','refresh')}<a class="button secondary" href="#control/receipts">操作回执</a><a class="button secondary" href="#control/profiles">工作端配置</a><a class="button primary" href="#control/new">新建任务</a></div>`;}
  function index(){
    const data=view.catalog;
    const rows=data.tasks.map(task=>`<a class="ctl-usage-task" href="#control/task/${encodeURIComponent(task.id)}"><div><strong>${h(task.title)}</strong><span>${h(task.spec.provider)} · ${h(task.spec.model)} · ${h(date(task.created))}</span></div>${status(task)}</a>`).join('')||empty('尚无任务。');
    const usage=data.campaign_usage,limits=data.campaign_limits;
    return `<section class="page ctl-usage"><header class="ctl-usage-heading"><h1>任务执行</h1>${actions()}</header>${usageCard('普通任务',rows)}<details class="ctl-usage-card ctl-usage-technical"><summary>服务状态与历史批次</summary><p>控制服务 ${h(data.health.instance.slice(0,8))} · 协议 v${h(data.protocol)} · 本地执行线程 ${h(data.health.active_owned_workers)} 个。</p><p>已封闭的后端批次 · API请求 ${h(usage.model_requests)} / ${h(limits.requests)} · 预算预留（人民币）${h(usageNumber(usage.reserved_cny))} / ${h(limits.cny)} · Codex尝试 ${h(usage.codex_turns)} / ${h(limits.codex_turns)}。</p></details></section>`;
  }
  const usageNumber=value=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:6}):'未知';
  const usageCard=(title,body)=>`<section class="ctl-usage-card"><h2>${h(title)}</h2>${body}</section>`;
  const usageModes={fixed:'固定流程模式',leader:'负责人带队模式',host_experts:'主持人—专家模式',cooperative:'合作工作',independent:'独立工作'};
  const usageProviders={codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen'};
  const usageTitle=task=>[usageModes[task.mode]||'工作',task.project,task.title].filter(Boolean).join(' - ');
  const modelName=row=>[usageProviders[row.provider]||row.provider,row.model].filter(Boolean).join(' / ');
  const usageCount=value=>value==null?'—':usageNumber(value);
  const currency=new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY',maximumFractionDigits:8});
  const usageCost=row=>row.estimated_records?(row.low===row.high?currency.format(Number(row.high)):
    currency.format(Number(row.low))+'–'+currency.format(Number(row.high))):'—';
  const usagePageSize=8;

  function taskUsageCard(task){
    const title=usageTitle(task);
    const models=task.models.map(row=>`<li><span>${h(modelName(row))}</span><strong>${row.tokens==null?'暂无 Token 回执':h(usageCount(row.tokens)+' Tokens')}</strong>
      ${row.estimated_records?`<small>估算 ${h(usageCost(row))}</small>`:''}</li>`).join('');
    return `<article class="ctl-usage-card ctl-usage-summary"><h2><a href="#control/usage/${encodeURIComponent(task.id)}" title="${h(title)}">
      <span>${h(title)}</span>${icon('arrow')}</a></h2><ul aria-label="按模型汇总">${models}</ul></article>`;
  }
  function paintUsageList(){
    const term=view.usageQuery.trim().toLocaleLowerCase();
    const results=view.usageTasks.filter(task=>
      [usageTitle(task),...task.models.map(modelName)].join(' ').toLocaleLowerCase().includes(term));
    const pages=Math.ceil(results.length/usagePageSize);
    view.usagePage=Math.max(0,Math.min(view.usagePage,pages-1));
    const start=view.usagePage*usagePageSize;
    $('#ctl-usage-list').innerHTML=results.slice(start,start+usagePageSize).map(taskUsageCard).join('')||
      `<p class="ctl-usage-note" role="status">${term?'没有匹配的任务。':'暂无工作用量记录。'}</p>`;
    $('#ctl-usage-pager').hidden=pages<2;
    $('#ctl-usage-page').textContent=`第 ${view.usagePage+1} / ${pages} 页`;
    $('#ctl-usage-prev').disabled=view.usagePage===0;
    $('#ctl-usage-next').disabled=view.usagePage+1>=pages;
  }
  function recordedUsage(task){
    const models=task.steps.map((step,index)=>{
      const stepTitle=step.title||`第 ${index+1} 次执行`;
      return `<tbody>${step.models.map((row,i)=>`<tr>${i===0?`<th scope="rowgroup" rowspan="${step.models.length}">
        <strong>${h(stepTitle)}</strong><time>${h(date(step.started))}</time>
        <a href="#control/task/${encodeURIComponent(step.task_id)}">查看工作回合</a>
        ${step.reserved>0?`<small>预算预留 ${h(currency.format(step.reserved))}</small>`:''}</th>`:''}
        <td>${h(modelName(row))}</td><td>${h(usageCount(row.cache_hit))}</td><td>${h(usageCount(row.cache_miss))}</td>
        <td>${h(usageCount(row.output))}</td><td><strong>${h(usageCount(row.tokens))}</strong></td><td>${h(usageCost(row))}</td></tr>`).join('')}</tbody>`;
    }).join('');
    return usageCard('步骤消耗',models?`<div class="ctl-usage-scroll"><table class="ctl-usage-steps">
      <thead><tr><th>工作步骤</th><th>模型</th><th>命中输入</th><th>未命中输入</th><th>输出</th><th>总 Tokens</th><th>估算费用</th></tr></thead>
      ${models}</table></div>`:'<p class="ctl-usage-note">这项工作暂无执行记录。</p>');
  }
  async function renderUsage(id,seq){
    const path=id?'work-usage?id='+encodeURIComponent(id):'work-usage';
    const back=id?['#control/usage','工作用量']:['#settings/usage','用量与预算'];
    const title=id?'任务用量明细':'工作用量';
    const page=(body)=>`<section class="page ctl-usage ctl-work-consumption"><a class="detail-back" href="${back[0]}">${icon('back')}${back[1]}</a>
      <header class="ctl-usage-heading"><h1>${title}</h1><div class="ctl-usage-actions">${button('刷新','refresh')}</div></header>${body}</section>`;
    $('#main').innerHTML=page('<p class="ctl-usage-note" role="status">正在读取工作用量…</p>');
    let data;
    try{data=await call(path);}
    catch(error){
      if(seq===view.seq)$('#main').innerHTML=page(`<p class="ctl-usage-note" role="status">${h(error.message)}</p>`);
      return;
    }
    if(seq!==view.seq||location.hash.split('/')[1]!=='usage')return;
    $('#main').scrollTop=0;
    if(id){
      const detail=data.task;
      $('#main').innerHTML=page(`<p class="ctl-usage-detail-title">${h(usageTitle(detail))}</p>${recordedUsage(detail)}
        <p class="ctl-usage-note">「—」表示缺少对应回执或费用估算。DeepSeek 费用按公开峰谷价估算。</p>`);
      return;
    }
    view.usageTasks=data.tasks;
    $('#main').innerHTML=page(`<label class="ctl-usage-search"><input type="search" aria-label="搜索工作任务"
      id="ctl-usage-search" placeholder="搜索任务、项目或模型" value="${h(view.usageQuery)}"></label><div id="ctl-usage-list" class="ctl-usage-list"></div>
      <nav id="ctl-usage-pager" class="ctl-usage-actions" aria-label="任务分页" hidden>
        ${button('上一页','usage-prev','id="ctl-usage-prev"')}<span id="ctl-usage-page" aria-live="polite"></span>
        ${button('下一页','usage-next','id="ctl-usage-next"')}</nav>`);
    paintUsageList();
  }
  document.addEventListener('input',event=>{
    if(event.target.id!=='ctl-usage-search'||view.usageTasks===null)return;
    view.usageQuery=event.target.value;
    view.usagePage=0;
    paintUsageList();
  });
  function newTask(){const data=view.catalog;return `<div class="page"><a class="detail-back" href="#control">${icon('back')}任务执行</a>${heading('新建任务','先保存草稿与材料快照，再确认范围和开始执行。','','BACKEND / NEW TASK')}<form id="ctl-task-form"><div class="ctl-grid">${field('项目目录','root','text',data.projects[0]?.root||'','required')}${field('任务标题','title')}</div>${text('任务目标','goal','','说明需要工作端完成的工作。')}<label class="ctl-field"><span>任务职责</span><select name="role"><option value="executor">执行</option><option value="diagnoser">诊断</option><option value="self_check">只读自检</option><option value="reviewer">独立审查（另会话）</option><option value="coordinator">主协作建议</option></select></label>${text('验收条件','acceptance','','说明检查方式与需要交付的结果。')}<label class="ctl-field"><span>执行路径（独立工作方式，与思考/速度分别选择）</span><select name="execution"><option value="codex_native">Codex 原生独立运行</option><option value="qwen_native">Qwen 原生独立运行</option><option value="deepseek_native">DeepSeek 原生独立运行</option><option value="scoped">历史受限工具样板</option></select></label><p class="section-subtitle">原生路径使用官方命令、编辑和检查工具。会话保存上下文，工作副本保存文件，沙箱限制访问；本机探测未观察到OS写入隔离；文件及网络访问按任务授权约束，不能将副本目录当作沙箱。网络、Web、委派和外部扩展在本任务中不授权。</p>${text('已授权替代工作端（JSON数组，默认无；交接会传递原材料、候选及任务完整记录）','alternatives','[]','[{"provider":"qwen","model":"qwen3.8-flash","effort":"off","tier":"standard","role":"executor"}]')}<div class="ctl-grid"><label class="ctl-field"><span>工作端</span><select name="provider" id="ctl-provider">${data.profiles.map(p=>`<option value="${p.id}">${h(p.name)}</option>`).join('')}</select></label><label class="ctl-field"><span>模型</span><select name="model" id="ctl-model"></select></label><label class="ctl-field"><span>思考</span><select name="effort" id="ctl-effort"></select></label><label class="ctl-field"><span>速度</span><select name="tier" id="ctl-tier"></select></label></div><div class="ctl-grid">${text('选定材料（每行一个项目内相对路径，可选择目录）','files','','src/example.py\ntests/test_example.py')}${text('允许写入的路径（包括拟新建文件，每行一个）','writable','','src/example.py')}</div><div class="ctl-grid">${text('PDF页面视觉（本机渲染→模型看图；每行路径，可加 | 1-5）','visual_attachments','','docs/report.pdf | 1-5')}${text('原生图像/附件（每行一个项目内相对路径）','native_attachments','','assets/chart.png')}${text('可选：本机文字提取（PDF/DOCX/XLSX/PPTX；每行路径，可加 | 1-5）','extracted_attachments','','docs/report.pdf | 1-5')}</div><p class="section-subtitle">图像格式PNG/JPEG/GIF/WebP。已知模型不支持会在保存/发送前拒绝。Qwen使用原生读取工具并占用工具额度；Codex/DeepSeek使用原生图像输入。PDF页面视觉明确在本机渲染后送入当前模型图片通道，不直接上传PDF，不启用OCR或其他模型。每份最多10页、每任务最多16张页图，单页1600万/总3200万像素，派生PNG合计32MiB；超限不跳页或自动缩放。选择本机提取只读取文本，不包含OCR或外部服务；未知格式不自动转换。最多8份附件、每份8MiB、材料合计32MiB。</p><p class="section-subtitle">保存时制作准确快照并列出排除项；凭据和控制数据不作为材料。原生工具遵守任务路径范围；沙箱的实际限制另记，完成后检查副本差异。</p><details class="history-page"><summary>检查命令与执行权限</summary>${text('准确检查命令（JSON数组；{python}代表指定的本机Python）','checks','[]','[{"id":"tests","argv":["{python}","-B","-m","unittest","discover"],"approval":"each","seconds":30}]')}<label class="ctl-checkbox"><input type="checkbox" name="host_command_permission">明确允许任务内原生命令或这些准确检查命令以本机用户权限运行；原生沙箱策略与实测限制单列，准确材料并非OS读取隔离。每次审批用each，任务范围预批用task。</label></details><details class="history-page" open><summary>任务预算与范围</summary><div class="ctl-grid ctl-budget">${field('最多尝试','attempts','number',3,'min="1" max="10"')}${field('单次秒数','seconds','number',180,'min="20" max="180"')}${field('累计工具调用','tools','number',20,'min="1" max="60"')}${field('累计API模型HTTP请求（Codex按尝试/时限控制）','requests','number',8,'min="1" max="30"')}${field('累计API预算预留上限（元）','cny','number',1.5,'min="0" max="1000" step="0.1"')}</div><label class="ctl-field"><span>预算归属（旧批次已封闭，请选择独立任务）</span><select name="campaign"><option value="backend-rollout-20261017">历史后端验证（已封闭）</option><option value="independent" selected>独立普通任务（需Human单独确认本任务范围）</option></select></label></details><div class="ctl-actions"><button type="submit" class="button primary">保存任务草稿</button><a class="button secondary" href="#control">返回</a></div></form></div>`;}
  const modelFamilies={codex:'GPT',deepseek:'DeepSeek',qwen:'Qwen'};
  const modelLabel=(provider,model)=>[modelFamilies[provider]||provider,model||'型号未登记'].filter(Boolean).join(' · ');
  const memberModelLabel=(member,members=[])=>modelLabel(member.provider,member.model)+
    (members.filter(row=>row.provider===member.provider&&row.model===member.model).length>1?' · 配置 '+String(member.id||member.member_id).slice(-6):'');
  const options=(values,selected,label=value=>value)=>values.map(v=>`<option value="${h(v)}" ${v===selected?'selected':''}>${h(label(v))}</option>`).join('');
  const entryFor=(profile,model)=>profile.model_catalog?.models.find(m=>m.model===model);
  function priceText(entry){
    const p=entry?.price;if(!p)return '价格未知；未启用';
    if(p.kind==='SUBSCRIPTION')return `订阅参考。相对 Standard，Fast 使用包含额度的 ${p.included_usage_multipliers.fast} 倍、已购 Credits 的 ${p.purchased_credit_multipliers.fast} 倍。实际额度和账单以账号为准。`;
    if(p.currency==='USD')return `官方API标价（美元/百万Token）：高峰缓存输入 ${p.peak.cache_hit_input}、未缓存输入 ${p.peak.cache_miss_input}、输出 ${p.peak.output}；非高峰为一半。${p.schedule}。当前适用时段与实际账单未知。`;
    const a=p.beijing_and_global_regions,b=p.singapore_and_us_scope;
    return `官方按量参考价（元/百万Token）：北京及全球部署范围，输入 ${a.input}、输出 ${a.output}、隐式缓存输入 ${a.implicit_cache_input}；新加坡及美国部署范围，输入 ${b.input}、输出 ${b.output}、隐式缓存输入 ${b.implicit_cache_input}。当前Key对应地域、优惠和实际账单未知。`;
  }
  function ultrafastText(speed){
    if(!speed)return '';
    if(speed.status!=='DOCUMENTED_FOR_MODEL')return 'Ultrafast 的模型适用范围尚未登记。';
    const credits=speed.temporary_purchased_credit_multiplier
      ?`已购 Credits 在 ${speed.temporary_purchased_credit_through}（太平洋时间）及之前按 ${speed.temporary_purchased_credit_multiplier} 倍，此后按 ${speed.purchased_credit_multiplier} 倍`
      :`已购 Credits 按 ${speed.purchased_credit_multiplier} 倍`;
    return `Ultrafast 官方参考为包含额度的 ${speed.included_usage_multiplier} 倍，${credits}。账号与工作端的可用状态待核对。`;
  }
  function modelInfo(entry){if(!entry)return '<p>能力与价格未知。</p>';return `<p>上下文 ${h(entry.context_tokens??'未知')} Token；最大输出 ${h(entry.max_output_tokens??'未知')} Token。模型输入：${h(entry.input_modalities?.join(' / ')||'未知')}。Workdesk支持选定文本、明确的原生图像、本机PDF页面视觉及可选文档文字提取；可用路线依赖当前模型图像能力与本机运行时。PDF页面视觉为本机渲染PNG后送入所选模型，不是PDF直传或OCR；选页及大小上限见任务材料入口。音视频按需后续接入。</p><p>${h(priceText(entry))}</p><p>来源核对：${h(entry.checked||'未知')}；认证/计费渠道 ${h(entry.billing_channel||'既有Codex订阅')}；地域 ${h(entry.region||'未知')}。目录可见与真实生成验证分别记录；原生上下文与压缩由工作端管理。${entry.access_status?"目录不是本账号推理资格证明，成功回合另记。":""}${h(ultrafastText(entry.ultrafast))}${h(Object.values(entry.task_mode_restrictions||{}).join("；"))}</p><p>${(entry.sources||[]).map(url=>`<a href="${h(url)}" target="_blank" rel="noopener noreferrer">官方来源</a>`).join(' · ')}</p>`;}
  function profileOptions(){const form=$('#ctl-task-form');if(!form)return;const execution=form.elements.namedItem('execution'),provider=form.elements.namedItem('provider').value;for(const option of execution.options)option.disabled=option.value!=='scoped'&&option.value!==provider+'_native';if(execution.value!=='scoped')execution.value=provider+'_native';const p=view.catalog.profiles.find(p=>p.id===provider);$('#ctl-model').innerHTML='<option value="">请选择模型</option>'+options(p.enabled_models||p.models,p.model,model=>modelLabel(provider,model));$('#ctl-model').value=(p.enabled_models||p.models).includes(p.model)?p.model:'';modelOptions(p.effort,p.tier);}
  function modelOptions(effort,tier){const form=$('#ctl-task-form');if(!form)return;const p=view.catalog.profiles.find(p=>p.id===form.elements.namedItem('provider').value),m=$('#ctl-model').value,e=entryFor(p,m);$('#ctl-effort').innerHTML=options(e?.selection_efforts||e?.efforts||(p.id==='codex'?(p.reasoning_by_model?.[m]||['low','medium','high','xhigh']):['off']),effort);$('#ctl-tier').innerHTML=options(e?.tiers||(p.id==='codex'?['fast','standard']:['standard']),tier);}
  function quotaObservations(q){
    return (q.official_observations||[]).map(o=>{
      const last=o.last_success,bound=o.binding_status==='MATCH'&&o.last_success_connection_revision===o.connection_revision;
      const state=o.binding_status!=='MATCH'?'连接已变化，历史观察不适用于当前引用':o.status==='FAILED'?'最近刷新失败；当前额度未知':'官方额度时间快照';
      return `<br><strong>${h(state)}</strong>${last?` · ${h(date(last.observed_at))}${!bound?' · 原连接历史记录':''}${o.status==='FAILED'?' · 上次成功值':''}${last.buckets.map(b=>`<br>${h(b.name||b.limit_id)}${[['primary','窗口一'],['secondary','窗口二']].map(([key,label])=>{const w=b[key];return ` · ${label}：${w?.remaining_percent==null?'未知':h(w.remaining_percent)+'%'}${w?.duration_minutes==null?'':' / '+h(w.duration_minutes)+'分钟'}${w?.resets_at==null?'':'；重置 '+h(date(w.resets_at))}`;}).join('')}${b.credits?` · 账户credit：${b.credits.balance==null?'未知':h(b.credits.balance)}${b.credits.unlimited?'（官方标记unlimited）':''}`:''}`).join('')||'<br>官方未返回额度窗口'}`:''}`;
    }).join('');
  }
  function resourcePanel(){
    const r=view.catalog.execution_resources;if(!r)return '';
    return `<section class="provider-detail" id="ctl-resources"><h2>成员、连接与额度来源</h2><p>成员保存模型配置和预职责。可登记已有本地认证引用。新执行冻结所选连接；续聊和原生控制沿用原连接，账号在执行时核对。</p><div class="ctl-actions">${button('新增成员','resource-member')}${button('新增额度来源','resource-quota')}${button('登记已有连接','resource-register')}${button('比较成员成本与耗时','resource-planning')}</div><h3>成员</h3>${r.members.map(m=>`<div class="provider-detail-top" data-resource-member="${h(m.id)}"><div><p><strong>${h(memberModelLabel(m,r.members))}</strong><br>思考 ${h(m.effort)} · 速度 ${h(m.tier)} · ${m.available?'可选择':'不可用'} · 修订 ${m.revision}</p><details><summary>配置身份与预职责</summary><p>配置名称：${h(m.name)}<br>预职责：${h(m.role)}<br>配置标识：${h(m.id)}</p></details></div>${button('编辑成员','resource-member',`data-id="${h(m.id)}"`)}</div>`).join('')||'<p>暂无成员。新增后可在任务页准备独立 Work。</p>'}<h3>连接</h3>${r.connections.map(c=>`<div class="provider-detail-top ctl-actions"><p><strong>${h(c.name)}</strong> · ${h(c.provider)}${c.is_default?' · 新工作默认':''} · ${c.available?'本地引用就绪':'不可用'}<br>认证有效性待执行核对 · 修订 ${h(c.revision.slice(0,12))}<br>额度来源：${h(r.quota_sources.find(q=>q.id===c.quota_id)?.name||'未知')}</p>${button('编辑连接','resource-connection',`data-id="${h(c.id)}"`)}${c.provider==='codex'&&c.available?button('刷新官方额度','resource-quota-check',`data-id="${h(c.id)}"`):''}</div>`).join('')}<h3>额度来源（共享来源只列一次）</h3>${r.quota_sources.map(q=>`<div class="provider-detail-top"><p><strong>${h(q.name)}</strong> · ${h(({cash:'现金',subscription:'订阅',local:'本地资源'})[q.kind])}<br>剩余：${q.remaining===null?'未知':h(q.remaining)+' '+h(q.unit)} · ${q.source==='MANUAL'?'手工记录':'尚无余额记录'}${q.observed_at?' · '+h(date(q.observed_at)):''}${quotaObservations(q)}</p>${q.revision?button('编辑额度来源','resource-quota',`data-id="${h(q.id)}"`):''}</div>`).join('')}<p class="section-subtitle">手工记录和官方额度快照用于查看，不是硬预算保证。官方额度需显式刷新，失败或连接变化时保留带时间的历史记录；执行用量和实际账单另记。多个连接可引用同一额度来源，成员不复制余额。</p></section>`;
  }
  function resourceEdit(action,id){
    const r=view.catalog.execution_resources,d=dialog();let row;
    const select=(label,name,values,value)=>`<label class="ctl-field"><span>${label}</span><select name="${name}">${values.map(([k,n])=>`<option value="${h(k)}" ${k===value?'selected':''}>${h(n)}</option>`).join('')}</select></label>`;
    const footer=`<div class="dialog-actions">${button('取消','close-dialog')}${button('保存资源','commit-dialog','',true)}</div>`;
    if(action==='resource-planning'){
      d.innerHTML=`<h2>成员成本与耗时</h2>${field('预计输入 token','estimate_input','number',1000,'min="0" max="100000000"')}${field('预计输出 token','estimate_output','number',1000,'min="0" max="100000000"')}${field('参考时长（秒，可留空）','estimate_deadline','number','','min="1"')}<p>使用已登记价格版本及近期完整回合的耗时样本。金额按币种分别比较，执行以实际用量为准。</p><div class="dialog-actions">${button('关闭','close-dialog')}${button('比较','estimate-members','',true)}</div><div data-resource-estimates aria-live="polite"></div>`;
      d.onchange=null;
      d.querySelector('[data-ctl="estimate-members"]').onclick=async event=>{
        event.stopPropagation();
        const button=event.currentTarget;button.disabled=true;
        try{
          const input=Number(d.querySelector('[name=estimate_input]').value),output=Number(d.querySelector('[name=estimate_output]').value),deadline=d.querySelector('[name=estimate_deadline]').value;
          const data=await command('resource.planning-preview',{input_tokens:input,output_tokens:output,deadline_seconds:deadline===''?null:Number(deadline)});
          d.querySelector('[data-resource-estimates]').innerHTML=data.members.map(m=>{
            const cost=m.cost.status==='REFERENCE_RANGE'?`${h(m.cost.minimum)}–${h(m.cost.maximum)} ${h(m.cost.currency)}`:m.cost.status==='SUBSCRIPTION'?'订阅额度':'费用未知';
            const latency=m.latency.median_seconds==null?'暂无耗时样本':`${h(m.latency.median_seconds)} 秒中位数（${h(m.latency.sample_count)} 个历史回合）`;
            const relation={ABOVE_OBSERVED_MEDIAN:'高于参考时长',WITHIN_OBSERVED_MEDIAN:'历史中位数在参考时长内'}[m.deadline_relation]||'';
            const member={...r.members.find(row=>row.id===m.member_id),...m};return `<p><strong>${h(memberModelLabel(member,r.members))}</strong> · ${m.available?'可选择':'不可用'}<br>${cost}${m.cost.checked?` · 价格记录 ${h(m.cost.checked)}`:''}<br>${latency}${relation?' · '+h(relation):''}</p>`;
          }).join('')||'<p>请先登记成员。</p>';
        }catch(error){fail(error);}finally{button.disabled=false;}
      };
    }else if(action==='resource-register'){
      d.innerHTML=`<h2>登记已有连接</h2>${field('连接名称','resource_name','text','')}${select('工作端','resource_provider',view.catalog.profiles.map(p=>[p.id,p.name]),'codex')}${field('已有本地认证引用（绝对路径）','resource_reference','text','')}<p>Codex选择受管的UUID认证目录；API选择已有凭据文件。只登记引用和版本，不在这里输入密钥或登录。显式连接用于官方独立Work；历史有限任务仍使用原确认流程。</p>${footer}`;
      d.onchange=null;d.effect=async()=>{const val=n=>d.querySelector(`[name=resource_${n}]`).value;await command('resource.connection-register',{name:val('name'),provider:val('provider'),reference:val('reference')});await render('profiles');};
    }else if(action==='resource-member'){
      row=r.members.find(m=>m.id===id);const provider=row?.provider||'codex';
      d.innerHTML=`<h2>${row?'编辑成员':'新增成员'}</h2>${field('成员名称','resource_name','text',row?.name||'')}${field('预职责','resource_role','text',row?.role||'执行')}${select('工作端','resource_provider',view.catalog.profiles.map(p=>[p.id,p.name]),provider)}${select('模型','resource_model',[],null)}${select('思考','resource_effort',[],null)}${select('速度','resource_tier',[],null)}${select('允许连接','resource_connection',[],null)}${select('状态','resource_enabled',[['yes','启用'],['no','停用']],row?.enabled===false?'no':'yes')}${footer}`;
      const fill=(name,values,value)=>{d.querySelector(`[name=${name}]`).innerHTML=values.map(v=>`<option value="${h(v)}" ${v===value?'selected':''}>${h(name==='resource_model'?modelLabel(d.querySelector('[name=resource_provider]').value,v):v)}</option>`).join('');};
      const modes=()=>{const p=view.catalog.profiles.find(p=>p.id===d.querySelector('[name=resource_provider]').value),e=entryFor(p,d.querySelector('[name=resource_model]').value);fill('resource_effort',e?.selection_efforts||e?.efforts||[],row?.effort||p.effort);fill('resource_tier',e?.tiers||[],row?.tier||p.tier);};
      const providerChanged=()=>{const p=view.catalog.profiles.find(p=>p.id===d.querySelector('[name=resource_provider]').value);fill('resource_model',p.enabled_models||[],row?.provider===p.id?row.model:p.model);const cs=r.connections.filter(c=>c.provider===p.id);d.querySelector('[name=resource_connection]').innerHTML=cs.map(c=>`<option value="${h(c.id)}" ${row?.connection_id===c.id?'selected':''}>${h(c.name)}</option>`).join('');modes();};
      providerChanged();d.onchange=e=>{if(e.target.name==='resource_provider')providerChanged();if(e.target.name==='resource_model')modes();};
      d.effect=async()=>{const val=n=>d.querySelector(`[name=resource_${n}]`).value;await command('resource.member-save',{...(row?{id:row.id,revision:row.revision}:{}),name:val('name'),role:val('role'),provider:val('provider'),model:val('model'),effort:val('effort'),tier:val('tier'),connection_id:val('connection'),enabled:val('enabled')==='yes'});await render('profiles');};
    }else if(action==='resource-quota'){
      row=r.quota_sources.find(q=>q.id===id);
      d.innerHTML=`<h2>${row?'编辑':'新增'}额度来源</h2>${field('来源名称','resource_name','text',row?.name||'')}${select('资源类型','resource_kind',[['cash','现金'],['subscription','订阅'],['local','本地资源']],row?.kind||'cash')}${field('剩余数量（留空为未知）','resource_remaining','number',row?.remaining??'','min="0" step="any"')}${field('单位','resource_unit','text',row?.unit||'CNY')}<p>保存为带时间的手工记录，供连接共同引用。</p>${footer}`;
      d.onchange=null;d.effect=async()=>{const val=n=>d.querySelector(`[name=resource_${n}]`).value;await command('resource.quota-save',{...(row?{id:row.id,revision:row.revision}:{}),name:val('name'),kind:val('kind'),remaining:val('remaining')===''?null:Number(val('remaining')),unit:val('unit')});await render('profiles');};
    }else{
      row=r.connections.find(c=>c.id===id);d.onchange=null;
      d.innerHTML=`<h2>编辑连接</h2>${field('连接名称','resource_name','text',row.name)}${select('额度来源','resource_quota',r.quota_sources.filter(q=>q.revision||q.id===row.quota_id).map(q=>[q.id,q.name]),row.quota_id)}${select('状态','resource_enabled',[['yes','启用'],['no','停用']],row.enabled?'yes':'no')}<p>默认引用由存储与恢复页管理。停用后阻止该连接的新执行与续聊，已运行回合仍可停止。已有执行继续绑定原引用。</p>${footer}`;
      d.effect=async()=>{await command('resource.connection-save',{id:row.id,revision:row.revision,name:d.querySelector('[name=resource_name]').value,quota_id:d.querySelector('[name=resource_quota]').value,enabled:d.querySelector('[name=resource_enabled]').value==='yes'});await render('profiles');};
    }
    d.showModal();
  }
  function profiles(){return `<div class="page"><a class="detail-back" href="#settings/accounts">${icon('back')}账号与模型</a>${heading('工作端配置与模型目录','显式刷新仅查询官方元数据，不发送目标或项目材料；失败保留旧版本和选择。',button('刷新本地显示','refresh'),'BACKEND / PROFILES')}${resourcePanel()}<div class="callout">默认选择仅影响之后的新草稿。在途、已确认和历史任务保留各自的目录快照；更换模型需在停止核对后选择原会话的下次尝试或当前文件的新上下文，累计预算保留，不自动派发。官方标价、预算预留、用量和实际账单分别记录。价格参考版随已核对的目录发布更新；在线刷新不推断新价格。</div>${view.catalog.profiles.map(p=>{const entries=p.model_catalog?.models||[],enabled=entries.filter(m=>m.runnable),
    unavailable=entries.filter(m=>m.access_status==='NOT_IN_CURRENT_ACCOUNT_CATALOG'),
    unknown=entries.filter(m=>!m.runnable&&m.access_status!=='NOT_IN_CURRENT_ACCOUNT_CATALOG');return `<div class="provider-detail"><div class="provider-detail-top"><div><h2>${h(p.name)}</h2><p>${h(p.harness)}</p></div><div class="ctl-actions">${button('检查认证与刷新官方目录','profile-check',`data-provider="${p.id}"`)}${button('设置新任务默认模型','select-default',`data-provider="${p.id}"`)}</div></div><p>当前默认：${h(modelLabel(p.id,p.model))} / ${h(p.effort)} / ${h(p.tier)}。认证：${h(p.auth_status||'未检查')}；最近检查：${p.auth_checked_at?h(date(p.auth_checked_at)):'无本批次记录'}；连接引用见上方；认证有效性、官方剩余额度与实际账单以工作端为准。</p><p>目录版本 ${h(p.model_catalog?.revision?.slice(0,12)||'旧目录')} · 价格参考版 ${h(p.model_catalog?.edition||'未知')}。恢复：${h(p.resume)}；停止：${h(p.interrupt)}。</p>${enabled.map(e=>`<details class="history-page"><summary>${h(modelLabel(p.id,e.model))} · 思考 ${h(e.efforts.join(' / '))} · 速度 ${h(e.tiers.join(' / '))}</summary>${modelInfo(e)}</details>`).join('')}${unavailable.map(e=>`<details class="history-page"><summary>${h(modelLabel(p.id,e.model))} · 账号目录未列出</summary><p>${h(e.unavailable_reason)}</p>${modelInfo(e)}</details>`).join('')}<details class="history-page"><summary>其余 ${unknown.length} 个已发现模型：Workdesk能力/协议/费用尚未登记</summary><p>不据此判定供应商不支持。可在后续目录版本中接通；当前不静默替换为其他模型。</p><pre class="raw-page">${h(unknown.map(e=>e.model).join('\n'))}</pre></details><p class="section-subtitle">${h(p.evidence_source||p.evidence)}；压缩和输出参数随原生工作端，Workdesk不另设统一Token上限。</p></div>`;}).join('')}</div>`;}
  function choiceModes(){const d=dialog(),p=view.catalog.profiles.find(p=>p.id===d.provider),e=entryFor(p,$('#ctl-choice-model').value);$('#ctl-choice-effort').innerHTML=options(e?.selection_efforts||e?.efforts||[],d.selection?.effort);$('#ctl-choice-tier').innerHTML=options(e?.tiers||[],d.selection?.tier);$('#ctl-choice-info').innerHTML=modelInfo(e);}
  async function chooseModel(provider,task){await catalog();const p=view.catalog.profiles.find(p=>p.id===provider),d=dialog();d.provider=provider;d.selection=task?.spec||p;const selected=d.selection.model;d.innerHTML=`<h2>${task?'切换模型与模式':'设置新任务默认模型'}</h2><p>${task?'旧任务必须已停止并核对。支持的路径可在下次尝试恢复原会话，或明确从当前文件建立新上下文。每次尝试的配置与累计消耗保留；新配置先重新确认范围，再显式开始。':'仅影响之后的新草稿，不修改已有任务、不切换账号、不自动执行。'}</p>${task?`<label class="ctl-field"><span>接续方式</span><select id="ctl-choice-continuation">${['codex','qwen'].includes(provider)&&task.spec.execution===provider+'_native'&&view.detail?.attempts.at(-1)?.model_configuration?.status==='FROZEN'?'<option value="native">原会话：下次尝试生效</option>':''}<option value="context">当前文件：新上下文草稿</option></select></label>`:''}<label class="ctl-field"><span>模型</span><select id="ctl-choice-model"><option value="">请选择模型</option>${options(p.enabled_models||p.models,selected,model=>modelLabel(provider,model))}</select></label><label class="ctl-field"><span>思考</span><select id="ctl-choice-effort"></select></label><label class="ctl-field"><span>速度</span><select id="ctl-choice-tier"></select></label><div id="ctl-choice-info"></div><div class="dialog-actions">${button('取消','close-dialog')}${button('保存选择','commit-dialog','',true)}</div>`;choiceModes();d.effect=async()=>{const selection={model:$('#ctl-choice-model').value,effort:$('#ctl-choice-effort').value,tier:$('#ctl-choice-tier').value};if(!selection.model||!selection.effort||!selection.tier)throw Error('请选择已接通的准确配置。');const receipt=await command(task?'task.select-model':'profile.select',task?{task_id:task.id,spec_hash:task.spec_hash,continuation:$('#ctl-choice-continuation').value,...selection}:{provider,...selection});if(task){const target='#control/task/'+receipt.task_id;if(location.hash===target)await loadTask(receipt.task_id,true);else location.hash=target;}else await render('profiles');toast('选择已保存，未派发模型。');};d.showModal();}
  function nativeRequests(detail,compact=false){
    const attempt=detail.attempts.at(-1);
    return (detail.native_requests||[]).filter(r=>r.state==='PENDING'&&(!compact||r.attempt_id===attempt?.id)).map(r=>{
      const input=r.method.includes('UserInput'), params=r.params||{};
      const fields=`data-id="${h(r.id)}"`;
      const raw=`<pre class="raw-page">${h(JSON.stringify({method:r.method,params:r.params},null,2))}</pre>`;
      const prompt=input?(Array.isArray(params.questions)?params.questions.map(q=>q.question).filter(Boolean).join('\n'):''):
        (typeof params.command==='string'?params.command:typeof params.reason==='string'?params.reason:'');
      return `<section class="warning-banner"><strong>等待原生${input?'补充输入':'审批'}</strong>
        ${compact?`<p class="ctl-embedded-request">${h(prompt||'请核对本次请求。')}</p>` :raw}
        <div class="ctl-actions">${input?button('填写回答','native-input',fields,true):`
          ${!r.method.includes('elicitation')?button('批准本次','native-decision',fields+' data-decision="accept"',true):''}
          ${(!params.availableDecisions||params.availableDecisions.includes('decline'))?button('拒绝本次','native-decision',fields+' data-decision="decline"'):''}
          ${button('取消','native-decision',fields+' data-decision="cancel"')}`}</div>
        ${compact?`<details class="ctl-embedded-evidence" data-ctl-open="request:${h(r.id)}"><summary>查看请求原文</summary>${raw}</details>`:
          '<p>关闭界面、刷新、沉默及到期均不批准；答复与原生接收回执分别保存。</p>'}
      </section>`;
    }).join('');
  }
  function approvalList(detail){return detail.approvals.filter(a=>a.state==='PENDING').map(a=>`<section class="warning-banner"><strong>等待Human审批检查命令</strong><pre class="raw-page">${h(a.description)}</pre><div class="ctl-actions">${button('批准本次','allow',`data-id="${a.id}"`,true)}${button('拒绝本次','deny',`data-id="${a.id}"`)}</div><p>关闭界面、沉默或到期均不视为批准。</p></section>`).join('');}
  function taskActions(detail){const task=detail.task,attempt=detail.attempts.at(-1),needsGrant=!task.grant||task.grant.expires_at<=Date.now()/1000,canConfirm=!active.has(task.state)&&!task.state.includes('UNKNOWN')&&!['CANCELLED','HANDED_OFF'].includes(task.state);return `<div class="ctl-actions">${canConfirm&&needsGrant?button(task.state==='DRAFT'?'确认材料与授权范围':'重新确认材料与授权范围','approve','',true):''}${!needsGrant&&task.state==='APPROVED'&&!attempt?button('开始执行','start','',true):''}${attempt&&active.has(attempt.state)?button('请求停止','stop')+button('暂停当前任务','pause'):''}${!needsGrant&&!attempt&&task.state==='PAUSED'?button('解除暂停','unpause'):''}${button('核对状态','reconcile')}${canConfirm&&!needsGrant&&attempt&&!active.has(attempt.state)?(task.spec.provider==='deepseek'?'':button('原会话恢复','resume-native'))+button('新会话接续','resume-context'):''}${canConfirm&&!attempt&&task.spec.execution?.endsWith('_native')?button(task.spec.work_mode==='cooperative'?'切换为独立工作':'切换为合作工作','task-select-work-mode'):''}${canConfirm?(!attempt?button('更换模型与模式','task-select-model'):'')+button('修订为新草稿','revise'):''}${!active.has(task.state)?button('取消任务','cancel'):''}${button('刷新','refresh')}${view.pending?button('查命令回执','receipt'):''}<a class="button secondary" href="#control">任务列表</a></div>`;}
  function progress(detail){const attempt=detail.attempts.at(-1),result=attempt?.result;let output=result?.answer||'';if(typeof output!=='string')output=JSON.stringify(output,null,2);if(!output){output=view.events.filter(e=>e.kind==='model_delta'&&e.attempt_id===attempt?.id).map(e=>e.payload.text||'').join('');}return `${attempt?.model_configuration?.status==='FROZEN'?`<p class="section-subtitle">当前展示的尝试：${h(attempt.model_configuration.model)} / ${h(attempt.model_configuration.effort)} / ${h(attempt.model_configuration.tier)}；规格 ${h(attempt.model_configuration.spec_hash.slice(0,12))}。任务上方的选择可已为下次尝试更新。</p>`:''}${attempt?.served_model_observations?.length?`<p class="callout">本次API返回模型标识：${h([...new Set(attempt.served_model_observations.flatMap(o=>o.served_model_labels))].join(" / ")||"未知")}。配置核对：${h(attempt.served_model_observations.map(o=>o.label_match).join(" / "))}。返回标识不证明精确权重版本；不匹配时停止后续请求。</p>`:''}${result?`<div class="callout">模型正文中的记忆与成功声明不作为后台证据。接续方式：${h(result.continuation_mode||'首次执行')}。检查状态：${h(result.validation?.status||'请核对已保存检查事件')}；验收未完成。协议/回合状态：${h(result.protocol||result.protocol_result?.finish_reason||result.protocol_result?.subtype||'未知')}；已知工具退出：${result.known_tools_exited?'有保存证据':'未知'}；远端请求状态：${h(result.remote_model_state||'未知')}。审查与Human验收未据此完成。</div>`:''}<div class="source-block"><h3>${result?'保存的最终回复':'模型输出与执行过程'}</h3><div class="message-content markdown">${output?markdown(output):'等待可展示的模型正文；下方事件保留工作端通知与工具过程。'}</div></div>${result?.errors?.length?rawBlock('运行错误与保留项',JSON.stringify(result.errors,null,2),true):''}<p class="section-subtitle">已读取 ${view.events.length} 条事件；按游标补读，不重发任务。后台连接未知时保留未知。</p><div class="trace">${view.events.slice(-120).map(e=>`<details class="trace-item ctl-event" data-seq="${e.seq}"><summary><span>${h(e.kind)}</span><time>${h(date(e.at))}</time></summary><pre class="raw-page">${h(JSON.stringify(e.payload,null,2))}</pre></details>`).join('')}</div>`;}
  function specification(detail){const task=detail.task;return `<div class="source-block"><h3>目标与准确规格</h3><pre class="raw-page">${h(JSON.stringify(task.spec,null,2))}</pre></div><div class="source-block"><h3>原始材料快照</h3>${Object.keys(task.spec.materials).map(file=>button(h(file),'material',`data-file="${h(file)}"`)).join('')||'无文件材料'}</div>${rawBlock('Human授权记录',JSON.stringify(task.grant,null,2),true)}`;}
  function artifacts(detail){return detail.attempts.map(attempt=>{const result=attempt.result;return `<div class="source-block"><h3>尝试 ${h(attempt.id.slice(0,8))} · ${h(labels[attempt.state]||attempt.state)}</h3>${result?`${Object.keys(result.manifest||{}).map(file=>button(h(file),'artifact',`data-attempt="${attempt.id}" data-file="${h(file)}"`)).join('')}${(result.diffs||[]).map(d=>rawBlock(d.file+' · 修改差异',d.diff,true)).join('')}`:'尚无已保存产物'}</div>`;}).join('')||empty('尚无尝试。');}
  function taskBody(detail){return view.tab==='scope'?specification(detail):view.tab==='artifacts'?artifacts(detail):view.tab==='technical'?rawBlock('任务、尝试、预算与运行观测',JSON.stringify(detail,null,2),true):progress(detail);}
  function officialPage(detail){const task=detail.task,attempt=detail.attempts.at(-1),co=task.spec.cooperative;return `<div class="page" ${co?'data-cooperative-origin="'+h(co.item_id)+'"':''}><a class="detail-back" href="${co?'#work-items/item/'+h(co.item_id):'#work/task/'+task.id}">${co?'返回合作业务任务':'返回独立Work'}</a>${heading(h(task.title),h(task.spec.provider)+' / '+h(task.spec.model),'',co?'COOPERATIVE UNIT':'INDEPENDENT WORK')}${status(task)}<p>${co?'合作工作单元使用所选成员的官方工作端与审批。累计尝试和新增派发截止见业务任务授权；输入及依赖版本已冻结。':'使用所选官方工作端配置与审批；Workdesk不追加任务预算、路径白名单或建项确认。'}在项目目录中运行，目录不等于执行沙箱。</p><div class="ctl-actions">${attempt&&active.has(attempt.state)?button('请求停止','stop'):''}${button('刷新','refresh')}</div>${nativeRequests(detail)}${progress(detail)}${rawBlock('运行记录',JSON.stringify(detail,null,2),true)}</div>`;}
  function taskPage(detail){if(detail.task.spec.runtime_mode==='official')return officialPage(detail);const task=detail.task,attempt=detail.attempts.at(-1);return `<div class="page"><a class="detail-back" href="#control">${icon('back')}任务执行</a>${heading(h(task.title),`${h(task.spec.provider)} / ${h(task.spec.model)} · ${h(task.id)}`,'','BACKEND / TASK')}${status(task)}<p>工作方式：${task.spec.work_mode==='cooperative'?'合作任务':'独立工作'}${detail.chain_usage?'；本任务沿用累计预算链，切换不会清零':''}。执行路径：${h(task.spec.execution||'scoped')}。${task.spec.controller_checks?'原生回合后另运行已批准的准确检查，计入同一预算。':''}</p>${task.spec.execution?.endsWith('_native')?'<div class="warning-banner">官方原生独立运行 · 本机权限命令。当前Windows探测未提供OS读写隔离；授权路径由任务规则及副本差异检查约束。原生上下文/压缩保留，Web、委派和新增外部服务未获本任务授权。</div>':''}<div class="section-space">${taskActions(detail)}</div>${task.spec.model_selection?`<details class="history-page"><summary>本任务冻结配置：${h(task.spec.model)} / ${h(task.spec.effort)} / ${h(task.spec.tier)} · 目录 ${h(task.spec.model_selection.catalog_revision.slice(0,12))}</summary>${modelInfo(task.spec.model_selection.entry)}<p>目录刷新不改写本任务。实际账单未知，费用预留只用于预算控制。</p></details>`:''}${task.spec.selection_from?`<p class="callout">从 <a href="#control/task/${h(task.spec.selection_from.task_id)}">旧任务</a> 的当前文件建立新上下文；累计消耗继承，旧模型会话历史未恢复。</p>`:''}${approvalList(detail)}${nativeRequests(detail)}${attachmentList(detail)}<div class="callout muted-callout">${h(task.spec.provider)} 当前连接：未知。${attempt?`控制器观测：${h(detail.runtime_observation.at(-1)?.controller_worker||'未知')}；停止请求：${attempt.stop_requested?'已登记，不等于已停止':'未登记'}。`:''}累计工具 ${detail.usage.tool_calls??'未知'} / ${task.spec.budget.tools}；累计尝试 ${detail.usage.attempts} / ${task.spec.budget.attempts}，API请求 ${detail.usage.model_requests} / ${task.spec.budget.requests}，费用预留 ¥${detail.usage.reserved_cny.toFixed(6)} / ${task.spec.budget.cny}。</div><div class="detail-tabs">${[['progress','过程与结果'],['scope','材料与授权'],['artifacts','产物与差异'],['technical','技术详情']].map(([key,label])=>`<button class="${view.tab===key?'active':''}" data-ctl-tab="${key}">${label}</button>`).join('')}</div><div id="ctl-task-body">${taskBody(detail)}</div><div class="ctl-actions">${button('复制任务身份','copy-task')}<a class="button secondary" href="#collab/task/${task.id}">成果、检查与独立审查</a></div></div>`;}
  function embeddedPage(detail){
    const task=detail.task,attempt=detail.attempts.at(-1);
    const state=attempt?.state||task.state;
    const statusText=({RUNNING:'运行中',COMPLETED:'本回合已结束',INTERRUPTED:'已中断',WAITING_INPUT:'等待补充输入'})[state]||labels[state]||state;
    const reconnect=attempt&&(state.includes('UNKNOWN')||attempt.result?.remote_model_state==='UNKNOWN');
    const config=attempt?.model_configuration;
    const workEnd=[config?.provider||task.spec.provider,config?.model||task.spec.model].filter(Boolean).join(' · ');
    const legacy=task.spec.runtime_mode!=='official';
    const waiting=(legacy?approvalList(detail):'')+nativeRequests(detail,true);
    return `<section class="ctl-embedded" aria-label="运行控制">
      <div class="ctl-embedded-summary"><div><h2>${attempt?'最近运行':'当前状态'}</h2>
        <p><strong>${h(statusText)}</strong>${workEnd?` · ${h(workEnd)}`:''}</p>
        ${attempt?.started?`<p class="ctl-embedded-time">开始于 ${h(date(attempt.started))}</p>`:''}</div>
        <div class="ctl-actions">
          ${active.has(state)&&attempt?button('请求停止','stop'):''}
          ${reconnect?button('核对状态','reconcile'):''}
          ${button('刷新','refresh')}${view.pending?button('查命令回执','receipt'):''}
        </div>
      </div>
      ${waiting?`<section class="ctl-embedded-waiting" aria-label="需要处理的请求">${waiting}</section>`:''}
      ${attempt?`<details class="ctl-embedded-evidence" data-ctl-open="progress"><summary>回合输出与运行证据</summary>${progress(detail)}</details>`:''}
      <details class="ctl-embedded-evidence" data-ctl-open="technical"><summary>技术详情</summary>
        ${attempt&&!reconnect?`<div class="ctl-actions">${button('核对状态','reconcile')}</div>`:''}
        ${legacy?`<a href="#control/task/${encodeURIComponent(task.id)}">查看原运行控制</a>`:''}
        <pre class="raw-page">${h(JSON.stringify(detail,null,2))}</pre>
      </details>
    </section>`;
  }
  function taskRoot(id){
    if(embedded)return embedded.id===id&&embeddedActive()?embedded.host:null;
    const [page,section,identity]=location.hash.slice(1).split('/');
    return page==='control'&&section==='task'&&identity===id&&controlActive()?$('#main'):null;
  }
  async function loadTask(id,initial=true){
    const origin=context(),read=++view.taskRead;
    const data=await call('task?id='+encodeURIComponent(id)+'&after='+(initial?0:view.cursor));
    const root=taskRoot(id);
    if(!root||!current(origin)||read!==view.taskRead)return;
    if(data.task?.id!==id||embedded&&(data.task.spec.cooperative||data.task.spec.work_mode==='cooperative'||data.task.spec.interaction==='chat'))
      throw Error('找不到对应的独立工作记录。');
    view.detail=data;
    view.events=initial?data.events:[...view.events,...data.events].slice(-500);
    view.cursor=data.cursor;
    const disclosure='details[data-ctl-open],.ctl-event';
    const openKeys=new Set([...root.querySelectorAll(disclosure)].filter(el=>el.open).map(el=>el.dataset.ctlOpen||el.dataset.seq));
    const scroll=root.scrollTop;
    root.innerHTML=embedded?embeddedPage(data):taskPage(data);
    for(const el of root.querySelectorAll(disclosure)){
      if(openKeys.has(el.dataset.ctlOpen||el.dataset.seq))el.open=true;
    }
    root.scrollTop=scroll;
    if(data.has_more_events||active.has(data.attempts.at(-1)?.state)){
      view.timer=setTimeout(()=>loadTask(id,false).catch(error=>{if(current(origin))fail(error);}),1200);
    }
  }
  async function mountTask(host,id){
    leave();
    if(!host.isConnected)return;
    embedded={host,id,hash:location.hash};
    view.detail=null;view.pending=null;view.tab='progress';
    host.innerHTML='<p class="ctl-embedded-loading" role="status">正在读取运行状态…</p>';
    const origin=context();
    try{await loadTask(id);}
    catch(error){
      if(current(origin))host.innerHTML=`<section class="ctl-embedded-summary" role="alert">
        <p>${h(error.message)}</p>${button('重试','refresh')}</section>`;
    }
  }
  function receiptBlock(command){
    const receipt=command.receipt||{};
    const state=receipt.state||receipt.status||receipt.task?.state;
    const outcome=typeof state==='string' ? WorkdeskSupport.state(state) : '已有回执';
    return rawBlock(command.kind+' · '+date(command.created)+' · '+outcome,JSON.stringify({id:command.id,receipt:command.receipt},null,2));
  }
  async function render(section='',id=''){leave();const seq=view.seq;try{if(section==='usage'){await renderUsage(id,seq);return;}if(section==='receipts'){const data=await call('receipts');if(seq!==view.seq)return;$('#main').innerHTML=`<div class="page"><a class="detail-back" href="#control">任务执行</a>${heading('操作回执','回执未知时先查已保存记录；刷新不重发命令。')}${data.commands.map(receiptBlock).join('')}</div>`;return;}if(section==='task'){view.tab='progress';await loadTask(id,true);return;}view.editSource=null;await catalog();if(section==='edit'){const detail=await call('task?id='+id);view.editSource=detail.task;$('#main').innerHTML=newTask();const form=$('#ctl-task-form'),task=detail.task,project=view.catalog.projects.find(p=>p.id===task.project_id);for(const [name,value] of Object.entries({root:project.root,title:task.title+' · 修订',goal:task.spec.goal,acceptance:task.spec.acceptance,provider:task.spec.provider,execution:task.spec.execution||'scoped',role:task.spec.role,files:Object.keys(task.spec.materials).join('\n'),visual_attachments:(task.spec.attachments||[]).filter(a=>a.route==='pdf_visual').map(a=>a.file+(a.pages?' | '+a.pages:'')).join('\n'),native_attachments:(task.spec.attachments||[]).filter(a=>a.route==='native').map(a=>a.file).join('\n'),extracted_attachments:(task.spec.attachments||[]).filter(a=>a.route==='extract_text').map(a=>a.file+(a.pages?' | '+a.pages:'')).join('\n'),writable:task.spec.writable.join('\n'),checks:JSON.stringify(task.spec.checks,null,2),alternatives:JSON.stringify(task.spec.alternatives||[],null,2),...task.spec.budget})){form.elements.namedItem(name).value=value;}form.elements.namedItem('root').readOnly=true;form.elements.namedItem('host_command_permission').checked=task.spec.host_command_permission;form.elements.namedItem('campaign').value=task.spec.campaign||'independent';profileOptions();form.elements.namedItem('model').value=task.spec.model;modelOptions(task.spec.effort,task.spec.tier);return;}$('#main').innerHTML=section==='new'?newTask():section==='profiles'?profiles():index();if(section==='new')profileOptions();}catch(error){if(seq===view.seq)$('#main').innerHTML=errorPage(error.message);}}
  async function refresh(origin){
    if(origin&&!current(origin))return;
    if(embedded){
      if(!embeddedActive())return;
      clearTimeout(view.timer);await loadTask(embedded.id,true);return;
    }
    if(!controlActive())return;
    const [,section,id]=location.hash.slice(1).split('/');
    if(section==='task'){clearTimeout(view.timer);await loadTask(id,true);}
    else await render(section,id);
  }
  function commandPayload(){const detail=view.detail,attempt=detail?.attempts.at(-1);return {task_id:detail.task.id,spec_hash:detail.task.spec_hash,...(attempt?{attempt_id:attempt.id,owner:attempt.owner}:{})};}
  async function operate(action,element,origin){
    if(action==='resource-quota-check'){const c=view.catalog.execution_resources.connections.find(c=>c.id===element.dataset.id);const result=await call('resource-quota-check',{connection_id:c.id,connection_revision:c.revision});toast(result.observation.status==='OBSERVED'?'官方额度已读取；这是带时间的快照。':'额度刷新失败，已保留历史观察。');await render('profiles');return;}
    if(action.startsWith('resource-')){resourceEdit(action,element.dataset.id);return;}
    if(action==='refresh'){await refresh(origin);return;}
    if(action==='usage-prev'||action==='usage-next'){
      if(view.usageTasks===null)return;
      view.usagePage+=action==='usage-next'?1:-1;
      paintUsageList();
      return;
    }
    if(action==='select-default'){await chooseModel(element.dataset.provider);return;}
    if(action==='task-select-work-mode'){const t=view.detail.task,to=t.spec.work_mode==='cooperative'?'independent':'cooperative';showConfirm('切换工作方式（保存新草稿，不派发）',JSON.stringify({source_task:t.id,from:t.spec.work_mode||'independent',to,materials:t.spec.materials,attachments:t.spec.attachments||[],budget:t.spec.budget,usage:view.detail.usage,note:'先停止并核对旧执行；保留累计预算链，旧归属撤销，新上下文须重新确认。不复制原生会话。加入方案须另行保存和启用。'},null,2),async()=>{const r=await command('task.select-work-mode',{task_id:t.id,spec_hash:t.spec_hash,work_mode:to});location.hash='#control/task/'+r.task_id;toast('工作方式已保存为新草稿，累计消耗保留，未发送模型。');});return;}
    if(action==='task-select-model'){await chooseModel(view.detail.task.spec.provider,view.detail.task);return;}
    if(action==='profile-check'){const result=await call('profile-check',{provider:element.dataset.provider});toast('检查完成：'+result.auth_status+'。未发送模型消息。');await render('profiles');return;}
    if(action==='receipt'){
      if(!view.pending){toast('没有待核对的命令身份。');return;}
      const receipt=await call('receipt?id='+view.pending.id);
      if(current(origin))showText('已保存命令回执',JSON.stringify(receipt,null,2));
      return;
    }
    if(action==='copy-task'){await copyText(view.detail.task.id);return;}
    if(action==='material'||action==='artifact'){const route=action==='material'?'material?id='+view.detail.task.id:'artifact?attempt='+element.dataset.attempt;const value=await call(route+'&file='+encodeURIComponent(element.dataset.file));showText(value.file,JSON.stringify(value,null,2));return;}
    if(action==='native-decision'||action==='native-input'){
      const row=view.detail.native_requests.find(r=>r.id===element.dataset.id);
      const payload=commandPayload();
      if(row?.state!=='PENDING'||row.attempt_id!==payload.attempt_id)throw Error('原生请求已失效，请刷新后核对。');
      const respond=async(response)=>{
        const reply=await command('native.resolve',{...payload,request_id:row.id,response,source:'HUMAN_UI'});
        if(reply.accepted===false)toast('请求已到期，未发送答复。');
        await refresh(origin);
      };
      if(action==='native-decision'){
        const decision=element.dataset.decision;
        const response=row.method.includes('permissions')?{allow:decision==='accept'}:
          row.method.includes('elicitation')?{action:decision==='cancel'?'cancel':'decline'}:{decision};
        showConfirm('答复这一次原生请求',JSON.stringify({method:row.method,params:row.params,response},null,2),()=>respond(response));
        return;
      }
      const questions=row.params.questions,d=dialog();
      d.innerHTML=`<h2>补充输入</h2>${questions.map((q,i)=>`<label class="ctl-field"><span>${h(q.question)}</span><p>${h((q.options||[]).map(o=>o.label+': '+o.description).join('；'))}</p><textarea data-native-answer="${i}" ${q.isSecret?'disabled':''}></textarea></label>`).join('')}
        <p>本次回答将提交给当前工作端。密码请用原生认证入口。</p>
        <div class="dialog-actions">${button('关闭','close-dialog')}${button('发送回答','commit-dialog','',true)}</div>`;
      d.effect=()=>respond({answers:Object.fromEntries(questions.map((q,i)=>[q.id,{answers:[d.querySelector(`[data-native-answer="${i}"]`).value]}]))});
      d.showModal();return;
    }
    if(action==='allow'||action==='deny'){await command('approval.resolve',{approval_id:element.dataset.id,allow:action==='allow'});await refresh(origin);return;}
    const payload=commandPayload();let kind;
    if(action==='approve'){
      const task=view.detail.task;
      showConfirm('确认本任务范围',JSON.stringify({recipient:task.spec.provider,model:task.spec.model,effort:task.spec.effort,tier:task.spec.tier,model_selection:task.spec.model_selection,execution:task.spec.execution,native_policy:task.spec.native,acceptance:task.spec.acceptance,campaign:task.spec.campaign,goal:task.spec.goal,materials:task.spec.materials,attachments:task.spec.attachments||[],writable:task.spec.writable,checks:task.spec.checks,budget:task.spec.budget,host_command_permission:task.spec.host_command_permission,usage_already_consumed:view.detail.usage,spec_hash:task.spec_hash},null,2),async()=>{await command('task.approve',payload);await refresh();});return;
    }
    if(action==='start'){
      showConfirm('开始执行此任务',`将向 ${view.detail.task.spec.provider} / ${view.detail.task.spec.model} 发送已确认的目标和材料。\n预算：${JSON.stringify(view.detail.task.spec.budget)}\n不会自动扩额或换模型。`,async()=>{await command('task.start',payload);await refresh();});return;
    }
    if(action==='unpause'){await command('task.resume',payload);await refresh();return;}
    if(action==='revise'){location.hash='#control/edit/'+payload.task_id;return;}
    if(action==='cancel'){await command('task.cancel',payload);await refresh();return;}
    if(action==='resume-native'||action==='resume-context'){
      const task=view.detail.task,d=dialog(),unknown=view.detail.attempts.at(-1)?.state.includes('UNKNOWN')||view.detail.attempts.at(-1)?.result?.remote_model_state==='UNKNOWN';
      d.innerHTML=`<h2>${action==='resume-native'?'恢复原会话':'新会话接续'}</h2><p>使用原范围和累计预算；本地退出必须先核对。新会话不恢复旧模型历史，模型的记忆声明不作为证据。</p>${text('本次接续说明','continuation_note','按原范围接续，先核对现有副本和已保存的检查结果。')}<label class="ctl-field"><span>受限样板的本次工具（原生路径保留原任务权限；更改须修订）</span><select name="continuation_tools"><option value="all">原授权工具范围</option><option value="read">仅查看材料</option><option value="none">仅回复，不调用工具</option></select></label>${unknown?'<label class="ctl-checkbox"><input type="checkbox" name="ack_unknown">我知悉旧远端仍未知、费用预留保留；本次接续不重放旧请求。</label>':''}<div class="dialog-actions">${button('取消','close-dialog')}${button('确认接续','commit-dialog','',true)}</div>`;
      d.effect=async()=>{const note=d.querySelector('[name=continuation_note]').value.trim(),mode=d.querySelector('[name=continuation_tools]').value,ack=!!d.querySelector('[name=ack_unknown]')?.checked;if(!note)throw Error('请填写接续说明。');if(unknown&&!ack)throw Error('旧远端仍未知，需要明确确认该未知；未派发。');await command('task.resume',{...payload,mode:action==='resume-native'?'native':'context',note,ack_unknown_remote:ack,...(task.spec.execution?.endsWith('_native')?{}:mode==='none'?{allowed_tools:[]}:mode==='read'?{allowed_tools:['list_files','read_file','search_files']}:{})});await refresh();};d.showModal();return;
    }
    if(action==='stop'||action==='pause'){
      showConfirm('登记停止意图','请求停止不等于协议中断或工具退出。系统只处理此任务拥有的运行环境。',async()=>{await command(action==='stop'?'task.stop':'task.pause',payload);await refresh(origin);});return;
    }
    if(action==='reconcile'){
      const value=await command('task.reconcile',{...payload,probe_codex:view.detail.task.spec.provider==='codex'});
      if(!current(origin))return;
      showText('状态核对（不重发模型消息）',JSON.stringify(value,null,2));await refresh(origin);
    }
  }
  function dialog(){
    let d=$('#ctl-dialog');
    if(!d){d=document.createElement('dialog');d.id='ctl-dialog';d.className='ctl-dialog';document.body.append(d);}
    d.controlContext=context();return d;
  }
  function showText(title,body){const d=dialog();d.innerHTML=`<h2>${h(title)}</h2><pre class="raw-page">${h(body)}</pre><div class="dialog-actions">${button('关闭','close-dialog')}</div>`;d.showModal();}
  function showConfirm(title,body,effect){const d=dialog();d.innerHTML=`<h2>${h(title)}</h2><pre class="raw-page">${h(body)}</pre><div class="dialog-actions">${button('取消','close-dialog')}${button('确认','commit-dialog','',true)}</div>`;d.effect=effect;d.showModal();}
  document.addEventListener('click',async event=>{
    const element=event.target.closest('[data-ctl],[data-ctl-tab]');
    if(!element)return;
    const popup=element.closest('#ctl-dialog'),origin=popup?.controlContext||context();
    const surface=embedded?embedded.host:$('#main');
    const legacy=controlActive()&&!embedded&&!popup?.controlContext?.embedded;
    if(!popup&&!surface.contains(element)||!legacy&&!current(origin))return;
    event.preventDefault();
    if(element.dataset.ctlTab&&!embedded){view.tab=element.dataset.ctlTab;surface.innerHTML=taskPage(view.detail);return;}
    const action=element.dataset.ctl;
    if(popup&&action==='close-dialog'){popup.effect=null;popup.close();return;}
    if(popup&&action==='commit-dialog'){
      const effect=popup.effect;popup.effect=null;popup.close();
      if(effect)try{await effect();}catch(error){fail(error);}
      return;
    }
    const embeddedActions=['refresh','receipt','stop','reconcile','allow','deny','native-input','native-decision'];
    if(embedded&&!embeddedActions.includes(action))return;
    element.disabled=true;
    try{await operate(action,element,origin);}catch(error){if(current(origin))fail(error);}
    finally{if(element.isConnected)element.disabled=false;}
  });
  document.addEventListener('change',event=>{if(event.target.id==='ctl-provider')profileOptions();if(event.target.id==='ctl-model')modelOptions();if(event.target.id==='ctl-choice-model')choiceModes();});
  document.addEventListener('submit',async event=>{if(event.target.id!=='ctl-task-form')return;event.preventDefault();const form=event.target;const submit=form.querySelector('[type=submit]');submit.disabled=true;try{const data=new FormData(form);if(!data.get('model')||!data.get('effort')||!data.get('tier'))throw Error('请选择已接通的准确模型、思考和速度。');const lines=name=>String(data.get(name)||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);const checks=JSON.parse(String(data.get('checks')||'[]'));const project=await command('project.create',{root:data.get('root')});const specification={project_id:project.project_id,title:data.get('title')||undefined,goal:data.get('goal'),acceptance:data.get('acceptance')||undefined,provider:data.get('provider'),execution:data.get('execution'),role:data.get('role'),model:data.get('model'),effort:data.get('effort'),tier:data.get('tier'),files:lines('files'),attachments:attachmentRequests(data),writable:lines('writable'),alternatives:JSON.parse(String(data.get('alternatives')||'[]')),checks,host_command_permission:data.get('host_command_permission')==='on',campaign:data.get('campaign')==='independent'?null:'backend-rollout-20261017',budget:Object.fromEntries(['attempts','seconds','tools','requests','cny'].map(k=>[k,Number(data.get(k))]))};const receipt=await command(view.editSource?'task.revise':'task.create',view.editSource?{task_id:view.editSource.id,changes:specification}:specification);location.hash='#control/task/'+receipt.task_id;toast('任务和准确材料快照已保存，尚未发送。');}catch(error){fail(error);}finally{submit.disabled=false;}});
  window.WorkdeskModules.onLeave('control',leave);
  window.WorkdeskControl={render,leave,mountTask,unmountTask};
})();
