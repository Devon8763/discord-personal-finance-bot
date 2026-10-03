import * as ledger from '../local-first/ledger.mjs';
import {recurringProgress,fixedNextMonth} from '../local-first/rules.mjs';
import {emptyData,readBackup,writeBackup} from '../local-first/backup.mjs';
import {STORES,addState,portable} from '../local-first/idb.mjs';
import {monthBudget,searchExpenses,calendarMonth} from '../local-first/browse.mjs';
const check=(value,message)=>{if(!value)throw new Error(message);};
const done=tx=>new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});
const db=await ledger.openDatabase('life-ledger-recurring-checks'),today=ledger.taiwanToday(),month=today.slice(0,7),lines=[];
const empty={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData()};
const input={kind:'訂閱',name:'合成訂閱',amount:'.29',category:'居住',start_month:month,due_day:1};
const seed=async state=>{const tx=db.transaction(STORES,'readwrite');for(const section of STORES)tx.objectStore(section).clear();addState(tx,state);await done(tx);};
const bytes=async connection=>new TextDecoder().decode(await ledger.exportPortableBackup(connection));
const refused=async action=>{const before=await bytes(db);let error;try{await action();}catch(caught){error=caught;}check(error,'Expected rejection');check(await bytes(db)===before,'Rejected transaction changed backup');return error;};
try {
  await seed(empty);let state=await ledger.updateRecurring(db,'add',null,null,input);
  state=await ledger.updateRecurring(db,'add',null,null,{...input,kind:'分期',periods:1});
  state=await ledger.updateRecurring(db,'add',null,null,{...input,kind:'固定'});
  check(state.recurring_rules.length===3&&!state.expenses.length&&state.recurring_versions.length===1,'Create fields/implicit post');
  const before=await bytes(db),snapshot=await ledger.readLedger(db);snapshot.recurring_rules.forEach(row=>recurringProgress(snapshot,row));
  check(before===await bytes(db),'Read-only progress wrote');lines.push('three types use existing stores; fixed-only versions; create/read never post');
  const other=await ledger.openDatabase(db.name);
  const concurrent=await Promise.allSettled([ledger.updateRecurring(db,'sync'),ledger.updateRecurring(other,'sync')]);
  check(concurrent.every(row=>row.status==='fulfilled'),'Concurrent sync refused');state=await ledger.readLedger(db);
  check(state.expenses.length===3&&state.actions.length===3&&new Set(state.expenses.map(row=>row.source)).size===3,'Duplicate/missing monthly sources');
  const sub=state.recurring_rules[0],races=await Promise.allSettled([ledger.updateRecurring(db,'stop',sub.id,0),ledger.updateRecurring(other,'stop',sub.id,0)]);
  check(races.filter(row=>row.status==='fulfilled').length===1,'Stop revision overwrite');other.close();
  await refused(()=>ledger.updateRecurring(db,'stop',state.recurring_rules[1].id,0));
  lines.push('two connections serialize all-source catch-up and one-winner stop; completed installment stop refused');
  for(const kind of ['訂閱','分期'])for(const op of ['add','update','sync','stop'])for(const mode of ['throw','abort']) {
    const form={...input,kind,periods:kind==='分期'?2:0};state=ledger.changeRecurring(empty,'add',null,null,form,today);await seed(state);
    const original=IDBObjectStore.prototype.put;let writes=0;
    IDBObjectStore.prototype.put=function(...args){if(mode==='throw'&&++writes===2)throw new DOMException('PRIVATE-DETAIL','QuotaExceededError');const request=original.apply(this,args);if(mode==='abort'&&++writes===2)request.addEventListener('success',()=>this.transaction.abort());return request;};
    try{const error=await refused(()=>ledger.updateRecurring(db,op,state.recurring_rules[0].id,0,form));check(!error.message.includes('PRIVATE-DETAIL'),'Storage error leaked');}finally{IDBObjectStore.prototype.put=original;}
  }
  lines.push('both types add/update/sync/stop mid-write quota exception and success-request abort fully roll back metadata, rows and status');
  for(const kind of ['訂閱','分期']) {
    state=ledger.changeRecurring(empty,'add',null,null,{...input,kind,periods:kind==='分期'?2:0},today);await seed(state);
    const id=state.recurring_rules[0].id,other=await ledger.openDatabase(db.name);
    const races=await Promise.allSettled([ledger.updateRecurring(db,'update',id,0,{amount:'.31',category:'居住'}),ledger.updateRecurring(other,'update',id,0,{amount:'.37',category:'醫療'})]);
    check(races.filter(row=>row.status==='fulfilled').length===1,'Edit revision overwrite');other.close();
    state=await ledger.readLedger(db);check(state.recurring_versions.length===1&&state.recurring_versions[0].effective_month===fixedNextMonth(month)&&state.recurring_rules[0].cents==='29','Edit changed original or wrong month');
    await refused(()=>ledger.updateRecurring(db,'update',id,0,{amount:'.41',category:'交通'}));
    state=await ledger.updateRecurring(db,'update',id,1,{amount:'.41',category:'交通'});check(state.recurring_versions.length===1&&state.recurring_versions[0].cents==='41','Same-month edit created conflicting versions');
  }
  lines.push('two connections have one edit winner; stale revision preserves bytes; repeated next-month edit replaces one version and keeps original rule');
  const NativeDate=globalThis.Date;let instant='2027-12-31T15:59:59Z';
  globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[instant]));}};
  try {
    for(const kind of ['訂閱','分期']) {
      instant='2027-12-31T15:59:59Z';state=ledger.changeRecurring(empty,'add',null,null,{...input,kind,periods:kind==='分期'?2:0,start_month:'2027-12'},'2027-12-31');await seed(state);
      const blocker=db.transaction(STORES,'readwrite');blocker.objectStore('meta').get('created').onsuccess=()=>{instant='2027-12-31T16:00:00Z';};
      const queued=ledger.updateRecurring(db,'update',state.recurring_rules[0].id,0,{amount:'.31',category:'醫療'});
      await done(blocker);state=await queued;check(state.recurring_versions[0].effective_month==='2028-02','Edit used pre-transaction month');
    }
  }finally{globalThis.Date=NativeDate;}
  lines.push('subscription/installment queued edits read Taiwan clock inside transaction across year and midnight');
  state=ledger.changeRecurring(empty,'add',null,null,{...input,kind:'分期',periods:1},today);state.recurring_rules[0].due_day=null;state.categories.push({name:'居住',active:0});await seed(state);
  state=await ledger.updateRecurring(db,'sync');state.expenses[0].voided=1;state.actions[0].undone=1;await seed(state);
  const voided=await bytes(db);state=await ledger.updateRecurring(db,'sync');check(await bytes(db)===voided&&recurringProgress(state,state.recurring_rules[0]).complete,'Voided installment revived');
  check(state.expenses[0].spent_on===month+'-01','Legacy null day');lines.push('null means day one; inactive historical category retained; voided final installment remains completed');
  state=ledger.changeRecurring(empty,'add',null,null,input,today);state.categories=Array.from({length:99999},(_,i)=>({name:'c'+i,active:0}));await seed(state);
  await refused(()=>ledger.updateRecurring(db,'update',state.recurring_rules[0].id,0,{amount:'.31',category:'居住'}));
  state.categories.pop();await seed(state);
  await refused(()=>ledger.updateRecurring(db,'sync'));await refused(()=>ledger.updateRecurring(db,'stop',state.recurring_rules[0].id,0));
  state.categories.pop();await seed(state);state=await ledger.updateRecurring(db,'sync');check(state.expenses.length===1,'Exact capacity boundary refused');
  lines.push('100000-row boundary cannot leave half expense/action/version or stopped status');
  const imported={...empty,...readBackup(new Uint8Array(await(await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer())).data};await seed(imported);
  state=await ledger.readLedger(db);
  for(const rule of state.recurring_rules.filter(row=>row.kind!=='固定')) state=await ledger.updateRecurring(db,'update',rule.id,rule.revision,{amount:'.31',category:rule.category});
  state=await ledger.updateRecurring(db,'sync');const prior=await bytes(db),view=await ledger.readLedger(db);
  check(searchExpenses(view,{keyword:'',start:month+'-01',end:today},today).reduce((sum,row)=>sum+BigInt(row.cents),0n)===monthBudget(view,today).spent,'Search/budget mismatch');
  check(calendarMonth(view,month,today).days.reduce((sum,row)=>sum+row.total,0n)===monthBudget(view,today).spent,'Calendar/budget mismatch');
  check(await bytes(db)===prior,'Read views wrote');
  const restored=await ledger.openDatabase(ledger.DB_NAME+'-portable-checks'),clear=restored.transaction(STORES,'readwrite');for(const section of STORES)clear.objectStore(section).clear();await done(clear);
  await ledger.restorePortableBackup(restored,await ledger.exportPortableBackup(db));check(await bytes(restored)===prior,'Full nine-section backup changed');
  const repack=writeBackup(portable(await ledger.readLedger(restored)));check(new TextDecoder().decode(repack)===prior,'Re-export mismatch');
  let repeated;try{await ledger.restorePortableBackup(restored,repack);}catch(error){repeated=error;}check(repeated&&await bytes(restored)===prior,'Repeated restore changed ledger');restored.close();
  lines.push('Python nine-section fixture retains huge imported periods, zero/history/before snapshots and exact action order; read-only search/calendar/budget agree; atomic restore roundtrip');
  document.querySelector('#result').textContent='PASS：'+lines.length+' 組訂閱／分期交易驗證';document.querySelector('#details').textContent=lines.join('\n');
}catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}finally{db.close();}
