import {openDatabase,readLedgerIfPresent,exportPortableBackup,taiwanToday} from '../local-first/ledger.mjs';
import {STORES,addState} from '../local-first/idb.mjs';
import {readBackup} from '../local-first/backup.mjs';
const q=selector=>document.querySelector(selector),check=(value,message)=>{if(!value)throw new Error(message);};
const report=document.createElement('pre');report.id='csv-test-result';document.body.prepend(report);
const lines=[],blobs=[],db=await openDatabase();
const wait=predicate=>new Promise((resolve,reject)=>{if(predicate()){resolve();return;}const observer=new MutationObserver(()=>{if(predicate()){observer.disconnect();clearTimeout(timer);resolve();}}),timer=setTimeout(()=>{observer.disconnect();reject(new Error('CSV UI timeout'));},4000);observer.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true});});
const raw=async()=>{
  const tx=db.transaction(STORES,'readonly'),data={};
  for(const store of STORES)tx.objectStore(store).getAll().onsuccess=event=>{data[store]=event.target.result;};
  await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=reject;});
  return JSON.stringify(STORES.map(store=>data[store]),(_key,value)=>typeof value==='bigint'?{bigint:value.toString()}:value);
};
const create=URL.createObjectURL,click=HTMLAnchorElement.prototype.click,transaction=IDBDatabase.prototype.transaction;
try{
  check(await readLedgerIfPresent(db)===null,'Use a fresh synthetic origin; never overwrite an existing ledger');
  const fixture=readBackup(new Uint8Array(await(await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer()));
  const state={format:'local-first-test-ledger',version:2,owner:'local-test-owner',...fixture.data};
  state.expenses.find(row=>row.id==='e6').voided=0;
  const base=state.expenses[0];
  for(let i=0;i<65;i++)state.expenses.push({...base,id:`csv-${String(i).padStart(3,'0')}`,spent_on:'2024-02-29',cents:'1',note:'CSV 合成',revision:0});
  state.expenses.push({...base,id:'csv-danger',spent_on:'2024-02-29',cents:'9223372036854775807',
    note:' \t=SUM(1,2)\n"中文"',category:'\u200B+分類',payment_source_name:'@卡,"舊名"',revision:0});
  const tx=db.transaction(STORES,'readwrite');addState(tx,state);
  await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=reject;});
  const before=await raw(),backup=await exportPortableBackup(db);
  await import('../local-first/page.mjs');
  check(q('#csv-form'),'CSV entry missing');check(before===await raw(),'Opening created defaults or changed data');
  const form=q('#csv-form'),today=taiwanToday();
  check(form.elements.start.value===today.slice(0,7)+'-01'&&form.elements.end.value===today,'Taiwan defaults missing');
  check(q('#csv-export').textContent.includes('CSV 未加密')&&q('#csv-export').textContent.includes('不能用來還原帳本'),'Risk notice missing');
  lines.push('Taiwan range defaults and risk notice; opening is read-only');
  URL.createObjectURL=blob=>{blobs.push(blob);return create(blob);};HTMLAnchorElement.prototype.click=function(){};
  let reads=0;IDBDatabase.prototype.transaction=function(...args){reads++;return transaction.apply(this,args);};
  q('#csv-export').open=true;form.elements.start.value='2025-01-02';form.elements.end.value='2025-01-01';form.requestSubmit();
  await wait(()=>q('#csv-error').textContent.length>0);
  check(reads===0&&blobs.length===0&&form.elements.start.value==='2025-01-02','Invalid range queried, downloaded or lost input');
  form.elements.start.value='2024-02-29';form.elements.end.value='2025-01-01';form.elements.start.setCustomValidity('synthetic invalid date');form.requestSubmit();
  await wait(()=>q('#csv-error').textContent.includes('日期'));
  check(reads===0,'Invalid native date opened a query');form.elements.start.setCustomValidity('');
  IDBDatabase.prototype.transaction=transaction;lines.push('invalid/native date retains input and performs no read');
  form.elements.start.value='2024-03-01';form.elements.end.value='2024-03-01';form.requestSubmit();
  await wait(()=>q('#csv-status').textContent.includes('沒有符合'));
  check(blobs.length===0,'Empty result downloaded a file');lines.push('empty result is normal and never downloads');
  form.elements.start.value='2020-01-01';form.elements.end.value='2099-12-31';form.requestSubmit();
  await wait(()=>q('#csv-status').textContent.includes('已要求瀏覽器下載'));
  check(blobs.length===1,'Missing CSV request');const payload=new Uint8Array(await blobs[0].arrayBuffer());
  check(payload[0]===239&&payload[1]===187&&payload[2]===191&&blobs[0].type==='text/csv;charset=utf-8','BOM/type missing');
  const output=new TextDecoder().decode(payload);
  check(output.startsWith('日期,金額,消費項目,分類,付款方式\r\n')&&output.split(',CSV 合成,').length-1===65,'CSV truncated or wrong columns');
  check(output.includes('訂閱')&&output.includes('分期')&&output.includes('2020-02-29,0.29,,歷史無override,未指定'),'Restored sources missing');
  check(!output.includes('已儲存未來帳目')&&!output.includes('原固定'),'Future or voided consumption leaked');
  lines.push('complete >50-row CSV, four sources and future/voided exclusion');
  check(output.includes('92233720368547758.07')&&output.includes('改名前信用卡')&&!output.includes('改名後信用卡'),'Precision/payment snapshot changed');
  check(output.includes('"\' \t=SUM(1,2)\n""中文"""')&&output.includes("'\u200B+分類")&&output.includes('"\'@卡,""舊名"""'),'CSV escaping/formula defense missing');
  check(!q('#csv-export img')&&!window.__synthetic_xss&&!location.search,'CSV executed text or leaked into URL');
  lines.push('exact large cents, historical name, formula/quote/newline safety');
  const getAll=IDBObjectStore.prototype.getAll;IDBObjectStore.prototype.getAll=function(){throw new Error('PRIVATE CSV PATH TOKEN');};
  try{form.requestSubmit();await wait(()=>q('#csv-error').textContent.length>0);}
  finally{IDBObjectStore.prototype.getAll=getAll;}
  check(blobs.length===1&&!q('#csv-error').textContent.includes('PRIVATE')&&form.elements.end.value==='2099-12-31','Read failure leaked, downloaded or lost draft');
  check(!q('#csv-status').textContent&& !q('#csv-inputs').disabled,'Stale success or stuck controls');lines.push('read failure is safe and retains range');
  check(before===await raw(),'Export changed raw stores, positions, revisions or metadata');
  check(new TextDecoder().decode(await exportPortableBackup(db))===new TextDecoder().decode(backup),'Export changed backup bytes');
  check(!q('#csv-status').textContent.includes('已保存'),'False saved claim');lines.push('raw IndexedDB and full backup unchanged byte-for-byte');
  report.textContent='PASS：'+lines.length+' 組 CSV 原生畫面驗證\n'+lines.join('\n');
}catch(error){report.textContent='FAIL：'+error.message+'\n'+lines.join('\n');throw error;}
finally{URL.createObjectURL=create;HTMLAnchorElement.prototype.click=click;IDBDatabase.prototype.transaction=transaction;db.close();}
