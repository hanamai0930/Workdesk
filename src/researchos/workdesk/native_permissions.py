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
    from .partners.permissions import catalog as partners
    row = next((row for row in partners() if row['partner_id'] == provider), None)
    if row is None:raise ValueError('未知官方工作端。')
    result=[]
    for mode in row['modes']:
        native=mode.get('native') or {}
        thread=native.get('thread') or {}
        label=((' / '.join((thread['sandbox'],thread['approvalPolicy'],thread['approvalsReviewer']))) if thread else
               native.get('mode_id') or native.get('permission_mode') or native.get('mode') or native.get('preset') or
               (' '.join(native['argv']) or 'native approval requests' if 'argv' in native else 'UNAVAILABLE'))
        result.append({**mode,'scope':'THIS_CONVERSATION','native_mode':label})
    return result


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
