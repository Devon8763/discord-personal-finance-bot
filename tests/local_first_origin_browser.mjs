import { DB_NAME } from '../local-first/ledger.mjs';
const result=document.querySelector('#result'),details=document.querySelector('#details'),button=document.querySelector('#check');
const hash=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');
const encode=value=>new TextEncoder().encode(JSON.stringify(value,(_key,item)=>typeof item==='bigint'?{$bigint:item.toString()}:item));
async function snapshot(){
  const databases=(await indexedDB.databases()).sort((a,b)=>a.name.localeCompare(b.name));
  let rows=null;
  if(databases.some(db=>db.name===DB_NAME)){
    const db=await new Promise((resolve,reject)=>{const request=indexedDB.open(DB_NAME);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject();request.onupgradeneeded=()=>request.transaction.abort();});
    try{rows=await new Promise((resolve,reject)=>{const names=Array.from(db.objectStoreNames),records={},tx=db.transaction(names,'readonly');
      tx.oncomplete=()=>resolve(records);tx.onabort=()=>reject();
      for(const name of names){const request=tx.objectStore(name).getAll();request.onsuccess=()=>{records[name]=request.result;};}
    });}finally{db.close();}
  }
  const cacheRows=[];
  for(const name of (await caches.keys()).filter(name=>name.startsWith('local-first-offline-')).sort()){
    const cache=await caches.open(name);
    for(const request of await cache.keys()){const response=await cache.match(request);cacheRows.push([name,request.url,response.status,Array.from(response.headers),await hash(await response.arrayBuffer())]);}
  }
  const workers=(await navigator.serviceWorker.getRegistrations()).map(worker=>[worker.scope,worker.active?.scriptURL]).sort();
  return {digest:await hash(encode({databases,rows,cacheRows,workers})),present:rows!==null,cacheCount:cacheRows.length};
}
let before;
try{
  if(location.origin!=='http://127.0.0.1:8767')throw new Error();
  before=await snapshot();
  details.textContent=`舊合成帳本存在：${before.present}；靜態快取項目：${before.cacheCount}；SHA-256：${before.digest}`;
  result.textContent='PASS：已建立唯讀基準（未建立、升級或修改帳本）';button.disabled=false;
}catch{result.textContent='FAIL：無法建立唯讀基準';}
button.addEventListener('click',async()=>{
  button.disabled=true;
  try{
    const after=await snapshot();
    if(after.digest!==before.digest)throw new Error();
    let readable=false;
    try{await fetch('http://127.0.0.1:8768/local-first/');readable=true;}catch{}
    if(readable)throw new Error();
    result.textContent='PASS：舊來源帳本、metadata、快取與 Service Worker 逐位元組比對不變；跨來源讀取被拒絕';
  }catch{result.textContent='FAIL：來源隔離或唯讀比對失敗';}
  finally{button.disabled=false;}
});
