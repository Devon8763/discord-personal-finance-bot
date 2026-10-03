import * as distribution from './vendor/lossless-json-4.3.1/lossless-json.js';
import { validateInput, categoryOptions, categoryName, paymentName, budgetCents, cents, isoDate, fixedNextMonth, fixedDueDate, fixedSettings, validateFixed, recurringProgress, ValidationError } from './rules.mjs';
import { emptyData, readBackup, writeBackup, integerValue, storedInteger, MAX_INTEGER, MAX_BYTES } from './backup.mjs';
import { STORES, openLedgerDatabase, readSnapshot, scanTransaction, addState, portable, metadata, rowKey } from './idb.mjs';

export const DB_NAME = 'discordbot-localfirst-synthetic-v1';
const { stringify } = distribution.default ?? globalThis.LosslessJSON;
const encoder = new TextEncoder();
export class LedgerError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export const openDatabase = (name = DB_NAME) => openLedgerDatabase(name);
export const readLedger = db => readSnapshot(db);
export const readLedgerIfPresent = db => readSnapshot(db, true);
export function taiwanToday() {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone:'Asia/Taipei', year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}
const identifier = prefix => prefix + crypto.randomUUID();
const length = row => encoder.encode(stringify(row)).byteLength;
const storageFailure = () => new LedgerError('storage', '儲存未完成，資料沒有變更。請確認儲存空間後重試。');

function transaction(db, operation) {
  return new Promise((resolve, reject) => {
    let failure, result;
    let tx;
    try { tx = db.transaction(STORES, 'readwrite'); } catch { reject(storageFailure()); return; }
    tx.oncomplete = () => resolve(result);
    tx.onabort = () => reject(failure || storageFailure());
    scanTransaction(tx, (state, meta) => { result = operation(tx, state, meta); }, error => {
      failure = error instanceof LedgerError || error instanceof ValidationError ? error : storageFailure();
    });
  });
}

export function initializeLedger(db) {
  return transaction(db, (tx, state) => {
    if (state !== null) return state;
    const initial = { format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...emptyData(),
      payment_sources:[{ id:'p_cash', name:'現金', active:1 }, { id:'p_unspecified', name:'未指定', active:1 }] };
    addState(tx, initial);
    return initial;
  });
}

export function restorePortableBackup(db, payload, confirmedMain = false) {
  if (db.name !== DB_NAME + '-portable-checks' && !(db.name === DB_NAME && confirmedMain === true)) {
    throw new LedgerError('storage', '還原僅適用獨立的互通測試帳本或已確認的主驗證頁。');
  }
  const parsed = readBackup(payload);
  return transaction(db, (tx, state) => {
    if (state !== null) throw new LedgerError('nonempty', '目標測試帳本已有資料或建立標記，無法還原。');
    const restored = { format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...parsed.data };
    addState(tx, restored);
    return restored;
  });
}
export const exportPortableBackup = async db => writeBackup(portable(await readSnapshot(db)));

function current(state, id, expectedRevision) {
  const position = state.expenses.findIndex(row => row.id === id && row.kind === 'consumption' &&
    ['manual', '固定', '訂閱', '分期'].includes(row.source) && row.voided === 0);
  if (position < 0) throw new LedgerError('unavailable', '此筆消費無法操作，請重新載入。');
  const entry = state.expenses[position];
  let expected;
  try { expected = integerValue(expectedRevision); }
  catch { throw new LedgerError('conflict', '資料已變更，請重新載入後再操作。'); }
  if (integerValue(entry.revision) !== expected) throw new LedgerError('conflict', '資料已變更，請重新載入後再操作。');
  if (expected === MAX_INTEGER) throw new LedgerError('limit', '版本已達 signed 64-bit 上限，請停止操作。');
  return { entry, position };
}

function save(tx, state, meta, entry, before, position) {
  const action = { id:identifier('a_'), expense_id:entry.id, before:before ?? null, undone:0 };
  const expenseAdded = before === undefined;
  const delta = length(entry) - (expenseAdded ? 0 : length(before)) +
    (expenseAdded && state.expenses.length ? 1 : 0) + length(action) + (state.actions.length ? 1 : 0);
  if (meta.row_count + 1 + Number(expenseAdded) > 100000 || meta.backup_bytes + delta > MAX_BYTES) {
    throw new LedgerError('limit', '本機帳本已達完整備份容量上限，資料沒有變更。');
  }
  tx.objectStore('expenses').put({ key:entry.id, position, value:entry });
  tx.objectStore('actions').add({ key:action.id, position:meta.next_action_order, value:action });
  tx.objectStore('meta').put({ key:'next_action_order', value:meta.next_action_order + 1 });
  tx.objectStore('meta').put({ key:'row_count', value:meta.row_count + 1 + Number(expenseAdded) });
  tx.objectStore('meta').put({ key:'backup_bytes', value:meta.backup_bytes + delta });
  if (expenseAdded) state.expenses.push(entry);
  else state.expenses[position] = entry;
  state.actions.push(action);
}

function write(db, change) {
  return transaction(db, (tx, state, meta) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    change(tx, state, meta);
    return state;
  });
}
export function addExpense(db, form, today) {
  return write(db, (tx, state, meta) => {
    const entry = { id:identifier('e_'), ...validateInput(form, state, today), source:'manual', recurring_id:null,
      period:null, voided:0, kind:'consumption', revision:0 };
    save(tx, state, meta, entry, undefined, state.expenses.length);
  });
}
export function editExpense(db, id, expectedRevision, form, today) {
  return write(db, (tx, state, meta) => {
    const {entry, position} = current(state, id, expectedRevision);
    const updated = { ...entry, ...validateInput(form, state, today, entry),
      revision:storedInteger(integerValue(entry.revision) + 1n) };
    save(tx, state, meta, updated, structuredClone(entry), position);
  });
}
export function voidExpense(db, id, expectedRevision) {
  return write(db, (tx, state, meta) => {
    const {entry, position} = current(state, id, expectedRevision);
    save(tx, state, meta, { ...entry, voided:1, revision:storedInteger(integerValue(entry.revision) + 1n) },
      structuredClone(entry), position);
  });
}

function incrementRevision(row) {
  const revision = integerValue(row.revision);
  if (revision === MAX_INTEGER) throw new LedgerError('limit', '版本已達 signed 64-bit 上限，請停止操作。');
  row.revision = storedInteger(revision + 1n);
}

export function changeCategory(state, operation, oldName, value) {
  metadata(state); // Reject corrupt or over-limit history before preparing any changes.
  const changed = structuredClone(state);
  const options = categoryOptions(state);
  if (operation === 'add') {
    const name = categoryName(value);
    if (options.some(row => row.name === name)) throw new ValidationError('已有同名分類（含停用項目），請使用其他名稱。');
    changed.categories.push({ name, active:1 });
  } else {
    if (!options.some(row => row.name === oldName && row.active === 1)) throw new ValidationError('找不到啟用中的分類，請重新載入。');
    const position = changed.categories.findIndex(row => row.name === oldName);
    if (operation === 'disable') {
      if (position < 0) changed.categories.push({ name:oldName, active:0 });
      else changed.categories[position].active = 0;
    } else if (operation === 'rename') {
      const name = categoryName(value);
      const references = ['expenses', 'budgets', 'recurring_rules', 'recurring_versions', 'shortcuts'];
      if (options.some(row => row.name === name) || references.some(section => state[section].some(row => row.category === name)) ||
          state.actions.some(row => row.before?.category === name)) {
        throw new ValidationError('已有同名分類或歷史，不能合併分類。');
      }
      // Missing overrides identify builtin defaults; explicit builtin overrides still use the same rule.
      if (categoryOptions({categories:[]}).some(row => row.name === oldName)) {
        if (position < 0) changed.categories.push({ name:oldName, active:0 });
        else changed.categories[position].active = 0;
        changed.categories.push({ name, active:1 });
      } else changed.categories[position].name = name;
      const versionRules = new Set(state.recurring_versions.filter(row => row.category === oldName).map(row => row.recurring_id));
      for (const row of changed.recurring_rules) {
        if (row.category === oldName || versionRules.has(row.id)) incrementRevision(row);
      }
      for (const section of references) for (const row of changed[section]) {
        if (row.category !== oldName) continue;
        row.category = name;
        if (section === 'expenses') incrementRevision(row);
      }
      for (const row of changed.actions) if (row.before?.category === oldName) row.before.category = name;
    } else throw new ValidationError('請選擇有效的分類操作。');
  }
  metadata(changed); // Includes complete backup validation, byte and row limits.
  return changed;
}

function saveSettings(tx, state, changed) {
  const meta = metadata(changed);
  for (const section of STORES.filter(section => !['meta', 'settings'].includes(section))) {
    changed[section].forEach((row, position) => {
      const previous = state[section][position];
      if (previous && stringify(previous) === stringify(row)) return;
      const store = tx.objectStore(section);
      if (previous && stringify(rowKey(section, previous)) !== stringify(rowKey(section, row))) store.delete(rowKey(section, previous));
      store.put({ key:rowKey(section, row), position, value:row });
    });
  }
  for (const key of ['row_count', 'backup_bytes']) tx.objectStore('meta').put({ key, value:meta[key] });
}

export function updateCategory(db, expectedCategories, operation, oldName, value) {
  return transaction(db, (tx, state) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    if (stringify(state.categories) !== stringify(expectedCategories)) throw new LedgerError('conflict', '設定已變更，請重新載入後再操作。');
    const changed = changeCategory(state, operation, oldName, value);
    saveSettings(tx, state, changed);
    return changed;
  });
}

export function changePayment(state, operation, id, value) {
  metadata(state);
  const changed = structuredClone(state);
  const row = changed.payment_sources.find(item => item.id === id);
  if (operation !== 'add') {
    if (!row || (operation === 'disable' && row.active !== 1)) throw new ValidationError('找不到付款方式，請重新載入。');
    if (['現金', '未指定'].includes(row.name)) throw new ValidationError('「現金」與「未指定」不能更改或停用。');
  }
  if (operation === 'add' || operation === 'rename') {
    const name = paymentName(value);
    if (changed.payment_sources.some(item => item.name === name && (operation === 'add' || item.id !== id))) {
      throw new ValidationError('已有同名付款方式（含停用項目），請使用其他名稱。');
    }
    if (operation === 'add') changed.payment_sources.push({ id:identifier('p_'), name, active:1 });
    else row.name = name;
  } else if (operation === 'disable') row.active = 0;
  else throw new ValidationError('請選擇有效的付款方式操作。');
  metadata(changed);
  return changed;
}

export function updatePayment(db, expectedPayments, operation, id, value) {
  return transaction(db, (tx, state) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    if (stringify(state.payment_sources) !== stringify(expectedPayments)) throw new LedgerError('conflict', '設定已變更，請重新載入後再操作。');
    const changed = changePayment(state, operation, id, value);
    saveSettings(tx, state, changed);
    return changed;
  });
}

export function changeBudget(state, operation, category, value, today) {
  metadata(state);
  const month = isoDate(today).slice(0, 7);
  const changed = structuredClone(state);
  const position = changed.budgets.findIndex(row => row.month === month && row.category === category);
  const previous = changed.budgets[position];
  if (operation === 'clear') {
    if (!previous) throw new ValidationError('找不到本月預算，請重新載入。');
    changed.budgets.splice(position, 1);
  } else {
    if (previous && cents(previous.cents) % 100n) throw new ValidationError('歷史小數預算只可清除；原值仍保留。');
    let amount;
    if (operation === 'sum' && category === '總額') {
      const rows = changed.budgets.filter(row => row.month === month && row.category !== '總額');
      const sum = rows.reduce((total, row) => total + cents(row.cents), 0n);
      if (!rows.length || sum <= 0n) throw new ValidationError('目前沒有正數分類預算可合計。');
      if (sum % 100n) throw new ValidationError('分類預算加總含歷史小數，請先清除小數預算。');
      amount = budgetCents((sum / 100n).toString());
    } else if (operation === 'set') {
      if (category !== '總額' && !categoryOptions(state).some(row => row.name === category && row.active === 1)) {
        throw new ValidationError('停用或不存在的分類不能設定預算，既有預算只可清除。');
      }
      amount = budgetCents(value);
    } else throw new ValidationError('請選擇有效的預算操作。');
    const row = {month, category, cents:amount};
    if (position < 0) changed.budgets.push(row);
    else changed.budgets[position] = row;
  }
  const rows = changed.budgets.filter(row => row.month === month);
  const total = rows.find(row => row.category === '總額');
  const sum = rows.filter(row => row.category !== '總額').reduce((sum, row) => sum + cents(row.cents), 0n);
  if (total && cents(total.cents) < sum) throw new ValidationError('分類預算合計不可超過總預算。');
  metadata(changed);
  return changed;
}

export function updateBudget(db, expectedBudgets, operation, category, value) {
  return transaction(db, (tx, state) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    // Read the Taiwan clock inside the write transaction; callers cannot choose a month.
    const today = taiwanToday(), month = today.slice(0, 7);
    const selected = rows => rows.find(row => row.month === month && row.category === category) || null;
    if (!Array.isArray(expectedBudgets) || stringify(selected(state.budgets)) !== stringify(selected(expectedBudgets))) {
      throw new LedgerError('conflict', '預算已變更，請重新載入後再操作。');
    }
    const changed = changeBudget(state, operation, category, value, today);
    const meta = metadata(changed), store = tx.objectStore('budgets');
    if (operation === 'clear') store.delete([month, category]);
    changed.budgets.forEach((row, position) => {
      if (stringify(state.budgets[position]) !== stringify(row)) store.put({key:rowKey('budgets', row), position, value:row});
    });
    for (const key of ['row_count', 'backup_bytes']) tx.objectStore('meta').put({key, value:meta[key]});
    return changed;
  });
}

export function changeRecurring(state, operation, id, expectedRevision, form, today, fixedOnly = false) {
  const meta = metadata(state);
  const month = isoDate(today).slice(0, 7), changed = structuredClone(state);
  const rule = changed.recurring_rules.find(row => row.id === id && (!fixedOnly || row.kind === '固定') && row.active === 1);
  if (operation !== 'add' && operation !== 'sync') {
    if (!rule || recurringProgress(changed, rule).complete) {
      throw new LedgerError('unavailable', '此定期規則無法操作，請重新載入。');
    }
    let expected;
    try { expected = integerValue(expectedRevision); } catch { /* Fixed conflict message below. */ }
    if (expected === undefined || expected < 0n || integerValue(rule.revision) !== expected) {
      throw new LedgerError('conflict', '定期規則已變更，請重新載入後再操作。');
    }
    incrementRevision(rule);
  }
  if (operation === 'add' || operation === 'update') {
    const kind = operation === 'add' ? form?.kind ?? '固定' : rule.kind;
    const periods = operation === 'add' ? (kind === '分期' ? form?.periods : form?.periods ?? 0) : rule.periods;
    if (!['固定', '訂閱', '分期'].includes(kind) || (fixedOnly && kind !== '固定') ||
        (operation === 'add' && (!Number.isInteger(periods) || (kind === '分期' ? periods < 1 || periods > 600 : periods !== 0))) ||
        (operation === 'update' && kind === '固定' && form?.periods !== undefined && form.periods !== 0)) {
      throw new ValidationError('請選擇有效的支出類型；分期總期數須為 1～600。');
    }
    const effective = operation === 'add' ? form?.start_month : fixedNextMonth(month);
    if (operation === 'add' && ![month, fixedNextMonth(month)].includes(effective)) {
      throw new ValidationError('開始月份只能選台灣本月或下月。');
    }
    if (operation === 'update' && form?.start_month !== undefined && form.start_month !== rule.start_month) {
      throw new ValidationError('建立後不能更改開始月份。');
    }
    const previous = operation === 'update' ? fixedSettings(changed, rule, effective) : null;
    const fields = validateFixed(form, changed, today, previous, operation === 'update' && kind !== '固定' ? rule : null);
    const key = operation === 'add' ? identifier('r_') : rule.id;
    if (operation === 'add') changed.recurring_rules.push({id:key, ...fields, kind, start_month:effective, periods, active:1, revision:0});
    if (kind === '固定' || operation === 'update') {
      const version = changed.recurring_versions.find(row => row.recurring_id === key && row.effective_month === effective);
      if (version) Object.assign(version, fields);
      else changed.recurring_versions.push({id:identifier('v_'), recurring_id:key, effective_month:effective, ...fields});
    }
  } else if (operation === 'sync' || operation === 'stop') {
    const posted = new Set(changed.expenses.filter(row => row.recurring_id !== null).map(row => row.recurring_id + ':' + row.period));
    const versions = new Map();
    for (const row of changed.recurring_versions) {
      if (!versions.has(row.recurring_id)) versions.set(row.recurring_id, []);
      versions.get(row.recurring_id).push(row);
    }
    let count = meta.row_count;
    const selected = operation === 'stop' ? [rule] : changed.recurring_rules.filter(row => (!fixedOnly || row.kind === '固定') && row.active === 1);
    for (const selectedRule of selected) {
      const history = (versions.get(selectedRule.id) || []).sort((a, b) => a.effective_month.localeCompare(b.effective_month));
      let settings = selectedRule, index = 0, installment = 1n;
      const total = integerValue(selectedRule.periods);
      for (let period = selectedRule.start_month; period <= month && (total === 0n || installment <= total); installment++) {
        while (index < history.length && history[index].effective_month <= period) settings = history[index++];
        const due = fixedDueDate(period, settings.due_day), key = selectedRule.id + ':' + period;
        if (due <= today && !posted.has(key)) {
          if (count + 2 > 100000) throw new LedgerError('limit', '本機帳本已達完整備份容量上限，資料沒有變更。');
          const note = settings.name + (selectedRule.kind === '分期' ? `（第 ${installment}/${total} 期）` : '');
          if ([...note].length > 4096) throw new LedgerError('limit', '分期項目加上期數後超過備份文字上限，資料沒有變更。請保留原備份。');
          const entry = {id:identifier('e_'), spent_on:due, cents:settings.cents, category:settings.category,
            note, source:selectedRule.kind, recurring_id:selectedRule.id, period, voided:0,
            payment_source_id:null, payment_source_name:'未指定', kind:'consumption', revision:0};
          changed.expenses.push(entry);
          changed.actions.push({id:identifier('a_'), expense_id:entry.id, before:null, undone:0});
          posted.add(key); count += 2;
        }
        if (period === month) break;
        period = fixedNextMonth(period);
      }
    }
    if (operation === 'stop') rule.active = 0;
  } else throw new ValidationError('請選擇有效的固定支出操作。');
  metadata(changed);
  return changed;
}

export function changeFixed(state, operation, id, expectedRevision, form, today) {
  return changeRecurring(state, operation, id, expectedRevision, form, today, true);
}

export function updateRecurring(db, operation, id = null, expectedRevision = null, form = null, fixedOnly = false) {
  return transaction(db, (tx, state) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    const changed = changeRecurring(state, operation, id, expectedRevision, form, taiwanToday(), fixedOnly);
    saveSettings(tx, state, changed);
    if (changed.actions.length !== state.actions.length) {
      tx.objectStore('meta').put({key:'next_action_order', value:changed.actions.length});
    }
    return changed;
  });
}

export function updateFixed(db, operation, id = null, expectedRevision = null, form = null) {
  return updateRecurring(db, operation, id, expectedRevision, form, true);
}
