import * as distribution from './vendor/lossless-json-4.3.1/lossless-json.js';
import { isoDate } from './rules.mjs';

// Official UMD exports in Node; its browser global is created by the same local file.
const { parse, stringify } = distribution.default ?? globalThis.LosslessJSON;
export const MAX_INTEGER = 9223372036854775807n;
export const MAX_BYTES = 64 * 1024 * 1024;
export const EXPENSE_FIELDS = ['id', 'spent_on', 'cents', 'category', 'note', 'source', 'recurring_id', 'period', 'voided', 'payment_source_id', 'payment_source_name', 'kind', 'revision'];
const FIELDS = {
  expenses: EXPENSE_FIELDS,
  categories: ['name', 'active'],
  payment_sources: ['id', 'name', 'active'],
  budgets: ['month', 'category', 'cents'],
  recurring_rules: ['id', 'name', 'cents', 'category', 'kind', 'start_month', 'periods', 'due_day', 'active', 'revision'],
  recurring_versions: ['id', 'recurring_id', 'effective_month', 'name', 'cents', 'category', 'due_day'],
  shortcuts: ['id', 'name', 'category', 'payment_source_id', 'note', 'cents', 'position', 'active'],
  actions: ['id', 'expense_id', 'before', 'undone'],
};
export const SECTIONS = [...Object.keys(FIELDS), 'settings'];
export class BackupError extends Error {
  constructor(kind) { super('生活帳本備份資料異常：' + kind); this.code = 'backup'; }
}
function invalid(kind) { throw new BackupError(kind); }
function shape(value, fields, kind) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value)) ||
      Object.keys(value).length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) invalid(kind + '欄位');
}
export function integerValue(value, minimum = 0n, maximum = MAX_INTEGER) {
  if (typeof value !== 'bigint' && (typeof value !== 'number' || !Number.isSafeInteger(value))) invalid('整數');
  const exact = BigInt(value);
  if (exact < minimum || exact > maximum) invalid('整數');
  return exact;
}
export const storedInteger = value => value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER) ? Number(value) : value;
function text(value, empty = false) {
  // Python str.strip whitespace differs from JavaScript trim (notably NEL and BOM).
  const whitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u;
  if (typeof value !== 'string' || (!empty && whitespace.test(value)) || value.includes('\0')) invalid('文字');
  let length = 0;
  for (const char of value) {
    const point = char.codePointAt(0);
    if (++length > 4096 || (point >= 0xd800 && point <= 0xdfff)) invalid('文字編碼／長度');
  }
}
function iso(value, month = false) {
  if (month && (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}$/.test(value))) invalid('月份');
  try { isoDate(month ? value + '-01' : value); } catch { invalid(month ? '月份' : '日期'); }
}
function amount(value, zero = false) {
  if (typeof value !== 'string' || value.length > 19 || !/^(0|[1-9][0-9]*)$/.test(value)) invalid('cents');
  integerValue(BigInt(value), zero ? 0n : 1n);
}
function unique(values, kind) { if (new Set(values).size !== values.length) invalid(kind + '重複'); }
function expense(row, payments, rules) {
  shape(row, EXPENSE_FIELDS, '消費'); iso(row.spent_on); amount(row.cents);
  text(row.category); text(row.payment_source_name); text(row.note, true);
  integerValue(row.voided, 0n, 1n); integerValue(row.revision);
  if (row.kind !== 'consumption' || !['manual', '固定', '訂閱', '分期'].includes(row.source)) invalid('消費種類／來源');
  if (row.payment_source_id !== null && (typeof row.payment_source_id !== 'string' || !payments.has(row.payment_source_id))) invalid('付款引用');
  if (row.recurring_id === null) {
    if (row.period !== null) invalid('規則月份引用');
    return; // A legacy automatic source without a rule link is valid in Python v1.
  }
  const rule = rules.get(row.recurring_id);
  if (typeof row.recurring_id !== 'string' || !rule || row.source !== rule.kind) invalid('規則引用');
  iso(row.period, true);
  if (row.spent_on.slice(0, 7) !== row.period || row.period < rule.start_month) invalid('已入帳月份');
  const periods = integerValue(rule.periods);
  const offset = (Number(row.period.slice(0, 4)) - Number(rule.start_month.slice(0, 4))) * 12 + Number(row.period.slice(5)) - Number(rule.start_month.slice(5));
  if (periods && BigInt(offset) >= periods) invalid('分期月份');
}

export function emptyData() {
  return { expenses: [], categories: [], payment_sources: [], budgets: [], recurring_rules: [], recurring_versions: [], shortcuts: [], actions: [], settings: { reminder_levels: null, recording_started_on: null } };
}
export function validateBackup(bundle) {
  shape(bundle, ['format', 'version', 'data'], '格式');
  if (bundle.format !== 'life-ledger-backup') invalid('格式／版本');
  integerValue(bundle.version, 1n, 1n);
  const data = bundle.data; shape(data, SECTIONS, '生活帳本');
  let count = 0;
  const ids = [];
  for (const [section, fields] of Object.entries(FIELDS)) {
    const rows = data[section];
    if (!Array.isArray(rows)) invalid(section + '結構');
    count += rows.length; if (count > 100000) invalid('資料筆數上限');
    for (const row of rows) {
      shape(row, fields, section);
      if (Object.hasOwn(row, 'id')) {
        if (typeof row.id !== 'string' || !/^[a-z][a-z0-9_-]{0,63}$/.test(row.id)) invalid('識別');
        ids.push(row.id);
      }
    }
  }
  unique(ids, '識別');
  for (const section of ['categories', 'payment_sources']) {
    for (const row of data[section]) { text(row.name); integerValue(row.active, 0n, 1n); }
    unique(data[section].map(row => row.name), section);
  }
  if (data.categories.some(row => row.name === '總額')) invalid('分類');
  if (data.payment_sources.some(row => ['現金', '未指定'].includes(row.name) && integerValue(row.active) === 0n)) invalid('保留付款方式');
  const payments = new Set(data.payment_sources.map(row => row.id));
  const rules = new Map(data.recurring_rules.map(row => [row.id, row]));
  for (const row of rules.values()) {
    text(row.name); text(row.category); amount(row.cents); iso(row.start_month, true);
    const periods = integerValue(row.periods);
    integerValue(row.active, 0n, 1n); integerValue(row.revision);
    if (!['固定', '訂閱', '分期'].includes(row.kind) || (row.kind === '分期') !== (periods !== 0n)) invalid('規則種類／期數');
    if (row.due_day !== null) integerValue(row.due_day, 1n, 31n);
  }
  for (const row of data.recurring_versions) {
    if (typeof row.recurring_id !== 'string' || rules.get(row.recurring_id)?.kind !== '固定') invalid('版本規則引用');
    iso(row.effective_month, true);
    if (row.effective_month < rules.get(row.recurring_id).start_month) invalid('生效月份');
    text(row.name); text(row.category); amount(row.cents); integerValue(row.due_day, 1n, 31n);
  }
  unique(data.recurring_versions.map(row => row.recurring_id + '\0' + row.effective_month), '生效版本');
  for (const row of data.budgets) { iso(row.month, true); text(row.category); amount(row.cents, true); }
  unique(data.budgets.map(row => row.month + '\0' + row.category), '預算');
  for (const row of data.expenses) expense(row, payments, rules);
  unique(data.expenses.filter(row => row.recurring_id !== null).map(row => row.recurring_id + '\0' + row.period), '規則月份');
  for (const row of data.shortcuts) {
    text(row.name); text(row.category); text(row.note, true);
    if (typeof row.payment_source_id !== 'string' || !payments.has(row.payment_source_id)) invalid('捷徑付款引用');
    if (row.cents !== null) amount(row.cents);
    integerValue(row.position); integerValue(row.active, 0n, 1n);
  }
  const expenses = new Map(data.expenses.map(row => [row.id, row]));
  for (const row of data.actions) {
    if (typeof row.expense_id !== 'string' || !expenses.has(row.expense_id)) invalid('操作消費引用');
    integerValue(row.undone, 0n, 1n);
    if (row.before !== null) {
      expense(row.before, payments, rules);
      if (row.before.id !== row.expense_id) invalid('操作快照引用');
      const current = expenses.get(row.expense_id);
      if (['source', 'recurring_id', 'period', 'kind'].some(field => row.before[field] !== current[field])) invalid('操作快照不可變欄位');
    }
  }
  shape(data.settings, ['reminder_levels', 'recording_started_on'], '設定');
  const levels = data.settings.reminder_levels;
  if (levels !== null) {
    if (!Array.isArray(levels) || levels.length > 10) invalid('提醒門檻');
    const exact = levels.map(level => integerValue(level, 1n, 1000n)); unique(exact, '提醒門檻');
  }
  if (data.settings.recording_started_on !== null) iso(data.settings.recording_started_on);
  return bundle;
}
export function validateStorageLimits(bundle) {
  writeBackup(bundle);
}
export function readBackup(payload) {
  if (!(payload instanceof Uint8Array) || !payload.byteLength || payload.byteLength > MAX_BYTES) invalid('輸入大小／型別');
  let decoded;
  try { decoded = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(payload); } catch { invalid('UTF-8'); }
  if (decoded.charCodeAt(0) === 0xfeff) invalid('JSON');
  let bundle;
  try {
    // Bound depth and keys before parsing: the vendor ignores equal duplicate keys
    // and its object assignment swallows __proto__. JSON syntax stays vendor-owned.
    const frames = [];
    let quoted = false, escaped = false, start = 0;
    for (let index = 0; index < decoded.length; index++) {
      const char = decoded[index];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') {
          quoted = false;
          let next = index + 1;
          while (/[ \t\r\n]/.test(decoded[next] ?? '')) next++;
          if (decoded[next] === ':') {
            const key = parse(decoded.slice(start, index + 1)); // Decode only this string with the official parser.
            const keys = frames.at(-1);
            if (key === '__proto__') invalid('未知欄位');
            if (keys?.has(key)) invalid('JSON重複欄位');
            keys?.add(key);
          }
        }
      } else if (char === '"') { quoted = true; start = index; }
      else if (char === '[' || char === '{') {
        frames.push(char === '{' ? new Set() : null);
        if (frames.length > 12) invalid('結構深度');
      } else if (char === ']' || char === '}') frames.pop();
    }
    bundle = parse(decoded, undefined, {
      parseNumber: token => {
        if (!/^-?(0|[1-9][0-9]*)$/.test(token) || token.length > 20) invalid('非整數／非標準數值');
        const exact = BigInt(token);
        if (exact < -MAX_INTEGER || exact > MAX_INTEGER) invalid('整數');
        return storedInteger(exact);
      },
      onDuplicateKey: () => invalid('JSON重複欄位'),
    });
  } catch (error) { if (error instanceof BackupError) throw error; invalid('JSON'); }
  return validateBackup(bundle);
}
export function writeBackup(bundle) {
  validateBackup(bundle);
  const payload = new TextEncoder().encode(stringify(bundle)); // Official serializer emits BigInt integer tokens.
  if (payload.byteLength > MAX_BYTES) invalid('輸入大小上限');
  return payload;
}
