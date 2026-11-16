import test from 'node:test';
import assert from 'node:assert/strict';
import { summarize, conversionPath, retention, getProfiles, demoRecommendations } from '../src/data/behavior.js';
import { parseBehaviorCSV, toCSV } from '../src/data/csv.js';

const time = s => Date.parse(`${s}+08:00`);
const event = (user, item, type, date, category = 'c1') => ({ user, item, type, t: time(date), category });

test('统计按用户去重、日期使用北京时间，购买事件不等同订单', () => {
  const result = summarize([
    event('u1', 'p1', 'pv', '2026-11-13T23:59:00'),
    event('u1', 'p1', 'buy', '2026-11-14T00:01:00'),
    event('u2', 'p1', 'pv', '2026-11-14T10:00:00'),
  ]);
  assert.equal(result.users, 2);
  assert.equal(result.items, 1);
  assert.equal(result.counts.buy, 1);
  assert.deepEqual(result.days.map(d => [d.day, d.users]), [['2026-11-13', 1], ['2026-11-14', 2]]);
});

test('漏斗只计同用户同商品的顺序路径，购买先于加购不算完成', () => {
  const result = conversionPath([
    event('u1', 'p1', 'buy', '2026-11-14T08:00:00'),
    event('u1', 'p1', 'pv', '2026-11-14T09:00:00'),
    event('u1', 'p1', 'cart', '2026-11-14T10:00:00'),
    event('u2', 'p2', 'pv', '2026-11-14T09:00:00'),
    event('u2', 'p2', 'cart', '2026-11-14T10:00:00'),
    event('u2', 'p2', 'buy', '2026-11-14T11:00:00'),
    event('u2', 'p3', 'buy', '2026-11-14T12:00:00'),
  ].reverse());
  assert.deepEqual(result.map(r => r.value), [2, 2, 1]);
});

test('留存区分未回访的零与窗口未覆盖的空值', () => {
  const result = retention([
    event('u1', 'p1', 'pv', '2026-11-12T10:00:00'),
    event('u2', 'p1', 'pv', '2026-11-12T11:00:00'),
    event('u1', 'p1', 'pv', '2026-11-13T10:00:00'),
    event('u3', 'p1', 'pv', '2026-11-14T10:00:00'),
  ]);
  assert.equal(result[0].retention[1], .5);
  assert.equal(result[0].retention[2], 0);
  assert.equal(result[0].retention[3], null);
  assert.equal(result[1].retention[1], null);
});

test('没有购买的用户保留缺失 R，推荐排除已购买商品', () => {
  const rows = [event('u1', 'p1', 'buy', '2026-11-14T08:00:00'), event('u1', 'p2', 'pv', '2026-11-14T09:00:00'), event('u2', 'p2', 'pv', '2026-11-14T10:00:00')];
  assert.equal(getProfiles(rows).find(p => p.user === 'u2').recency, null);
  assert(demoRecommendations('u1', rows).every(r => r.id !== 'p1'));
  assert(demoRecommendations('new', rows, 'popular').length > 0);
});

test('CSV 合并完全重复记录，识别秒和毫秒，并跳过非法行为', () => {
  const result = parseBehaviorCSV('user_id,item_id,category_id,behavior_type,timestamp\nu1,p1,c1,pv,1794614400\nu1,p1,c1,pv,1794614400\nu2,p2,c2,buy,1794614400000\nu3,p3,c3,invalid,1794614400');
  assert.equal(result.rows.length, 2);
  assert.equal(result.invalid, 1);
  assert.equal(result.duplicate, 1);
  assert.equal(result.rows[0].t, result.rows[1].t);
  assert.throws(() => parseBehaviorCSV('a,b\n1,2'), /需要/);
});

test('CSV 支持带引号逗号字段，导出防止表格公式执行', () => {
  const result = parseBehaviorCSV('user_id,item_id,category_id,behavior_type,timestamp\n"u,1",p1,c1,pv,1794614400');
  assert.equal(result.rows[0].user, 'u,1');
  const csv = toCSV([{ value: '=1+1' }], [{ label: '值', value: r => r.value }]);
  assert(csv.includes("'=1+1"));
});
