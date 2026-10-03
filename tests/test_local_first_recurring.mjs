import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyData,writeBackup,readBackup,MAX_INTEGER} from '../local-first/backup.mjs';
import {portable} from '../local-first/idb.mjs';
import * as ledger from '../local-first/ledger.mjs';
import * as rules from '../local-first/rules.mjs';
const initial=()=>({format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData()});
const form=(changes={})=>({kind:'訂閱',name:' 合成訂閱 ',amount:'.29',category:'居住',start_month:'2026-12',due_day:31,...changes});
const change=(state,op,id=null,revision=null,input=null,today='2026-12-15')=>ledger.changeRecurring(state,op,id,revision,input,today);
const add=(state=initial(),changes={},today)=>change(state,'add',null,null,form(changes),today);
const bytes=state=>writeBackup(portable(state));
const refuse=(state,op,id,revision,input,today)=>{const before=bytes(state);assert.throws(()=>change(state,op,id,revision,input,today));assert.deepEqual(bytes(state),before);};

test('subscription and installment creation retain exact fields, fixed-only versions and current/next start',()=>{
  let state=add();state=add(state,{kind:'分期',periods:600,start_month:'2027-01',amount:'1000000000'});
  assert.deepEqual(state.recurring_rules.map(row=>[row.kind,row.periods,row.start_month,row.cents]),[['訂閱',0,'2026-12','29'],['分期',600,'2027-01','100000000000']]);
  assert.equal(state.recurring_versions.length,0);assert.equal(state.expenses.length,0);assert.equal(state.actions.length,0);
  assert.equal(state.recurring_rules[0].name,' 合成訂閱 ');
  state=add(state,{kind:'固定'});assert.equal(state.recurring_versions.length,1);
});
test('new recurring validation rejects invalid periods, kind, category, start, day, names and money without mutation',()=>{
  for(const periods of [undefined,0,601,1.5,true,'2',NaN])refuse(initial(),'add',null,null,form({kind:'分期',periods}));
  for(const fields of [{kind:'其他'},{kind:'訂閱',periods:1},{kind:'固定',periods:1},{name:''},{name:'\u0085'},{name:'😀'.repeat(101)},
    {amount:'0'},{amount:'1.001'},{amount:'1000000000.01'},{category:'不存在'},{start_month:'2026-11'},{start_month:'2027-02'},{due_day:null},{due_day:0},{due_day:32},{due_day:true}])refuse(initial(),'add',null,null,form(fields));
  const state=initial();state.categories.push({name:'居住',active:0});refuse(state,'add',null,null,form());
});

test('all sources catch up across year and leap months with exact installment notes and no forecast',()=>{
  for(const day of [29,30,31]) {
    let state=add(initial(),{kind:'訂閱',due_day:day,start_month:'2027-12'},'2027-12-31');
    state=add(state,{kind:'分期',periods:3,due_day:day,start_month:'2027-12'},'2027-12-31');
    state=add(state,{kind:'固定',due_day:day,start_month:'2027-12'},'2027-12-31');
    state=change(state,'sync',null,null,null,'2028-02-28');assert.equal(state.expenses.length,6);
    state=change(state,'sync',null,null,null,'2028-02-29');assert.equal(state.expenses.length,9);
    assert.equal(state.expenses.filter(row=>row.spent_on==='2028-02-29').length,3);
    const payments=state.expenses.filter(row=>row.source==='分期');
    assert.deepEqual(payments.map(row=>row.note),[1,2,3].map(i=>` 合成訂閱 （第 ${i}/3 期）`));
    state=change(state,'sync',null,null,null,'2028-04-30');
    assert.equal(state.expenses.filter(row=>row.source==='分期').length,3);
    assert.equal(state.expenses.filter(row=>row.source==='訂閱').length,5);
    assert.equal(state.actions.length,state.expenses.length);assert.ok(state.actions.every(row=>row.before===null));
  }
});

test('completed and voided installments never revive; legacy null and inactive categories retain history',()=>{
  let state=add(initial(),{kind:'分期',periods:1,due_day:1});const id=state.recurring_rules[0].id;
  state.recurring_rules[0].due_day=null;state.categories.push({name:'居住',active:0});
  const unread=bytes(state);assert.equal(rules.recurringProgress(state,state.recurring_rules[0]).complete,false);assert.deepEqual(bytes(state),unread);
  state=change(state,'sync');assert.equal(state.expenses[0].spent_on,'2026-12-01');
  state.expenses[0].voided=1;state.actions[0].undone=1;
  assert.deepEqual(rules.recurringProgress(state,state.recurring_rules[0]),{posted:1n,total:1n,complete:true});
  const before=bytes(state);state=change(state,'sync',null,null,null,'2027-06-01');assert.deepEqual(bytes(state),before);
  assert.equal(state.recurring_rules[0].active,1);refuse(state,'stop',id,0);
});

test('stop catches only due installments then disables atomically',()=>{
  for(const kind of ['訂閱','分期']) {
    let state=add(initial(),{kind,periods:kind==='分期'?3:0,due_day:1});const id=state.recurring_rules[0].id;
    refuse(state,'stop',id,1);
    state=change(state,'stop',id,0,null,'2027-01-01');
    assert.equal(state.expenses.length,2);assert.equal(state.recurring_rules[0].active,0);assert.equal(state.recurring_rules[0].revision,1);
    refuse(state,'stop',id,1);assert.deepEqual(bytes(change(state,'sync',null,null,null,'2027-03-01')),bytes(state));
    const future=add(initial(),{kind,periods:kind==='分期'?1:0,start_month:'2027-01'});
    assert.equal(change(future,'stop',future.recurring_rules[0].id,0).expenses.length,0);
  }
});

test('subscription/installment edits replace next-month versions and delayed posting uses each due month',()=>{
  for(const kind of ['訂閱','分期'])for(const due_day of [29,30,31]) {
    let state=add(initial(),{kind,periods:kind==='分期'?3:0,start_month:'2027-12',due_day},'2027-12-15');
    const original=structuredClone(state.recurring_rules[0]),id=original.id;
    state=change(state,'update',id,0,{amount:'1.01',category:'醫療'},'2027-12-15');
    const versionId=state.recurring_versions[0].id;
    assert.deepEqual(state.recurring_rules[0],{...original,revision:1});
    assert.equal(state.recurring_versions[0].effective_month,'2028-01');
    assert.equal(rules.fixedView(state,state.recurring_rules[0],'2027-12').cents,'29');
    state=change(state,'update',id,1,{amount:'.37',category:'交通'},'2027-12-31');
    assert.equal(state.recurring_versions.length,1);assert.equal(state.recurring_versions[0].id,versionId);
    assert.equal(rules.fixedView(state,state.recurring_rules[0],'2027-12').pending.cents,'37');
    state=change(state,'sync',null,null,null,'2028-02-29');
    assert.deepEqual(state.expenses.map(row=>[row.period,row.cents,row.category]),[['2027-12','29','居住'],['2028-01','37','交通'],['2028-02','37','交通']]);
    const booked=structuredClone(state.expenses);state.expenses[0].voided=1;state.actions[0].undone=1;booked[0].voided=1;
    state=change(state,'sync',null,null,null,'2028-03-31');
    assert.deepEqual(state.expenses.slice(0,3),booked);assert.equal(state.expenses.length,kind==='分期'?3:4);
    assert.deepEqual(writeBackup(readBackup(bytes(state))),bytes(state));
  }
});

test('next-month first/final installment edits keep start/periods; completed and inactive reject',()=>{
  for(const kind of ['訂閱','分期']) {
    let state=add(initial(),{kind,periods:kind==='分期'?1:0,start_month:'2027-01'});
    const id=state.recurring_rules[0].id;
    state=change(state,'update',id,0,{amount:'1000000000',category:'其他'});
    assert.equal(state.recurring_rules[0].start_month,'2027-01');assert.equal(state.expenses.length,0);
    state=change(state,'sync',null,null,null,'2027-01-31');assert.equal(state.expenses[0].cents,'100000000000');
    if(kind==='分期')refuse(state,'update',id,1,{amount:'1',category:'其他'},'2027-01-31');
    else {state=change(state,'stop',id,1,null,'2027-01-31');refuse(state,'update',id,2,{amount:'1',category:'其他'},'2027-01-31');}
  }
});

test('immutable fields, forged effective month, stale revision and invalid edits cannot mutate state',()=>{
  for(const kind of ['訂閱','分期']) {
    const state=add(initial(),{kind,periods:kind==='分期'?2:0}),id=state.recurring_rules[0].id;
    for(const fields of [{name:'偽造名稱'},{due_day:1},{due_day:null},{kind:'固定'},{start_month:'2027-01'},{periods:3},{effective_month:'2026-12'},
      {amount:'0'},{amount:'1.001'},{amount:'1000000000.01'},{category:'不存在'}])refuse(state,'update',id,0,{amount:'.31',category:'居住',...fields});
    const changed=change(state,'update',id,0,{amount:'.31',category:'居住'});
    refuse(changed,'update',id,0,{amount:'.99',category:'居住'});
  }
});

test('historical null day, large periods/name and disabled original category stay exact on edits',()=>{
  let state=add(initial(),{kind:'分期',periods:2});const id=state.recurring_rules[0].id;
  state.recurring_rules[0].due_day=null;state.recurring_rules[0].periods=MAX_INTEGER;state.recurring_rules[0].name='字'.repeat(4096);
  state.categories.push({name:'居住',active:0});
  state=change(state,'update',id,0,{amount:'.31',category:'居住'});
  assert.equal(state.recurring_rules[0].due_day,null);assert.equal(state.recurring_rules[0].periods,MAX_INTEGER);
  assert.equal(state.recurring_versions[0].due_day,1);assert.equal(state.recurring_versions[0].name.length,4096);
  const changed=change(state,'update',id,1,{amount:'.32',category:'其他'});
  refuse(changed,'update',id,2,{amount:'.33',category:'居住'});
});

test('non-fixed backup versions accept only immutable name/day and valid rule/month references',()=>{
  for(const kind of ['訂閱','分期']) {
    const state=add(initial(),{kind,periods:kind==='分期'?2:0}),rule=state.recurring_rules[0];
    state.recurring_versions.push({id:'v_test',recurring_id:rule.id,effective_month:'2027-01',name:rule.name,cents:'31',category:'其他',due_day:31});
    assert.deepEqual(writeBackup(readBackup(bytes(state))),bytes(state));
    for(const fields of [{name:'偽造名稱'},{due_day:30},{recurring_id:'r_missing'},{effective_month:'2026-11'}]) {
      const changed=structuredClone(state);Object.assign(changed.recurring_versions[0],fields);assert.throws(()=>bytes(changed));
    }
  }
});

test('category rename follows all recurring versions without changing immutable fields',()=>{
  let state=add();const id=state.recurring_rules[0].id;
  state=change(state,'update',id,0,{amount:'.31',category:'居住'});
  state=ledger.changeCategory(state,'rename','居住','住家');
  assert.equal(state.recurring_rules[0].category,'住家');assert.equal(state.recurring_versions[0].category,'住家');
  assert.equal(state.recurring_rules[0].revision,2);assert.equal(state.recurring_versions[0].due_day,31);
  state=change(state,'sync',null,null,null,'2027-01-31');assert.ok(state.expenses.every(row=>row.category==='住家'));
});

test('legacy fixed APIs keep their scope and corruption, revision and capacity errors cannot half-post',()=>{
  let state=add();assert.equal(ledger.changeFixed(state,'sync',null,null,null,'2027-01-31').expenses.length,0);
  const id=state.recurring_rules[0].id;refuse(state,'stop',id,-1);state.recurring_rules[0].revision=MAX_INTEGER;refuse(state,'stop',id,MAX_INTEGER);
  state=add();state.recurring_rules[0].due_day=32;assert.throws(()=>change(state,'sync'));
  state=add(initial(),{due_day:1});state.categories=Array.from({length:99998},(_,i)=>({name:'c'+i,active:0}));
  refuse(state,'sync');refuse(state,'stop',state.recurring_rules[0].id,0);
  state.categories.pop();state=change(state,'sync');assert.equal(state.expenses.length,1);
  const backup=readBackup(bytes(state));assert.deepEqual(writeBackup(backup),bytes(state));
});

test('imported installment names at backup limit reject the entire catch-up safely without truncation',()=>{
  let state=add(initial(),{kind:'分期',periods:1,due_day:1});state.recurring_rules[0].name='字'.repeat(4096);
  state=add(state,{due_day:1});const before=bytes(state);
  assert.throws(()=>change(state,'sync'),error=>error.code==='limit'&&error.message.includes('分期'));
  assert.deepEqual(bytes(state),before);refuse(state,'stop',state.recurring_rules[0].id,0);
});

test('progress uses one precomputed posting count in the page and never scans non-installments',()=>{
  const state={get expenses(){throw new Error('Unexpected full expense scan');}};
  assert.deepEqual(rules.recurringProgress(state,{kind:'訂閱',periods:0}),{posted:0n,total:0n,complete:false});
  assert.deepEqual(rules.recurringProgress(state,{kind:'分期',periods:2},2n),{posted:2n,total:2n,complete:true});
});

test('non-leap February clamps all late days and next-month starts remain unposted',()=>{
  for(const kind of ['訂閱','分期'])for(const due_day of [29,30,31]) {
    let state=add(initial(),{kind,periods:kind==='分期'?2:0,start_month:'2027-02',due_day},'2027-02-28');
    state=change(state,'sync',null,null,null,'2027-02-28');assert.equal(state.expenses[0].spent_on,'2027-02-28');
    const future=add(initial(),{kind,periods:kind==='分期'?2:0,start_month:'2027-03',due_day},'2027-02-28');
    assert.equal(change(future,'sync',null,null,null,'2027-02-28').expenses.length,0);
  }
});
