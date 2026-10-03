import { activeExpenses, ordered } from './browse.mjs';
import { cents, isoDate, ValidationError } from './rules.mjs';

export function validateCsvRange(query, today) {
  isoDate(today);
  const start = isoDate(query.start), end = isoDate(query.end);
  if (start > end) throw new ValidationError('開始日期不可晚於結束日期。');
  return {start, end};
}

function safeText(value) {
  let controls = false;
  for (const character of value) {
    const control = /[\p{Cc}\p{Cf}]/u.test(character);
    if (/\s/u.test(character) || control) { controls ||= control; continue; }
    return controls || '=+-@'.includes(character) ? "'" + value : value;
  }
  return controls ? "'" + value : value;
}
const cell = value => /[",\r\n]/u.test(value) ? '"' + value.replaceAll('"', '""') + '"' : value;
function decimalAmount(value) {
  const total = cents(value);
  const fraction = (total % 100n).toString().padStart(2, '0').replace(/0+$/, '');
  return (total / 100n).toString() + (fraction ? '.' + fraction : '');
}

export function expenseCsv(state, query, today) {
  const {start, end} = validateCsvRange(query, today);
  const rows = ordered(activeExpenses(state, today).filter(row => row.spent_on >= start && row.spent_on <= end));
  if (!rows.length) return {count:0, payload:null};
  const lines = ['日期,金額,消費項目,分類,付款方式'];
  for (const row of rows) lines.push([
    row.spent_on, decimalAmount(row.cents),
    ...[row.note, row.category, row.payment_source_name].map(safeText),
  ].map(cell).join(','));
  // ponytail: memory output uses the existing ledger capacity; never truncate an export.
  return {count:rows.length, payload:new TextEncoder().encode('\uFEFF' + lines.join('\r\n') + '\r\n')};
}
