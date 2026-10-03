import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as rules from '../local-first/rules.mjs';
import * as ledger from '../local-first/ledger.mjs';
import { emptyData, readBackup, writeBackup, MAX_INTEGER, MAX_BYTES } from '../local-first/backup.mjs';

const fixture = () => ({ format:'local-first-test-ledger', version:2, owner:'local-test-owner',
  ...readBackup(new Uint8Array(readFileSync(new URL('fixtures/portable_life_ledger.json', import.meta.url)))).data });
const blank = () => ({ ...emptyData(), payment_sources:[{id:'p_cash',name:'現金',active:1}] });

test('category names follow Python strip, codepoints, newline and reserved-name rules', () => {
  assert.equal(typeof rules.categoryName, 'function');
  for (const [value, expected] of [['\u0085餐飲\u001c','餐飲'], ['😀'.repeat(20),'😀'.repeat(20)], ['\ufeff','\ufeff'], ['<svg/onload=1>','<svg/onload=1>']]) assert.equal(rules.categoryName(value), expected);
  for (const value of ['', ' ', '總額', '字'.repeat(21), '中\n文', '中\r文', null, 1]) assert.throws(() => rules.categoryName(value), rules.ValidationError);
});

test('category add and disable preserve defaults and historical records', () => {
  assert.equal(typeof ledger.changeCategory, 'function');
  const state = fixture(), before = structuredClone(state);
  const added = ledger.changeCategory(state, 'add', null, ' 測試分類 ');
  assert.deepEqual(added.categories.at(-1), {name:'測試分類',active:1});
  assert.deepEqual(state, before);
  const disabled = ledger.changeCategory(state, 'disable', '居住');
  assert.equal(rules.categoryOptions(disabled).find(row=>row.name==='居住').active, 0);
  for (const section of ['expenses','budgets','recurring_rules','recurring_versions','shortcuts','actions']) assert.deepEqual(disabled[section], state[section]);
  for (const name of ['餐飲','交通','自訂停用']) assert.throws(()=>ledger.changeCategory(state,'add',null,name));
  assert.throws(()=>ledger.changeCategory(state,'disable','交通'));
});

test('builtin rename updates voided expenses, all budgets, versions, shortcuts and undo before; revisions stay exact', () => {
  assert.equal(typeof ledger.changeCategory, 'function');
  const state = fixture();
  state.budgets.push({month:'2020-01',category:'居住',cents:'0'},{month:'2029-01',category:'居住',cents:'9007199254740993'});
  state.shortcuts[0].category='居住';
  state.actions.push({id:'a8',expense_id:'e2',undone:1,before:{...state.expenses[1],revision:0}});
  const changed = ledger.changeCategory(state,'rename','居住','房屋');
  assert.deepEqual(changed.categories.filter(row=>['居住','房屋'].includes(row.name)),[{name:'居住',active:0},{name:'房屋',active:1}]);
  assert.equal(rules.categoryOptions(changed).find(row=>row.name==='餐飲').active,1);
  assert.equal(changed.expenses[1].category,'房屋'); assert.equal(changed.expenses[1].voided,1); assert.equal(changed.expenses[1].revision,2);
  assert.equal(changed.recurring_rules[0].revision,9007199254740995n);
  assert.equal(changed.recurring_rules[1].revision,4);
  assert.equal(changed.recurring_versions[0].category,'房屋');
  assert.equal(changed.recurring_versions[1].category,'自訂停用');
  assert.equal(changed.shortcuts[0].category,'房屋');
  assert.equal(changed.actions.at(-1).before.category,'房屋'); assert.equal(changed.actions.at(-1).before.revision,0);
  assert.deepEqual(changed.actions.map(row=>row.id),['a1','a2','a3','a4','a5','a6','a7','a8']);
  assert.deepEqual(changed.budgets.slice(-2),[{month:'2020-01',category:'房屋',cents:'0'},{month:'2029-01',category:'房屋',cents:'9007199254740993'}]);
});

test('custom rename rekeys only its override; version-only reference increments parent once', () => {
  assert.equal(typeof ledger.changeCategory, 'function');
  const state=fixture(); state.categories.push({name:'自訂',active:1}); state.recurring_versions[1].category='自訂';
  const changed=ledger.changeCategory(state,'rename','自訂','新版');
  assert.deepEqual(changed.categories.at(-1),{name:'新版',active:1});
  assert.equal(changed.recurring_rules[0].revision,9007199254740995n);
  assert.equal(changed.recurring_rules[0].category,'居住');
  assert.equal(changed.recurring_versions[1].category,'新版');
});

test('category refuses every effective or historical merge, corrupt snapshot and revision overflow without mutation', () => {
  assert.equal(typeof ledger.changeCategory, 'function');
  for (const section of ['categories','expenses','budgets','recurring_rules','recurring_versions','shortcuts','actions']) {
    const state=fixture();
    if(section==='categories') state.categories.push({name:'衝突',active:0});
    else if(section==='actions') state.actions[5].before.category='衝突';
    else state[section][0].category='衝突';
    const before=structuredClone(state);
    assert.throws(()=>ledger.changeCategory(state,'rename','餐飲','衝突'));
    assert.deepEqual(state,before);
  }
  for (const corrupt of [state=>state.actions[5].before='bad', state=>state.expenses[1].revision=MAX_INTEGER, state=>state.recurring_rules[0].revision=MAX_INTEGER]) {
    const state=fixture(); corrupt(state); const before=structuredClone(state);
    assert.throws(()=>ledger.changeCategory(state,'rename','居住','房屋')); assert.deepEqual(state,before);
  }
  for (const [old,name] of [['交通','新版'],['不存在','新版'],['餐飲','餐飲'],['餐飲','總額']]) assert.throws(()=>ledger.changeCategory(fixture(),'rename',old,name));
});

test('category rejects added override at total row limit and rename that exceeds backup bytes', () => {
  assert.equal(typeof ledger.changeCategory, 'function');
  const state=blank(); state.categories=Array.from({length:99999},(_,i)=>({name:'c'+i,active:1}));
  assert.throws(()=>ledger.changeCategory(state,'add',null,'上限'));
  // 64 MiB valid text-heavy history; renaming expands every occurrence beyond the byte limit.
  const large=blank(); const row={id:'e0',spent_on:'2025-01-01',cents:'1',category:'餐飲',note:'字'.repeat(4096),source:'manual',recurring_id:null,period:null,voided:0,payment_source_id:'p_cash',payment_source_name:'現金',kind:'consumption',revision:0};
  large.expenses=Array.from({length:5300},(_,i)=>({...row,id:'e'+i}));
  // Pad to just below the limit, retaining the format's per-text ceiling.
  const size=writeBackup({format:'life-ledger-backup',version:1,data:large}).byteLength;
  const extra=Math.floor((MAX_BYTES-size-20000)/12527);
  for(let i=0;i<extra;i++) large.expenses.push({...row,id:'extra'+i});
  assert.ok(writeBackup({format:'life-ledger-backup',version:1,data:large}).byteLength < MAX_BYTES);
  assert.throws(()=>ledger.changeCategory(large,'rename','餐飲','字'.repeat(20)));
});

test('payment names follow Python control, strip and 30-codepoint rules', () => {
  assert.equal(typeof rules.paymentName,'function');
  assert.equal(rules.paymentName('\u0085 合成卡 \u001c'),'合成卡');
  assert.equal(rules.paymentName('😀'.repeat(30)),'😀'.repeat(30));
  assert.equal(rules.paymentName('\ufeff'),'\ufeff');
  for(const value of ['', ' ', '字'.repeat(31),'中\n文','中\t文','中\0文',null]) assert.throws(()=>rules.paymentName(value),rules.ValidationError);
});

test('payment preserves ID, expense and before snapshots, rejects reserved and disabled-name duplicates', () => {
  assert.equal(typeof ledger.changePayment,'function');
  const state=fixture(), original=structuredClone(state);
  const changed=ledger.changePayment(state,'rename','p3','新卡');
  assert.deepEqual(changed.payment_sources[2],{id:'p3',name:'新卡',active:0});
  for(const section of ['expenses','actions','shortcuts']) assert.deepEqual(changed[section],state[section]);
  assert.deepEqual(state,original);
  const added=ledger.changePayment(state,'add',null,' 新增卡 ');
  assert.match(added.payment_sources.at(-1).id,/^p_[a-z0-9-]+$/);assert.equal(added.payment_sources.at(-1).name,'新增卡');
  assert.equal(added.payment_sources.at(-1).active,1);
  const disabled=ledger.changePayment(added,'disable',added.payment_sources.at(-1).id);
  assert.equal(disabled.payment_sources.at(-1).active,0);assert.deepEqual(disabled.expenses,state.expenses);
  for(const name of ['現金','未指定','改名後信用卡']) assert.throws(()=>ledger.changePayment(state,'add',null,name));
  for(const id of ['p1','p2']) for(const op of ['rename','disable']) assert.throws(()=>ledger.changePayment(state,op,id,'更改'));
  assert.throws(()=>ledger.changePayment(state,'rename','p3','現金'));
  assert.throws(()=>ledger.changePayment(state,'disable','p3'));
  assert.throws(()=>ledger.changePayment(state,'rename','missing','更改'));
});

test('settings immediately govern new input and retain original inactive category/payment when editing', () => {
  assert.equal(typeof ledger.changePayment,'function');
  let state=ledger.changeCategory(blank(),'add',null,'合成');
  state=ledger.changePayment(state,'add',null,'合成卡');
  const payment=state.payment_sources.at(-1);
  const input={amount:'12.34',note:'合成',spent_on:'2025-01-01',category:'合成',payment_source_id:payment.id};
  const previous=rules.validateInput(input,state,'2025-01-01');
  state=ledger.changePayment(state,'rename',payment.id,'新名');
  assert.equal(rules.validateInput(input,state,'2025-01-01').payment_source_name,'新名');
  state=ledger.changeCategory(state,'disable','合成');state=ledger.changePayment(state,'disable',payment.id);
  assert.throws(()=>rules.validateInput(input,state,'2025-01-01'));
  const retained=rules.validateInput({...input,payment_source_id:''},state,'2025-01-01',previous);
  assert.equal(retained.category,'合成');assert.equal(retained.payment_source_name,'合成卡');
  assert.throws(()=>rules.validateInput({...input,category:'餐飲'},state,'2025-01-01'));
});
