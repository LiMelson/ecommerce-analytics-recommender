export const behaviorLabels = { pv: '浏览', fav: '收藏', cart: '加购', buy: '购买' };
export const categoryNames = { '982926': '数码配件', '154040': '户外运动', '4391936': '居家生活', '800581': '图书文具', '4757812': '咖啡茶饮', '4357323': '个护清洁' };
export const behaviorColors = { pv: '#407fac', fav: '#75b6a0', cart: '#d8a24d', buy: '#9980be' };
export const labelCategory = id => `类目 ${id}`;
export const number = n => Number(n || 0).toLocaleString('zh-CN');
export const dayOf = t => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(t);
export const dateTime = t => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(t);

export function createDemoData() {
  let seed = 20261114;
  const rand = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  const categories = Object.keys(categoryNames);
  const rows = [];
  for (let u = 0; u < 48; u++) {
    const user = String(1003707 + u * 1139);
    const start = u === 0 ? 0 : u % 4;
    for (let d = start; d < 9; d++) {
      if (u !== 0 && d > start && (u + d) % 7 === 0) continue;
      const count = u === 0 ? 56 : 18 + (u * 7 % 20) + (d >= 7 ? 10 : 0);
      for (let n = 0; n < count; n++) {
        const categoryIndex = rand() < .78 ? (u + (d === 8 && u % 5 === 0 ? 1 : 0)) % 6 : Math.floor(rand() * 6);
        const itemIndex = Math.floor(rand() * 24);
        const item = String(1325619 + categoryIndex * 573211 + itemIndex * 1937);
        const r = rand();
        const type = r < .64 ? 'pv' : r < .79 ? 'fav' : r < .94 ? 'cart' : 'buy';
        const t = Date.parse('2026-11-06T00:00:00+08:00') + d * 86400000 + (7 + Math.floor(rand() * 16)) * 3600000 + Math.floor(rand() * 3600000);
        rows.push({ id: rows.length + 1, user, item, category: categories[categoryIndex], type, t });
      }
    }
  }
  return rows.sort((a, b) => a.t - b.t);
}

export function summarize(rows) {
  const counts = { pv: 0, fav: 0, cart: 0, buy: 0 };
  const users = new Set(), items = new Set(), days = new Map();
  for (const row of rows) {
    counts[row.type]++;
    users.add(row.user); items.add(row.item);
    const key = dayOf(row.t);
    if (!days.has(key)) days.set(key, { day: key, pv: 0, fav: 0, cart: 0, buy: 0, userSet: new Set() });
    const d = days.get(key); d[row.type]++; d.userSet.add(row.user);
  }
  return { counts, users: users.size, items: items.size, total: rows.length,
    days: [...days.values()].sort((a, b) => a.day.localeCompare(b.day)).map(d => ({ ...d, users: d.userSet.size })) };
}

export function getProfiles(rows) {
  const reference = Math.max(...rows.map(r => r.t), 0);
  const grouped = new Map();
  for (const r of rows) {
    if (!grouped.has(r.user)) grouped.set(r.user, { user: r.user, rows: [], items: new Set(), cats: new Map(), counts: { pv: 0, fav: 0, cart: 0, buy: 0 }, last: 0, lastBuy: null });
    const p = grouped.get(r.user); p.rows.push(r); p.items.add(r.item); p.counts[r.type]++;
    p.last = Math.max(p.last, r.t);
    if (r.type === 'buy') p.lastBuy = Math.max(p.lastBuy || 0, r.t);
    const weight = { pv: 1, fav: 3, cart: 5, buy: 8 }[r.type] * Math.pow(.5, (reference - r.t) / (3 * 86400000));
    p.cats.set(r.category, (p.cats.get(r.category) || 0) + weight);
  }
  return [...grouped.values()].map(p => {
    const recency = p.lastBuy === null ? null : Math.floor((reference - p.lastBuy) / 86400000);
    const frequency = p.counts.buy;
    const segment = !frequency ? '潜在转化用户' : recency <= 2 ? frequency >= 12 ? '高频活跃用户' : '近期购买用户' : frequency >= 12 ? '高频待唤回用户' : '低频待唤回用户';
    return { ...p, itemCount: p.items.size, cats: [...p.cats].sort((a, b) => b[1] - a[1]), recency, frequency, segment };
  }).sort((a, b) => b.rows.length - a.rows.length);
}

export function hourly(rows) {
  const hours = Array.from({ length: 24 }, (_, hour) => ({ hour, pv: 0, fav: 0, cart: 0, buy: 0 }));
  rows.forEach(r => { hours[Math.floor((r.t + 8 * 3600000) / 3600000) % 24][r.type]++; });
  return hours;
}

export function purchaseRanking(rows, field = 'category') {
  const counts = new Map();
  rows.filter(r => r.type === 'buy').forEach(r => { const key = r[field]; const p = counts.get(key) || { id: key, count: 0, users: new Set(), category: r.category }; p.count++; p.users.add(r.user); counts.set(key, p); });
  return [...counts.values()].map(p => ({ ...p, users: p.users.size })).sort((a, b) => b.count - a.count);
}

export function conversionPath(rows) {
  const stages = new Map();
  for (const row of rows.slice().sort((a, b) => a.t - b.t)) {
    const key = JSON.stringify([row.user, row.item]);
    const stage = stages.get(key) || 0;
    if (stage === 0 && row.type === 'pv') stages.set(key, 1);
    else if (stage === 1 && row.type === 'cart') stages.set(key, 2);
    else if (stage === 2 && row.type === 'buy') stages.set(key, 3);
  }
  const values = [...stages.values()];
  return [{ name: '浏览商品', value: values.filter(v => v >= 1).length }, { name: '浏览后加购', value: values.filter(v => v >= 2).length }, { name: '加购后购买', value: values.filter(v => v >= 3).length }];
}

export function retention(rows) {
  const active = new Map();
  rows.forEach(r => { if (!active.has(r.user)) active.set(r.user, new Set()); active.get(r.user).add(dayOf(r.t)); });
  const maxDay = [...new Set(rows.map(r => dayOf(r.t)))].sort().at(-1);
  const cohorts = new Map();
  for (const [user, dates] of active) { const first = [...dates].sort()[0]; if (!cohorts.has(first)) cohorts.set(first, []); cohorts.get(first).push({ user, dates }); }
  return [...cohorts].sort().map(([first, people]) => ({ day: first, users: people.length,
    retention: [0, 1, 2, 3, 4, 5, 6, 7].map(offset => {
      const date = dayOf(Date.parse(`${first}T12:00:00+08:00`) + offset * 86400000);
      return date > maxDay ? null : people.filter(p => p.dates.has(date)).length / people.length;
    }) }));
}

export function demoRecommendations(user, rows, strategy = 'bpr') {
  const profile = getProfiles(rows).find(p => p.user === user);
  const popularity = new Map();
  rows.forEach(r => { const p = popularity.get(r.item) || { id: r.item, category: r.category, heat: 0 }; p.heat += { pv: 1, fav: 3, cart: 5, buy: 8 }[r.type]; popularity.set(r.item, p); });
  const purchased = new Set(profile?.rows.filter(r => r.type === 'buy').map(r => r.item) || []);
  const cats = new Map(profile?.cats || []);
  const maxInterest = Math.max(...cats.values(), 1);
  const maxHeat = Math.max(...[...popularity.values()].map(p => p.heat), 1);
  let candidates = [...popularity.values()].filter(p => !purchased.has(p.id));
  candidates = candidates.map(p => ({ ...p, score: strategy === 'popular' || !profile ? p.heat / maxHeat : .78 * (cats.get(p.category) || 0) / maxInterest + .22 * p.heat / maxHeat }));
  candidates.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  const best = candidates[0]?.score || 1;
  return candidates.slice(0, 10).map((p, index) => ({ ...p, rank: index + 1, score: Number((p.score / best * 100).toFixed(1)), reason: strategy === 'popular' || !profile ? '全体用户行为热度匹配' : '类目兴趣与行为热度匹配' }));
}
