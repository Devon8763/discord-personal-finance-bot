import { STORES, openLedgerDatabase, readSnapshot } from '../local-first/idb.mjs';
import { readBackup, writeBackup } from '../local-first/backup.mjs';

const prefix = 'discordbot-localfirst-synthetic-v1-storage-checks';
const lines = [];
function ok(value, message) { if (!value) throw new Error(message); }
function done(tx) { return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = () => reject(tx.error); }); }
function request(req) { return new Promise((resolve, reject) => { req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); }); }
async function erase(name) { await request(indexedDB.deleteDatabase(name)); }
async function seed(name, state, marker = 1) {
  const db = await new Promise((resolve, reject) => {
    const opening = indexedDB.open(name, 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore('ledger');
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error);
  });
  const tx = db.transaction('ledger', 'readwrite');
  tx.objectStore('ledger').add(state, 'local-test-owner');
  tx.objectStore('ledger').add(marker, 'created');
  await done(tx);
  db.close();
}
async function fixtureState() {
  const bytes = new Uint8Array(await (await fetch('/tests/fixtures/portable_life_ledger.json')).arrayBuffer());
  return { format: 'local-first-test-ledger', version: 2, owner: 'local-test-owner', ...readBackup(bytes).data };
}
try {
  const blankName = prefix + '-blank';
  await erase(blankName);
  let db = await openLedgerDatabase(blankName);
  ok(db.version === 2 && await readSnapshot(db, true) === null, 'Blank v2 must stay blank on read');
  db.close(); lines.push('blank v2 remains uninitialized');

  const original = await fixtureState();
  const name = prefix + '-full';
  await erase(name); await seed(name, original);
  db = await openLedgerDatabase(name);
  let restored = await readSnapshot(db);
  ok(restored.format === original.format && restored.version === original.version && restored.owner === original.owner, 'State header');
  const encode = state => writeBackup({ format:'life-ledger-backup', version:1,
    data: Object.fromEntries(['expenses','categories','payment_sources','budgets','recurring_rules',
      'recurring_versions','shortcuts','actions','settings'].map(section => [section,state[section]])) });
  ok(new TextDecoder().decode(encode(restored)) === new TextDecoder().decode(encode(original)), 'Exact backup bytes');
  ok(restored.expenses[0].revision === original.expenses[0].revision, 'Large revision');
  ok(!db.objectStoreNames.contains('ledger'), 'Old store removed after success');
  db.close(); db = await openLedgerDatabase(name);
  restored = await readSnapshot(db);
  ok(restored.actions.map(x=>x.id).join() === original.actions.map(x=>x.id).join(), 'Action order after reopen');
  db.close(); lines.push('v1 full migration preserves bytes and order after reopen');

  const legacyName = prefix + '-legacy';
  const legacy = { format:'local-first-test-ledger', version:1, owner:'local-test-owner',
    expenses:[], actions:[], categories:[], payment_sources:[] };
  await erase(legacyName); await seed(legacyName, legacy);
  db = await openLedgerDatabase(legacyName);
  ok((await readSnapshot(db)).settings.reminder_levels === null, 'Legacy missing sections default');
  db.close(); lines.push('known legacy state gains empty sections');

  for (const [suffix, bad] of [['wrong-marker', original], ['unknown-version', {...original,version:3}],
    ['unknown-field', {...original,hidden:'bad'}]]) {
    const badName = prefix + '-' + suffix;
    await erase(badName); await seed(badName,bad,suffix==='wrong-marker'?2:1);
    let rejected=false;
    try { (await openLedgerDatabase(badName)).close(); } catch { rejected=true; }
    ok(rejected, suffix + ' must reject upgrade');
    const raw = await request(indexedDB.open(badName,1));
    ok(raw.objectStoreNames.contains('ledger'), suffix + ' must preserve legacy store');
    raw.close();
  }
  lines.push('invalid migration aborts without erasing old store');

  const futureName = prefix + '-future';
  await erase(futureName);
  const future = await request(indexedDB.open(futureName,3)); future.close();
  let futureRejected=false;
  try { (await openLedgerDatabase(futureName)).close(); } catch { futureRejected=true; }
  ok(futureRejected,'Future DB version must reject'); lines.push('future version rejected');

  const extraName = prefix + '-extra-store';
  await erase(extraName);
  const extraOpening = indexedDB.open(extraName,2);
  extraOpening.onupgradeneeded = () => {
    for (const section of [...STORES,'unexpected']) extraOpening.result.createObjectStore(section,{keyPath:'key'});
    extraOpening.transaction.objectStore('unexpected').add({key:'private',value:'preserve'});
  };
  (await request(extraOpening)).close();
  let extraRejected=false;
  try { (await openLedgerDatabase(extraName)).close(); } catch { extraRejected=true; }
  ok(extraRejected,'Unknown nonempty store must stop writes');
  const extraRaw=await request(indexedDB.open(extraName,2));
  const extraTx=extraRaw.transaction('unexpected','readonly');
  ok((await request(extraTx.objectStore('unexpected').get('private'))).value==='preserve','Unknown store survives rejection');
  extraRaw.close(); lines.push('unknown populated v2 store is preserved and refused');

  const blockedName = prefix + '-blocked';
  await erase(blockedName); await seed(blockedName,original);
  const old = await request(indexedDB.open(blockedName,1));
  old.onversionchange = () => {};
  let blocked=false;
  try { (await openLedgerDatabase(blockedName)).close(); } catch { blocked=true; }
  ok(blocked,'Old tab blocks upgrade');
  ok(old.objectStoreNames.contains('ledger'), 'Blocked DB unchanged');
  old.close();
  const blockedAfterClose=await request(indexedDB.open(blockedName,1));
  ok(blockedAfterClose.objectStoreNames.contains('ledger'),'Rejected blocked request must not upgrade later');
  blockedAfterClose.close(); lines.push('blocked upgrade preserves old store after old tab closes');

  document.querySelector('#result').textContent = 'PASS：' + lines.length + '組逐筆儲存驗證';
} catch (error) { document.querySelector('#result').textContent = 'FAIL：' + error.message; throw error; }
finally { document.querySelector('#details').textContent = lines.join('\n'); }
