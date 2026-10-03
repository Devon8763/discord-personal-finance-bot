import {confirmBackupPassword,encryptBackup,EncryptedBackupError} from './backup-crypto.mjs';
import {MAX_BYTES} from './backup.mjs';

const form=document.querySelector('#ledger-backup-form'),password=document.querySelector('#backup-password');
const confirmation=document.querySelector('#backup-confirmation'),button=document.querySelector('#backup-download');
const cancel=document.querySelector('#backup-cancel'),error=document.querySelector('#backup-error'),status=document.querySelector('#backup-status');
let busy=false,selection=0,request;
cancel.disabled=false;
if(crypto?.subtle)button.disabled=false;
else error.textContent='此瀏覽器無法使用安全加密；不會下載明文，請使用支援 Web Crypto 的本機瀏覽器。';
cancel.onclick=()=>{
  ++selection;request?.abort();password.value='';confirmation.value='';error.textContent='';status.textContent='已取消，未發出下載要求。';
};
form.onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  error.textContent='';status.textContent='';
  try{confirmBackupPassword(password.value,confirmation.value);}
  catch(failure){error.textContent=failure instanceof EncryptedBackupError?failure.message:'請檢查備份密碼。';return;}
  const current=++selection,secret=password.value;
  busy=true;button.disabled=true;password.disabled=true;confirmation.disabled=true;request=new AbortController();
  try{
    const response=await fetch('/export/ledger/payload',{method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',
      body:new URLSearchParams({csrf_token:form.elements.csrf_token.value}),signal:request.signal});
    if(current!==selection)return;
    if(!response.ok||!response.headers.get('content-type')?.startsWith('application/json'))throw new Error('Export unavailable');
    const size=response.headers.get('content-length');
    if(size!==null&&(!/^\d+$/.test(size)||BigInt(size)>BigInt(MAX_BYTES)))throw new Error('Export limit');
    const chunks=[];let length=0;
    const reader=response.body.getReader();
    try{while(true){const {value,done}=await reader.read();if(done)break;
      length+=value.length;if(length>MAX_BYTES)throw new Error('Export limit');chunks.push(value);
    }}finally{await reader.cancel();}
    if(current!==selection)return;
    const payload=new Uint8Array(length);let offset=0;for(const chunk of chunks){payload.set(chunk,offset);offset+=chunk.length;}
    const encrypted=await encryptBackup(payload,secret);
    if(current!==selection)return;
    const url=URL.createObjectURL(new Blob([encrypted],{type:'application/octet-stream'})),link=document.createElement('a');
    link.href=url;link.download='life-ledger-encrypted-backup-v1.llbk';
    try{link.click();}finally{setTimeout(()=>URL.revokeObjectURL(url),60000);}
    password.value='';confirmation.value='';
    status.textContent='已要求瀏覽器下載加密完整備份；尚未確認檔案已保存。請確認檔案存在並妥善保管備份與密碼。';
  }catch{if(current===selection)error.textContent='完整備份未產生。請確認登入、密碼及瀏覽器支援後重試；不會改下載明文。';}
  finally{busy=false;request=null;button.disabled=!crypto?.subtle;password.disabled=false;confirmation.disabled=false;}
};
