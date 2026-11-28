import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { ArrowLeft, File, Folder, FolderOpen, Search } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../components/ui/button';
import { NativeSelect } from '../components/ui/native-select';
import './quick-files.css';

type Project = {id:string; name:string; removed?:boolean};
type ProjectFile = {file:string; name:string; directory:boolean};
type FolderReply = {files:ProjectFile[]; limited?:boolean};
type Preview = {kind:'text'|'image'|'pdf'|'binary'; content?:string; image?:string; sha256?:string};
type Result<T> = {url:string; value?:T; error?:string};
type QuickFilesProps = {projectId?:string|null; initialQuery?:string; onBack?:()=>void};

const previewLimit=100000;
const localSizeLimit=8*1024*1024;
const markdownPlugins=[remarkGfm];
const markdownComponents={
  a:({children,href}:ComponentProps<'a'>)=><span title={href}>{children}</span>,
  img:({alt}:ComponentProps<'img'>)=>alt?<span>{alt}</span>:null,
  table:({children}:ComponentProps<'table'>)=><div className="wk-quick-file-table" tabIndex={0}><table>{children}</table></div>,
};
const localImageTypes:Record<string,string>={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'};

function useFileRequest<T>(url:string|null){
  const [result,setResult]=useState<Result<T>|null>(null);
  useEffect(()=>{
    if(!url)return;
    const controller=new AbortController();
    async function read(requestUrl:string){
      try {
        const response=await fetch(requestUrl,{signal:controller.signal,cache:'no-store',credentials:'omit'});
        const value=await response.json();
        if(!response.ok)throw Error(value.error||'读取文件失败。');
        if(!controller.signal.aborted)setResult({url:requestUrl,value});
      } catch (error) {
        if(!controller.signal.aborted)setResult({url:requestUrl,error:error instanceof Error?error.message:'读取文件失败。'});
      }
    }
    void read(url);
    return ()=>controller.abort();
  },[url]);
  return result?.url===url?result:null;
}

export function PreviewContents({value,name,pdf}:{value:Preview;name:string;pdf?:string}){
  if(value.kind==='image')return <img src={value.image} alt={name}/>;
  if(value.kind==='pdf'&&pdf)return <iframe title={`预览 ${name}`} src={pdf}/>;
  if(value.kind!=='text')return <p className="wk-note">此文件格式需要使用对应的本机应用打开。</p>;
  const content=value.content??'';
  const sample=content.slice(0,previewLimit).replace(/^\uFEFF/,'');
  return <>
    {/\.(md|markdown|mdown|mdx)$/i.test(name)
      ?<div className="wk-quick-file-markdown"><Markdown skipHtml remarkPlugins={markdownPlugins} components={markdownComponents}>{sample}</Markdown></div>
      :<pre tabIndex={0}><code>{sample}</code></pre>}
    {content.length>previewLimit&&<p className="wk-note">此处显示文件的前 {previewLimit.toLocaleString('zh-CN')} 个字符。</p>}
  </>;
}

function FileHeading({name,fullName,onBack,backLabel='返回'}:{name:string;fullName?:string;onBack?:()=>void;backLabel?:string}){
  return <div className="wk-quick-files-heading">
    {onBack&&<Button variant="ghost" size="icon" type="button" className="wk-icon" onClick={onBack} aria-label={backLabel} title={backLabel}><ArrowLeft/></Button>}
    <strong title={fullName}>{name}</strong>
  </div>;
}

function splitProjectPath(input:string){
  const path=input.trim().replaceAll('\\','/');
  if(!path.includes('/')||path.startsWith('/')||/[:\u0000]/.test(path))return null;
  const directory=path.endsWith('/');
  const parts=path.replace(/\/$/,'').split('/');
  if(parts.some(part=>!part||part==='.'||part==='..'))return null;
  const name=directory?'':parts.pop()??'';
  return {folder:parts.join('/'),name};
}

function ProjectBrowser({projectId,projectName,initialQuery,onPreview}:{projectId:string;projectName:string;initialQuery:string;onPreview:(file:string)=>void}){
  const initialPath=splitProjectPath(initialQuery);
  const [folder,setFolder]=useState(initialPath?.folder??'');
  const [query,setQuery]=useState(initialPath?.name??initialQuery);
  const result=useFileRequest<FolderReply>(`api/v1/work-files?${new URLSearchParams({project:projectId,folder})}`);
  const normalized=query.trim().toLocaleLowerCase();
  const typedPath=splitProjectPath(query);
  const entries=result?.value?.files.filter(row=>row.name.toLocaleLowerCase().includes(normalized));
  function openPath(){
    if(!typedPath)return;
    setFolder(typedPath.folder);
    setQuery(typedPath.name);
  }
  return <>
    {folder&&<FileHeading name={folder.split('/').at(-1)||folder} fullName={folder} backLabel="返回上一级目录" onBack={()=>{
      setFolder(folder.split('/').slice(0,-1).join('/'));
      setQuery('');
    }}/>}
    <div className="wk-quick-files-search-row">
      <label className="wk-quick-files-search"><Search aria-hidden="true"/>
        <input type="search" aria-label="搜索当前目录的文件" placeholder="搜索当前目录或输入相对路径" value={query}
          onChange={event=>setQuery(event.currentTarget.value)} onKeyDown={event=>{
            if(event.key==='Enter'&&typedPath&&!event.nativeEvent.isComposing){event.preventDefault();openPath();}
          }}/>
      </label>
      {typedPath&&<Button variant="outline" size="sm" type="button" onClick={openPath}>转到路径</Button>}
    </div>
    {!result&&<p className="wk-note" role="status">正在读取目录…</p>}
    {result?.error&&<p className="wk-error" role="alert">{result.error}</p>}
    {entries?.length===0&&<p className="wk-note">{normalized
      ? `在“${projectName} / ${folder||'根目录'}”${result?.value?.limited?'的前 500 项中':''}未找到匹配文件。可输入相对路径，或`
      : '当前目录没有文件。'}{normalized&&<a href={'#files/'+encodeURIComponent(projectId)}>浏览项目文件</a>}</p>}
    {entries?.map(row=><Button key={row.file} variant="ghost" type="button" className="wk-quick-file-row" title={row.file}
      onClick={()=>{if(row.directory){setFolder(row.file);setQuery('');}else onPreview(row.file);}}>
      {row.directory?<Folder aria-hidden="true"/>:<File aria-hidden="true"/>}<span>{row.name}</span>
    </Button>)}
    {result?.value?.limited&&<p className="wk-note">当前目录仅显示前 500 项。</p>}
  </>;
}

function WorkspacePreview({project,file,onBack}:{project:string;file:string;onBack:()=>void}){
  const params=new URLSearchParams({project,file});
  const result=useFileRequest<Preview>(`api/v1/file-inspect?${params}`);
  const value=result?.value;
  const name=file.split('/').at(-1)||file;
  if(value?.sha256)params.set('sha256',value.sha256);
  return <div className="wk-quick-file-preview">
    <FileHeading name={name} fullName={file} onBack={onBack}/>
    {!result&&<p className="wk-note" role="status">正在读取文件…</p>}
    {result?.error&&<p className="wk-error" role="alert">{result.error}</p>}
    {value&&<PreviewContents value={value} name={name} pdf={`api/file-preview?${params}`}/>}
  </div>;
}

function LocalPreview({file,onBack}:{file:File;onBack:()=>void}){
  const [value,setValue]=useState<Preview|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    if(file.size>localSizeLimit){setError('请选择 8 MB 以内的文件进行预览。');return;}
    const suffix=file.name.split('.').at(-1)?.toLowerCase()||'';
    const mime=Object.hasOwn(localImageTypes,suffix)?localImageTypes[suffix]:null;
    if(mime){
      const reader=new FileReader();
      reader.onload=()=>setValue({kind:'image',image:String(reader.result)});
      reader.onerror=()=>setError('读取本机文件失败。');
      reader.readAsDataURL(new Blob([file],{type:mime}));
      return ()=>{if(reader.readyState===FileReader.LOADING)reader.abort();};
    }
    let current=true;
    void file.arrayBuffer().then(bytes=>{
      if(!current)return;
      try {
        const content=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
        setValue(content.includes('\0')?{kind:'binary'}:{kind:'text',content});
      } catch {setValue({kind:'binary'});}
    },()=>{if(current)setError('读取本机文件失败。');});
    return ()=>{current=false;};
  },[file]);
  return <div className="wk-quick-file-preview">
    <FileHeading name={file.name} fullName={file.name} onBack={onBack}/>
    {error?<p className="wk-error" role="alert">{error}</p>:value?<PreviewContents value={value} name={file.name}/>:<p className="wk-note" role="status">正在读取本机文件…</p>}
  </div>;
}

export function QuickFiles({projectId,initialQuery='',onBack}:QuickFilesProps){
  const request=useFileRequest<{projects:Project[]}>('api/v1/workspace?view=workbench');
  const projects=request?.value?.projects.filter(row=>!row.removed);
  const [selectedProject,setSelectedProject]=useState('');
  const [workspaceFile,setWorkspaceFile]=useState<{project:string;file:string}|null>(null);
  const [localFile,setLocalFile]=useState<File|null>(null);
  const localPicker=useRef<HTMLInputElement>(null);
  const preferred=selectedProject||projectId||'';
  const project=(!projects||projects.some(row=>row.id===preferred)?preferred:'')||projects?.[0]?.id||'';
  const file=workspaceFile?.project===project?workspaceFile.file:'';
  return <section className="wk-quick-files" data-quick-files="" aria-label="文件">
    <input ref={localPicker} type="file" hidden onChange={event=>{
      const picked=event.currentTarget.files?.[0];
      if(picked)setLocalFile(picked);
      event.currentTarget.value='';
    }}/>
    {localFile&&<LocalPreview file={localFile} onBack={()=>setLocalFile(null)}/>}
    {file&&!localFile&&<WorkspacePreview project={project} file={file} onBack={()=>setWorkspaceFile(null)}/>}
    <div hidden={!!file||!!localFile}>
      <FileHeading name="文件" onBack={onBack}/>
      <div className="wk-quick-files-controls">
        <NativeSelect aria-label="选择文件所在的项目" value={project} disabled={!projects?.length} onChange={event=>{
          setSelectedProject(event.currentTarget.value);
          setWorkspaceFile(null);
        }}>
          {!projects?.length?<option value={project}>{project?'当前项目':request?'暂无项目':'正在读取项目…'}</option>:
            projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
        </NativeSelect>
        <Button variant="outline" type="button" onClick={()=>localPicker.current?.click()}><FolderOpen/>打开本机文件</Button>
      </div>
      {request?.error&&<p className="wk-error" role="alert">{request.error}</p>}
      {project&&<ProjectBrowser key={`${project}\u0000${initialQuery}`} projectId={project} projectName={projects?.find(row=>row.id===project)?.name||'当前项目'} initialQuery={initialQuery} onPreview={name=>setWorkspaceFile({project,file:name})}/>}
    </div>
  </section>;
}
