"""Explicit per-conversation choices mapped to vendor permission modes."""
ACCESS = {
    'workspace': {'name':'请求批准','description':'编辑外部文件和使用互联网时向你申请。'},
    'auto': {'name':'帮我批准','description':'由官方自动审核能力检查有风险的操作请求。'},
    'full': {'name':'完全访问权限','description':'使用官方完整访问模式，允许文件、命令及网络操作。'},
}


def choice(value):
    # Historical drafts may contain inherit. New sends always choose an
    # explicit mode; historical audit snapshots are never rewritten.
    value='workspace' if not value or value=='inherit' else value
    if value not in ACCESS:raise ValueError('请选择请求批准、帮我批准或完全访问权限。')
    return value


def catalog(provider):
    mapping={'codex':{'workspace':'workspace-write / on-request','full':'danger-full-access / never'},
             'deepseek':{'workspace':'workspace-write / ask','full':'danger-full-access / never'},
             'qwen':{'workspace':'default','full':'yolo'}}
    mapping['codex']['auto']='workspace-write / on-request / auto_review'
    mapping['qwen']['auto']='auto'
    return [{'id':key,**value,'supported':not (provider=='deepseek' and key=='auto'),
             **({'description':'当前 DeepSeek SDK 配置未加载官方自动审核服务。'} if provider=='deepseek' and key=='auto' else {}),
             'native_mode':mapping[provider].get(key,'UNAVAILABLE'),'scope':'THIS_CONVERSATION'} for key,value in ACCESS.items()]


def codex_policy(access):
    access=choice(access)
    return {'approvalPolicy':'never' if access=='full' else 'on-request',
            'approvalsReviewer':'auto_review' if access=='auto' else 'user',
            'permissions':':danger-full-access' if access=='full' else ':workspace'}


def deepseek_preset(access):
    access=choice(access)
    if access=='auto':raise ValueError('当前 DeepSeek SDK 配置没有加载官方自动审核服务。')
    return 'danger-full-access' if access=='full' else 'workspace-write'


def qwen_mode(access):
    access=choice(access)
    return 'yolo' if access=='full' else 'auto' if access=='auto' else 'default'
