import { openDatabase, initializeLedger, readLedger, editExpense, voidExpense, DB_NAME, restorePortableBackup, exportPortableBackup } from '../local-first/ledger.mjs';
import { readBackup, writeBackup, integerValue, MAX_INTEGER } from '../local-first/backup.mjs';
const lines = [];
const name = DB_NAME + '-portable-checks';
const text = payload => new TextDecoder().decode(payload);
function ok(value, message) { if (!value) throw new Error(message); }
async function rejects(fn, code) { let error; try { await fn(); } catch (caught) { error = caught; } ok(error && (!code || error.code === code), 'Expected rejection: ' + code); }
function done(tx) { return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); }
function request(q) { return new Promise((resolve, reject) => { q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error); }); }
// Clear only this separately named synthetic test DB, never the main validation ledger.
async function reset(db) { const tx = db.transaction('ledger', 'readwrite'); tx.objectStore('ledger').clear(); await done(tx); }
async function count(db) { const tx = db.transaction('ledger', 'readonly'); const completion = done(tx); const n = await request(tx.objectStore('ledger').count()); await completion; return n; }
function fault(db, method) {
  return { name: db.name, transaction(...args) {
    const tx = db.transaction(...args), original = tx.objectStore.bind(tx);
    tx.objectStore = (...names) => {
      const store = original(...names), call = store[method].bind(store);
      store[method] = (...values) => { const q = call(...values); q.addEventListener('success', () => tx.abort()); return q; };
      return store;
    };
    return tx;
  } };
}
let db;
try {
  db = await openDatabase(name);
  const payload = new Uint8Array(await (await fetch('fixtures/portable_life_ledger.json')).arrayBuffer());
  const original = readBackup(payload), exact = text(writeBackup(original));
  if (!new URL(location.href).searchParams.has('resume')) {
    await rejects(() => restorePortableBackup({ name: DB_NAME }, payload), 'storage');
    await reset(db);
    await rejects(() => exportPortableBackup(db), 'uninitialized');
    ok(await count(db) === 0, 'Read/export must not initialize');
    lines.push('未建立帳本的匯出不產生資料');
    await restorePortableBackup(db, payload);
    let state = await readLedger(db);
    ok(text(await exportPortableBackup(db)) === exact, 'All nine sections, snapshots and order');
    ok(typeof state.expenses.find(row => integerValue(row.revision) === 9007199254740993n).revision === 'bigint', 'Native BigInt in IndexedDB');
    ok(state.recurring_rules.some(row => row.periods === 9007199254740997n), 'Large periods');
    ok(state.shortcuts[0].position === 9007199254740995n, 'Large position');
    ok(state.expenses.some(row => row.spent_on === '2029-01-01') && state.budgets.some(row => row.cents === '0'), 'Historical/future values retained');
    lines.push('九類資料、全部引用／快照／順序與超安全整數精確保留');
    await rejects(() => restorePortableBackup(db, payload), 'nonempty');
    ok(text(await exportPortableBackup(db)) === exact, 'Nonempty unchanged');
    lines.push('非空目標拒絕且完全不變');
    db.close(); db = await openDatabase(name);
    ok(text(await exportPortableBackup(db)) === exact, 'Connection reopened');
    lines.push('關閉並重開原生連線仍保留完整資料');
    const modes = [];
    const observed = { transaction(store, mode) { modes.push(mode); return db.transaction(store, mode); } };
    await readLedger(observed); await exportPortableBackup(observed);
    ok(modes.every(mode => mode === 'readonly'), 'Readonly snapshots');
    ok(text(await exportPortableBackup(db)) === exact, 'No read side effects');
    lines.push('一般讀取與匯出只使用 readonly 快照');
    const entry = state.expenses.find(row => row.revision === 9007199254740993n);
    const form = { amount: '12.34', note: '合成更改', spent_on: '2024-12-31', category: entry.category, payment_source_id: '' };
    state = await editExpense(db, entry.id, entry.revision, form, '2025-02-28');
    ok(state.expenses.find(row => row.id === entry.id).revision === 9007199254740994n, 'Exact revision increment');
    ok(state.actions.at(-1).before.revision === entry.revision, 'Exact before snapshot');
    await rejects(() => voidExpense(db, entry.id, entry.revision), 'conflict');
    await rejects(() => editExpense(fault(db, 'put'), entry.id, 9007199254740994n, form, '2025-02-28'));
    const large = state.expenses.find(row => row.revision === MAX_INTEGER);
    await rejects(() => voidExpense(db, large.id, large.revision), 'limit');
    ok((await readLedger(db)).actions.length === state.actions.length, 'Abort/limit no actions');
    await rejects(() => voidExpense(db, state.expenses.find(row => row.source === '訂閱').id, 0), 'unavailable');
    lines.push('大 revision 更改、快照、舊 revision 拒絕、遞增上限及中途 rollback');
    await reset(db);
    await rejects(() => restorePortableBackup(fault(db, 'add'), payload));
    ok(await count(db) === 0, 'Abort rolls back both ledger and marker');
    await rejects(() => readLedger(db), 'uninitialized');
    lines.push('還原 request success 後 abort，帳本與標記皆未留下');
    const competing = await openDatabase(name);
    const results = await Promise.allSettled([restorePortableBackup(db, payload), restorePortableBackup(competing, payload)]);
    ok(results.filter(row => row.status === 'fulfilled').length === 1 && results.filter(row => row.status === 'rejected' && row.reason.code === 'nonempty').length === 1, 'Atomic empty target race');
    competing.close();
    ok(text(await exportPortableBackup(db)) === exact, 'Race winner complete');
    lines.push('兩原生連線競爭空白目標，只有一個成功');
    for (const [key, value] of [['created', 1], ['local-test-owner', undefined], ['other', undefined]]) {
      await reset(db); const tx = db.transaction('ledger', 'readwrite'); tx.objectStore('ledger').add(value, key); await done(tx);
      await rejects(() => restorePortableBackup(db, payload), 'nonempty');
      ok(await count(db) === 1, 'Unexpected/marker keys untouched');
    }
    lines.push('只有建立標記、未定義值或其他鍵也拒絕還原');
    await reset(db); await initializeLedger(db);
    await rejects(() => restorePortableBackup(db, payload), 'nonempty');
    ok((await readLedger(db)).payment_sources.length === 2, 'Defaults are nonempty');
    lines.push('已初始化且只有預設付款方式仍為非空');
    for (const levels of [null, []]) {
      await reset(db);
      const minimal = { format: 'life-ledger-backup', version: 1, data: Object.fromEntries(Object.keys(original.data).map(section => [section, section === 'settings' ? { reminder_levels: levels, recording_started_on: null } : []])) };
      await restorePortableBackup(db, writeBackup(minimal));
      ok(text(await exportPortableBackup(db)) === text(writeBackup(minimal)), 'Empty/settings-only roundtrip');
      ok((await readLedger(db)).payment_sources.length === 0, 'Import must not create defaults');
    }
    lines.push('空資料／只有設定保留 null 與空清單，還原不補付款預設');
    for (const [section, limit] of [['expenses', 200], ['actions', 1000]]) {
      await reset(db); const bundle = structuredClone(original);
      const row = bundle.data[section].find(row => section === 'actions' || row.source === 'manual');
      const next = () => ({ ...structuredClone(row), id: (section === 'actions' ? 'test_a_' : 'test_e_') + bundle.data[section].length });
      while (bundle.data[section].length < limit) bundle.data[section].push(next());
      await restorePortableBackup(db, writeBackup(bundle));
      ok(text(await exportPortableBackup(db)) === text(writeBackup(bundle)), 'Capacity boundary complete');
      await reset(db); bundle.data[section].push(next());
      await rejects(() => restorePortableBackup(db, writeBackup(bundle)), 'backup');
      ok(await count(db) === 0, 'No partial/truncated import');
    }
    lines.push('200／1000 筆邊界完整還原，超限拒絕整份，不截斷');
    await reset(db);
    const legacy = { format: 'local-first-test-ledger', version: 1, owner: 'local-test-owner', expenses: [structuredClone(entry)],
      categories: structuredClone(original.data.categories), payment_sources: structuredClone(original.data.payment_sources), actions: [] };
    let tx = db.transaction('ledger', 'readwrite'); tx.objectStore('ledger').add(legacy, legacy.owner); tx.objectStore('ledger').add(1, 'created'); await done(tx);
    state = await readLedger(db); ok(state.version === 2 && state.budgets.length === 0 && state.settings.reminder_levels === null, 'Known v1 normalized');
    await exportPortableBackup(db);
    tx = db.transaction('ledger', 'readonly'); const complete = done(tx); const raw = await request(tx.objectStore('ledger').get(legacy.owner)); await complete;
    ok(raw.version === 1 && !Object.hasOwn(raw, 'settings'), 'Read/export do not upgrade stored state');
    state = await editExpense(db, entry.id, entry.revision, form, '2025-02-28');
    ok(state.version === 2 && state.actions.length === 1 && state.expenses.length === 1, 'Write retains legacy history');
    lines.push('明確舊 v1 測試帳本唯讀補齊；成功更改才保存 v2，不重建歷史');
    await reset(db);
    delete legacy.actions;
    tx = db.transaction('ledger', 'readwrite'); tx.objectStore('ledger').add(legacy, legacy.owner); tx.objectStore('ledger').add(1, 'created'); await done(tx);
    await rejects(() => readLedger(db), 'storage');
    lines.push('損毀舊帳本拒絕，不當成可相容缺省');
    await reset(db); await restorePortableBackup(db, payload);
  } else {
    lines.push('同來源關閉頁面後重新開啟：未重設、未重新還原');
  }
  ok(text(await exportPortableBackup(db)) === exact, 'Final full neutral equality');
  document.querySelector('#backup').textContent = text(await exportPortableBackup(db));
  document.querySelector('#result').textContent = 'PASS：' + lines.length + ' 組原生 IndexedDB 互通驗證';
} catch (error) { document.querySelector('#result').textContent = 'FAIL：' + error.message; throw error; }
finally { db?.close(); document.querySelector('#details').textContent = lines.join('\n'); }
