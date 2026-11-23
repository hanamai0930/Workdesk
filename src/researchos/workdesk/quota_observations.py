"""Read-only official quota metadata. Never creates a model thread or a turn."""
import json
import math
import os
import re
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, build_opener

from .paths import clean_env, legacy


QWEN_QUOTA_PROFILE='workdesk-quota'


def qwen_cli(locations,source):
    from .workspace import plain

    name={'free':'bl.exe','cash':'aliyun.exe'}[source]
    try:
        executable=plain(locations.runtime/'qwen-account-tools'/name,locations.runtime)
    except (OSError,ValueError):
        return None
    return str(executable) if os.name=='nt' and executable.is_file() else None


def _billing_paths():
    # Match the official Windows CLI's home lookup, including redirected homes.
    home = os.environ.get('HOMEDRIVE', '') + os.environ.get('HOMEPATH', '')
    directory = Path(home or os.environ['USERPROFILE']) / '.aliyun'
    return directory / 'workdesk-billing.json', directory / 'config.json'


def _status_output(executable, args):
    result = subprocess.run([executable, *args], env=clean_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or len(result.stdout) > 262144:
        raise ValueError('官方工具未返回登录状态。')
    return result.stdout


def _billing_profile_present(executable, path):
    if not path.is_file():
        return False
    # The CLI lists masked credential hints; retain only the Boolean result.
    output = _status_output(executable, ['configure', 'list', '--config-path', str(path)])
    for line in output.decode('utf-8').splitlines():
        fields = [value.strip() for value in line.split('|')]
        if len(fields) >= 3 and fields[0].removesuffix(' *') == QWEN_QUOTA_PROFILE:
            return fields[2] == 'Valid' and bool(re.fullmatch(r'OAuth:.+@(CN|INTL)', fields[1]))
    return False


def _billing_profile(executable):
    private, legacy = _billing_paths()
    if _billing_profile_present(executable, private):
        return private, True
    if _billing_profile_present(executable, legacy):
        return legacy, True
    return private, False


def qwen_console_configured(locations, source):
    """Local profile evidence only. None means that the tool could not check."""
    executable = qwen_cli(locations, source)
    if not executable:
        return None
    try:
        if source == 'cash':
            return _billing_profile(executable)[1]
        status = json.loads(_status_output(executable, ['--config', QWEN_QUOTA_PROFILE,
                                                       'auth', 'status', '--output', 'json']))
        if status.get('config') != QWEN_QUOTA_PROFILE or type(status.get('authenticated')) is not bool:
            return None
        console = status.get('console') or {}
        return status['authenticated'] and console.get('source') == 'config' and bool(console.get('masked'))
    except (ValueError, OSError, AttributeError, subprocess.TimeoutExpired):
        return None


def open_qwen_console_login(locations,source):
    executable=qwen_cli(locations,source)
    if not executable:raise ValueError('本机缺少对应的阿里云官方工具。')
    try:
        if source == 'cash':
            private, _ = _billing_paths()
            private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                # With a nonexistent --config-path the CLI imports its global profiles.
                # Seed only our profile so login cannot change the global default.
                with private.open('x', encoding='utf-8') as out:
                    json.dump({'current': QWEN_QUOTA_PROFILE, 'profiles': [{
                        'name': QWEN_QUOTA_PROFILE, 'mode': 'OAuth', 'oauth_site_type': 'CN',
                        'region_id': 'cn-hangzhou', 'language': 'zh'}]}, out)
            except FileExistsError:
                pass
            args = ['configure', '--profile', QWEN_QUOTA_PROFILE, '--mode', 'OAuth', '--config-path', str(private)]
        else:
            args = ['--config',QWEN_QUOTA_PROFILE,'auth','login','--console','--output','text']
        return subprocess.Popen([executable,*args],cwd=locations.runtime/'qwen-account-tools',
                                stdin=subprocess.DEVNULL if source == 'cash' else None,
                                env=clean_env(),creationflags=(subprocess.CREATE_NO_WINDOW if source == 'cash'
                                                              else subprocess.CREATE_NEW_CONSOLE))
    except OSError:
        raise ValueError('无法打开官方授权窗口。') from None


def read_qwen_console(locations,source):
    executable=qwen_cli(locations,source)
    if not executable:raise ValueError('缺少对应的阿里云官方查询工具。')
    if source=='free':
        from .chat_api import API_MODELS
        args=['--config',QWEN_QUOTA_PROFILE,'usage','free','--model',','.join(API_MODELS['qwen']),'--output','json']
    else:
        try:
            profile_path, configured = _billing_profile(executable)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ValueError('无法核实本机账单授权配置。') from None
        if not configured:
            raise ValueError('请先连接账单账号，再查询余额。')
        args=['bssopenapi','QueryAccountBalance','--profile',QWEN_QUOTA_PROFILE,'--region','cn-hangzhou',
              '--config-path',str(profile_path)]
    try:
        result=subprocess.run([executable,*args],env=clean_env(),stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=16,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('阿里云官方额度查询未完成。') from None
    if result.returncode!=0 or len(result.stdout)>262144:
        raise ValueError('官方查询未完成，请核对阿里云登录或账单读取权限。')
    try:
        value=json.loads(result.stdout)
        if source=='free':
            if not isinstance(value,list) or len(value)>len(API_MODELS['qwen']):
                raise ValueError('百炼免费额度响应无效。')
            rows=[]
            for row in value:
                if row['model'] not in API_MODELS['qwen']:raise ValueError('百炼免费额度型号无效。')
                for key in ('remaining','total'):
                    if row[key] is not None and (type(row[key]) is not int or not 0<=row[key]<=2**53-1):
                        raise ValueError('百炼 Token 额度数值无效。')
                expires=row.get('expires')
                if expires is not None and (not isinstance(expires,str) or not re.fullmatch(r'[0-9 /:.+-]{1,35}',expires)):
                    expires=None
                rows.append({'model':row['model'],'remaining':row['remaining'],'total':row['total'],'expires':expires})
        else:
            if value.get('Success') is not True:raise ValueError('阿里云余额未返回结果。')
            row=value['Data'];currency=row['Currency']
            if currency not in ('CNY','USD','JPY'):raise ValueError('阿里云余额币种无效。')
            rows={'currency':currency,'available':money_text(row['AvailableAmount']),
                  'cash':money_text(row['AvailableCashAmount'])}
    except (KeyError,TypeError,AttributeError,UnicodeError,ValueError):
        raise ValueError('官方额度响应格式无法识别。') from None
    return {'source':source,'rows':rows,'observed_at':time.time(),'no_dispatch':True}


def money_text(value):
    if not isinstance(value,str) or not re.fullmatch(r'-?[0-9]{1,18}(?:\.[0-9]{1,12})?',value):
        raise ValueError('官方余额数值无效。')
    return value


def read_deepseek(secret):
    from .profiles import NoRedirect

    request = Request('https://api.deepseek.com/user/balance', headers={
        'Authorization': 'Bearer ' + secret.reveal_for_provider(), 'Accept': 'application/json'})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        body = json.loads(response.read(65537))
    if not isinstance(body,dict):raise ValueError('DeepSeek 余额响应格式无效。')
    balances = body.get('balance_infos')
    if type(body.get('is_available')) is not bool or not isinstance(balances, list) or len(balances) > 10:
        raise ValueError('DeepSeek 余额响应格式无效。')
    result = []
    for row in balances:
        if not isinstance(row, dict) or row.get('currency') not in ('CNY', 'USD'):
            raise ValueError('DeepSeek 余额币种无效。')
        amounts = {key: money_text(row.get(key)) for key in ('total_balance', 'granted_balance', 'topped_up_balance')}
        result.append({'currency': row['currency'], **amounts})
    return {'source': 'OFFICIAL_DEEPSEEK_API', 'buckets': [], 'balances': result,
            'is_available': body['is_available'], 'enforcement': 'INFORMATION_ONLY',
            'raw_identity_saved': False}


def number(value, maximum=None):
    if value is None:return None
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (maximum is not None and value>maximum):
        raise ValueError('官方额度数值无效。')
    return value


def label(value):
    if value is None:return None
    if not isinstance(value,str) or not 0<len(value)<=200 or any(ord(c)<32 for c in value):
        raise ValueError('官方额度标签无效。')
    return value


def window(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('官方额度窗口无效。')
    used=number(value.get('usedPercent'),100)
    return {'used_percent':used,'remaining_percent':None if used is None else 100-used,
            'duration_minutes':number(value.get('windowDurationMins')),
            'resets_at':number(value.get('resetsAt'))}


def normalize(value):
    if not isinstance(value,dict):raise ValueError('官方额度响应无效。')
    multiple=value.get('rateLimitsByLimitId')
    if multiple is not None:
        if not isinstance(multiple,dict) or len(multiple)>100:raise ValueError('官方额度桶无效。')
        items=list(multiple.items());source='rateLimitsByLimitId'
    else:
        one=value.get('rateLimits')
        if one is not None and not isinstance(one,dict):raise ValueError('官方额度桶无效。')
        items=[] if one is None else [(one.get('limitId') or 'legacy',one)];source='rateLimits'
    buckets=[]
    for key,row in items:
        if not isinstance(row,dict):raise ValueError('官方额度桶无效。')
        identity=label(key)
        if identity is None:raise ValueError('官方额度桶身份缺失。')
        # Credit balance is separate from subscription-window percentages.
        credits=row.get('credits');credit=None
        if credits is not None:
            if not isinstance(credits,dict):raise ValueError('官方额度现金字段无效。')
            credit={}
            for field in ('hasCredits','unlimited'):
                flag=credits.get(field)
                if flag is not None and type(flag) is not bool:raise ValueError('官方额度现金状态无效。')
                credit[field]=flag
            balance=credits.get('balance')
            if isinstance(balance,str):
                if len(balance)>60:raise ValueError('官方额度现金数值无效。')
                try:balance=float(balance)
                except ValueError:raise ValueError('官方额度现金数值无效。') from None
            credit['balance']=number(balance)
        buckets.append({'limit_id':identity,'name':label(row.get('limitName')),
            'primary':window(row.get('primary')),'secondary':window(row.get('secondary')),
            'plan_type':label(row.get('planType')),'limit_reached_type':label(row.get('rateLimitReachedType')),
            'credits':credit})
    return {'buckets':buckets,'response_view':source,'source':'OFFICIAL_CODEX',
            'enforcement':'INFORMATION_ONLY','raw_identity_saved':False}


def read_codex(locations):
    transport=legacy(locations,'codex-harness','rpc');executable=locations.codex
    if not executable:raise ValueError('未找到本机Codex程序。')
    env=clean_env();env['CODEX_HOME']=str(locations.codex_home)
    rpc=transport.Rpc(transport.codex_command(str(executable))+['-c','project_doc_max_bytes=0','-c','features.apps=false'],
                      locations.data,env,max_frame=None)
    deadline=time.monotonic()+16  # Leave time to close the owned reader within the HTTP timeout.
    def request(method,params,maximum=10):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('额度元数据读取超时。')
        return rpc.request(method,params,timeout=min(maximum,remaining))
    try:
        request('initialize',{'clientInfo':{'name':'workdesk_quota_reader','version':'1.0'},
                    'capabilities':{'experimentalApi':True}},5)
        rpc.send({'method':'initialized','params':{}})
        before=request('account/read',{'refreshToken':False})
        if (before.get('account') or {}).get('type')!='chatgpt':raise ValueError('需要原订阅登录；不回退API。')
        raw=request('account/rateLimits/read',{})
        after=request('account/read',{'refreshToken':False})
        def identity(metadata):
            account=metadata.get('account') or {}
            return {'type':account.get('type'),'email':account.get('email'),
                    'account_id':(metadata.get('workspaceRouting') or {}).get('chatgptAccountId')}
        if identity(before)!=identity(after):raise ValueError('查询期间账号变化，未保存额度。')
        result=normalize(raw);account=identity(before)
        email=account['email'];account_id=account['account_id'] or raw.get('accountId')
        # Match the existing runtime account-binding fingerprint format.
        fingerprint=sha256(json.dumps({'email':email.strip().casefold(),'account_id':account_id},sort_keys=True).encode()).hexdigest() if isinstance(email,str) and email.strip() and isinstance(account_id,str) and account_id else None
        return {**result,'account_fingerprint':fingerprint,
                'account_identity_status':'OBSERVED' if fingerprint else 'UNKNOWN'}
    finally:rpc.close()
