"""Service chooses these exact typed scopes before inherited V3 markers."""
from . import work_coordinator_handoff_text_rework_contracts as c
from .work_coordinator_handoff_text_rework_runtime import child


def command_pair(service, db, kind, request, plan):
    if kind == c.SEND:
        from .work_coordinator_handoff_text_rework_command_scope import review_command
        from .work_coordinator_handoff_text_rework_commands import finish_review
        def scope(s, d, k, p):
            if k != c.SEND:
                raise ValueError('返工审阅scope命令类型不符。')
            return review_command(s, d, p)
        def finish(s, d, k, p, receipt):
            return finish_review(s, d, p, receipt)
        return scope, finish
    if kind in {'work-item.unit-send', 'work-item.proposal-check'} and child(plan):
        from .work_coordinator_handoff_text_rework_child_scope import child_command
        from .work_coordinator_handoff_text_rework_child_commands import finish_send
        def finish(s, d, k, p, receipt):
            if k == 'work-item.unit-send':
                return finish_send(s, d, p, receipt)
            from .work_coordinator_handoff_text_rework_child_scope import _pending_check
            owner = _pending_check.get(); owner.require_result(owner.replay(), receipt)
            if receipt != owner.response:
                raise ValueError('后继提案最终响应对象发生替换。')
            return receipt
        return child_command, finish
    if kind == 'work-item.proposal-check':
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_unit_run' AND id=?", (request.get('run_id'),)).fetchone()
        if row is not None:
            from .work_coordinator_handoff_historical_assets import _json
            run = _json(row['data'])
            if run.get('input', {}).get('handoff_coordination_context', {}).get('protocol') == c.REVIEW_INPUT:
                from .work_coordinator_handoff_text_rework_review_check_scope import review_check_command, _pending
                def scope(s, d, k, p):
                    return review_check_command(s, d, p)
                def finish(s, d, k, p, receipt):
                    owner = _pending.get()
                    if receipt != owner._response:
                        raise ValueError('返工审阅检查最终响应对象发生替换。')
                    return receipt
                return scope, finish
    return None
