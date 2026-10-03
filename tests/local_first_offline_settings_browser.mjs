import {openDatabase,exportPortableBackup} from '../local-first/ledger.mjs';
import {STORES} from '../local-first/idb.mjs';
const result=document.querySelector('#result'),button=document.querySelector('#check');
const db=await openDatabase();
const before=await exportPortableBackup(db),namesBefore=await caches.keys();
const raw=async()=>{
  const tx=db.transaction(STORES,'readonly'),data={};
  for(const store of STORES)tx.objectStore(store).getAll().onsuccess=event=>{data[store]=event.target.result;};
  await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=reject;});
  return JSON.stringify(STORES.map(store=>data[store]),(_key,value)=>typeof value==='bigint'?{bigint:value.toString()}:value);
};
const rawBefore=await raw();
const text=bytes=>new TextDecoder().decode(bytes);
button.onclick=async()=>{
  button.disabled=true;
  try {
    const names=await caches.keys(),version='local-first-offline-0.13.12-v2';
    if(!names.includes(version))throw new Error('新版快取尚未啟用');
    const cache=await caches.open(version);
    for(const [path,content] of [['index.html','id="storage-status"'],['rules.mjs','自動來源消費的原入帳日期不可更改'],
      ['ledger.mjs',"kind === '固定' || operation === 'update'"],['page.mjs','function expenseRow'],['backup.mjs','版本不可更改欄位'],['browse.mjs','function monthBudget'],['csv.mjs','function expenseCsv'],['comparison.mjs','function compareExpenses'],['vendor/chartjs-4.5.1/chart.umd.min.js','Chart.js v4.5.1'],['backup-crypto.mjs','function decryptBackup']]){
      const resource=await cache.match('/local-first/'+path);
      if(!resource||!(await resource.text()).includes(content))throw new Error('離線資源不完整');
    }
    const paths=(await cache.keys()).map(request=>new URL(request.url));
    const required=new Set(['','index.html','style.css','rules.mjs','ledger.mjs','page.mjs','browse.mjs','csv.mjs','comparison.mjs','vendor/chartjs-4.5.1/chart.umd.min.js','idb.mjs','backup.mjs','backup-crypto.mjs','vendor/lossless-json-4.3.1/lossless-json.js'].map(path=>'/local-first/'+path));
    if(paths.length!==required.size||paths.some(url=>url.origin!==location.origin||url.search||!required.has(url.pathname)))throw new Error('快取超出必要靜態來源');
    if(names.some(name=>name.startsWith('local-first-offline-')&&name!==version))throw new Error('舊版快取仍在，請關閉舊頁後再試');
    if(text(await exportPortableBackup(db))!==text(before)||rawBefore!==await raw())throw new Error('唯讀檢查期間帳本改變');
    result.textContent='PASS：0.13.12-v2 支出比較、Chart.js、搜尋／月曆、定期支出、CSV、持久保存與完整備份共 14 個離線資源；原始資料列、metadata 與備份逐位元組不變'+(namesBefore.some(name=>name.startsWith('local-first-offline-')&&name!==version)?'；原生 Service Worker 更新完成':'');
  }catch(error){result.textContent='FAIL：'+error.message;}
  finally{button.disabled=false;}
};
button.click();
window.addEventListener('pagehide',()=>db.close());
