import { useEffect, useState, type RefObject } from 'react';

type NativeStatus={status:string;error?:string};
export function useDesktopChat(provider:string,url:string|undefined,active:boolean,slot:RefObject<HTMLDivElement|null>){
  const supported=!!(window as any).WorkdeskDesktop?.chatViews&&!!(window as any).chrome?.webview;
  const [state,setState]=useState<NativeStatus>({status:supported?'idle':'unavailable'});
  useEffect(()=>{
    if(!supported)return;
    const bridge=(window as any).chrome.webview;
    const receive=(event:any)=>{const m=event.data;if(m?.channel==='workdesk.chat'&&m.provider===provider)setState({status:m.status,error:m.error});};
    bridge.addEventListener('message',receive);
    if(!active||!slot.current){bridge.postMessage({channel:'workdesk.chat',action:'hide'});setState({status:'idle'});return()=>bridge.removeEventListener('message',receive);}
    setState({status:'loading'});let shown=false;
    const update=()=>{
      const r=slot.current?.getBoundingClientRect();
      const blocked=!!document.querySelector('dialog[open],[data-quota-popup]:popover-open')||!!document.getElementById('wb-overlays')?.childElementCount;
      if(!r||r.width<20||r.height<20||blocked){bridge.postMessage({channel:'workdesk.chat',action:'hide'});shown=false;return;}
      const summary=document.querySelector('[data-conversation-summary-panel]')?.getBoundingClientRect();
      const overlap=summary&&summary.left<r.right&&summary.right>r.left&&summary.top<r.bottom&&summary.bottom>r.top;
      const width=overlap?Math.min(r.width,summary.left-r.left-4):r.width;
      if(overlap&&width<160){bridge.postMessage({channel:'workdesk.chat',action:'hide'});shown=false;return;}
      bridge.postMessage({channel:'workdesk.chat',action:shown?'bounds':'show',provider,url,scale:window.devicePixelRatio,
        bounds:{x:r.x,y:r.y,width,height:r.height}});shown=true;
    };
    const frame=requestAnimationFrame(update),resize=new ResizeObserver(update);
    resize.observe(slot.current);window.addEventListener('resize',update);document.addEventListener('toggle',update,true);
    document.addEventListener('workdesk:summary-visibility',update);
    const overlays=document.getElementById('wb-overlays'),menus=new MutationObserver(update);if(overlays)menus.observe(overlays,{childList:true});
    return()=>{cancelAnimationFrame(frame);resize.disconnect();menus.disconnect();window.removeEventListener('resize',update);document.removeEventListener('toggle',update,true);document.removeEventListener('workdesk:summary-visibility',update);bridge.removeEventListener('message',receive);bridge.postMessage({channel:'workdesk.chat',action:'hide'});};
  },[provider,url,active,supported,slot]);
  return {...state,supported};
}
