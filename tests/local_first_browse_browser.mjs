import { DB_NAME, openDatabase, readLedger, exportPortableBackup, addExpense } from '../local-first/ledger.mjs';
import { readBackup, SECTIONS } from '../local-first/backup.mjs';
import { addState } from '../local-first/idb.mjs';

const check = (value, message) => { if (!value) throw new Error(message); };
const report = document.createElement('pre');
document.body.prepend(report);
const open = indexedDB.open.bind(indexedDB);
indexedDB.open = (name, version) => open(name === DB_NAME ? DB_NAME + '-browse-checks' : name, version);
const db = await openDatabase();
const sections = [...SECTIONS, 'meta'];
let tx = db.transaction(sections, 'readwrite');
for (const section of sections) tx.objectStore(section).clear();
await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = reject; });
const fixture = readBackup(new Uint8Array(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer()));
const seeded = { format:'local-first-test-ledger', version:2, owner:'local-test-owner', ...fixture.data };
seeded.expenses.find(row => row.id === 'e2').voided = 0;
for (let i = 0; i < 65; i++) seeded.expenses.push({ ...seeded.expenses[0], id:`batch-${i}`, spent_on:'2025-01-01',
  cents:'1', note:'批次測試', revision:0 });
tx = db.transaction(sections, 'readwrite');
addState(tx, seeded);
await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = reject; });
const before = await exportPortableBackup(db);
const actions = (await readLedger(db)).actions.length;
const lines = [];
try {
  await import('../local-first/page.mjs');
  const form = document.querySelector('#search-form');
  check(form && !document.querySelector('#ledger-page').hidden, 'Search UI unavailable');
  form.elements.keyword.value = '  ';
  form.elements.end.value = '2025-01-31';
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 50));
  check(document.querySelector('#search-error').textContent.includes('關鍵字或開始日期'), 'Invalid search not reported');
  check(form.elements.end.value === '2025-01-31', 'Invalid search input lost');
  lines.push('invalid input retained');

  form.elements.keyword.value = '午餐';
  form.elements.start.setCustomValidity('invalid synthetic date');
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 50));
  check(document.querySelector('#search-error').textContent.includes('日期') &&
    !document.querySelector('#search-count').textContent, 'Native invalid date was queried');
  form.elements.start.setCustomValidity('');
  lines.push('invalid native date rejected');

  form.elements.keyword.value = '原固定';
  form.elements.end.value = '';
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 100));
  check(document.querySelector('#search-results').textContent.includes('原固定'), 'Fixed expense missing');
  check(!document.querySelector('#search-results button'), 'Search added an edit/delete control');
  lines.push('search across restored source');

  form.elements.keyword.value = '<img src=x';
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 100));
  check(document.querySelector('#search-results').textContent.includes('<img src=x'), 'Malicious text missing');
  check(!document.querySelector('#search-results img') && !window.__synthetic_xss, 'Search executed HTML');
  check(!location.search && performance.getEntriesByType('resource').every(item => new URL(item.name).origin === location.origin),
    'Search text entered URL or loaded an external resource');
  lines.push('untrusted search result is text');

  form.elements.keyword.value = '批次測試';
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 100));
  check(document.querySelector('#search-count').textContent.includes('65'), 'Full result count missing');
  check(document.querySelectorAll('#search-results li').length === 50 && !document.querySelector('#search-more').hidden, 'First batch incorrect');
  document.querySelector('#search-more').click();
  check(document.querySelectorAll('#search-results li').length === 65, 'Matching rows silently truncated');
  lines.push('all results beyond first batch');

  check(document.querySelector('#calendar-month').textContent, 'Calendar missing');
  const parts = new Intl.DateTimeFormat('en-US', { timeZone:'Asia/Taipei', year:'numeric', month:'2-digit' }).formatToParts(new Date());
  const year = Number(parts.find(part => part.type === 'year').value);
  const month = Number(parts.find(part => part.type === 'month').value);
  for (let i = 0; i < (year - 2025) * 12 + month - 1; i++) {
    const heading = document.querySelector('#calendar-month');
    const beforeMonth = heading.textContent;
    const changed = new Promise((resolve, reject) => {
      const observer = new MutationObserver(() => {
        if (heading.textContent !== beforeMonth) { observer.disconnect(); clearTimeout(timeout); resolve(); }
      });
      const timeout = setTimeout(() => { observer.disconnect(); reject(new Error('Month did not change')); }, 3000);
      observer.observe(heading, { childList:true, subtree:true });
    });
    document.querySelector('#calendar-prev').click();
    await changed;
  }
  check(document.querySelector('#calendar-month').textContent.includes('2025'), 'Month navigation failed');
  check(document.querySelector('#calendar-grid').children.length >= 35, 'Calendar alignment missing');
  document.querySelector('[data-day="2025-01-29"]').click();
  check(document.querySelector('#calendar-day-total').textContent.includes('10.29'), 'Daily total missing');
  check(document.querySelector('#calendar-day-results').textContent.includes('分期'), 'Daily expense missing');
  lines.push('calendar navigation');

  check((await readLedger(db)).actions.length === actions, 'Browse wrote an action');
  check(new TextDecoder().decode(await exportPortableBackup(db)) === new TextDecoder().decode(before), 'Browse changed backup');
  lines.push('browse read-only snapshot');

  await addExpense(db, { amount:'1.25', note:'另一頁合成新增', spent_on:'2025-01-15',
    category:'居住', payment_source_id:'p1' }, '2026-10-01');
  const afterOtherTab = await exportPortableBackup(db);
  form.elements.keyword.value = '另一頁合成新增';
  form.requestSubmit();
  await new Promise(resolve => setTimeout(resolve, 100));
  check(document.querySelector('#search-count').textContent.includes('1 筆') &&
    document.querySelector('[data-day="2025-01-15"]').getAttribute('aria-label').includes('當日有消費'),
    'Search and calendar used different snapshots');
  check(new TextDecoder().decode(await exportPortableBackup(db)) === new TextDecoder().decode(afterOtherTab),
    'Search wrote after another tab changed data');
  lines.push('search and calendar refresh from one snapshot');
  report.textContent = 'PASS：' + lines.length + '組搜尋／月曆實際頁面驗證\n' + lines.join('\n');
} catch (error) { report.textContent = 'FAIL：' + error.message + '\n' + lines.join('\n'); throw error; }
finally { db.close(); }
