import { number, labelCategory } from './behavior.js';

const palette = ['#4788b7', '#4ba99f', '#d9a24b', '#9a84bb', '#7db6ca'];
function base(dark = false) {
  const ink = dark ? '#bdd4df' : '#87939c';
  return {
    animationDuration: 350, animationDurationUpdate: 250,
    textStyle: { fontFamily: 'Inter, Microsoft YaHei, sans-serif', color: ink },
    color: palette,
    tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: dark ? '#183744' : '#fff', borderColor: dark ? '#294d5c' : '#e8eef2', textStyle: { color: dark ? '#e5f1f5' : '#384c5c', fontSize: 12 }, padding: 14 },
    legend: { top: 4, right: 8, itemWidth: 14, itemHeight: 8, textStyle: { color: ink, fontSize: 11 }, icon: 'roundRect' },
    grid: { top: 57, bottom: 32, left: 54, right: 50, containLabel: false },
  };
}
const axis = (dark = false) => ({ axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: dark ? '#8daebd' : '#8b969f', fontSize: 10, margin: 12 }, splitLine: { lineStyle: { color: dark ? '#203d4a' : '#eef2f4', type: 'solid' } } });
const compact = value => value >= 10000 ? `${(value / 10000).toFixed(1)}万` : number(value);

export function trendOption(days, { detailed = false, dark = false } = {}) {
  return {
    ...base(dark),
    grid: { top: 62, bottom: 33, left: 50, right: 45 },
    xAxis: { type: 'category', data: days.map(d => d.day.slice(5)), ...axis(dark), boundaryGap: detailed, splitLine: { show: false } },
    yAxis: [
      { type: 'value', name: '浏览 / 次', ...axis(dark), axisLabel: { ...axis(dark).axisLabel, formatter: compact } },
      { type: 'value', name: detailed ? '用户及其他行为 / 次' : '购买 / 次', ...axis(dark), splitLine: { show: false } },
    ],
    series: detailed ? [
      { name: '浏览数', type: 'bar', data: days.map(d => d.pv), barMaxWidth: 40, itemStyle: { color: '#65a994', borderRadius: [3, 3, 0, 0], opacity: .85 } },
      ...[['用户数', 'users', '#d5ae4d'], ['收藏数', 'fav', '#6da5ce'], ['加购数', 'cart', '#4fae99'], ['购买数', 'buy', '#a28abd']].map(([name, key, color]) => ({ name, type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 5, data: days.map(d => d[key]), lineStyle: { width: 2, color }, itemStyle: { color } })),
    ] : [
      { name: '浏览量', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6, data: days.map(d => d.pv), lineStyle: { width: 2.5 }, areaStyle: { opacity: .045 } },
      { name: '购买量', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 6, data: days.map(d => d.buy), lineStyle: { width: 2.5, color: '#d9a24b' }, itemStyle: { color: '#d9a24b' } },
    ],
  };
}

export function funnelOption(values, dark = false) {
  return { ...base(dark), tooltip: { ...base(dark).tooltip, trigger: 'item' }, legend: { show: false },
    series: [{ type: 'funnel', top: 22, bottom: 16, left: '12%', width: '76%', minSize: '25%', maxSize: '100%', gap: 4,
      sort: 'descending', label: { position: 'inside', formatter: p => `${p.name}  ${number(p.value)}`, color: '#fff', fontSize: 12 },
      labelLine: { show: false }, itemStyle: { borderWidth: 0 },
      data: values.map((p, i) => ({ ...p, itemStyle: { color: ['#4587b9', '#4ba99f', '#dea34c'][i] } })),
    }],
  };
}

export function hourlyOption(hours) {
  return { ...base(), grid: { top: 58, bottom: 35, left: 48, right: 25 },
    xAxis: { ...axis(), type: 'category', data: hours.map(h => `${String(h.hour).padStart(2, '0')}:00`), splitLine: { show: false } },
    yAxis: { ...axis(), type: 'value', name: '行为 / 次' },
    series: [['浏览', 'pv', '#4788b7'], ['收藏', 'fav', '#7fb7aa'], ['加购', 'cart', '#d9a24b'], ['购买', 'buy', '#9a84bb']].map(([name, key, color]) => ({ name, type: 'bar', stack: 'total', data: hours.map(h => h[key]), itemStyle: { color }, barMaxWidth: 28 })),
  };
}

export function rankingOption(values, field = 'category', dark = false) {
  const list = values.slice(0, 8).reverse();
  return { ...base(dark), legend: { show: false }, grid: { top: 10, bottom: 25, left: field === 'category' ? 102 : 100, right: 35 },
    xAxis: { ...axis(dark), type: 'value', minInterval: 1 },
    yAxis: { ...axis(dark), type: 'category', data: list.map(r => field === 'category' ? labelCategory(r.id) : `商品 ${r.id}`), splitLine: { show: false } },
    series: [{ name: '购买事件', type: 'bar', barMaxWidth: 20, data: list.map(r => r.count), itemStyle: { color: '#5d9fb1', borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', color: dark ? '#b8d6e1' : '#6c8190', fontSize: 11 } }],
  };
}

export function segmentOption(profiles) {
  const groups = new Map(); profiles.forEach(p => groups.set(p.segment, (groups.get(p.segment) || 0) + 1));
  return { ...base(), tooltip: { ...base().tooltip, trigger: 'item' }, legend: { orient: 'vertical', left: '57%', top: 'middle', itemGap: 15 },
    series: [{ type: 'pie', radius: ['48%', '73%'], center: ['28%', '50%'], avoidLabelOverlap: true, label: { show: false }, itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 }, data: [...groups].map(([name, value]) => ({ name, value })) }],
  };
}

export function interestOption(profile) {
  const cats = profile?.cats || [];
  const total = cats.reduce((sum, c) => sum + c[1], 0);
  return { ...base(), legend: { show: false }, grid: { top: 10, left: 100, right: 35, bottom: 25 },
    xAxis: { ...axis(), type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    yAxis: { ...axis(), type: 'category', inverse: true, data: cats.slice(0, 5).map(([id]) => labelCategory(id)), splitLine: { show: false } },
    series: [{ type: 'bar', data: cats.slice(0, 5).map(([, n]) => Math.round(n / total * 100)), barMaxWidth: 16, itemStyle: { color: '#63a99e', borderRadius: [0, 5, 5, 0] }, label: { show: true, position: 'right', formatter: '{c}%', color: '#69898a', fontSize: 11 } }],
  };
}
