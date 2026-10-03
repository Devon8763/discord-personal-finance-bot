import {readBackup,validateStorageLimits,MAX_BYTES} from './backup.mjs';

const MAGIC=new Uint8Array([76,76,66,75,69,78,67,0]); // LLBKENC\0; independent envelope, not JSON v2.
const HEADER=47, TAG=16, ITERATIONS=600000;
export const MAX_ENCRYPTED_BYTES=MAX_BYTES+HEADER+TAG;
export class EncryptedBackupError extends Error {
  constructor(message='加密備份格式或參數不受支援，請選擇有效的備份檔。'){super(message);}
}
function passwordBytes(password,creating=false){
  if(typeof password!=='string'||!password.length||password.length>1024)throw new EncryptedBackupError('請輸入備份密碼（最多 1024 UTF-8 bytes）。');
  const bytes=new TextEncoder().encode(password);
  if(bytes.length>1024||new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes)!==password)throw new EncryptedBackupError('密碼編碼或長度不符合規則。');
  if(creating&&Array.from(password).length<12)throw new EncryptedBackupError('新備份密碼至少需要 12 個字元，建議使用較長且不重複的密碼。');
  return bytes;
}
export function confirmBackupPassword(password,confirmation){
  passwordBytes(password,true).fill(0);
  if(password!==confirmation)throw new EncryptedBackupError('兩次輸入的密碼不一致。');
}
export const isEncryptedBackup=bytes=>bytes instanceof Uint8Array&&bytes.length>=MAGIC.length&&MAGIC.every((value,index)=>bytes[index]===value);
export function inspectEncryptedBackup(bytes){
  if(!isEncryptedBackup(bytes)||bytes.length<HEADER+TAG||bytes.length>MAX_ENCRYPTED_BYTES)throw new EncryptedBackupError();
  const header=bytes.slice(0,HEADER),view=new DataView(header.buffer);
  // V1 supports only this fixed work factor: reject attacker-controlled cost before any KDF.
  const length=view.getUint32(43);
  if(header[8]!==1||header[9]!==1||header[10]!==1||view.getUint32(11)!==ITERATIONS||
      !length||length>MAX_BYTES||bytes.length!==HEADER+length+TAG)throw new EncryptedBackupError();
  return header;
}
async function key(password,header,usage,creating=false){
  const bytes=passwordBytes(password,creating);
  let material;
  try{material=await crypto.subtle.importKey('raw',bytes,'PBKDF2',false,['deriveKey']);}
  finally{bytes.fill(0);}
  return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:header.slice(15,31),iterations:ITERATIONS},
    material,{name:'AES-GCM',length:256},false,[usage]);
}
const parameters=header=>({name:'AES-GCM',iv:header.slice(31,43),additionalData:header,tagLength:128});
export async function encryptBackup(payload,password){
  validateStorageLimits(readBackup(payload));
  const header=new Uint8Array(HEADER),view=new DataView(header.buffer);
  header.set(MAGIC);header[8]=1;header[9]=1;header[10]=1;view.setUint32(11,ITERATIONS);
  crypto.getRandomValues(header.subarray(15,31));crypto.getRandomValues(header.subarray(31,43));view.setUint32(43,payload.length);
  const derived=await key(password,header,'encrypt',true);
  const cipher=new Uint8Array(await crypto.subtle.encrypt(parameters(header),derived,payload));
  const output=new Uint8Array(HEADER+cipher.length);output.set(header);output.set(cipher,HEADER);return output;
}
export async function decryptBackup(payload,password){
  const header=inspectEncryptedBackup(payload),derived=await key(password,header,'decrypt');
  let plain;
  try{plain=new Uint8Array(await crypto.subtle.decrypt(parameters(header),derived,payload.subarray(HEADER)));}
  catch{throw new EncryptedBackupError('密碼錯誤或備份檔已損壞，無法解密。');}
  // Authentication is not ledger validation; keep the one existing JSON v1 contract.
  validateStorageLimits(readBackup(plain));return plain;
}
