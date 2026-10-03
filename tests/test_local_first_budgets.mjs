import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as rules from '../local-first/rules.mjs';
import * as browse from '../local-first/browse.mjs';
import * as ledger from '../local-first/ledger.mjs';
import { emptyData, readBackup, writeBackup } from '../local-first/backup.mjs';
import { portable } from '../local-first/idb.mjs';

const today = '2026-10-15', month = '2026-10';
const blank = () => ({format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData(),
  payment_sources:[{id:'p_cash',name:'現金',active:1}]});
const change = (state, op, category, value) => ledger.changeBudget(state,op,category,value,today);
const budget = (category,cents,period=month) => ({month:period,category,cents});

test('new TWD budgets follow Python ASCII positive integer and billion limit', () => {
  assert.equal(typeof rules.budgetCents,'function');
  for (const [input,expected] of [['1','100'],['000123','12300'],[1000000000,'100000000000']]) assert.equal(rules.budgetCents(input),expected);
  for (const input of ['', ' 1', '1 ', '+1', '1e3', '1.00', '1.5', '１', '0', '-1', '1000000001', 1.5, true, null]) assert.throws(()=>rules.budgetCents(input),rules.ValidationError);
});

test('optional total and categories, exact lower bound and independent clear preserve other months', () => {
  assert.equal(typeof ledger.changeBudget,'function');
  const state=blank();state.budgets=[budget('餐飲','0','2020-01'),budget('總額','9007199254740993','2029-01')];
  const before=structuredClone(state);
  let next=change(state,'set','餐飲','100');
  assert.equal(next.budgets.length,3);assert.deepEqual(state,before);
  assert.throws(()=>change(next,'set','總額','99'));
  next=change(next,'set','總額','100');
  assert.throws(()=>change(next,'set','交通','1'));
  next=change(next,'clear','總額');assert.equal(next.budgets.length,3);
  next=change(next,'set','交通','1');next=change(next,'sum','總額');
  assert.equal(next.budgets.at(-1).cents,'10100');
  next=change(next,'clear','餐飲');
  assert.deepEqual(next.budgets.slice(0,2),before.budgets);
  assert.throws(()=>change(next,'clear','餐飲'));
  assert.throws(()=>change(next,'set','不存在','1'));
  assert.throws(()=>change(next,'bad','總額','1'));
});

test('sum reuses current categories including disabled budgets and rejects zero, fractional and over-limit sums', () => {
  assert.equal(typeof ledger.changeBudget,'function');
  const state=blank();state.categories=[{name:'交通',active:0}];
  assert.throws(()=>change(state,'sum','總額'));
  state.budgets=[budget('交通','0')];assert.throws(()=>change(state,'sum','總額'));
  state.budgets[0].cents='100';assert.equal(change(state,'sum','總額').budgets.at(-1).cents,'100');
  assert.throws(()=>change(state,'set','交通','1'));
  assert.deepEqual(change(state,'clear','交通').budgets,[]);
  for (const amount of ['101','100000000001']) {
    state.budgets[0].cents=amount;assert.throws(()=>change(state,'sum','總額'));
  }
  state.budgets=[budget('餐飲','100000000000'),budget('交通','100')];
  assert.throws(()=>change(state,'sum','總額'));
});

test('explicit historical zero remains configured; fractional history is exact and clear-only', () => {
  assert.equal(typeof ledger.changeBudget,'function');assert.equal(typeof browse.monthBudget,'function');
  const state=blank();state.budgets=[budget('總額','0'),budget('交通','0')];
  const summary=browse.monthBudget(state,today);
  assert.equal(summary.total.cents,0n);assert.equal(summary.categories[0].cents,0n);
  assert.throws(()=>change(state,'set','總額','0'));
  assert.deepEqual(change(state,'clear','交通').budgets,[budget('總額','0')]);
  state.budgets=[budget('餐飲','101')];
  assert.equal(browse.monthBudget(state,today).categories[0].cents,101n);
  assert.throws(()=>change(state,'set','餐飲','2'));
  assert.deepEqual(change(state,'clear','餐飲').budgets,[]);
});

test('read-only monthly overview includes exact booked four-source consumption, unbudgeted and inactive categories', () => {
  assert.equal(typeof browse.monthBudget,'function');
  const state=blank();state.categories=[{name:'交通',active:0}];
  const entry={spent_on:today,cents:'1234',category:'餐飲',kind:'consumption',voided:0};
  state.expenses=['manual','固定','訂閱','分期'].map((source,i)=>({...entry,id:String(i),source}));
  state.expenses.push({...entry,source:'manual',category:'其他',cents:'1'},
    {...entry,source:'manual',voided:1}, {...entry,source:'manual',spent_on:'2026-10-16'},
    {...entry,source:'manual',spent_on:'2026-09-15'}, {...entry,source:'manual',kind:'income'});
  let result=browse.monthBudget(state,today);assert.equal(result.spent,4937n);assert.equal(result.total,null);assert.deepEqual(result.categories,[]);
  state.budgets=[budget('總額','4900'),budget('餐飲','100'),budget('交通','0')];
  const before=structuredClone(state);result=browse.monthBudget(state,today);
  assert.equal(result.total.remaining,-37n);assert.equal(result.categories[0].spent,4936n);
  assert.equal(result.categories[1].spent,0n);assert.equal(result.categories[1].active,false);
  assert.deepEqual(state,before);assert.throws(()=>browse.monthBudget(state,'2026-02-30'));
});

test('corrupt data and full row capacity refuse budget changes; backup preserves exact history', () => {
  assert.equal(typeof ledger.changeBudget,'function');
  const broken=blank();broken.budgets=[budget('交通','-1')];assert.throws(()=>change(broken,'set','總額','1'));
  const full=blank();full.categories=Array.from({length:99999},(_,i)=>({name:'c'+i,active:1}));
  assert.throws(()=>change(full,'set','總額','1'));
  full.categories.pop();assert.equal(change(full,'set','總額','1').budgets.length,1);
  const fixture={...blank(),...readBackup(new Uint8Array(readFileSync(new URL('fixtures/portable_life_ledger.json',import.meta.url)))).data};
  const original=writeBackup(portable(fixture));
  const next=change(fixture,'set','總額','1000000000');
  assert.deepEqual(readBackup(writeBackup(portable(next))).data,nextData(next));
  assert.deepEqual(writeBackup(portable(fixture)),original);
});
test('Taiwan clock crosses month at UTC 16:00 without moving into pure rules', () => {
  const NativeDate=globalThis.Date;
  let instant='2026-09-30T15:59:59Z';
  globalThis.Date=class extends NativeDate {constructor(...args){super(...(args.length?args:[instant]));}};
  try {assert.equal(ledger.taiwanToday(),'2026-09-30');instant='2026-09-30T16:00:00Z';assert.equal(ledger.taiwanToday(),'2026-10-01');}
  finally {globalThis.Date=NativeDate;}
});
function nextData(state) { const {format,version,owner,...data}=state;return data; }
