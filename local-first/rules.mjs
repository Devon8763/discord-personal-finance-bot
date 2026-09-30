const DEFAULT_CATEGORIES = ['餐飲', '交通', '購物', '居住', '娛樂', '醫療', '其他'];
export class ValidationError extends Error {}

export function cents(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) throw new ValidationError('金額資料格式錯誤。');
  return BigInt(value);
}
export const sumCents = records => records.reduce((sum, row) => sum + cents(row.cents), 0n);
export const differenceCents = (a, b) => cents(a) - cents(b);

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
  if (spent_on > isoDate(today)) throw new ValidationError('生活消費日期不可晚於台灣今天。');
  if (typeof form.note !== 'string' || !form.note.trim() || [...form.note].length > 200) throw new ValidationError('消費項目需填寫，且最多 200 字。');
  const category = categoryOptions(state).find(option => option.name === form.category);
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
