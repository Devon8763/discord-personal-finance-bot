import { openDatabase, readLedgerIfPresent, readLedger, initializeLedger, restorePortableBackup, exportPortableBackup, addExpense, editExpense, voidExpense, LedgerError } from './ledger.mjs';
import { readBackup, validateStorageLimits, MAX_BYTES, SECTIONS } from './backup.mjs';
import { categoryOptions, cents, ValidationError } from './rules.mjs';

const form = document.querySelector('#expense-form');
const fields = document.querySelector('#fields');
const status = document.querySelector('#status');
const error = document.querySelector('#error');
const list = document.querySelector('#expenses');
const dialog = document.querySelector('#delete-dialog');
const welcome = document.querySelector('#welcome');
const ledgerPage = document.querySelector('#ledger-page');
const management = document.querySelector('#data-management');
const restoreFile = document.querySelector('#restore-file');
const review = document.querySelector('#restore-review');
let db, state, editing = null, deleting = null, busy = false;
let pendingBackup = null, selection = 0;

function taiwanToday() {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}
function amountText(value, grouped = true) {
  const total = cents(value);
  const whole = (total / 100n).toString();
  const fraction = (total % 100n).toString().padStart(2, '0').replace(/0+$/, '');
  return (grouped ? whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : whole) + (fraction ? '.' + fraction : '');
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function choices() {
  const categories = categoryOptions(state).filter(option => option.active === 1 || option.name === editing?.category);
  form.elements.category.replaceChildren(...categories.map(option => {
    const element = node('option', option.name + (option.active === 0 ? '（停用，保留原值）' : ''));
    element.value = option.name;
    return element;
  }));
  const payments = state.payment_sources.filter(option => option.active === 1).map(option => {
    const element = node('option', option.name); element.value = option.id; return element;
  });
  if (editing) {
    const retain = node('option', '保留原付款方式：' + editing.payment_source_name); retain.value = ''; payments.unshift(retain);
  }
  form.elements.payment_source_id.replaceChildren(...payments);
}
function resetForm() {
  editing = null;
  choices();
  form.elements.amount.value = '';
  form.elements.note.value = '';
  form.elements.spent_on.value = taiwanToday();
  document.querySelector('#form-title').textContent = '記錄消費';
  document.querySelector('#save').textContent = '儲存消費';
  document.querySelector('#cancel-edit').hidden = true;
}
function render() {
  list.replaceChildren();
  const entries = state.expenses.filter(row => row.voided === 0).slice().reverse();
  document.querySelector('#empty').hidden = entries.length !== 0;
  for (const entry of entries) {
    const row = node('li', undefined, 'expense');
    for (const [label, value] of [['日期', entry.spent_on], ['項目', entry.note], ['金額', amountText(entry.cents) + ' 元'], ['分類', entry.category], ['付款方式', entry.payment_source_name]]) {
      const cell = node('div'); cell.append(node('small', label), node('span', value)); row.append(cell);
    }
    const buttons = node('div', undefined, 'buttons');
    const edit = node('button', '更改'); edit.type = 'button';
    edit.onclick = () => {
      if (busy) return;
      editing = structuredClone(entry); choices(); error.textContent = '';
      form.elements.amount.value = amountText(entry.cents, false);
      form.elements.note.value = entry.note;
      form.elements.spent_on.value = entry.spent_on;
      form.elements.category.value = entry.category;
      form.elements.payment_source_id.value = '';
      document.querySelector('#form-title').textContent = '更改消費';
      document.querySelector('#save').textContent = '儲存更改';
      document.querySelector('#cancel-edit').hidden = false;
      form.elements.amount.focus();
    };
    const remove = node('button', '刪除'); remove.type = 'button';
    remove.onclick = () => {
      if (busy) return;
      deleting = structuredClone(entry);
      document.querySelector('#delete-error').textContent = '';
      document.querySelector('#delete-summary').textContent = `${entry.spent_on}｜${entry.note}｜${amountText(entry.cents)} 元｜${entry.category}｜${entry.payment_source_name}`;
      dialog.showModal();
    };
    buttons.append(edit, remove); row.append(buttons); list.append(row);
  }
}
async function saving(operation, success) {
  if (busy) return;
  busy = true; fields.disabled = true; error.textContent = '';
  document.querySelector('#delete-error').textContent = '';
  document.querySelector('#confirm-delete').disabled = true;
  document.querySelector('#cancel-delete').disabled = true;
  status.textContent = '正在儲存瀏覽器本機測試帳本……';
  try {
    state = await operation(); // Resolved only by transaction complete, never request success.
    success(); render();
    status.textContent = '瀏覽器本機測試帳本；儲存完成。';
  } catch (failure) {
    error.textContent = failure instanceof LedgerError || failure instanceof ValidationError ? failure.message : '儲存未完成，請確認瀏覽器儲存空間後重試。';
    if (dialog.open) document.querySelector('#delete-error').textContent = error.textContent;
    status.textContent = '瀏覽器本機測試帳本；此次儲存未完成。';
  } finally {
    busy = false; fields.disabled = false;
    document.querySelector('#confirm-delete').disabled = false;
    document.querySelector('#cancel-delete').disabled = false;
  }
}
form.addEventListener('submit', event => {
  event.preventDefault();
  const input = Object.fromEntries(new FormData(form));
  const today = taiwanToday();
  saving(() => editing ? editExpense(db, editing.id, editing.revision, input, today) : addExpense(db, input, today), resetForm);
});
document.querySelector('#cancel-edit').onclick = () => { if (!busy) { resetForm(); error.textContent = ''; } };
document.querySelector('#cancel-delete').onclick = () => dialog.close();
dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
dialog.addEventListener('close', () => { deleting = null; });
document.querySelector('#confirm-delete').onclick = () => saving(
  () => voidExpense(db, deleting.id, deleting.revision),
  () => { if (editing?.id === deleting.id) resetForm(); dialog.close(); }
);
function showLedger() {
  welcome.hidden = true;
  review.hidden = true;
  ledgerPage.hidden = false;
  management.hidden = false;
  resetForm(); render(); fields.disabled = false;
  status.textContent = '瀏覽器本機測試帳本；資料僅保存在此瀏覽器與來源。';
}
function restoreControls(disabled) {
  for (const id of ['start-ledger', 'choose-restore', 'confirm-restore', 'cancel-restore']) {
    document.getElementById(id).disabled = disabled;
  }
}
document.querySelector('#start-ledger').onclick = async () => {
  if (busy) return;
  busy = true; restoreControls(true); error.textContent = '';
  try {
    state = await initializeLedger(db);
    showLedger();
  } catch {
    error.textContent = '建立本機測試帳本失敗，資料沒有變更。請確認儲存空間後重試。';
  } finally { busy = false; restoreControls(false); }
};
document.querySelector('#choose-restore').onclick = () => { if (!busy) restoreFile.click(); };
restoreFile.onchange = async () => {
  const file = restoreFile.files[0];
  const currentSelection = ++selection;
  restoreFile.value = '';
  pendingBackup = null;
  review.hidden = true;
  error.textContent = '';
  if (!file || busy) return;
  if (file.size > MAX_BYTES) { error.textContent = '備份檔超過 64 MiB 上限。'; return; }
  try {
    const payload = new Uint8Array(await file.arrayBuffer());
    const backup = readBackup(payload);
    validateStorageLimits(backup);
    if (currentSelection !== selection) return;
    const labels = { expenses: '消費', categories: '分類', payment_sources: '付款方式', budgets: '預算', recurring_rules: '定期規則', recurring_versions: '歷史版本', shortcuts: '捷徑', actions: '操作紀錄', settings: '設定' };
    document.querySelector('#restore-summary').replaceChildren(...SECTIONS.map(section => node('li', `${labels[section]}：${section === 'settings' ? 1 : backup.data[section].length} 項`)));
    pendingBackup = payload;
    review.hidden = false;
    document.querySelector('#restore-title').focus();
  } catch {
    if (currentSelection === selection) error.textContent = '備份檔格式或資料不符合規則，或超過本機容量上限。請選擇有效的完整備份。';
  }
};
document.querySelector('#cancel-restore').onclick = () => {
  if (busy) return;
  ++selection; pendingBackup = null; review.hidden = true; error.textContent = '';
  document.querySelector('#choose-restore').focus();
};
document.querySelector('#confirm-restore').onclick = async () => {
  if (busy || !pendingBackup) return;
  busy = true; restoreControls(true); error.textContent = '';
  let completed = false;
  try {
    await restorePortableBackup(db, pendingBackup, true);
    completed = true;
    state = await readLedger(db);
    pendingBackup = null;
    showLedger();
  } catch {
    pendingBackup = null; review.hidden = true;
    error.textContent = completed ? '還原交易已完成，但重新讀取失敗。請重新載入頁面確認資料。' : '還原未完成，資料沒有變更。請重新選擇備份；若帳本已有資料，無法還原。';
  } finally { busy = false; restoreControls(false); }
};
document.querySelector('#download-backup').onclick = async () => {
  if (busy) return;
  busy = true; error.textContent = '';
  try {
    const payload = await exportPortableBackup(db);
    const url = URL.createObjectURL(new Blob([payload], { type: 'application/json;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'life-ledger-backup-v1.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    status.textContent = '已要求瀏覽器下載完整備份；請確認檔案已保存並自行妥善保管。';
  } catch {
    error.textContent = '無法產生完整備份，帳本沒有變更。請確認瀏覽器儲存狀態後重試。';
  } finally { busy = false; }
};
try {
  db = await openDatabase();
  state = await readLedgerIfPresent(db);
  if (state) showLedger();
  else {
    welcome.hidden = false;
    status.textContent = '瀏覽器本機測試帳本尚未建立；請選擇開始記帳或從備份還原。';
  }
} catch {
  error.textContent = '無法開啟本機測試帳本。請保留資料並確認瀏覽器允許本機儲存。';
  status.textContent = '瀏覽器本機測試帳本尚未就緒。';
}

if (['/local-first/', '/local-first/index.html'].includes(location.pathname) && 'serviceWorker' in navigator) {
  const offlineStatus = document.querySelector('#offline-status');
  let checkNumber = 0;
  const checkOffline = async () => {
    const current = ++checkNumber;
    offlineStatus.textContent = '離線重新開啟尚未就緒；請保持預覽服務開啟。';
    const controller = navigator.serviceWorker.controller;
    if (!controller || new URL(controller.scriptURL).pathname !== '/local-first/sw.js') return;
    const channel = new MessageChannel();
    const complete = new Promise(resolve => {
      const timeout = setTimeout(() => resolve(false), 5000);
      channel.port1.onmessage = event => { clearTimeout(timeout); resolve(event.data === true); };
    });
    try { controller.postMessage({ type: 'offline-ready' }, [channel.port2]); }
    catch { channel.port1.close(); return; }
    const ready = await complete;
    channel.port1.close();
    if (current !== checkNumber) return;
    offlineStatus.textContent = ready
      ? '可離線重新開啟（相同瀏覽器與網址）；請定期下載備份。'
      : '離線重新開啟尚未就緒；請保持預覽服務開啟。';
  };
  navigator.serviceWorker.addEventListener('controllerchange', checkOffline);
  window.addEventListener('pageshow', checkOffline);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkOffline(); });
  navigator.serviceWorker.register('/local-first/sw.js', { scope: '/local-first/' })
    .then(checkOffline)
    .catch(checkOffline);
}
