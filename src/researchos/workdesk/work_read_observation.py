"""Reuse database-only facts within one unchanged, transactional observation."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


_current = ContextVar('work_database_read_observation', default=None)


@contextmanager
def database_observation(db):
    previous = _current.get()
    if previous is not None and previous['db'] is db:
        yield
        return
    token = _current.set({'db': db, 'changes': db.total_changes, 'facts': {}})
    try:
        yield
    finally:
        _current.reset(token)


def database_fact(service, db, key, compute):
    observation = _current.get()
    if observation is None or observation['db'] is not db or not db.in_transaction:
        return compute()
    if observation['changes'] != db.total_changes:
        # total_changes is monotone even when a SAVEPOINT is rolled back.
        # Once written, this observation never resumes reuse: a cached answer
        # from after a write could otherwise survive its subsequent rollback.
        observation['facts'].clear()
        return compute()
    identity = (id(service), key)
    if identity not in observation['facts']:
        observation['facts'][identity] = deepcopy(compute())
    return deepcopy(observation['facts'][identity])
