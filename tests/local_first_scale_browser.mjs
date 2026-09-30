import { DB_NAME, openDatabase, addExpense, exportPortableBackup, restorePortableBackup, readLedger } from '../local-first/ledger.mjs';
import { readBackup, writeBackup, SECTIONS } from '../local-first/backup.mjs';
import * as distribution from '../local-first/vendor/lossless-json-4.3.1/lossless-json.js';

const result = document.querySelector('#result');
const details = document.querySelector('#details');
const sourceName = DB_NAME + '-storage-checks-scale';
const targetName = DB_NAME + '-portable-checks';
const timings = {};
const stamp = () => performance.now();
const ok = (value, message) => { if (!value) throw new Error(message); };
const request = q => new Promise((resolve, reject) => { q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error); });
const done = tx => new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); });
const { stringify } = distribution.default ?? globalThis.LosslessJSON;
const rowBytes = row => new TextEncoder().encode(stringify(row)).byteLength;
function noteWithBytes(size) {
  const whole = Math.floor(size / 3), remainder = size % 3;
  if (whole + remainder <= 4096) return '中'.repeat(whole) + 'x'.repeat(remainder);
  if (whole + 1 <= 4096 && remainder === 2) return '中'.repeat(whole) + 'é';
  throw new Error('Cannot form exact note bytes');
}
let source, target;
try {
  let start = stamp();
  const input = new Uint8Array(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer());
  const bundle = readBackup(input);
  const manual = bundle.data.expenses.find(row => row.source === 'manual');
  const originalAction = bundle.data.actions.find(row => row.before === null);
  while (bundle.data.expenses.length < 20000) {
    const i = bundle.data.expenses.length;
    bundle.data.expenses.push({ ...manual, id:'e_bulk_' + i, note:i === 100 ? '<img src=x onerror=alert(1)>合成' : '合成批次',
      cents:'1', payment_source_id:null, voided:i % 97 === 0 ? 1 : 0, revision:0 });
  }
  while (bundle.data.actions.length < 50000) {
    const i = bundle.data.actions.length;
    bundle.data.actions.push({ ...originalAction, id:'a_bulk_' + i, expense_id:bundle.data.expenses[i % 20000].id });
  }
  const payload = writeBackup(bundle);
  ok(payload.byteLength <= 64 * 1024 * 1024, 'Sample exceeds 64 MiB');
  timings.prepare_ms = Math.round(stamp() - start);
  result.textContent = '合成資料已建立；正在測試舊版升級……';

  const deletion = indexedDB.deleteDatabase(sourceName);
  await request(deletion);
  const old = await new Promise((resolve, reject) => {
    const opening = indexedDB.open(sourceName, 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore('ledger');
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error);
  });
  let tx = old.transaction('ledger','readwrite');
  tx.objectStore('ledger').add({ format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...bundle.data }, 'local-test-owner');
  tx.objectStore('ledger').add(1,'created');
  await done(tx); old.close();
  start = stamp();
  source = await openDatabase(sourceName);
  timings.migrate_ms = Math.round(stamp() - start);
  result.textContent = '升級完成；正在驗證讀取與寫入……';
  start = stamp();
  const state = await readLedger(source);
  ok(state.expenses.length === 20000 && state.actions.length === 50000, 'Incomplete list');
  timings.read_ms = Math.round(stamp() - start);
  start = stamp();
  const exported = await exportPortableBackup(source);
  ok(exported.byteLength === payload.byteLength && new TextDecoder().decode(exported) === new TextDecoder().decode(payload), 'Migration changed bytes');
  timings.export_ms = Math.round(stamp() - start);
  start = stamp();
  const changed = await addExpense(source, { amount:'1', note:'合成新增', spent_on:'2026-09-30', category:'餐飲', payment_source_id:'p1' }, '2026-09-30');
  ok(changed.expenses.length === 20001 && changed.actions.length === 50001, 'Large write lost rows');
  const after = await exportPortableBackup(source);
  ok(readBackup(after).data.actions.length === 50001, 'Large write cannot export');
  timings.write_ms = Math.round(stamp() - start);
  result.textContent = '寫入完成；正在驗證空白還原……';

  target = await openDatabase(targetName);
  tx = target.transaction([...SECTIONS,'meta'],'readwrite');
  for (const section of [...SECTIONS,'meta']) tx.objectStore(section).clear();
  await done(tx);
  start = stamp();
  await restorePortableBackup(target, after);
  const restored = await exportPortableBackup(target);
  ok(new TextDecoder().decode(restored) === new TextDecoder().decode(after), 'Large restore changed bytes');
  timings.restore_ms = Math.round(stamp() - start);
  target.close(); target = await openDatabase(targetName);
  ok(new TextDecoder().decode(await exportPortableBackup(target)) === new TextDecoder().decode(after), 'Reopen changed backup');
  result.textContent = '大量往返完成；正在驗證 64 MiB 邊界……';
  const boundary = readBackup(input);
  let boundaryBytes = writeBackup(boundary).byteLength;
  let nextId = 0;
  const row = (id, note) => ({ ...manual, id:'e_near_' + id, note, cents:'1', payment_source_id:null, revision:0 });
  const maximum = 64 * 1024 * 1024;
  while (true) {
    const full = row(nextId, '中'.repeat(4096));
    const extra = rowBytes(full) + 1;
    if (maximum - boundaryBytes <= 2 * extra) break;
    boundary.data.expenses.push(full);
    boundaryBytes += extra;
    nextId++;
  }
  const first = row(nextId++, ''), second = row(nextId++, '');
  const noteBytes = maximum - boundaryBytes - rowBytes(first) - rowBytes(second) - 2;
  ok(noteBytes >= 0 && noteBytes <= 24576, 'Boundary construction range');
  first.note = noteWithBytes(Math.min(12288, noteBytes));
  second.note = noteWithBytes(Math.max(0, noteBytes - 12288));
  boundary.data.expenses.push(first, second);
  const exact = writeBackup(boundary);
  ok(exact.byteLength === maximum, 'Exact 64 MiB backup not accepted');
  tx = target.transaction([...SECTIONS,'meta'],'readwrite');
  for (const section of [...SECTIONS,'meta']) tx.objectStore(section).clear();
  await done(tx);
  start = stamp();
  await restorePortableBackup(target, exact);
  ok((await exportPortableBackup(target)).byteLength === maximum, 'Exact maximum cannot export');
  let limited = false;
  try { await addExpense(target, {amount:'1',note:'超過邊界',spent_on:'2026-09-30',category:'餐飲',payment_source_id:'p1'}, '2026-09-30'); }
  catch (error) { limited = error.code === 'limit'; }
  ok(limited && (await exportPortableBackup(target)).byteLength === maximum, 'Over-limit write changed ledger');
  timings.boundary_ms = Math.round(stamp() - start);
  result.textContent = '64 MiB 邊界完成；正在驗證 100,000 列邊界……';
  const rowLimit = readBackup(input);
  const otherRows = SECTIONS.filter(section => section !== 'actions' && section !== 'settings')
    .reduce((total, section) => total + rowLimit.data[section].length, 0);
  while (rowLimit.data.actions.length + otherRows < 99998) {
    rowLimit.data.actions.push({ ...originalAction, id:'a_limit_' + rowLimit.data.actions.length, expense_id:'e1' });
  }
  const limitPayload = writeBackup(rowLimit);
  tx = target.transaction([...SECTIONS,'meta'],'readwrite');
  for (const section of [...SECTIONS,'meta']) tx.objectStore(section).clear();
  await done(tx);
  start = stamp();
  await restorePortableBackup(target, limitPayload);
  const permitted = await addExpense(target, {amount:'1',note:'最後合法紀錄',spent_on:'2026-09-30',category:'餐飲',payment_source_id:'p1'}, '2026-09-30');
  ok(permitted.actions.length + otherRows + permitted.expenses.length - rowLimit.data.expenses.length === 100000,
    'Legal final two rows were not committed');
  const beforeRefusal = await exportPortableBackup(target);
  limited = false;
  try { await addExpense(target, {amount:'1',note:'超過列數',spent_on:'2026-09-30',category:'餐飲',payment_source_id:'p1'}, '2026-09-30'); }
  catch (error) { limited = error.code === 'limit'; }
  ok(limited && new TextDecoder().decode(await exportPortableBackup(target)) === new TextDecoder().decode(beforeRefusal),
    '100001st row changed ledger');
  timings.row_limit_ms = Math.round(stamp() - start);
  details.textContent = JSON.stringify({ browser:navigator.userAgent, expenses:changed.expenses.length,
    actions:changed.actions.length, bytes:after.byteLength, boundary_bytes:exact.byteLength, timings }, null, 2);
  result.textContent = 'PASS：20,000／50,000 往返、64 MiB 與 100,000 列邊界';
} catch (error) { result.textContent = 'FAIL：' + error.message; throw error; }
finally { source?.close(); target?.close(); }
