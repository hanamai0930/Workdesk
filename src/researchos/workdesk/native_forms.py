"""Validate the primitive form subset exposed by native MCP elicitations."""
import math


def validate(schema,value):
    def check(s,v):
        variants=s.get('anyOf') or s.get('oneOf')
        if variants:
            for variant in variants:
                try:check(variant,v);return
                except ValueError:pass
            raise ValueError('表单选项不在允许值内。')
        if 'const' in s and v!=s['const']:raise ValueError('表单选项不匹配。')
        if 'enum' in s and v not in s['enum']:raise ValueError('请选择表单提供的选项。')
        t=s.get('type')
        if t=='object':
            if not isinstance(v,dict):raise ValueError('表单须为JSON对象。')
            props=s.get('properties',{})
            if set(v)-set(props) or set(s.get('required') or [])-set(v):raise ValueError('表单字段缺失或含未知字段。')
            for k,x in v.items():check(props[k],x)
        elif t=='array':
            if not isinstance(v,list) or len(v)<s.get('minItems',0) or len(v)>s.get('maxItems',float('inf')):raise ValueError('多选字段格式或数量不符。')
            for x in v:check(s.get('items',{}),x)
        elif t=='string':
            if not isinstance(v,str) or len(v)<(s.get('minLength') or 0) or len(v)>(s.get('maxLength') or float('inf')):raise ValueError('文本字段格式或长度不符。')
        elif t=='boolean':
            if type(v) is not bool:raise ValueError('布尔字段须为true或false。')
        elif t in {'integer','number'}:
            if type(v) not in {int,float} or not math.isfinite(v) or t=='integer' and not float(v).is_integer() or v<s.get('minimum',-float('inf')) or v>s.get('maximum',float('inf')):raise ValueError('数值字段格式或范围不符。')
        elif t is not None:raise ValueError('当前表单类型未接通。')
    check(schema,value)
