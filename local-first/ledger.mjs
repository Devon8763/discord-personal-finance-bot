import * as distribution from './vendor/lossless-json-4.3.1/lossless-json.js';
import { validateInput } from './rules.mjs';
import { emptyData, readBackup, writeBackup, integerValue, storedInteger, MAX_INTEGER, MAX_BYTES } from './backup.mjs';
import { STORES, openLedgerDatabase, readSnapshot, scanTransaction, addState, portable } from './idb.mjs';

export const DB_NAME = 'discordbot-localfirst-synthetic-v1';
const { stringify } = distribution.default ?? globalThis.LosslessJSON;
const encoder = new TextEncoder();
export class LedgerError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export const openDatabase = (name = DB_NAME) => openLedgerDatabase(name);
export const readLedger = db => readSnapshot(db);
export const readLedgerIfPresent = db => readSnapshot(db, true);
const identifier = prefix => prefix + crypto.randomUUID();
const length = row => encoder.encode(stringify(row)).byteLength;
const storageFailure = () => new LedgerError('storage', '儲存未完成，資料沒有變更。請確認儲存空間後重試。');

function transaction(db, operation) {
  return new Promise((resolve, reject) => {
    let failure;
    let tx;
    try { tx = db.transaction(STORES, 'readwrite'); } catch { reject(storageFailure()); return; }
    tx.oncomplete = () => readSnapshot(db).then(resolve, reject);
    tx.onabort = () => reject(failure || storageFailure());
    scanTransaction(tx, (state, meta) => operation(tx, state, meta), error => { failure = error; });
  });
}

export function initializeLedger(db) {
  return transaction(db, (tx, state) => {
    if (state !== null) return;
    const initial = { format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...emptyData(),
      payment_sources:[{ id:'p_cash', name:'現金', active:1 }, { id:'p_unspecified', name:'未指定', active:1 }] };
    addState(tx, initial);
  });
}

export function restorePortableBackup(db, payload, confirmedMain = false) {
  if (db.name !== DB_NAME + '-portable-checks' && !(db.name === DB_NAME && confirmedMain === true)) {
    throw new LedgerError('storage', '還原僅適用獨立的互通測試帳本或已確認的主驗證頁。');
  }
  const parsed = readBackup(payload);
  return transaction(db, (tx, state) => {
    if (state !== null) throw new LedgerError('nonempty', '目標測試帳本已有資料或建立標記，無法還原。');
    addState(tx, { format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...parsed.data });
  });
}
export const exportPortableBackup = async db => writeBackup(portable(await readSnapshot(db)));

function current(state, id, expectedRevision) {
  const position = state.expenses.findIndex(row => row.id === id && row.kind === 'consumption' &&
    row.source === 'manual' && row.voided === 0);
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
}

function write(db, change) {
  return transaction(db, (tx, state, meta) => {
    if (state === null) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
    change(tx, state, meta);
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
