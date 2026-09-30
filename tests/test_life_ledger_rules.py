"""Portable rule checks: stdlib only, no database, OAuth or website startup."""
import copy
from datetime import date
from decimal import Decimal
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests/fixtures/life_ledger_rules.json').read_text(encoding='utf-8'))


class LifeLedgerRulesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('life_ledger_rules'), '缺少可獨立匯入的純規則模組')
        self.rules = importlib.import_module('life_ledger_rules')

    def test_exact_money_cases(self):
        for case in CASES['amounts']:
            with self.subTest(case=case):
                if case.get('invalid'):
                    with self.assertRaisesRegex(ValueError, '金額需為正數'):
                        self.rules.money(case['input'])
                else:
                    self.assertEqual(self.rules.money(case['input']), case['cents'])
                    self.assertIs(type(self.rules.money(case['input'])), int)

    def test_month_and_short_month_cases(self):
        for case in CASES['due_dates']:
            month = self.rules.month_date(case['month'])
            self.assertEqual(self.rules.recurring_due_date(month, case['day']).isoformat(), case['date'])
        self.assertEqual(self.rules.next_month(date(2026, 12, 1)), date(2027, 1, 1))
        for value in ('2026-9', '2026-13', '2026/09', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '月份格式'):
                self.rules.month_date(value)
        for value in (0, 32, True, '1'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '預定扣款日無效'):
                self.rules.recurring_due_date(date(2026, 9, 1), value)

    def test_budget_cases_and_category_total_limit(self):
        for case in CASES['budgets']:
            with self.subTest(case=case):
                if case.get('invalid'):
                    with self.assertRaisesRegex(ValueError, '預算金額需為正整數'):
                        self.rules.budget_cents(case['input'])
                else:
                    self.assertEqual(self.rules.budget_cents(case['input']), case['cents'])
        for settings in ({'餐飲': 200}, {'總額': 300, '餐飲': 200, '交通': 100}):
            self.rules.validate_budget_totals(settings)
        with self.assertRaisesRegex(ValueError, '分類預算合計不可超過總預算'):
            self.rules.validate_budget_totals({'總額': 299, '餐飲': 200, '交通': 100})

    def test_comparison_cases_preserve_daily_totals_and_future_states(self):
        for case in CASES['comparisons']:
            with self.subTest(case=case):
                original = copy.deepcopy(case)
                options = [{'name': '餐飲', 'state': 'inactive'}]
                result = self.rules.compare_expenses(*case['a'], *case['b'], case['a_items'], case['b_items'],
                                                    as_of=date.fromisoformat(case['as_of']), category_options=options)
                self.assertEqual(case, original)
                self.assertEqual(result['category_options'], options)
                self.assertEqual(result['as_of'], case['as_of'])
                self.assertEqual(result['difference_cents'], case['difference'])
                expected_percent = Decimal(case['change_percent']) if case['change_percent'] is not None else None
                self.assertEqual(result['change_percent'], expected_percent)
                self.assertEqual(result['periods_differ'], case['periods_differ'])
                self.assertEqual([r['category'] for r in result['categories']], sorted({r['category'] for r in case['a_items']+case['b_items']}))
                for key in ('a', 'b'):
                    period = result[key]
                    self.assertEqual(period['total_cents'], case[key+'_total'])
                    self.assertEqual([p['cents'] for p in period['daily']], case[key+'_daily'])
                    self.assertEqual([p['day'] for p in period['daily']], list(range(1, len(period['daily'])+1)))
                    if period['started']:
                        self.assertEqual(sum(p['cents'] for p in period['daily']), period['total_cents'])
                        self.assertEqual(sum(period['categories'].values()), period['total_cents'])
                        self.assertEqual(period['daily'][-1]['date'], period['actual_end'])
                        self.assertEqual(period['record_count'], len(case[key+'_items']))
                    else:
                        self.assertIsNone(period['record_count'])
                        self.assertIsNone(period['actual_start'])
                for row in result['categories']:
                    for key in ('a', 'b'):
                        cents, total = row[key+'_cents'], result[key]['total_cents']
                        self.assertEqual(row[key+'_percent'], Decimal(cents)*100/Decimal(total) if total else None)

    def test_comparison_validation_remains_strict(self):
        for start, end in (('2026-9-01', '2026-09-02'), ('2026-02-30', '2026-03-01'), ('2026-09-02', '2026-09-01')):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.rules.comparison_period(start, end, date(2026, 9, 24))

    def test_import_and_calculation_do_not_load_platform_or_storage(self):
        # A fresh interpreter prevents earlier integration-test imports masking coupling.
        script = '''
import importlib.util, sys
spec = importlib.util.spec_from_file_location('life_ledger_rules', sys.argv[1])
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
assert rules.money('0.29') == 29
assert rules.budget_cents(1) == 100
for name in ('db', 'ledger', 'sqlite3', 'discord', 'web', 'dotenv', 'presentation', 'portfolio'):
    assert name not in sys.modules, name
'''
        result = subprocess.run([sys.executable, '-I', '-c', script, str(ROOT/'life_ledger_rules.py')],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
