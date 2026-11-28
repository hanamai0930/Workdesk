import {useEffect, useState} from 'react';
import {Popover} from 'radix-ui';
import {Check, ChevronDown} from 'lucide-react';
import {coreClient} from '../core/client';
import type {PartnerRun} from '../core/partner-runtime';
import './work-model-picker.css';

export type TurnConnection = {
  id: string; revision: string; partner_id: string; route: string; model: string;
  source_label: string; account_label: string; available: boolean; reason?: string;
};
type Catalog = {partner_connections: {connections: TurnConnection[]}};
const partners: Record<string,string> = {codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen',claude:'Claude',gemini:'Gemini',grok:'Grok',kimi:'Kimi',glm:'GLM'};
export const partnerName = (id?:string) => id && (partners[id] || id);
export function turnModelName(provider?:string, model?:string) {
  const name=partnerName(provider);
  if (!model || model==='official-default') return name;
  return name && !model.toLowerCase().startsWith(name.toLowerCase()) ? name+' · '+model : model;
}

export function TurnModelPicker({run,choice,onChoose,disabled}:{
  run:PartnerRun; choice:TurnConnection|null; onChoose:(choice:TurnConnection|null)=>void; disabled:boolean;
}) {
  const [open,setOpen]=useState(false);
  const [rows,setRows]=useState<TurnConnection[]>();
  const [error,setError]=useState('');
  useEffect(()=>{
    if (!open) return;
    const ctl=new AbortController();
    setRows(undefined);setError('');
    coreClient.request<Catalog>('v1/workspace?view=workbench',{signal:ctl.signal}).then(data=>{
      if (!ctl.signal.aborted) setRows(data.partner_connections.connections);
    }).catch(()=>{if(!ctl.signal.aborted)setError('暂时读不到模型列表，请重新打开。');});
    return ()=>ctl.abort();
  },[open]);
  const current=run.connection_snapshot;
  const label=choice?turnModelName(choice.partner_id,choice.model):turnModelName(run.partner_id,current?.model || run.model);
  const isOriginal=(r:TurnConnection)=>r.id===run.connection_id && String(r.revision)===String(current?.revision);
  const isSelected=(row:TurnConnection)=>choice ? choice.id===row.id && choice.revision===row.revision : isOriginal(row);
  const choices=rows?.filter(row=>row.available || isSelected(row)).sort((a,b)=>
    Number(isSelected(b))-Number(isSelected(a)) || Number(b.partner_id===run.partner_id)-Number(a.partner_id===run.partner_id)
    || a.partner_id.localeCompare(b.partner_id) || a.model.localeCompare(b.model));
  return <Popover.Root open={open&&!disabled} onOpenChange={setOpen}>
    <Popover.Trigger asChild><button type="button" role="combobox" aria-haspopup="listbox" aria-expanded={open&&!disabled}
      aria-label="选择下一条消息的模型" className="wd-talk-model-trigger" disabled={disabled}
      title={disabled?'回合结束后可切换模型':'选择下一条消息的模型'}>{label||'当前模型'}<ChevronDown size={14}/></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="wk-model-picker wd-turn-model-menu" data-ui-module="workbench" side="top" align="end" sideOffset={8} collisionPadding={8}>
      <div role="listbox" aria-label="可用模型和连接" className="wk-model-picker-list">
        {choices?.map(row=>{
          const selected=isSelected(row);
          const multipleAccounts=choices.some(other=>other.id!==row.id && other.partner_id===row.partner_id && other.route===row.route && other.model===row.model && other.source_label===row.source_label);
          return <button key={row.id} type="button" role="option" aria-selected={selected} disabled={!row.available} title={row.account_label}
            onClick={()=>{onChoose(isOriginal(row)?null:row);setOpen(false);}}>
            <span>{turnModelName(row.partner_id,row.model)}<small>{row.source_label}{multipleAccounts?' · '+row.account_label:''}{row.available ? row.route==='api'?' · 按用量计费':'' : ' · '+row.reason}</small></span>
            {selected&&<Check size={14}/>}
          </button>;
        })}
      </div>
      {!choices?.length&&<p className="wk-model-picker-note" role="status">{error || (rows?'暂无可用的模型连接。':'正在读取模型…')}</p>}
      <p className="wk-model-picker-note"><a href="#settings/accounts">管理模型连接</a></p>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
