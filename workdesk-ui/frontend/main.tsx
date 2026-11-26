import { lazy, Suspense, type ComponentProps } from 'react';
import { Shell } from './modules/shell';
import { Conversation } from './modules/conversation';
import { OfficialChat } from './modules/chat';
import { ApiChat } from './modules/api-chat';
import { ChatRecords } from './modules/chat-records';
import { WorkRecords } from './modules/work-records';
import { QuickWorkspace, WorkspaceTool } from './modules/quick-workspace';
import { ConversationSummaryButton } from './modules/conversation-summary';
import { Template } from './components/template';
import { markdownTemplate, registerMarkdownElement } from './components/safe-markdown';
import { SettingsPage } from './modules/settings';
import { PartnerWork } from './modules/partner-work';
import { ManagementPage } from './modules/management';
import { ConnectionSetup } from './modules/connection-setup';
import { renderInto, renderTemplate, ownHost, release } from './lib/render';
import { coreClient } from './core/client';
import { loadLegacyModule, loadScript } from './core/legacy-loader';
import './styles.css';
import './modules/work-projects.css';
import './modules/work-files.css';
import './modules/settings.css';
import './modules/management.css';
import '../static/appearance.css';
import '../static/extensions.css';
import '../static/files-page.css';
import '../static/records-page.css';
import '../static/results-page.css';
import '../static/monitor-page.css';
import '../static/management-navigation.css';
import '../static/projects-page.css';
import '../static/work-items-page.css';
import '../static/conversation-detail.css';
import '../static/cooperation-toolbar.css';
import '../static/planning-page.css';
import '../static/libraries-page.css';
import '../static/control.css';
import '../static/collaboration-ledger.css';
import '../static/operations.css';
import '../static/conversation-detail.js';
import '../static/chat-detail-data.js';
import '../static/work-detail-data.js';

document.body.dataset.connectionRequired = 'checking';
window.WorkdeskCore = coreClient;
window.WorkdeskLegacyModules = loadLegacyModule;
const WorkItemFilePreview = lazy(() => import('./modules/work-item-file-preview'));
const ResourceSpace = lazy(async () => ({ default: (await import('./modules/resource-space')).ResourceSpace }));
registerMarkdownElement();
renderInto(document.getElementById('root')!, <Shell/>);
window.WorkdeskReact = {
  version: 'react19-assistant-ui-shadcn-v1',
  ownHost, renderTemplate, release,
  markdownTemplate,
  conversation(host:HTMLElement, props:any) {
    renderInto(host,<Conversation key={`${props.v.mode}:${props.v.activeChat||props.v.detail?.task.id||props.v.dialogue?.id||'home'}`} {...props}/>);
  },
  chat(host:HTMLElement, props:any) {renderInto(host,props.v.chatRoute==='api'?<ApiChat key={props.v.activeChat||'home'} {...props}/>:<OfficialChat key={props.v.activeChat||'home'} {...props}/>);},
  settings(host:HTMLElement, props:any) { renderInto(host,<SettingsPage {...props}/>); },
  partnerWork(host:HTMLElement, props:{runId?:string}) { renderInto(host,<PartnerWork key={props.runId||'new'} {...props}/>); },
  management(host:HTMLElement, props:any) { renderInto(host,<ManagementPage {...props}/>); },
  resourceSpace(host:HTMLElement, props:{ routeKey:string }) {
    renderInto(host,<Suspense fallback={<p className="loading" role="status">正在打开资料空间…</p>}>
      <ResourceSpace {...props} onNewPresentation={() => {
        window.WorkdeskWorkbench?.openWorkspaceTool('presentation',(item:{id:string}) => {
          location.hash = '#resources/item/' + encodeURIComponent(item.id);
        });
      }}/>
    </Suspense>);
  },
  connectionSetup(host:HTMLElement, props:any) { renderInto(host,<ConnectionSetup key={props.routeKey} {...props}/>); },
  chatRecords(host:HTMLElement,props:any){renderInto(host,<ChatRecords {...props}/>);},
  workRecords(host:HTMLElement,props:any){renderInto(host,<WorkRecords {...props}/>);},
  quickWorkspace(host:HTMLElement, {extra, ...props}:Omit<ComponentProps<typeof QuickWorkspace>,'children'>&{extra:string}) {
    renderInto(host,<QuickWorkspace {...props}><Template html={extra}/></QuickWorkspace>);
  },
  workspaceTool(host:HTMLElement, props:ComponentProps<typeof WorkspaceTool>) {
    renderInto(host,<WorkspaceTool {...props}/>);
  },
  conversationSummary(host:HTMLElement, props:ComponentProps<typeof ConversationSummaryButton>) {
    renderInto(host,<ConversationSummaryButton {...props}/>);
  },
  workItemFilePreview(host:HTMLElement, props:ComponentProps<typeof WorkItemFilePreview>){
    renderInto(host,<Suspense fallback={<p className="wi-file-preview-status" role="status">正在打开预览…</p>}>
      <WorkItemFilePreview {...props}/>
    </Suspense>);
  },
};
for(const id of ['wb-sidebar','wb-header-actions','wb-dialog','wb-overlays','search-results']) ownHost(document.getElementById(id)!);

// Retain audited command/state controllers as classic scripts during the view
// migration. They cannot render before React and the module boundary exist.
async function boot() {
  for(const name of ['ui-modules','app','appearance','ui-dialogs','support-common','settings-preferences','chat-api','chat','chat-records','work-records','workbench-features','independent-work','workbench']) {
    await loadScript(name);
  }
  await window.WorkdeskLegacy.startPreferences();
  window.WorkdeskLegacy.start();
  document.documentElement.dataset.uiRuntime=window.WorkdeskReact.version;
}
boot().catch(e=>{const main=document.getElementById('main');if(main)main.textContent=e.message;});
