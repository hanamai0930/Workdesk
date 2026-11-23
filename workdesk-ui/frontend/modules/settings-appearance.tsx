import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import type { ChangeEvent } from 'react';

function edit(event:ChangeEvent<HTMLInputElement>){event.stopPropagation();(window as any).WorkdeskAppearance?.edit(event.currentTarget.dataset.appearanceField,event.currentTarget.value);}
function select(event:ChangeEvent<HTMLSelectElement>){event.stopPropagation();document.dispatchEvent(new CustomEvent('workdesk:appearance-select',{detail:{field:event.currentTarget.dataset.appearanceSelect,value:event.currentTarget.value}}));}

export function AppearancePanel({a}:any) {
  const r=a?.draft?.themes?.[a.editTheme]?.regions?.[a.region];
  return <section className="wd-settings-group" aria-labelledby="wd-settings-appearance"><h2 id="wd-settings-appearance" className="wd-settings-group-title">外观 · 区域配色</h2><Card className="wd-settings-card wd-appearance gap-4 shadow-none">
    {!r?<><p role="status">{a?.status||'正在读取配色…'}</p><Button variant="outline" data-appearance="reload">重新读取</Button></>:<>
      {a.region.startsWith('chat.') && a.region!=='chat.api' && <p className="wd-appearance-note">官网区域可调整淡色覆盖层。</p>}
      <div className="wd-appearance-selectors"><label>配色主题<NativeSelect aria-label="配色主题" data-appearance-select="theme" value={a.editTheme} onChange={select}><option value="light">浅色</option><option value="dark">深色</option></NativeSelect></label><label>区域<NativeSelect aria-label="配色区域" data-appearance-select="region" value={a.region} onChange={select}>{a.regions.map((item:any)=><option key={item.id} value={item.id}>{item.label}</option>)}</NativeSelect></label></div>
      <div className="wd-appearance-fields">{[['background','背景'],['border','边框']].map(([field,label])=><fieldset key={field} disabled={a.busy}><legend>{label}</legend><div className="wd-appearance-color"><Input type="color" aria-label={label+'颜色选择器'} data-appearance-field={field} value={/^#[0-9A-F]{6}$/i.test(r[field])?r[field]:'#000000'} onChange={edit}/><Input aria-label={label+'颜色'} data-appearance-field={field} value={r[field]} maxLength={7} spellCheck={false} onChange={edit}/></div><label className="wd-appearance-opacity">透明度<Input type="range" aria-label={label+'透明度滑块'} data-appearance-field={field+'Opacity'} min={0} max={100} step={1} value={Number.isFinite(r[field+'Opacity'])?r[field+'Opacity']*100:0} onChange={edit}/><span><Input type="number" aria-label={label+'透明度百分比'} data-appearance-field={field+'Opacity'} min={0} max={100} step={1} value={Number.isFinite(r[field+'Opacity'])?Number((r[field+'Opacity']*100).toFixed(2)):''} onChange={edit}/>%</span></label></fieldset>)}</div>
      <div className="wd-appearance-actions"><Button variant="outline" data-appearance="preview" disabled={a.busy||!a.valid}>预览</Button><Button data-appearance="save" disabled={a.busy||!a.valid||!a.dirty||a.uncertain||a.conflict}>保存配色</Button><Button variant="outline" data-appearance="cancel" disabled={a.busy||(!a.dirty&&!a.preview)}>取消</Button><Button variant="ghost" data-appearance="reset-region" disabled={a.busy}>此区域恢复默认</Button><Button variant="ghost" data-appearance="reset-all" disabled={a.busy}>全部配色恢复默认</Button></div>
      {(a.status||a.conflict||a.uncertain) && <div className="wd-appearance-feedback">{a.status&&<p role="status" aria-live="polite">{a.status}</p>}{(a.conflict||a.uncertain)&&<Button variant="outline" data-appearance="reload" disabled={a.busy}>重新读取已保存配色</Button>}{a.pending&&!a.busy&&<details className="wd-settings-receipt"><summary>保存回执</summary><code>{a.pending}</code> · <a href="#control/receipts">查询回执</a></details>}</div>}
    </>}
  </Card></section>;
}
