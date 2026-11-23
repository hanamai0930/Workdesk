"""Versioned official reference terms, separate from access and actual charges.

Discovery may add arbitrary model IDs. Only reviewed model/protocol/cost entries
are runnable; absence of an entry means unknown, not provider non-support.
Prices are reference terms, never the user's invoice or a currency quotation.
"""
from copy import deepcopy

from .store import digest


EDITION = 'official-reference-20261018-1'
CHECKED = '2026-10-18'
ENDPOINTS = {'qwen': 'https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions',
             'deepseek': 'https://api.deepseek.com/chat/completions'}
MODEL_URLS = {'qwen': ENDPOINTS['qwen'].replace('/chat/completions', '/models'),
              'deepseek': 'https://api.deepseek.com/models'}
DS_PRICE = 'https://api-docs.deepseek.com/quick_start/pricing/'
DS_MODE = 'https://api-docs.deepseek.com/guides/thinking_mode/'
Q_PRICE = 'https://help.aliyun.com/zh/model-studio/model-pricing'
CODEX_SOURCES = ['https://learn.chatgpt.com/docs/models', 'https://learn.chatgpt.com/docs/pricing']
CODEX_EDITION = 'official-codex-reference-20261119-1'
CODEX_GPT_62 = {
    'gpt-6.2-astra': {'display_name': 'GPT-6.2 Astra', 'context_tokens': 272000},
    'gpt-6.2-sol': {'display_name': 'GPT-6.2 Sol', 'context_tokens': 500000},
}


def reference(provider, model):
    """Return a fresh reference. Region/alias resolution uncertainty is explicit."""
    entry = {'model': model, 'provider': provider, 'edition': EDITION, 'checked': CHECKED,
             'input_modalities': None, 'output_modalities': None, 'context_tokens': None,
             'max_output_tokens': None, 'efforts': [], 'tiers': [], 'price': None,
             'source_status': 'UNKNOWN', 'runnable': False,
        'workdesk_input_path': 'EXPLICIT_NATIVE_IMAGE_OR_USER_SELECTED_LOCAL_TEXT_EXTRACTION; FORMAT_AND_INSTALLED_RUNTIME_CHECK_REQUIRED',
             'live_generation_verification': 'NOT_VERIFIED_FOR_THIS_SELECTION'}
    if provider == 'codex':
        gpt_62 = CODEX_GPT_62.get(model)
        legacy_ultrafast = model in {'gpt-6-astra', 'gpt-6.1-sol'}
        entry.update(source_status='OFFICIAL_DOCUMENTATION' if gpt_62 else 'NATIVE_ACCOUNT_CATALOG', sources=CODEX_SOURCES,
                     access_status='NATIVE_CLIENT_CATALOG; INFERENCE_ENTITLEMENT_NOT_CONFIRMED',
                     tiers=['standard', 'fast'], price={'kind': 'SUBSCRIPTION', 'currency': None,
                     'per_token_price': None, 'included_usage_multipliers': {'standard': 1, 'fast': 2 if gpt_62 else 2.5},
                     'purchased_credit_multipliers': {'standard': 1, 'fast': 2},
                     'remaining_allowance': 'UNKNOWN', 'account_charge': 'UNKNOWN',
                     'api_price_not_applicable': True},
                     ultrafast={'status':'DOCUMENTED_FOR_MODEL' if (gpt_62 or legacy_ultrafast) else 'NOT_REGISTERED_FOR_THIS_MODEL',
                                'account_and_interface_eligibility':'UNKNOWN',
                                'included_usage_multiplier':6 if gpt_62 else 8 if legacy_ultrafast else None,
                                'purchased_credit_multiplier':6 if (gpt_62 or legacy_ultrafast) else None})
        if gpt_62:
            entry.update(**gpt_62, edition=CODEX_EDITION, checked='2026-11-19',
                         input_modalities=['text', 'image'], output_modalities=['text'])
            entry['ultrafast'].update(temporary_purchased_credit_multiplier=3,
                                     temporary_purchased_credit_through='2027-02-28',
                                     temporary_purchased_credit_timezone='America/Los_Angeles')
        return entry
    if provider == 'deepseek' and model in ('deepseek-flash', 'deepseek-v4-pro'):
        flash = model == 'deepseek-flash'
        peak = {'cache_hit_input': .006 if flash else .044,
                'cache_miss_input': .3 if flash else 1.32, 'output': 1.2 if flash else 3.96}
        entry.update(source_status='OFFICIAL_DOCUMENTATION', runnable=True, endpoint=ENDPOINTS[provider],
                     billing_channel='OFFICIAL_API', region='OFFICIAL_GLOBAL_ENDPOINT',
                     model_version='DeepSeek-V4.1-Flash-1017' if flash else 'DeepSeek-V4-Pro-0813',
                     alias_resolution='PROVIDER_MANAGED_ALIAS; RESOLVED_SERVER_REVISION_NOT_PINNED',
                     context_tokens=1048576, max_output_tokens=393216,
                     input_modalities=['text', 'image'] if flash else ['text'], output_modalities=['text'],
                     efforts=['off', 'low', 'high', 'max'], tiers=['standard'],
                     sources=[DS_PRICE, DS_MODE, 'https://api-docs.deepseek.com/api/list-models/'],
                     price={'kind': 'API_REFERENCE', 'currency': 'USD', 'unit_tokens': 1000000,
                            'peak': peak, 'off_peak': {k: v / 2 for k, v in peak.items()},
                            'schedule': 'Peak UTC Mon-Fri 01:00-04:00 and 06:00-10:00, excluding Chinese public holidays; all other times off-peak',
                            'applied_schedule': 'UNKNOWN; NO_HOLIDAY_CALENDAR_ASSUMED', 'account_charge': 'UNKNOWN'},
                     served_model_labels=[model,'DeepSeek-V4.1-Flash-1017' if flash else 'DeepSeek-V4-Pro-0813'],
                     reservation={'input_cny_per_million': max(3, peak['cache_miss_input'] * 10),
                                  'output_cny_per_million': max(12, peak['output'] * 10),
                                  'usd_budget_factor': 10, 'factor_is_exchange_rate': False,
                                  'thinking_extra_tokens': 0, 'policy': 'PEAK_BOUND; NOT_AN_INVOICE'})
    if provider == 'qwen' and model in ('qwen3.8-flash', 'qwen3.8-max', 'qwen3.8-max-0902'):
        flash = model == 'qwen3.8-flash'
        domestic = {'input': .8 if flash else 12, 'output': 2.7 if flash else 36,
                    'implicit_cache_input': .1 if flash else 1.5}
        international = {'input': 1.094 if flash else 14.988, 'output': 3.427 if flash else 44.965,
                         'implicit_cache_input': .117 if flash else 1.874}
        entry.update(source_status='OFFICIAL_DOCUMENTATION', runnable=True, endpoint=ENDPOINTS[provider],
                     billing_channel='EXISTING_GENERAL_API', region='UNKNOWN_FOR_CURRENT_CREDENTIAL',
                     alias_resolution='DATED_ID' if model.endswith('-0902') else 'PROVIDER_MANAGED_ALIAS',
                     context_tokens=1000000, max_output_tokens=131072,
                     max_input_tokens={'off': 991808, 'thinking': 983616},
                     input_modalities=['text', 'image', 'video'], output_modalities=['text'],
                     efforts=['off', 'thinking'], tiers=['standard'],
                     sources=[Q_PRICE, 'https://help.aliyun.com/en/model-studio/qwen3-8-' + ('flash' if flash else 'max')],
                     price={'kind': 'API_REFERENCE', 'currency': 'CNY', 'unit_tokens': 1000000,
                            'beijing_and_global_regions': domestic, 'singapore_and_us_scope': international,
                            'applies_to_current_credential': 'UNKNOWN; REGION_NOT_INFERRED_FROM_KEY_PREFIX',
                            'discounts_and_free_quota': 'UNKNOWN', 'account_charge': 'UNKNOWN'},
                     served_model_labels=[model]+(['qwen3.8-max-0902'] if model=='qwen3.8-max' else []),
                     reservation={'input_cny_per_million': 3 if flash else 20,
                                  'output_cny_per_million': 12 if flash else 60,
                                  'thinking_extra_tokens': 262144,
                                  'policy': 'UPPER_REFERENCE_REGION_BOUND; NOT_AN_INVOICE'})
    return entry


def entries(provider, current):
    """Merge observed IDs and published Codex models without inferring account access."""
    observed = {m['id']: m for m in current.get('native_models', []) if isinstance(m, dict) and isinstance(m.get('id'), str)}
    account_models = current.get('models', [])
    models = list(dict.fromkeys([*account_models, *CODEX_GPT_62])) if provider == 'codex' else account_models
    results = []
    for model in models:
        item = reference(provider, model);native = deepcopy(observed.get(model, {}))
        item['native_metadata'] = native
        item['discovery_source'] = current.get('evidence_source', 'SAVED_OR_DEFAULT; ONLINE_ACCESS_UNKNOWN')
        item['discovered_at'] = current.get('catalog_checked_at')
        if provider == 'codex':
            if model not in account_models:
                item.update(access_status='NOT_IN_CURRENT_ACCOUNT_CATALOG',
                            discovery_source='OFFICIAL_DOCUMENTATION',
                            discovered_at=None,
                            unavailable_reason='当前 Codex 账号目录未提供此模型。')
                results.append(item)
                continue
            item['efforts'] = current.get('reasoning_by_model', {}).get(model, ['low', 'medium', 'high', 'xhigh'])
            item['selection_efforts'] = [e for e in item['efforts'] if e!='ultra']
            item['task_mode_restrictions'] = {'ultra':'官方Ultra会使用子代理；当前Workdesk任务未授权委派，未将其默认为单工作端模式。'} if 'ultra' in item['efforts'] else {}
            item['runnable'] = bool(item['selection_efforts'])
            for key in ('context_tokens', 'max_output_tokens', 'input_modalities', 'output_modalities'):
                if native.get(key) is not None:item[key] = native[key]
        elif provider == 'deepseek':
            if item['runnable'] and isinstance(native.get('name'),str) and len(native['name'])<=200:
                item['served_model_labels']=list(dict.fromkeys(item['served_model_labels']+[native['name']]))
            for source, target in [('context_window', 'context_tokens'), ('max_output_tokens', 'max_output_tokens'),
                                   ('input_modalities', 'input_modalities'), ('output_modalities', 'output_modalities')]:
                if native.get(source) is not None:item[target] = native[source]
            supported = native.get('effort', {}).get('supported_levels')
            if isinstance(supported, list):item['efforts'] = ['off'] + [e for e in supported if e in item['efforts']]
        if not item['runnable']:item['unavailable_reason'] = 'Workdesk尚未登记该模型的准确协议、能力和费用边界；不是供应商不支持。'
        results.append(item)
    return results


def snapshot(provider, current):
    value = {'edition': CODEX_EDITION if provider == 'codex' else EDITION, 'provider': provider, 'models': entries(provider, current),
             'checked_at': current.get('catalog_checked_at'),
             'refresh_policy': 'Explicit official metadata refresh; curated price edition changes only in a reviewed catalog release',
             'effect': 'NEW_DRAFTS_OR_EXPLICIT_SELECTION_REVISION_ONLY; RUNNING_AND_HISTORICAL_TASKS_UNCHANGED'}
    return {**value, 'revision': digest(value)}
