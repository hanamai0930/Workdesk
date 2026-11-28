import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight,Folder,Paperclip,Plus,Target} from 'lucide-react';
import {coreClient as core} from '../core/client';

export type TurnFile={file:string;sha256:string};
type Command=(kind:string,payload:Record<string,unknown>,id?:string)=>ReturnType<typeof core.command>;
type Actor=(fn:()=>Promise<void>,recover?:boolean)=>Promise<void>;
const valid=(f:any):f is TurnFile=>typeof f?.file==='string'&&/^[a-f0-9]{64}$/i.test(f.sha256);
export function useTurnFiles(task:string,attempt:string,project:string,command:Command,act:Actor){
  const key='workdesk.turn-files:'+task+':'+attempt;
  const [files,setFiles]=useState<TurnFile[]>(()=>{
    try{return JSON.parse(sessionStorage.getItem(key)||'[]').filter(valid);}catch{return [];}
  });
  const list=useRef(files);
  function save(next:TurnFile[]){
    list.current=next;setFiles(next);
    try{if(next.length)sessionStorage.setItem(key,JSON.stringify(next));else sessionStorage.removeItem(key);}catch{/* Keep the current draft in memory. */}
  }
  function apply(kind:string,receipt:any){
    const received=kind==='work.upload'?[receipt]:kind==='work.files-select'?receipt.selected:[];
    if(!Array.isArray(received)||!received.length)return;
    if(!received.every(valid))throw Error('附件缺少版本信息，请重新选择。');
    const merged=new Map(list.current.map(f=>[f.file,f]));
    for(const f of received)merged.set(f.file,{file:f.file,sha256:f.sha256});
    save([...merged.values()]);
  }
  function upload(items:File[]){
    if(!items.length)return;
    void act(async()=>{
      for(const file of items){
        if(!file.size||file.size>8*1024*1024)throw Error('请选择 1 字节至 8 MB 的文件。');
        const bytes=new Uint8Array(await file.arrayBuffer());
        let raw='';for(let i=0;i<bytes.length;i+=32768)raw+=String.fromCharCode(...bytes.subarray(i,i+32768));
        const result=await command('work.upload',{project_id:project,name:file.name,data:btoa(raw)});
        apply('work.upload',result.receipt);
      }
    });
  }
  function select(path:string){
    void act(async()=>{
      const result=await command('work.files-select',{project_id:project,files:[path]});
      apply('work.files-select',result.receipt);
      if(!(result.receipt as {selected:TurnFile[]}).selected?.length)throw Error('这里没有可添加的文件。');
    });
  }
  return {files,upload,select,apply,remove:(path:string)=>save(list.current.filter(f=>f.file!==path)),clear:()=>save([])};
}

export function ComposerAdd({project,attachDisabled,disabled,onGoal,upload,select}:{
  project:string;attachDisabled:boolean;disabled:boolean;onGoal:()=>void;upload:(items:File[])=>void;select:(path:string)=>void
}){
  const [pane,setPane]=useState<''|'main'|'project'>(''),[folder,setFolder]=useState('');
  const [entries,setEntries]=useState<{name:string;file:string;directory:boolean}[]>([]),[error,setError]=useState('');
  const box=useRef<HTMLDivElement>(null),picker=useRef<HTMLInputElement>(null),trigger=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    if(!pane)return;
    const outside=(event:PointerEvent)=>{if(!box.current?.contains(event.target as Node))setPane('');};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){setPane('');trigger.current?.focus();}};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[pane]);
  useEffect(()=>{
    if(pane!=='project')return;
    const ctl=new AbortController();setEntries([]);setError('');
    core.request('v1/work-files?'+new URLSearchParams({project,folder}),{signal:ctl.signal})
      .then(res=>{if(!ctl.signal.aborted){setEntries(res.files);if(!res.files.length)setError('文件夹为空');}})
      .catch(e=>{if(!ctl.signal.aborted)setError(String(e.message||e));});
    return()=>ctl.abort();
  },[pane,folder,project]);
  function pick(path:string){setPane('');select(path);}
  return <div className="wd-compose-add" ref={box}>
    <button type="button" ref={trigger} className="wd-add-trigger" aria-label="添加" title="添加" aria-expanded={!!pane} disabled={disabled} onClick={()=>setPane(pane?'':'main')}><Plus size={18}/></button>
    <input ref={picker} type="file" multiple hidden aria-label="本机附件" onChange={e=>{upload(Array.from(e.target.files||[]));e.target.value='';}}/>
    {pane&&<div className="wd-add-popup" aria-label="添加内容">
      {pane==='main'?<><small>添加</small>
        <button type="button" disabled={attachDisabled} onClick={()=>{setPane('');picker.current?.click();}}><Paperclip size={16}/>本机文件</button>
        <button type="button" disabled={attachDisabled} onClick={()=>{setFolder('');setPane('project');}}><Folder size={16}/>项目文件和文件夹</button>
        <button type="button" onClick={()=>{setPane('');onGoal();}}><Target size={16}/>持续目标</button></>:
        <><button type="button" onClick={()=>folder?setFolder(folder.split('/').slice(0,-1).join('/')):setPane('main')}><ArrowLeft size={14}/>{folder||'项目文件'}</button>
        {folder&&<button type="button" onClick={()=>pick(folder)}><Plus size={15}/>添加此文件夹</button>}
        {entries.map(e=><button key={e.file} type="button" onClick={()=>e.directory?setFolder(e.file):pick(e.file)}>{e.directory?<Folder size={15}/>:<Paperclip size={15}/>}<span>{e.name}</span>{e.directory&&<ChevronRight size={12}/>}</button>)}
        {error&&<small role="status">{error}</small>}</>}
    </div>}
  </div>;
}
