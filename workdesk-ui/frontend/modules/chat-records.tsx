import { MoreHorizontal, Pin, Square, SquareCheck, Trash2, X } from 'lucide-react';
import { Button } from '../components/ui/button';
import './chat-records.css';

export function ChatRecords({rows,active,selecting,selected,menuId}:any){
  const chosen=new Set(selected);
  return <div data-ui-module="workbench" style={{display:'contents'}}><section className="wk-side-section wd-chat-records" data-chat-records>
    <div className="wk-section-label wd-chat-records-heading"><span>{selecting?'已选择 '+chosen.size+' 项':'最近聊天'}</span>{selecting&&<Button variant="ghost" size="icon" className="wd-chat-select-close" data-wb="chat-record-finish" aria-label="完成多选"><X/></Button>}</div>
    {selecting&&<div className="wd-chat-batch"><Button variant="ghost" data-wb="chat-record-all">{rows.length&&rows.every((r:any)=>chosen.has(r.id))?'取消全选':'全选'}</Button><Button variant="ghost" data-wb="chat-record-delete-selected" disabled={!chosen.size}><Trash2/>删除所选</Button></div>}
    {rows.map((c:any)=><div key={c.id} className={'wd-chat-record-row '+(active===c.id?'is-active ':'')+(chosen.has(c.id)?'is-selected':'')} data-chat-id={c.id}>
      {selecting&&<button type="button" className="wd-chat-select" role="checkbox" aria-label={'选择聊天：'+c.title} aria-checked={chosen.has(c.id)} data-wb="chat-record-toggle" data-id={c.id}>{chosen.has(c.id)?<SquareCheck/>:<Square/>}</button>}
      {selecting?<button type="button" className="wk-record wd-chat-record-link" data-wb="chat-record-toggle" data-id={c.id}><span>{c.title}</span></button>:<a className="wk-record wd-chat-record-link" href={'#chat/'+c.id} title={c.title} aria-current={active===c.id?'page':undefined}><span>{c.title}</span>{c.pinned&&<Pin aria-label="已置顶"/>}</a>}
      {!selecting&&<Button type="button" variant="ghost" size="icon" className="wd-chat-record-more" data-wb-menu="chat-record" data-id={c.id} aria-label={'聊天操作：'+c.title} aria-haspopup="menu" aria-expanded={menuId===c.id}><MoreHorizontal/></Button>}
    </div>)}
    {!rows.length&&<Button variant="ghost" className="wk-empty-side" data-wb="new">开始新的对话</Button>}
  </section></div>;
}
