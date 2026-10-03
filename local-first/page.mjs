import { openDatabase, readLedgerIfPresent, readLedger, initializeLedger, restorePortableBackup, exportPortableBackup, addExpense, editExpense, voidExpense, updateCategory, updatePayment, updateBudget, updateRecurring, taiwanToday, LedgerError } from './ledger.mjs';
import { readBackup, validateStorageLimits, MAX_BYTES, SECTIONS, integerValue, storedInteger } from './backup.mjs';
import {encryptBackup,decryptBackup,isEncryptedBackup,inspectEncryptedBackup,confirmBackupPassword,MAX_ENCRYPTED_BYTES,EncryptedBackupError} from './backup-crypto.mjs';
import { categoryOptions, categoryName, cents, fixedNextMonth, fixedSettings, fixedView, recurringProgress, ValidationError } from './rules.mjs';
import { activeExpenses, validateSearch, searchExpenses, calendarMonth, monthBudget } from './browse.mjs';
import { validateCsvRange, expenseCsv } from './csv.mjs';
import {comparisonDefaults,comparisonCategories,validateComparison,compareExpenses,comparisonDay,canPlotComparison} from './comparison.mjs';

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
let db, state, editing = null, deleting = null, busy = false, writeBlocked = false;
let editingOrigin = null, deletingOrigin = null;
let pendingBackup = null, selection = 0;
let pendingEncrypted = null, exportSelection = 0;
const decryptForm=document.querySelector('#decrypt-form'),decryptPanel=document.querySelector('#decrypt-panel');
const restorePassword=document.querySelector('#restore-password'),passwordError=document.querySelector('#restore-password-error');
const exportForm=document.querySelector('#export-form'),exportPanel=document.querySelector('#export-panel');
const exportMode=document.querySelector('#export-mode'),exportError=document.querySelector('#export-error');
const csvForm = document.querySelector('#csv-form');
const csvError = document.querySelector('#csv-error'), csvStatus = document.querySelector('#csv-status');
const csvToday = taiwanToday();
csvForm.elements.start.value = csvToday.slice(0, 7) + '-01';
csvForm.elements.end.value = csvToday;
let searchRows = [], searchShown = 0, lastQuery = null, calendarData = null, selectedDay = null;
let calendarShown = 0;
let viewedMonth = null;
const settingsDialog = document.querySelector('#settings-dialog');
let setting = null;
const fixedForm = document.querySelector('#fixed-form');
const fixedStopDialog = document.querySelector('#fixed-stop-dialog');
let fixedEditing = null, fixedStopping = null;
const storageStatus = document.querySelector('#storage-status');
const persistenceButton = document.querySelector('#request-persistence');
let persistenceRequested = false;
async function storageState() {
  try {
    if (typeof navigator.storage?.persisted !== 'function') {
      storageStatus.textContent = '本來源瀏覽器不支援持久保存狀態查詢。';
      return;
    }
    const granted = await navigator.storage.persisted();
    storageStatus.textContent = granted ? '本來源已獲准持久保存。' : '本來源未獲准持久保存。';
    if (!granted && typeof navigator.storage.persist !== 'function') storageStatus.textContent += '此瀏覽器不支援申請。';
    persistenceButton.disabled = granted || typeof navigator.storage.persist !== 'function';
  } catch {
    storageStatus.textContent = '本來源儲存狀態查詢失敗；無法確認是否獲准持久保存。';
  }
}
persistenceButton.onclick = async () => {
  if (persistenceRequested || persistenceButton.disabled) return;
  persistenceRequested = true; persistenceButton.disabled = true;
  storageStatus.textContent = '正在申請本來源持久保存……';
  try {
    storageStatus.textContent = await navigator.storage.persist()
      ? '本來源已獲准持久保存。' : '本來源未獲准持久保存；本次不再重複申請。';
  } catch {
    storageStatus.textContent = '本次申請結果無法確認；請保留手動備份，本次不再重複申請。';
  }
};
storageState(); // Query only; requesting permission requires the explicit button.

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
const comparisonForm=document.querySelector('#comparison-form');
for(const [key,value] of Object.entries(comparisonDefaults(taiwanToday())))comparisonForm.elements[key].value=value;
let comparisonCharts=[];
function comparisonChoices() {
  const selected=comparisonForm.elements.category.value;
  const all=node('option','全部分類');all.value='';
  comparisonForm.elements.category.replaceChildren(all,...comparisonCategories(state,taiwanToday()).map(row=>{
    const option=node('option',row.name+(row.state==='inactive'?'（停用）':row.state==='historical'?'（歷史）':''));option.value=row.name;return option;
  }));
  comparisonForm.elements.category.value=selected;
}
function comparisonTable(headers,rows) {
  const wrap=node('div',undefined,'comparison-table'),table=node('table'),head=node('thead'),tr=node('tr'),body=node('tbody');
  for(const title of headers){const cell=node('th',title);cell.scope='col';tr.append(cell);}head.append(tr);table.append(head,body);wrap.append(table);
  for(const values of rows){const row=node('tr');for(const text of values)row.append(node('td',text));body.append(row);}
  return wrap;
}
const comparisonMoney=value=>value===null?'—':amountText(value)+' 元';
const comparisonDifference=value=>value===null?'無法比較':(value>0n?'+':value<0n?'−':'')+amountText(value<0n?-value:value)+' 元';
function clearComparison() {
  for(const chart of comparisonCharts)chart.destroy();comparisonCharts=[];
  document.querySelector('#comparison-results').replaceChildren();
}
async function showComparison(result) {
  const target=document.querySelector('#comparison-results');
  for(const [key,label] of [['a','期間 A'],['b','期間 B']]){
    const period=result[key];target.append(node('h3',label),node('p',`所選範圍：${period.start} ～ ${period.end}`));
    target.append(node('p',period.started?`實際統計：${period.actual_start} ～ ${period.actual_end}；${comparisonMoney(period.total_cents)}，${period.record_count} 筆`:'期間尚未開始'));
  }
  target.append(node('p','差額 A − B：'+comparisonDifference(result.difference_cents)));
  const percent=result.percent;
  target.append(node('p',result.difference_cents===null?'百分比：無法比較（期間尚未開始）':percent===null?'百分比：B 為 0，無法計算百分比。':`以 B 為基準：${result.difference_cents>0n?'增加':result.difference_cents<0n?'減少':'相同'} ${percent.replace(/^−/,'')}%`));
  if(result.periods_differ)target.append(node('p','期間不等長或尚未結束，不可直接解讀為花費速度變化。'));
  target.append(node('h3','分類比較'),comparisonTable(['分類','A 金額／占比','B 金額／占比','A − B'],result.categories.map(row=>[row.name,...['a','b'].map(key=>comparisonMoney(row[key+'_cents'])+(row[key+'_percent']===null?'':`／${row[key+'_percent']}%`)),comparisonDifference(row.difference_cents)])));
  if(!result.categories.length)target.append(node('p','已開始的期間沒有符合條件的消費。'));
  const detail=node('details'),summary=node('summary','每日詳細數據（按各期間第幾天對齊）');detail.append(summary);
  const dayCount=Math.max(result.a.actual_days,result.b.actual_days);
  const dailyRow=index=>{const a=comparisonDay(result.a,index),b=comparisonDay(result.b,index);return [String(index+1),a?a.date:'—',a?comparisonMoney(a.cents):'—',b?b.date:'—',b?comparisonMoney(b.cents):'—'];};
  const dailyTable=comparisonTable(['第幾天','A 日期','A 支出','B 日期','B 支出'],[]),more=node('button','顯示更多日期');more.type='button';let shown=0;
  const appendDays=()=>{const body=dailyTable.querySelector('tbody'),end=Math.min(shown+500,dayCount);for(let i=shown;i<end;i++){const tr=node('tr');for(const value of dailyRow(i))tr.append(node('td',value));body.append(tr);}shown=end;more.hidden=shown>=dayCount;};
  more.onclick=appendDays;appendDays();detail.append(dailyTable,more);target.append(detail);
  const chartStatus=node('p','圖表載入中；完整數據可由文字與表格閱讀。');chartStatus.id='comparison-chart-status';target.append(chartStatus);
  if(![result.a,result.b].some(period=>period.total_cents>0n)){chartStatus.textContent='沒有已發生的正額支出可繪圖；未開始期間沒有統計資料。';return;}
  // ponytail: large series use exact paged text; add chart downsampling only if requested.
  if(!canPlotComparison(result)||dayCount>10000){chartStatus.textContent='資料超出安全繪圖範圍，請閱讀精確文字與表格。';return;}
  const wrappers=[];
  const canvas=(title,parent=target)=>{parent.append(node('h3',title));const wrap=node('div',undefined,'comparison-chart');wrap.hidden=true;const element=node('canvas');element.setAttribute('role','img');element.setAttribute('aria-label',title+'；完整數據見表格');wrap.append(element);parent.append(wrap);wrappers.push(wrap);return element;};
  const dailyCanvas=canvas('每日支出（非累積，按第幾天對齊）'),barCanvas=canvas('分類支出 A／B');
  const pies=node('div',undefined,'comparison-pies');target.append(pies);
  const pieCanvases=['a','b'].map(key=>{const panel=node('div');pies.append(panel);return result[key].total_cents>0n?canvas(`期間 ${key.toUpperCase()} 分類占比`,panel):(panel.append(node('h3',`期間 ${key.toUpperCase()} 分類占比`),node('p',result[key].started?'沒有正額支出可計算占比。':'期間尚未開始')),null);});
  try{
    await import('./vendor/chartjs-4.5.1/chart.umd.min.js');
    if(typeof globalThis.Chart!=='function')throw new Error('Chart unavailable');
    const keys=['a','b'],colors=['#26745b','#946437'];
    const options={responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:true}}};
    const tick=value=>Number.isSafeInteger(value)?amountText(BigInt(value))+' 元':'';
    const make=(element,config)=>{const chart=new globalThis.Chart(element,config);comparisonCharts.push(chart);};
    make(dailyCanvas,{type:'line',data:{labels:Array.from({length:dayCount},(_,i)=>String(i+1)),datasets:keys.filter(key=>result[key].started).map(key=>({label:'期間 '+key.toUpperCase(),data:Array.from({length:result[key].actual_days},(_,i)=>Number(comparisonDay(result[key],i).cents)),borderColor:colors[key==='a'?0:1],borderDash:key==='b'?[6,3]:[],pointRadius:2,tension:0,fill:false}))},options:{...options,scales:{y:{beginAtZero:true,ticks:{callback:tick}},x:{title:{display:true,text:'期間第幾天'}}},plugins:{...options.plugins,tooltip:{callbacks:{label:context=>{const key=keys.filter(key=>result[key].started)[context.datasetIndex],day=comparisonDay(result[key],context.dataIndex);return `${context.dataset.label} ${day.date}：${comparisonMoney(day.cents)}`;}}}}}});
    make(barCanvas,{type:'bar',data:{labels:result.categories.map(row=>row.name),datasets:keys.filter(key=>result[key].started).map(key=>({label:'期間 '+key.toUpperCase(),data:result.categories.map(row=>Number(row[key+'_cents'])),backgroundColor:colors[key==='a'?0:1]}))},options:{...options,scales:{y:{beginAtZero:true,ticks:{callback:tick}}},plugins:{...options.plugins,tooltip:{callbacks:{label:context=>{const key=keys.filter(key=>result[key].started)[context.datasetIndex];return context.dataset.label+'：'+comparisonMoney(result.categories[context.dataIndex][key+'_cents']);}}}}}});
    for(const [index,key] of keys.entries())if(pieCanvases[index]){
      const rows=result.categories.map((row,i)=>({...row,color:`hsl(${(i*137.5)%360} 50% 45%)`})).filter(row=>row[key+'_cents']>0n);
      make(pieCanvases[index],{type:'pie',data:{labels:rows.map(row=>row.name),datasets:[{data:rows.map(row=>Number(row[key+'_cents'])),backgroundColor:rows.map(row=>row.color)}]},options:{...options,plugins:{...options.plugins,tooltip:{callbacks:{label:context=>{const row=rows[context.dataIndex];return row.name+'：'+comparisonMoney(row[key+'_cents'])+`（${row[key+'_percent']}%）`;}}}}}});
    }
    for(const wrap of wrappers)wrap.hidden=false;
    for(const chart of comparisonCharts)chart.resize();chartStatus.textContent='圖表只顯示彙總；精確金額與占比見文字及表格。';
  }catch{
    for(const chart of comparisonCharts)chart.destroy();comparisonCharts=[];
    for(const wrap of wrappers)wrap.remove();chartStatus.textContent='圖表無法載入，請閱讀完整文字與表格。';
  }
}
comparisonForm.addEventListener('submit',async event=>{
  event.preventDefault();const button=comparisonForm.querySelector('button');if(button.disabled)return;
  const query=Object.fromEntries(new FormData(comparisonForm)),today=taiwanToday(),message=document.querySelector('#comparison-error');
  message.textContent='';clearComparison();button.disabled=true;
  try{
    if(['a_start','a_end','b_start','b_end'].some(key=>!comparisonForm.elements[key].validity.valid))throw new ValidationError('請輸入有效日期。');
    validateComparison(query,today);
    const snapshot=await readLedger(db); // One readonly transaction; never sync recurring rules.
    await showComparison(compareExpenses(snapshot,query,today));
  }catch(failure){clearComparison();message.textContent=failure instanceof ValidationError?failure.message:'無法比較支出，請重新載入後再試。';}
  finally{button.disabled=false;}
});
function choices() {
  const categories = categoryOptions(state).filter(option => option.active === 1 || option.name === editing?.category);
  if (editing && !categories.some(option => option.name === editing.category)) categories.push({name:editing.category, active:0});
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
  const origin = editingOrigin;
  editing = null;
  editingOrigin = null;
  choices();
  form.elements.amount.value = '';
  form.elements.note.value = '';
  form.elements.spent_on.value = taiwanToday();
  form.elements.spent_on.readOnly = false;
  document.querySelector('#expense-date-note').hidden = true;
  document.querySelector('#form-title').textContent = '記錄消費';
  document.querySelector('#save').textContent = '儲存消費';
  document.querySelector('#cancel-edit').hidden = true;
  return origin;
}
function returnToExpense(origin) {
  if (!origin) return;
  const container = document.getElementById(origin.listId);
  const row = [...container.children].find(row => row.dataset.expenseId === origin.id);
  const target = row?.querySelector('button') || container;
  if (target === container) target.tabIndex = -1;
  target.focus();
}
function render() {
  comparisonChoices();
  renderSettings();
  renderBudgets();
  renderFixed();
  list.replaceChildren();
  const entries = activeExpenses(state, taiwanToday()).slice().reverse();
  document.querySelector('#empty').hidden = entries.length !== 0;
  list.append(...entries.map(expenseRow));
}
function expenseRow(entry) {
    const row = node('li', undefined, 'expense');
    row.dataset.expenseId = entry.id;
    for (const [label, value] of [['日期', entry.spent_on], ['項目', entry.note], ['金額', amountText(entry.cents) + ' 元'], ['分類', entry.category], ['付款方式', entry.payment_source_name]]) {
      const cell = node('div'); cell.append(node('small', label), node('span', value)); row.append(cell);
    }
    const buttons = node('div', undefined, 'buttons');
    const edit = node('button', '更改'); edit.type = 'button';
    edit.onclick = () => {
      if (busy || writeBlocked) return;
      editingOrigin = {listId:row.parentElement.id, id:entry.id};
      editing = structuredClone(entry); choices(); error.textContent = '';
      form.elements.amount.value = amountText(entry.cents, false);
      form.elements.note.value = entry.note;
      form.elements.spent_on.value = entry.spent_on;
      form.elements.spent_on.readOnly = entry.source !== 'manual';
      document.querySelector('#expense-date-note').hidden = entry.source === 'manual';
      form.elements.category.value = entry.category;
      form.elements.payment_source_id.value = '';
      document.querySelector('#form-title').textContent = '更改消費';
      document.querySelector('#save').textContent = '儲存更改';
      document.querySelector('#cancel-edit').hidden = false;
      form.elements.amount.focus();
    };
    const remove = node('button', '刪除'); remove.type = 'button';
    remove.onclick = () => {
      if (busy || writeBlocked) return;
      deletingOrigin = {listId:row.parentElement.id, id:entry.id};
      deleting = structuredClone(entry);
      document.querySelector('#delete-error').textContent = '';
      document.querySelector('#delete-summary').textContent = `${entry.spent_on}｜${entry.note}｜${amountText(entry.cents)} 元｜${entry.category}｜${entry.payment_source_name}`;
      dialog.showModal();
    };
    buttons.append(edit, remove);
    row.append(buttons); return row;
}
function appendBatch(rows, listElement, more, shown) {
  listElement.append(...rows.slice(shown, shown + 50).map(expenseRow));
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
  const retainedDay = selectedDay;
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
  const retained = calendarData.days.find(day => day.date === retainedDay && !day.isFuture);
  if (retained) showDay(retained);
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
  if (busy || writeBlocked) return;
  let committed = false;
  const budgetControl = document.activeElement.closest('#budget-fields') ? document.activeElement : null;
  const fixedControl = document.activeElement.closest('#fixed-fields') ? document.activeElement : null;
  const budgetRow = budgetControl?.closest('#budget-setting-list > li');
  busy = true; fields.disabled = true; error.textContent = '';
  message.textContent = '';
  document.querySelector('#settings-fields').disabled = true;
  document.querySelector('#budget-fields').disabled = true;
  document.querySelector('#setting-fields').disabled = true;
  fixedBusy(true);
  document.querySelector('#delete-error').textContent = '';
  document.querySelector('#confirm-delete').disabled = true;
  document.querySelector('#cancel-delete').disabled = true;
  status.textContent = '正在儲存瀏覽器本機測試帳本……';
  try {
    state = await operation(); // Resolved only by transaction complete, never request success.
    committed = true;
    const origin = success(); render();
    if (lastQuery) showSearch(state, lastQuery, taiwanToday());
    if (viewedMonth) showCalendar(state, taiwanToday());
    returnToExpense(origin);
    status.textContent = '瀏覽器本機測試帳本；儲存完成。';
  } catch (failure) {
    writeBlocked = committed;
    if (committed) for (const modal of [dialog, settingsDialog, fixedStopDialog]) if (modal.open) modal.close();
    message.textContent = committed ? '資料已儲存，但畫面更新未完成。請重新載入確認，勿重複提交。'
      : failure instanceof LedgerError || failure instanceof ValidationError ? failure.message : '儲存未完成，請確認瀏覽器儲存空間後重試。';
    if (dialog.open) document.querySelector('#delete-error').textContent = error.textContent;
    status.textContent = committed ? message.textContent : '瀏覽器本機測試帳本；此次儲存未完成。';
  } finally {
    busy = false; fields.disabled = writeBlocked;
    document.querySelector('#settings-fields').disabled = writeBlocked;
    document.querySelector('#budget-fields').disabled = writeBlocked;
    document.querySelector('#setting-fields').disabled = writeBlocked;
    fixedBusy(writeBlocked);
    fixedControl?.focus();
    document.querySelector('#confirm-delete').disabled = writeBlocked;
    document.querySelector('#cancel-delete').disabled = false;
    if (budgetControl) {
      const row = [...document.querySelectorAll('#budget-setting-list > li')].find(row => row.dataset.category === budgetRow?.dataset.category);
      const selector = budgetControl.tagName === 'INPUT' ? 'input' : budgetControl.dataset.operation === 'clear' ? '[data-operation="clear"]' : 'button[type="submit"]';
      const control = budgetControl.isConnected ? budgetControl : row?.querySelector(selector);
      (control || document.querySelector('#budget-settings summary')).focus();
    }
  }
}
function fixedBusy(disabled) {
  document.querySelector('#fixed-fields').disabled = disabled;
  for (const button of document.querySelectorAll('#fixed-list button, #fixed-sync, #confirm-fixed-stop, #cancel-fixed-stop')) button.disabled = disabled;
}
function fixedChoices() {
  const selected = fixedForm.elements.category.value;
  const retained = fixedEditing ? fixedSettings(state, fixedEditing, fixedNextMonth(taiwanToday().slice(0, 7))).category : null;
  const options = categoryOptions(state).filter(row => row.active === 1 || row.name === retained);
  if (retained && !options.some(row => row.name === retained)) options.push({name:retained, active:0});
  fixedForm.elements.category.replaceChildren(...options.map(row => {
    const option = node('option', row.name + (row.active === 0 ? '（停用，保留原值）' : '')); option.value = row.name; return option;
  }));
  if (options.some(row => row.name === selected)) fixedForm.elements.category.value = selected;
  const month = taiwanToday().slice(0, 7), previous = fixedForm.elements.start_month.value;
  const months = fixedEditing ? [fixedEditing.start_month] : [month, fixedNextMonth(month)];
  fixedForm.elements.start_month.replaceChildren(...months.map(value => {const option = node('option', value); option.value = value; return option;}));
  if (months.includes(previous)) fixedForm.elements.start_month.value = previous;
  fixedForm.elements.start_month.disabled = fixedEditing !== null;
}
function resetFixed() {
  fixedEditing = null; fixedForm.reset(); fixedChoices();
  recurringFields();
  document.querySelector('#cancel-fixed-edit').hidden = true;
}
const recurringLabel = kind => kind === '固定' ? '固定支出' : kind;
function recurringFields() {
  const installment = !fixedEditing && fixedForm.elements.kind.value === '分期';
  for (const key of ['name', 'due_day']) fixedForm.elements[key].disabled = fixedEditing !== null && fixedEditing.kind !== '固定';
  fixedForm.elements.kind.disabled = fixedEditing !== null;
  fixedForm.elements.periods.disabled = !installment;
  document.querySelector('#installment-fields').hidden = !installment;
  if (!fixedEditing) for (const id of ['fixed-form-title', 'save-fixed']) {
    document.querySelector('#'+id).textContent = '新增'+recurringLabel(fixedForm.elements.kind.value);
  }
}
fixedForm.elements.kind.onchange = recurringFields;
function fixedText(row) {
  return `${row.name}｜${amountText(row.cents)} 元｜${row.category}｜每月 ${row.due_day ?? 1} 日（短月取月底）`;
}
function renderFixed() {
  fixedChoices(); recurringFields();
  const rows = state.recurring_rules;
  document.querySelector('#fixed-empty').hidden = rows.length !== 0;
  const listElement = document.querySelector('#fixed-list'); listElement.replaceChildren();
  const month = taiwanToday().slice(0, 7);
  const postedCounts = new Map();
  for (const entry of state.expenses) if (entry.recurring_id !== null) {
    postedCounts.set(entry.recurring_id, (postedCounts.get(entry.recurring_id) ?? 0n) + 1n);
  }
  for (const rule of rows.slice().reverse()) {
    const view = fixedView(state, rule, month), row = node('li'); row.dataset.id = rule.id;
    const progress = recurringProgress(state, rule, postedCounts.get(rule.id) ?? 0n);
    row.append(node('h3', recurringLabel(rule.kind)+'：'+view.name + (progress.complete ? '（已完成）' : rule.active === 0 ? '（已停用）' : '')), node('p', '目前設定：' + fixedText(view)),
      node('p', `開始月份：${rule.start_month}${rule.start_month > month ? '（尚未開始）' : ''}`));
    if (rule.kind === '分期') row.append(node('p', `已入帳 ${progress.posted}／${progress.total} 期（含已撤銷期數）`));
    if (view.pending) row.append(node('p', `${view.pending.effective_month === fixedNextMonth(month) ? '下月生效設定' : '未來生效設定'}（${view.pending.effective_month}）：${fixedText(view.pending)}${progress.complete ? '；已完成，不會執行' : rule.active === 0 ? '；已停用，不會執行' : ''}`));
    if (rule.active === 1 && !progress.complete) {
      const buttons = node('div', undefined, 'buttons');
      const edit = node('button', '更改'+recurringLabel(rule.kind)); edit.type = 'button'; edit.dataset.operation = 'update';
      edit.onclick = () => {
        if (busy) return;
        fixedEditing = structuredClone(rule); fixedChoices(); fixedForm.elements.kind.value = rule.kind; recurringFields();
        const target = fixedSettings(state, rule, fixedNextMonth(taiwanToday().slice(0, 7)));
        for (const key of ['name', 'category']) fixedForm.elements[key].value = target[key];
        fixedForm.elements.amount.value = amountText(target.cents, false);
        fixedForm.elements.due_day.value = target.due_day ?? 1;
        document.querySelector('#fixed-form-title').textContent = '更改'+recurringLabel(rule.kind)+'（下月生效）';
        document.querySelector('#save-fixed').textContent = '儲存'+recurringLabel(rule.kind)+'更改';
        document.querySelector('#cancel-fixed-edit').textContent = '取消更改'+recurringLabel(rule.kind);
        document.querySelector('#cancel-fixed-edit').hidden = false;
        document.querySelector('#fixed-error').textContent = ''; fixedForm.elements[rule.kind === '固定' ? 'name' : 'amount'].focus();
      };
      const stop = node('button', '停用'+recurringLabel(rule.kind)); stop.type = 'button'; stop.dataset.operation = 'stop';
      stop.onclick = () => {
        if (busy) return;
        fixedStopping = structuredClone(rule);
        for (const id of ['fixed-stop-title', 'confirm-fixed-stop']) document.querySelector('#'+id).textContent = '確認停用'+recurringLabel(rule.kind);
        document.querySelector('#fixed-stop-summary').textContent = recurringLabel(rule.kind)+'目前設定：' + fixedText(view) + (rule.kind === '分期' ? `；已入帳 ${progress.posted}／${progress.total} 期` : '') + (view.pending ? '；未來設定：' + fixedText(view.pending) : '');
        document.querySelector('#fixed-stop-error').textContent = ''; fixedStopDialog.showModal();
      };
      buttons.append(edit);
      buttons.append(stop); row.append(buttons);
    }
    listElement.append(row);
  }
}
fixedForm.addEventListener('submit', event => {
  event.preventDefault(); if (busy) return;
  const input = Object.fromEntries(new FormData(fixedForm));
  if (!fixedEditing || fixedEditing.kind === '固定') input.due_day = /^\d{1,2}$/.test(input.due_day) ? Number(input.due_day) : NaN;
  if (!fixedEditing && input.kind === '分期') input.periods = /^\d{1,3}$/.test(input.periods) ? Number(input.periods) : NaN;
  saving(() => updateRecurring(db, fixedEditing ? 'update' : 'add', fixedEditing?.id, fixedEditing?.revision, input),
    () => {resetFixed(); fixedForm.elements.name.focus();}, document.querySelector('#fixed-error'));
});
document.querySelector('#cancel-fixed-edit').onclick = () => {if (!busy) {resetFixed(); document.querySelector('#fixed-error').textContent = ''; fixedForm.elements.name.focus();}};
document.querySelector('#fixed-sync').onclick = () => saving(() => updateRecurring(db, 'sync'), () => {}, document.querySelector('#fixed-error'));
document.querySelector('#confirm-fixed-stop').onclick = () => saving(
  () => updateRecurring(db, 'stop', fixedStopping.id, fixedStopping.revision),
  () => {if (fixedEditing?.id === fixedStopping.id) resetFixed(); fixedStopDialog.close(); document.querySelector('#fixed-settings summary').focus();},
  document.querySelector('#fixed-stop-error')
);
document.querySelector('#cancel-fixed-stop').onclick = () => {if (!busy) fixedStopDialog.close();};
fixedStopDialog.addEventListener('cancel', event => {if (busy) event.preventDefault();});
fixedStopDialog.addEventListener('close', () => {fixedStopping = null;});

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
document.querySelector('#cancel-edit').onclick = () => { if (!busy) { const origin = resetForm(); error.textContent = ''; returnToExpense(origin); } };
document.querySelector('#cancel-delete').onclick = () => dialog.close();
dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
dialog.addEventListener('close', () => { deleting = null; deletingOrigin = null; });
document.querySelector('#confirm-delete').onclick = () => saving(
  () => voidExpense(db, deleting.id, deleting.revision),
  () => { const origin = deletingOrigin; if (editing?.id === deleting.id) resetForm(); dialog.close(); return origin; }
);
function showLedger() {
  ++selection;pendingEncrypted=null;pendingBackup=null;decryptForm.reset();decryptPanel.hidden=true;
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
  for (const id of ['start-ledger', 'choose-restore', 'confirm-restore', 'cancel-restore', 'decrypt-backup']) {
    document.getElementById(id).disabled = disabled;
  }
  restorePassword.disabled=disabled;
}
document.querySelector('#start-ledger').onclick = async () => {
  if (busy || writeBlocked) return;
  restoreControls(true);
  try { await saving(() => initializeLedger(db), showLedger); }
  finally { restoreControls(false); }
};
document.querySelector('#choose-restore').onclick = () => { if (!busy) restoreFile.click(); };
function prepareRestore(payload){
  const backup=readBackup(payload);validateStorageLimits(backup);
  const labels={expenses:'消費',categories:'分類',payment_sources:'付款方式',budgets:'預算',recurring_rules:'定期規則',recurring_versions:'歷史版本',shortcuts:'捷徑',actions:'操作紀錄',settings:'設定'};
  document.querySelector('#restore-summary').replaceChildren(...SECTIONS.map(section=>node('li',`${labels[section]}：${section==='settings'?1:backup.data[section].length} 項`)));
  pendingBackup=payload;pendingEncrypted=null;decryptForm.reset();decryptPanel.hidden=true;review.hidden=false;
  document.querySelector('#restore-title').focus();
}
restoreFile.onchange = async () => {
  if(busy)return;
  const file = restoreFile.files[0];
  const currentSelection = ++selection;
  restoreFile.value = '';
  pendingBackup = null;
  pendingEncrypted=null;decryptForm.reset();decryptPanel.hidden=true;passwordError.textContent='';
  review.hidden = true;
  error.textContent = '';
  if (!file || busy) return;
  if (file.size > MAX_ENCRYPTED_BYTES) { error.textContent = '備份檔超過容量上限（JSON 64 MiB；加密檔另含 63 bytes 封裝）。'; return; }
  busy=true;restoreControls(true);
  try {
    const payload = new Uint8Array(await file.arrayBuffer());
    if (currentSelection !== selection) return;
    if(isEncryptedBackup(payload)){
      inspectEncryptedBackup(payload);pendingEncrypted=payload;decryptPanel.hidden=false;
    }else{
      if(payload.length>MAX_BYTES){error.textContent='JSON 備份檔超過 64 MiB 上限。';return;}
      prepareRestore(payload);
    }
  } catch {
    if (currentSelection === selection) error.textContent = '備份檔格式或資料不符合規則，或超過本機容量上限。請選擇有效的完整備份。';
  }finally{busy=false;restoreControls(false);if(!decryptPanel.hidden)restorePassword.focus();}
};
decryptForm.onsubmit=async event=>{
  event.preventDefault();if(busy||!pendingEncrypted)return;
  const currentSelection=selection;busy=true;restoreControls(true);passwordError.textContent='';
  try{
    const payload=await decryptBackup(pendingEncrypted,restorePassword.value);
    if(currentSelection!==selection)return;
    prepareRestore(payload);
  }catch(failure){if(currentSelection===selection)passwordError.textContent=failure instanceof EncryptedBackupError?failure.message:'解密或備份資料驗證未完成，請確認密碼與備份檔。';}
  finally{busy=false;restoreControls(false);if(currentSelection!==selection)document.querySelector('#choose-restore').focus();}
};
document.querySelector('#cancel-decrypt').onclick=()=>{
  ++selection;pendingEncrypted=null;pendingBackup=null;decryptForm.reset();decryptPanel.hidden=true;passwordError.textContent='';
  if(!busy)document.querySelector('#choose-restore').focus();
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
function resetExport(){
  exportForm.reset();exportError.textContent='';document.querySelector('#export-password-fields').hidden=false;
  document.querySelector('#export-plain-risk').hidden=true;document.querySelector('#confirm-export').textContent='產生加密備份';
}
csvForm.onsubmit = async event => {
  event.preventDefault();
  if (busy) return;
  csvError.textContent = ''; csvStatus.textContent = '';
  const query = Object.fromEntries(new FormData(csvForm)), today = taiwanToday();
  try {
    if (!csvForm.elements.start.validity.valid || !csvForm.elements.end.validity.valid) {
      throw new ValidationError('請輸入有效日期。');
    }
    validateCsvRange(query, today); // Reject invalid input before opening a read transaction.
  } catch (failure) {
    csvError.textContent = failure instanceof ValidationError ? failure.message : '請檢查匯出日期。';
    return;
  }
  busy = true; document.querySelector('#csv-inputs').disabled = true;
  try {
    const snapshot = await readLedger(db);
    const result = expenseCsv(snapshot, query, today);
    if (!result.count) { csvStatus.textContent = '沒有符合日期範圍的已入帳生活消費。'; return; }
    const url = URL.createObjectURL(new Blob([result.payload], {type:'text/csv;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url; link.download = 'discordbot-expenses.csv';
    document.body.append(link);
    try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60_000); }
    csvStatus.textContent = '已要求瀏覽器下載 CSV；請自行確認檔案存在並妥善保管。';
  } catch {
    csvError.textContent = '無法產生 CSV，帳本沒有變更。請重新載入後再試。';
  } finally { busy = false; document.querySelector('#csv-inputs').disabled = false; }
};
document.querySelector('#download-backup').onclick=()=>{
  if(busy)return;++exportSelection;resetExport();exportPanel.hidden=false;document.querySelector('#export-password').focus();
};
exportMode.onchange=()=>{
  document.querySelector('#export-password').value='';document.querySelector('#export-confirm-password').value='';document.querySelector('#export-risk').checked=false;
  const encrypted=exportMode.value==='encrypted';document.querySelector('#export-password-fields').hidden=!encrypted;
  document.querySelector('#export-plain-risk').hidden=encrypted;document.querySelector('#confirm-export').textContent=encrypted?'產生加密備份':'確認下載未加密備份';exportError.textContent='';
};
document.querySelector('#cancel-export').onclick=()=>{++exportSelection;resetExport();exportPanel.hidden=true;if(!busy)document.querySelector('#download-backup').focus();};
exportForm.onsubmit = async event => {
  event.preventDefault();
  if (busy) return;
  const encrypted=exportMode.value==='encrypted',password=document.querySelector('#export-password').value;
  exportError.textContent='';
  try{
    if(encrypted)confirmBackupPassword(password,document.querySelector('#export-confirm-password').value);
    else if(exportMode.value!=='plain'||!document.querySelector('#export-risk').checked)throw new EncryptedBackupError('請閱讀未加密風險，並勾選明確確認後再下載。');
  }catch(failure){exportError.textContent=failure instanceof EncryptedBackupError?failure.message:'備份設定不符合規則。';return;}
  const currentSelection=++exportSelection;busy = true;
  document.querySelector('#export-inputs').disabled=true;document.querySelector('#confirm-export').disabled=true;document.querySelector('#download-backup').disabled=true;
  try {
    let payload = await exportPortableBackup(db);
    if(currentSelection!==exportSelection)return;
    if(encrypted)payload=await encryptBackup(payload,password);
    if(currentSelection!==exportSelection)return;
    const url = URL.createObjectURL(new Blob([payload], { type: encrypted?'application/octet-stream':'application/json;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = encrypted?'life-ledger-encrypted-backup-v1.llbk':'life-ledger-backup-v1.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    resetExport();exportPanel.hidden=true;
    status.textContent = '已要求瀏覽器下載完整備份；尚未確認檔案已保存。請確認檔案存在，並妥善保管備份與密碼。';
  } catch {
    if(currentSelection===exportSelection)exportError.textContent = '無法產生完整備份，帳本沒有變更。請確認瀏覽器儲存狀態後重試。';
  } finally {
    busy = false;document.querySelector('#export-inputs').disabled=false;document.querySelector('#confirm-export').disabled=false;document.querySelector('#download-backup').disabled=false;
    if(exportPanel.hidden)document.querySelector('#download-backup').focus();
  }
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
