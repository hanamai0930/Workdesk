/* Shared appearance adapter. Values come only from the finite server registry. */
(() => {
  'use strict';
  const root=document.documentElement, clone=value=>JSON.parse(JSON.stringify(value));
  const a={saved:null,draft:null,defaults:null,regions:[],editTheme:root.dataset.theme||'light',region:'shell',preview:false,busy:false,status:'',pending:null,uncertain:false,conflict:false};
  let sequence=0;
  const rgb=color=>[1,3,5].map(n=>parseInt(color.slice(n,n+2),16));
  const validRegion=r=>r&&['background','border'].every(k=>/^#[0-9a-fA-F]{6}$/.test(r[k]))&&['backgroundOpacity','borderOpacity'].every(k=>typeof r[k]==='number'&&Number.isFinite(r[k])&&r[k]>=0&&r[k]<=1);
  const valid=value=>value&&['light','dark'].every(t=>a.regions.every(({id})=>validRegion(value.themes?.[t]?.regions?.[id])));
  const dirty=()=>!!a.saved&&JSON.stringify(a.saved.themes)!==JSON.stringify(a.draft?.themes);
  const notify=()=>document.dispatchEvent(new CustomEvent('workdesk:appearance'));
  function apply(){
    const value=a.preview?a.draft:a.saved;if(!valid(value))return;
    const regions=value.themes[root.dataset.theme==='dark'?'dark':'light'].regions;
    for(const {id} of a.regions){
      const r=regions[id],name=id.replaceAll('.','-');
      root.style.setProperty('--wd-region-'+name+'-bg',`rgba(${rgb(r.background).join(',')},${r.backgroundOpacity})`);
      root.style.setProperty('--wd-region-'+name+'-border',`rgba(${rgb(r.border).join(',')},${r.borderOpacity})`);
    }
    for(const provider of ['api','codex','deepseek','qwen']){
      const r=regions['chat.'+provider];if(!r)continue;
      root.style.setProperty('--wd-chat-'+provider+'-rgb',rgb(r.background).join(','));
      root.style.setProperty('--wd-chat-'+provider+'-opacity',String(r.backgroundOpacity));
      // WebView2 receives finite channels only, never CSS or executable text.
      if(provider!=='api'&&window.WorkdeskDesktop?.chatViews&&window.chrome?.webview)
        window.chrome.webview.postMessage({channel:'workdesk.chat',action:'appearance',provider,
          background:rgb(r.background),backgroundOpacity:r.backgroundOpacity,border:rgb(r.border),borderOpacity:r.borderOpacity});
    }
    const c=regions.composer;
    root.style.setProperty('--wd-chat-composer-bg',`rgba(${rgb(c.background).join(',')},${c.backgroundOpacity})`);
    root.dataset.appearance='ready';
  }
  async function load(discard=false){
    if(a.busy)return;
    const current=++sequence;
    const data=await api('v1/appearance');if(current!==sequence)return;
    const changed=a.saved&&a.saved.revision!==data.appearance.revision, keep=!discard&&dirty();
    a.defaults=data.defaults;a.regions=data.regions;a.saved=data.appearance;
    if(!keep){a.draft=clone(a.saved);a.preview=false;a.conflict=false;}
    else if(changed){a.conflict=true;a.status='另一窗口已保存配色。本窗口修改仍保留，重新读取后可继续。';}
    if(discard){a.uncertain=false;a.pending=null;a.status='已读取保存的配色。';}
    apply();notify();return data;
  }
  function edit(field,raw){
    if(a.busy||!a.draft)return;
    const region=a.draft.themes[a.editTheme].regions[a.region];
    if(!Object.hasOwn(region,field))return;
    region[field]=field.endsWith('Opacity')?(raw===''?NaN:Number(raw)/100):raw.toUpperCase();
    a.status=valid(a.draft)?'修改尚未保存。':'颜色须为 #RRGGBB，透明度须为0至100%。';
    if(a.preview)apply();notify();
  }
  function cancel(){a.draft=clone(a.saved);a.preview=false;a.conflict=false;a.status='已取消预览与未保存修改。';apply();notify();}
  function reset(all=false){
    if(!a.draft||a.busy)return;
    if(all)a.draft.themes=clone(a.defaults.themes);
    else a.draft.themes[a.editTheme].regions[a.region]=clone(a.defaults.themes[a.editTheme].regions[a.region]);
    a.status=all?'全部配色已恢复默认，保存后生效。':'此区域已恢复默认，保存后生效。';
    if(a.preview)apply();notify();
  }
  async function save(){
    if(a.busy||a.uncertain||!valid(a.draft)||!dirty())return;
    ++sequence;a.busy=true;a.status='正在保存…';a.pending=crypto.randomUUID();notify();
    try{
      const result=await api('v1/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:a.pending,kind:'settings.appearance.save',payload:a.draft})});
      a.saved=result.receipt.appearance;a.draft=clone(a.saved);a.preview=false;a.conflict=false;a.pending=null;
      a.status='配色已保存。';apply();
      try{channel?.postMessage({revision:a.saved.revision});}catch{}
    }catch(error){
      a.uncertain=!error.status||error.status>=500;
      a.conflict=String(error.message).includes('另一窗口');
      a.status=error.message+(a.uncertain?'；保存结果待核对，请查询回执或重新读取。':'');
    }finally{a.busy=false;notify();}
  }
  let channel=null;
  try{channel=new BroadcastChannel('workdesk.appearance');channel.onmessage=()=>load().catch(()=>{});}catch{}
  new MutationObserver(()=>{if(!dirty())a.editTheme=root.dataset.theme==='dark'?'dark':'light';apply();notify();}).observe(root,{attributes:true,attributeFilter:['data-theme']});
  window.addEventListener('focus',()=>load().catch(()=>{}));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load().catch(()=>{});});
  window.WorkdeskAppearance={load,edit,cancel,reset,save,apply,
    snapshot:()=>({...a,dirty:dirty(),valid:!!valid(a.draft)}),
    selectRegion:id=>{if(a.regions.some(r=>r.id===id)){a.region=id;notify();}},
    selectTheme:t=>{if(['light','dark'].includes(t)){a.editTheme=t;notify();}},
    preview:()=>{if(valid(a.draft)){a.preview=true;a.status='正在预览，保存后保留。';apply();notify();}}};
  load().catch(error=>{a.status='配色读取失败：'+error.message;notify();});
})();
