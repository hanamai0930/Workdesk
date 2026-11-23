import { useEffect, useId, useState } from 'react';
import { MoreHorizontal, Pin, Square, SquareCheck, Trash2, X, Folder, MessageSquare, Workflow, Plus, ChevronDown, ChevronRight, Telescope, FileText, MousePointer2, Terminal, History } from 'lucide-react';
import { Button } from '../components/ui/button';
import './work-records.css';

const sceneIcons:Record<string,any>={research:Telescope,documents:FileText,computer:MousePointer2,development:Terminal,general:Folder,legacy:History};
const key=(r:any)=>r.kind+':'+r.id;
function recordHref(r:any) {
  const id=encodeURIComponent(r.id);
  if(r.kind==='cooperation')return '#work-items/conversation/'+id;
  if(r.kind==='chat')return '#chat/'+id;
  return '#work/'+r.kind+'/'+id;
}

function ProjectBranch({id,name,scene,pinned,conversations,current,active,selecting,chosen}:any){
  const conversationList=useId();
  const [open,setOpen]=useState(true);
  const activeChild=conversations.some((r:any)=>key(r)===active)?active:null;
  useEffect(()=>{if(activeChild)setOpen(true);},[activeChild]);
  const hasChildren=conversations.length>0;
  const projectHref=id?'#work-items/project/'+encodeURIComponent(id):'#work/project/unfiled/'+scene;
  return <div className="wd-work-branch">
    <div className="wd-work-project-row" data-work-project={id||''}>
      {hasChildren?<Button variant="ghost" size="icon" className="wd-work-branch-toggle" aria-expanded={open} aria-controls={conversationList}
        aria-label={(open?'收起':'展开')+name+'的对话'} onClick={()=>setOpen(!open)}><ChevronRight aria-hidden="true"/></Button>:<span className="wd-work-branch-spacer" aria-hidden="true"/>}
      <a className="wk-record wk-project-row" href={projectHref} title={name} aria-current={current?'location':undefined}>
        <Folder aria-hidden="true"/><span>{name}</span>{pinned&&<Pin aria-label="已置顶"/>}
      </a>
      {!selecting&&<Button variant="ghost" size="icon" className="wd-work-more" data-wb-menu="work-project" data-project={id||''} data-scene={scene} aria-label={'项目操作：'+name} aria-haspopup="menu"><MoreHorizontal/></Button>}
    </div>
    {hasChildren&&<ul className="wd-work-conversations" id={conversationList} hidden={!open} aria-label={name+'的对话'}>
      {conversations.map((r:any)=>{
        const selected=chosen.has(key(r)),isActive=key(r)===active,manageable=r.kind==='task'||r.kind==='dialogue';
        const Icon=r.kind==='cooperation'?Workflow:MessageSquare;
        const recordContent=<><Icon aria-hidden="true"/><span>{r.title}</span>{r.pinned&&<Pin aria-label="已置顶"/>}</>;
        return <li key={key(r)} className={'wd-work-record-row '+(isActive?'is-active ':'')+(selected?'is-selected':'')} data-work-key={manageable?key(r):undefined}>
          {selecting&&manageable&&<button type="button" className="wd-work-select" role="checkbox" aria-label={'选择Work记录：'+r.title} aria-checked={selected} data-wb="work-record-toggle" data-key={key(r)}>{selected?<SquareCheck/>:<Square/>}</button>}
          {selecting&&manageable?<button type="button" className="wk-record wk-chat-row wd-work-record-link" data-wb="work-record-toggle" data-key={key(r)}>{recordContent}</button>:
            <a className="wk-record wk-chat-row wd-work-record-link" href={recordHref(r)} title={r.title} aria-current={isActive?'page':undefined}>{recordContent}</a>}
          {!selecting&&manageable&&<Button variant="ghost" size="icon" className="wd-work-more" data-wb-menu="work-record" data-key={key(r)} aria-label={'Work记录操作：'+r.title} aria-haspopup="menu"><MoreHorizontal/></Button>}
        </li>;
      })}
    </ul>}
  </div>;
}

export function WorkRecords({rows,chats,cooperation,projects,scenes,active,activeProject,activeUnfiled,selecting,selected}:any){
  const chosen=new Set(selected);
  const workRows=[...rows,...cooperation];
  const groups=[...Object.entries(scenes).map(([id,s]:any)=>({id,name:s.name})),{id:'general',name:'普通项目'},{id:'legacy',name:'未分类的旧记录'}];
  const knownProjects=new Set(projects.map((p:any)=>p.id));
  const rowScene=(r:any)=>r.group_scene||(scenes[r.scene]?r.scene:'legacy');
  const projectBranches=projects.map((p:any)=>{
    const work=workRows.filter((r:any)=>r.project===p.id);
    const conversations=[...work,...chats.filter((r:any)=>r.project===p.id)].sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||b.created-a.created);
    const scene=scenes[p.scene]?p.scene:work[0]?rowScene(work[0]):'general';
    return {id:p.id,name:p.name,pinned:p.pinned,scene,conversations};
  }).sort((a:any,b:any)=>Number(!!b.pinned)-Number(!!a.pinned));
  return <div data-ui-module="workbench" style={{display:'contents'}}><section className="wk-side-section wd-work-records" data-work-records>
    <div className="wk-section-label"><span>{selecting?'已选择 '+chosen.size+' 项':'项目与对话'}</span>{selecting?<Button variant="ghost" size="icon" data-wb="work-record-finish" aria-label="完成Work多选"><X/></Button>:<Button variant="ghost" size="icon" data-wb="new-project" aria-label="新建项目"><Plus/></Button>}</div>
    {selecting&&<div className="wd-work-batch"><Button variant="ghost" data-wb="work-record-all">{rows.length&&rows.every((r:any)=>chosen.has(key(r)))?'取消全选':'全选'}</Button><Button variant="ghost" data-wb="work-record-scene-selected" disabled={!chosen.size}>归入场景</Button><Button variant="ghost" data-wb="work-record-project-selected" disabled={!chosen.size}>归入项目</Button><Button variant="ghost" data-wb="work-record-delete-selected" disabled={!chosen.size}><Trash2/>删除所选（{chosen.size}）</Button></div>}
    {groups.map(g=>{
      const branches=projectBranches.filter((branch:any)=>branch.scene===g.id);
      const unfiled=workRows.filter((r:any)=>!knownProjects.has(r.project)&&rowScene(r)===g.id);
      if(unfiled.length)branches.push({id:null,name:'未归入项目',scene:g.id,conversations:unfiled});
      const count=branches.reduce((total:number,branch:any)=>total+branch.conversations.length,0);
      const Icon=sceneIcons[g.id];
      const heading=<><Icon/><span>{g.name}</span>{count>0&&<small className="wk-scene-count" aria-label={count+' 条对话'}>{count}</small>}</>;
      if(!branches.length)return ['legacy','general'].includes(g.id)?null:<div className="wk-scene-group" data-empty key={g.id}><div className="wk-scene-label" title={g.name}>{heading}</div></div>;
      const containsActive=branches.some((branch:any)=>(branch.id&&branch.id===activeProject)||branch.conversations.some((r:any)=>key(r)===active));
      return <details className="wk-scene-group" key={g.id} open={g.id!=='legacy'||selecting||containsActive} data-work-scene={g.id}>
        <summary title={g.name}>{heading}<ChevronDown/></summary>
        {branches.map((branch:any)=><ProjectBranch key={branch.id||'unfiled'} {...branch} active={active} selecting={selecting} chosen={chosen}
          current={branch.id?branch.id===activeProject:activeUnfiled===g.id}/>)}
      </details>;
    })}
  </section></div>;
}
