import * as ledger from '../local-first/ledger.mjs';
import { monthBudget } from '../local-first/browse.mjs';
import { emptyData, readBackup, writeBackup, MAX_BYTES } from '../local-first/backup.mjs';
import { STORES, addState, portable } from '../local-first/idb.mjs';
const check=(value,message)=>{if(!value)throw new Error(message);};
const done=tx=>new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});
const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
const datePart=type=>parts.find(row=>row.type===type).value;
const today=`${datePart('year')}-${datePart('month')}-${datePart('day')}`,month=today.slice(0,7);
const initial={format:'local-first-test-ledger',version:2,owner:'local-test-owner',
  ...readBackup(new Uint8Array(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer())).data};
const historical=structuredClone(initial.budgets);
const row=(category,cents)=>({month,category,cents});
initial.budgets.push(row('交通','0'));
const lines=[];
const db=await ledger.openDatabase(ledger.DB_NAME+'-budgets-checks');
const seed=async state=>{const tx=db.transaction(STORES,'readwrite');for(const section of STORES)tx.objectStore(section).clear();addState(tx,state);await done(tx);};
const bytes=async connection=>new TextDecoder().decode(await ledger.exportPortableBackup(connection));
const refused=async(action,connection=db)=>{const before=await bytes(connection);let rejected;try{await action();}catch(error){rejected=error;}check(rejected,'Expected rejection');check(await bytes(connection)===before,'Rejected write changed backup');return rejected;};
try {
  await seed(initial);
  check(typeof ledger.updateBudget==='function','Missing budget transaction');
  let state=await ledger.updateBudget(db,initial.budgets,'set','餐飲','100');
  check(state.budgets.at(-1).month===month && state.budgets.at(-1).cents==='10000','Current Taiwan month');
  check(monthBudget(state,today).total===null,'Independent category budget');
  await refused(()=>ledger.updateBudget(db,state.budgets,'set','總額','99'));
  state=await ledger.updateBudget(db,state.budgets,'set','總額','100');
  await refused(()=>ledger.updateBudget(db,state.budgets,'set','餐飲','101'));
  await refused(()=>ledger.updateBudget(db,state.budgets,'set','交通','1'));
  state=await ledger.updateBudget(db,state.budgets,'clear','交通');
  state=await ledger.updateBudget(db,state.budgets,'clear','總額');
  check(state.budgets.length===historical.length+1,'Clear reindexed rows');
  check(JSON.stringify(state.budgets.slice(0,historical.length))===JSON.stringify(historical),'Historical months changed');
  lines.push('current Taiwan month, optional total, exact lower bound, inactive zero clear and row order');

  const other=await ledger.openDatabase(db.name),old=structuredClone(state.budgets);
  state=await ledger.updateBudget(other,old,'set','總額','150');
  check((await refused(()=>ledger.updateBudget(db,old,'set','總額','200'))).code==='conflict','Stale absent total');
  const two=await Promise.allSettled([ledger.updateBudget(db,state.budgets,'set','總額','160'),ledger.updateBudget(other,state.budgets,'set','總額','170')]);
  check(two.filter(item=>item.status==='fulfilled').length===1 && two.some(item=>item.reason?.code==='conflict'),'Concurrent writes silently overwrote');
  state=await ledger.readLedger(db);
  const prior=structuredClone(state.budgets);
  await ledger.updateBudget(other,state.budgets,'set','餐飲','110');
  state=await ledger.updateBudget(db,prior,'sum','總額');
  check(state.budgets.find(item=>item.month===month&&item.category==='總額').cents==='11000','Sum used stale category data');
  other.close();lines.push('two connections reject stale same budget; sum re-reads latest categories inside write transaction');

  for(const op of ['set','clear'])for(const mode of ['throw','abort']) {
    const original=IDBObjectStore.prototype.put;let writes=0;
    IDBObjectStore.prototype.put=function(...args){if(mode==='throw'&&++writes===2)throw new DOMException('PRIVATE-DETAIL','QuotaExceededError');const req=original.apply(this,args);if(mode==='abort'&&++writes===2)req.addEventListener('success',()=>this.transaction.abort());return req;};
    try{await refused(()=>ledger.updateBudget(db,state.budgets,op,'餐飲','50'));}finally{IDBObjectStore.prototype.put=original;}
  }
  lines.push('mid-write exception and request-success abort roll back set/clear and capacity metadata');
  const corrupt=db.transaction('budgets','readwrite');corrupt.objectStore('budgets').put({key:[month,'總額'],position:state.budgets.findIndex(item=>item.month===month&&item.category==='總額'),value:row('總額','bad')});await done(corrupt);
  let invalid=false;try{await ledger.updateBudget(db,state.budgets,'set','總額','200');}catch{invalid=true;}check(invalid,'Corrupt storage accepted');
  await seed(state);lines.push('corrupt stored budget refused without replacement');

  const payload=await ledger.exportPortableBackup(db),portableDb=await ledger.openDatabase(ledger.DB_NAME+'-portable-checks');
  const clear=portableDb.transaction(STORES,'readwrite');for(const section of STORES)clear.objectStore(section).clear();await done(clear);
  await ledger.restorePortableBackup(portableDb,payload);
  check(await bytes(portableDb)===await bytes(db),'Backup roundtrip differs');portableDb.close();
  const beforeRead=await bytes(db);monthBudget(await ledger.readLedger(db),today);await ledger.readLedger(db);
  check(await bytes(db)===beforeRead,'Read-only summary changed ledger');
  lines.push('all nine sections, every month, exact values, zero and action order roundtrip; reads never write');

  const NativeDate=globalThis.Date;
  const priorMonth=month.endsWith('-01')?(Number(month.slice(0,4))-1)+'-12':month.slice(0,5)+String(Number(month.slice(5))-1).padStart(2,'0');
  let instant=priorMonth+'-01T00:00:00Z';
  globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[instant]));}};
  try {
    const blocker=db.transaction(STORES,'readwrite');
    blocker.objectStore('meta').get('created').onsuccess=()=>{instant=month+'-01T00:00:00Z';};
    const queued=ledger.updateBudget(db,state.budgets,'clear','總額',undefined,'2001-01');
    await done(blocker);const changed=await queued;
    check(!changed.budgets.some(item=>item.month===month&&item.category==='總額'),'Queued operation used old/external month');
    check(JSON.stringify(changed.budgets.slice(0,historical.length))===JSON.stringify(historical),'Clock rollover changed history');
  }finally{globalThis.Date=NativeDate;}
  await seed(state);lines.push('queued write reads Taiwan month at transaction operation time and ignores caller month');

  const full={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),
    payment_sources:[{id:'p_cash',name:'現金',active:1}],categories:Array.from({length:99998},(_,i)=>({name:'c'+i,active:1}))};
  await seed(full);let fullState=await ledger.updateBudget(db,[],'set','總額','1');
  await refused(()=>ledger.updateBudget(db,fullState.budgets,'set','餐飲','1'));
  fullState=await ledger.updateBudget(db,fullState.budgets,'set','總額','2');
  fullState=await ledger.updateBudget(db,fullState.budgets,'clear','總額');
  check(fullState.budgets.length===0,'Capacity clear rejected');lines.push('100000-row boundary allows existing-row changes/clear and refuses append');

  const large={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),payment_sources:[{id:'p_cash',name:'現金',active:1}]};
  const expense={id:'e0',spent_on:today,cents:'1',category:'餐飲',note:'字'.repeat(4096),source:'manual',recurring_id:null,period:null,voided:0,payment_source_id:'p_cash',payment_source_name:'現金',kind:'consumption',revision:0};
  large.expenses=Array.from({length:5350},(_,i)=>({...expense,id:'e'+i}));
  let size=writeBackup(portable(large)).byteLength;
  const overhead=JSON.stringify({...expense,id:'epad',note:''}).length+1;
  // Fill exactly to the portable byte boundary using bounded ASCII notes, without changing the format.
  while(MAX_BYTES-size>=overhead){const id='epad'+large.expenses.length;const cost=new TextEncoder().encode(JSON.stringify({...expense,id,note:''})).byteLength+1;
    if(MAX_BYTES-size<cost)break;const note='x'.repeat(Math.min(4096,MAX_BYTES-size-cost));large.expenses.push({...expense,id,note});size+=cost+note.length;}
  const padding=MAX_BYTES-size,last=large.expenses.at(-1);
  check(last.note.length>=padding,'Fixture padding exceeds note');
  last.note='é'.repeat(padding)+last.note.slice(padding);size+=padding;
  check(writeBackup(portable(large)).byteLength===MAX_BYTES && size===MAX_BYTES,'Near-byte-boundary fixture');
  await seed(large);await refused(()=>ledger.updateBudget(db,[],'set','總額','1000000000'));
  await seed(state);lines.push('near-64MiB budget append rejected with unchanged bytes');
  document.querySelector('#result').textContent='PASS：'+lines.length+' 組原生預算交易驗證';
}catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}
finally{db.close();document.querySelector('#details').textContent=lines.join('\n');}
