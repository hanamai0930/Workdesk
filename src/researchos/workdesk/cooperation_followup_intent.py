"""Recognize explicit relative changes to a cooperation dispatch grant."""

import re
import unicodedata

from .contracts import bounded_text

LIMITS = {'add_rounds': 4, 'add_attempts': 1000, 'add_hours': 168}
_SEPARATORS = ',，、;；.。!?！？\n'
_SENTENCE_END = '.。!?！？;；\n'
_QUOTES = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"', "'": "'", '`': '`'}
_CN_DIGITS = {word: i for i, word in enumerate('零一二三四五六七八九')} | {'〇': 0, '两': 2}
_CN_PLACES = {'千': 1000, '百': 100, '十': 10}
_EN_DIGITS = {word: i for i, word in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split())}
_EN_TENS = {word: i * 10 for i, word in enumerate(
    'twenty thirty forty fifty sixty seventy eighty ninety'.split(), 2)}
_EN_NUMERAL = r'(?:' + '|'.join((*_EN_DIGITS, *_EN_TENS, 'hundred', 'thousand', 'half')) + r')'
_NUMBER = (r'(?:[+-]?[0-9]{1,12}(?:\.[0-9]{1,8})?|[零〇一二两三四五六七八九十百千半]{1,12}|'
           + _EN_NUMERAL + r'(?:[ -]+' + _EN_NUMERAL + r'){0,3})')

_ZH_TARGET = (r'(?:(?:PDCA\s*|决策|总)?轮数|(?:工作|派发)回合(?:上限)?|回合上限|'
              r'派发(?:上限|次数|时限)|(?:执行)?时限|授权(?:时长|时限|次数)|截止时间|到期时间)')
_ZH_COMMAND = (r'(?:请\s*)?(?:帮我\s*)?(?:现在\s*|本次\s*)?(?:(?:把|将)?\s*' + _ZH_TARGET + r'\s*)?'
               r'(?:再\s*(?:跑|做|执行)|(?:再\s*|多\s*)?(?:加(?:上)?|给|增加|添加|追加|延长|续))\s*')
_EN_TARGET = r'(?:(?:the\s+)?(?:PDCA rounds?|(?:dispatch|work)(?:[- ]rounds?|[- ]limit|[- ]time)|deadline|duration)\s+by\s+)?'
_EN_COMMAND = r'(?:please\s+)?(?:now\s+)?(?:add|increase|extend|allow|run)\s+' + _EN_TARGET + r'(?:by\s+|another\s+)?'
_HEAD = re.compile(r'(?:' + _ZH_COMMAND + r'|' + _EN_COMMAND + r')', re.I)
_LINK = re.compile(r'\s*(?:并(?:且)?|以及|和|\band\b)\s*', re.I)
_UNITS = re.compile(
    rf'(?P<number>{_NUMBER})(?:\s*个)?\s*(?:more\s+)?(?:'
    r'(?P<attempts>(?:工作|派发|执行)?回合|次\s*(?:新增)?\s*派发|(?:work|dispatch)[- ](?:rounds?|turns?|attempts?)|dispatches|attempts?)'
    r'|(?P<rounds>轮\s*(?:PDCA|决策)?|(?:PDCA\s+|decision\s+)?rounds?)'
    r'|(?P<hours>小时|钟头|hours?|hrs?|h)|(?P<days>天|日|days?|d))'
    r'\s*(?:吧|即可|please)?', re.I)

_DEFERRED = re.compile(
    r'^(?:如果|若|假如|要是|等到?|以后|下次|之后|稍后|明天|后天|可否|是否|能否|能不能|可以|可能|比如|例如|举例|假设|'
    r'e\.g\.|i\.e\.|\b(?:if|unless|when|once|after|later|tomorrow|next time|could|would|can|perhaps|maybe|for example)\b)', re.I)
_NEGATED = re.compile(
    r'^(?:请\s*)?(?:先\s*)?(?:别|不要|不用|不必|无需|不再|禁止|不(?:加|增加|追加|延长|续))|'
    r'^(?:please\s+)?(?:do not|don\x27t|never|no need to)\b', re.I)
_CONTROL = re.compile(
    _ZH_TARGET + r'|PDCA|决策轮数|工作回合|次派发|回合|时限|授权|'
    r'\b(?:rounds?|attempts?|work turns?|dispatch(?:es| limit| time)?|deadline|duration|hours?|days?)\b', re.I)
_CONTROL_WITH_QUANTITY = re.compile(rf'{_NUMBER}\s*(?:个\s*)?(?:轮|回合|次派发|小时|钟头|天|\b(?:rounds?|hours?|days?|h|d)\b)', re.I)
_CANCEL_EXECUTION = re.compile(r'(?:真的\s*)?(?:启动|执行|派发|应用)|\b(?:execute|apply|dispatch|start)\b', re.I)
_OPERATOR_QUERY = re.compile(
    r'^(?:请问\s*)?(?:还(?:有|剩|能|可以)|(?:离|距)(?:结束|到期|截止)|(?:什么时候|何时)(?:能|结束|到期|截止)|'
    r'\b(?:how much (?:time|is left)|how long (?:is|until)|how many (?:are left|remain)|can (?:we|I|you) (?:continue|resume|stop))\b)', re.I)
_REVISE = re.compile(
    r'(?:修改|修正|调整|润色|重写|改写|补充|加入|补上|写入|写到|写进|缩短|删掉|删除|核实|细化|整理|精简|重排|改善|'
    r'\b(?:revise|edit|rewrite|polish|shorten|remove|clarify|correct|check|include|add|expand|reorganize)\b)', re.I)
_ARTIFACT = re.compile(
    r'(?:报告|文章|结构|论点|试验|实验|示例|措辞|表达|段落|证据|引用|引文|文献|对照|图表|表格|'
    r'标题|结论|摘要|正文|格式|代码|文件|交付物|\b(?:report|article|document|section|paragraph|citation|'
    r'reference|example|table|evidence|chart|format|code|file|summary|title|conclusion)s?\b)', re.I)
_ABSOLUTE = re.compile(
    r'^(?:请\s*)?(?:(?:把|将)?\s*' + _ZH_TARGET + r'\s*)?'
    r'(?:设(?:成|为|置)|调(?:整)?(?:到|至|成|为)|改(?:到|至|成|为)|延长(?:到|至|为)|'
    r'减少|减去|缩短|降低|取消|跳过|替换授权|[+-]\s*\d)'
    r'|^(?:please\s+)?(?:set|change|replace|reduce|decrease|remove|cancel|skip|extend\s+to)\b', re.I)
_AMBIGUOUS = '请分别写明要在当前授权上增加多少，例如「再加两轮 PDCA」「再加十个工作回合」或「延长 6 小时」。'


def _sentences(note):
    """Group clauses outside quotes so a condition applies to the whole sentence."""
    abbreviation_dots = {i for match in re.finditer(r'\b(?:e\.g\.|i\.e\.)', note, re.I)
                         for i in (match.start() + 1, match.start() + 3)}
    start = 0
    quote = None
    quoted = False
    sentence = []
    unquoted = []
    for i, char in enumerate(note):
        if quote:
            if char == quote and (not i or note[i - 1] != '\\'):
                quote = None
            continue
        if char in _QUOTES:
            if char != "'" or not 0 < i < len(note) - 1 or not (note[i - 1].isalnum() and note[i + 1].isalnum()):
                quote = _QUOTES[char]
                quoted = True
                unquoted.append(' ')
                continue
        elif char in _SEPARATORS:
            internal_period = char == '.' and (i in abbreviation_dots or
                0 < i < len(note) - 1 and note[i - 1].isdecimal() and note[i + 1].isdecimal())
            if not internal_period:
                sentence.append((note[start:i], char, quoted, ''.join(unquoted)))
                start, quoted, unquoted = i + 1, False, []
                if char in _SENTENCE_END:
                    yield sentence
                    sentence = []
                continue
        unquoted.append(char)
    if start < len(note):
        sentence.append((note[start:], '', quoted, ''.join(unquoted)))
    if sentence:
        yield sentence


def _chinese_number(token):
    if len(token) == 1 and token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if token == '半':
        return 0.5
    total, pending, previous, zero = 0, None, 10000, False
    for char in token:
        if char in ('零', '〇'):
            if not total or pending is not None or zero:
                raise ValueError(_AMBIGUOUS)
            zero = True
            continue
        if char in _CN_DIGITS and pending is None:
            pending = _CN_DIGITS[char]
        elif char in _CN_PLACES and _CN_PLACES[char] < previous:
            if pending is None and (total or char != '十'):
                raise ValueError(_AMBIGUOUS)
            place = _CN_PLACES[char]
            if total and zero != (place * 10 < previous):
                raise ValueError(_AMBIGUOUS)
            previous, zero = place, False
            total += (pending if pending is not None else 1) * previous
            pending = None
        else:
            raise ValueError(_AMBIGUOUS)
    if (zero and pending is None) or (pending is not None and total and zero != (previous > 10)):
        raise ValueError(_AMBIGUOUS)
    return total + (pending or 0)


def _number(token):
    if token[0].isascii() and (token[0].isdigit() or token[0] in '+-'):
        return float(token) if '.' in token else int(token)
    if token[0].isascii():
        words = token.lower().replace('-', ' ').split()
        if words == ['half']:
            return 0.5
        if len(words) == 1:
            if words[0] in _EN_DIGITS or words[0] in _EN_TENS:
                return _EN_DIGITS.get(words[0], _EN_TENS.get(words[0]))
            raise ValueError(_AMBIGUOUS)
        first, second, *rest = words
        if second in {'hundred', 'thousand'} and first in _EN_DIGITS and 1 <= _EN_DIGITS[first] <= 9:
            return _EN_DIGITS[first] * (100 if second == 'hundred' else 1000) + (_number(' '.join(rest)) if rest else 0)
        if not rest and first in _EN_TENS and second in _EN_DIGITS and 1 <= _EN_DIGITS[second] <= 9:
            return _EN_TENS[first] + _EN_DIGITS[second]
        raise ValueError(_AMBIGUOUS)
    return _chinese_number(token)


def _mentions_settings(clause):
    return _CONTROL.search(clause) or _CONTROL_WITH_QUANTITY.search(clause)


def _declines_execution(clause, negative):
    return negative and _CANCEL_EXECUTION.match(clause[negative.end():].strip())


def _normalized(clause):
    return unicodedata.normalize('NFKC', clause.strip()).replace('’', "'")


def _increments(clause):
    head = _HEAD.match(clause)
    if not head:
        if _ABSOLUTE.match(clause) and _mentions_settings(clause):
            raise ValueError(_AMBIGUOUS)
        return
    pieces = _LINK.split(clause)
    pairs = []
    for piece in pieces:
        verb = _HEAD.match(piece)
        quantity = piece[verb.end():] if verb else piece
        if verb and verb[0].lower().rstrip().endswith(' another') and re.fullmatch(
                r'(?:PDCA |decision |work |dispatch )?(?:round|turn|attempt)|hour|day', quantity, re.I):
            quantity = '1 ' + quantity
        match = _UNITS.fullmatch(quantity)
        if not match:
            if _mentions_settings(clause):
                raise ValueError(_AMBIGUOUS)
            return
        key = 'add_attempts' if match['attempts'] else 'add_rounds' if match['rounds'] else 'add_hours'
        amount = _number(match['number'])
        if match['days']:
            amount *= 24
        if not 0 < amount <= LIMITS[key] or (key != 'add_hours' and not isinstance(amount, int)):
            raise ValueError('追加数量须为正数，PDCA 轮数和工作回合须为整数。')
        pairs.append((key, float(amount) if key == 'add_hours' else amount))
    return pairs


def is_authorization_comment(text):
    """Suppress implicit continuation for questions, settings mentions, or refusals."""
    control = False
    for sentence in _sentences(text):
        if sentence[-1][1] in ('?', '？'):
            control = True
        for raw, _, _, unquoted in sentence:
            clause = _normalized(unquoted)
            negative = _NEGATED.match(clause)
            if not negative and _REVISE.search(clause) and _ARTIFACT.search(clause):
                return False
            if _mentions_settings(_normalized(raw)) or _OPERATOR_QUERY.match(clause) or _declines_execution(clause, negative):
                control = True
    return control


def parse_followup(text):
    """Return preserved feedback and safe additive settings, without changing state."""
    note = bounded_text(text, '后续意见', 16000)
    result = {'feedback': '', 'add_rounds': 0, 'add_attempts': 0, 'add_hours': 0.0}
    parts = []
    conflicting = False
    for sentence in _sentences(note):
        normalized = [_normalized(raw) for raw, _, _, _ in sentence]
        if sentence[-1][1] in ('?', '？') or any(_DEFERRED.match(clause) for clause in normalized):
            parts.extend(raw + delimiter for raw, delimiter, _, _ in sentence)
            continue
        for (raw, delimiter, quoted, _), clause in zip(sentence, normalized):
            negative = _NEGATED.match(clause) if not quoted else None
            if negative and (_mentions_settings(clause) or _declines_execution(clause, negative)):
                conflicting = True
            changes = None if quoted or negative or not clause else _increments(clause)
            if changes:
                for key, amount in changes:
                    if result[key]:
                        raise ValueError('同一项授权请只填写一次追加数量。')
                    result[key] = amount
            else:
                parts.append(raw + delimiter)
    if conflicting and any(result[key] for key in LIMITS):
        raise ValueError('同一条消息包含追加和取消追加的指令，请确认要修改的授权。')
    result['feedback'] = ''.join(parts).strip().rstrip(',，、;；').rstrip()
    return result
