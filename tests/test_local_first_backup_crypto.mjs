import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pbkdf2Sync,createDecipheriv} from 'node:crypto';
import {readBackup,writeBackup,MAX_BYTES} from '../local-first/backup.mjs';
const url=new URL('../local-first/backup-crypto.mjs',import.meta.url);
const fixture=new Uint8Array(await readFile(new URL('fixtures/portable_life_ledger.json',import.meta.url)));
const password='合成 test password 123';
const load=async()=>{assert.ok(await readFile(url).catch(()=>null),'Missing encrypted backup module');return import(url.href);};
test('encrypted round-trip preserves exact JSON v1 bytes, all nine sections and BigInt',async()=>{
  const b=await load(),encrypted=await b.encryptBackup(fixture,password);
  assert.equal(b.isEncryptedBackup(encrypted),true);assert.equal(encrypted.length,fixture.length+63);
  assert.deepEqual(await b.decryptBackup(encrypted,password),fixture);
  assert.deepEqual(readBackup(await b.decryptBackup(encrypted,password)),readBackup(fixture));
  assert.equal(Object.keys(readBackup(fixture).data).length,9);
  assert.equal(readBackup(fixture).data.expenses[0].revision,9007199254740993n);
  assert.deepEqual(writeBackup(readBackup(await b.decryptBackup(encrypted,password))),writeBackup(readBackup(fixture)));
});
test('two exports have new salt, IV and ciphertext; password whitespace and Unicode stay exact',async()=>{
  const b=await load(),first=await b.encryptBackup(fixture,password),second=await b.encryptBackup(fixture,password);
  assert.notDeepEqual(first.slice(15,31),second.slice(15,31));assert.notDeepEqual(first.slice(31,43),second.slice(31,43));assert.notDeepEqual(first.slice(47),second.slice(47));
  const exact='  密碼合成字元 e\u0301 12345  ',encrypted=await b.encryptBackup(fixture,exact);
  assert.deepEqual(await b.decryptBackup(encrypted,exact),fixture);
  await assert.rejects(b.decryptBackup(encrypted,exact.trim()));await assert.rejects(b.decryptBackup(encrypted,exact.normalize('NFC')));
});
test('wrong password, tampered header, ciphertext and tag all reject safely',async()=>{
  const b=await load(),encrypted=await b.encryptBackup(fixture,password);
  await assert.rejects(b.decryptBackup(encrypted,'WRONG SECRET'),error=>error.message==='密碼錯誤或備份檔已損壞，無法解密。');
  for(const offset of [0,8,9,10,11,15,31,43,47,encrypted.length-1]){
    const changed=encrypted.slice();changed[offset]^=1;
    await assert.rejects(b.decryptBackup(changed,password),error=>!error.message.includes(password)&&!error.message.includes('OperationError'));
  }
});
test('malicious size, iteration counts, unknown versions and truncated files reject before KDF',async t=>{
  const b=await load(),encrypted=await b.encryptBackup(fixture,password);let calls=0;
  t.mock.method(crypto.subtle,'deriveKey',()=>{calls++;throw new Error('Should not derive');});
  for(const iterations of [0,1,599999,600001,0xffffffff]){const changed=encrypted.slice();new DataView(changed.buffer).setUint32(11,iterations);await assert.rejects(b.decryptBackup(changed,password));}
  const over=encrypted.slice();new DataView(over.buffer).setUint32(43,MAX_BYTES+1);
  for(const bytes of [over,new Uint8Array(b.MAX_ENCRYPTED_BYTES+1),encrypted.slice(0,46),encrypted.slice(0,-1),new Uint8Array([...encrypted,0])])await assert.rejects(b.decryptBackup(bytes,password));
  const future=encrypted.slice();future[8]=2;await assert.rejects(b.decryptBackup(future,password));assert.equal(calls,0);
});
test('new passwords require 12 codepoints, matching confirmation and bounded valid UTF8',async()=>{
  const b=await load();
  for(const value of ['','short','12345678901','\ud800'.repeat(12),'密'.repeat(342)])assert.throws(()=>b.confirmBackupPassword(value,value));
  assert.throws(()=>b.confirmBackupPassword(password,password+'x'));b.confirmBackupPassword('密'.repeat(12),'密'.repeat(12));
  b.confirmBackupPassword('a'.repeat(1024),'a'.repeat(1024));assert.throws(()=>b.confirmBackupPassword('a'.repeat(1025),'a'.repeat(1025)));
});
test('validly authenticated but invalid ledger JSON still uses existing validation',async()=>{
  const b=await load(),encrypted=await b.encryptBackup(fixture,password),header=encrypted.slice(0,47);
  const invalid=new TextEncoder().encode('{"format":"life-ledger-backup","version":1,"data":{}}');new DataView(header.buffer).setUint32(43,invalid.length);
  const keyMaterial=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:header.slice(15,31),iterations:600000},keyMaterial,{name:'AES-GCM',length:256},false,['encrypt']);
  const cipher=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv:header.slice(31,43),additionalData:header,tagLength:128},key,invalid));
  await assert.rejects(b.decryptBackup(new Uint8Array([...header,...cipher]),password));
  await assert.rejects(b.encryptBackup(invalid,password));assert.equal(b.isEncryptedBackup(fixture),false);
});
test('leading Unicode BOM in passwords remains exact without normalization',async()=>{
  const b=await load(),exact='\ufeffabcdefghijklm';
  b.confirmBackupPassword(exact,exact);
  const encrypted=await b.encryptBackup(fixture,exact);
  assert.deepEqual(await b.decryptBackup(encrypted,exact),fixture);
  await assert.rejects(b.decryptBackup(encrypted,exact.slice(1)));
});
test('independent Node crypto reads documented header and exact maximum size is bounded',async()=>{
  const b=await load(),encrypted=await b.encryptBackup(fixture,password),header=encrypted.slice(0,47);
  const derived=pbkdf2Sync(password,header.slice(15,31),new DataView(header.buffer).getUint32(11),32,'sha256');
  const decipher=createDecipheriv('aes-256-gcm',derived,header.slice(31,43));
  decipher.setAAD(header);decipher.setAuthTag(encrypted.slice(-16));
  assert.deepEqual(new Uint8Array(Buffer.concat([decipher.update(encrypted.slice(47,-16)),decipher.final()])),fixture);
  const maximum=new Uint8Array(b.MAX_ENCRYPTED_BYTES);maximum.set(header);new DataView(maximum.buffer).setUint32(43,MAX_BYTES);
  assert.equal(b.inspectEncryptedBackup(maximum).length,47);
  assert.throws(()=>b.inspectEncryptedBackup(new Uint8Array([...header,...encrypted.slice(47),0])));
});
