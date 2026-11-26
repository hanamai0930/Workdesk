/* Read-only summary of an individual Work conversation. */
window.WorkdeskWorkDetail=(()=>{
  'use strict';

  const providers={codex:'Codex',deepseek:'DeepSeek',qwen:'Qwen'};
  const accessNames={workspace:'请求批准',auto:'官方自动审核',full:'完全访问权限',inherit:'沿用工作端设置'};
  const usageFields=[
    ['input','输入 Token'],['cache_hit','缓存命中 Token'],['cache_miss','缓存未命中 Token'],
    ['output','输出 Token'],['tokens','合计 Token']
  ];

  function usageRows(models){
    return usageFields.flatMap(([field,label])=>{
      const values=models.map(model=>model[field]).filter(Number.isFinite);
      return values.length?[{label,value:values.reduce((total,value)=>total+value,0).toLocaleString('zh-CN')}]:[];
    });
  }

  function savedRuns(attempts, steps, state, when, usageView){
    const usageByRun=new Map(steps.map(step=>[step.id,step]));
    return attempts.map((attempt,index)=>{
      const result=attempt.result;
      const body=typeof result?.answer==='string'?result.answer:
        result?.answer==null?'':JSON.stringify(result.answer,null,2);
      const notes=Array.isArray(result?.errors)?result.errors.map(error=>error?.message).filter(Boolean):[];
      const step=usageByRun.get(attempt.id);
      const details=usageRows(step?.models||[]);
      if(Number.isFinite(step?.started))details.unshift({label:'开始时间',value:when(step.started)});
      if(Number.isFinite(step?.ended)&&step.ended>=step.started){
        details.splice(1,0,{label:'结束时间',value:when(step.ended)},
          {label:'耗时',value:Math.floor(step.ended-step.started).toLocaleString('zh-CN')+' 秒'});
      }
      return {
        title:'第 '+(index+1)+' 个运行回合',
        subtitle:[state(attempt.state),attempt.model_configuration?.model,attempt.started&&when(attempt.started),
          attempt.native_history_reverted?'原生对话已回退':''].filter(Boolean).join(' · '),
        body:usageView?(step?.models?.some(model=>model.records>0)?'':'暂无用量回执'):
          [body,...notes].filter(Boolean).join('\n\n')||'暂无保存的回复。',
        rows:usageView?details:[]
      };
    });
  }

  async function load(id,tab='overview'){
    const S=window.WorkdeskSupport;
    const get=(route,query)=>S.request(route+'?'+new URLSearchParams(query));
    const detail=await get('task',{id,after:Number.MAX_SAFE_INTEGER});
    if(detail.task?.id!==id)throw Error('找不到对应的工作记录。');
    const partnerRunId=detail.partner_run_id||detail.task.partner_run_id||detail.task.spec?.partner_run_id;
    if(partnerRunId||detail.task.spec?.execution==='partner_native')return {kind:'work',title:detail.task.title,subtitle:'伙伴原生工作',projectId:detail.task.project_id,
      partnerNative:true,partnerRunId,backHref:'#work/task/'+encodeURIComponent(id),workItems:detail.work_items||[],metadata:[],summaries:[],runs:[]};
    const [catalog,accounting]=await Promise.all([
      get('result-catalog',{task:id}),
      tab==='usage'?get('work-usage',{id}).catch(()=>null):null
    ]);
    const {task,attempts,work_items:items=[]}=detail;
    const entry=catalog.tasks.find(record=>record.id===id);
    if(!entry||task.id!==id||task.spec.cooperative||task.spec.work_mode==='cooperative'||task.spec.interaction==='chat'){
      throw Error('找不到对应的独立工作记录。');
    }
    const last=attempts.at(-1);
    const current=last?.model_configuration||{};
    const spec=task.spec;
    const state=value=>({DRAFT:'草稿',PAUSED:'已暂停',APPROVED:'已批准'}[value]||S.state(value));
    const label=(provider,model)=>[providers[provider]||provider,model].filter(Boolean).join(' · ');
    const usage=accounting?.task;
    const steps=(usage?.steps||[]).filter(step=>step.task_id===id);
    const knownTokens=usageRows(steps.flatMap(step=>step.models||[]));
    const covered=steps.filter(step=>step.models?.some(model=>model.records>0)).length;
    const usageInfo=knownTokens.length
      ?[{label:'用量回执',value:covered+' / '+attempts.length+' 个运行回合'},...knownTokens]
      :[{label:'Token',value:accounting?'暂无用量回执':'暂时无法读取'}];
    const results=catalog.results.filter(result=>result.task_id===id);
    const accepted=results.filter(result=>result.decision?.value==='accept').length;
    const returned=results.filter(result=>result.decision?.value==='return').length;
    const pending=results.filter(result=>!result.decision).length;
    const savedFiles=[...new Set(results.flatMap(result=>Object.keys(result.snapshot?.manifest||{})))];
    const summaries=[];
    if(results.length){
      summaries.push({title:'已保存成果',rows:savedFiles.length
        ?savedFiles.map(file=>({label:'文件',value:file}))
        :[{label:'成果',value:'已保存 '+results.length+' 个回复版本'}]});
    }
    const attachments=new Set((spec.attachments||[]).map(item=>item.file).filter(Boolean));
    const materials=[...new Set([...Object.keys(spec.materials||{}),...attachments])];
    if(materials.length){
      summaries.push({title:'任务材料与附件',rows:materials.map(file=>({
        label:attachments.has(file)?'附件':'项目文件',value:file
      }))});
    }
    const usageView=tab==='usage';
    const modelReceipts=steps.flatMap(step=>step.models||[]).filter(model=>model.records>0);
    const models=Map.groupBy(modelReceipts, model=>label(model.provider,model.model));
    const usageSummaries=[{title:'已记录用量',rows:usageInfo},
      ...[...models].filter(()=>models.size>1).map(([title,receipts])=>({title,rows:usageRows(receipts)}))];
    const recordId=encodeURIComponent(id);
    return {
      kind:'work',title:entry.title||task.title,subtitle:'独立 Work · '+label(current.provider||spec.provider,current.model||spec.model),
      backHref:'#work/task/'+recordId,projectId:task.project_id,workItems:items,
      metadata:[
        {label:'最近回合',value:last?state(last.state):'尚未开始'},
        {label:'成果保存',value:results.length?'已保存 '+results.length+' 个版本':'尚未保存成果版本'},
        {label:'成果验收',value:results.length?[
          accepted?'已接受 '+accepted+' 个版本':'',returned?'已退回 '+returned+' 个版本':'',
          pending?'待验收 '+pending+' 个版本':''
        ].filter(Boolean).join(' · '):'保存成果版本后可验收'},
        {label:current.model?'最近回合模型':'任务登记模型',value:label(current.provider||spec.provider,current.model||spec.model)},
        {label:'工作目录',value:last?.workspace||spec.workspace},
        {label:'权限',value:accessNames[current.access||(!last&&spec.access)]},
        {label:'运行回合',value:attempts.length},
        {label:'最近运行',value:last?.started&&S.when(last.started)}
      ].filter(row=>row.value!==undefined&&row.value!==''),
      summaries:usageView?usageSummaries:summaries,
      runs:savedRuns(attempts,steps,state,S.when,usageView),runsTitle:usageView?'逐回合用量':'运行回合'
    };
  }

  return {load};
})();
