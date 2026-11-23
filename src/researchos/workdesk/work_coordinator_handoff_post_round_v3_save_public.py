"""Exact V3 public save route and independently audited V8 source admission."""
from .work_coordinator_handoff_post_round_v3_saved_component import COMMAND
from .work_coordinator_handoff_post_round_v3_saved_records import request_shape


def save(service, db, request):
    from .work_coordinator_handoff_post_round_v3_apply import WorkCoordinatorHandoffPostRoundV3Applications
    return WorkCoordinatorHandoffPostRoundV3Applications(service).save(db, request)


def action(service, db, item, plan, proposal, *, current_verified):
    from .work_coordinator_handoff_post_round_v2_preview import proposal_state
    row = proposal.get('record')
    if not row or not proposal_state(row['snapshot']):
        return None
    value = {'can_save': False, 'kind': COMMAND, 'request': None, 'problems': list(proposal.get('problems', []))}
    if not current_verified or proposal['state'] != 'POST_ROUND_ADJUSTMENT_PREVIEW':
        return value
    from .work_coordinator_handoff_post_round_v3_candidates import WorkCoordinatorHandoffPostRoundV3Candidates
    request = {'id': item['id'], 'revision': item['revision'],
        'current_driver': service.work_drivers.reference(service.work_drivers.current(db, item['id'])),
        'plan': {k: plan[k] for k in ('id', 'version')},
        'authorization': service.work_units.auth_ref(service.work_units.authorization(db,
            service.work_drivers.current(db, item['id']))),
        'frontier': service.work_units.frontier(db, item['id']), 'proposal': service.work_proposals.reference(row)}
    try:
        request_shape(request)
        WorkCoordinatorHandoffPostRoundV3Candidates(service)._live(db, request)
    except (ValueError, OSError) as exc:
        value['problems'].append(str(exc))
        return value
    value.update(can_save=True, request=request)
    return value
