import { ArrowLeft, ArrowRight, PanelLeft, House, LibraryBig, ClipboardList, Plug, MoreHorizontal, Search, SquarePen, Plus, Save, Bell, Settings, CircleHelp, Gauge, RefreshCw } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Separator } from '../components/ui/separator';
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Usage } from './settings-usage';
import { formatMoney } from '../lib/format';
import { coreClient, type InstanceIdentity } from '../core/client';

const quotaNumber=new Intl.NumberFormat('zh-CN');
const quotaTime=new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'});

const CoreWorkspace = lazy(() => import('../core/workspace'));

function DevelopmentIdentity(){
  const development=document.documentElement.dataset.workdeskChannel==='development';
  const [instance,setInstance]=useState<InstanceIdentity|null>(null);
  const [failed,setFailed]=useState(false);
  const [revision,reload]=useState(0);
  const [open,setOpen]=useState(false);
  const panel=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    if(!development)return;
    const controller=new AbortController();
    setFailed(false);
    coreClient.loadIdentity(controller.signal)
      .then(result=>{
        if(result.schema!==1||result.channel!=='development'||typeof result.program_root!=='string')throw Error();
        if(!controller.signal.aborted)setInstance(result);
      })
      .catch(()=>{if(!controller.signal.aborted)setFailed(true);});
    return ()=>controller.abort();
  },[development,revision]);
  useEffect(()=>{if(development)document.title='Workdesk · 开发';},[development]);
  if(!development)return null;
  const fields=instance?[
    ['程序位置',instance.program_root],['用户资料',instance.user_root],['活动资料库',instance.data_root],
    ['源码版本',instance.source_revision+(instance.source_dirty?' · 含未提交修改':'')],
    ['实例身份',instance.instance_id],['当前服务',instance.process_instance],
    ['运行实现',instance.implementation_revision],['控制协议',String(instance.protocol)],
    ...(instance.core_engine?[['应用核心',instance.core_engine==='go'?'Go':instance.core_engine],['资料格式',String(instance.storage_schema??'—')]]:[]),
  ]:[];
  return <>
    <section className="wd-instance-bar" data-instance-channel="development" aria-label="开发实例">
      <Button type="button" variant="outline" size="xs" aria-haspopup={instance?'dialog':undefined} aria-controls={instance?'wd-instance-dialog':undefined}
        disabled={!instance&&!failed} onClick={()=>{if(instance){setOpen(true);panel.current?.showModal();}else reload(value=>value+1);}}>开发<span className="wd-instance-more">{instance?' · 实例详情':failed?' · 重试核对':''}</span></Button>
      {instance?<span className="wd-instance-location" title={instance.program_root}>开发源码目录：{instance.program_root}</span>:
        <span role="status">{failed?'暂未核对实例身份，点击开发重试。':'正在核对开发实例…'}</span>}
    </section>
    {instance&&<dialog id="wd-instance-dialog" ref={panel} className="wd-instance-dialog" aria-labelledby="wd-instance-title" onClose={()=>setOpen(false)}>
      <header><h2 id="wd-instance-title">Workdesk · 开发实例</h2>
        <Button type="button" variant="ghost" size="sm" onClick={()=>panel.current?.close()} aria-label="关闭实例详情">关闭</Button>
      </header>
      <dl>{fields.slice(0,3).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'尚未取得'}</dd></div>)}</dl>
      <details><summary>版本与服务身份</summary><dl>{fields.slice(3).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'尚未取得'}</dd></div>)}</dl></details>
      {open&&<Suspense fallback={<p role="status">正在打开本地工具与记录…</p>}><CoreWorkspace instance={instance}/></Suspense>}
      <p>任务执行位置以任务中选择的工作目录为准。</p>
    </dialog>}
  </>;
}

function QuotaPopup(){
  const panel=useRef<HTMLDivElement>(null);
  const [open,setOpen]=useState(false);
  const [data,setData]=useState<Usage|null>(null);
  const [revision,reload]=useState(0);
  const [loading,setLoading]=useState(false);
  const [message,setMessage]=useState('');
  const [readState,setReadState]=useState<'reading'|'ready'|'failed'>('reading');
  useEffect(()=>{
    if(!open)return;
    const controller=new AbortController();
    setReadState('reading');
    fetch('api/v1/settings-usage?days=7',{signal:controller.signal,cache:'no-store'})
      .then(response=>{if(!response.ok)throw Error();return response.json() as Promise<Usage>;})
      .then(result=>{if(!controller.signal.aborted){setData(result);setReadState('ready');}})
      .catch(()=>{if(!controller.signal.aborted)setReadState('failed');});
    const close=()=>panel.current?.hidePopover();
    window.addEventListener('hashchange',close);
    return ()=>{controller.abort();window.removeEventListener('hashchange',close);};
  },[open,revision]);
  async function refresh(){
    if(loading)return;
    if(!data||readState==='failed'){setMessage('');reload(value=>value+1);return;}
    setLoading(true);
    setMessage('');
    const requests=data.quotas.flatMap(quota=>[
      ...(quota.refresh?[{route:'resource-quota-check',body:quota.refresh}]:[]),
      ...(quota.console_snapshots??[]).map(snapshot=>({route:'settings-qwen-quota',body:{source:snapshot.source}})),
    ]);
    const results=await Promise.allSettled(requests.map(async ({route,body})=>{
      const response=await fetch('api/v1/'+route,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!response.ok)throw Error();
      const result=await response.json();
      if(result.observation?.status==='FAILED')throw Error();
    }));
    if(results.some(result=>result.status==='rejected'))setMessage('部分额度刷新失败，请查看明细。');
    setLoading(false);
    reload(value=>value+1);
  }
  return <>
    <Button type="button" variant="ghost" className="wk-rail-button" aria-label="额度" title="额度"
      popoverTarget="wd-quota-popup" aria-haspopup="dialog" aria-expanded={open}><Gauge/></Button>
    <div id="wd-quota-popup" ref={panel} popover="auto" role="dialog" aria-label="余额与用量" data-quota-popup=""
      onToggle={e=>{const visible=e.currentTarget.matches(':popover-open');setOpen(visible);if(!visible)setMessage('');}}>
      <header><strong>余额与用量</strong>
        <Button type="button" variant="ghost" size="icon" className="wk-icon" onClick={refresh}
          aria-label="刷新额度" title="刷新额度" disabled={loading||readState==='reading'}>
          <RefreshCw/></Button>
      </header>
      {readState==='reading'&&<p role="status">正在读取额度…</p>}
      {readState==='failed'&&<p role="status">{data?'读取失败，以下为上次读取的额度。':'暂时无法读取额度。'} 可点击刷新重试。</p>}
      {data?.quotas.map(quota=>{
        const buckets=quota.buckets??[];
        const main=buckets.find(bucket=>bucket.limit_id==='codex')??buckets[0];
        const windows=[main?.primary,main?.secondary].filter(window=>window?.remaining_percent!=null);
        const free=quota.console_snapshots?.find(snapshot=>snapshot.source==='free');
        const cash=quota.console_snapshots?.find(snapshot=>snapshot.source==='cash');
        const input=quota.usage?.input_tokens,output=quota.usage?.output_tokens;
        const tokenUsage=input!=null||output!=null?quotaNumber.format((input??0)+(output??0))+' Tokens':null;
        const lines: {text:string;exact?:string;secondary?:boolean}[]=[
          ...windows.map(window=>({text:`${window?.duration_minutes===10080?'每周':window?.duration_minutes===300?'5 小时':'订阅'}剩余 ${quotaNumber.format(window!.remaining_percent!)}%`,exact:undefined})),
          ...(main?.credits?.balance!=null?[{text:quotaNumber.format(main.credits.balance)+' Credits',exact:undefined}]:[]),
          ...(quota.balances??[]).map(balance=>({text:'可用金额 '+formatMoney(balance.total_balance,balance.currency),exact:balance.currency+' '+balance.total_balance})),
          ...(cash?[
            {text:'可用额度 '+formatMoney(cash.rows.available,cash.rows.currency),exact:cash.rows.currency+' '+cash.rows.available},
            {text:'现金余额 '+formatMoney(cash.rows.cash,cash.rows.currency),exact:cash.rows.currency+' '+cash.rows.cash,secondary:true},
            {text:'阿里云账单 · '+quotaTime.format(cash.observed_at*1000)+' 查询',secondary:true},
          ]:[]),
          ...(free?.rows??[]).flatMap(row=>row.remaining==null?[]:[{text:`${row.model} 剩余 ${quotaNumber.format(row.remaining)} Tokens`,exact:undefined}]),
          ...(free?[{text:'百炼免费额度 · '+quotaTime.format(free.observed_at*1000)+' 查询',secondary:true}]:[]),
          ...(quota.official_at!=null?[{text:'官方查询 · '+quotaTime.format(quota.official_at*1000),secondary:true}]:[]),
        ];
        const empty=quota.refresh_status==='FAILED'?'最近查询失败，尚无官方读数':
          quota.provider==='qwen'?(quota.console_sources?.length?'尚未查询，请在明细中查询官方额度':'请在明细中查看查询工具与连接配置'):
          quota.refresh?'尚未读取官方额度，可刷新查询':'尚未配置可查询的连接，请查看明细';
        return <div key={quota.provider} className="wd-quota-popup-row">
          <strong>{quota.label}</strong><div>
            {lines.length?lines.map(line=>line.secondary?<small key={line.text} title={line.exact}>{line.text}</small>:<span key={line.text} title={line.exact}>{line.text}</span>):
              <span>{empty}</span>}
            {lines.length>0&&quota.refresh_status==='FAILED'&&<small>最近刷新失败，以上为上次查询结果</small>}
            {tokenUsage&&<small>近 7 天已记录 {tokenUsage}</small>}
          </div>
        </div>;
      })}
      {message&&<p role="status">{message}</p>}
      <a href="#settings/usage" onClick={()=>panel.current?.hidePopover()}>用量与预算明细</a>
    </div>
  </>;
}

function SidebarResize() {
  const clamp=(n:number)=>Math.max(180,Math.min(n,Math.min(520,window.innerWidth-420)));
  const [width,setWidth]=useState(()=>{try{return clamp(Number(localStorage.getItem('workdesk.sidebar.width'))||Math.max(200,Math.min(innerWidth*.148,232)));}catch{return 212;}});
  useLayoutEffect(()=>{document.documentElement.style.setProperty('--wk-sidebar-width',width+'px');},[width]);
  const save=(n:number)=>{setWidth(clamp(n));try{localStorage.setItem('workdesk.sidebar.width',String(clamp(n)));}catch{/* local settings may be unavailable */}};
  return <div className="wk-sidebar-resize" role="separator" aria-label="调整侧栏宽度" aria-orientation="vertical" aria-valuenow={Math.round(width)} aria-valuemin={180} aria-valuemax={Math.min(520,innerWidth-420)} tabIndex={0}
    onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);document.body.dataset.sidebarResizing='true';}}
    onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId)){const left=document.getElementById('sidebar')!.getBoundingClientRect().left;save(e.clientX-left);}}}
    onPointerUp={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);delete document.body.dataset.sidebarResizing;}}
    onLostPointerCapture={()=>{delete document.body.dataset.sidebarResizing;}}
    onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();save(e.key==='Home'?180:e.key==='End'?520:width+(e.key==='ArrowLeft'?-16:16));}}}
    onDoubleClick={()=>save(212)}/>;
}

export function Shell() {
  return <>
    <div className="wk-topbar">
      <Button variant="ghost" size="icon" className="wk-icon" data-wb="back" aria-label="后退"><ArrowLeft/></Button>
      <Button variant="ghost" size="icon" className="wk-icon" data-wb="forward" aria-label="前进"><ArrowRight/></Button>
      <Button variant="ghost" size="icon" className="wk-icon" data-wb="sidebar" aria-label="显示或收起侧栏" aria-controls="sidebar" aria-expanded="false"><PanelLeft/></Button>
      <nav className="wk-menubar" aria-label="应用菜单">{[['file','文件'],['edit','编辑'],['view','视图'],['help','帮助']].map(([key,label]) => <Button key={key} variant="ghost" className="wk-top-menu" data-wb-menu={key} aria-haspopup="menu" aria-expanded="false">{label}</Button>)}</nav>
      <div className="wk-topbar-workspace"><span id="wb-title" className="wk-header-title"/>
        <div className="wk-mode-tabs" aria-label="对话模式"><a href="#chat" id="wb-chat-tab">Chat</a><a href="#work" id="wb-work-tab">Work</a></div>
        <div className="wk-header-actions" id="wb-header-actions"><Button variant="ghost" size="icon" className="wk-icon" data-wb="new" aria-label="新建对话"><Plus/></Button></div>
      </div>
    </div>
    <DevelopmentIdentity/>
    <div className="wk-shell">
      <nav className="wk-rail" aria-label="功能分区">
        <div className="wk-rail-group">
          <a className="wk-rail-button" href="#chat" aria-label="主页" title="主页"><House/></a>
          <a className="wk-rail-button" href="#resources" data-resource-entry aria-label="资料空间" title="资料空间"><LibraryBig/></a>
          <a className="wk-rail-button" href="#records" data-management-entry aria-label="工作与记录" title="工作与记录"><ClipboardList/></a>
        </div><Separator className="wk-rail-divider"/>
        <div className="wk-rail-group"><a className="wk-rail-button" href="#settings/extensions" aria-label="插件与 Skills" title="插件与 Skills"><Plug/></a><Button variant="ghost" className="wk-rail-button" data-wb-menu="more" aria-label="更多功能" title="更多功能" aria-haspopup="menu" aria-expanded="false"><MoreHorizontal/></Button></div>
        <div className="wk-rail-footer">
          <a className="wk-rail-button" href="#settings/diagnostics" aria-label="帮助与诊断" title="帮助与诊断"><CircleHelp/></a>
          <a className="wk-rail-button" href="#settings/notifications" aria-label="任务通知" title="任务通知"><Bell/></a>
          <QuotaPopup/>
          <a className="wk-rail-button" href="#settings" aria-label="设置" title="设置"><Settings/></a>
          <Button variant="ghost" className="wk-account" data-wb-menu="app" aria-haspopup="menu" aria-expanded="false" aria-label="应用与设置">W</Button>
        </div>
      </nav>
      <aside className="wk-sidebar" id="sidebar" aria-label="项目与对话">
        <div className="wk-brand">Workdesk<Button variant="ghost" size="icon" className="wk-icon" data-wb="search" aria-label="搜索对话"><Search/></Button><Button variant="ghost" size="icon" className="wk-icon wk-sidebar-close" data-wb="sidebar" aria-label="关闭导航侧栏"><PanelLeft/></Button></div>
        <Button variant="ghost" className="wk-new" data-wb="new"><SquarePen/><span id="wb-new-label">新聊天</span></Button>
        <div id="wb-sidebar"/><div className="wk-sidebar-footer"><Save/><span>本地工作台</span></div>
        <div id="wb-settings-sidebar" data-ui-module="settings"/>
      </aside>
      <SidebarResize/>
      <section className="wk-main">
        <div id="wb-section-label" className="wk-location-label" aria-label="当前分区" hidden/>
        <div data-management-navigation hidden/>
        <main id="main" tabIndex={-1}><div className="loading">正在读取工作台…</div></main>
      </section>
    </div>
    <div hidden aria-hidden="true"><span id="task-count"/><div id="recent-nav"/><span id="breadcrumb"/><span id="load-status"/><button data-action="theme"/></div>
    <dialog id="search-dialog" className="search-dialog" aria-label="搜索已保存记录"><div className="search-field"><Search/><input id="search-input" placeholder="搜索已保存记录…" aria-label="搜索已保存记录"/><kbd>Esc</kbd></div><div id="search-results" className="search-results"/><div className="search-foot">仅搜索本地记录</div></dialog>
    <dialog id="confirm-dialog" className="confirm-dialog" aria-labelledby="wd-stop-confirm-title"><h2 id="wd-stop-confirm-title">登记停止请求？</h2><p>向所选任务登记停止意图，随后核对后台中断和退出记录。</p><div className="dialog-actions"><Button variant="outline" data-action="cancel-stop">取消</Button><Button variant="destructive" data-action="confirm-stop">登记停止请求</Button></div></dialog>
    <dialog id="wb-dialog" className="wb-dialog"/><div id="wb-overlays"/><div className="toast" id="toast" role="status" aria-live="polite"/>
  </>;
}
