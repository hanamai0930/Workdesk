"""Mixed saved path and exact frozen ordinary add/revise run identities."""
from copy import deepcopy
from .store import digest
from .work_file_handoff_rework_path import WorkFileHandoffReworkPath
from .work_file_handoff_unit_revisions import (
    PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION, PATH, FIELDS,
    TRANSITION, PAUSE_REASON, INPUT, build_preview, historical_keys)
from .work_transitions import plan_ref


def input_context(service, db, plan, unit):
    if plan.get('file_handoff_lineage', {}).get('protocol') != APPLICATION:
        return None
    path = WorkFileHandoffUnitRevisionPath(service)
    parent = service.collaboration.get(db, 'teamwork_plan', plan['previous_plan_reference']['id'])
    return path.run_context(unit, path._saved_edge(db, parent, plan))[1]


class WorkFileHandoffUnitRevisionPath(WorkFileHandoffReworkPath):
    path_protocol = PATH

    def explicit_protocols(self):
        return (*super().explicit_protocols(), APPLICATION)

    def edge_contract(self, protocol):
        if protocol == APPLICATION:
            return PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION, TRANSITION, PAUSE_REASON
        return super().edge_contract(protocol)

    def run_context(self, unit, edge):
        if edge['preview']['protocol'] != PREVIEW:
            return super().run_context(unit, edge)
        preview = edge['preview']
        entry = next(e for e in preview['units'] if e['key'] == unit['key'])
        context = None
        if entry['state'] in ('ADDED', 'AFFECTED'):
            context = deepcopy({'protocol':INPUT, 'application':edge['application'],
                'from_plan':preview['base_plan'], 'unit_key':unit['key'],
                **{k:entry[k] for k in ('operation','basis','prior_source')}})
        return 'handoff_unit_revision_context', context

    def _rework_candidate(self, db, paths, edges, parent, child, preview, audit, rows, evidence, root):
        if child['file_handoff_lineage']['protocol'] != APPLICATION:
            return super()._rework_candidate(db, paths, edges, parent, child, preview, audit, rows, evidence, root)
        request = preview['request']
        raw = [{'run_id':r['id'], 'evidence':evidence[r['id']]['version']} for r in rows]
        lineage = []
        for key, reference in parent.get('result_lineage', {}).items():
            local = [r for r in rows if r['plan_id'] == parent['id'] and r['unit_key'] == key]
            run_id = local[0]['id'] if local else reference['run_id']
            lineage.append({'key':key, 'source':None if local else reference, 'problems':[],
                            'evidence':evidence[run_id]['version']})
        frontier = digest({'runs':raw, 'plan':plan_ref(parent), 'lineage':lineage}) if lineage else digest(raw)
        if (set(request) != FIELDS or request['id'] != parent['item_id'] or
                request['plan'] != plan_ref(parent) or request['revision'] != preview['item_revision'] or
                request['current_driver'] != preview['driver'] or
                request['authorization'] != self.s.work_units.auth_ref(preview['authorization']) or
                request['frontier'] != frontier or preview['frontier'] != frontier):
            raise ValueError('普通文字修订边须绑定准确原请求与全部运行前沿。')
        proof = {'plans':[plan_ref(p) for p in paths], 'edges':[e['application'] for e in edges],
            'frontier':[{'run_id':r['id'], 'evidence':evidence[r['id']]} for r in rows],
            'retired_source':root['relation']['source_unit'], 'completed_key':root['relation']['target_unit'],
            'historical_source':root['historical_source']}
        expected = build_preview(self.s, db, parent, request, preview['driver'], preview['authorization'],
            frontier, paths, proof, rows, evidence,
            historical_keys(self.s, db, parent['item_id'], before=child['created']))
        expected['version'] = digest(expected)
        if preview != expected or audit != expected['ancestry_audit'] or child['units'] != expected['candidate_units']:
            raise ValueError('普通文字修订边的完整定义、历史key、成员、旧新图闭包或来源已变化。')
        return set(expected['affected_units'])
