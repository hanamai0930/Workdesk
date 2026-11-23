"""Primary turn-scoped permission observations and original Human answer shapes.

Profiles are returned request data. Replay never applies OS grants. This source
covers nullable network and legacy read/write arrays; environment and entries
extensions require their own contract.
"""
from .store import canonical
from .work_coordinator_handoff_post_round_v3_execution_shapes import fields


def permission_params(params, thread, turn, attempt):
    fields(params, 'threadId turnId itemId environmentId startedAtMs cwd reason permissions')
    if (params['threadId'] != thread or params['turnId'] != turn or
            not isinstance(params['itemId'], str) or not params['itemId'] or
            params['environmentId'] is not None or params['cwd'] != attempt['workspace'] or
            type(params['startedAtMs']) is not int or params['startedAtMs'] < 0 or
            params['reason'] is not None and not isinstance(params['reason'], str)):
        raise ValueError('V3权限请求须绑定原主回合、副本目录与已核实环境。')
    profile = params['permissions']; fields(profile, 'network fileSystem')
    if profile['network'] is not None:
        fields(profile['network'], 'enabled')
        if profile['network']['enabled'] is not None and type(profile['network']['enabled']) is not bool:
            raise ValueError('V3权限网络观察须为布尔值或null。')
    if profile['fileSystem'] is not None:
        fields(profile['fileSystem'], 'read write')
        for paths in profile['fileSystem'].values():
            if paths is not None and (not isinstance(paths, list) or any(not isinstance(p, str) for p in paths)):
                raise ValueError('V3权限路径须保留完整原文本数组或null。')


def permission_answers(params, response):
    """Reconstruct both original scope spellings without hashing the wire reply.

    native.resolve stores the normalized permissions/scope reply, whereas the
    original command contains allow plus optional scope. Exact command lookup
    must select one original payload across these two supported spellings.
    """
    fields(response, 'permissions scope')
    if response['scope'] != 'turn':
        raise ValueError('V3权限答复尚未核实会话授权。')
    if canonical(response['permissions']) == canonical(params['permissions']):
        allow = True
    elif response['permissions'] == {}:
        allow = False
    else:
        raise ValueError('V3权限答复必须是原请求完整配置或准确拒绝。')
    return [{'allow': allow}, {'allow': allow, 'scope': 'turn'}]
