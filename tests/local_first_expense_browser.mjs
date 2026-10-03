import * as ledger from '../local-first/ledger.mjs';
import { SECTIONS, emptyData, readBackup, writeBackup } from '../local-first/backup.mjs';
import { addState, portable } from '../local-first/idb.mjs';
import { searchExpenses, calendarMonth, monthBudget } from '../local-first/browse.mjs';
const check=(value,message)=>{if(!value)throw new Error(message);};
const done=tx=>new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});
const db=await ledger.openDatabase(ledger.DB_NAME+'-expense-checks'), peer=await ledger.openDatabase(db.name),lines=[];
const sections=[...SECTIONS,'meta'], today='2026-09-24';
const seed=async state=>{const tx=db.transaction(sections,'readwrite');for(const section of sections)tx.objectStore(section).clear();addState(tx,state);await done(tx);};
const bytes=async()=>new TextDecoder().decode(await ledger.exportPortableBackup(db));
async function rejected(operation,code){const before=await bytes();let error;try{await operation();}catch(failure){error=failure;}check(error&&(!code||error.code===code),'Expected rejection '+code);check(await bytes()===before,'Rejected operation changed ledger');}
const fault=(method,abort=false)=>({transaction(...args){const tx=db.transaction(...args),get=tx.objectStore.bind(tx);let aborted=false;tx.objectStore=name=>{const store=get(name),original=store[method].bind(store);store[method]=(...values)=>{if(!abort)throw new DOMException('synthetic private detail','QuotaExceededError');const q=original(...values);q.addEventListener('success',()=>{if(!aborted){aborted=true;tx.abort();}});return q;};return store;};return tx;}});
let initial={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),payment_sources:[{id:'p_cash',name:'現金',active:1},{id:'p_old',name:'停用卡',active:0}]};
for(const kind of ['固定','訂閱','分期'])initial=ledger.changeRecurring(initial,'add',null,null,{kind,name:'合成'+kind,amount:'.29',category:'餐飲',start_month:'2026-09',due_day:1,periods:kind==='分期'?2:0},today);
initial=ledger.changeRecurring(initial,'sync',null,null,null,today);
initial.expenses.push({...initial.expenses[0],id:'e_manual',source:'manual',recurring_id:null,period:null,revision:0});
initial.categories=[{name:'餐飲',active:0}];
for(const row of initial.expenses){row.payment_source_id='p_old';row.payment_source_name='更改前歷史卡';}
try{
  await seed(initial);let state=await ledger.readLedger(db);
  const rules=JSON.stringify(state.recurring_rules),versions=JSON.stringify(state.recurring_versions);
  for(const source of ['manual','固定','訂閱','分期']){
    let entry=state.expenses.find(row=>row.source===source);
    const form={amount:'.37',note:'<img src=x onerror=alert(1)>合成'+source,spent_on:entry.spent_on,category:entry.category,payment_source_id:'',source:'manual',recurring_id:null,period:null};
    if(source!=='manual')await rejected(()=>ledger.editExpense(db,entry.id,entry.revision,{...form,spent_on:'2026-08-31'},today));
    state=await ledger.editExpense(db,entry.id,entry.revision,form,today);
    const edited=state.expenses.find(row=>row.id===entry.id);
    check(edited.source===source&&edited.recurring_id===entry.recurring_id&&edited.period===entry.period,'Forged identity accepted');
    check(edited.cents==='37'&&edited.payment_source_name===entry.payment_source_name&&edited.payment_source_id==='p_old','Exact amount or historical snapshot lost');
    check(JSON.stringify(state.actions.at(-1).before)===JSON.stringify(entry),'Before snapshot changed');
    await rejected(()=>ledger.editExpense(db,entry.id,entry.revision,form,today),'conflict');
    await rejected(()=>ledger.voidExpense(db,entry.id,entry.revision),'conflict');
    await rejected(()=>ledger.editExpense(fault('add'),entry.id,edited.revision,form,today),'storage');
    await rejected(()=>ledger.voidExpense(fault('put',true),entry.id,edited.revision),'storage');
    state=await ledger.voidExpense(db,entry.id,edited.revision);
    await rejected(()=>ledger.voidExpense(db,entry.id,edited.revision),'unavailable');
    await rejected(()=>ledger.editExpense(db,entry.id,edited.revision,form,today),'unavailable');
  }
  check(JSON.stringify(state.recurring_rules)===rules&&JSON.stringify(state.recurring_versions)===versions,'Single expense changed schedule');
  check(ledger.changeRecurring(state,'sync',null,null,null,today).expenses.length===state.expenses.length,'Voided periods revived');
  const allSourcesState=structuredClone(state);
  check(searchExpenses(state,{keyword:'',start:'2026-09-01',end:''},today).length===0&&calendarMonth(state,'2026-09',today).days.every(day=>day.total===0n)&&monthBudget(state,today).spent===0n,'Effective views disagree after soft delete');
  lines.push('four sources: exact edits, immutable dates/identity, historical choices, ordered before snapshots, stale/voided and per-write rollback; no rule changes or revival');
  await rejected(()=>ledger.voidExpense(db,'missing',0),'unavailable');
  await seed(initial);const entry=initial.expenses[0],form={amount:'.41',note:'合成競爭',spent_on:entry.spent_on,category:'餐飲',payment_source_id:''};
  const race=await Promise.allSettled([ledger.editExpense(db,entry.id,0,form,today),ledger.voidExpense(peer,entry.id,0)]);
  check(race.filter(row=>row.status==='fulfilled').length===1&&race.filter(row=>row.reason?.code==='conflict'||row.reason?.code==='unavailable').length===1,'Two connections silently overwrite');
  lines.push('two connections edit/delete race commits once');
  const full=structuredClone(initial),other=SECTIONS.filter(section=>!['categories','settings'].includes(section)).reduce((count,section)=>count+full[section].length,0);
  full.categories.push(...Array.from({length:100000-other-full.categories.length},(_,i)=>({name:'c'+i,active:0})));await seed(full);
  await rejected(()=>ledger.editExpense(db,entry.id,0,form,today),'limit');await rejected(()=>ledger.voidExpense(db,entry.id,0),'limit');
  lines.push('100000-row boundary refuses edit/delete without partial action or expense');
  await seed(initial);let tx=db.transaction('expenses','readwrite');tx.objectStore('expenses').put({key:entry.id,position:0,value:{...entry,cents:'broken'}});await done(tx);
  const raw=async()=>{const tx=db.transaction(sections,'readonly'),rows={};for(const section of sections)tx.objectStore(section).getAll().onsuccess=event=>{rows[section]=event.target.result;};await done(tx);return JSON.stringify(sections.map(section=>rows[section]));};
  const corrupt=await raw();let refused=false;try{await ledger.editExpense(db,entry.id,0,form,today);}catch{refused=true;}check(refused&&await raw()===corrupt,'Corrupt ledger accepted or overwritten');
  await seed(allSourcesState);state=await ledger.readLedger(db);
  const original=await bytes(),restored={...state,...readBackup(writeBackup(portable(state))).data};await seed(restored);check(await bytes()===original,'Nine-section backup lost links or history');
  document.querySelector('#backup').textContent=await bytes();
  lines.push('corrupt data rejected; full backup roundtrip preserves nine sections, links, revisions and actions');
  document.querySelector('#result').textContent='PASS：'+lines.length+' 組四來源單筆交易驗證';
}catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}
finally{db.close();peer.close();document.querySelector('#details').textContent=lines.join('\n');}
