import { DB_NAME, openDatabase, readLedger, updateBudget, exportPortableBackup, taiwanToday } from '../local-first/ledger.mjs';
import { readBackup } from '../local-first/backup.mjs';
import { STORES, addState } from '../local-first/idb.mjs';
const check=(value,message)=>{if(!value)throw new Error(message);};
const report=document.createElement('pre');report.id='budget-test-result';document.body.prepend(report);
const open=indexedDB.open.bind(indexedDB);
indexedDB.open=(name,version)=>open(name===DB_NAME?DB_NAME+'-budgets-ui-checks':name,version);
const db=await openDatabase(),today=taiwanToday(),month=today.slice(0,7);
const initial={format:'local-first-test-ledger',version:2,owner:'local-test-owner',
  ...readBackup(new Uint8Array(await(await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer())).data};
for(const entry of initial.expenses.slice(0,4)){entry.spent_on=today;entry.voided=0;if(entry.recurring_id)entry.period=month;}
initial.expenses[0].cents='1234';initial.expenses[0].category='餐飲';
initial.budgets.push({month,category:'總額',cents:'0'},{month,category:'交通',cents:'0'},{month,category:'居住',cents:'101'},
  {month,category:initial.categories[0].name,cents:'0'});
const tx=db.transaction(STORES,'readwrite');for(const section of STORES)tx.objectStore(section).clear();addState(tx,initial);
await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=reject;});
const lines=[];
const wait=async action=>{
  const complete=new Promise((resolve,reject)=>{const observer=new MutationObserver(()=>{
    if(document.querySelector('#status').textContent.includes('正在儲存'))return;observer.disconnect();clearTimeout(timeout);resolve();});
    const timeout=setTimeout(()=>{observer.disconnect();reject(new Error('Write timeout'));},5000);observer.observe(document.body,{subtree:true,attributes:true,childList:true});});
  action();await complete;
};
const row=category=>[...document.querySelectorAll('#budget-setting-list > li')].find(item=>item.dataset.category===category);
const submit=async(category,value)=>{const form=row(category).querySelector('form');form.elements.amount.value=value;form.elements.amount.focus();await wait(()=>form.requestSubmit());check(document.activeElement===row(category).querySelector('input'),'Budget input keyboard focus lost after submit');};
const clear=category=>row(category).querySelector('[data-operation="clear"]').click();
try {
  const before=await exportPortableBackup(db);
  await import('../local-first/page.mjs');
  check(document.querySelector('#budget-overview'),'Missing budget overview');
  check(document.querySelector('#budget-settings')?.tagName==='DETAILS' && !document.querySelector('#budget-settings').open,'Native collapsed budget settings');
  check(!document.querySelector('#category-budget-overview').open,'Category budgets must start collapsed');
  check(document.querySelector('#budget-overview').textContent.includes('已記錄消費：43.21 元'),'Four-source exact expenses');
  check(document.querySelector('#budget-total').textContent.includes('總預算：0 元') && document.querySelector('#budget-total').textContent.includes('超支 43.21 元'),'Historical zero total lost');
  check(!document.querySelector('#budget-total progress'),'Zero drew percentage');
  check(document.querySelector('#category-budget-list').textContent.includes('交通（已停用）') && document.querySelector('#category-budget-list').textContent.includes('已花 0 元'),'Inactive zero-spent missing');
  check(document.querySelector('#category-budget-list').textContent.includes('1.01 元'),'Fractional history rounded');
  check(document.querySelector('#category-budget-list').textContent.includes(initial.categories[0].name) &&
    !document.querySelector('#category-budget-list img,#budget-setting-list img'),'Malicious budget category not safe text');
  check(!row('居住').querySelector('input') && !row('交通').querySelector('input'),'Clear-only budget exposes set');
  check(await exportPortableBackup(db).then(value=>value.toString())===before.toString(),'Page read wrote');
  lines.push('read-only exact four-source expenses, explicit zero/no progress, inactive zero-spent and clear-only fractional history');
  document.querySelector('#ledger-settings').open=true;document.querySelector('#budget-settings').open=true;
  await wait(()=>clear('居住'));await wait(()=>clear('總額'));
  check(document.querySelector('#budget-total').textContent.includes('尚未設定總預算'),'Missing total shown as zero');
  await wait(()=>clear('交通'));await wait(()=>clear(initial.categories[0].name));
  check(document.querySelector('#category-budget-list').textContent.includes('尚未設定分類預算'),'Missing categories shown as zero');
  await submit('餐飲','100');check(document.querySelector('#category-budget-list').textContent.includes('100 元'),'Category immediate summary');
  await submit('總額','99');check(row('總額').querySelector('input').value==='99' && document.querySelector('#budget-error').textContent.includes('不可超過'),'Lower bound failed input lost');
  await wait(()=>document.querySelector('#budget-sum').click());
  check(document.querySelector('#budget-total').textContent.includes('剩餘 56.79 元'),'Exact remaining');
  check(row('總額').querySelector('input').value==='100' && !row('總額').querySelector('input').value.includes('元'),'Budget input formatting');
  await submit('總額','100.00');check(row('總額').querySelector('input').value==='100.00' && document.querySelector('#budget-error').textContent,'Invalid integer lost');
  await submit('餐飲','1');await submit('總額','1');
  check(document.querySelector('#budget-total').textContent.includes('超支 42.21 元') && document.querySelector('#budget-total progress').value===100,'Overspent progress/text');
  lines.push('individual set/change/clear, absent labels, sum, lower bound, retained errors, exact remaining and capped progress');
  const old=(await readLedger(db)).budgets,other=await openDatabase();await updateBudget(other,old,'set','總額','200');other.close();
  await submit('總額','300');check(document.querySelector('#budget-error').textContent.includes('重新載入') && row('總額').querySelector('input').value==='300','Stale UI overwrite');
  check((await readLedger(db)).budgets.find(item=>item.month===month&&item.category==='總額').cents==='20000','Stale write changed total');
  lines.push('another connection changes selected budget; stale UI refuses overwrite and retains input');
  const put=IDBObjectStore.prototype.put,beforeFailure=await exportPortableBackup(db);
  IDBObjectStore.prototype.put=()=>{throw new DOMException('PRIVATE-DETAIL','QuotaExceededError');};
  try{await submit('餐飲','2');}finally{IDBObjectStore.prototype.put=put;}
  check(row('餐飲').querySelector('input').value==='2' && !document.querySelector('#budget-error').textContent.includes('PRIVATE-DETAIL'),'Failed write input/raw error');
  check((await exportPortableBackup(db)).toString()===beforeFailure.toString(),'Failed write changed ledger');
  check(!document.querySelector('#budget-setting-list img,#budget-setting-list script,#category-budget-list img'),'Unsafe backup DOM');
  check(performance.getEntriesByType('resource').every(item=>new URL(item.name).origin===location.origin),'External resource request');
  lines.push('storage failure retains input and hides details; malicious backup labels remain safe text');
  report.textContent='PASS：'+lines.length+' 組預算畫面驗證\n'+lines.join('\n');
}catch(error){report.textContent='FAIL：'+error.message;throw error;}finally{db.close();}
