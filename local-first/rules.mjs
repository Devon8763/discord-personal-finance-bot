const DEFAULT_CATEGORIES = ['餐飲', '交通', '購物', '居住', '娛樂', '醫療', '其他'];
export class ValidationError extends Error {}

export function pythonStrip(value) {
  if (typeof value !== 'string') throw new ValidationError('請輸入有效名稱。');
  return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '');
}
export function categoryName(value) {
  const name = pythonStrip(value);
  if (!name || [...name].length > 20 || name === '總額' || /[\n\r]/u.test(name)) {
    throw new ValidationError('分類名稱需 1～20 字，不能使用「總額」或換行。');
  }
  return name;
}
export function paymentName(value) {
  const name = pythonStrip(value);
  if (!name || [...name].length > 30 || /[\u0000-\u001f]/u.test(name)) {
    throw new ValidationError('付款方式名稱需 1～30 字，不可包含換行或控制字元。');
  }
  return name;
}

export function cents(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) throw new ValidationError('金額資料格式錯誤。');
  return BigInt(value);
}
export const sumCents = records => records.reduce((sum, row) => sum + cents(row.cents), 0n);
export const differenceCents = (a, b) => cents(a) - cents(b);

export function budgetCents(value) {
  if (!(typeof value === 'string' && /^[0-9]+$/.test(value)) &&
      !(typeof value === 'number' && Number.isSafeInteger(value))) {
    throw new ValidationError('預算金額需為正整數台幣。');
  }
  const whole = BigInt(value);
  if (whole <= 0n || whole > 1000000000n) throw new ValidationError('預算金額需為正整數台幣且不超過十億元。');
  return (whole * 100n).toString();
}

export function money(value) {
  if (!['string', 'number', 'bigint'].includes(typeof value)) throw new ValidationError('請輸入有效金額。');
  // Decimal accepts Unicode decimal digits and ignores underscores; retain that input contract.
  const text = String(value).trim().replace(/_/g, '').replace(/\p{Decimal_Number}/gu, digit => {
    let first = digit.codePointAt(0);
    while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
    return String((digit.codePointAt(0) - first) % 10);
  });
  const match = /^\+?(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) throw new ValidationError('請輸入正數金額，最多兩位小數。');
  const [whole, fraction = ''] = match[1].split('.');
  let digits = (whole + fraction).replace(/^0+/, '');
  if (!digits) throw new ValidationError('金額必須大於零。');
  const trimmed = digits.replace(/0+$/, '');
  const power = 2n + BigInt(match[2] || '0') - BigInt(fraction.length) + BigInt(digits.length - trimmed.length);
  digits = trimmed;
  if (power < 0n) throw new ValidationError('金額最多兩位小數。');
  if (BigInt(digits.length) + power > 12n) throw new ValidationError('金額超過上限 1,000,000,000 元。');
  const result = BigInt(digits) * 10n ** power;
  if (result > 100000000000n) throw new ValidationError('金額超過上限 1,000,000,000 元。');
  return result.toString();
}

export function isoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new ValidationError('請輸入有效日期。');
  const [year, month, day] = value.split('-').map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]) throw new ValidationError('請輸入有效日期。');
  return value;
}

export function categoryOptions(state) {
  const options = new Map(DEFAULT_CATEGORIES.map(name => [name, { name, active: 1 }]));
  for (const category of state.categories) options.set(category.name, category);
  return [...options.values()];
}

export function validateInput(form, state, today, previous = null) {
  const spent_on = isoDate(form.spent_on);
  if (previous?.source && previous.source !== 'manual' && spent_on !== previous.spent_on) {
    throw new ValidationError('自動來源消費的原入帳日期不可更改。');
  }
  if (spent_on > isoDate(today)) throw new ValidationError('生活消費日期不可晚於台灣今天。');
  if (typeof form.note !== 'string' || !form.note.trim() || [...form.note].length > 200) throw new ValidationError('消費項目需填寫，且最多 200 字。');
  const category = categoryOptions(state).find(option => option.name === form.category) ||
    (previous?.category === form.category ? {name:previous.category, active:0} : null);
  if (!category || (category.active !== 1 && previous?.category !== category.name)) throw new ValidationError('請選擇有效分類。');
  let payment;
  if (previous && form.payment_source_id === '') {
    payment = { id: previous.payment_source_id, name: previous.payment_source_name };
  } else {
    payment = state.payment_sources.find(option => option.id === form.payment_source_id && option.active === 1);
    if (!payment) throw new ValidationError('請選擇有效付款方式。');
  }
  return { cents: money(form.amount), spent_on, category: category.name, note: form.note,
    payment_source_id: payment.id, payment_source_name: payment.name };
}

export function fixedNextMonth(month) {
  isoDate(month + '-01');
  const [year, number] = month.split('-').map(Number);
  const next = number === 12 ? `${String(year + 1).padStart(4, '0')}-01` : `${month.slice(0, 4)}-${String(number + 1).padStart(2, '0')}`;
  isoDate(next + '-01');
  return next;
}
export function fixedDueDate(month, dueDay) {
  isoDate(month + '-01');
  const day = dueDay === null ? 1 : dueDay;
  if (!Number.isInteger(day) || day < 1 || day > 31) throw new ValidationError('每月扣款日需為 1～31 的整數。');
  const [year, number] = month.split('-').map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const last = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][number - 1];
  return `${month}-${String(Math.min(day, last)).padStart(2, '0')}`;
}
export function fixedSettings(state, rule, month) {
  let current = rule;
  for (const version of state.recurring_versions) {
    if (version.recurring_id === rule.id && version.effective_month <= month &&
        (current === rule || version.effective_month > current.effective_month)) current = version;
  }
  return current;
}
export function recurringProgress(state, rule, posted = null) {
  const total = BigInt(rule.periods);
  if (rule.kind !== '分期') return {posted:0n, total, complete:false};
  posted ??= BigInt(new Set(state.expenses.filter(row => row.recurring_id === rule.id).map(row => row.period)).size);
  return {posted, total, complete:rule.kind === '分期' && posted === total};
}

export function fixedView(state, rule, month) {
  const current = fixedSettings(state, rule, month);
  const view = {...rule, name:current.name, cents:current.cents, category:current.category, due_day:current.due_day ?? 1};
  const future = state.recurring_versions.filter(row => row.recurring_id === rule.id && row.effective_month > month)
    .sort((a, b) => a.effective_month.localeCompare(b.effective_month))[0];
  view.pending = future && ['name', 'cents', 'category', 'due_day'].some(key => future[key] !== view[key]) ? future : null;
  return view;
}
export function validateFixed(form, state, today, previous = null, immutable = null) {
  if (immutable && (['name', 'due_day', 'start_month', 'kind', 'periods'].some(key =>
    Object.hasOwn(form, key) && form[key] !== immutable[key]) || Object.hasOwn(form, 'effective_month'))) {
    throw new ValidationError('訂閱與分期只能更改金額及分類。');
  }
  const name = immutable ? immutable.name : form?.name, dueDay = immutable ? immutable.due_day ?? 1 : form?.due_day;
  if (!immutable && (typeof name !== 'string' || !pythonStrip(name) || [...name].length > 100)) {
    throw new ValidationError('消費項目需填寫，且最多 100 字。');
  }
  if (dueDay === null) throw new ValidationError('每月扣款日需為 1～31 的整數。');
  fixedDueDate(isoDate(today).slice(0, 7), dueDay);
  if (form.category !== previous?.category && !categoryOptions(state).some(row => row.name === form.category && row.active === 1)) {
    throw new ValidationError('請選擇有效分類；更改時可保留原停用分類。');
  }
  return {name, cents:money(form.amount), category:form.category, due_day:dueDay};
}
