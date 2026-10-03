import test from 'node:test';
import assert from 'node:assert/strict';
const load=()=>import('../local-first/comparison.mjs');
const row=(date,cents='101',extra={})=>({spent_on:date,cents,category:'餐飲',note:'Lunch [x]',source:'manual',kind:'consumption',voided:0,...extra});
const state=expenses=>({expenses,categories:[{name:'停用',active:0}]});
const query=(a_start='2024-02-01',a_end='2024-02-29',b_start='2024-01-01',b_end='2024-01-31',extra={})=>({a_start,a_end,b_start,b_end,category:'',keyword:'',...extra});
test('Taiwan whole-month defaults cross year and leap February',async()=>{
  const {comparisonDefaults}=await load();
  assert.deepEqual(comparisonDefaults('2025-01-01'),query('2025-01-01','2025-01-31','2024-12-01','2024-12-31'));
  assert.equal(comparisonDefaults('2024-02-20').a_end,'2024-02-29');
});
test('inclusive ranges, exact cents, four sources and effective booked consumption only',async()=>{
  const {compareExpenses,comparisonDay}=await load();
  const data=state([row('2024-02-01','101'),row('2024-02-29','2',{source:'固定'}),row('2024-02-29','3',{source:'訂閱'}),row('2024-02-29','4',{source:'分期'}),row('2024-01-31','50'),row('2024-01-01','50'),row('2024-02-28','999',{voided:1}),row('2024-02-28','999',{source:'prediction'}),row('2024-02-28','999',{kind:'income'}),row('2024-03-01','999')]);
  const before=JSON.stringify(data),r=compareExpenses(data,query(),'2024-02-29');
  assert.equal(r.a.total_cents,110n);assert.equal(r.a.record_count,4);assert.equal(r.b.total_cents,100n);
  assert.equal(r.difference_cents,10n);assert.equal(r.percent,'10');assert.equal(comparisonDay(r.a,28).cents,9n);
  assert.equal(r.a.actual_days,29);assert.equal(r.periods_differ,true);assert.equal(JSON.stringify(data),before);
});
test('future is unknown, unfinished is cut off today and started empty is zero',async()=>{
  const {compareExpenses}=await load();
  const r=compareExpenses(state([]),query('2024-02-29','2024-03-31','2024-03-01','2024-03-02'),'2024-02-29');
  assert.equal(r.a.total_cents,0n);assert.equal(r.a.actual_end,'2024-02-29');assert.equal(r.a.actual_days,1);
  assert.equal(r.b.started,false);assert.equal(r.b.total_cents,null);assert.equal(r.b.record_count,null);assert.equal(r.b.actual_start,null);assert.equal(r.b.daily.size,0);
  assert.equal(r.difference_cents,null);assert.equal(r.percent,null);
  assert.equal(compareExpenses(state([]),query(),'2024-02-29').percent,null);
});
test('overlap, nonadjacent unequal periods and half-year include every real day',async()=>{
  const {compareExpenses}=await load();
  const r=compareExpenses(state([row('2023-12-31'),row('2024-02-29'),row('2024-06-30')]),query('2023-12-31','2024-06-30','2024-02-29','2024-02-29'),'2024-07-01');
  assert.equal(r.a.actual_days,183);assert.equal(r.a.record_count,3);assert.equal(r.b.record_count,1);assert.equal(r.difference_cents,202n);
});
test('literal ASCII-insensitive keyword AND category, historical and one-sided categories and filtered shares',async()=>{
  const {compareExpenses,comparisonCategories}=await load();
  const data=state([row('2024-02-01','100',{category:'停用'}),row('2024-02-02','300',{category:'歷史'}),row('2024-01-01','200',{category:'單邊'}),row('2024-02-03','900',{note:'other'})]);
  assert.equal(comparisonCategories(data,'2024-02-29').find(r=>r.name==='歷史').state,'historical');
  const r=compareExpenses(data,query(undefined,undefined,undefined,undefined,{keyword:'lunch [x]'}),'2024-02-29');
  assert.equal(r.a.total_cents,400n);assert.deepEqual(r.categories.map(c=>c.name),['停用','單邊','歷史']);
  assert.equal(r.categories.find(c=>c.name==='停用').a_percent,'25');assert.equal(r.categories.find(c=>c.name==='單邊').a_cents,0n);
  const selected=compareExpenses(data,query(undefined,undefined,undefined,undefined,{category:'停用',keyword:'LUNCH [x]'}),'2024-02-29');
  assert.equal(selected.a.record_count,1);assert.equal(selected.categories[0].a_percent,'100');
});
test('more than 50, BigInt extreme aggregates and percentage rounding remain exact; plotting safely declines',async()=>{
  const {compareExpenses,canPlotComparison}=await load();
  const many=compareExpenses(state(Array.from({length:65},()=>row('2024-02-01','1'))),query(),'2024-02-29');
  assert.equal(many.a.record_count,65);assert.equal(many.a.total_cents,65n);assert.equal(canPlotComparison(many),true);
  const big=compareExpenses(state([row('2024-02-01','9223372036854775807'),row('2024-02-01','9223372036854775807'),row('2024-01-01','3')]),query(),'2024-02-29');
  assert.equal(big.a.total_cents,18446744073709551614n);assert.equal(canPlotComparison(big),false);
  const rounding=compareExpenses(state([row('2024-02-01','4'),row('2024-01-01','3')]),query(),'2024-02-29');
  assert.equal(rounding.percent,'33.33');
});
test('invalid inputs reject before reading expenses and preserve the caller input',async()=>{
  const {validateComparison,compareExpenses}=await load();
  const unreadable={get expenses(){throw new Error('read happened');}};
  for(const bad of [query('2023-02-29'),query('2024-02-30'),query('2024-03-01','2024-02-29'),query(''),query(undefined,undefined,undefined,undefined,{keyword:'x'.repeat(201)}),query(undefined,undefined,undefined,undefined,{category:7})]){
    const before=JSON.stringify(bad);assert.throws(()=>validateComparison(bad,'2024-02-29'));assert.throws(()=>compareExpenses(unreadable,bad,'2024-02-29'),e=>e.message!=='read happened');assert.equal(JSON.stringify(bad),before);
  }
  assert.throws(()=>compareExpenses(state([]),query(undefined,undefined,undefined,undefined,{category:'不存在'}),'2024-02-29'));
});
test('very long valid periods remain sparse, with exact on-demand zero days and no clipped statistics',async()=>{
  const {compareExpenses,comparisonDay}=await load();
  const r=compareExpenses(state([row('0001-01-01','1'),row('2026-10-02','2')]),query('0001-01-01','9999-12-31','0001-01-01','9999-12-31'),'2026-10-02');
  assert.equal(r.a.actual_days,739891);assert.equal(r.a.daily.size,2);assert.equal(r.a.total_cents,3n);
  assert.deepEqual(comparisonDay(r.a,0),{day:1,date:'0001-01-01',cents:1n});assert.equal(comparisonDay(r.a,1).cents,0n);
  assert.equal(comparisonDay(r.a,739890).cents,2n);assert.equal(comparisonDay(r.a,739891),null);
});
test('Python keyword code-point length and stripping do not change literal matching',async()=>{
  const {compareExpenses,validateComparison}=await load();
  assert.equal(validateComparison(query(undefined,undefined,undefined,undefined,{keyword:'😀'.repeat(200)}),'2024-02-29').keyword.length,400);
  assert.throws(()=>validateComparison(query(undefined,undefined,undefined,undefined,{keyword:'😀'.repeat(201)}),'2024-02-29'));
  const data=state([row('2024-02-01','100',{note:'\uFEFFLunch'}),row('2024-02-01','100',{note:'Lunch'})]);
  const r=compareExpenses(data,query(undefined,undefined,undefined,undefined,{keyword:'\uFEFFLunch'}),'2024-02-29');assert.equal(r.a.record_count,1);
  assert.equal(validateComparison(query(undefined,undefined,undefined,undefined,{keyword:'\u0085Lunch\u0085'}),'2024-02-29').keyword,'Lunch');
});
