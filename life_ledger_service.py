"""Reusable life-expense operations with no Discord UI dependency."""
import spending as sp


def _user(value):
    return str(value)


def add_expense(user_id, amount, category, note, spent_on=None, payment_source_id=None):
    return sp.add(_user(user_id), amount, category, note, spent_on, payment_source_id)


def get_expense(user_id, expense_id):
    return sp.get_expense(_user(user_id), expense_id)


def list_expenses(user_id, month, *, include_voided=False, limit=None, offset=0):
    return sp.list_expenses(_user(user_id), month, include_voided, limit, offset)


def search_expenses(user_id, keyword='', start=None, end=None):
    return sp.search_expenses(_user(user_id), keyword, start, end)


def update_expense(user_id, expense_id, amount, category, note, spent_on,
                   payment_source_id=None, expected_revision=None):
    return sp.edit(_user(user_id), expense_id, amount, category, note, spent_on,
                   payment_source_id, expected_revision)


def get_calendar_days(user_id, month):
    return sp.calendar_days(_user(user_id), month)


def get_month_summary(user_id, month=None):
    return sp.month_report(_user(user_id), month)


def get_chart_data(user_id, month=None):
    return sp.chart_data(_user(user_id), month)


def get_categories(user_id, include_inactive=False):
    return sp.category_names(_user(user_id), include_inactive)


def get_payment_sources(user_id, include_inactive=False):
    return sp.payment_sources(_user(user_id), include_inactive)


def get_recent_expenses(user_id):
    return sp.recent_expenses(_user(user_id))
