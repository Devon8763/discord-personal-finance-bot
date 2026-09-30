import { validateInput } from './rules.mjs';
import { emptyData, SECTIONS, readBackup, writeBackup, validateStorageLimits, integerValue, storedInteger, MAX_INTEGER } from './backup.mjs';

export const DB_NAME = 'discordbot-localfirst-synthetic-v1';
const OWNER = 'local-test-owner';
const MAX_EXPENSES = 200;
const MAX_ACTIONS = 1000;
export class LedgerError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

export function openDatabase(name = DB_NAME) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('ledger');
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => db.close();
      resolve(db);
    };
    request.onerror = () => reject(new LedgerError('storage', '無法開啟本機測試帳本。'));
    request.onblocked = () => reject(new LedgerError('storage', '請關閉其他驗證頁後重新開啟。'));
  });
}

// ponytail: copy one ledger, capped at 200 expenses / 1000 actions; use per-record stores
// and indexes for a production-sized ledger, retaining atomic expense/action transactions.
function transact(db, mode, operation) {
  return new Promise((resolve, reject) => {
    let result, failure;
    const transaction = db.transaction('ledger', mode);
    const store = transaction.objectStore('ledger');
    transaction.oncomplete = () => resolve(result);
    transaction.onabort = () => reject(failure || new LedgerError('storage', '儲存未完成，資料沒有變更。請確認儲存空間後重試。'));
    const request = store.get(OWNER);
    request.onsuccess = () => {
      const marker = store.get('created');
      marker.onsuccess = () => {
        const count = store.count();
        count.onsuccess = () => {
          try {
            result = operation(request.result, marker.result, store, count.result);
          } catch (error) {
            failure = error;
            transaction.abort();
          }
        };
      };
    };
  });
}

function bundle(state) {
  return { format: 'life-ledger-backup', version: 1, data: Object.fromEntries(SECTIONS.map(section => [section, state[section]])) };
}
function checked(state, marker, count) {
  if (count === 0) throw new LedgerError('uninitialized', '測試帳本尚未建立。');
  const legacy = state?.version === 1;
  const fields = ['format', 'version', 'owner', ...(legacy ? ['expenses', 'actions', 'categories', 'payment_sources'] : SECTIONS)];
  if (!state || count !== 2 || marker !== 1 || state.owner !== OWNER || ![1, 2].includes(state.version) || state.format !== 'local-first-test-ledger' ||
      Object.keys(state).length !== fields.length || fields.some(field => !Object.hasOwn(state, field))) {
    throw new LedgerError('storage', '本機測試帳本資料不完整，請保留資料並停止操作。');
  }
  // Only the known local v1 state gains empty sections. Reading never writes a migration.
  if (legacy) state = { ...emptyData(), ...state, version: 2 };
  try { writeBackup(bundle(state)); validateStorageLimits(bundle(state)); }
  catch { throw new LedgerError('storage', '本機測試帳本資料不完整，請保留資料並停止操作。'); }
  return state;
}

export function initializeLedger(db) {
  return transact(db, 'readwrite', (state, marker, store, count) => {
    if (count !== 0) return checked(state, marker, count);
    const initial = { format: 'local-first-test-ledger', version: 2, owner: OWNER, ...emptyData(),
      payment_sources: [{ id: 'p_cash', name: '現金', active: 1 }, { id: 'p_unspecified', name: '未指定', active: 1 }] };
    store.add(1, 'created');
    store.add(initial, OWNER);
    return initial;
  });
}
export const readLedger = db => transact(db, 'readonly', (state, marker, _store, count) => checked(state, marker, count));
export const readLedgerIfPresent = db => transact(db, 'readonly', (state, marker, _store, count) => count === 0 ? null : checked(state, marker, count));
export function restorePortableBackup(db, payload, confirmedMain = false) {
  // The main page must explicitly confirm; independent interoperability checks retain their own database.
  if (db.name !== DB_NAME + '-portable-checks' && !(db.name === DB_NAME && confirmedMain === true)) {
    throw new LedgerError('storage', '還原僅適用獨立的互通測試帳本或已確認的主驗證頁。');
  }
  const parsed = readBackup(payload);
  validateStorageLimits(parsed);
  return transact(db, 'readwrite', (_state, _marker, store, count) => {
    if (count !== 0) throw new LedgerError('nonempty', '目標測試帳本已有資料或建立標記，無法還原。');
    const state = { format: 'local-first-test-ledger', version: 2, owner: OWNER, ...parsed.data };
    store.add(state, OWNER);
    store.add(1, 'created');
    return state;
  });
}
export const exportPortableBackup = db => transact(db, 'readonly', (state, marker, _store, count) => writeBackup(bundle(checked(state, marker, count))));
const identifier = prefix => prefix + crypto.randomUUID();

function write(db, mutate) {
  return transact(db, 'readwrite', (state, marker, store, count) => {
    state = checked(state, marker, count);
    if (state.actions.length >= MAX_ACTIONS) throw new LedgerError('limit', '測試操作紀錄已達 1,000 筆上限。');
    mutate(state);
    writeBackup(bundle(state));
    store.put(state, OWNER);
    return state;
  });
}
function current(state, id, expectedRevision) {
  const entry = state.expenses.find(row => row.id === id && row.kind === 'consumption' && row.source === 'manual' && row.voided === 0);
  if (!entry) throw new LedgerError('unavailable', '此筆消費無法操作，請重新載入。');
  let expected;
  try { expected = integerValue(expectedRevision); } catch { throw new LedgerError('conflict', '資料已變更，請重新載入後再操作。'); }
  if (integerValue(entry.revision) !== expected) throw new LedgerError('conflict', '資料已變更，請重新載入後再操作。');
  if (expected === MAX_INTEGER) throw new LedgerError('limit', '版本已達 signed 64-bit 上限，請停止操作。');
  return entry;
}
function action(state, entry, before) {
  state.actions.push({ id: identifier('a_'), expense_id: entry.id, before, undone: 0 });
}
export function addExpense(db, form, today) {
  return write(db, state => {
    if (state.expenses.length >= MAX_EXPENSES) throw new LedgerError('limit', '測試帳本已達 200 筆上限（包含已刪除消費）。');
    const entry = { id: identifier('e_'), ...validateInput(form, state, today), source: 'manual', recurring_id: null,
      period: null, voided: 0, kind: 'consumption', revision: 0 };
    state.expenses.push(entry);
    action(state, entry, null);
  });
}
export function editExpense(db, id, expectedRevision, form, today) {
  return write(db, state => {
    const entry = current(state, id, expectedRevision);
    const before = structuredClone(entry);
    Object.assign(entry, validateInput(form, state, today, entry));
    entry.revision = storedInteger(integerValue(entry.revision) + 1n);
    action(state, entry, before);
  });
}
export function voidExpense(db, id, expectedRevision) {
  return write(db, state => {
    const entry = current(state, id, expectedRevision);
    const before = structuredClone(entry);
    entry.voided = 1;
    entry.revision = storedInteger(integerValue(entry.revision) + 1n);
    action(state, entry, before);
  });
}
