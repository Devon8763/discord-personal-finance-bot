import { openDatabase, initializeLedger, readLedger, readLedgerIfPresent, addExpense, editExpense, voidExpense, DB_NAME } from '../local-first/ledger.mjs';
import { money, isoDate, sumCents } from '../local-first/rules.mjs';
const lines=[];
function ok(value,message){if(!value)throw new Error(message);}
async function rejects(fn,code){let e;try{await fn();}catch(error){e=error;}ok(e && (!code || e.code===code),'Expected rejection '+code);}
function txDone(tx){return new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});}
function request(q){return new Promise((resolve,reject)=>{q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error);});}
const today='2026-09-24';const form={amount:'12.34',note:'合成測試午餐',spent_on:today,category:'餐飲',payment_source_id:'p_cash'};
const db=await openDatabase(DB_NAME+'-checks');
try {
  // Only this separate synthetic test DB is reset. The page DB is never deleted.
  let tx=db.transaction('ledger','readwrite');tx.objectStore('ledger').clear();await txDone(tx);
  ok(await readLedgerIfPresent(db) === null, 'Blank check must be readonly and return no ledger');
  await rejects(()=>readLedger(db),'uninitialized');lines.push('read before explicit initialization does not write');
  let s=await initializeLedger(db);ok(s.actions.length===0 && s.expenses.length===0,'No initial operations');
  const before=JSON.stringify(s);s=await initializeLedger(db);ok(JSON.stringify(s)===before,'Initialize once');
  s=await readLedger(db);ok(JSON.stringify(s)===before,'Read is pure');lines.push('initial defaults once; repeated read unchanged');
  s=await addExpense(db,form,today);const entry=s.expenses[0];ok(entry.cents==='1234' && entry.revision===0,'Add exact');
  ok(s.actions.length===1 && s.actions[0].before===null,'Add action');lines.push('add commits record and action');
  const stale=structuredClone(entry);s=await editExpense(db,entry.id,0,{...form,amount:'1e-2'},today);
  ok(s.expenses[0].cents==='1' && s.expenses[0].revision===1 && s.actions[1].before.cents==='1234','Edit snapshot');
  await rejects(()=>editExpense(db,stale.id,stale.revision,form,today),'conflict');
  await rejects(()=>voidExpense(db,stale.id,stale.revision),'conflict');lines.push('stale revision rejects both writes');
  await rejects(()=>addExpense(db,{...form,amount:'0.001'},today));
  ok((await readLedger(db)).actions.length===2,'No validation write');lines.push('invalid input leaves ledger unchanged');
  // A real IDB transaction abort after put request success must still reject.
  const fault={transaction(...args){const transaction=db.transaction(...args);const getStore=transaction.objectStore.bind(transaction);transaction.objectStore=(...names)=>{const store=getStore(...names);const put=store.put.bind(store);store.put=(...values)=>{const q=put(...values);q.addEventListener('success',()=>transaction.abort());return q;};return store;};return transaction;}};
  await rejects(()=>editExpense(fault,entry.id,1,{...form,amount:'50'},today));
  s=await readLedger(db);ok(s.expenses[0].cents==='1' && s.actions.length===2,'Abort rollback');lines.push('request success then abort rolls back both writes');
  s=await voidExpense(db,entry.id,1);ok(s.expenses[0].voided===1 && s.expenses[0].revision===2 && s.actions.length===3,'Soft delete');
  ok(s.actions[2].before.voided===0,'Void before snapshot');lines.push('void retains entry and ordered actions');
  db.close();const reopened=await openDatabase(DB_NAME+'-checks');s=await readLedger(reopened);
  ok(s.expenses[0].voided===1 && s.actions.length===3,'No resurrection');reopened.close();lines.push('reopened connection preserves void and operations');
  const second=await openDatabase(DB_NAME+'-checks');
  await Promise.all([addExpense(second,form,today),addExpense(second,{...form,note:'另一合成用途'},today)]);
  s=await readLedger(second);ok(s.actions.length===5 && s.expenses.length===3,'No lost concurrent add');
  const active=s.expenses.find(x=>x.voided===0);const competing=await openDatabase(DB_NAME+'-checks');
  const results=await Promise.allSettled([editExpense(second,active.id,0,form,today),editExpense(competing,active.id,0,form,today)]);
  ok(results.filter(r=>r.status==='fulfilled').length===1 && results.filter(r=>r.status==='rejected'&&r.reason.code==='conflict').length===1,'Concurrent revision');
  competing.close();lines.push('two connections preserve writes and reject old revision');
  tx=second.transaction('ledger','readwrite');const store=tx.objectStore('ledger');const changed=await request(store.get('local-test-owner'));
  changed.categories=[{name:'餐飲',active:0}];store.put(changed,'local-test-owner');await txDone(tx);
  await rejects(()=>addExpense(second,form,today));s=await readLedger(second);ok(s.categories[0].active===0,'No reenable');
  lines.push('disabled override survives reads and rejects new use');
  for(const c of (await (await fetch('fixtures/life_ledger_rules.json')).json()).amounts){if(c.invalid) await rejects(()=>Promise.resolve(money(c.input)));else ok(money(c.input)===String(c.cents),'Money fixture');}
  ok(isoDate('2024-02-29')==='2024-02-29','Leap day');ok(sumCents([{cents:'5000000000000001'},{cents:'5000000000000002'}])===10000000000000003n,'Exact large sum');lines.push('unchanged amount fixtures; leap date and BigInt totals');
  second.close();document.querySelector('#result').textContent='PASS：'+lines.length+'組原生 IndexedDB／規則驗證';
} catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}
finally{document.querySelector('#details').textContent=lines.join('\n');}
