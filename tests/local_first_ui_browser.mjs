import { DB_NAME, openDatabase, initializeLedger, readLedger, editExpense } from '../local-first/ledger.mjs';
import { SECTIONS, writeBackup } from '../local-first/backup.mjs';
import { portable } from '../local-first/idb.mjs';
const lines = [];
const report = document.createElement('pre');
document.body.prepend(report);
function ok(value, message) { if (!value) throw new Error(message); }
function done(action) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { observer.disconnect(); reject(new Error('Write did not finish')); }, 5000);
    const observer = new MutationObserver(() => {
      if (document.querySelector('#status').textContent.includes('正在儲存') || document.querySelector('#fields').disabled || document.querySelector('#confirm-delete').disabled) return;
      observer.disconnect(); clearTimeout(timeout); resolve();
    });
    observer.observe(document.body, { childList: true, attributes: true, subtree: true });
    action();
  });
}
// Isolate the actual page in a third, disposable DB; no prototype override ships in the page.
const open = indexedDB.open.bind(indexedDB);
indexedDB.open = (name, version) => open(name === DB_NAME ? DB_NAME + '-ui-checks' : name, version);
const db = await openDatabase();
let tx = db.transaction([...SECTIONS,'meta'], 'readwrite');
for (const section of [...SECTIONS,'meta']) tx.objectStore(section).clear();
await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = reject; });
try {
  const initial = await initializeLedger(db);
  const maliciousCategory = '<img src=x onerror=alert(2)>合成分類';
  const changed = structuredClone(initial);
  changed.categories.push({ name: maliciousCategory, active: 1 });
  tx = db.transaction(['categories','meta'], 'readwrite');
  tx.objectStore('categories').add({ key:maliciousCategory, position:0, value:changed.categories[0] });
  tx.objectStore('meta').put({ key:'row_count', value:3 });
  tx.objectStore('meta').put({ key:'backup_bytes', value:writeBackup(portable(changed)).byteLength });
  await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = reject; });
  await import('../local-first/page.mjs');
  const form = document.querySelector('#expense-form');
  const input = (amount, note) => { form.elements.amount.value = amount; form.elements.note.value = note; };
  input('0.001', '保留無效合成輸入');
  await done(() => form.requestSubmit());
  ok(form.elements.amount.value === '0.001' && form.elements.note.value === '保留無效合成輸入', 'Invalid input lost');
  ok(document.querySelector('#error').textContent.includes('兩位小數'), 'Validation message lost');
  ok((await readLedger(db)).actions.length === 0, 'Invalid input wrote');
  lines.push('invalid input retained; no action');

  // Native request success followed by forced abort simulates a storage failure after put.
  const put = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (...args) {
    const request = put.apply(this, args);
    request.addEventListener('success', () => this.transaction.abort());
    return request;
  };
  input('12.34', '保留儲存失敗合成輸入');
  await done(() => form.requestSubmit());
  IDBObjectStore.prototype.put = put;
  ok(form.elements.amount.value === '12.34' && form.elements.note.value === '保留儲存失敗合成輸入', 'Failed storage lost input');
  ok((await readLedger(db)).expenses.length === 0, 'Failed storage left record');
  lines.push('storage failure retains input and rolls back');

  const storageDetail = 'SYNTHETIC-STORAGE-DETAIL-MUST-NOT-APPEAR';
  IDBObjectStore.prototype.put = () => { throw new DOMException(storageDetail, 'QuotaExceededError'); };
  input('12.34', '保留容量不足合成輸入');
  await done(() => form.requestSubmit());
  IDBObjectStore.prototype.put = put;
  ok(!document.querySelector('#error').textContent.includes(storageDetail), 'Raw browser storage error leaked into UI');
  ok(form.elements.note.value === '保留容量不足合成輸入' && (await readLedger(db)).expenses.length === 0, 'Quota failure changed input or data');
  lines.push('quota failure hides browser details and preserves input');

  IDBObjectStore.prototype.put = () => { throw new Error(storageDetail); };
  await done(() => form.requestSubmit());
  IDBObjectStore.prototype.put = put;
  ok(!document.querySelector('#error').textContent.includes(storageDetail), 'Raw storage error leaked into UI');

  input('12.34', '<img src="/local-first/x" onerror="window.__synthetic_xss=1">合成文字');
  form.elements.category.value = maliciousCategory;
  await done(() => form.requestSubmit());
  ok(document.querySelector('#expenses').textContent.includes('<img src="/local-first/x" onerror="window.__synthetic_xss=1">'), 'Unsafe text missing');
  ok(!document.querySelector('#expenses img'), 'Injected HTML');
  ok(!window.__synthetic_xss && !performance.getEntriesByType('resource').some(entry => new URL(entry.name).pathname === '/local-first/x'), 'Malicious text executed or requested a URL');
  ok(performance.getEntriesByType('resource').every(entry => new URL(entry.name).origin === location.origin), 'Page requested an external origin');
  ok(document.querySelector('#expenses').textContent.includes(maliciousCategory), 'Category text lost');
  ok(form.elements.amount.value === '', 'Success did not clear');
  lines.push('safe text and successful input clearing');

  document.querySelector('#expenses button:last-child').click();
  document.querySelector('#cancel-delete').click();
  ok((await readLedger(db)).actions.length === 1, 'Cancel wrote action');
  lines.push('cancel delete does not write');
  document.querySelector('#expenses button:last-child').click();
  IDBObjectStore.prototype.put = function (...args) {
    const request = put.apply(this, args);
    request.addEventListener('success', () => this.transaction.abort());
    return request;
  };
  await done(() => document.querySelector('#confirm-delete').click());
  IDBObjectStore.prototype.put = put;
  ok(document.querySelector('#delete-dialog').open && document.querySelector('#delete-error')?.textContent, 'Delete failure has no visible dialog error');
  ok((await readLedger(db)).expenses[0].voided === 0, 'Failed delete left partial change');
  lines.push('delete storage failure keeps summary and visible error');
  await done(() => document.querySelector('#confirm-delete').click());
  ok((await readLedger(db)).expenses[0].voided === 1 && !document.querySelector('#expenses li'), 'Soft delete failed');
  lines.push('confirmed soft delete removes effective row');
  const transact = IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction = function (...args) {
    if (args[1] === 'readwrite') throw new DOMException(storageDetail, 'InvalidStateError');
    return transact.apply(this, args);
  };
  input('1.25', '無法開啟交易的合成輸入');
  await done(() => form.requestSubmit());
  IDBDatabase.prototype.transaction = transact;
  ok(form.elements.note.value === '無法開啟交易的合成輸入' && !document.querySelector('#error').textContent.includes(storageDetail), 'Unavailable database loses input or leaks detail');
  ok((await readLedger(db)).expenses.length === 1, 'Unavailable database changed data');
  lines.push('unavailable transaction retains input and safe error');
  input('1.25', '重複點擊合成消費');
  await done(() => { form.requestSubmit(); form.requestSubmit(); });
  ok((await readLedger(db)).expenses.length === 2, 'Repeated submission duplicates expense');
  lines.push('pending repeated submission records once');
  const current = (await readLedger(db)).expenses.at(-1);
  document.querySelector('#expenses button').click();
  const changedElsewhere = await editExpense(db, current.id, current.revision, {amount:'1.50',note:'另一分頁已儲存',spent_on:current.spent_on,category:current.category,payment_source_id:''}, current.spent_on);
  input('1.75', '舊分頁保留輸入');
  await done(() => form.requestSubmit());
  ok(form.elements.note.value === '舊分頁保留輸入' && document.querySelector('#error').textContent.includes('重新載入'), 'Stale edit loses input or safe conflict');
  ok(writeBackup(portable(await readLedger(db))).toString() === writeBackup(portable(changedElsewhere)).toString(), 'Stale edit overwrote newer data');
  document.querySelector('#cancel-edit').click();
  lines.push('two connections stale expense revision preserves draft and newer data');
  const modal = new URLSearchParams(location.search).get('case');
  if (modal === 'settings-dialog') {
    document.querySelector('#ledger-settings').open = true;
    const row = [...document.querySelectorAll('#category-list li')].find(row => row.querySelector('span').textContent === maliciousCategory);
    row.querySelector('[data-operation="rename"]').click();
    document.querySelector('#setting-name').value = '更改後合成分類';
  } else if (modal === 'fixed-dialog') {
    document.querySelector('#ledger-settings').open = true; document.querySelector('#fixed-settings').open = true;
    const fixed = document.querySelector('#fixed-form');
    fixed.elements.name.value = '確認停用故障合成規則'; fixed.elements.amount.value = '1'; fixed.elements.due_day.value = '1';
    await done(() => fixed.requestSubmit());
    document.querySelector('#fixed-list [data-operation="update"]').click();
    document.querySelector('#fixed-list [data-operation="stop"]').click();
  }
  const beforeCommit = await readLedger(db), replace = Element.prototype.replaceChildren;
  Element.prototype.replaceChildren = function (...args) {
    if (modal === 'settings-dialog' ? this === form.elements.category : modal === 'fixed-dialog' ? this === document.querySelector('#fixed-form').elements.category : this.id === 'category-list') { Element.prototype.replaceChildren = replace; throw new Error(storageDetail); }
    return replace.apply(this, args);
  };
  input('2.50', '交易完成後畫面故障合成消費');
  const finished = new Promise((resolve, reject) => {
    const observer = new MutationObserver(() => {
      if (document.querySelector('#status').textContent.includes('正在儲存')) return;
      observer.disconnect(); clearTimeout(timer); resolve();
    }), timer = setTimeout(() => { observer.disconnect(); reject(new Error('Post-commit UI timeout')); }, 5000);
    observer.observe(document.body, {childList:true,subtree:true});
  });
  if (modal === 'settings-dialog') document.querySelector('#settings-form').requestSubmit();
  else if (modal === 'fixed-dialog') document.querySelector('#confirm-fixed-stop').click();
  else form.requestSubmit();
  await finished; Element.prototype.replaceChildren = replace;
  const committed = await readLedger(db);
  ok(modal === 'settings-dialog' ? committed.categories.some(row => row.name === '更改後合成分類') : modal === 'fixed-dialog' ? committed.recurring_rules.at(-1).active === 0 : committed.expenses.length === beforeCommit.expenses.length + 1 && committed.actions.length === beforeCommit.actions.length + 1, 'Expected completed transaction missing');
  ok(document.querySelector('#status').textContent.includes('已儲存') && document.querySelector('#status').textContent.includes('重新載入'), 'Committed data falsely reported as failed');
  ok(document.querySelector('#fields').disabled && !document.querySelector('#download-backup').disabled, 'Post-commit failure permits repeated writes or blocks backup');
  ok(![...document.querySelectorAll('dialog')].some(dialog => dialog.open), 'Committed UI failure traps backup behind modal');
  document.querySelector('#download-backup').click();
  ok(!document.querySelector('#export-panel').hidden, 'Post-commit failure blocks backup panel');
  input('2.50', '不得重複寫入'); form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 50));
  ok((await readLedger(db)).expenses.length === committed.expenses.length && !document.querySelector('#error').textContent.includes(storageDetail), 'Post-commit retry writes or leaks detail');
  lines.push('committed UI failure reports truth, blocks writes until reload, preserves backup access');
  report.textContent = 'PASS：' + lines.length + '組實際頁面故障／安全操作驗證\n' + lines.join('\n');
} catch (error) { report.textContent = 'FAIL：' + error.message + '\n' + lines.join('\n'); throw error; }
finally { db.close(); }
