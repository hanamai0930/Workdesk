"""Check scoped memory revisions, merge and a source-bearing project handoff over HTTP."""
from urllib.parse import urlencode
from uuid import uuid4
from check_core_chain import require


def check_memory(chain):
    project={'scope':'project','project_id':chain.project_id}
    personal={'scope':'personal','project_id':''}
    def get(scope, identity=None, revision=None):
        fields=dict(scope)
        if identity: fields['id']=identity
        if revision: fields['revision']=revision
        return chain.api('/api/v2/memories?'+urlencode(fields))
    def save(scope, title, body, area='note', **fields):
        return chain.command('memory.save',{**scope,'title':title,'body':body,'area':area,**fields})['receipt']
    def ref(row):
        return {'kind':'memory','id':row['memory_id'],'revision':row['revision']}
    def payload(row):
        return {'id':row['memory_id'],'revision':row['revision']}
    private=save(personal,'个人样本','临时核查')
    proposal=save(project,'样本决定草稿','先保留资料原件','decision')
    updated=save(project,'样本决定草稿','结合来源修订','decision',**payload(proposal))
    chain.command('memory.save',{**project,**payload(proposal),'area':'note','title':'旧修订','body':'拒绝'},expected=409)
    require(get(project,updated['memory_id'],1)['memory']['body']=='先保留资料原件','历史修订不符')
    chain.command('memory.archive',{**project,**payload(private),'archived':True},expected=409)
    candidate=save(project,'复核意见','核对材料标识')
    merged=chain.command('memory.merge',{**project,'title':'样本决定汇总','body':'阶段 C 采用来源版本',
                            'area':'decision','sources':[ref(updated),ref(candidate)]})['receipt']
    row=get(project,merged['memory_id'])['memory']
    require(len(row['sources'])==2 and row['sources'][0]['revision']==2,'合并来源未保存修订')
    require(get(project,updated['memory_id'])['memory']['state']=='ARCHIVED','合并后来源未归档')
    chain.command('memory.delete',{**personal,**payload(private),'confirm':False},expected=409)
    chain.command('memory.delete',{**personal,**payload(private),'confirm':True})
    chain.api('/api/v2/memories?'+urlencode({**personal,'id':private['memory_id'],'revision':1}),expected=409)
    issue=save(project,'材料复核','待复核选定资料的版本','blocker')
    item=next(r for r in chain.api('/api/v1/work-items?project='+chain.project_id)['work_items'] if r['id']==chain.item_id)
    chain.command('file.selection-save',{'project_id':chain.project_id,'files':['notes.txt']})
    materials=chain.api('/api/v2/projects/'+chain.project_id+'/materials?limit=100')['materials']
    selected=next(m for m in materials if m['kind']=='selection' and m['name']=='notes.txt')
    refs=[ref(merged),ref(issue),{'kind':'work_item','id':item['id'],'revision':item['revision']},
          {'kind':'material','version':selected['version']}]
    command=str(uuid4())
    body={**project,'sources':refs}
    pack=chain.command('project.handoff-create',body,command)['receipt']['handoff_id']
    require(chain.command('project.handoff-create',body,command)['replayed'],'项目接续重试创建了重复记录')
    url='/api/v2/project-handoffs?'+urlencode({**project,'id':pack})
    handoff=chain.api(url)['handoff']
    groups=handoff['sections']
    require(all(groups[k] for k in ('goals','material_versions','decisions','open_questions','next_steps')),
            '缺少项目接续内容')
    require(groups['decisions'][0]['text']=='阶段 C 采用来源版本' and groups['material_versions'][0]['source']['version']==selected['version'],
            '接续内容没有冻结准确的来源')
    require(chain.api('/api/v1/receipt?id='+command)['receipt']['handoff_id']==pack,'接续回执引用无效')
    return {'merged_memory':merged['memory_id'],'source_revisions':[r['revision'] for r in row['sources']],
            'handoff_id':pack,'handoff_command':command,'material_sha256':selected['version']['sha256'],
            'stale_edit_rejected':True,'scope_mismatch_rejected':True,'confirmed_memory_removed':True,
            'sections':{k:len(v) for k,v in groups.items()},'real_model_requests':0}
