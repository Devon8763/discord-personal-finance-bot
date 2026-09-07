"""Compact user-facing numbers, AI output and one shared coverage note."""
import json
from decimal import Decimal

NOTE = 'ℹ️ 僅依已記錄資料統計。'


def number(value, signed=False):
    result = format(Decimal(str(value)), '+,.2f' if signed else ',.2f')
    return result.rstrip('0').rstrip('.')


class AIResponseError(ValueError):
    pass


def json_response(text):
    try:
        if not isinstance(text,str):
            raise ValueError()
        text = text.strip()
        if text.startswith('```') and text.endswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        value = json.loads(text)
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except (ValueError,TypeError):
        raise AIResponseError('AI 回覆未完成，請再試一次。') from None


SUMMARY_SCHEMA = {'type':'object','properties':{'points':{'type':'array','minItems':1,'maxItems':3,'items':{'type':'string','maxLength':60}}},'required':['points'],'additionalProperties':False}


def summary_text(raw):
    data = json_response(raw)
    points = data.get('points')
    if not isinstance(points,list) or not points or any(not isinstance(p,str) or not p.strip() for p in points):
        raise AIResponseError('AI 回覆未完成，請再試一次。')
    clean = []
    for point in points[:3]:
        text = ' '.join(point.split()).lstrip('-• ')
        # Shared footer replaces repetitive coverage disclaimers.
        if any(term in text for term in ('未記錄','無紀錄不代表','非銀行餘額','無法完全反映','僅依已記錄','僅反映已記錄')):
            continue
        if len(text)>60:
            text = text[:59]+'…'
        clean.append('• '+text)
    return '\n\n'.join(clean) if clean else '• 資料不足，暫不建議調整預算。'
