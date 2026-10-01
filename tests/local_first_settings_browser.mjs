import * as ledger from '../local-first/ledger.mjs';
import { readBackup, writeBackup, emptyData } from '../local-first/backup.mjs';
import { STORES, addState, portable } from '../local-first/idb.mjs';
const lines=[];
const check=(value,message)=>{if(!value) throw new Error(message);};
const done=tx=>new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=()=>reject(tx.error);});
const bytes=async db=>new TextDecoder().decode(await ledger.exportPortableBackup(db));
const db=await ledger.openDatabase(ledger.DB_NAME+'-settings-checks');
const seed=async state=>{
  const tx=db.transaction(STORES,'readwrite');
  for(const section of STORES) tx.objectStore(section).clear();
  addState(tx,state); await done(tx);
};
const original={format:'local-first-test-ledger',version:2,owner:'local-test-owner',
  ...readBackup(new Uint8Array(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer())).data};
original.budgets.push({month:'2020-01',category:'居住',cents:'0'},{month:'2029-01',category:'居住',cents:'9007199254740993'});
original.shortcuts[0].category='居住';
original.actions.push({id:'a8',expense_id:'e2',before:{...original.expenses[1],revision:0},undone:1});
try {
  await seed(original);
  check(typeof ledger.updateCategory==='function','Missing category transaction');
  let state=await ledger.updateCategory(db,original.categories,'rename','居住','房屋');
  check(state.expenses[1].category==='房屋' && state.expenses[1].revision===2,'Voided expense/reference revision');
  check(state.actions.at(-1).before.category==='房屋' && state.actions.at(-1).before.revision===0,'Undo snapshot');
  check(state.recurring_rules[0].revision===9007199254740995n,'Exact parent revision');
  check(state.budgets.at(-1).category==='房屋' && state.shortcuts[0].category==='房屋','All historical references');
  check(state.actions.map(row=>row.id).join()==='a1,a2,a3,a4,a5,a6,a7,a8','Action order');
  state=await ledger.updateCategory(db,state.categories,'add',null,'自訂');
  state=await ledger.updateCategory(db,state.categories,'rename','自訂','自訂新');
  check(!state.categories.some(row=>row.name==='自訂'),'Custom category rekey');
  state=await ledger.updateCategory(db,state.categories,'disable','自訂新');
  check((await ledger.readLedger(db)).categories.at(-1).active===0,'Disable persisted');
  lines.push('category references, precise revisions, custom rekey, add and disable');

  const committed=await bytes(db);
  const other=await ledger.openDatabase(db.name);
  let rejected=false;
  try{await ledger.updateCategory(other,original.categories,'disable','餐飲');}catch(error){rejected=error.code==='conflict';}
  other.close(); check(rejected && await bytes(db)===committed,'Stale connection must not write');
  lines.push('stale settings from another connection rejected');

  for(const mode of ['throw','abort']) {
    const put=IDBObjectStore.prototype.put; let writes=0;
    IDBObjectStore.prototype.put=function(...args){
      if(mode==='throw' && ++writes===3) throw new DOMException('PRIVATE-DETAIL','QuotaExceededError');
      const req=put.apply(this,args);
      if(mode==='abort' && ++writes===3) req.addEventListener('success',()=>this.transaction.abort());
      return req;
    };
    try{await ledger.updateCategory(db,state.categories,'rename','房屋','房屋新');throw new Error('Write succeeded');}
    catch(error){check(error.message!=='Write succeeded','Failure injection not exercised');}
    finally{IDBObjectStore.prototype.put=put;}
    check(await bytes(db)===committed,'Partial category write after '+mode);
  }
  lines.push('mid-write throw and request-success abort roll back every store and metadata');

  for(const value of ['交通','自訂停用','歷史無override']) {
    let refused=false;try{await ledger.updateCategory(db,state.categories,'rename','房屋',value);}catch{refused=true;}
    check(refused && await bytes(db)===committed,'Historical/default conflict wrote');
  }
  lines.push('default, disabled and historical conflicts leave identical backup');

  const tx=db.transaction('actions','readwrite');
  tx.objectStore('actions').put({key:'a8',position:7,value:{...state.actions[7],before:'corrupt'}});await done(tx);
  let corrupt=false;try{await ledger.updateCategory(db,state.categories,'rename','房屋','拒絕');}catch{corrupt=true;}
  check(corrupt,'Corrupt history accepted');
  await seed(state);check(await bytes(db)===committed,'Test reseed mismatch');
  lines.push('corrupt undo history rejected');

  check(typeof ledger.updatePayment==='function','Missing payment transaction');
  const expenseHistory=structuredClone(state.expenses), actionHistory=structuredClone(state.actions);
  state=await ledger.updatePayment(db,state.payment_sources,'add',null,'合成卡');
  const paymentId=state.payment_sources.at(-1).id;
  const oldPayments=structuredClone(state.payment_sources);
  state=await ledger.updatePayment(db,oldPayments,'rename',paymentId,'合成新卡');
  check(state.payment_sources.at(-1).id===paymentId && state.payment_sources.at(-1).name==='合成新卡','Payment ID/name');
  state=await ledger.updatePayment(db,state.payment_sources,'disable',paymentId);
  check(state.payment_sources.at(-1).active===0,'Payment disable');
  check(writeBackup(portable({...state,expenses:expenseHistory,actions:actionHistory})).toString()===writeBackup(portable(state)).toString(),'Payment rewrote history');
  const paymentCommitted=await bytes(db);
  let stale=false;try{await ledger.updatePayment(db,oldPayments,'rename',paymentId,'拒絕');}catch(error){stale=error.code==='conflict';}
  check(stale && await bytes(db)===paymentCommitted,'Stale payment wrote');
  for(const [op,id,value] of [['add',null,'合成新卡'],['rename','p1','拒絕'],['disable','p2',null]]) {
    let refused=false;try{await ledger.updatePayment(db,state.payment_sources,op,id,value);}catch{refused=true;}
    check(refused && await bytes(db)===paymentCommitted,'Reserved/duplicate payment wrote');
  }
  const originalPut=IDBObjectStore.prototype.put;let writes=0;
  IDBObjectStore.prototype.put=function(...args){const req=originalPut.apply(this,args);if(++writes===2)req.addEventListener('success',()=>this.transaction.abort());return req;};
  try{await ledger.updatePayment(db,state.payment_sources,'add',null,'回滾卡');throw new Error('Write succeeded');}
  catch(error){check(error.message!=='Write succeeded','Payment failure injection not exercised');}
  finally{IDBObjectStore.prototype.put=originalPut;}
  check(await bytes(db)===paymentCommitted,'Payment partial write');
  lines.push('payment ID, snapshots, disabled duplicates, reserved options, stale state and rollback');

  const portableDb=await ledger.openDatabase(ledger.DB_NAME+'-portable-checks');
  const clear=portableDb.transaction(STORES,'readwrite');for(const section of STORES)clear.objectStore(section).clear();await done(clear);
  const final=await bytes(db);
  await ledger.restorePortableBackup(portableDb,new TextEncoder().encode(final));
  check(await bytes(portableDb)===final,'Export/blank restore/export differs');portableDb.close();
  check(new TextDecoder().decode(writeBackup(portable(state)))===final,'Returned state differs from committed backup');
  lines.push('export, blank restore and re-export preserve bytes and operation order');
  const full={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),
    categories:Array.from({length:99998},(_,i)=>({name:'limit'+i,active:1})),
    payment_sources:[{id:'p_cash',name:'現金',active:1},{id:'p_unspecified',name:'未指定',active:1}]};
  await seed(full);const capacityBefore=await bytes(db);
  for(const action of [()=>ledger.updateCategory(db,full.categories,'add',null,'拒絕'),
    ()=>ledger.updateCategory(db,full.categories,'rename','餐飲','拒絕'),
    ()=>ledger.updatePayment(db,full.payment_sources,'add',null,'拒絕卡')]) {
    let refused=false;try{await action();}catch{refused=true;}
    check(refused && await bytes(db)===capacityBefore,'Row capacity failure left changes');
  }
  await seed(state);
  lines.push('100000-row limit refuses category add, builtin rename and payment add without writes');
  const large={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),
    payment_sources:[{id:'p_cash',name:'現金',active:1}]};
  const largeRow={id:'e0',spent_on:'2025-01-01',cents:'1',category:'餐飲',note:'字'.repeat(4096),source:'manual',
    recurring_id:null,period:null,voided:0,payment_source_id:'p_cash',payment_source_name:'現金',kind:'consumption',revision:0};
  large.expenses=Array.from({length:5356},(_,i)=>({...largeRow,id:'e'+i}));
  await seed(large);const bytesBefore=await bytes(db);
  let byteLimit=false;try{await ledger.updateCategory(db,large.categories,'rename','餐飲','字'.repeat(20));}catch{byteLimit=true;}
  check(byteLimit && await bytes(db)===bytesBefore,'Byte capacity failure left changes');
  await seed(state);
  lines.push('near-64MiB history expansion refuses rename and preserves identical backup');
  document.querySelector('#result').textContent='PASS：'+lines.length+' 組原生設定交易驗證';
}catch(error){document.querySelector('#result').textContent='FAIL：'+error.message;throw error;}
finally{db.close();document.querySelector('#details').textContent=lines.join('\n');}
