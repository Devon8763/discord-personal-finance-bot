import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
const url = new URL('../local-first/rules.mjs', import.meta.url);
const fixture = JSON.parse(readFileSync(new URL('fixtures/life_ledger_rules.json', import.meta.url), 'utf8'));
async function rules() { assert.ok(existsSync(url), 'Missing isolated browser rules'); return import(url); }
const state = () => ({ categories: [], payment_sources: [{ id: 'p_cash', name: '現金', active: 1 }, { id: 'p_other', name: '測試卡', active: 0 }] });
const input = () => ({ amount: '12.34', note: '測試午餐', spent_on: '2026-09-24', category: '餐飲', payment_source_id: 'p_cash' });

test('unchanged Python amount fixtures use exact cents', async () => {
  const r = await rules();
  for (const c of fixture.amounts) {
    if (c.invalid) assert.throws(() => r.money(c.input));
    else assert.equal(r.money(c.input), String(c.cents));
  }
});
test('scientific notation, underscores and Unicode decimal digits stay exact', async () => {
  const r = await rules();
  for (const [value, cents] of [['+1.20e+2','12000'],['.29','29'],[' １_٢.٣٤ ','1234'],['1_0e-1','100'],['100e-4','1']]) assert.equal(r.money(value), cents);
  for (const value of ['1e-3','Infinity','-1e2','1e1000000',true,null,'①']) assert.throws(() => r.money(value));
});
test('approved difference rejects Decimal context rounding and underflow', async () => {
  const r = await rules();
  assert.throws(() => r.money('1e-1000030'));
  assert.throws(() => r.money('1.000000000000000000000000000001'));
  assert.equal(r.money('1.000000000000000000000000000000'), '100');
});
test('exact totals and differences exceed Number safe precision', async () => {
  const r = await rules();
  assert.equal(r.sumCents([{cents:'5000000000000001'},{cents:'5000000000000002'}]),10000000000000003n);
  assert.equal(r.differenceCents('10000000000000003','9007199254740993'),992800745259010n);
  for (const v of ['01','-1','1.2',1,true]) assert.throws(() => r.cents(v));
});
test('strict ISO dates, leap years and unchanged short-month fixtures', async () => {
  const r = await rules();
  for (const c of fixture.due_dates) assert.equal(r.isoDate(c.date), c.date);
  for (const v of ['2025-02-29','2024-02-30','1900-02-29','2026-04-31','2026-9-01','2026/09/01','0000-01-01']) assert.throws(() => r.isoDate(v));
  assert.equal(r.isoDate('2000-02-29'), '2000-02-29');
  assert.equal(r.isoDate('0001-01-01'), '0001-01-01');
});
test('entry validation uses supplied Taiwan today and preserves note text', async () => {
  const r = await rules();
  assert.equal(r.validateInput(input(),state(),'2026-09-24').cents, '1234');
  assert.throws(() => r.validateInput(input(),state(),'2026-09-23'));
  const form={...input(), note:'  <script>合成用途</script>  '};
  assert.equal(r.validateInput(form,state(),'2026-09-24').note,form.note);
  for (const extra of [{note:' '},{note:'字'.repeat(201)},{category:'不存在'},{payment_source_id:'missing'},{payment_source_id:'p_other'},{spent_on:'2026-02-30'}]) assert.throws(() => r.validateInput({...input(),...extra},state(),'2026-09-24'));
});
test('missing category overrides are different from explicitly disabled defaults', async () => {
  const r = await rules();const s=state();
  assert.ok(r.categoryOptions(s).some(c=>c.name==='餐飲' && c.active===1));
  s.categories=[{name:'餐飲',active:0},{name:'<img src=x onerror=alert(1)>',active:1}];
  assert.equal(r.categoryOptions(s).find(c=>c.name==='餐飲').active,0);
  assert.throws(() => r.validateInput(input(),s,'2026-09-24'));
  assert.equal(s.categories.length,2);
});
test('edit retains disabled original category and historical payment snapshot', async () => {
  const r=await rules();const s=state();s.categories=[{name:'餐飲',active:0}];
  const previous={category:'餐飲',payment_source_id:'p_other',payment_source_name:'改名前測試卡'};
  const result=r.validateInput({...input(),payment_source_id:''},s,'2026-09-24',previous);
  assert.equal(result.payment_source_name,'改名前測試卡');
  assert.equal(result.payment_source_id,'p_other');
  assert.throws(()=>r.validateInput({...input(),category:'未知'},s,'2026-09-24',previous));
});
test('Taiwan calendar boundary is owned by entry, not pure rules', async () => {
  const r=await rules();
  assert.equal(r.isoDate('2026-09-25'),'2026-09-25');
  const code=readFileSync(url,'utf8');
  assert.doesNotMatch(code,/Date\.now|new Date|indexedDB|\bdocument\b|\bfetch\s*\(/);
  const source=readFileSync(new URL('../local-first/page.mjs',import.meta.url),'utf8');
  assert.match(source,/\btaiwanToday\b.*from '\.\/ledger\.mjs'/);
  const entry=readFileSync(new URL('../local-first/ledger.mjs',import.meta.url),'utf8');
  assert.match(entry,/timeZone:\s*['"]Asia\/Taipei['"]/);
});

test('automatic expense dates cannot move; manual dates and four-source historical snapshots remain editable', async () => {
  const r = await rules(), s = state(); s.categories = [{name:'餐飲',active:0}];
  for (const source of ['manual','固定','訂閱','分期']) {
    const previous = {source,spent_on:'2026-09-23',category:'餐飲',payment_source_id:'p_other',payment_source_name:'歷史卡'};
    const form = {...input(),spent_on:previous.spent_on,payment_source_id:'',amount:'.29'};
    assert.equal(r.validateInput(form,s,'2026-09-24',previous).cents,'29');
    assert.equal(r.validateInput(form,s,'2026-09-24',previous).payment_source_name,'歷史卡');
    if (source === 'manual') assert.equal(r.validateInput({...form,spent_on:'2026-09-24'},s,'2026-09-24',previous).spent_on,'2026-09-24');
    else for (const spent_on of ['2026-08-23','2026-09-24']) assert.throws(() => r.validateInput({...form,spent_on},s,'2026-09-24',previous), /日期不可更改/);
  }
});

test('imported original category without an override can be retained but never newly selected', async () => {
  const r=await rules(),s=state(),previous={source:'固定',spent_on:input().spent_on,category:'歷史無override',payment_source_id:null,payment_source_name:'歷史付款'};
  const form={...input(),category:previous.category,payment_source_id:''};
  assert.equal(r.validateInput(form,s,'2026-09-24',previous).category,previous.category);
  assert.throws(()=>r.validateInput({...input(),category:previous.category},s,'2026-09-24'));
});
