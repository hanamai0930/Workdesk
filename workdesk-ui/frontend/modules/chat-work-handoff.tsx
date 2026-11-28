import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {coreClient} from '../core/client';

type Message={id:string;role:string;content:string;attachments?:{id:string;name:string;kind:string}[]};
export type HandoffChat={id:string;title:string;revision?:number;route?:string;list_project?:string;messages?:Message[];web_url?:string;prompt?:string;pending?:unknown};
export function ChatWorkHandoff({source}:{source:HandoffChat}){
  const dialog=useRef<HTMLDialogElement>(null),lock=useRef(false);
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState('');
  const [snapshot,setSnapshot]=useState<HandoffChat|null>(null);
  useEffect(()=>()=>{dialog.current?.close();},[]);
  async function open(){
    setSnapshot(structuredClone(source));setError('');setProjects([]);dialog.current?.showModal();
    try{await coreClient.loadIdentity();setPending(coreClient.uncertainCommands().find(c=>c.kind==='chat.work-draft')?.id||'');setProjects((await coreClient.request('v2/projects?limit=200')).projects);}
    catch(e){setError(String(e));}
  }
  async function save(form:HTMLFormElement){
    if(!snapshot||lock.current||pending)return;
    const f=new FormData(form);lock.current=true;setBusy(true);setError('');
    try{
      const supplied=f.getAll('local_files').filter((x):x is File=>x instanceof File&&!!x.name);
      if(supplied.length>8||supplied.reduce((sum,x)=>sum+x.size,0)>8*1024*1024)throw Error('最多补充 8 份文件，合计不超过 8 MiB。');
      const local_files=await Promise.all(supplied.map(async file=>{
        const data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('无法读取 '+file.name));reader.readAsDataURL(file);});
        return {name:file.name,data};
      }));
      const r=await coreClient.command('chat.work-draft',{chat_id:snapshot.id,revision:snapshot.revision,project_id:f.get('project'),goal:f.get('goal'),message_ids:f.getAll('message'),attachment_ids:f.getAll('attachment'),include_prompt:f.has('prompt'),include_link:f.has('link'),pasted:f.get('pasted')||'',local_files,confirm:true});
      if(dialog.current?.open){dialog.current.close();location.hash='#work/draft/'+encodeURIComponent(r.receipt.draft_id);}
    }catch(e){setError(String(e));setPending(coreClient.uncertainCommands().find(c=>c.kind==='chat.work-draft')?.id||'');}
    finally{lock.current=false;setBusy(false);}
  }
  const files=new Map((snapshot?.messages||[]).flatMap(m=>(m.attachments||[]).map(f=>[f.id,f] as const)));
  return <><Button type="button" variant="outline" size="sm" disabled={!!source.pending} onClick={open}>交给 Work</Button>
    <dialog ref={dialog} aria-label="选材并创建工作草稿" className="wd-stage4-dialog"><h2>选材并创建工作草稿</h2>
    {snapshot&&<form key={snapshot.id+':'+snapshot.revision} onSubmit={e=>{e.preventDefault();void save(e.currentTarget);}}>
    <label>目标项目<select name="project" required key={projects.length} defaultValue={snapshot.list_project||''}><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label>工作目标<textarea name="goal" required maxLength={12000}/></label>
    <fieldset><legend>带入工作任务的材料</legend>
    {snapshot.route==='web'?<>{snapshot.prompt&&<label><input type="checkbox" name="prompt"/>开场文字 · {snapshot.prompt}</label>}{snapshot.web_url&&<label><input type="checkbox" name="link"/>网页链接 · {snapshot.web_url}</label>}</>:
    (snapshot.messages||[]).filter(m=>m.id&&m.content).map(m=><details key={m.id}><summary><label><input type="checkbox" name="message" value={m.id}/>{m.role==='human'?'用户':'回复'} · {m.content.slice(0,90)}</label></summary><pre>{m.content}</pre></details>)}
    {Array.from(files.values()).map(f=><label key={f.id}><input type="checkbox" name="attachment" value={f.id}/>{f.name} · {f.kind==='image'?'图片原件':'已保存的提取文字'}</label>)}
    <label>补充本地原件<input type="file" name="local_files" multiple/></label>
    <label>补充或粘贴的材料<textarea name="pasted" maxLength={12000}/></label></fieldset>
    <p>选定内容将保存到目标项目，工作端和发送时间可在草稿中选择。</p>
    {error&&<p role="alert">{error}</p>}
    {pending&&<Button type="button" onClick={async()=>{try{const r=await coreClient.receipt(pending);if(r.state==='RECORDED'){setPending('');if(r.receipt?.draft_id){dialog.current?.close();location.hash='#work/draft/'+encodeURIComponent(r.receipt.draft_id);}}else setError('原命令仍待核对。');}catch(e){setError(String(e));}}}>核对原交接命令</Button>}
    <Button type="button" variant="outline" onClick={()=>dialog.current?.close()}>关闭</Button> <Button disabled={busy||!projects.length||!!pending}>保存工作草稿</Button></form>}</dialog></>;
}
