import { useEffect, useState } from 'react';
import { Popover, Slider } from 'radix-ui';
import { Brain, Check, ChevronRight, RotateCcw, Zap } from 'lucide-react';
import { Button } from '../components/ui/button';
import './work-model-picker.css';

const effortNames:Record<string,string>={off:'关闭思考',none:'关闭思考',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'极高',ultra:'最高'};
const modelName=(id:string)=>id.replace(/^gpt-/i,'GPT-').replace(/-(sol|luna|astra|terra)$/i,(_,name)=>' '+name[0].toUpperCase()+name.slice(1));

function PartnerConnectionPicker({v,disabled}:any){
  const [open,setOpen]=useState(false);
  const rows=(v.data.partner_connections.connections||[]).filter((row:any)=>row.partner_id===v.provider);
  const selected=rows.find((row:any)=>row.id===v.connectionId),changed=selected&&String(selected.revision)!==String(v.connectionRevision);
  const source=(row:any)=>row.route==='api'?(row.api_name||row.api_provider)+' API':'官方账号';
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger asChild><Button id="wb-model" type="button" variant="ghost" role="combobox" aria-label="选择工作连接与模型" aria-expanded={open} disabled={disabled} className="wk-model-picker-trigger">
      <span className="wk-model-picker-level">{selected?source(selected)+' · '+selected.account_label:'选择已保存的连接'}</span>
      <span className="wk-model-picker-name">{changed?'连接已更新，请重新选择':selected?.model_label||selected?.model||'官方默认模型'}<ChevronRight/></span>
    </Button></Popover.Trigger>
    <Popover.Portal><Popover.Content data-ui-module="workbench" className="wk-model-picker" side="top" align="end" sideOffset={10} collisionPadding={12} aria-label="工作连接与模型">
      <div className="wk-model-picker-list" role="listbox" aria-label="工作连接">
        {rows.map((row:any)=><button type="button" key={row.id} role="option" aria-selected={selected?.id===row.id&&!changed} disabled={!row.available} title={row.reason||''} onClick={()=>{
          window.WorkdeskWorkbench.selectIndependentConfig({connectionId:row.id,connectionRevision:String(row.revision),model:row.model||'official-default',workEffort:'default',workTier:'standard'});setOpen(false);
        }}><Brain/><span>{row.model_label||row.model||'官方默认模型'}<small>{source(row)} · {row.account_label}{!row.available?' · '+(row.reason||'暂不可用'):''}</small></span>{selected?.id===row.id&&!changed&&<Check/>}</button>)}
      </div>
      {!rows.length&&<p className="wk-model-picker-note">尚未保存此工作端的连接。</p>}
      <p className="wk-model-picker-note"><a href="#settings/accounts">账号与模型</a></p>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

/** Explicit native model/effort choices. No change is sent until the next turn. */
export function WorkModelPicker(props:any){
  if(!props.v.detail&&props.v.data?.partner_connections)return <PartnerConnectionPicker {...props}/>;
  return <NativeModelPicker {...props}/>;
}
function NativeModelPicker({v,profile,disabled}:any){
  const [open,setOpen]=useState(false);
  const entry=profile?.model_catalog?.models.find((m:any)=>m.model===v.model);
  const unavailableModels=(profile?.model_catalog?.models||[]).filter((m:any)=>m.access_status==='NOT_IN_CURRENT_ACCOUNT_CATALOG');
  const efforts:string[]=(entry?.selection_efforts||entry?.efforts||[profile?.effort]).filter(Boolean);
  const selected=efforts.includes(v.workEffort)?v.workEffort:efforts.includes(profile?.effort)?profile.effort:efforts[0];
  const [preview,setPreview]=useState(Math.max(0,efforts.indexOf(selected)));
  useEffect(()=>setPreview(Math.max(0,efforts.indexOf(selected))),[v.model,selected,open]);
  const qwen=v.provider==='qwen';
  const pick=(values:any)=>window.WorkdeskWorkbench.selectIndependentConfig(values);
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger asChild><Button id="wb-model" type="button" variant="ghost" role="combobox" aria-label="选择工作模型" aria-expanded={open} disabled={disabled} className="wk-model-picker-trigger" title="选择下一回合的模型和思考强度"><span className="wk-model-picker-level">{qwen?'官方思考配置':effortNames[selected]||selected}</span><span className="wk-model-picker-name">{modelName(v.model||'正在载入模型')}<ChevronRight/></span></Button></Popover.Trigger>
    <Popover.Portal><Popover.Content data-ui-module="workbench" className="wk-model-picker" side="top" align="end" sideOffset={10} collisionPadding={12} aria-label="模型和思考配置">
      <div className="wk-model-picker-top"><Zap/><span>{qwen?'官方思考配置':effortNames[efforts[preview]]||efforts[preview]}</span><Button type="button" variant="ghost" size="icon" aria-label="恢复默认思考强度" disabled={qwen||!efforts.length} onClick={()=>{const next=efforts.includes(profile.effort)?profile.effort:efforts[0];setPreview(efforts.indexOf(next));pick({workEffort:next});}}><RotateCcw/></Button></div>
      {!qwen&&efforts.length>1?<>
        <Slider.Root className="wk-effort-slider" aria-label="思考强度" value={[preview]} min={0} max={efforts.length-1} step={1} onValueChange={value=>setPreview(value[0])} onValueCommit={value=>pick({workEffort:efforts[value[0]]})}>
          <Slider.Track className="wk-effort-track"><Slider.Range className="wk-effort-range"/>{efforts.map((e,i)=><span key={e} className="wk-effort-dot" style={{left:`${i/(efforts.length-1)*100}%`}}/>)}</Slider.Track><Slider.Thumb className="wk-effort-thumb" aria-label="思考强度" aria-valuetext={effortNames[efforts[preview]]||efforts[preview]}/>
        </Slider.Root><div className="wk-effort-labels">{efforts.map((e,i)=><button type="button" key={e} aria-pressed={preview===i} onClick={()=>{setPreview(i);pick({workEffort:e});}}>{effortNames[e]||e}</button>)}</div>
      </>:<p className="wk-model-picker-note">{qwen?'当前官方 SDK 沿用原生思考设置，尚不支持完整开关。':'此模型使用固定的官方思考配置。'}</p>}
      <div className="wk-model-picker-divider"/><div className="wk-model-picker-list" role="listbox" aria-label="工作模型">
        {(profile?.enabled_models||profile?.models||[]).map((model:string)=><button type="button" key={model} role="option" aria-selected={v.model===model} onClick={()=>{pick({model,workEffort:''});setOpen(false);}}><Brain/><span>{modelName(model)}</span>{v.model===model&&<Check/>}</button>)}
        {unavailableModels.map((model:any)=><button type="button" key={model.model} role="option" aria-selected={false} disabled><Brain/><span>{model.display_name||modelName(model.model)} · 账号目录未列出</span></button>)}
      </div><p className="wk-model-picker-note">应用到下一回合</p>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
