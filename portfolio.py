"""Shared validation and valuation; no Discord or network dependencies."""
import math


def normalize_symbol(symbol):
    symbol = symbol.strip().upper()
    for suffix in ('.TWO', '.TW'):
        if symbol.endswith(suffix):
            return symbol[:-len(suffix)]
    return symbol


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def value_position(name, cost, quantity, price, currency):
    if not positive(price):
        raise ValueError('價格必須是有效正數')
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 for x in (cost, quantity)):
        raise ValueError('持倉成本或數量無效')
    value = price * quantity
    profit = value - cost
    return dict(name=name, cost=cost, quantity=quantity, price=price,
                currency=currency, value=value, profit=profit,
                percent=profit / cost * 100 if cost else 0)
