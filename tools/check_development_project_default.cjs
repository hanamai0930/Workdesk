/* Exercise the production controller's route/draft priority without services. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../workdesk-ui/static/workbench.js'),'utf8');

async function render({channel='development',defaultId='repo',selected='',draft=null,type='independent',task=false}={}){
  let view,lastError;
  const nodes=new Map(),element=selector=>{
    if(!nodes.has(selector))nodes.set(selector,{dataset:{},open:false,clientWidth:0,
      setAttribute(){},removeAttribute(){},replaceChildren(){},querySelectorAll:()=>[],close(){this.open=false;}});
    return nodes.get(selector);
  };
  const projects=[{id:'repo',name:'Development repo',root:'C:/development/repo',scene:'development'},
    {id:'other',name:'Other project',root:'C:/selected-project',scene:'documents'}];
  const sandbox={
    document:{documentElement:{dataset:{workdeskChannel:channel,workdeskDefaultProject:defaultId}},body:{dataset:{}},querySelectorAll:()=>[],addEventListener(){}},
    location:{hash:task?'#work/task/history':'#work'},state:{},addEventListener(){},setTimeout(){},clearTimeout(){},
    matchMedia:()=>({matches:false}),esc:value=>String(value??''),icon:()=>'',getLocal:(_,fallback)=>fallback,setLocal(){},
    errorPage:message=>{lastError=message;return message;},toast(){},copyText(){},structuredClone,
    api:async route=>route==='v1/workspace?view=workbench'?{projects,profiles:[{id:'codex',model:'fixture-model'}],tasks:[]}:
      route==='v1/collaboration?view=workbench'?{dialogues:[],work_records:[],display_records:[]}:assert.fail('Unexpected request '+route),
    WorkdeskModules:{dom:()=>element,currentOwner:()=> 'workbench'},
    WorkdeskSettingsPreferences:{get:()=>({workAccess:{codex:'workspace'},workProvider:'codex'}),remember(){}},
    WorkdeskIndependent:({v})=>{
      view=v;Object.assign(view,{project:selected,type});
      return {install(){},normalize(){},presentation:()=>({}),readTask:async()=>({attempts:[],task:{id:'history',project_id:'other',state:'DRAFT',spec:{work_scene:'documents',provider:'codex',model:'fixture-model',work_mode:'independent',materials:{},attachments:[]}}})};
    },
    WorkdeskFeatures:({v})=>({leave(){},beforeRender:async()=>{},special:()=>false,
      restore:(mode,id)=>{if(!id&&draft)Object.assign(v,draft);return !!draft;},chatPresentation:()=>({}),runtimeControls:()=>'',pollChat(){}}),
    WorkdeskChatRecords:()=>({leave(){},render(){}}),WorkdeskWorkRecords:()=>({leave(){},render(){}}),
    WorkdeskReact:{conversation(){},chat(){}},
  };
  sandbox.window=sandbox;
  vm.runInNewContext(source,sandbox,{filename:'workbench.js'});
  await sandbox.WorkdeskWorkbench.render(task?['task','history']:[]);
  assert.equal(lastError,undefined,lastError);
  return view;
}

(async()=>{
  const fresh=await render();assert.equal(fresh.project,'repo');assert.equal(fresh.scene,'development');
  assert.equal((await render({channel:'daily'})).project,'');
  assert.equal((await render({defaultId:'missing'})).project,'');
  assert.equal((await render({selected:'other'})).project,'other');
  const saved=await render({draft:{project:'',input:'saved draft'}});assert.equal(saved.project,'');assert.equal(saved.input,'saved draft');
  assert.equal((await render({type:'cooperative'})).project,'');
  assert.equal((await render({task:true})).project,'other');
  console.log(JSON.stringify({checks:7,development_default:'passed',daily_explicit_draft_history_preserved:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
