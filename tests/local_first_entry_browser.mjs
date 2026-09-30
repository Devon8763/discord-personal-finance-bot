import { DB_NAME, openDatabase, readLedger, restorePortableBackup, exportPortableBackup } from '../local-first/ledger.mjs';
import { readBackup, writeBackup, MAX_BYTES } from '../local-first/backup.mjs';
import { SECTIONS } from '../local-first/backup.mjs';

const report = document.createElement('pre');
document.body.prepend(report);
const params = new URLSearchParams(location.search);
const test = params.get('case') || 'start';
const run = params.get('run') || crypto.randomUUID();
const name = `${DB_NAME}-entry-${test}-${run}`;
const lines = [];
const ok = (condition, message) => { if (!condition) throw new Error(message); };
const done = tx => new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = reject; });
const request = q => new Promise((resolve, reject) => { q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error); });
const count = async db => { const tx = db.transaction([...SECTIONS,'meta'], 'readonly'); const complete = done(tx); const values = await Promise.all([...SECTIONS,'meta'].map(section => request(tx.objectStore(section).count()))); await complete; return values.reduce((a,b)=>a+b,0); };
const waitFor = predicate => new Promise((resolve, reject) => {
  if (predicate()) { resolve(); return; }
  const timeout = setTimeout(() => { observer.disconnect(); reject(new Error('Page did not update')); }, 5000);
  const observer = new MutationObserver(() => { if (predicate()) { observer.disconnect(); clearTimeout(timeout); resolve(); } });
  observer.observe(document.body, { childList: true, characterData: true, attributes: true, subtree: true });
});
const rejected = async (operation, code) => { let failure; try { await operation(); } catch (caught) { failure = caught; } ok(failure?.code === code, `Expected ${code}`); };
const bytes = value => new Uint8Array(value);
const text = value => new TextDecoder().decode(value);
const chooseFile = file => {
  const input = document.querySelector('#restore-file');
  const transfer = new DataTransfer();
  if (file) transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event('change'));
};

// This one-purpose interception isolates the actual page without clearing any existing DB.
const open = indexedDB.open.bind(indexedDB);
indexedDB.open = (value, version) => {
  const request = open(value === DB_NAME ? name : value, version);
  if (value === DB_NAME) request.addEventListener('success', () => Object.defineProperty(request.result, 'name', { value: DB_NAME }));
  return request;
};
const db = await openDatabase();
// Exercise the main-page permission branch against this separate synthetic store.
try {
  const previouslyCreated = params.has('resume');
  if (!previouslyCreated) ok(await count(db) === 0, 'Test database must start blank');
  await import('../local-first/page.mjs');
  const welcome = document.querySelector('#welcome');
  const ledger = document.querySelector('#ledger-page');
  const management = document.querySelector('#data-management');
  const status = document.querySelector('#status');
  const error = document.querySelector('#error');
  ok(welcome && ledger && management, 'Missing first-use or data-management UI');

  if (params.has('inspect') || params.has('inspect-error') || params.has('inspect-preview')) {
    ok((await count(db)) === 0 && !welcome.hidden && ledger.hidden, 'Inspection must start with an empty synthetic ledger');
    if (params.has('inspect-error')) {
      chooseFile(new File(['invalid synthetic JSON'], 'invalid.json'));
      await waitFor(() => error.textContent.length > 0);
    }
    if (params.has('inspect-preview')) {
      const payload = await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer();
      chooseFile(new File([payload], 'synthetic.json'));
      await waitFor(() => !document.querySelector('#restore-review').hidden);
    }
  } else if (previouslyCreated) {
    ok(welcome.hidden && !ledger.hidden && !management.hidden, 'Existing ledger incorrectly shows first-use choice');
    const saved = await readLedger(db);
    ok((await count(db)) > 0 && saved.expenses.length > 0, 'Same-origin reopened ledger missing');
    lines.push('同來源重新開啟：直接進入原帳本、資料與管理入口仍在');
  } else {
    ok(!welcome.hidden && ledger.hidden && management.hidden, 'Blank page should show only two choices');
    ok((await count(db)) === 0, 'Opening page created a ledger or marker');
    lines.push('首次載入唯讀，未建立帳本／標記');
    const input = document.querySelector('#restore-file');
    let picker = 0;
    input.click = () => { picker++; };
    document.querySelector('#choose-restore').click();
    ok(picker === 1, 'Restore button did not open file picker');
    chooseFile(null);
    ok((await count(db)) === 0 && document.querySelector('#restore-review').hidden, 'Cancel file selection wrote data');
    lines.push('選檔取消無寫入');

    if (test === 'start') {
      document.querySelector('#start-ledger').click();
      await waitFor(() => !ledger.hidden);
      const state = await readLedger(db);
      ok((await count(db)) > 0 && state.expenses.length === 0 && state.payment_sources.length === 2, 'Start did not atomically create defaults');
      ok(welcome.hidden && !management.hidden && !document.querySelector('#fields').disabled, 'Start did not enter original ledger');
      lines.push('明確開始才建立，原記帳頁與下載入口可用');
    } else {
      const secret = 'DO-NOT-ECHO-SYNTHETIC-SECRET';
      chooseFile(new File([secret], 'wrong.json', { type: 'application/json' }));
      await waitFor(() => error.textContent.length > 0);
      ok(!error.textContent.includes(secret) && (await count(db)) === 0, 'Invalid file leaked data or wrote ledger');
      chooseFile(new File([new Uint8Array(MAX_BYTES + 1)], 'too-big.json'));
      await waitFor(() => error.textContent.includes('上限'));
      ok((await count(db)) === 0, 'Oversized file wrote ledger');
      lines.push('錯誤／超限檔案拒絕，錯誤不回顯內容');
      const payload = bytes(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer());
      const parsed = readBackup(payload);
      const file = new File([payload], 'synthetic.json', { type: 'application/json' });
      chooseFile(file);
      await waitFor(() => !document.querySelector('#restore-review').hidden);
      ok(document.activeElement.id === 'restore-title', 'Keyboard focus did not move to restore summary');
      const summary = document.querySelector('#restore-summary').textContent;
      ok(summary.includes('消費') && summary.includes('操作') && summary.includes('設定') && !summary.includes(parsed.data.expenses[0].note), 'Review must show nine counts, not raw data');
      document.querySelector('#cancel-restore').click();
      ok((await count(db)) === 0 && document.querySelector('#restore-review').hidden, 'Cancel confirmation wrote data');
      ok(document.activeElement.id === 'choose-restore', 'Cancel did not return focus to file choice');
      lines.push('九類摘要確認與取消無寫入');
      chooseFile(file);
      await waitFor(() => !document.querySelector('#restore-review').hidden);
      if (test === 'failure') {
        const add = IDBObjectStore.prototype.add;
        IDBObjectStore.prototype.add = function (...args) { const q = add.apply(this, args); q.addEventListener('success', () => this.transaction.abort()); return q; };
        document.querySelector('#confirm-restore').click();
        await waitFor(() => error.textContent.includes('還原未完成'));
        IDBObjectStore.prototype.add = add;
        ok((await count(db)) === 0, 'Aborted restore left ledger or marker');
        lines.push('request success 後 abort 完整回滾');
        chooseFile(file);
        await waitFor(() => !document.querySelector('#restore-review').hidden);
        const storageDetail = 'SYNTHETIC-INTERNAL-STORAGE-DETAIL';
        IDBObjectStore.prototype.add = () => { throw new DOMException(storageDetail, 'QuotaExceededError'); };
        document.querySelector('#confirm-restore').click();
        await waitFor(() => error.textContent.includes('還原未完成'));
        IDBObjectStore.prototype.add = add;
        ok((await count(db)) === 0 && !error.textContent.includes(storageDetail), 'Quota failure left data or leaked browser details');
        lines.push('容量錯誤完整回滾且不顯示底層訊息');
        chooseFile(file);
        await waitFor(() => !document.querySelector('#restore-review').hidden);
      }
      document.querySelector('#confirm-restore').click();
      await waitFor(() => !ledger.hidden);
      ok(welcome.hidden && !management.hidden, 'Restore did not enter original ledger');
      ok(text(await exportPortableBackup(db)) === text(writeBackup(parsed)), 'Nine sections, large integers or history changed on restore');
      ok((await readLedger(db)).expenses.length === parsed.data.expenses.length, 'Restore did not reread ledger');
      ok(document.querySelector('#expenses').textContent.includes('<img src=x onerror=alert(1)>') &&
        !document.querySelector('#expenses img') && !document.querySelector('#expenses script') &&
        !performance.getEntriesByType('resource').some(entry => new URL(entry.name).pathname === '/local-first/x'),
      'Restored malicious text executed or requested a URL');
      ok(performance.getEntriesByType('resource').every(entry => new URL(entry.name).origin === location.origin), 'Restore page requested an external origin');
      await rejected(() => restorePortableBackup({ name: DB_NAME, transaction: (...args) => db.transaction(...args) }, payload, true), 'nonempty');
      lines.push('空白目標確認還原、九類資料保真、非空再次拒絕');
    }

    const before = text(await exportPortableBackup(db));
    const click = HTMLAnchorElement.prototype.click;
    let link;
    HTMLAnchorElement.prototype.click = function () { link = { href: this.href, download: this.download }; };
    document.querySelector('#download-backup').click();
    await waitFor(() => status.textContent.includes('已要求瀏覽器下載'));
    HTMLAnchorElement.prototype.click = click;
    ok(link?.download === 'life-ledger-backup-v1.json', 'Download filename must be fixed');
    const received = bytes(await (await fetch(link.href)).arrayBuffer());
    ok(text(received) === before && text(await exportPortableBackup(db)) === before, 'Download changed ledger or backup');
    ok(readBackup(received).version === 1, 'Downloaded bytes are not valid v1');
    lines.push('下載固定檔名與UTF-8 v1完整備份；前後帳本相同');
    db.close();
    const reopened = await openDatabase();
    ok(text(await exportPortableBackup(reopened)) === before, 'Reopened database changed backup');
    reopened.close();
    lines.push('關閉並重開同來源後資料保留');
  }
  report.textContent = params.has('inspect') || params.has('inspect-error') || params.has('inspect-preview')
    ? '合成資料手動畫面檢查；此頁只使用獨立測試帳本。'
    : `PASS：${lines.length}組首次選擇／還原／下載驗證\n` + lines.join('\n');
} catch (failure) {
  report.textContent = 'FAIL：' + failure.message + '\n' + lines.join('\n');
  throw failure;
} finally { db.close(); }
