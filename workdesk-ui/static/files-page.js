/* File selections retain exact versions; processing belongs to the native runtime. */
(()=>{
  'use strict';const $=WorkdeskModules.dom('files'),S=WorkdeskSupport,{h}=S;
  const v={projects:[],project:'',folder:'',files:[],selected:new Set(),saved:[],preview:null,error:'',busy:false,seq:0};
  const active=()=>location.hash.split('/')[0]==='#files';
  const projectRoute=()=>location.hash.split('/')[1]||'';
  function resetProject(project){v.project=project;v.folder='';v.selected.clear();v.preview=null;v.error='';v.files=[];v.saved=[];}
  function draw(){if(!active())return;const p=v.preview, id=projectRoute(), project=v.projects.find(row=>row.id===id);
    $('#main').innerHTML=`<section class="sp-page">${id?`<a class="text-button" href="#work-items/project/${encodeURIComponent(id)}">‹ 返回项目概览</a>`:''}<header class="sp-heading"><div><h1>${project?h(project.name)+' · 项目文件':'文件与附件'}</h1><p>浏览和核对原文件；发送后的格式处理由所选官方工作端完成。</p></div></header>${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}${id?'':S.select('项目工作区','project',v.projects.map(p=>[p.id,p.name]),v.project)}${v.project?`<div class="sp-grid"><section class="sp-card"><div class="sp-row"><h2>${h(v.folder||'项目根目录')}</h2>${S.button('上一级','data-file-action="parent" '+(!v.folder?'disabled':''))}</div><p class="sp-note">已选 ${v.selected.size} 个文件，可跨文件夹选择。预览不代表模型已读取。</p><div class="sp-file-list">${v.files.map(f=>`<div class="sp-file-row">${f.directory?'<span>📁</span>':`<input type="checkbox" aria-label="选择 ${h(f.name)}" data-file-check="${h(f.file)}" ${v.selected.has(f.file)?'checked':''}>`}<button type="button" data-file-action="${f.directory?'folder':'preview'}" data-name="${h(f.file)}">${h(f.name)}</button><small>${f.directory?'文件夹':f.bytes+' B'}</small></div>`).join('')||'<p class="sp-empty">该文件夹没有可浏览文件。</p>'}</div><div class="sp-actions">${S.button('保存选择清单','data-file-action="save" '+(!v.selected.size?'disabled':''))}${S.button('清空选择','data-file-action="clear"')}<label class="sp-upload">添加本机文件<input type="file" aria-label="添加本机文件" multiple></label></div><p class="sp-note">添加文件会复制到项目的 workdesk-inputs，保留原文件；单个文件最多 8MB。</p>${v.selected.size?`<details><summary>所选路径</summary>${S.raw([...v.selected])}</details>`:''}</section><section class="sp-card sp-preview"><div class="sp-row"><h2>${p?h(p.file):'文件预览'}</h2>${p?S.button('在本机打开','data-file-action="open-local"'):''}</div>${p?`<p class="sp-note">${p.bytes} B · SHA-256 ${h(p.sha256)}</p>${p.kind==='text'?`<pre>${h(p.content)}</pre>`:p.kind==='image'?`<img src="${h(p.image)}" alt="${h(p.file)}">`:p.kind==='pdf'?`<iframe title="PDF 原文件预览" src="api/file-preview?${h(new URLSearchParams({project:v.project,file:p.file,sha256:p.sha256}).toString())}"></iframe>`:`<p>${h(p.content||'按原文件交给官方工作端处理。')}</p>`}`:'<p class="sp-empty">选择一个文件查看预览。</p>'}</section></div><section><h2>已保存的选择</h2><p class="sp-note">生成草稿和发送前会再次核对文件版本；文件改动后需要重新保存。</p>${v.saved.map(r=>`<article class="sp-card"><div class="sp-row"><strong>${r.selected.length} 个文件 · ${h(S.when(r.created))}</strong>${S.button('用于新工作草稿',`data-file-action="draft" data-id="${h(r.id)}"`)}</div><details><summary>路径与版本</summary>${S.raw(r.selected)}</details></article>`).join('')||'<p class="sp-empty">暂无已保存选择。</p>'}</section>`:'<p class="sp-empty">请先添加项目工作区。</p>'}</section>`;
  }
  async function load(){const seq=++v.seq;try{const data=await S.request('workspace?view=workbench');if(seq!==v.seq||!active())return;v.projects=data.projects.filter(p=>!p.removed);const id=projectRoute();
    if(id&&!v.projects.some(p=>p.id===id)){resetProject('');v.error='找不到这个项目或项目已移除。';draw();return;}
    if(!v.projects.some(p=>p.id===v.project))resetProject(id||v.projects[0]?.id||'');
    if(v.project){const [files,saved]=await Promise.all([S.request('work-files?'+new URLSearchParams({project:v.project,folder:v.folder})),S.request('file-selections?'+new URLSearchParams({project:v.project}))]);if(seq!==v.seq||!active())return;v.files=files.files;v.saved=saved.selections;if(files.limited)v.error='本目录仅显示前500项，请进入子目录继续选择。';}draw();
  }catch(e){v.error=e.message;draw();}}
  document.addEventListener('change',async e=>{if(!active())return;const el=e.target;if(el.matches('[data-file-check]')){el.checked?v.selected.add(el.dataset.fileCheck):v.selected.delete(el.dataset.fileCheck);draw();}
    else if(el.name==='project'&&!projectRoute()){resetProject(el.value);await load();}
    else if(el.matches('input[type=file][aria-label="添加本机文件"]')){if(v.busy)return;v.busy=true;try{for(const file of el.files){if(file.size>8388608)throw Error('单个文件最多8MB。');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let n=0;n<bytes.length;n+=32768)raw+=String.fromCharCode(...bytes.subarray(n,n+32768));const r=await S.command('work.upload',{project_id:v.project,name:file.name,data:btoa(raw)});v.selected.add(r.file);}v.error='';await load();}catch(e){v.error=e.message;draw();}finally{v.busy=false;}}
  });
  document.addEventListener('click',async e=>{const el=e.target.closest('[data-file-action]');if(!el||!active()||v.busy)return;e.preventDefault();v.busy=true;v.error='';try{
    const a=el.dataset.fileAction;if(a==='folder'){v.folder=el.dataset.name;await load();}else if(a==='parent'){v.folder=v.folder.split('/').slice(0,-1).join('/');await load();}else if(a==='clear'){v.selected.clear();draw();}
    else if(a==='preview'){const seq=++v.seq,p=await S.request('file-inspect?'+new URLSearchParams({project:v.project,file:el.dataset.name}));if(seq===v.seq&&active()){v.preview=p;draw();}}
    else if(a==='open-local'&&v.preview){await api('open-project-file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:v.preview.project_id,file:v.preview.file,sha256:v.preview.sha256})});}
    else if(a==='save'){await S.command('file.selection-save',{project_id:v.project,files:[...v.selected]});await load();}
    else if(a==='draft'){const r=v.saved.find(r=>r.id===el.dataset.id),out=await S.command('file.selection-draft',{id:r.id,revision:r.revision});location.hash='#work/draft/'+out.draft.id;}
  }catch(e){v.error=e.message;draw();}finally{v.busy=false;}});
  WorkdeskModules.onLeave('files',()=>{++v.seq;});window.WorkdeskFilesPage={render:()=>{
    const id=projectRoute();
    if(id&&id!==v.project)resetProject(id);
    return load();
  }};
})();
