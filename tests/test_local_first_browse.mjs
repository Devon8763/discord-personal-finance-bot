import test from 'node:test';
import assert from 'node:assert/strict';
import { searchExpenses, calendarMonth, validateSearch } from '../local-first/browse.mjs';

const entry = (id, spent_on, cents, source = 'manual', extra = {}) => ({
  id, spent_on, cents, source, kind: 'consumption', voided: 0,
  note: '午餐', category: '餐飲', payment_source_name: '現金', ...extra,
});
const state = { expenses: [
  entry('a', '2024-02-29', '1234'),
  entry('b', '2024-02-29', '29', '固定'),
  entry('c', '2024-02-01', '100', '訂閱'),
  entry('d', '2023-12-31', '1', '分期'),
  entry('e', '2024-02-29', '999', 'manual', { voided: 1 }),
  entry('f', '2025-01-01', '9'),
  entry('g', '2024-02-29', '88', 'manual', { kind: 'investment' }),
] };

test('search uses inclusive ISO dates, literal keyword and all four active sources', () => {
  const result = searchExpenses(state, { keyword:' 午餐 ', start:'2023-12-31', end:'2024-02-29' }, '2024-03-01');
  assert.deepEqual(result.map(row => row.id), ['b', 'a', 'c', 'd']);
  assert.deepEqual(searchExpenses(state, { keyword:'餐', start:'2024-02-29', end:'2024-02-29' }, '2024-03-01').map(row => row.id), ['b','a']);
  assert.deepEqual(searchExpenses(state, { keyword:'不存在', start:'', end:'' }, '2024-03-01'), []);
  assert.deepEqual(searchExpenses(state, { keyword:'午餐', start:'', end:'' }, '2024-03-01').map(row => row.id), ['b','a','c','d']);
});

test('invalid search conditions reject before touching expenses', () => {
  const unreadable = { get expenses() { throw new Error('query ran'); } };
  for (const query of [
    { keyword:'', start:'', end:'' }, { keyword:' ', start:'', end:'2024-02-29' },
    { keyword:'x', start:'2024-02-30', end:'' }, { keyword:'x', start:'2024-03-02', end:'2024-03-01' },
    { keyword:'x', start:'2024-03-02', end:'' }, { keyword:'x', start:'', end:'2024-03-02' },
  ]) assert.throws(() => searchExpenses(unreadable, query, '2024-03-01'), error => error.message !== 'query ran');
});

test('form validation is possible before opening an IndexedDB read', () => {
  assert.throws(() => validateSearch({ keyword:' ', start:'', end:'2024-02-29' }, '2024-03-01'));
  assert.deepEqual(validateSearch({ keyword:'  午餐  ', start:'2024-02-29', end:'' }, '2024-03-01'),
    { keyword:'午餐', start:'2024-02-29', end:'2024-03-01' });
});

test('calendar starts Monday, marks only visible days and totals exact cents', () => {
  const month = calendarMonth(state, '2024-02', '2024-03-01');
  assert.equal(month.leadingBlanks, 3);
  assert.equal(month.days.length, 29);
  assert.deepEqual(month.days.filter(day => day.hasExpense).map(day => day.date), ['2024-02-01', '2024-02-29']);
  assert.equal(month.days[28].total, 1263n);
  assert.deepEqual(month.days[28].expenses.map(row => row.id), ['b','a']);
  assert.equal(calendarMonth(state, '2024-01', '2024-03-01').days.every(day => !day.hasExpense), true);
  assert.equal(calendarMonth(state, '2024-03', '2024-03-01').days[1].isFuture, true);
  assert.throws(() => calendarMonth(state, '2024-04', '2024-03-01'));
});

test('full result beyond first screen and malicious text stay data', () => {
  const many = { expenses: Array.from({ length: 65 }, (_, i) => entry(`e${i}`, '2024-02-29', '9007199254740993', 'manual', { note:'<img src=x onerror=alert(1)>' })) };
  assert.equal(searchExpenses(many, { keyword:'<img', start:'', end:'' }, '2024-03-01').length, 65);
  assert.equal(calendarMonth(many, '2024-02', '2024-03-01').days[28].total, 65n * 9007199254740993n);
});
