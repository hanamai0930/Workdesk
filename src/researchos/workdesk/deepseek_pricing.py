"""Reference CNY prices per million tokens and the Beijing billing calendar."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal


LOCAL = timezone(timedelta(hours=8))
RATE_BASIS = {
    'version': 'CNY-20261122-1', 'reference_date': '2026-11-22',
    'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
    'coverage_from': '2026-09-10T12:00:00+08:00',
}
# The Flash tariff was revised at noon on September 10. This reference table
# does not reconstruct the rates in force before then.
_COVERAGE_START = datetime.fromisoformat(RATE_BASIS['coverage_from']).timestamp()
_PEAK = {'deepseek-flash': ('0.04', '2', '8'), 'deepseek-v4-pro': ('0.30', '9', '27')}
_ALIASES = {'deepseek-v4.1-flash-1017': 'deepseek-flash', 'deepseek-v4.1-flash': 'deepseek-flash',
            'deepseek-v4-flash': 'deepseek-flash', 'deepseek-v4-flash-vision-exp': 'deepseek-flash',
            'deepseek-v4-pro-0813': 'deepseek-v4-pro'}
# State Council published days off (MMDD inclusive). Weekend make-up working
# days remain off-peak under the provider's separate weekend rule.
# https://www.gov.cn/gongbao/2025/issue_12406/202511/content_7048922.html
# https://www.gov.cn/zhengce/content/202611/content_7085101.htm
_DAYS_OFF = {
    2026: ((101, 103), (215, 223), (404, 406), (501, 505), (619, 621), (925, 927), (1001, 1007)),
    2027: ((101, 103), (205, 213), (403, 405), (501, 505), (609, 609), (915, 915), (1001, 1007)),
}


def billing_period(at):
    local = datetime.fromtimestamp(at, LOCAL)
    if local.weekday() >= 5 or not (9 <= local.hour < 12 or 14 <= local.hour < 18):
        return 'off_peak'
    holidays = _DAYS_OFF.get(local.year)
    if holidays is None:
        return None
    day = local.month * 100 + local.day
    return 'off_peak' if any(start <= day <= end for start, end in holidays) else 'peak'


def estimate(provider, model, numbers, at, time_basis):
    keys = ('cache_hit', 'cache_miss', 'output')
    if provider != 'deepseek' or not isinstance(model, str) or at < _COVERAGE_START:
        return None
    model = model.lower()
    rates = _PEAK.get(_ALIASES.get(model, model))
    period = billing_period(at)
    if rates is None or period is None or any(numbers[key] is None for key in keys):
        return None
    cost = sum(Decimal(rate) * numbers[key] for rate, key in zip(rates, keys)) / 1_000_000
    amount = format(cost / 2 if period == 'off_peak' else cost, 'f')
    return {'amount': amount, 'period': period, 'time_basis': time_basis,
            'low': amount, 'high': amount, 'currency': 'CNY'}
