"""Pure TWD rules. Callers supply dates and already-scoped consumption records."""
import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal('1000000000'):
            raise ValueError()
        cents = number * 100
        if cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise ValueError('金額需為正數，最多兩位小數且不超過十億元')


def month_date(value):
    try:
        result = date.fromisoformat(value + '-01')
        if result.strftime('%Y-%m') != value:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('月份格式：YYYY-MM')


def next_month(value):
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def recurring_due_date(month, due_day):
    due_day = due_day if due_day is not None else 1
    if type(due_day) is not int or not 1 <= due_day <= 31:
        raise ValueError('預定扣款日無效，請檢查規則')
    return month.replace(day=min(due_day, calendar.monthrange(month.year, month.month)[1]))


def budget_cents(amount):
    if type(amount) is int:
        whole_twd = amount
    elif isinstance(amount, str) and amount.isascii() and amount.isdigit():
        whole_twd = int(amount)
    else:
        raise ValueError('預算金額需為正整數台幣')
    if not 0 < whole_twd <= 1_000_000_000:
        raise ValueError('預算金額需為正整數台幣且不超過十億元')
    return whole_twd * 100


def validate_budget_totals(budgets):
    if '總額' in budgets and sum(v for k,v in budgets.items() if k != '總額') > budgets['總額']:
        raise ValueError('分類預算合計不可超過總預算')


def category_totals(entries):
    totals = {}
    for entry in entries:
        totals[entry['category']] = totals.get(entry['category'], 0) + entry['cents']
    return totals


def comparison_period(start, end, as_of):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError('日期請使用 YYYY-MM-DD 且日期須存在') from None
    if first.isoformat() != start or last.isoformat() != end or first > last:
        raise ValueError('請確認日期格式與期間順序')
    started = first <= as_of
    return dict(start=start, end=end, days=(last-first).days+1, started=started, unfinished=last>as_of,
                actual_start=start if started else None, actual_end=min(last, as_of).isoformat() if started else None)


def comparison_daily(items, start, end):
    daily = {}
    for item in items:
        daily[item['spent_on']] = daily.get(item['spent_on'], 0) + item['cents']
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    points = []
    for offset in range((last-first).days+1):
        on = (first+timedelta(days=offset)).isoformat()
        points.append(dict(day=offset+1, date=on, cents=daily.get(on, 0)))
    return points


def compare_expenses(a_start, a_end, b_start, b_end, a_items, b_items, *, as_of, category_options):
    """Aggregate prefiltered records; ownership, validity and filters belong to the caller.

    Records need only spent_on, category and integer cents, within each actual range.
    This function does not read a clock, apply storage filters or change its inputs.
    """
    a, b = (comparison_period(start, end, as_of) for start, end in ((a_start,a_end),(b_start,b_end)))
    for period, items in ((a, a_items), (b, b_items)):
        if not period['started']:
            period.update(total_cents=None, record_count=None, categories={}, daily=[])
            continue
        totals = category_totals(items)
        period.update(total_cents=sum(totals.values()), record_count=len(items), categories=totals,
                      daily=comparison_daily(items, period['actual_start'], period['actual_end']))
    comparable = a['started'] and b['started']
    difference = a['total_cents']-b['total_cents'] if comparable else None
    percent = Decimal(difference)*100/Decimal(b['total_cents']) if comparable and b['total_cents'] else None
    categories = []
    for name in sorted(a['categories'].keys() | b['categories'].keys()):
        a_cents = a['categories'].get(name,0) if a['started'] else None
        b_cents = b['categories'].get(name,0) if b['started'] else None
        a_percent = Decimal(a_cents)*100/Decimal(a['total_cents']) if a['total_cents'] else None
        b_percent = Decimal(b_cents)*100/Decimal(b['total_cents']) if b['total_cents'] else None
        categories.append(dict(category=name, a_cents=a_cents, b_cents=b_cents, a_percent=a_percent, b_percent=b_percent,
                               difference_cents=a_cents-b_cents if comparable else None))
    return dict(as_of=as_of.isoformat(), a=a, b=b, categories=categories, category_options=category_options,
                difference_cents=difference, change_percent=percent,
                periods_differ=a['days']!=b['days'] or a['unfinished'] or b['unfinished'])
