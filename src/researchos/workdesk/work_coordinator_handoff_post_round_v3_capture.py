"""Dedicated V3 proposal-check transaction owner; no dispatch."""
from copy import deepcopy
import time

from .store import uid,digest
from .work_coordinator_handoff_records import versioned
from . import work_coordinator_handoff_post_round_v3_continuation_proposals as p
from .work_coordinator_handoff_post_round_v3_command_scope import pending_request,bind_proposal,pending_proposal


class Capture:
    def __init__(self,service): self.s=service

    def capture(self,db,request):
        from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
        owner=WorkCoordinatorHandoffPostRoundV3Runtime(self.s)
        item=self.s.work_items.checked(db,request); plan=self.s.teamwork.current(db,item['id'])
        h=owner.read(db,plan); owner.admission(db,plan,h)
        if not pending_request(self.s,db,self.s.command_identity,request,p.COMMAND):
            raise ValueError('V3提案检查须属于本请求的专属事务。')
        previous=self.s.work_proposals.current(db,item['id'])
        body=p.snapshot(owner,h,request,previous)
        row={'id':'work-proposal:'+uid(),'item_id':item['id'],'snapshot':body,'version':digest(body),
            'created':time.time(),'command_id':self.s.command_identity}
        baseline=h['material_observation']
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s,baseline); self.s.work_file_rework_applications.preserve(baseline)
        self.s.collaboration.put(db,'work_proposal',row)
        self.s.collaboration.put(db,'work_proposal_current',{'id':'proposal-current:'+item['id'],'proposal_id':row['id']})
        source=body['source']
        self.s.store.event(source['task_id'],source['attempt_id'],'coordinator_proposal_checked',row,db)
        event=owner.checkpoints._event(db,'coordinator_proposal_checked',row,None,source['task_id'],source['attempt_id'])
        witness_body=p.receipt_body(request,self.s.command_identity,self.s.work_proposals.reference(previous),row,event,baseline)
        witness={'id':p.KIND+':'+witness_body['version'],'version':witness_body['version'],'snapshot':witness_body,
            'item_id':item['id'],'created':time.time(),'command_id':self.s.command_identity}
        p.validate_receipt(witness); self.s.collaboration.put(db,p.KIND,witness)
        self.s.store.event(None,None,p.EVENT,witness,db); bind_proposal(self.s,db,self,witness)
        after=owner.read(db,plan)
        for key in ('task','driver','authorization','frontier','phase','material_observation','frames','units','saved_path','application','registration'):
            if after[key]!=h[key]: raise ValueError('V3提案保存期间准确历史或当前材料变化：'+key)
        if (self.s.work_proposals.current(db,item['id'])!=row or
                self.s.work_file_rework_applications.observe(baseline['root'],baseline['paths'])!=baseline):
            raise ValueError('V3提案保存末尾当前指针或材料字节变化。')
        return {'proposal':row,'no_dispatch':True}


def finish(service,db,request,response):
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    plan=service.teamwork.current(db,request['id']); owner=WorkCoordinatorHandoffPostRoundV3Runtime(service)
    h=owner.read(db,plan)
    matching=[v for v in h['proposal_receipts'] if v['command_id']==service.command_identity]
    if len(matching)!=1 or not pending_proposal(service,db,matching[0]) or matching[0]['snapshot']['response']!=response:
        raise ValueError('V3提案提交须有本命令已写完整回执的准确pending见证。')
    baseline=matching[0]['snapshot']['material_observation']
    if service.work_file_rework_applications.observe(baseline['root'],baseline['paths'])!=baseline:
        raise ValueError('V3提案提交末尾当前材料字节已变化。')
    return response
