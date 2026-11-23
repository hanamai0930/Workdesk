"""Exact-turn observations in the Workdesk ledger, without opening a daemon."""

import json


def observations(db, attempt):
    if not attempt['session_id'] or not attempt['turn_id']:
        return []
    rows = db.execute("""
        SELECT seq, kind, payload FROM events WHERE task_id=? AND attempt_id=?
        AND kind IN ('native_turn_terminal', 'native_background_observation', 'native_daemon_closed')
        AND json_extract(payload,'$.session_id')=? AND json_extract(payload,'$.turn_id')=?
        ORDER BY seq DESC LIMIT 50
    """, (attempt['task_id'], attempt['id'], attempt['session_id'], attempt['turn_id']))
    return [{'seq': row['seq'], 'kind': row['kind'], **json.loads(row['payload'])} for row in rows]


def recorded_background(db, attempt):
    events = observations(db, attempt)
    sampled = next((row for row in events if row['kind'] == 'native_background_observation'), {})
    closed = bool(events and any(row['kind'] == 'native_daemon_closed' for row in events))
    return {'source': 'RECORDED_NATIVE_DAEMON_OBSERVATION', 'session_id': attempt['session_id'],
            'turn_id': attempt['turn_id'], 'historical': True,
            'data': sampled.get('tasks', []), 'as_of_event': sampled.get('seq'),
            'settlement': 'OBSERVED' if closed and sampled.get('settled') is True else 'NOT_OBSERVED'}


def terminal(db, attempt):
    events = observations(db, attempt)
    closed = next((row for row in events if row['kind'] == 'native_daemon_closed'), None)
    settled = next((row for row in events if row['kind'] == 'native_background_observation'), None)
    main = next((row for row in events if row['kind'] == 'native_turn_terminal'), None)
    if not (closed and settled and settled.get('settled') is True and main
            and closed['seq'] > settled['seq'] > main['seq']):
        return None
    outcome = {'success': 'completed', 'error': 'failed', 'interrupted': 'interrupted'}.get(main.get('outcome'))
    if outcome:
        return {'outcome': outcome, 'terminal_event': main['seq'], 'background_event': settled['seq'],
                'exit_event': closed['seq']}
    return None
