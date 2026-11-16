import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import App from '../../src/App.vue';

vi.mock('../../src/components/AppChart.vue', () => ({
  default: { template: '<div aria-label="图表"></div>' },
}));
vi.mock('../../src/components/AppIcon.vue', () => ({
  default: { template: '<span></span>' },
}));
const perms = [
  '数据概览',
  '行为分析',
  '推荐查看',
  '数据导入',
  '任务管理',
  '账号管理',
];
const dataset = {
  id: 'a'.repeat(32),
  filename: 'fixture.csv',
  source: '测试数据',
  status: 'ready',
  start_ms: 1793462400000,
  end_ms: 1793462520000,
};
const account = {
  id: 'admin',
  name: '测试管理员',
  role: '管理员',
  role_id: 'admin',
  email: '',
  team: '',
  note: '',
};
const behavior = {
  id: 1,
  user: 'u1',
  item: 'p1',
  category: 'c1',
  type: 'buy',
  t: 1793462520000,
};
const profile = {
  user: 'u1',
  total: 3,
  itemCount: 1,
  counts: { pv: 1, fav: 0, cart: 1, buy: 1 },
  recency: 0,
  frequency: 1,
  segment: '近期购买用户',
  cats: [['c1', 3]],
  rows: [behavior],
};
let app, host, fetchMock, guest, allowed, imported;
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const button = (name, scope = document) =>
  [...scope.querySelectorAll('button')].find(
    (b) => (b.getAttribute('aria-label') || b.textContent.trim()) === name,
  );
async function settle(check) {
  await vi.waitFor(check, { timeout: 3000 });
  await nextTick();
}
async function mount() {
  host = document.createElement('div');
  document.body.append(host);
  app = createApp(App);
  app.mount(host);
  await nextTick();
}
async function navigate(name) {
  button(name, document.querySelector('.side-nav')).click();
  await nextTick();
}
async function input(selector, value) {
  const el = document.querySelector(selector);
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  await nextTick();
}

beforeEach(() => {
  guest = false;
  allowed = perms.slice();
  imported = false;
  history.replaceState(null, '', '/#home');
  fetchMock = vi.fn(async (path, options = {}) => {
    const url = new URL(path, 'http://localhost');
    const route = url.pathname.replace('/api/v1', '');
    if (route === '/auth/me')
      return guest
        ? json({ error: { message: '请先登录' } }, 401)
        : json({ account, permissions: allowed, csrf_token: 'csrf-test' });
    if (route === '/auth/login')
      return json({ error: { message: '账号或密码不正确' } }, 401);
    if (route === '/datasets/active') return json({ dataset });
    if (route === '/overview')
      return json({
        counts: { pv: 1, fav: 0, cart: 1, buy: 1 },
        total: 3,
        users: 1,
        items: 1,
        categories: 1,
        days: [{ day: '2026-11-01', pv: 1, users: 1, fav: 0, cart: 1, buy: 1 }],
      });
    if (route === '/models/active')
      return json({ status: 'not_connected', model: null });
    if (route === '/datasets') return json({ items: [dataset], total: 1 });
    if (route === '/behaviors')
      return json({
        items: [behavior],
        total: 3,
        page: 1,
        dataset_id: dataset.id,
      });
    if (route === '/jobs') return json({ items: [], total: 0 });
    if (route === '/analytics/rf')
      return json({ items: [{ name: '近期购买用户', value: 1 }] });
    if (route === '/analytics/funnel')
      return json({
        items: [
          { name: '浏览商品', value: 1 },
          { name: '浏览后加购', value: 1 },
          { name: '加购后购买', value: 1 },
        ],
      });
    if (route === '/analytics/retention')
      return json({
        items: [
          {
            day: '2026-11-01',
            users: 1,
            retention: [1, null, null, null, null, null, null, null],
          },
        ],
      });
    if (route === '/users')
      return json({
        items: [profile],
        total: 1,
        page: 1,
        dataset_id: dataset.id,
      });
    if (route === '/users/u1/profile') return json(profile);
    if (route === '/recommendations')
      return json({ error: { message: '推荐算法服务尚未接入' } }, 503);
    if (route === '/datasets/import') {
      imported = true;
      return json({ job: { id: 'job1', status: 'queued' } }, 202);
    }
    if (route === '/jobs/job1')
      return json({ id: 'job1', status: 'ready', processed_rows: 2 });
    throw new Error(`Unexpected request: ${route} ${options.method}`);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  app?.unmount();
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('真实 API 页面接入', () => {
  it('登录失败显示后端错误，不退回虚构数据', async () => {
    guest = true;
    await mount();
    await settle(() => expect(button('登录工作台')).toBeTruthy());
    await input('input[type="password"]', 'wrong-password');
    document
      .querySelector('form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await settle(() =>
      expect(document.body.textContent).toContain('账号或密码不正确'),
    );
    expect(document.querySelector('.application')).toBeNull();
  });

  it('明细表显示服务器记录、筛选请求和用户画像', async () => {
    await mount();
    await settle(() =>
      expect(document.body.textContent).toContain('测试管理员'),
    );
    await navigate('数据管理');
    await settle(() =>
      expect(document.querySelector('tbody')?.textContent).toContain('u1'),
    );
    expect(document.querySelector('tbody').textContent).toContain('购买');
    expect(document.body.textContent).toContain('共 3 条');
    button('查看用户').click();
    await settle(() =>
      expect(document.querySelector('.modal-body')?.textContent).toContain(
        '3 条行为',
      ),
    );
    expect(document.querySelector('.modal-body').textContent).toContain('购买');
  });

  it('分群、漏斗与留存能渲染服务端统计', async () => {
    await mount();
    await settle(() =>
      expect(document.querySelector('.application')).not.toBeNull(),
    );
    document.querySelector('[aria-label="数据分析"]').click();
    await nextTick();
    await navigate('RFM客户分析');
    await settle(() =>
      expect(document.querySelector('tbody')?.textContent).toContain(
        '近期购买用户',
      ),
    );
    await navigate('漏斗转化分析');
    await settle(() =>
      expect(document.querySelector('tbody')?.textContent).toContain('100.0%'),
    );
    await navigate('用户留存分析');
    await settle(() =>
      expect(document.querySelector('.retention-cell')?.textContent).toBe(
        '100%',
      ),
    );
    expect(document.querySelectorAll('.retention-cell')[1].textContent).toBe(
      '—',
    );
  });

  it('粘贴导入直接发送原始文件，重复记录留给后端原样保留', async () => {
    await mount();
    await settle(() =>
      expect(document.querySelector('.application')).not.toBeNull(),
    );
    button('导入行为数据').click();
    await nextTick();
    const raw =
      'user_id,item_id,behavior_type,timestamp\nu1,p1,pv,1793462400000\nu1,p1,pv,1793462400000';
    await input('textarea', raw);
    button('使用此内容').click();
    await nextTick();
    button('确认导入').click();
    await settle(() => expect(imported).toBe(true));
    const [, options] = fetchMock.mock.calls.find(([path]) =>
      path.endsWith('/datasets/import'),
    );
    expect(options.headers['X-CSRF-Token']).toBe('csrf-test');
    expect(options.credentials).toBe('include');
    expect(await options.body.get('file').text()).toBe(raw);
    await settle(() => expect(document.querySelector('.modal')).toBeNull());
  });

  it('权限控制导航，推荐未接入时展示真实状态且没有调参入口', async () => {
    allowed = ['数据概览', '行为分析', '推荐查看'];
    await mount();
    await settle(() =>
      expect(document.querySelector('.application')).not.toBeNull(),
    );
    expect(document.querySelector('[aria-label="系统管理"]')).toBeNull();
    await navigate('智能推荐');
    await settle(() =>
      expect(document.querySelector('#recommend-user')?.value).toBe('u1'),
    );
    button('生成推荐').click();
    await settle(() =>
      expect(document.body.textContent).toContain('推荐算法服务尚未接入'),
    );
    expect(document.body.textContent).not.toContain('重新训练模型');
    expect(document.querySelector('.recommendation-card')).toBeNull();
  });
});
