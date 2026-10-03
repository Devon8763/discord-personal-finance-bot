import {isoDate,cents,categoryOptions,pythonStrip,ValidationError} from './rules.mjs';
import {activeExpenses,literalMatch} from './browse.mjs';
const dayNumber=date=>Date.parse(date+'T00:00:00Z')/86400000;
const dateText=day=>new Date(day*86400000).toISOString().slice(0,10);
// Decimal ROUND_HALF_UP, without converting money or ratios to floating point.
export function ratioText(numerator,denominator,digits=2) {
  if(denominator===0n)return null;
  const scale=10n**BigInt(digits),negative=numerator<0n;
  const absolute=negative?-numerator:numerator;
  const rounded=(absolute*100n*scale*2n+denominator)/(denominator*2n);
  const fraction=(rounded%scale).toString().padStart(digits,'0').replace(/0+$/,'');
  return (negative&&rounded?'−':'')+(rounded/scale).toString()+(fraction?'.'+fraction:'');
}
export function comparisonDefaults(today) {
  isoDate(today);
  const first=today.slice(0,7)+'-01',previousEnd=dateText(dayNumber(first)-1);
  const next=new Date(first+'T00:00:00Z');next.setUTCMonth(next.getUTCMonth()+1);
  return {a_start:first,a_end:dateText(next.getTime()/86400000-1),b_start:previousEnd.slice(0,7)+'-01',b_end:previousEnd,category:'',keyword:''};
}
export function validateComparison(query,today) {
  isoDate(today);
  const result={};
  for(const key of ['a_start','a_end','b_start','b_end'])result[key]=isoDate(query[key]);
  if(result.a_start>result.a_end||result.b_start>result.b_end)throw new ValidationError('開始日期不可晚於結束日期。');
  if(typeof query.category!=='string'||typeof query.keyword!=='string'||[...query.keyword].length>200)throw new ValidationError('請確認分類及關鍵字（最多 200 字）。');
  return {...result,category:query.category,keyword:pythonStrip(query.keyword)};
}
export function comparisonCategories(state,today) {
  const options=categoryOptions(state).map(row=>({name:row.name,state:row.active===1?'active':'inactive'}));
  const names=new Set(options.map(row=>row.name));
  for(const name of [...new Set(activeExpenses(state,today).map(row=>row.category))].sort()){
    if(!names.has(name)){options.push({name,state:'historical'});names.add(name);}
  }
  return options;
}
export function compareExpenses(state,input,today) {
  const query=validateComparison(input,today),options=comparisonCategories(state,today);
  if(query.category&&!options.some(row=>row.name===query.category))throw new ValidationError('分類已變動，請重新載入後再選擇。');
  const rows=activeExpenses(state,today).filter(row=>(!query.category||row.category===query.category)&&(!query.keyword||literalMatch(row.note,query.keyword)));
  const period=(start,end)=>{
    const started=start<=today,actualEnd=end<today?end:today;
    const result={start,end,days:dayNumber(end)-dayNumber(start)+1,started,unfinished:end>today,actual_start:started?start:null,actual_end:started?actualEnd:null,total_cents:started?0n:null,record_count:started?0:null,categories:new Map(),daily:new Map(),actual_days:started?dayNumber(actualEnd)-dayNumber(start)+1:0};
    if(!started)return result;
    for(const row of rows){
      if(row.spent_on<start||row.spent_on>actualEnd)continue;
      const amount=cents(row.cents);result.total_cents+=amount;result.record_count++;
      result.categories.set(row.category,(result.categories.get(row.category)||0n)+amount);
      result.daily.set(row.spent_on,(result.daily.get(row.spent_on)||0n)+amount);
    }
    return result;
  };
  const a=period(query.a_start,query.a_end),b=period(query.b_start,query.b_end),comparable=a.started&&b.started;
  const share=(amount,total)=>{const value=ratioText(amount,total,1);return amount>0n&&value==='0'?'<0.1':value;};
  const categories=[...new Set([...a.categories.keys(),...b.categories.keys()])].sort().map(name=>{
    const ac=a.started?a.categories.get(name)||0n:null,bc=b.started?b.categories.get(name)||0n:null;
    return {name,a_cents:ac,b_cents:bc,difference_cents:comparable?ac-bc:null,a_percent:a.started?share(ac,a.total_cents):null,b_percent:b.started?share(bc,b.total_cents):null};
  });
  const difference=comparable?a.total_cents-b.total_cents:null;
  return {today,query,a,b,categories,difference_cents:difference,percent:comparable?ratioText(difference,b.total_cents):null,periods_differ:a.days!==b.days||a.unfinished||b.unfinished};
}
export function comparisonDay(period,index) {
  if(index<0||index>=period.actual_days)return null;
  const date=dateText(dayNumber(period.start)+index);
  return {day:index+1,date,cents:period.daily.get(date)||0n};
}
export function canPlotComparison(result) {
  const limit=BigInt(Number.MAX_SAFE_INTEGER);
  return [result.a,result.b].every(period=>period.total_cents===null||period.total_cents<=limit)&&
    result.categories.every(row=>[row.a_cents,row.b_cents].every(value=>value===null||value<=limit));
}
