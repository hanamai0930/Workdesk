"""Model-specific observations, never a Workdesk token limit or compactor.

Provider maxima below are used only for conservative cost reservation. Unknown
API output maxima block paid sends instead of silently imposing a token ceiling.
"""
import json

from .store import canonical
from .model_catalog import reference


API_MODELS = {
    ('deepseek', 'deepseek-flash'): {
        'context_tokens': 1_000_000, 'max_output_tokens': 393216,
        'source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    },
    ('qwen', 'qwen3.8-flash'): {
        'context_tokens': 1_000_000, 'max_output_tokens': 131072,
        'max_input_tokens': {'off': 991808, 'thinking': 983616},
        'source': 'https://help.aliyun.com/en/model-studio/qwen3-8-flash',
    },
}


def describe(locations, provider, model, mode):
    value = {'model': model, 'mode': mode, 'management': 'NATIVE_HARNESS',
             'workdesk_input_token_limit': None, 'workdesk_output_token_limit': None,
             'workdesk_compaction_override': None, 'observed_effective_tokens': None,
             'full_window_test': 'NOT_VERIFIED', 'capacity_source': 'UNKNOWN'}
    if provider == 'codex':
        try:
            cache = locations.codex_home / 'models_cache.json'
            if cache.is_file():
                rows = json.loads(cache.read_text(encoding='utf-8')).get('models', [])
                entry = next((m for m in rows if m.get('slug') == model), {})
                value.update(catalog_context_tokens=entry.get('context_window'),
                             catalog_max_context_tokens=entry.get('max_context_window'),
                             capacity_source='LOCAL_NATIVE_MODEL_CATALOG' if entry else 'UNKNOWN')
        except (ValueError, OSError):
            pass
    else:
        entry = reference(provider,model)
        if not entry.get('runnable'):entry={}
        value.update(documented_context_tokens=entry.get('context_tokens'),
                     documented_output_tokens=entry.get('max_output_tokens'),
                     documented_input_tokens=entry.get('max_input_tokens', {}).get('off' if mode == 'off' else 'thinking'),
                     capacity_source='OFFICIAL_DOCUMENTATION' if entry else 'UNKNOWN',
                     source=(entry.get('sources') or [None])[0], documentation_checked=entry.get('checked'))
    return value


def request_reserve(provider, body, selection=None):
    """Do not mutate SDK parameters. Reserve a documented maximum, not a cap.

    Existing conservative CNY rates (3 input / 12 output per million) are cost
    bounds, not provider invoices. UTF-8 bytes plus overhead overestimate input.
    Native SDK max_tokens, when present, may lower the bound on its own request.
    """
    entry = selection['entry'] if selection else API_MODELS.get((provider, body.get('model')))
    if entry is None:
        raise ValueError('该模型最大输出/费用边界未知；未发送，不设置替代token上限。')
    if selection and (entry.get('provider')!=provider or entry.get('model')!=body.get('model') or not entry.get('reservation')):
        raise ValueError('准确目录快照与请求不符，未发送。')
    maximum = entry['max_output_tokens']
    if type(maximum) is not int or maximum<=0:raise ValueError('模型费用边界未知，未发送。')
    explicit = [body[k] for k in ('max_tokens', 'max_completion_tokens') if body.get(k) is not None]
    if explicit:
        if any(type(n) is not int or n <= 0 for n in explicit):
            raise ValueError('原生SDK输出参数无效。')
        maximum = min(maximum, *explicit)
    rates=entry.get('reservation',{'input_cny_per_million':3,'output_cny_per_million':12})
    if selection and selection.get('effort')!='off':maximum+=rates.get('thinking_extra_tokens',0)
    input_bound=len(canonical(body).encode('utf-8'))+16384
    # Image tokens do not follow encoded byte count. Reserve at least the whole
    # documented model context, without changing any SDK request/output limits.
    if any(isinstance(m.get('content'),list) and any(isinstance(p,dict) and p.get('type')=='image_url' for p in m['content']) for m in body.get('messages',[]) if isinstance(m,dict)):
        input_bound=max(input_bound,entry.get('context_tokens',0))
    return (input_bound*rates['input_cny_per_million']+maximum*rates['output_cny_per_million'])/1_000_000
