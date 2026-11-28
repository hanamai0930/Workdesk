(()=>{
  'use strict';const $=WorkdeskModules.dom('projects'),S=WorkdeskSupport,{h}=S;
  const v={data:null,error:'',busy:false,folder:'',name:'',scene:'',relocate:null,preview:null,seq:0,edits:{}};const active=()=>location.hash.split('/')[0]==='#projects';
  const scenes=[['','普通项目'],['research','科研探索'],['documents','文档处理'],['computer','电脑操控'],['development','应用开发']];
  const edited=p=>v.edits[p.id]||{title:p.name,scene:p.scene||''};
  const changed=(p,values=edited(p))=>values.title.trim()!==p.name||values.scene!==(p.scene||'');
  function removalReason(p){
    const links=[['任务',p.work_items],['项目对话',p.project_conversations],['执行记录',p.records],['草稿',p.drafts],['自动目标',p.goals]]
      .filter(([,count])=>count>0).map(([label,count])=>`${count} 项${label}`);
    return links.length?'仍关联 '+links.join('、')+'，暂不能移除登记。':'';
  }
  function relocation(p){
    if(v.relocate!==p.id)return '';
    return `<section class="project-relocation" aria-label="重新定位项目文件夹">
      <p class="sp-note">先选择新文件夹，再核对指定文件的版本。历史回合保留原路径；此处不移动文件。旧目录不可用时采用已保存的材料版本。</p>
      ${p.identity_files.map(n=>`<label><input type="checkbox" data-project-proof="${h(n)}"> ${h(n)}</label><br>`).join('')||'<p>尚无可核对文件；请先保存一个材料选择，或将新文件夹作为新项目添加。</p>'}
      ${S.button('选择新位置并核对',`data-project="location" data-id="${h(p.id)}"`)}
      ${v.preview?`<section class="project-location-preview"><h3 id="project-location-preview-title" tabindex="-1">目录核对预览</h3>
        <p>${h(v.preview.old_root)} → ${h(v.preview.new_root)}</p>
        <p class="sp-note">仅核对所选文件；不代表整个文件夹相同。确认后保留项目身份并撤销旧位置的范围授权。</p>
        ${S.raw(v.preview.manifest)}${S.button('确认使用新位置',`data-project="commit" data-id="${h(p.id)}"`)}
      </section>`:''}
    </section>`;
  }
  const versionLabels={reply:'任务回复',selection:'选定材料',input:'工具输入',previous_result:'修订所用成果',result:'任务成果',job:'工具成果'};
  async function projectVersions(panel,project,more){
    const query=new URLSearchParams({limit:'30'});
    if(more)query.set('cursor',panel.dataset.cursor||'0');
    const response=await fetch('api/v2/projects/'+encodeURIComponent(project)+'/materials?'+query,{cache:'no-store'});
    const value=await response.json();
    if(!response.ok)throw Error(value.error||'项目清单读取失败');
    if(!panel.isConnected)return;
    const rows=value.materials||[];
    if(!more)panel.innerHTML='<div data-version-rows></div>';
    const list=panel.querySelector('[data-version-rows]');
    for(const row of rows){
      const line=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input');
      input.type='checkbox';input.dataset.version=JSON.stringify(row.version);
      label.append(input,document.createTextNode(' '+(versionLabels[row.kind]||row.kind)+' · '+row.name+' · '+row.bytes+' 字节 · '+row.sha256.slice(0,12)));
      line.append(label);list.append(line);
    }
    panel.querySelector('[data-version-actions]')?.remove();
    if(!list.children.length){list.textContent='尚无已登记的材料或成果版本。';return;}
    panel.dataset.cursor=value.cursor;
    const actions=document.createElement('div');actions.dataset.versionActions='';
    actions.innerHTML=(value.has_more?S.button('查看更多',`data-project="versions-more" data-id="${h(project)}"`):'')+
      S.button('比较两个版本',`data-project="versions-compare" data-id="${h(project)}"`)+
      S.button('导出选定版本',`data-project="versions-export" data-id="${h(project)}"`);
    panel.append(actions);
  }
  async function compareVersions(panel,project){
    const refs=[...panel.querySelectorAll('[data-version]:checked')];
    if(refs.length!==2)throw Error('请选择要比较的两个版本。');
    const q=new URLSearchParams({left:refs[0].dataset.version,right:refs[1].dataset.version});
    const response=await fetch('api/v2/projects/'+encodeURIComponent(project)+'/compare?'+q,{cache:'no-store'});
    const d=await response.json();if(!response.ok)throw Error(d.error||'版本比较失败');
    if(!panel.isConnected)return;
    panel.querySelector('[data-comparison]')?.remove();
    const box=document.createElement('section');box.dataset.comparison='';
    const h=document.createElement('h4');h.textContent=d.identical?'文件内容一致':'文件内容有变化';box.append(h);
    d.versions.forEach((v,i)=>{
      const p=document.createElement('p');p.textContent=(i?'第二个版本':'第一个版本')+' · '+v.name+' · '+v.bytes+' 字节 · '+v.version.sha256;box.append(p);
      if(!d.identical&&d.mode==='text'){
        const n=i?d.right_lines:d.left_lines;
        const pre=document.createElement('pre');pre.textContent='从第 '+d.start_line+' 行起，变化范围 '+n+' 行'+(n>80?'（显示前 80 行）':'')+'\n'+(i?d.right_excerpt:d.left_excerpt).join('\n');box.append(pre);
      }
    });
    panel.append(box);
  }
  async function exportVersions(panel,project){
    const refs=[...panel.querySelectorAll('[data-version]:checked')];
    if(!refs.length||refs.length>32)throw Error('请选择 1 至 32 个版本。');
    const q=new URLSearchParams();for(const el of refs)q.append('version',el.dataset.version);
    const reply=await fetch('api/v2/projects/'+encodeURIComponent(project)+'/export?'+q,{cache:'no-store'});
    if(!reply.ok){const error=await reply.json();throw Error(error.error||'版本导出失败');}
    const url=URL.createObjectURL(await reply.blob());
    const a=document.createElement('a');a.href=url;a.download='Workdesk-项目材料与成果.zip';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  function projectCard(p){
    const values=edited(p),dirty=changed(p),reason=removalReason(p);
    return `<article class="sp-card" data-project-card="${h(p.id)}"><div class="sp-row"><h2>${h(p.name)}</h2><span class="sp-badge">${p.exists?'目录可用':'目录不可用'}</span></div>
      <p class="sp-note">${h(p.root)} · ${p.work_items} 个任务 · ${p.project_conversations} 条项目对话 · ${p.records} 条执行记录 · ${p.drafts} 个草稿 · ${p.goals} 个目标</p>
      <a class="project-primary-link" href="#work-items/project/${h(p.id)}">进入项目任务与对话</a>
      ${document.documentElement.dataset.workdeskChannel?`<div class="sp-actions">
        ${S.button('电脑与开发工具',`data-project="computer-tools" data-id="${h(p.id)}"`)}
        ${S.button('文档工具',`data-project="document-tools" data-id="${h(p.id)}"`)}
        ${S.button('工具预约',`data-project="tool-schedules" data-id="${h(p.id)}"`)}
        ${S.button('项目版本归档',`data-project="project-archive" data-id="${h(p.id)}"`)}
        ${S.button('材料与成果版本',`data-project="versions" data-id="${h(p.id)}"`)}
      </div><section aria-label="项目版本清单" aria-live="polite" data-project-versions></section>`:''}
      <form data-project-edit="${h(p.id)}">
        ${S.field('项目名称','title',values.title,'required maxlength="100"')}${S.select('工作场景','scene',scenes,values.scene)}
        <div class="sp-actions"><button type="submit" ${dirty?'':'disabled'} aria-describedby="project-save-${h(p.id)}">保存名称和场景</button>
          ${S.button(p.pinned?'取消置顶':'置顶',`data-project="pin" data-id="${h(p.id)}"`)}
          ${S.button('重新定位文件夹',`data-project="relocate" data-id="${h(p.id)}"`)}
          ${S.button('移除闲置登记',`class="project-remove" data-project="remove" data-id="${h(p.id)}" ${reason?`disabled aria-describedby="project-remove-${h(p.id)}" title="${h(reason)}"`:''}`)}
        </div>
        <p class="sp-note" id="project-save-${h(p.id)}" data-project-save-status aria-live="polite" ${dirty?'':'hidden'}>${dirty?'修改尚未保存。':''}</p>
        ${reason?`<p class="sp-note" id="project-remove-${h(p.id)}">${h(reason)}</p>`:''}
      </form>${relocation(p)}
    </article>`;
  }
  function draw(){
    if(!active()||!v.data)return;
    $('#main').innerHTML=`<section class="sp-page">${S.nav('projects')}
      <header class="sp-heading"><div><h1>项目与工作区</h1><p>管理项目登记和文件夹位置；这些操作不会移动或删除项目文件，也不改变执行权限。</p></div></header>
      ${v.error?`<p class="sp-error" role="alert">${h(v.error)}</p>`:''}
      <section class="sp-card"><h2>添加项目</h2><form id="projects-add">${S.field('项目名称','name',v.name,'required maxlength="100"')}${S.select('工作场景','scene',scenes,v.scene)}
        <p class="sp-note">${h(v.folder||'请选择项目源文件夹')}</p>${S.button('选择项目文件夹','data-project="pick"')}<button type="submit" ${v.folder?'':'disabled'}>添加项目</button>
      </form></section>${v.data.projects.map(projectCard).join('')}
    </section>`;
  }
  function updateEdit(form){
    const p=v.data?.projects.find(p=>p.id===form.dataset.projectEdit);if(!p)return;
    const data=new FormData(form),values={title:String(data.get('title')||''),scene:String(data.get('scene')||'')};
    v.edits[p.id]=values;
    const dirty=changed(p,values);form.querySelector('[type="submit"]').disabled=!dirty;
    const status=form.querySelector('[data-project-save-status]'),message=dirty?'修改尚未保存。':'';
    status.hidden=!dirty;
    if(status.textContent!==message)status.textContent=message;
  }
  async function load(){const seq=++v.seq;try{const data=await S.request('projects');if(seq!==v.seq||!active())return;v.data=data;draw();}catch(e){v.error=e.message;if(v.data)draw();else if(active())$('#main').innerHTML=errorPage(e.message);}}
  document.addEventListener('input',e=>{if(!active())return;const form=e.target.closest('form');if(form?.dataset.projectEdit)return updateEdit(form);if(form?.id==='projects-add'&&e.target.name==='name')v.name=e.target.value;});
  document.addEventListener('change',e=>{if(!active())return;const form=e.target.closest('form');if(form?.dataset.projectEdit)return updateEdit(form);if(form?.id==='projects-add'&&e.target.name==='scene')v.scene=e.target.value;});
  document.addEventListener('click',async e=>{const el=e.target.closest('[data-project]');if(!active()||!el||el.disabled||v.busy)return;v.busy=true;try{const a=el.dataset.project,p=v.data.projects.find(p=>p.id===el.dataset.id);
    if(['document-tools','computer-tools','project-archive','tool-schedules'].includes(a))window.dispatchEvent(new CustomEvent('workdesk-'+a,{detail:{project_id:p.id,project_root:p.root}}));
    if(a==='versions'||a==='versions-more'||a==='versions-export'||a==='versions-compare'){
      const panel=el.closest('[data-project-card]').querySelector('[data-project-versions]');
      try{if(a==='versions-compare')await compareVersions(panel,p.id);else if(a==='versions-export')await exportVersions(panel,p.id);else await projectVersions(panel,p.id,a==='versions-more');}
      catch(error){let message=panel.querySelector('[role="alert"]');if(!message){message=document.createElement('p');message.setAttribute('role','alert');panel.append(message);}message.textContent=error.message;}
    }
    if(a==='pick'){const path=await S.directory();if(path){v.folder=path;if(!v.name)v.name=path.split(/[\\/]/).at(-1);draw();}}
    if(a==='pin')await S.command('work.project-record',{project_id:p.id,revision:p.revision,pinned:!p.pinned});
    if(a==='remove'&&await S.confirm('移除此工作区登记？',p.name,'项目文件和历史审计会保留；仍被记录、草稿或目标使用的登记不会移除。',{tone:'danger',confirmLabel:'移除登记'}))await S.command('work.projects-remove',{projects:[{id:p.id,revision:p.revision}],confirm:true});
    if(a==='relocate'){v.relocate=p.id;v.preview=null;draw();}
    if(a==='location'){const files=[...el.closest('[data-project-card]').querySelectorAll('[data-project-proof]:checked')].map(x=>x.dataset.projectProof);const path=await S.directory();if(path){v.preview=(await S.command('project.location-prepare',{project_id:p.id,revision:p.revision,new_root:path,files})).preview;draw();$('#project-location-preview-title')?.focus();}}
    if(a==='commit'&&v.relocate===p.id&&v.preview&&await S.confirm('更改项目登记位置？',v.preview.new_root,'保留旧文件夹和历史回合路径；自动计划不会静默跟随目录变化。',{confirmLabel:'使用新位置'})){await S.command('project.location-commit',{id:v.preview.id,preview_hash:v.preview.preview_hash,confirm:true});v.preview=null;v.relocate=null;}
    if(['pin','remove','commit'].includes(a)){await S.refreshChrome();await load();}v.error='';
  }catch(e){v.error=e.message;draw();}finally{v.busy=false;}});
  document.addEventListener('submit',async e=>{const f=e.target;if(!active()||!(f.id==='projects-add'||f.dataset.projectEdit))return;e.preventDefault();if(v.busy)return;v.busy=true;try{const data=new FormData(f);if(f.id==='projects-add'){await S.command('project.create',{root:v.folder,name:data.get('name'),scene:data.get('scene')||null});v.folder='';v.name='';}else{const p=v.data.projects.find(p=>p.id===f.dataset.projectEdit);updateEdit(f);if(!changed(p))return;await S.command('work.project-record',{project_id:p.id,revision:p.revision,title:data.get('title'),scene:data.get('scene')||null});delete v.edits[p.id];}v.error='';await S.refreshChrome();await load();}catch(e){v.error=e.message;draw();}finally{v.busy=false;}});
  WorkdeskModules.onLeave('projects',()=>++v.seq);window.WorkdeskProjectsPage={render:load};
})();
