"""Shared validation and legacy finite-package identifiers; no dispatch."""

CAMPAIGN='backend-rollout-20261017'

CAMPAIGN_LIMITS={'requests':60,'cny':12,'codex_turns':12}

NATIVE_CAMPAIGN='workdesk-native-independent-01'

NATIVE_LIMITS={'requests':0,'cny':0,'codex_turns':6,'tools':180}

def bounded_text(value, name, limit=None):
    if not isinstance(value,str) or not value.strip() or limit is not None and len(value)>limit:raise ValueError(name+'为空或超过长度限制。')
    return value.strip()

def integer(value,low,high,label):
    if type(value) is not int or not low<=value<=high:raise ValueError(label+'不在支持范围内。')
    return value

READ_TOOLS=['list_files','read_file','search_files']

ROLES={'executor','diagnoser','self_check','reviewer','coordinator'}

def text(value,limit=None):
    if not isinstance(value,str) or not value.strip() or limit is not None and len(value)>limit:raise ValueError('文本为空或超过长度限制。')
    return value.strip()
