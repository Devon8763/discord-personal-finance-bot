import {decryptBackup} from '/export/ledger/modules/backup-crypto.mjs';
import {DB_NAME,openDatabase,restorePortableBackup,exportPortableBackup,readLedgerIfPresent} from '/tests/ledger/ledger.mjs';
import {readBackup,writeBackup} from '/tests/ledger/backup.mjs';
const q=s=>document.querySelector(s),lines=[],check=(ok,message)=>{if(!ok)throw new Error(message);};
const report=document.createElement('pre');report.id='migration-test-result';document.body.prepend(report);
const wait=predicate=>new Promise((resolve,reject)=>{if(predicate()){resolve();return;}const observer=new MutationObserver(()=>{if(predicate()){observer.disconnect();clearTimeout(timer);resolve();}}),timer=setTimeout(()=>{observer.disconnect();reject(new Error('UI timeout'));},12000);observer.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true});});
const text=bytes=>new TextDecoder().decode(bytes),secret='Synthetic migration password 123';
const originalFetch=window.fetch.bind(window),blobs=[],requests=[];let fault='',release;
const originalCreate=URL.createObjectURL,originalClick=HTMLAnchorElement.prototype.click;
URL.createObjectURL=blob=>{blobs.push(blob);return originalCreate(blob);};HTMLAnchorElement.prototype.click=function(){};
window.fetch=async (url,options)=>{
  if(url==='/export/ledger/payload'){
    requests.push(options);
    if(fault==='export')return new Response('PRIVATE',{status:503});
    if(fault==='oversize')return new Response('{}',{headers:{'content-type':'application/json','content-length':'67108865'}});
    if(fault==='invalid')return new Response('{}',{headers:{'content-type':'application/json'}});
    if(fault==='pending')await new Promise(resolve=>{release=resolve;});
  }
  return originalFetch(url,options);
};
const setPasswords=(a=secret,b=a)=>{q('#backup-password').value=a;q('#backup-confirmation').value=b;};
const submit=()=>q('#ledger-backup-form').requestSubmit();
let db;
try{
  const csrf=q('[name=csrf_token]').value;
  const fetchPayload=async()=>new Uint8Array(await (await originalFetch('/export/ledger/payload',{method:'POST',body:new URLSearchParams({csrf_token:csrf})})).arrayBuffer());
  const before=await fetchPayload();
  await import('/export/ledger/modules/download.mjs');
  setPasswords(secret,'wrong');submit();check(requests.length===0&&blobs.length===0&&q('#backup-password').value===secret,'Mismatch fetched or lost draft');lines.push('password confirmation rejects before fetching; input retained');
  for(const mode of ['export','oversize','invalid']){
    fault=mode;setPasswords();submit();await wait(()=>!q('#backup-download').disabled);
    check(blobs.length===0&&q('#backup-password').value===secret&&!q('#backup-error').textContent.includes('PRIVATE'),'Failure downloads or leaks/loses input');
  }fault='';lines.push('export, capacity and invalid-format failures never download plaintext');
  const originalEncrypt=crypto.subtle.encrypt.bind(crypto.subtle);
  crypto.subtle.encrypt=()=>Promise.reject(new Error('PRIVATE-CRYPTO'));setPasswords();submit();await wait(()=>!q('#backup-download').disabled);crypto.subtle.encrypt=originalEncrypt;
  check(blobs.length===0&&q('#backup-password').value===secret,'Crypto failure downloads or loses password');lines.push('encryption failure has no plaintext fallback');
  fault='pending';setPasswords();submit();submit();await wait(()=>typeof release==='function');q('#backup-cancel').click();release();await wait(()=>!q('#backup-download').disabled);fault='';
  check(blobs.length===0&&q('#backup-password').value===''&&q('#backup-status').textContent.includes('已取消'),'Canceled request downloaded or retained password');lines.push('pending duplicate submission and cancel discard the result');
  setPasswords();submit();await wait(()=>!q('#backup-download').disabled);
  check(blobs.length===1&&q('#backup-password').value===''&&q('#backup-status').textContent.includes('尚未確認'),'Missing encrypted request or false saved claim');
  check(requests.every(r=>r.method==='POST'&&r.cache==='no-store'&&r.redirect==='error'&&[...r.body.keys()].join()==='csrf_token'&&!r.body.toString().includes(secret)),'Password sent to server');
  const cipher=new Uint8Array(await blobs[0].arrayBuffer()),plain=await decryptBackup(cipher,secret);
  check(text(plain)===text(before),'Export bytes changed');lines.push('real Web response encrypted locally; only CSRF posted; no saved claim');
  db=await openDatabase(DB_NAME+'-portable-checks');check(await readLedgerIfPresent(db)===null,'Test target not blank');
  for(const bytes of [cipher,cipher.map((b,i)=>i===47?b^1:b)]){
    let rejected=false;try{await decryptBackup(bytes,bytes===cipher?'wrong':secret);}catch{rejected=true;}
    check(rejected&&await readLedgerIfPresent(db)===null,'Bad password/tamper modified blank ledger');
  }
  const add=IDBObjectStore.prototype.add;IDBObjectStore.prototype.add=function(...args){const req=add.apply(this,args);req.addEventListener('success',()=>this.transaction.abort());return req;};
  let rejected=false;try{await restorePortableBackup(db,plain);}catch{rejected=true;}finally{IDBObjectStore.prototype.add=add;}
  check(rejected&&await readLedgerIfPresent(db)===null,'Abort left partial ledger');
  await restorePortableBackup(db,plain);check(text(await exportPortableBackup(db))===text(writeBackup(readBackup(before))),'Nine sections changed');
  db.close();db=await openDatabase(DB_NAME+'-portable-checks');check(text(await exportPortableBackup(db))===text(writeBackup(readBackup(before))),'Reopen changed history');
  check(text(await fetchPayload())===text(before),'Export tests wrote Python ledger');lines.push('wrong password/tamper/abort leave blank; nine sections, BigInt, relations, revisions, voided history and order survive restore/reopen');
  check(!document.querySelector('img')&&!document.querySelector('[src^="https:"]'),'Untrusted content became executable/external');
  report.textContent='PASS：'+lines.length+' 組 Python Web → 原生 IndexedDB 合成互通（Blob 測試，不代表下載落地）\n'+lines.join('\n');
}catch(failure){report.textContent='FAIL：'+failure.message;throw failure;}
finally{db?.close();window.fetch=originalFetch;URL.createObjectURL=originalCreate;HTMLAnchorElement.prototype.click=originalClick;}
