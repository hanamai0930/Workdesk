import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { NativeSelect } from '../components/ui/native-select';
import { Textarea } from '../components/ui/textarea';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';

const names:any={settings:'外观设置',scene:'场景说明',member:'模型与工作配置',skills:'查找与安装 Skill',development:'程序功能修改'};
const providers:Record<string,string>={codex:'GPT',deepseek:'DeepSeek',qwen:'Qwen'};
const effortNames:Record<string,string>={off:'关闭',none:'关闭',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'极高',ultra:'最高'};
const tierNames:Record<string,string>={fast:'快速',standard:'标准'};
const modelLabel=(value:any)=>[providers[value?.provider]||value?.provider,value?.model].filter(Boolean).join(' · ');
const configurationLabel=(value:any)=>`思考 ${effortNames[value.effort]||value.effort} · ${tierNames[value.tier]||value.tier} · 配置 ${value.id.split(':').at(-1).slice(0,8)}`;
const states:any={PARSING:'正在解析',WAITING_INPUT:'等待具体条件',NEEDS_RECONCILIATION:'原回合待核对',APPLIED:'已处理',FAILED:'处理失败',CANCELLED:'已取消'};
const effects:any={APPLICATION_APPEARANCE:'应用界面外观',NEW_WORK_ONLY:'该场景以后创建的工作',FUTURE_MEMBER_SELECTIONS:'以后选择该模型配置的工作',DEVELOPMENT_WORK_REGISTERED:'已登记开发工作，尚未修改程序',READ_ONLY_SEARCH:'仅查找，未安装',SELECTED_SCENE_NEW_WORK_ONLY:'所选场景以后创建的工作'};

function ManagementField({ label, compact, children }: { label: string; compact: boolean; children: ReactNode }) {
  return compact ? <label className="wd-settings-row"><span className="wd-settings-copy">{label}</span>
    <span className="wd-settings-value">{children}</span></label> : <label>{label}{children}</label>;
}

export function ManagementForm({ data, draft, busy, pending, error, onField, compact = false }: any) {
  const members = data?.members || [];
  const parserModels=data?.parser_models||[];
  const parser=parserModels.find((row:any)=>row.provider===draft.parser_provider&&row.model===draft.parser_model);
  const parserFamilies=Object.entries(providers).filter(([provider])=>parserModels.some((row:any)=>row.provider===provider));
  const parserChoices=parserModels.filter((row:any)=>row.provider===draft.parser_provider);
  const memberTargets=members.filter((row:any)=>row.provider===draft.target_provider&&row.model===draft.target_model);
  const targetFamilies=Object.entries(providers).filter(([provider])=>members.some((row:any)=>row.provider===provider));
  const targetModels=[...new Set<string>(members.filter((row:any)=>row.provider===draft.target_provider).map((row:any)=>row.model))];
  const targets=draft.kind==='member'?memberTargets:data?.scenes;
  const needsTarget = ['scene','skills','member'].includes(draft.kind);
  const target = targets?.find((row:any)=>row.id===draft.target_id);
  const missing = !data ? '正在读取模型和作用范围…' : !parserModels.some((row:any)=>row.available)
    ? '需要一个可用的官方工作端和解析型号。' : !parser?.available ? '请选择可用的解析大模型和型号。'
    : needsTarget && target?.revision==null ? '请选择本次请求的生效目标。'
    : draft.kind==='settings' && !Number.isInteger(data.settings?.appearance?.revision) ? '请刷新外观设置版本。' : '';
  const Select = compact ? NativeSelect : 'select';
  return <form id="management-request-form">
    <div className={compact ? undefined : 'wd-management-grid'}>
      <ManagementField label="管理内容" compact={compact}><Select name="kind" value={draft.kind} onChange={e=>onField('kind',e.target.value)}>
        {Object.entries(names).map(([key,label]:any)=><option key={key} value={key}>{label}</option>)}
      </Select></ManagementField>
      <ManagementField label="解析大模型" compact={compact}><Select name="parser_provider" value={draft.parser_provider} onChange={e=>onField('parser_provider',e.target.value)}>
        <option value="">选择大模型</option>{parserFamilies.map(([provider,label])=><option key={provider} value={provider} disabled={!parserModels.some((row:any)=>row.provider===provider&&row.available)}>{label}</option>)}
      </Select></ManagementField>
      <ManagementField label="解析型号" compact={compact}><Select name="parser_model" value={draft.parser_model} disabled={!draft.parser_provider} onChange={e=>onField('parser_model',e.target.value)}>
        <option value="">选择具体型号</option>{parserChoices.map((choice:any)=><option key={choice.model} value={choice.model} disabled={!choice.available}>{choice.model}</option>)}
      </Select></ManagementField>
      {draft.kind==='member'?<>
        <ManagementField label="目标大模型" compact={compact}><Select name="target_provider" value={draft.target_provider} onChange={e=>onField('target_provider',e.target.value)}>
          <option value="">选择大模型</option>{targetFamilies.map(([provider,label])=><option key={provider} value={provider}>{label}</option>)}
        </Select></ManagementField>
        <ManagementField label="目标型号" compact={compact}><Select name="target_model" value={draft.target_model} disabled={!draft.target_provider} onChange={e=>onField('target_model',e.target.value)}>
          <option value="">选择具体型号</option>{targetModels.map(model=><option key={model} value={model}>{model}</option>)}
        </Select></ManagementField>
        {memberTargets.length>1&&<ManagementField label="生效配置" compact={compact}><Select name="target_id" value={draft.target_id} onChange={e=>onField('target_id',e.target.value)}>
          <option value="">此型号有多份配置，请明确选择</option>{memberTargets.map((row:any)=><option key={row.id} value={row.id}>{configurationLabel(row)}</option>)}
        </Select></ManagementField>}
      </>:['scene','skills'].includes(draft.kind)&&<ManagementField label="生效目标" compact={compact}>
        <Select name="target_id" value={draft.target_id} onChange={e=>onField('target_id',e.target.value)}><option value="">选择目标</option>
          {(targets||[]).map((t:any)=><option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
      </ManagementField>}
      {draft.kind==='skills'&&<ManagementField label="本次操作" compact={compact}>
        <Select name="intent" value={draft.intent} onChange={e=>onField('intent',e.target.value)}><option value="inspect">只查找与查看版本</option><option value="install">查找并安装到所选场景</option></Select>
      </ManagementField>}
    </div>
    <label className={compact?'block px-4 py-3':undefined}>你的请求
      <Textarea name="text" value={draft.text} onChange={e=>onField('text',e.target.value)} rows={4} maxLength={12000}
        placeholder={draft.kind==='development'?'例如：给文件列表增加按修改时间排序。':'例如：把这个场景的交付要求改为先给结论，再列出来源。'}/>
    </label>
    {!compact&&<p className="wd-management-note">本次请求即授权范围。明确要求安装后会直接安装准确版本；缺少认证或信息时显示具体等待条件。程序功能修改会登记为开发工作。</p>}
    {compact&&draft.kind==='skills'&&draft.intent==='install'&&<p className="wd-settings-empty">提交后会将符合请求的 Skill 安装到所选场景。</p>}
    {missing&&<p className={compact?'wd-settings-empty':'wd-management-note'} role="status">{missing}
      {data&&!parser?.available&&<> <a href="#settings/accounts">账号与模型</a></>}</p>}
    {draft.kind==='member'&&target&&<p className={compact?'wd-settings-empty':'wd-management-note'}>调整 {modelLabel(target)} 的所选配置；{configurationLabel(target)}。不更改其他同型号配置或当前工作。</p>}
    {error&&<p role="alert" className={compact?'wd-settings-error':'wd-management-error'}>{error}</p>}
    {pending&&<div className={compact?'wd-settings-row':undefined} role="status">操作回执待核对
      {!compact&&<code>{pending}</code>} <Button type="button" variant="outline" className={compact?'wd-settings-button':undefined} data-management="receipt" disabled={busy}>{compact?'核对原请求':'核对原命令'}</Button>
    </div>}
    <div className={compact?'wd-settings-actions':'wd-management-actions'}>
      <Button type="submit" disabled={busy||!!pending||!draft.text.trim()||!!missing}>{busy?'正在处理…':'提交请求'}</Button>
      <Button type="button" variant="outline" className={compact?'wd-settings-button':undefined} data-management="refresh" disabled={busy}>刷新记录</Button>
      {!compact&&parser?.available&&<a href="#settings/accounts">模型与工作端设置</a>}
    </div>
  </form>;
}

export function ManagementPage(props: any) {
  const {data,busy,pending,focusRecord}=props;
  const records=data?.requests||[];
  const recordsRef=useRef<HTMLElement>(null);
  const hasFocusRecord=records.some((row:any)=>row.id===focusRecord);
  useEffect(()=>{
    if(!focusRecord)return;
    const record=Array.from(recordsRef.current?.querySelectorAll<HTMLElement>('[data-management-record]')||[])
      .find(item=>item.dataset.managementRecord===focusRecord);
    record?.scrollIntoView({block:'nearest'});
    record?.focus({preventScroll:true});
  },[focusRecord,hasFocusRecord]);
  return <section className="wd-management" data-react-management>
    <header><a href="#settings/management">返回自然语言管理设置</a><h1>用自然语言管理 Workdesk</h1><p>说明需要的变化，由所选官方工作端解析，程序按本次范围调用管理功能。</p></header>
    <Card className="wd-management-compose"><ManagementForm {...props}/></Card>
    <section ref={recordsRef} aria-labelledby="management-records-title"><h2 id="management-records-title">请求与变化记录</h2>{records.length===0&&<p>提交后会保留原请求、提案、操作结果与生效范围。</p>}
      {records.map((row:any)=><Card key={row.id} className="wd-management-record" data-management-record={row.id} tabIndex={focusRecord===row.id?-1:undefined}>
        <div className="wd-management-record-head"><strong>{names[row.scope.kind]}</strong><span>{states[row.state]||row.state}</span><time>{new Date(row.created*1000).toLocaleString()}</time></div>
        <p className="wd-management-request">{row.text}</p><p>作用范围：{row.scope.kind==='member'?modelLabel(row.target):row.target?.name||names[row.scope.kind]}{row.target?.revision!=null?` · 提交时版本 ${row.target.revision}`:''}</p>
        {row.parser_selection&&<p>解析模型：{modelLabel(row.parser_selection)}</p>}
        {row.proposal&&<p>处理提案：{row.proposal.summary}</p>}{row.reason&&<p role="status">{row.reason}</p>}
        {row.effect&&<p>生效范围：{effects[row.effect]||row.effect}</p>}
        {row.outcome?.matches&&<ul>{row.outcome.matches.map((skill:any)=><li key={skill.name}><strong>{skill.name}</strong> · {skill.description}<small>版本 {skill.commit}</small><a href={skill.url} target="_blank" rel="noopener noreferrer">查看固定来源</a><details><summary>安装内容预览</summary><ul>{skill.files?.map((path:string)=><li key={path}>{path}</li>)}</ul></details><Button variant="outline" data-management="install" data-id={row.id} data-skill={skill.name} data-commit={skill.commit} disabled={busy||!!pending}>安装此版本到所选场景</Button></li>)}</ul>}
        {row.outcome?.matches?.length===0&&<p>在当前公开目录中没有匹配条目。</p>}
        {row.outcome?.preview&&<div className="wd-management-preview"><p>已安装版本：{row.outcome.preview.name} · {row.outcome.preview.origin.commit}</p><a href={row.outcome.preview.origin.url} target="_blank" rel="noopener noreferrer">查看固定来源</a><details><summary>准确文件预览与内容校验</summary><ul>{row.outcome.preview.files.map((f:any)=><li key={f.path}>{f.path} · {f.bytes} 字节<small>{f.sha256}</small></li>)}</ul></details></div>}
        {row.outcome?.href&&<a href={row.outcome.href}>打开已登记的开发工作</a>}
        <div className="wd-management-actions">{['PARSING','NEEDS_RECONCILIATION'].includes(row.state)&&<Button variant="outline" data-management="check" data-id={row.id} data-revision={row.revision} disabled={busy}>核对原回合</Button>}{!['APPLIED','CANCELLED'].includes(row.state)&&<Button variant="outline" data-management="cancel" data-id={row.id} data-revision={row.revision} disabled={busy}>取消请求</Button>}{row.state==='APPLIED'&&row.outcome?.restore&&row.scope.kind!=='development'&&<Button variant="outline" data-management="restore" data-id={row.id} disabled={busy}>填写撤回请求</Button>}{row.task_id&&<a href={`#work/task/${row.task_id}`}>解析回合记录</a>}</div>
        <details><summary>提案与操作回执</summary><pre>{JSON.stringify({id:row.id,scope:row.scope,target:row.target,parser_selection:row.parser_selection,member_id:row.member_id,selection:row.selection,authorization:row.authorization,proposal:row.proposal,evidence:row.evidence,outcome:row.outcome},null,2)}</pre></details>
      </Card>)}
    </section>
  </section>;
}
