import { openDatabase, readLedgerIfPresent, readLedger, initializeLedger, restorePortableBackup, exportPortableBackup, addExpense, editExpense, voidExpense, updateCategory, updatePayment, updateBudget, taiwanToday, LedgerError } from './ledger.mjs';
import { readBackup, validateStorageLimits, MAX_BYTES, SECTIONS, integerValue, storedInteger } from './backup.mjs';
import { categoryOptions, categoryName, cents, ValidationError } from './rules.mjs';
import { validateSearch, searchExpenses, calendarMonth, monthBudget } from './browse.mjs';

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
let searchRows = [], searchShown = 0, lastQuery = null, calendarData = null, selectedDay = null;
let calendarShown = 0;
let viewedMonth = null;
const settingsDialog = document.querySelector('#settings-dialog');
let setting = null;

function amountText(value, grouped = true) {
  const total = typeof value === 'bigint' ? value : cents(value);
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
  renderSettings();
  renderBudgets();
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
function readOnlyRow(entry) {
  const row = node('li', undefined, 'expense');
  for (const [label, value] of [['日期', entry.spent_on], ['項目', entry.note],
    ['金額', amountText(entry.cents) + ' 元'], ['分類', entry.category], ['付款方式', entry.payment_source_name]]) {
    const cell = node('div'); cell.append(node('small', label), node('span', value)); row.append(cell);
  }
  return row;
}
function appendBatch(rows, listElement, more, shown) {
  listElement.append(...rows.slice(shown, shown + 50).map(readOnlyRow));
  more.hidden = shown + 50 >= rows.length;
  return Math.min(shown + 50, rows.length);
}
const searchForm = document.querySelector('#search-form');
function showSearch(snapshot, query, today) {
  searchRows = searchExpenses(snapshot, query, today);
  searchShown = 0;
  document.querySelector('#search-results').replaceChildren();
  document.querySelector('#search-count').textContent = searchRows.length
    ? `符合 ${searchRows.length} 筆消費。` : '沒有符合條件的消費。';
  searchShown = appendBatch(searchRows, document.querySelector('#search-results'), document.querySelector('#search-more'), searchShown);
}
searchForm.addEventListener('submit', async event => {
  event.preventDefault();
  const query = Object.fromEntries(new FormData(searchForm));
  const today = taiwanToday();
  const message = document.querySelector('#search-error');
  message.textContent = '';
  try {
    if (!searchForm.elements.start.validity.valid || !searchForm.elements.end.validity.valid) {
      throw new ValidationError('請輸入有效日期。');
    }
    validateSearch(query, today); // Invalid conditions never open a database read.
    const snapshot = await readLedger(db);
    lastQuery = query;
    showSearch(snapshot, query, today);
    showCalendar(snapshot, today);
  } catch (failure) {
    lastQuery = null;
    searchRows = [];
    document.querySelector('#search-results').replaceChildren();
    document.querySelector('#search-count').textContent = '';
    document.querySelector('#search-more').hidden = true;
    message.textContent = failure instanceof ValidationError ? failure.message : '無法搜尋帳目，請重新載入後再試。';
  }
});
document.querySelector('#search-more').onclick = () => {
  searchShown = appendBatch(searchRows, document.querySelector('#search-results'), document.querySelector('#search-more'), searchShown);
};
function nextMonth(month, offset) {
  const [year, number] = month.split('-').map(Number);
  const serial = year * 12 + number - 1 + offset;
  return `${String(Math.floor(serial / 12)).padStart(4, '0')}-${String(serial % 12 + 1).padStart(2, '0')}`;
}
function showDay(day) {
  selectedDay = day.date;
  document.querySelector('#calendar-day-details').hidden = false;
  document.querySelector('#calendar-day-title').textContent = day.date;
  document.querySelector('#calendar-day-total').textContent = `當日消費合計：${amountText(day.total)} 元`;
  document.querySelector('#calendar-day-empty').hidden = day.expenses.length !== 0;
  const listElement = document.querySelector('#calendar-day-results');
  listElement.replaceChildren();
  calendarShown = appendBatch(day.expenses, listElement, document.querySelector('#calendar-day-more'), 0);
  for (const button of document.querySelectorAll('#calendar-grid button[data-day]')) {
    button.setAttribute('aria-pressed', String(button.dataset.day === selectedDay));
  }
}
document.querySelector('#calendar-day-more').onclick = () => {
  const day = calendarData.days.find(item => item.date === selectedDay);
  calendarShown = appendBatch(day.expenses, document.querySelector('#calendar-day-results'),
    document.querySelector('#calendar-day-more'), calendarShown);
};
function showCalendar(snapshot, today) {
  calendarData = calendarMonth(snapshot, viewedMonth, today);
  document.querySelector('#calendar-month').textContent = viewedMonth.replace('-', ' 年 ') + ' 月';
  document.querySelector('#calendar-next').disabled = viewedMonth >= today.slice(0, 7);
  document.querySelector('#calendar-prev').disabled = viewedMonth === '0001-01';
  const grid = document.querySelector('#calendar-grid');
  grid.replaceChildren(...['一', '二', '三', '四', '五', '六', '日'].map(label => node('strong', label)));
  for (let i = 0; i < calendarData.leadingBlanks; i++) grid.append(node('span', ''));
  for (const day of calendarData.days) {
    const cell = node(day.isFuture ? 'span' : 'button');
    cell.textContent = String(day.day);
    cell.setAttribute('aria-label', day.date + (day.hasExpense ? '，當日有消費' : ''));
    if (!day.isFuture) {
      cell.type = 'button'; cell.dataset.day = day.date;
      cell.onclick = () => showDay(day);
    } else cell.setAttribute('aria-disabled', 'true');
    if (day.hasExpense) cell.append(node('span', '•', 'marker'));
    grid.append(cell);
  }
  document.querySelector('#calendar-empty').hidden = calendarData.days.some(day => day.hasExpense);
  document.querySelector('#calendar-day-details').hidden = true;
  selectedDay = null;
}
async function moveMonth(offset) {
  const today = taiwanToday();
  const month = nextMonth(viewedMonth, offset);
  if (month > today.slice(0, 7) || month < '0001-01') return;
  try {
    const snapshot = await readLedger(db);
    viewedMonth = month;
    showCalendar(snapshot, today);
    if (lastQuery) showSearch(snapshot, lastQuery, today);
    document.querySelector('#calendar-error').textContent = '';
  } catch { document.querySelector('#calendar-error').textContent = '無法讀取月曆，請重新載入後再試。'; }
}
document.querySelector('#calendar-prev').onclick = () => moveMonth(-1);
document.querySelector('#calendar-next').onclick = () => moveMonth(1);
async function saving(operation, success, message = error) {
  if (busy) return;
  const budgetControl = document.activeElement.closest('#budget-fields') ? document.activeElement : null;
  const budgetRow = budgetControl?.closest('#budget-setting-list > li');
  busy = true; fields.disabled = true; error.textContent = '';
  message.textContent = '';
  document.querySelector('#settings-fields').disabled = true;
  document.querySelector('#budget-fields').disabled = true;
  document.querySelector('#setting-fields').disabled = true;
  document.querySelector('#delete-error').textContent = '';
  document.querySelector('#confirm-delete').disabled = true;
  document.querySelector('#cancel-delete').disabled = true;
  status.textContent = '正在儲存瀏覽器本機測試帳本……';
  try {
    state = await operation(); // Resolved only by transaction complete, never request success.
    success(); render();
    if (lastQuery) showSearch(state, lastQuery, taiwanToday());
    if (viewedMonth) showCalendar(state, taiwanToday());
    status.textContent = '瀏覽器本機測試帳本；儲存完成。';
  } catch (failure) {
    message.textContent = failure instanceof LedgerError || failure instanceof ValidationError ? failure.message : '儲存未完成，請確認瀏覽器儲存空間後重試。';
    if (dialog.open) document.querySelector('#delete-error').textContent = error.textContent;
    status.textContent = '瀏覽器本機測試帳本；此次儲存未完成。';
  } finally {
    busy = false; fields.disabled = false;
    document.querySelector('#settings-fields').disabled = false;
    document.querySelector('#budget-fields').disabled = false;
    document.querySelector('#setting-fields').disabled = false;
    document.querySelector('#confirm-delete').disabled = false;
    document.querySelector('#cancel-delete').disabled = false;
    if (budgetControl) {
      const row = [...document.querySelectorAll('#budget-setting-list > li')].find(row => row.dataset.category === budgetRow?.dataset.category);
      const selector = budgetControl.tagName === 'INPUT' ? 'input' : budgetControl.dataset.operation === 'clear' ? '[data-operation="clear"]' : 'button[type="submit"]';
      const control = budgetControl.isConnected ? budgetControl : row?.querySelector(selector);
      (control || document.querySelector('#budget-settings summary')).focus();
    }
  }
}
function renderSettings() {
  for (const [type, options] of [['category', categoryOptions(state)], ['payment', state.payment_sources]]) {
    const rows = options.map(option => {
      const row = node('li'); row.append(node('span', option.name));
      if (option.active === 0) row.append(node('small', '已停用'));
      if (type === 'payment' && ['現金', '未指定'].includes(option.name)) row.append(node('small', '保留選項'));
      else for (const [operation, label] of [['rename', '更改'], ['disable', '停用']]) {
        if (option.active === 0 && (type === 'category' || operation === 'disable')) continue;
        const button = node('button', label); button.type = 'button'; button.dataset.operation = operation;
        button.setAttribute('aria-label', label + (type === 'category' ? '分類：' : '付款方式：') + option.name);
        button.onclick = () => {
          if (busy) return;
          setting = { type, operation, key:type === 'category' ? option.name : option.id,
            expected:structuredClone(type === 'category' ? state.categories : state.payment_sources) };
          document.querySelector('#settings-title').textContent = label + (type === 'category' ? '分類' : '付款方式');
          document.querySelector('#settings-summary').textContent = option.name + '。' + (operation === 'disable'
            ? '停用後不供新帳目選取，既有帳目與歷史仍保留。'
            : type === 'category' ? '更改將同步既有帳目、所有預算、定期規則、捷徑及操作快照。'
              : '更改後沿用原付款方式，舊帳目的付款名稱快照仍保留。');
          document.querySelector('#setting-name-label').hidden = operation === 'disable';
          document.querySelector('#setting-name').value = option.name;
          document.querySelector('#setting-error').textContent = '';
          document.querySelector('#confirm-setting').textContent = '確認' + label;
          settingsDialog.showModal();
        };
        row.append(button);
      }
      return row;
    });
    document.querySelector('#' + type + '-list').replaceChildren(...rows);
  }
}

function renderBudgets() {
  const overview = monthBudget(state, taiwanToday());
  document.querySelector('#budget-month').textContent = overview.month.replace('-', ' 年 ') + ' 月（截至台灣今天）';
  document.querySelector('#budget-spent').textContent = '已記錄消費：' + amountText(overview.spent) + ' 元';
  const display = (row, index) => {
    const block = node(row.category === '總額' ? 'div' : 'li');
    const title = node('h3', row.category === '總額' ? '總預算：' + amountText(row.cents) + ' 元' : row.category + (row.active ? '' : '（已停用）'));
    title.id = 'budget-label-' + index;
    const remaining = row.remaining;
    const text = node('p', (row.category === '總額' ? '' : '預算 ' + amountText(row.cents) + ' 元；') +
      '已花 ' + amountText(row.spent) + ' 元；' + (remaining < 0n ? '超支 ' : '剩餘 ') + amountText(remaining < 0n ? -remaining : remaining) + ' 元');
    text.id = 'budget-status-' + index;
    block.append(title, text);
    if (row.cents > 0n) {
      const progress = node('progress');
      const ratio = row.spent * 10000n / row.cents;
      progress.max = 100; progress.value = Number(ratio > 10000n ? 10000n : ratio) / 100;
      progress.setAttribute('aria-labelledby', title.id); progress.setAttribute('aria-describedby', text.id);
      block.append(progress);
    } else block.append(node('small', '零元歷史預算不計算使用率。'));
    return block;
  };
  document.querySelector('#budget-total').replaceChildren(overview.total ? display(overview.total, 'total') : node('p', '尚未設定總預算'));
  document.querySelector('#category-budget-list').replaceChildren(...(overview.categories.length
    ? overview.categories.map((row, index) => display(row, index)) : [node('li', '尚未設定分類預算')]));

  const expected = structuredClone(state.budgets);
  const options = categoryOptions(state);
  const names = ['總額', ...options.filter(row => row.active === 1 || overview.categories.some(budget => budget.category === row.name)).map(row => row.name)];
  for (const row of overview.categories) if (!names.includes(row.category)) names.push(row.category);
  const rows = names.map(category => {
    const row = node('li'); row.dataset.category = category;
    const budget = category === '總額' ? overview.total : overview.categories.find(row => row.category === category);
    const active = category === '總額' || options.some(row => row.name === category && row.active === 1);
    const name = category === '總額' ? '總預算' : category + '預算';
    row.append(node('h3', name + (active ? '' : '（已停用）')), node('p', budget ? amountText(budget.cents) + ' 元' : '尚未設定'));
    if (budget && budget.cents % 100n) row.append(node('small', '歷史小數預算保留精確原值，僅可清除。'));
    else if (active) {
      const form = node('form'); form.noValidate = true;
      const label = node('label', name + '金額');
      const input = node('input'); input.name = 'amount'; input.type = 'text'; input.inputMode = 'numeric'; input.autocomplete = 'off';
      input.value = budget ? amountText(budget.cents, false) : '';
      label.append(input);
      const button = node('button', (budget ? '更改' : '設定') + name); button.type = 'submit';
      form.append(label, button);
      form.onsubmit = event => {
        event.preventDefault();
        saving(() => updateBudget(db, expected, 'set', category, input.value), () => {}, document.querySelector('#budget-error'));
      };
      row.append(form);
    }
    if (budget) {
      const clear = node('button', '清除' + name); clear.type = 'button'; clear.dataset.operation = 'clear';
      clear.onclick = () => saving(() => updateBudget(db, expected, 'clear', category), () => {}, document.querySelector('#budget-error'));
      row.append(clear);
    }
    return row;
  });
  document.querySelector('#budget-setting-list').replaceChildren(...rows);
  const sum = document.querySelector('#budget-sum');
  sum.disabled = !overview.categories.length || overview.categories.reduce((sum, row) => sum + row.cents, 0n) <= 0n;
  sum.onclick = () => saving(() => updateBudget(db, expected, 'sum', '總額'), () => {}, document.querySelector('#budget-error'));
}
function refreshChoices(change = null) {
  let category = form.elements.category.value;
  const payment = form.elements.payment_source_id.value;
  if (change?.type === 'category' && change.operation === 'rename') {
    const name = categoryName(document.querySelector('#setting-name').value);
    if (category === change.key) category = name;
    const committed = state.expenses.find(row => row.id === editing?.id);
    if (editing?.category === change.key && committed?.category === name &&
        integerValue(committed.revision) === integerValue(editing.revision) + 1n) {
      editing.category = name; editing.revision = storedInteger(integerValue(editing.revision) + 1n);
    }
  }
  choices();
  if ([...form.elements.category.options].some(row => row.value === category)) form.elements.category.value = category;
  if ([...form.elements.payment_source_id.options].some(row => row.value === payment)) form.elements.payment_source_id.value = payment;
}
for (const type of ['category', 'payment']) {
  const addForm = document.querySelector('#' + type + '-add');
  addForm.addEventListener('submit', event => {
    event.preventDefault();
    const name = addForm.elements.name.value;
    const update = type === 'category' ? updateCategory : updatePayment;
    const expected = type === 'category' ? state.categories : state.payment_sources;
    saving(() => update(db, expected, 'add', null, name), () => { addForm.reset(); refreshChoices(); }, document.querySelector('#settings-error'));
  });
}
document.querySelector('#settings-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!setting || busy) return;
  const change = setting, value = document.querySelector('#setting-name').value;
  const update = change.type === 'category' ? updateCategory : updatePayment;
  saving(() => update(db, change.expected, change.operation, change.key, value),
    () => { refreshChoices(change); settingsDialog.close(); }, document.querySelector('#setting-error'));
});
document.querySelector('#cancel-setting').onclick = () => { if (!busy) settingsDialog.close(); };
settingsDialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
settingsDialog.addEventListener('close', () => { setting = null; });
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
  viewedMonth = taiwanToday().slice(0, 7);
  showCalendar(state, taiwanToday());
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
