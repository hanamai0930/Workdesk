import {useState} from 'react';

export type VersionRef = {kind:'job';source:string;entry:string;sha256:string};
export type DocumentInput = {files:(string|VersionRef)[];parameters:Record<string,unknown>};
const formats = {xlsx:'Excel 工作簿',csv:'CSV 表格',docx:'Word 文档',pptx:'PowerPoint 演示文稿',pdf:'PDF',md:'Markdown',tex:'LaTeX',html:'HTML'} as const;
type Format=keyof typeof formats;
export type VersionTemplate={ref:VersionRef;name:string;format:Format};
export function templateFormat(name:string):Format|null{
  const ext=name.split('.').pop()?.toLowerCase();
  return ext&&Object.hasOwn(formats,ext)?ext as Format:null;
}
type CellRow={id:number;address:string;value:string;kind:'text'|'number'|'formula'};
const emptyCell=(id:number):CellRow=>({id,address:'',value:'',kind:'number'});
function cellValues(rows:CellRow[]){
  const entries:Record<string,string|number>={};
  for(const row of rows){
    if(!row.address.trim()&&!row.value)continue;
    const address=row.address.trim().toUpperCase();
    if(!/^[A-Z]{1,3}[1-9]\d{0,6}$/.test(address))throw Error('请输入有效的单元格位置，例如 B2。');
    if(Object.hasOwn(entries,address))throw Error('单元格 '+address+' 重复。');
    if(row.kind==='number'&&(!row.value.trim()||!Number.isFinite(Number(row.value))))throw Error(address+' 需要有限数值。');
    if(row.kind==='formula'&&!row.value.startsWith('='))throw Error(address+' 的公式需要以等号开头。');
    entries[address]=row.kind==='number'?Number(row.value):row.value;
  }
  return entries;
}
function CellEditor({rows,change,expected=false}:{rows:CellRow[];change:(r:CellRow[])=>void;expected?:boolean}){
  function set(id:number,v:Partial<CellRow>){change(rows.map(r=>r.id===id?{...r,...v}:r));}
  return <section className="dt-cells"><strong>{expected?'重算后核对':'写入单元格'}</strong>
    {rows.map((r,i)=><div className="dt-cell" key={r.id}><label>位置<input placeholder="B2" aria-label={(expected?'核对':'写入')+'位置 '+(i+1)} value={r.address} onChange={e=>set(r.id,{address:e.target.value})}/></label>
      <label>类型<select value={r.kind} onChange={e=>set(r.id,{kind:e.target.value as CellRow['kind']})}><option value="number">数值</option>{!expected&&<><option value="text">文字</option><option value="formula">公式</option></>}</select></label>
      <label>{expected?'预期数值':'值或公式'}<input value={r.value} onChange={e=>set(r.id,{value:e.target.value})} placeholder={r.kind==='formula'?'=SUM(B2:B3)':undefined}/></label>
      {rows.length>1&&<button type="button" aria-label={'移除第 '+(i+1)+' 行'} onClick={()=>change(rows.filter(item=>item.id!==r.id))}>移除</button>}</div>)}
    <button type="button" onClick={()=>change([...rows,emptyCell((rows.at(-1)?.id||0)+1)])}>添加{expected?'核对项':'单元格'}</button></section>;
}
export type Capability = {ready:boolean;missing?:string[]};
export function DocumentForm({disabled,submit,capabilities,checked,template,clearTemplate}:{template:VersionTemplate|null;clearTemplate:()=>void;disabled:boolean;submit:(input:DocumentInput)=>void;capabilities:Record<string,Capability>;checked:boolean}){
  const [format,setFormat]=useState<Format>(template?.format||'xlsx'),[action,setAction]=useState<'create'|'edit'>(template?'edit':'create');
  const [name,setName]=useState(''),[path,setPath]=useState(''),[sheet,setSheet]=useState(''),[content,setContent]=useState('');
  const [from,setFrom]=useState(''),[to,setTo]=useState(''),[error,setError]=useState('');
  const [cells,setCells]=useState<CellRow[]>([emptyCell(1)]),[checks,setChecks]=useState<CellRow[]>([emptyCell(1)]);
  const spreadsheet=format==='xlsx'||format==='csv';
  const [pdfMode,setPdfMode]=useState('append'),[pages,setPages]=useState(''),[field,setField]=useState(''),[value,setValue]=useState('');
  const pdfEdit=action==='edit'&&format==='pdf';
  const [ocr,setOcr]=useState(false),[lang,setLang]=useState(''),[extract,setExtract]=useState(false),[previewPages,setPreviewPages]=useState('1');
  const replace=action==='edit'&&!spreadsheet&&!pdfEdit;
  const capability=capabilities[format];
  function start(){
    if(disabled||capability?.ready===false)return;
    setError('');
    try{
      const parameters:Record<string,unknown>={format,action};
      if(name.trim()){
        if(/[\\/:*?"<>|]/.test(name)||!name.toLowerCase().endsWith('.'+format))throw Error('请输入带 .'+format+' 扩展名的文件名。');
        parameters.name=name.trim();
      }
      if(spreadsheet){
        if(format==='xlsx'&&sheet.trim())parameters.sheet=sheet.trim();
        parameters.cells=cellValues(cells);
        if(format==='xlsx')parameters.expected=cellValues(checks);
        if(!Object.keys(parameters.cells as object).length)throw Error('请填写至少一个要写入的单元格。');
      }else if(replace){if(!from)throw Error('请填写要替换的原文。');parameters.replace={from,to};}
      else if(pdfEdit&&pdfMode==='pages'){
        const selected=pages.split(/[,，、\s]+/).filter(Boolean).map(Number);
        if(!selected.length||selected.some(n=>!Number.isSafeInteger(n)||n<1))throw Error('请输入从 1 开始的页码，用逗号分隔。');
        parameters.pages=selected;
      }else if(pdfEdit&&pdfMode==='fields'){if(!field.trim())throw Error('请输入表单字段名称。');parameters.fields={[field]:value};}
      else if(!(pdfEdit&&['merge','read'].includes(pdfMode)))parameters.content=content;
      if(format==='pdf'){
        const pp=previewPages.split(/[,，、\s]+/).filter(Boolean).map(Number);
        if(!pp.length||pp.length>10||pp.some(n=>!Number.isSafeInteger(n)||n<1))throw Error('请指定 1 至 10 个要预览的页码。');
        parameters.preview_pages=pp;
        if(ocr){parameters.ocr=true;if(lang)parameters.ocr_language=lang;}
        if(extract||pdfEdit&&pdfMode==='read'&&!ocr)parameters.extract_text=true;
      }
      const files=template?[template.ref]:action==='create'?[]:pdfEdit&&pdfMode==='merge'?path.split(/\r?\n/).map(s=>s.trim()).filter(Boolean):[path.trim()];
      if(pdfEdit&&pdfMode==='merge'&&(files.length<2||files.length>16))throw Error('合并需要 2 至 16 个项目内的 PDF 文件。');
      submit({files,parameters});
    }catch(e){setError(e instanceof Error?e.message:'请核对输入。');}
  }
  return <form onSubmit={e=>{e.preventDefault();start();}}><fieldset disabled={disabled}>
    <div className="dt-cols"><label>文件格式<select disabled={!!template} value={format} onChange={e=>{setFormat(e.target.value as Format);setError('');}}>{Object.entries(formats).map(([v,n])=><option key={v} value={v}>{n}</option>)}</select></label>
    <label>操作<select disabled={!!template} value={action} onChange={e=>setAction(e.target.value as typeof action)}><option value="create">新建文件</option><option value="edit">基于已有文件修改</option></select></label></div>
    {template&&<div className="dt-file"><strong>模板版本 · {template.name}</strong><small>SHA-256 {template.ref.sha256}</small><button type="button" onClick={clearTemplate}>更换模板</button></div>}
    {action==='edit'&&!template&&<label>{pdfEdit&&pdfMode==='merge'?'项目内 PDF 路径（每行一个，按合并顺序）':'项目内的模板路径'}{pdfEdit&&pdfMode==='merge'?<textarea required value={path} onChange={e=>setPath(e.target.value)}/>:<input required placeholder={'例如：模板/原件.'+format} value={path} onChange={e=>setPath(e.target.value)}/>}</label>}
    <label>成果文件名（可选）<input placeholder={'例如：成果.'+format} value={name} onChange={e=>setName(e.target.value)}/></label>
    {pdfEdit&&<label>PDF 操作<select value={pdfMode} onChange={e=>{setPdfMode(e.target.value);setPath('');}}><option value="append">追加文字页</option><option value="read">提取或识别文字</option><option value="pages">提取指定页面</option><option value="fields">填写表单字段</option>{!template&&<option value="merge">合并多个 PDF</option>}</select></label>}
    {format==='pdf'&&<><label>预览页码<input value={previewPages} onChange={e=>setPreviewPages(e.target.value)} placeholder="1, 2"/></label>
      <label className="dt-check"><input type="checkbox" checked={ocr} onChange={e=>setOcr(e.target.checked)}/>识别预览页中的图像文字（OCR）</label>
      {ocr&&<label>识别语言<select value={lang} onChange={e=>setLang(e.target.value)}><option value="">系统默认</option><option value="zh-Hans-CN">简体中文</option><option value="en-US">英语</option></select></label>}
      <label className="dt-check"><input type="checkbox" checked={extract||pdfEdit&&pdfMode==='read'&&!ocr} disabled={pdfEdit&&pdfMode==='read'&&!ocr} onChange={e=>setExtract(e.target.checked)}/>导出已有文本层</label></>}
    {spreadsheet?<>{format==='xlsx'&&<label>工作表（可选）<input placeholder="默认使用活动工作表" value={sheet} onChange={e=>setSheet(e.target.value)}/></label>}
      <CellEditor rows={cells} change={setCells}/>{format==='xlsx'&&<CellEditor rows={checks} change={setChecks} expected/>}</>
      :replace?<><label>查找原文<textarea required value={from} onChange={e=>setFrom(e.target.value)}/></label><label>替换为<textarea value={to} onChange={e=>setTo(e.target.value)}/></label></>
      :pdfEdit&&pdfMode==='pages'?<label>要保留的页码<input required value={pages} placeholder="1, 3, 4" onChange={e=>setPages(e.target.value)}/></label>
      :pdfEdit&&pdfMode==='fields'?<div className="dt-cols"><label>字段名称<input required value={field} onChange={e=>setField(e.target.value)}/></label><label>填写内容<input required value={value} onChange={e=>setValue(e.target.value)}/></label></div>
      :pdfEdit&&['merge','read'].includes(pdfMode)?null:<label>内容<textarea required value={content} onChange={e=>setContent(e.target.value)}/></label>}
    {error&&<p role="alert">{error}</p>}
    {capability?.ready===false?<p role="alert">缺少此格式的执行依赖：{capability.missing?.join('、')||'请检查开发实例的文档组件'}。</p>
      :(!checked||!capability)&&<p>此格式依赖状态待核对，实际操作会返回检查结果。</p>}
    <button type="submit" disabled={capability?.ready===false}>授权并生成新版本</button>
  </fieldset></form>;
}
