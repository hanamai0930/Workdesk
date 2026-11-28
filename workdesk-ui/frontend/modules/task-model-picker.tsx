import {useEffect, useState} from 'react';
import {Popover} from 'radix-ui';
import {Check, ChevronDown} from 'lucide-react';
import {coreClient} from '../core/client';
import type {PartnerRun} from '../core/partner-runtime';
import './work-model-picker.css';

export type TurnConnection = {
  id: string; revision: string; partner_id: string; route: string; model: string;
  source_label: string; account_label: string; available: boolean; reason?: string;
  model_label?: string; turn_models?: {id:string; name:string}[];
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
  const [connection,setConnection]=useState<TurnConnection>();
  const [error,setError]=useState('');
  useEffect(()=>{
    if (!open) return;
    const ctl=new AbortController();
    setConnection(undefined);setError('');
    coreClient.request<Catalog>('v1/workspace?view=workbench',{signal:ctl.signal}).then(data=>{
      if (ctl.signal.aborted) return;
      const current=data.partner_connections.connections.find(row=>row.id===run.connection_id
        && row.partner_id===run.partner_id && String(row.revision)===String(run.connection_snapshot?.revision));
      if (current?.available) setConnection(current);
      else setError('当前模型连接已变化或暂不可用。');
    }).catch(()=>{if(!ctl.signal.aborted)setError('暂时读不到模型列表，请重新打开。');});
    return ()=>ctl.abort();
  },[open,run.connection_id,run.partner_id,run.connection_snapshot?.revision]);
  const original=run.connection_snapshot?.model || 'official-default';
  const selected=choice?.model || original;
  const options=connection?.turn_models || (connection?[{id:connection.model,name:connection.model_label||connection.model}]:[]);
  const currentName=choice?.model==='official-default'?'默认':choice?.model_label
    || (original==='official-default'?(run.result?.model||run.model):original);
  const label=currentName && currentName!=='official-default' ? currentName : (partnerName(run.partner_id)||'当前模型');
  return <Popover.Root open={open&&!disabled} onOpenChange={setOpen}>
    <Popover.Trigger asChild><button type="button" role="combobox" aria-haspopup="listbox" aria-expanded={open&&!disabled}
      aria-label="选择下一条消息的模型" className="wd-talk-model-trigger" disabled={disabled}
      title={disabled?'回合结束后可切换模型':'选择下一条消息的模型'}>{label}<ChevronDown size={14}/></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="wk-model-picker wd-turn-model-menu" data-ui-module="workbench" side="top" align="end" sideOffset={8} collisionPadding={8}>
      <div className="wk-model-picker-note">选择模型</div>
      <div role="listbox" aria-label="选择模型" className="wk-model-picker-list">
        {connection && options.map(option=><button key={option.id} type="button" role="option" aria-selected={selected===option.id}
          onClick={()=>{onChoose(option.id===original?null:{...connection,model:option.id,model_label:option.name});setOpen(false);}}>
          <span>{option.id==='official-default'?'默认':option.name}</span>{selected===option.id&&<Check size={14}/>}
        </button>)}
      </div>
      {!connection&&<p className="wk-model-picker-note" role="status">{error || '正在读取模型…'}</p>}
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
