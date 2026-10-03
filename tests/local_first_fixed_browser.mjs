import * as ledger from '../local-first/ledger.mjs';
import { fixedNextMonth, fixedView } from '../local-first/rules.mjs';
import { monthBudget, searchExpenses, calendarMonth } from '../local-first/browse.mjs';
import { emptyData, readBackup, writeBackup, MAX_BYTES } from '../local-first/backup.mjs';
import { STORES, addState, portable } from '../local-first/idb.mjs';
const check=(value,message)=>{if(!value)throw new Error(message);};
const done=tx=>new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});
const db=await ledger.openDatabase(ledger.DB_NAME+'-fixed-checks'),lines=[];
const today=ledger.taiwanToday(),month=today.slice(0,7);
const initial={format:'local-first-test-ledger',version:2,owner:'local-test-owner',
  ...readBackup(new Uint8Array(await(await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer())).data};
const input={name:'合成固定',amount:'12.34',category:'居住',start_month:month,due_day:1};
const seed=async state=>{const tx=db.transaction(STORES,'readwrite');for(const section of STORES)tx.objectStore(section).clear();addState(tx,state);await done(tx);};
const bytes=async connection=>new TextDecoder().decode(await ledger.exportPortableBackup(connection));
const refused=async action=>{const before=await bytes(db);let rejected;try{await action();}catch(error){rejected=error;}check(rejected,'Expected rejection');check(await bytes(db)===before,'Rejected write changed backup');return rejected;};
try {
  await seed(initial);check(typeof ledger.updateFixed==='function','Missing fixed transaction');
  let state=await ledger.updateFixed(db,'add',null,null,input),rule=state.recurring_rules.at(-1);
  check(rule.start_month===month&&state.expenses.length===initial.expenses.length,'Add implicitly posted');
  state=await ledger.updateFixed(db,'update',rule.id,0,{...input,name:'下月合成',amount:'.29'});
  state=await ledger.updateFixed(db,'update',rule.id,1,{...input,name:'最後設定',amount:'3.01'});
  check(state.recurring_versions.filter(row=>row.recurring_id===rule.id).length===2,'Duplicate effective version');
  check(fixedView(state,state.recurring_rules.at(-1),month).pending.name==='最後設定','Pending view');
  await refused(()=>ledger.updateFixed(db,'update',rule.id,0,input));
  lines.push('add never posts; next-month last success wins; stale revision rejected');

  const other=await ledger.openDatabase(db.name),beforeCount=state.expenses.length;
  const results=await Promise.allSettled([ledger.updateFixed(db,'sync'),ledger.updateFixed(other,'sync')]);
  check(results.every(result=>result.status==='fulfilled'),'Parallel sync failed');
  state=await ledger.readLedger(db);
  const posted=state.expenses.filter(row=>row.recurring_id===rule.id);
  check(posted.length===1&&posted[0].cents==='1234'&&posted[0].spent_on===month+'-01','Parallel duplicate or wrong version');
  check(state.expenses.length>beforeCount&&state.expenses.filter(row=>row.source==='訂閱'||row.source==='分期').length===
    initial.expenses.filter(row=>row.source==='訂閱'||row.source==='分期').length,'Fixed sync touched other sources');
  const concurrent=await Promise.allSettled([ledger.updateFixed(db,'update',rule.id,2,{...input,name:'競爭A'}),ledger.updateFixed(other,'stop',rule.id,2)]);
  check(concurrent.filter(row=>row.status==='fulfilled').length===1,'Stale update/stop silently overwrote');
  state=await ledger.readLedger(db);other.close();
  lines.push('two connections serialize catch-up with one expense/action per month; competing same revision has one winner');

  const bare={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData()};
  state=ledger.changeFixed(bare,'add',null,null,input,today);rule=state.recurring_rules[0];await seed(state);
  for(const op of ['add','update','sync','stop'])for(const mode of ['throw','abort']) {
    const original=IDBObjectStore.prototype.put;let writes=0;
    IDBObjectStore.prototype.put=function(...args){if(mode==='throw'&&++writes===2)throw new DOMException('PRIVATE-DETAIL','QuotaExceededError');const req=original.apply(this,args);if(mode==='abort'&&++writes===2)req.addEventListener('success',()=>this.transaction.abort());return req;};
    try {const error=await refused(()=>ledger.updateFixed(db,op,rule.id,0,{...input,name:'更改合成'}));check(!error.message.includes('PRIVATE-DETAIL'),'Raw storage error leaked');}
    finally{IDBObjectStore.prototype.put=original;}
  }
  lines.push('add/update/sync/stop mid-write exception and request-success abort fully roll back rows, versions and metadata');

  state=await ledger.updateFixed(db,'sync');state.expenses[0].voided=1;state.actions[0].undone=1;await seed(state);
  const beforeVoid=await bytes(db);await ledger.updateFixed(db,'sync');check(await bytes(db)===beforeVoid,'Voided record revived');
  state=await ledger.updateFixed(db,'stop',rule.id,0);check(state.recurring_rules[0].active===0&&state.expenses.length===1,'Stop recreated voided');
  await refused(()=>ledger.updateFixed(db,'stop',rule.id,1));
  state=ledger.changeFixed(bare,'add',null,null,input,today);state.recurring_rules[0].due_day=null;state.recurring_versions=[];await seed(state);
  state=await ledger.updateFixed(db,'stop',state.recurring_rules[0].id,0);
  check(state.expenses[0].spent_on===month+'-01'&&state.recurring_rules[0].active===0,'Legacy null catchup/stop');
  lines.push('voided/undone uniqueness, inactive refusal, legacy null day-one catch-up and atomic stop');

  await seed(initial);state=await ledger.updateFixed(db,'sync');
  const beforeRead=await bytes(db),snapshot=await ledger.readLedger(db);
  const total=monthBudget(snapshot,today).spent;
  const search=searchExpenses(snapshot,{keyword:'',start:month+'-01',end:today},today);
  const calendar=calendarMonth(snapshot,month,today);
  check(search.reduce((sum,row)=>sum+BigInt(row.cents),0n)===total,'Budget/search mismatch');
  check(calendar.days.reduce((sum,day)=>sum+day.total,0n)===total,'Budget/calendar mismatch');
  snapshot.recurring_rules.filter(row=>row.kind==='固定').forEach(row=>fixedView(snapshot,row,month));
  check(await bytes(db)===beforeRead,'Read-only views posted');
  const portableDb=await ledger.openDatabase(ledger.DB_NAME+'-portable-checks'),clear=portableDb.transaction(STORES,'readwrite');
  for(const section of STORES)clear.objectStore(section).clear();await done(clear);
  await ledger.restorePortableBackup(portableDb,await ledger.exportPortableBackup(db));
  check(await bytes(portableDb)===beforeRead,'Nine-section backup/action order roundtrip');portableDb.close();
  lines.push('read-only fixed/search/calendar/budget agree; full nine-section backup restores exactly including zero, historical months and actions');

  const NativeDate=globalThis.Date;let instant='2026-09-30T15:59:59Z';
  globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[instant]));}};
  try {
    state=ledger.changeFixed(bare,'add',null,null,{...input,start_month:'2026-09'},'2026-09-30');await seed(state);
    const blocker=db.transaction(STORES,'readwrite');blocker.objectStore('meta').get('created').onsuccess=()=>{instant='2026-09-30T16:00:00Z';};
    const queued=ledger.updateFixed(db,'update',state.recurring_rules[0].id,0,{...input,start_month:'2026-09'},'2001-01-01');
    await done(blocker);state=await queued;
    check(state.recurring_versions.at(-1).effective_month==='2026-11','Queued edit used old/caller clock');
  }finally{globalThis.Date=NativeDate;}
  lines.push('Taiwan midnight queued transaction reads current clock inside transaction; no caller-supplied posting month');

  state=ledger.changeFixed(bare,'add',null,null,input,today);rule=state.recurring_rules[0];
  state.categories=Array.from({length:99996},(_,i)=>({name:'c'+i,active:0}));await seed(state);
  state=await ledger.updateFixed(db,'sync');check(state.expenses.length===1,'Exact 100000 row append rejected');
  await refused(()=>ledger.updateFixed(db,'add',null,null,input));
  state=await ledger.updateFixed(db,'stop',rule.id,0);check(state.recurring_rules[0].active===0,'Stop at row limit rejected');
  lines.push('exact 100000-row boundary permits complete posting and stop, refuses another rule');

  let large=ledger.changeFixed(bare,'add',null,null,input,today);
  large=ledger.changeRecurring(large,'add',null,null,{...input,kind:'訂閱'},today);
  const expense={id:'e0',spent_on:today,cents:'1',category:'餐飲',note:'字'.repeat(4096),source:'manual',recurring_id:null,period:null,voided:0,payment_source_id:null,payment_source_name:'未指定',kind:'consumption',revision:0};
  large.expenses=Array.from({length:5350},(_,i)=>({...expense,id:'e'+i}));let size=writeBackup(portable(large)).byteLength;
  const overhead=JSON.stringify({...expense,id:'epad',note:''}).length+1;
  while(MAX_BYTES-size>=overhead){const id='epad'+large.expenses.length,cost=new TextEncoder().encode(JSON.stringify({...expense,id,note:''})).byteLength+1;
    if(MAX_BYTES-size<cost)break;const note='x'.repeat(Math.min(4096,MAX_BYTES-size-cost));large.expenses.push({...expense,id,note});size+=cost+note.length;}
  const padding=MAX_BYTES-size,last=large.expenses.at(-1);check(last.note.length>=padding,'Fixture padding');last.note='é'.repeat(padding)+last.note.slice(padding);
  check(writeBackup(portable(large)).byteLength===MAX_BYTES,'Exact byte boundary');await seed(large);
  await refused(()=>ledger.editExpense(db,'e0',0,{amount:'.31',note:'容量上限',spent_on:today,category:'餐飲',payment_source_id:''},today));
  await refused(()=>ledger.voidExpense(db,'e0',0));
  await refused(()=>ledger.updateFixed(db,'sync'));await refused(()=>ledger.updateFixed(db,'stop',large.recurring_rules[0].id,0));
  await refused(()=>ledger.updateRecurring(db,'update',large.recurring_rules.at(-1).id,0,{amount:'.31',category:'居住'}));
  lines.push('64MiB boundary refuses single-expense edit/delete, catch-up, stop and subscription version without partial expense/action/version or disabled rule');

  await seed(initial);const tx=db.transaction('recurring_rules','readwrite');
  tx.objectStore('recurring_rules').put({key:'r1',position:0,value:{...initial.recurring_rules[0],cents:'broken'}});await done(tx);
  const rawRows=()=>new Promise(resolve=>{const tx=db.transaction('recurring_rules');tx.objectStore('recurring_rules').getAll().onsuccess=event=>resolve(JSON.stringify(event.target.result,(_key,value)=>typeof value==='bigint'?value.toString():value));});
  const raw=await rawRows();
  let invalid=false;try{await ledger.updateFixed(db,'sync');}catch{invalid=true;}check(invalid,'Corrupt rule accepted');
  const after=await rawRows();
  check(after===raw,'Corrupt data overwritten');await seed(initial);
  lines.push('corrupt persisted rule rejected without replacement or repair');
  document.querySelector('#result').textContent='PASS：'+lines.length+' 組原生固定支出交易驗證';
}catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}
finally{db.close();document.querySelector('#details').textContent=lines.join('\n');}
