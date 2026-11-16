const typeAliases = { pv: 'pv', view: 'pv', click: 'pv', '浏览': 'pv', '1': 'pv', fav: 'fav', favorite: 'fav', '收藏': 'fav', '2': 'fav', cart: 'cart', '加购': 'cart', '3': 'cart', buy: 'buy', purchase: 'buy', '购买': 'buy', '4': 'buy' };
export function parseBehaviorCSV(text) {
  if (text.length > 10 * 1024 * 1024) throw new Error('展示版最多支持 10 MB 文件，请先抽样。');
  const grid = []; let row = [], cell = '', quote = false;
  const input = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < input.length; i++) {
    const c = input[i];
    if (c === '"') { if (quote && input[i + 1] === '"') { cell += '"'; i++; } else quote = !quote; }
    else if (c === ',' && !quote) { row.push(cell.trim()); cell = ''; }
    else if ((c === '\n' || c === '\r') && !quote) { if (c === '\r' && input[i + 1] === '\n') i++; row.push(cell.trim()); if (row.some(Boolean)) grid.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (quote) throw new Error('CSV 引号未闭合，请检查文件。');
  row.push(cell.trim()); if (row.some(Boolean)) grid.push(row);
  if (!grid.length) throw new Error('文件中没有行为记录。');
  const alias = { user_id: 'user', item_id: 'item', product_id: 'item', category_id: 'category', behavior_type: 'type', behavior: 'type', timestamp: 't', event_time: 't' };
  let columns = grid[0].map(c => alias[c.toLowerCase()]), records = grid.slice(1);
  if (!columns.includes('user') && grid[0].length === 5) { columns = ['user', 'item', 'category', 'type', 't']; records = grid; }
  if (!['user', 'item', 'type', 't'].every(c => columns.includes(c))) throw new Error('需要 user_id、item_id、behavior_type、timestamp；category_id 可选。');
  if (records.length > 25000) throw new Error('展示版最多支持 25,000 条记录，请先抽样。');
  let invalid = 0, duplicate = 0; const rows = [], seen = new Set();
  for (const values of records) {
    const record = {}; columns.forEach((c, i) => { if (c) record[c] = values[i] || ''; });
    record.type = typeAliases[String(record.type).toLowerCase()];
    const raw = record.t;
    if (/^\d{10}$/.test(raw)) record.t = Number(raw) * 1000;
    else if (/^\d{13}$/.test(raw)) record.t = Number(raw);
    else { let value = raw.replace(' ', 'T'); if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) value += '+08:00'; record.t = Date.parse(value); }
    if (!record.user || !record.item || !record.type || !Number.isFinite(record.t)) { invalid++; continue; }
    record.category ||= '未分类';
    const key = JSON.stringify([record.user, record.item, record.category, record.type, record.t]);
    if (seen.has(key)) { duplicate++; continue; }
    seen.add(key); rows.push({ ...record, id: rows.length + 1 });
  }
  if (!rows.length) throw new Error('没有有效记录，请检查字段、行为类型和时间。');
  return { rows: rows.sort((a, b) => a.t - b.t), invalid, duplicate };
}
export function toCSV(rows, columns) {
  const safe = value => { let s = String(value ?? ''); if (/^[=+@\-]/.test(s)) s = `'${s}`; return `"${s.replaceAll('"', '""')}"`; };
  return '\uFEFF' + [columns.map(c => safe(c.label)).join(','), ...rows.map(row => columns.map(c => safe(c.value(row))).join(','))].join('\r\n');
}
