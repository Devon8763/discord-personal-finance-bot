"""Reusable life-expense operations with no Discord UI dependency."""
import spending as sp

ExpenseUnavailableError = sp.ExpenseUnavailableError
ExpenseRevisionConflictError = sp.ExpenseRevisionConflictError
RecurringUnavailableError = sp.RecurringUnavailableError
RecurringRevisionConflictError = sp.RecurringRevisionConflictError


def _user(value):
    return str(value)


def get_today():
    return sp.today()


def parse_month(value):
    return sp.month_date(value)


def _expense(value):
    result = dict(value)
    result['status'] = 'voided' if result['voided'] else 'active'
    result['origin'] = result['source']
    result['entry_type'] = 'consumption'
    return result


def add_expense(user_id, amount, category, note, spent_on=None, payment_source_id=None):
    return sp.add(_user(user_id), amount, category, note, spent_on, payment_source_id)


def get_expense(user_id, expense_id):
    return _expense(sp.get_expense(_user(user_id), expense_id))


def list_expenses(user_id, month, *, include_voided=False, limit=None, offset=0):
    result = sp.list_expenses(_user(user_id), month, include_voided, limit, offset)
    return {'items': [_expense(row) for row in result['items']], 'total': result['total']}


def list_expenses_in_range(user_id, start=None, end=None, *, keyword=''):
    result = sp.list_expenses_in_range(_user(user_id), start, end, keyword=keyword)
    return {'items': [_expense(row) for row in result['items']], 'total': result['total']}


def get_expense_comparison(user_id, a_start, a_end, b_start, b_end, *, category='', keyword='', as_of=None):
    return sp.expense_comparison(_user(user_id), a_start, a_end, b_start, b_end,
                                 category=category, keyword=keyword, as_of=as_of)


def get_expense_comparison_categories(user_id, *, as_of=None):
    return sp.expense_comparison_categories(_user(user_id), as_of=as_of)


def search_expenses(user_id, keyword='', start=None, end=None):
    return [_expense(row) for row in sp.search_expenses(_user(user_id), keyword, start, end)]


def update_expense(user_id, expense_id, amount, category, note, spent_on,
                   payment_source_id=None, expected_revision=None):
    return sp.edit(_user(user_id), expense_id, amount, category, note, spent_on,
                   payment_source_id, expected_revision)


def void_expense(user_id, expense_id, expected_revision=None):
    return sp.void_expense(_user(user_id), expense_id, expected_revision)


def preview_undo(user_id):
    action_id, expense_id = sp.undo(_user(user_id))
    return {'action_id': action_id, 'expense_id': expense_id}


def undo_latest_action(user_id, expected_action_id=None):
    owner = _user(user_id)
    action_id = expected_action_id
    if action_id is None:
        action_id, _ = sp.undo(owner)
    undone_action_id, expense_id = sp.undo(owner, action_id)
    return {'action_id': undone_action_id, 'expense_id': expense_id}


def get_calendar_days(user_id, month):
    return sp.calendar_days(_user(user_id), month)


def get_month_summary(user_id, month=None):
    return sp.month_report(_user(user_id), month)


def get_chart_data(user_id, month=None):
    return sp.chart_data(_user(user_id), month)


def get_categories(user_id, include_inactive=False):
    return sp.category_names(_user(user_id), include_inactive)


def get_payment_sources(user_id, include_inactive=False, *, initialize_defaults=True):
    return sp.payment_sources(_user(user_id), include_inactive,
                              initialize_defaults=initialize_defaults)


def get_recent_expenses(user_id):
    return [_expense(row) for row in sp.recent_expenses(_user(user_id))]


def get_recurring_expenses(user_id):
    return sp.recurring_expenses(_user(user_id))


def get_recurring_start_months():
    return sp.recurring_start_months()


def get_fixed_recurring_rules(user_id):
    return sp.list_fixed_recurring(_user(user_id))


def get_fixed_recurring(user_id, key):
    return sp.get_fixed_recurring(_user(user_id), key)


def add_fixed_recurring(user_id, name, amount, category, start, due_day):
    return sp.add_recurring(_user(user_id), '固定', name, amount, category, start, due_day=due_day)


def update_fixed_recurring(user_id, key, name, amount, category, due_day, expected_revision):
    return sp.update_fixed_recurring(_user(user_id), key, name, amount, category, due_day, expected_revision)


def stop_fixed_recurring(user_id, key, expected_revision):
    return sp.stop_fixed_recurring(_user(user_id), key, expected_revision)


def sync_fixed_recurring(user_id):
    return sp.sync_fixed_recurring(_user(user_id))


def get_shortcuts(user_id, include_inactive=False):
    return sp.shortcuts(_user(user_id), include_inactive)


def get_reminder_levels(user_id):
    return sp.reminder_levels(_user(user_id))


def set_budget(user_id, month, category, amount):
    return sp.set_budget(_user(user_id), month, category, amount)


def clear_budget(user_id, month, category):
    return sp.clear_budget(_user(user_id), month, category)


def set_total_budget_to_category_sum(user_id, month):
    return sp.set_total_budget_to_category_sum(_user(user_id), month)


def add_category(user_id, name):
    return sp.set_category(_user(user_id), name, True)


def rename_category(user_id, old_name, new_name):
    return sp.rename_category(_user(user_id), old_name, new_name)


def disable_category(user_id, name):
    return sp.set_category(_user(user_id), name, False)


def add_payment_source(user_id, name):
    return sp.add_payment_source(_user(user_id), name)


def rename_payment_source(user_id, payment_source_id, name):
    return sp.rename_payment_source(_user(user_id), payment_source_id, name)


def disable_payment_source(user_id, payment_source_id):
    return sp.disable_payment_source(_user(user_id), payment_source_id)


def sync_recurring(user_id, as_of=None):
    return sp.sync_recurring(_user(user_id), as_of)


def get_onboarding_needed(user_id):
    return sp.onboarding_needed(_user(user_id))


def dismiss_onboarding(user_id):
    return sp.dismiss_onboarding(_user(user_id))


def get_monthly_closing(user_id, month):
    return sp.monthly_closing(_user(user_id), month)


def get_recorded_months(user_id):
    return sp.recorded_months(_user(user_id))


def get_fixed_burdens(user_id):
    result = sp.fixed_burdens(_user(user_id))
    return {**result, 'entries': [_expense(row) for row in result['entries']]}



def export_portable_backup(user_id):
    """The trusted caller supplies identity; no login is performed here."""
    import life_ledger_backup
    return life_ledger_backup.export_backup(_user(user_id))


def restore_portable_backup(user_id, payload):
    """Restore only into an empty life ledger owned by the trusted caller."""
    import life_ledger_backup
    return life_ledger_backup.restore_backup(_user(user_id), payload)
