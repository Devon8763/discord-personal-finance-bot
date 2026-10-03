import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyData, MAX_INTEGER, writeBackup } from '../local-first/backup.mjs';
import { portable } from '../local-first/idb.mjs';
import * as rules from '../local-first/rules.mjs';
import * as ledger from '../local-first/ledger.mjs';
const initial = () => ({format:'local-first-test-ledger',version:2,owner:'local-test-owner',...emptyData()});
const form = changes => ({name:'房租',amount:'100.29',category:'居住',start_month:'2026-09',due_day:28,...changes});
const change = (state,op,id,rev,input,date='2026-09-24') => ledger.changeFixed(state,op,id,rev,input,date);
const add = (state=initial(),changes={}) => change(state,'add',null,null,form(changes));
const bytes = state => new TextDecoder().decode(writeBackup(portable(state)));
const reject = (state,op,id,rev,input,date) => {const before=bytes(state);assert.throws(()=>change(state,op,id,rev,input,date));assert.equal(bytes(state),before);};

test('fixed dates clamp short/leap months, legacy null and cross-year without floating money',()=>{
  assert.equal(rules.fixedDueDate('2028-02',31),'2028-02-29');
  assert.equal(rules.fixedDueDate('2027-02',29),'2027-02-28');
  assert.equal(rules.fixedDueDate('2026-04',31),'2026-04-30');
  assert.equal(rules.fixedDueDate('0001-02',null),'0001-02-01');
  assert.equal(rules.fixedNextMonth('2026-12'),'2027-01');
  for(const day of [0,32,1.5,true,'1']) assert.throws(()=>rules.fixedDueDate('2026-09',day));
});
test('new fixed fields match Python name/money/category/start rules and immutable start',()=>{
  const state=add(initial(),{name:' 名稱 ',amount:'.29'}),row=state.recurring_rules[0];
  assert.equal(row.name,' 名稱 ');assert.equal(row.cents,'29');assert.equal(row.revision,0);
  assert.equal(state.recurring_versions[0].effective_month,'2026-09');
  for(const input of [{name:''},{name:'\u0085'},{name:'x'.repeat(101)},{amount:'0'},{amount:'1.001'},
    {amount:'1000000000.01'},{category:'不存在'},{start_month:'2026-08'},{start_month:'2026-11'},
    {due_day:null},{due_day:'1'},{due_day:32}]) reject(initial(),'add',null,null,form(input));
  const unicode=add(initial(),{name:'😀'.repeat(100)});assert.equal([...unicode.recurring_rules[0].name].length,100);
  reject(state,'update',row.id,0,form({start_month:'2026-10'}));
});
test('next-month changes replace only that version, increment exact revision and preserve history',()=>{
  let state=add();const id=state.recurring_rules[0].id;
  const original=structuredClone(state.recurring_rules[0]),first=structuredClone(state.recurring_versions[0]);
  state=change(state,'update',id,0,form({name:'十月',amount:'200.50',due_day:3}));
  const versionId=state.recurring_versions[1].id;
  state=change(state,'update',id,1,form({name:'最後設定',amount:'.29',category:'交通',due_day:5}));
  assert.deepEqual(state.recurring_rules[0],{...original,revision:2});
  assert.deepEqual(state.recurring_versions[0],first);assert.equal(state.recurring_versions.length,2);
  assert.equal(state.recurring_versions[1].id,versionId);
  const view=rules.fixedView(state,state.recurring_rules[0],'2026-09');
  assert.equal(view.name,'房租');assert.equal(view.pending.name,'最後設定');
  assert.equal(rules.fixedView(state,state.recurring_rules[0],'2026-10').pending,null);
  state=change(state,'sync',null,null,null,'2026-10-05');
  assert.deepEqual(state.expenses.map(row=>[row.spent_on,row.cents,row.note]),[['2026-09-28','10029','房租'],['2026-10-05','29','最後設定']]);
  assert.ok(state.actions.every(row=>row.before===null&&row.undone===0));
  assert.ok(state.expenses.every(row=>row.payment_source_id===null&&row.payment_source_name==='未指定'&&row.revision===0));
});
test('inactive or historical category can be retained, not newly selected; catch-up preserves it',()=>{
  let state=add();const id=state.recurring_rules[0].id;
  state.categories.push({name:'居住',active:0},{name:'交通',active:0});
  reject(state,'add',null,null,form());reject(state,'update',id,0,form({category:'交通'}));
  state=change(state,'update',id,0,form({amount:'2'}));
  state.recurring_versions[1].category='歷史分類';
  state=change(state,'update',id,1,form({category:'歷史分類'}));
  state=change(state,'sync',null,null,null,'2026-10-28');
  assert.deepEqual(state.expenses.map(row=>row.category),['居住','歷史分類']);
});
test('fixed-only catch-up is idempotent across voided/undone records; no read creates entries',()=>{
  let state=add(initial(),{due_day:20});const id=state.recurring_rules[0].id;
  state.recurring_rules.push({...state.recurring_rules[0],id:'r_sub',kind:'訂閱',due_day:null});
  const before=bytes(state);rules.fixedView(state,state.recurring_rules[0],'2026-09');assert.equal(bytes(state),before);
  state=change(state,'sync',null,null,null);assert.equal(state.expenses.length,1);
  state.expenses[0].voided=1;state.actions[0].undone=1;
  const synced=change(state,'sync',null,null,null);assert.equal(bytes(synced),bytes(state));
  state=change(state,'stop',id,0,null);assert.equal(state.recurring_rules[0].active,0);assert.equal(state.expenses.length,1);
  assert.equal(change(state,'sync',null,null,null,'2026-12-31').expenses.length,1);
  reject(state,'update',id,1,form());reject(state,'stop',id,1,null);
  reject(state,'stop','r_sub',0,null);
});
test('stop catches missing history atomically, legacy null is day one and start next-month stays untouched',()=>{
  let state=add();const id=state.recurring_rules[0].id;
  state.recurring_rules[0].due_day=null;state.recurring_versions=[];
  state=change(state,'stop',id,0,null,'2026-11-01');
  assert.deepEqual(state.expenses.map(row=>row.spent_on),['2026-09-01','2026-10-01','2026-11-01']);
  let future=add(initial(),{start_month:'2026-10'});const key=future.recurring_rules[0].id;
  future=change(future,'update',key,0,form({start_month:'2026-10',name:'新下月',due_day:1}));
  assert.equal(future.recurring_versions.length,1);
  assert.equal(change(future,'sync',null,null,null).expenses.length,0);
  assert.equal(change(future,'sync',null,null,null,'2026-10-01').expenses[0].note,'新下月');
});
test('stale, invalid and exhausted revisions or corrupt data reject entire change',()=>{
  const state=add(),id=state.recurring_rules[0].id;
  for(const revision of [null,true,'0',-1,1]) reject(state,'update',id,revision,form());
  state.recurring_rules[0].revision=MAX_INTEGER;reject(state,'stop',id,MAX_INTEGER,null);
  state.recurring_rules[0].revision=9007199254740994n;
  assert.equal(change(state,'update',id,9007199254740994n,form()).recurring_rules[0].revision,9007199254740995n);
  state.recurring_rules[0].cents='broken';const before=structuredClone(state);
  assert.throws(()=>change(state,'sync',null,null,null));assert.deepEqual(state,before);
});
test('row capacity is exact and cannot leave half a posting or version',()=>{
  const state=add(initial(),{due_day:1});
  state.categories=Array.from({length:99998},(_,i)=>({name:'category'+i,active:0}));
  assert.equal(state.categories.length+state.recurring_rules.length+state.recurring_versions.length,100000);
  reject(state,'sync',null,null,null);reject(state,'stop',state.recurring_rules[0].id,0,null);
  reject(state,'update',state.recurring_rules[0].id,0,form());
});
