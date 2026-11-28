/* Page ownership and mounting. Shared chrome is deliberately outside page hosts. */
(() => {
  'use strict';
  const definitions=Object.freeze({
    connect:{routes:['connect'],scripts:[],styles:['../frontend/modules/connection-setup.css'],sources:['../frontend/modules/connection-setup.tsx']},
    resources:{routes:['resources'],scripts:[],styles:['../frontend/modules/resource-space.css'],sources:['../frontend/modules/resource-space.tsx','../frontend/modules/resource-knowledge.tsx','../frontend/modules/project-memory.tsx']},
    workitems:{routes:['work-items'],scripts:['work-items-page.js','cooperation-toolbar.js'],styles:['work-items-page.css','cooperation-toolbar.css','conversation-detail.css'],sources:['../frontend/modules/work-item-file-preview.tsx','conversation-detail.js','chat-detail-data.js','work-detail-data.js']},
    files:{routes:['files'],scripts:['files-page.js'],styles:['files-page.css']},
    records:{routes:['records'],scripts:['records-page.js'],styles:['records-page.css']},
    results:{routes:['results'],scripts:['results-page.js'],styles:['results-page.css']},
    monitor:{routes:['monitor'],scripts:['monitor-page.js'],styles:['monitor-page.css']},
    projects:{routes:['projects'],scripts:['projects-page.js'],styles:['projects-page.css']},
    planning:{routes:['planning'],scripts:['planning-page.js'],styles:['planning-page.css']},
    libraries:{routes:['libraries'],scripts:['libraries-page.js'],styles:['libraries-page.css']},
    settings:{routes:['settings'],scripts:['settings.js'],styles:['settings.css','../frontend/modules/settings.css','../frontend/modules/settings-accounts.css','../frontend/modules/settings-usage.css'],sources:['../frontend/modules/settings.tsx','../frontend/modules/settings-appearance.tsx','../frontend/modules/settings-accounts.tsx','../frontend/modules/settings-usage.tsx','../frontend/modules/settings-extensions.tsx']},
    management:{routes:['management'],scripts:['management.js'],styles:['../frontend/modules/management.css'],sources:['../frontend/modules/management.tsx']},
    extensions:{routes:['extensions'],scripts:['extensions.js'],styles:['extensions.css']},
    control:{routes:['control'],scripts:['control.js'],styles:['control.css']},
    collaboration:{routes:['collab'],scripts:['collaboration.js'],styles:['collaboration-ledger.css']},
    operations:{routes:['operations'],scripts:['operations.js'],styles:['operations.css']},
    workbench:{routes:['','home','chat','work','drafts','archives','schedules'],scripts:['workbench.js','workbench-features.js','independent-work.js','work-records.js','chat.js','chat-api.js','chat-records.js'],styles:['../frontend/modules/native-work.css','../frontend/modules/work-files.css','../frontend/modules/work-projects.css','../frontend/modules/work-records.css','../frontend/modules/conversation.css','../frontend/modules/independent-work.css','../frontend/modules/chat.css','../frontend/modules/api-chat.css','../frontend/modules/chat-records.css'],sources:['../frontend/modules/native-events.ts','../frontend/modules/native-work.tsx','../frontend/modules/work-records.tsx','../frontend/modules/conversation.tsx','../frontend/modules/independent-work.tsx','../frontend/modules/chat.tsx','../frontend/modules/desktop-chat.ts','../frontend/modules/api-chat.tsx','../frontend/modules/chat-records.tsx']},
    history:{routes:['tasks','task','providers','history'],scripts:['app.js'],styles:[]}
  });
  definitions.projects.sources=['../frontend/core/tool-schedules.tsx','../frontend/core/project-archive.tsx','../frontend/core/computer-tools.tsx','../frontend/core/document-tools.tsx','../frontend/core/document-form.tsx'];
  definitions.projects.styles.push('../frontend/core/document-tools.css');
  definitions.settings.sources.push('../frontend/modules/settings-token-usage.tsx','../frontend/modules/settings-inbox.tsx','../frontend/modules/settings-project-budget.tsx');
  definitions.settings.styles.push('../frontend/modules/settings-token-usage.css');
  definitions.settings.sources.push('../frontend/modules/partners.tsx');
  definitions.connect.sources.push('../frontend/modules/partners.tsx');
  definitions.settings.styles.push('../frontend/modules/partners.css');
  definitions.workbench.sources.push('../frontend/modules/partner-work.tsx','../frontend/modules/task-delivery.tsx');
  definitions.workbench.sources.push('../frontend/modules/chat-work-handoff.tsx','../frontend/modules/work-goal.tsx');
  definitions.workbench.sources.push('../frontend/modules/task-chat.tsx');
  definitions.workbench.styles.push('../frontend/modules/task-chat.css');
  definitions.workbench.styles.push('../frontend/modules/partner-work.css','../frontend/modules/stage4-dialog.css');
  // Native Work-only floating selectors; portal roots retain workbench scope.
  definitions.workbench.sources.push('../frontend/modules/work-model-picker.tsx');
  definitions.workbench.styles.push('../frontend/modules/work-model-picker.css');
  const hosts=new Map(),leaveHooks=new Map();let owner=null,epoch=0;
  const main=()=>document.getElementById('main');
  function host(id){
    if(!definitions[id])throw Error('Unknown UI module: '+id);
    if(!hosts.has(id)){
      const el=document.createElement('div');el.dataset.uiModule=id;el.className='ui-module-root';el.style.display='contents';
      window.WorkdeskReact?.ownHost(el);
      let savedScroll=0;
      Object.defineProperty(el,'scrollTop',{get:()=>owner===id?main().scrollTop:savedScroll,set:value=>{savedScroll=value;if(owner===id)main().scrollTop=value;}});
      hosts.set(id,el);
    }
    return hosts.get(id);
  }
  function dom(id){
    return selector=>{
      const root=host(id);
      if(selector==='#main')return root;
      const local=selector.startsWith('#main ')?selector.slice(6):selector;
      const found=root.querySelector(local);if(found)return found;
      // Only shared chrome and overlays may be queried outside this host.
      // Another page's content is never a fallback query target.
      return [...document.querySelectorAll(selector)].find(el=>!el.closest('#main'))||null;
    };
  }
  function activate(route){
    const next=Object.keys(definitions).find(id=>definitions[id].routes.includes(route))||'history';
    if(owner!==next){
      if(owner){
        for(const leave of leaveHooks.get(owner)||[])leave();
        window.WorkdeskReact?.release(host(owner));
        for(const dialog of document.querySelectorAll('dialog[open]')){
          dialog.dispatchEvent(new Event('cancel',{cancelable:true}));if(dialog.open)dialog.close();
        }
      }
      owner=next;main().replaceChildren(host(next));main().scrollTop=0;
    }else if(!host(next).isConnected)main().replaceChildren(host(next));
    main().dataset.activeModule=next;return ++epoch;
  }
  window.WorkdeskModules=Object.freeze({definitions,dom,activate,currentHost:()=>owner?host(owner):null,
    currentOwner:()=>owner,isCurrent:token=>token===epoch,
    onLeave:(id,leave)=>{if(!leaveHooks.has(id))leaveHooks.set(id,[]);leaveHooks.get(id).push(leave);},
    shared:{scripts:['app.js','appearance.js','ui-modules.js','ui-dialogs.js','support-common.js','settings-preferences.js'],styles:['style.css','workbench.css','appearance.css','management-navigation.css','../frontend/modules/conversation-scroll.css'],
      sources:['../index.html','../package.json','../pnpm-lock.yaml','../pnpm-workspace.yaml','../components.json','../tsconfig.json','../vite.config.ts',
        '../frontend/main.tsx','../frontend/bridge.d.ts','../frontend/styles.css','../frontend/lib/utils.ts','../frontend/lib/render.tsx',
        '../frontend/core/client.ts','../frontend/core/legacy-loader.ts','../frontend/core/workspace.tsx','../frontend/core/workspace.css','../frontend/core/partner-runtime.tsx',
        '../frontend/modules/shell.tsx','../frontend/components/template.tsx','../frontend/lib/format.ts',
        '../frontend/modules/conversation-scroll.tsx',
        '../frontend/components/safe-markdown.tsx','../frontend/components/safe-markdown.css',
        ...['quick-workspace.tsx','quick-workspace.css','quick-documents.tsx','quick-documents.css','quick-files.tsx','quick-files.css','conversation-summary.tsx','conversation-summary.css'].map(file=>'../frontend/modules/'+file),
        ...['button.tsx','input.tsx','textarea.tsx','native-select.tsx','card.tsx','badge.tsx','separator.tsx','registry-provenance.json','LICENSE.md'].map(file=>'../frontend/components/ui/'+file)],
      description:'Shared tokens, navigation, base controls and legacy appearance; changes require cross-page checks.'}});
})();
