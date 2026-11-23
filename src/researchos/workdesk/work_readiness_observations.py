"""Contemporaneous resource observations for cooperative command history.

Availability is observed by the real command, never inferred from a later
dispatch. The receipt stays unchanged. This immutable carrier records the
specific catalog rows used by its readiness calculation, without live secrets.
"""
from copy import deepcopy
import time

from .store import canonical, digest
from .work_transitions import plan_ref


KIND = 'work_readiness_observation'
EVENT = 'cooperation_readiness_observed'
PROTOCOL = 'COOPERATIVE_READINESS_OBSERVATION_V1'
SCOPE = 'ORIGINAL_COMMAND_RESOURCE_OBSERVATION; NOT_CURRENT_AVAILABILITY'
RESOURCE_FIELDS = {
    'members': {'id', 'revision', 'connection_id', 'available', 'name', 'role', 'provider', 'model', 'effort', 'tier'},
    'connections': {'id', 'revision', 'quota_id'},
    'quota_sources': {'id', 'revision'},
}


def selections(plan):
    return [('coordinator', plan['coordinator']), ('integrator', plan['integrator']),
        *[(u['key'], u['member']) for u in plan['units']]]


def resource_changes(plan, data):
    """The resource part of Teamwork.view, over the supplied real observation."""
    members = {m['id']: m for m in data['members']}
    connections = {c['id']: c for c in data['connections']}
    quotas = {q['id']: q for q in data['quota_sources']}
    changed = []
    for label, selection in selections(plan):
        ref = selection['reference']
        m, c, q = members.get(ref['member_id']), connections.get(ref['connection_id']), quotas.get(ref['quota_id'])
        current = {'member_id': m['id'], 'member_revision': m['revision'], 'connection_id': c['id'],
            'connection_revision': c['revision'], 'quota_id': q['id'], 'quota_revision': q['revision']} if m and c and q else None
        if not m or not m['available'] or current != ref or m['connection_id'] != ref['connection_id'] or c['quota_id'] != ref['quota_id']:
            changed.append(label)
    return changed


def selected_resources(plan, catalog):
    refs = [value['reference'] for _, value in selections(plan)]
    result = {}
    for key, field in (('members', 'member_id'), ('connections', 'connection_id'), ('quota_sources', 'quota_id')):
        identities = {ref[field] for ref in refs}
        rows = catalog[key]
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('责任资源观察包含重复身份。')
        result[key] = sorted(({field: deepcopy(row[field]) for field in RESOURCE_FIELDS[key]}
            for row in rows if row['id'] in identities), key=lambda row: row['id'])
    return result


def record(service, db, item, plan, driver, request, command_kind, catalog):
    """Called only by the actual command writer, inside its write transaction."""
    if not plan or not plan.get('coordinator_file_handoff_root'):
        return
    snapshot = {'protocol': PROTOCOL, 'item_revision': item['revision'], 'plan': plan_ref(plan),
        'driver': {k: driver[k] for k in ('id', 'generation', 'revision')} if driver else None,
        'command_kind': command_kind, 'request_hash': digest(request),
        'resources': selected_resources(plan, catalog), 'scope': SCOPE, 'no_dispatch': True}
    row = {'id': 'work-readiness:' + service.command_identity, 'item_id': item['id'],
        'command_id': service.command_identity, 'created': time.time(),
        'snapshot': snapshot, 'version': digest(snapshot)}
    # A separate immutable observation table keeps resource observations under
    # their own owner. Legacy saved-tree protocols continue to own only their
    # original carriers. This event is the exact typed reference to this row.
    db.execute('INSERT INTO work_readiness_observations VALUES(?,?,?,?)',
        (row['id'], row['item_id'], row['command_id'], canonical(row)))
    service.store.event(None, None, EVENT, {'id': row['id'], 'version': row['version'], 'no_dispatch': True}, db)
