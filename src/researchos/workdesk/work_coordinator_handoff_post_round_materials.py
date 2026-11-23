"""Rollback only newly created post-ROUND frozen material directories."""
from contextlib import contextmanager
from contextvars import ContextVar
import shutil

_transaction=ContextVar('post_round_material_transaction',default=None)


def track(service,baseline):
    state=_transaction.get()
    if state is None or state['service'] is not service: return
    folder=service.work_file_rework_applications.folder(baseline).parent
    expected=(service.locations.data/'results').resolve()
    target=folder.resolve()
    if target.parent!=expected or target.name!='file-rework-base-'+baseline['version']:
        raise ValueError('新材料回滚目标必须是准确结果目录内的版本目录。')
    if not target.exists(): state['created'].add(target)


@contextmanager
def transaction(service):
    parent=_transaction.get()
    state={'service':service,'created':set()};token=_transaction.set(state)
    try:
        yield
    except BaseException:
        for target in state['created']:
            if target.parent!=(service.locations.data/'results').resolve():
                raise ValueError('材料回滚目录边界变化。')
            if target.exists(): shutil.rmtree(target)
        raise
    else:
        # A successful nested application still belongs to its enclosing
        # command until that command commits its durable receipt.
        if parent is not None and parent['service'] is service:
            parent['created'].update(state['created'])
    finally:
        _transaction.reset(token)
