"""Full completed-turn Chat context. Estimates never silently truncate history."""
from functools import lru_cache
import math
from pathlib import Path

CONTEXT_WINDOW=1048576
MAX_OUTPUT=393216

@lru_cache(maxsize=1)
def tokenizer():
    try:
        from tokenizers import Tokenizer
        return Tokenizer.from_file(str(Path(__file__).resolve().parents[3]/'runtime/deepseek-tokenizer/tokenizer.json'))
    except Exception:return None

@lru_cache(maxsize=32)
def text_tokens(text):
    tk=tokenizer()
    if tk:return len(tk.encode(text,add_special_tokens=False).ids)
    return math.ceil(sum(.3 if ord(c)<128 else .6 for c in text))

def count(messages):
    total=3
    for m in messages:
        total+=8
        value=m['content']
        if isinstance(value,str):total+=text_tokens(value)
        else:
            for p in value:total+=text_tokens(p.get('text','')) if p['type']=='text' else 1024
    return total

def status(tokens,limit=CONTEXT_WINDOW):
    ratio=tokens/limit
    return {'tokens':tokens,'limit':limit,'ratio':ratio,'warning':95 if ratio>=.95 else 80 if ratio>=.8 else 0,
            'estimated':True,'no_compression':True}
