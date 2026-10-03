import test from 'node:test';
import assert from 'node:assert/strict';

const load = () => import('../local-first/csv.mjs');
const row = (id, spent_on, source = 'manual', extra = {}) => ({
  id, spent_on, source, kind:'consumption', voided:0, cents:'1234',
  note:'午餐', category:'餐飲', payment_source_name:'歷史卡名', ...extra,
});
const range = {start:'2024-02-01', end:'2024-03-01'};
const text = result => new TextDecoder('utf-8', {ignoreBOM:true}).decode(result.payload);

test('CSV uses inclusive leap-day/cross-month range and the four effective consumption sources', async () => {
  const {expenseCsv} = await load();
  const state = {expenses:[row('a','2024-02-01'), row('b','2024-02-29','固定'),
    row('c','2024-03-01','訂閱'), row('d','2024-02-29','分期'),
    row('v','2024-02-29','manual',{voided:1}), row('future','2024-03-02'),
    ...['income','investment','transfer'].map(kind=>row(kind,'2024-02-29','manual',{kind})),
    row('prediction','2024-02-29','prediction'), row('outside','2024-01-31')]};
  const result = expenseCsv(state, range, '2024-03-01');
  assert.equal(result.count,4);
  assert.equal(text(result), '\uFEFF日期,金額,消費項目,分類,付款方式\r\n' +
    '2024-03-01,12.34,午餐,餐飲,歷史卡名\r\n2024-02-29,12.34,午餐,餐飲,歷史卡名\r\n' +
    '2024-02-29,12.34,午餐,餐飲,歷史卡名\r\n2024-02-01,12.34,午餐,餐飲,歷史卡名\r\n');
});
test('future end is accepted but cannot expose stored future consumption', async () => {
  const {expenseCsv} = await load();
  const result=expenseCsv({expenses:[row('today','2024-03-01'),row('future','2024-03-02')]},
    {start:'2024-03-01',end:'2025-12-31'},'2024-03-01');
  assert.equal(result.count,1);assert.ok(!text(result).includes('2024-03-02'));
});
test('all rows beyond 50 are exported, ordered by date then local id descending', async () => {
  const {expenseCsv} = await load();
  const state={expenses:Array.from({length:65},(_,i)=>row(`e${String(i).padStart(3,'0')}`,'2024-02-29','manual',{note:`項目${i}`}))};
  const result=expenseCsv(state,range,'2024-03-01'), lines=text(result).split('\r\n');
  assert.equal(result.count,65);assert.equal(lines.length,67);
  assert.equal(lines[1],'2024-02-29,12.34,項目64,餐飲,歷史卡名');
  assert.equal(lines[65],'2024-02-29,12.34,項目0,餐飲,歷史卡名');
});
test('cents remain exact decimal text through signed-64-bit maximum and retain payment snapshot', async () => {
  const {expenseCsv} = await load();
  const state={expenses:['1','10','100','101','9223372036854775807'].map((value,i)=>row(String(i),'2024-02-29','manual',{cents:value})),
    payment_sources:[{name:'重新命名後'}]};
  const output=text(expenseCsv(state,range,'2024-03-01'));
  for(const value of ['0.01','0.1','1','1.01','92233720368547758.07']) assert.ok(output.includes(`,${value},午餐,餐飲,歷史卡名\r\n`),value);
  assert.ok(!output.includes('重新命名後'));assert.ok(!output.includes('.00'));
});
test('BOM, CRLF and standard CSV escaping protect all untrusted text without changing originals', async () => {
  const {expenseCsv} = await load();
  const state={expenses:[row('a','2024-02-29','manual',{
    note:' \t=SUM(1,2)\n"中文"',category:'\u200B+分類',payment_source_name:'@卡,"舊名"',
  })]};
  const before=JSON.stringify(state),result=expenseCsv(state,range,'2024-03-01');
  assert.deepEqual([...result.payload.slice(0,3)],[239,187,191]);
  assert.equal(text(result),'\uFEFF日期,金額,消費項目,分類,付款方式\r\n' +
    '2024-02-29,12.34,"\' \t=SUM(1,2)\n""中文""",\'\u200B+分類,"\'@卡,""舊名"""\r\n');
  assert.equal(JSON.stringify(state),before);
});
test('formula protection handles dangerous prefixes, Unicode whitespace and control characters', async () => {
  const {expenseCsv} = await load();
  for(const [input,expected] of [['=1',"'=1"],['+1',"'+1"],['-1',"'-1"],['@x',"'@x"],
    ['\u3000=1',"'\u3000=1"],['\u0001text',"'\u0001text"],['\uFEFF=1',"'\uFEFF=1"],
    ['\t正常',"'\t正常"],['  正常','  正常'],['"=1','""=1']]) {
    const output=text(expenseCsv({expenses:[row('a','2024-02-29','manual',{note:input,category:input,payment_source_name:input})]},range,'2024-03-01'));
    const cell=input.includes('"')?`"${expected}"`:expected;
    assert.ok(output.endsWith(`,${cell},${cell},${cell}\r\n`),JSON.stringify(input));
  }
});
test('invalid dates and reversed ranges reject before reading any expenses', async () => {
  const {expenseCsv,validateCsvRange} = await load();
  const unreadable={get expenses(){throw new Error('query ran');}};
  for(const value of [{start:'',end:'2024-03-01'},{start:'2024-2-01',end:'2024-03-01'},
    {start:'2023-02-29',end:'2024-03-01'},{start:'2024-02-30',end:'2024-03-01'},
    {start:'2024-03-02',end:'2024-03-01'},{start:'2024-02-29',end:''}]) {
    assert.throws(()=>expenseCsv(unreadable,value,'2024-03-01'),error=>error.message!=='query ran');
  }
  assert.deepEqual(validateCsvRange({start:'2024-02-29',end:'2024-03-02'},'2024-03-01'),{start:'2024-02-29',end:'2024-03-02'});
});
test('empty and wholly future ranges return no download and never mutate or reorder snapshot', async () => {
  const {expenseCsv} = await load();
  const state={expenses:[row('a','2024-03-02'),row('b','2024-02-29','manual',{voided:1})]};
  const before=JSON.stringify(state);
  assert.deepEqual(expenseCsv(state,range,'2024-03-01'),{count:0,payload:null});
  assert.deepEqual(expenseCsv(state,{start:'2024-03-02',end:'2025-01-01'},'2024-03-01'),{count:0,payload:null});
  assert.equal(JSON.stringify(state),before);
});
