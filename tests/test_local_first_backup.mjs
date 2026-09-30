import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import * as vendor from '../local-first/vendor/lossless-json-4.3.1/lossless-json.js';
const { stringify } = vendor.default;
const url = new URL('../local-first/backup.mjs', import.meta.url);
const fixture = readFileSync(new URL('fixtures/portable_life_ledger.json', import.meta.url));
const bytes = value => new TextEncoder().encode(typeof value === 'string' ? value : stringify(value));
async function module() { assert.ok(existsSync(url), 'Missing pure portable backup format'); return import(url); }
function empty() { return { format: 'life-ledger-backup', version: 1, data: { expenses: [], categories: [], payment_sources: [], budgets: [], recurring_rules: [], recurring_versions: [], shortcuts: [], actions: [], settings: { reminder_levels: null, recording_started_on: null } } }; }

test('full Python fixture preserves all fields and large integers without classes', async () => {
  const b = await module(); const bundle = b.readBackup(fixture);
  assert.equal(Object.keys(bundle.data).length, 9);
  assert.equal(bundle.data.expenses[0].revision, 9007199254740993n);
  assert.equal(bundle.data.shortcuts[0].position, 9007199254740995n);
  assert.equal(bundle.data.recurring_rules[2].periods, 9007199254740997n);
  assert.deepEqual(b.readBackup(b.writeBackup(bundle)), bundle);
  assert.deepEqual(structuredClone(bundle), bundle);
  const output = new TextDecoder().decode(b.writeBackup(bundle));
  assert.match(output, /"revision":9007199254740993[,}]/);
  assert.doesNotMatch(output, /user_id|owner|source-discord-identity|LosslessNumber/);
});
test('empty and settings-only preserve absent vs explicit settings', async () => {
  const b = await module(); const bundle = empty();
  assert.deepEqual(b.readBackup(bytes(bundle)), bundle);
  bundle.data.settings.reminder_levels = [];
  assert.deepEqual(b.readBackup(b.writeBackup(bundle)), bundle);
  bundle.data.settings.recording_started_on = '0001-01-01';
  assert.deepEqual(b.readBackup(b.writeBackup(bundle)), bundle);
});
test('reject every duplicate key including equal values and swallowed prototype keys', async () => {
  const b = await module(); const source = stringify(empty());
  const cases = [
    source.replace('"version":1', '"version":1,"version":1'),
    source.replace('"format":"life-ledger-backup"', '"format":"life-ledger-backup","format":"life-ledger-backup"'),
    source.replace('"expenses":[]', '"expenses":[],"expenses":[]'),
    source.replace('"settings":', '"settings":{"reminder_levels":null,"recording_started_on":null},"settings":'),
    source.replace('"version":1', '"version":1,"v\\u0065rsion":1'),
    ...['null', '0', 'true', '"text"'].map(value => source.replace('"version":1', '"version":1,"__proto__":' + value)),
    source.replace('"version":1', '"version":1,"\\u005f\\u005fproto__":null'),
    source.replace('"reminder_levels":null', '"reminder_levels":null,"__proto__":0'),
  ];
  for (const input of cases) assert.throws(() => b.readBackup(bytes(input)), b.BackupError);
});
test('reject invalid UTF8, BOM, syntax, duplicate keys and noninteger numeric tokens', async () => {
  const b = await module();
  for (const input of [new Uint8Array([255]), bytes('\ufeff{}'), bytes('{'), bytes('{"x":1,"x":2}'), bytes('{"x":NaN}'), bytes('{"x":Infinity}'), bytes('{"x":1.0}'), bytes('{"x":1e2}'), bytes('{"x":-Infinity}'), bytes('['.repeat(13)+']'.repeat(13))]) assert.throws(() => b.readBackup(input), b.BackupError);
  for (const value of [null, '', {}, new ArrayBuffer(0), new Uint8Array(0), new Uint8Array(16*1024*1024+1)]) assert.throws(() => b.readBackup(value), b.BackupError);
});
test('reject field sets, types, references, immutable snapshots and unique markers', async () => {
  const b = await module(); const source = b.readBackup(fixture);
  const mutations = [x=>x.version=true, x=>x.version=2, x=>x.owner='injected', x=>delete x.data.settings,
    x=>x.data.expenses[0].cents='01', x=>x.data.expenses[0].cents='9223372036854775808', x=>x.data.expenses[0].revision=true,
    x=>x.data.expenses[0].revision='9007199254740993', x=>x.data.expenses[0].revision=9007199254740993,
    x=>x.data.expenses[0].payment_source_id=[], x=>x.data.expenses[0].spent_on='2025-02-30', x=>x.data.expenses[0].note='\ud800',
    x=>x.data.expenses[0].note='\0', x=>x.data.expenses[0].note='字'.repeat(4097), x=>x.data.categories[0].name='\u0085',
    x=>x.data.categories.push({name:'總額',active:1}), x=>x.data.payment_sources[0].active=0,
    x=>x.data.expenses.push({...x.data.expenses[1],id:'e_new'}), x=>x.data.expenses[1].source='訂閱',
    x=>x.data.expenses[1].period='2025-02', x=>x.data.recurring_rules[2].periods=0,
    x=>x.data.recurring_versions[0].recurring_id=x.data.recurring_rules[1].id,
    x=>x.data.budgets.push({...x.data.budgets[0]}), x=>x.data.shortcuts[0].payment_source_id='missing',
    x=>x.data.actions[0].expense_id='missing', x=>x.data.actions[5].before.source='固定',
    x=>x.data.settings.reminder_levels=[true], x=>x.data.settings.reminder_levels=[80,80], x=>x.data.settings.oauth='forbidden'];
  for (const mutate of mutations) { const value=structuredClone(source); mutate(value); assert.throws(()=>b.validateBackup(value),b.BackupError); }
});
test('legal historical data and Python whitespace/Unicode semantics remain intact', async () => {
  const b=await module(); const value=b.readBackup(fixture);
  value.data.expenses[0].note='😀'.repeat(4096);
  value.data.categories.push({name:'\ufeff',active:0});
  assert.deepEqual(b.readBackup(b.writeBackup(value)),value);
  assert.equal(value.data.budgets[0].cents,'0');
  assert.equal(value.data.expenses.at(-1).recurring_id,null);
});
test('format limits are independent from local 200 / 1000 limits', async () => {
  const b=await module(); const source=b.readBackup(fixture);const value=empty();
  value.data.expenses=Array.from({length:201},(_,i)=>({...source.data.expenses[0],id:'e_'+i,payment_source_id:null}));
  assert.doesNotThrow(()=>b.validateBackup(value));
  assert.throws(()=>b.validateStorageLimits(value),b.BackupError);
  value.data.expenses=value.data.expenses.slice(0,1);
  value.data.actions=Array.from({length:1001},(_,i)=>({id:'a_'+i,expense_id:'e_0',before:null,undone:0}));
  assert.doesNotThrow(()=>b.validateBackup(value)); assert.throws(()=>b.validateStorageLimits(value),b.BackupError);
  const many=empty();many.data.categories=Array.from({length:100001},(_,i)=>({name:'c'+i,active:0}));
  assert.throws(()=>b.validateBackup(many),b.BackupError);
});
test('valid UTF-8 JSON v1 above 16 MiB round-trips, but input above 64 MiB is rejected', async () => {
  const b = await module();
  const value = empty();
  const row = b.readBackup(fixture).data.expenses[0];
  value.data.expenses = Array.from({ length: 1400 }, (_, i) => ({
    ...row, id: 'e_large_' + i, note: '中'.repeat(4096), payment_source_id: null,
  }));
  const encoded = b.writeBackup(value);
  assert.ok(encoded.byteLength > 16 * 1024 * 1024);
  assert.ok(encoded.byteLength < 64 * 1024 * 1024);
  assert.deepEqual(b.readBackup(encoded), value);
  assert.throws(() => b.readBackup(new Uint8Array(64 * 1024 * 1024 + 1)), b.BackupError);
});
