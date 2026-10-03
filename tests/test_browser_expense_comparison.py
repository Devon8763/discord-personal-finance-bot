"""Synthetic local-first aggregates agree with the existing Python comparison rules."""
import json
import shutil
import subprocess
import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from life_ledger_rules import compare_expenses, comparison_period

ROOT = Path(__file__).resolve().parents[1]


class BrowserExpenseComparisonTests(unittest.TestCase):
    def compare(self, dates, rows, today='2024-03-01', category='', keyword=''):
        query = dict(zip(('a_start', 'a_end', 'b_start', 'b_end'), dates)) | dict(category=category, keyword=keyword)
        script = """
          import {compareExpenses,comparisonDay} from './local-first/comparison.mjs';
          const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
          const input=JSON.parse(Buffer.concat(chunks));
          const r=compareExpenses(input.state,input.query,input.today);
          for(const key of ['a','b']){const p=r[key];p.daily=Array.from({length:p.actual_days},(_,i)=>comparisonDay(p,i));}
          process.stdout.write(JSON.stringify(r,(_key,value)=>typeof value==='bigint'?value.toString():value));
        """
        child = subprocess.run([shutil.which('node') or 'node', '--input-type=module', '-e', script], cwd=ROOT,
                               input=json.dumps(dict(state=dict(expenses=rows, categories=[]), query=query, today=today)),
                               capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
        actual = json.loads(child.stdout)
        valid = [row | dict(cents=int(row['cents'])) for row in rows
                 if row['kind'] == 'consumption' and row['voided'] == 0
                 and row['source'] in ('manual', '固定', '訂閱', '分期') and row['spent_on'] <= today
                 and (not category or row['category'] == category)
                 and (not keyword or keyword.lower() in row['note'].lower())]
        ranges = [comparison_period(*dates[offset:offset+2], date.fromisoformat(today)) for offset in (0, 2)]
        items = [[row for row in valid if period['started'] and period['actual_start'] <= row['spent_on'] <= period['actual_end']]
                 for period in ranges]
        expected = compare_expenses(*dates, *items, as_of=date.fromisoformat(today), category_options=[])
        for key in ('a', 'b'):
            for field in ('start', 'end', 'days', 'started', 'unfinished', 'actual_start', 'actual_end', 'record_count'):
                self.assertEqual(actual[key][field], expected[key][field])
            self.assertEqual(actual[key]['total_cents'], None if expected[key]['total_cents'] is None else str(expected[key]['total_cents']))
            self.assertEqual(actual[key]['daily'], [row | dict(cents=str(row['cents'])) for row in expected[key]['daily']])
        self.assertEqual(actual['periods_differ'], expected['periods_differ'])
        self.assertEqual(actual['difference_cents'], None if expected['difference_cents'] is None else str(expected['difference_cents']))
        percent = expected['change_percent']
        rounded = None if percent is None else format(percent.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.')
        if rounded in ('-0', ''):
            rounded = '0'
        self.assertEqual(actual['percent'], rounded.replace('-', '−') if rounded else rounded)
        self.assertEqual([row['name'] for row in actual['categories']], [row['category'] for row in expected['categories']])
        for browser, python in zip(actual['categories'], expected['categories']):
            for field in ('a_cents', 'b_cents', 'difference_cents'):
                self.assertEqual(browser[field], None if python[field] is None else str(python[field]))

    @staticmethod
    def rows():
        return [dict(spent_on=on, cents=str(amount), category=category, note='Lunch [x]', source=source, kind='consumption', voided=0)
                for on, amount, category, source in [('2023-12-31', 3, '單邊', 'manual'), ('2024-01-31', 7, '停用', '固定'),
                                                     ('2024-02-29', 29, '停用', '訂閱'), ('2024-03-01', 1, '歷史', '分期')]]

    def test_leap_year_overlap_half_year_and_future(self):
        for dates in [('2023-12-31', '2024-06-30', '2024-01-01', '2024-02-29'),
                      ('2024-02-29', '2024-03-01', '2024-03-01', '2024-03-01'),
                      ('2024-03-02', '2024-03-31', '2024-04-01', '2024-04-30'),
                      ('2024-03-01', '2024-03-31', '2024-01-01', '2024-01-31')]:
            self.compare(dates, self.rows())

    def test_filtered_empty_and_extreme_precise_totals(self):
        dates = ('2024-02-01', '2024-02-29', '2024-01-01', '2024-01-31')
        self.compare(dates, self.rows(), category='停用', keyword='LUNCH [x]')
        self.compare(dates, self.rows(), keyword='no match')
        rows = self.rows() + [self.rows()[2] | dict(cents='9223372036854775807') for _ in range(65)]
        self.compare(dates, rows)
