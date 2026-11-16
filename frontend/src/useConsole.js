import {
  ref,
  reactive,
  computed,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue';
import { navigation, pageMap } from './navigation.js';
import { api, queryString, saveBlob, setSession } from './api.js';
import { behaviorLabels, dateTime, dayOf } from './data/behavior.js';
import { toCSV } from './data/csv.js';

const emptyStats = () => ({
  counts: { pv: 0, fav: 0, cart: 0, buy: 0 },
  total: 0,
  users: 0,
  items: 0,
  categories: 0,
  days: [],
});
const pagePermissions = {
  home: '数据概览',
  screen: '数据概览',
  daily: '行为分析',
  hourly: '行为分析',
  funnel: '行为分析',
  rfm: '行为分析',
  retention: '行为分析',
  categories: '行为分析',
  products: '行为分析',
  data: '行为分析',
  recommend: '推荐查看',
  accounts: '账号管理',
  roles: '账号管理',
};

export function useConsole() {
  const authenticated = ref(false),
    authReady = ref(false),
    authBusy = ref(false),
    authError = ref('');
  const allowed = ref([]),
    loading = ref(false),
    loadError = ref(''),
    busy = ref(false);
  const personal = reactive({
    id: '',
    name: '',
    email: '',
    team: '',
    note: '',
    role: '',
  });
  const current = ref(
    pageMap[location.hash.slice(1)] ? location.hash.slice(1) : 'home',
  );
  const tabs = ref(['home']),
    collapsed = ref(false),
    expanded = reactive({ analysis: false, system: false });
  const can = (permission) => allowed.value.includes(permission);
  const canPage = (id) => !pagePermissions[id] || can(pagePermissions[id]);
  const visibleNavigation = computed(() =>
    navigation.flatMap((item) => {
      if (!item.children) return canPage(item.id) ? [item] : [];
      const children = item.children.filter((child) => canPage(child.id));
      return children.length ? [{ ...item, children }] : [];
    }),
  );
  const page = computed(() => pageMap[current.value]);
  const showData = ref(false),
    toast = ref('');
  let toastTimer,
    taskTimer,
    previousFocus,
    pageSequence = 0,
    profileSequence = 0;
  const stats = ref(emptyStats()),
    activeDataset = ref(null),
    datasetList = ref([]),
    datasetPage = ref(1),
    datasetTotal = ref(0);
  const source = computed(() => activeDataset.value?.source || '尚未导入数据');
  const sourceFile = computed(
    () => activeDataset.value?.filename || '尚未选择数据集',
  );
  const dateRange = computed(() =>
    activeDataset.value?.start_ms
      ? `${dayOf(activeDataset.value.start_ms)} 至 ${dayOf(activeDataset.value.end_ms)}`
      : '暂无日期',
  );
  const kpis = computed(() => [
    {
      label: '浏览量',
      value: stats.value.counts.pv,
      icon: 'Eye',
      color: 'blue',
      note: '浏览事件累计',
    },
    {
      label: '活跃用户',
      value: stats.value.users,
      icon: 'UserRound',
      color: 'teal',
      note: '按用户 ID 去重',
    },
    {
      label: '加购量',
      value: stats.value.counts.cart,
      icon: 'ShoppingCart',
      color: 'gold',
      note: '加购事件累计',
    },
    {
      label: '购买量',
      value: stats.value.counts.buy,
      icon: 'ShoppingBag',
      color: 'purple',
      note: '购买事件累计',
    },
  ]);
  const hours = ref([]),
    path = ref([
      { name: '浏览商品', value: 0 },
      { name: '浏览后加购', value: 0 },
      { name: '加购后购买', value: 0 },
    ]);
  const cohorts = ref([]),
    categoryRank = ref([]),
    productRank = ref([]),
    segments = ref([]);
  const retentionAverage = computed(() => {
    const available = cohorts.value.filter((c) => c.retention[1] !== null),
      total = available.reduce((sum, c) => sum + c.users, 0);
    return total
      ? available.reduce((sum, c) => sum + c.users * c.retention[1], 0) / total
      : null;
  });
  const profiles = ref([]),
    userSearch = ref(''),
    userPage = ref(1),
    userTotal = ref(0);
  const rfmUsers = ref([]),
    segmentFilter = ref('全部分群'),
    rfPage = ref(1),
    rfTotal = ref(0);
  const recUser = ref(''),
    recReady = ref(false),
    recItems = ref([]),
    selectedProfile = ref(null),
    recommendationError = ref('');
  const modelInfo = ref({ status: 'not_connected', model: null });
  const trainingStatus = computed(() =>
    modelInfo.value.status === 'incompatible'
      ? '模型与当前数据集不兼容'
      : modelInfo.value.model
        ? modelInfo.value.model.name
        : '等待算法组接入',
  );
  const dialog = ref(null),
    modalElement = ref(null),
    dialogUser = ref(null),
    dialogLoading = ref(false);
  const importPreview = ref(null),
    importError = ref(''),
    importName = ref(''),
    importInput = ref(null),
    pasteCSV = ref('');
  const importFile = ref(null),
    importJob = ref(null);
  const serverFiles = ref([]),
    serverForm = reactive({ relative_path: '', name: '' });
  const query = reactive({ keyword: '', type: '', from: '', to: '' }),
    applied = ref({ ...query });
  const dataPage = ref(1),
    dataTab = ref('records'),
    visibleRows = ref([]),
    behaviorTotal = ref(0);
  const pageCount = computed(() =>
    Math.max(1, Math.ceil(behaviorTotal.value / 10)),
  );
  const jobs = ref([]),
    jobPage = ref(1),
    jobTotal = ref(0);
  const logs = ref([]),
    logModule = ref('全部模块'),
    logPage = ref(1),
    logTotal = ref(0);
  const filteredLogs = computed(() => logs.value);
  const messages = ref([]),
    messagePage = ref(1),
    messageTotal = ref(0),
    messageForm = reactive({ content: '', type: '功能建议' });
  const accounts = ref([]),
    accountPage = ref(1),
    accountTotal = ref(0),
    accountError = ref('');
  const accountForm = reactive({
    id: '',
    name: '',
    role_id: 'analyst',
    enabled: true,
    password: '',
  });
  const passwordForm = reactive({ password: '', current_password: '' });
  const permissions = ref([]),
    rolePermissions = reactive({}),
    roleIds = reactive({}),
    roleEditing = ref(false);

  function notify(message) {
    clearTimeout(toastTimer);
    toast.value = message;
    toastTimer = setTimeout(() => {
      toast.value = '';
    }, 5000);
  }
  function loseSession() {
    authenticated.value = false;
    allowed.value = [];
    setSession('');
    closeDialog();
    stats.value = emptyStats();
    profiles.value = [];
    selectedProfile.value = null;
    visibleRows.value = [];
    logs.value = [];
    messages.value = [];
    accounts.value = [];
    jobs.value = [];
    recItems.value = [];
    recReady.value = false;
    activeDataset.value = null;
  }
  function acceptSession(session) {
    setSession(session.csrf_token, loseSession);
    Object.assign(personal, session.account);
    allowed.value = session.permissions;
    authenticated.value = true;
  }
  async function login(body) {
    authBusy.value = true;
    authError.value = '';
    try {
      acceptSession(await api('/auth/login', { method: 'POST', body }));
      tabs.value = ['home'];
      await hydrate();
    } catch (error) {
      authError.value = error.message;
    } finally {
      authBusy.value = false;
    }
  }
  async function logout() {
    try {
      await api('/auth/logout', { method: 'POST' });
    } finally {
      loseSession();
    }
  }
  async function hydrate() {
    loadError.value = '';
    loading.value = true;
    try {
      const previousId = activeDataset.value?.id;
      const { dataset } = await api('/datasets/active');
      activeDataset.value = dataset;
      hours.value = [];
      cohorts.value = [];
      categoryRank.value = [];
      productRank.value = [];
      segments.value = [];
      rfmUsers.value = [];
      stats.value = dataset
        ? await api('/overview' + queryString({ dataset_id: dataset.id }))
        : emptyStats();
      if (dataset && dataset.id !== previousId) {
        Object.assign(query, {
          keyword: '',
          type: '',
          from: dayOf(dataset.end_ms - 6 * 86400000),
          to: dayOf(dataset.end_ms),
        });
        applied.value = { ...query };
        dataPage.value = 1;
      }
      profiles.value = [];
      recUser.value = '';
      selectedProfile.value = null;
      recItems.value = [];
      recReady.value = false;
      if (can('推荐查看')) modelInfo.value = await api('/models/active');
      if (!canPage(current.value)) current.value = 'home';
      if (!tabs.value.includes(current.value)) tabs.value.push(current.value);
      if (page.value.parent === '数据分析') expanded.analysis = true;
      if (page.value.parent === '系统管理') expanded.system = true;
      await loadPage();
    } catch (error) {
      loadError.value = error.message;
    } finally {
      loading.value = false;
    }
  }
  function navigate(id) {
    if (!pageMap[id] || !canPage(id)) return;
    current.value = id;
    if (!tabs.value.includes(id)) tabs.value.push(id);
    if (pageMap[id].parent === '数据分析') expanded.analysis = true;
    if (pageMap[id].parent === '系统管理') expanded.system = true;
    showData.value = false;
    history.replaceState(null, '', `#${id}`);
    run(loadPage);
  }
  function closeTab(id) {
    const index = tabs.value.indexOf(id);
    tabs.value = tabs.value.filter((t) => t !== id);
    if (current.value === id)
      navigate(tabs.value[Math.max(0, index - 1)] || 'home');
  }
  async function run(action) {
    try {
      await action();
    } catch (error) {
      notify(error.message);
    }
  }
  async function refreshPage() {
    await hydrate();
    if (!loadError.value) notify('已从数据库刷新当前视图。');
  }
  async function loadPage() {
    const id = current.value,
      sequence = ++pageSequence;
    loading.value = true;
    loadError.value = '';
    try {
      const dataset = activeDataset.value;
      const metricRefs = {
        hourly: [hours, 'hourly'],
        funnel: [path, 'funnel'],
        retention: [cohorts, 'retention'],
        categories: [categoryRank, 'category'],
        products: [productRank, 'product'],
      };
      if (dataset && metricRefs[id]) {
        const [target, metric] = metricRefs[id];
        const result = await api(`/analytics/${metric}${datasetQuery()}`);
        if (sequence === pageSequence) target.value = result.items;
      }
      if (dataset && id === 'screen')
        categoryRank.value = (
          await api(`/analytics/category${datasetQuery()}`)
        ).items;
      if (id === 'rfm' && dataset) {
        segments.value = (await api(`/analytics/rf${datasetQuery()}`)).items;
        await loadRF(1);
      }
      if (id === 'recommend' && dataset) {
        if (can('行为分析')) await loadUsers(1);
        modelInfo.value = await api('/models/active');
      }
      if (id === 'data') {
        await loadDatasets();
        if (dataset) await loadBehaviors();
        if (can('数据导入') || can('任务管理')) await loadJobs();
      }
      if (id === 'logs') await loadLogs();
      if (id === 'messages') await loadMessages();
      if (id === 'accounts') await loadAccounts();
      if (id === 'roles') await loadRoles();
    } catch (error) {
      if (sequence === pageSequence) loadError.value = error.message;
    } finally {
      if (sequence === pageSequence) loading.value = false;
    }
  }
  const datasetQuery = () =>
    queryString({ dataset_id: activeDataset.value?.id });
  async function loadUsers(pageNumber = userPage.value) {
    const result = await api(
      '/users' +
        queryString({
          dataset_id: activeDataset.value.id,
          page: pageNumber,
          page_size: 50,
          keyword: userSearch.value,
        }),
    );
    profiles.value = result.items;
    userPage.value = result.page;
    userTotal.value = result.total;
    if (!recUser.value && profiles.value.length)
      recUser.value = profiles.value[0].user;
  }
  async function loadRF(pageNumber = rfPage.value) {
    const result = await api(
      '/users' +
        queryString({
          dataset_id: activeDataset.value.id,
          page: pageNumber,
          page_size: 50,
          segment:
            segmentFilter.value === '全部分群' ? '' : segmentFilter.value,
        }),
    );
    rfmUsers.value = result.items;
    rfPage.value = result.page;
    rfTotal.value = result.total;
  }
  async function loadBehaviors() {
    if (!activeDataset.value) return;
    const snapshot = JSON.stringify(applied.value),
      requestedPage = dataPage.value;
    const result = await api(
      '/behaviors' + behaviorQuery({ page: dataPage.value, page_size: 10 }),
    );
    if (
      result.dataset_id !== activeDataset.value?.id ||
      requestedPage !== dataPage.value ||
      snapshot !== JSON.stringify(applied.value)
    )
      return;
    visibleRows.value = result.items;
    behaviorTotal.value = result.total;
  }
  function behaviorQuery(extra = {}) {
    return queryString({
      dataset_id: activeDataset.value?.id,
      keyword: applied.value.keyword,
      behavior: applied.value.type,
      from_day: applied.value.from,
      to_day: applied.value.to,
      ...extra,
    });
  }
  async function loadDatasets() {
    const result = await api(
      '/datasets' + queryString({ page: datasetPage.value }),
    );
    datasetList.value = result.items;
    datasetTotal.value = result.total;
  }
  async function loadJobs() {
    const result = await api('/jobs' + queryString({ page: jobPage.value }));
    jobs.value = result.items;
    jobTotal.value = result.total;
  }
  async function loadLogs() {
    const result = await api(
      '/logs' +
        queryString({
          page: logPage.value,
          module: logModule.value === '全部模块' ? '' : logModule.value,
        }),
    );
    logs.value = result.items.map((l) => ({
      ...l,
      time: dateTime(l.created_at * 1000),
    }));
    logTotal.value = result.total;
  }
  async function loadMessages() {
    const result = await api(
      '/messages' + queryString({ page: messagePage.value }),
    );
    messages.value = result.items.map((m) => ({
      ...m,
      time: dateTime(m.created_at * 1000),
    }));
    messageTotal.value = result.total;
  }
  async function loadAccounts() {
    const result = await api(
      '/accounts' + queryString({ page: accountPage.value }),
    );
    accounts.value = result.items.map((a) => ({
      ...a,
      created: dayOf(a.created_at * 1000),
    }));
    accountTotal.value = result.total;
  }
  async function loadRoles() {
    const result = await api('/roles');
    permissions.value = result.permissions;
    for (const key of Object.keys(rolePermissions)) delete rolePermissions[key];
    for (const role of result.items) {
      rolePermissions[role.name] = role.permissions.slice();
      roleIds[role.name] = role.id;
    }
  }
  function openDialog(value) {
    previousFocus = document.activeElement;
    dialog.value = value;
    nextTick(() => modalElement.value?.querySelector('button,input')?.focus());
  }
  function closeDialog() {
    dialog.value = null;
    nextTick(() => previousFocus?.isConnected && previousFocus.focus());
  }
  function handleKeys(event) {
    if (!dialog.value) return;
    if (event.key === 'Escape' && !busy.value) {
      closeDialog();
      return;
    }
    if (event.key !== 'Tab') return;
    const elements = [
      ...modalElement.value.querySelectorAll(
        'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]',
      ),
    ].filter((el) => el.getClientRects().length);
    const first = elements[0],
      last = elements.at(-1);
    if (!first) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  async function inspectUser(id) {
    dialogUser.value = null;
    dialogLoading.value = true;
    openDialog({ kind: 'user', title: '用户画像', user: id });
    try {
      const result = await api(
        `/users/${encodeURIComponent(id)}/profile${datasetQuery()}`,
      );
      if (dialog.value?.user === id) dialogUser.value = result;
    } catch (error) {
      notify(error.message);
    } finally {
      dialogLoading.value = false;
    }
  }
  async function generateRecommendation() {
    if (!recUser.value.trim()) {
      notify('请先填写或选择用户 ID。');
      return;
    }
    const user = recUser.value,
      dataset = activeDataset.value?.id;
    busy.value = true;
    recommendationError.value = '';
    try {
      const result = await api(
        '/recommendations' + queryString({ user_id: user }),
      );
      if (recUser.value !== user || activeDataset.value?.id !== dataset) return;
      recItems.value = result.items.map((i) => ({
        ...i,
        id: i.item_id,
        category: i.category_id,
      }));
      recReady.value = true;
    } catch (error) {
      recItems.value = [];
      recReady.value = false;
      recommendationError.value = error.message;
    } finally {
      busy.value = false;
    }
  }
  function recommendForUser(id) {
    closeDialog();
    recUser.value = id;
    navigate('recommend');
    run(generateRecommendation);
  }
  function showImport() {
    importPreview.value = null;
    importFile.value = null;
    importError.value = '';
    importName.value = '';
    pasteCSV.value = '';
    importJob.value = null;
    openDialog({ kind: 'import', title: '导入用户行为数据' });
  }
  async function showServerImport() {
    importError.value = '';
    importJob.value = null;
    serverForm.relative_path = '';
    serverForm.name = '';
    serverFiles.value = (await api('/datasets/inbox')).items;
    openDialog({ kind: 'server-import', title: '导入服务器上的数据文件' });
  }
  function checkText(text, name) {
    const file = new File([text], name, { type: 'text/csv' });
    importFile.value = file;
    importName.value = name;
    importPreview.value = { bytes: file.size };
    importError.value = '';
  }
  function readFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      importError.value = '网页上传上限 100 MB；更大文件请使用服务器文件导入。';
      return;
    }
    importFile.value = file;
    importName.value = file.name;
    importPreview.value = { bytes: file.size };
    importError.value = '';
  }
  async function waitJob(id) {
    for (let i = 0; i < 600 && authenticated.value; i++) {
      const job = await api(`/jobs/${id}`);
      importJob.value = job;
      if (['ready', 'failed'].includes(job.status)) {
        if (job.status === 'failed') throw new Error(job.error);
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error('任务仍在后台运行，可到后台任务列表继续查看。');
  }
  async function applyImport() {
    if (!importFile.value || busy.value) return;
    busy.value = true;
    importError.value = '';
    try {
      const body = new FormData();
      body.append('file', importFile.value);
      const result = await api('/datasets/import', { method: 'POST', body });
      importJob.value = result.job;
      await waitJob(result.job.id);
      closeDialog();
      current.value = 'data';
      await hydrate();
      notify('数据已入库，统计已更新；刷新和重启后仍然保留。');
    } catch (error) {
      importError.value = error.message;
    } finally {
      busy.value = false;
    }
  }
  async function applyServerImport() {
    busy.value = true;
    importError.value = '';
    try {
      const result = await api('/datasets/import-local', {
        method: 'POST',
        body: { ...serverForm, activate: true },
      });
      importJob.value = result.job;
      await waitJob(result.job.id);
      closeDialog();
      current.value = 'data';
      await hydrate();
      notify('服务器文件已导入。');
    } catch (error) {
      importError.value = error.message;
    } finally {
      busy.value = false;
    }
  }
  async function activateDataset(id) {
    await api(`/datasets/${id}/activate`, { method: 'POST' });
    await hydrate();
    notify('已切换数据集。');
  }
  async function restoreDemo() {
    const demo = datasetList.value.find(
      (d) => d.is_demo && d.status === 'ready',
    );
    if (demo) await activateDataset(demo.id);
    else notify('当前列表没有演示数据集，可在数据集列表中选择其他已完成版本。');
  }
  async function downloadJob(id) {
    saveBlob(
      '用户行为数据.csv',
      await api(`/jobs/${id}/download`, { blob: true }),
    );
  }
  async function exportRows() {
    busy.value = true;
    try {
      const job = await api('/behaviors/export' + behaviorQuery(), {
        method: 'POST',
      });
      notify('导出任务已创建，正在后台生成。');
      await waitJob(job.id);
      await downloadJob(job.id);
      await loadJobs();
    } finally {
      busy.value = false;
    }
  }
  function exportDaily() {
    saveBlob(
      '每日趋势统计.csv',
      toCSV(stats.value.days, [
        { label: '日期', value: (r) => r.day },
        ...[
          ['浏览数', 'pv'],
          ['用户数', 'users'],
          ['收藏数', 'fav'],
          ['加购数', 'cart'],
          ['购买数', 'buy'],
        ].map(([label, key]) => ({ label, value: (r) => r[key] })),
      ]),
    );
  }
  async function downloadTemplate() {
    saveBlob(
      '行为数据导入模板.csv',
      await api('/datasets/template', { blob: true }),
    );
  }
  async function applyQuery() {
    if (query.from && query.to && query.from > query.to) {
      notify('开始日期不能晚于结束日期。');
      return;
    }
    applied.value = { ...query };
    dataPage.value = 1;
    await loadBehaviors();
  }
  async function resetQuery() {
    Object.assign(query, { keyword: '', type: '', from: '', to: '' });
    await applyQuery();
  }
  async function postMessage() {
    if (!messageForm.content.trim()) return;
    await api('/messages', { method: 'POST', body: messageForm });
    messageForm.content = '';
    await loadMessages();
    notify('留言已保存。');
  }
  async function toggleMessage(message) {
    await api(`/messages/${message.id}`, {
      method: 'PATCH',
      body: { done: !message.done },
    });
    await loadMessages();
  }
  function editAccount(account) {
    accountError.value = '';
    Object.assign(
      accountForm,
      account
        ? {
            id: account.id,
            name: account.name,
            role_id: account.role_id,
            enabled: account.enabled,
            password: '',
          }
        : { id: '', name: '', role_id: 'analyst', enabled: true, password: '' },
    );
    openDialog({
      kind: 'account',
      title: account ? '编辑账号' : '创建系统账号',
      editing: Boolean(account),
    });
  }
  async function saveAccount() {
    busy.value = true;
    accountError.value = '';
    try {
      const editing = dialog.value.editing,
        body = {
          name: accountForm.name,
          role_id: accountForm.role_id,
          enabled: accountForm.enabled,
        };
      if (!editing)
        Object.assign(body, {
          username: accountForm.id,
          password: accountForm.password,
        });
      const result = await api(
        editing
          ? `/accounts/${encodeURIComponent(accountForm.id)}`
          : '/accounts',
        { method: editing ? 'PATCH' : 'POST', body },
      );
      if (result.id === personal.id) Object.assign(personal, result);
      closeDialog();
      await loadAccounts();
      notify('系统账号已保存。');
    } catch (error) {
      accountError.value =
        error.details?.map((d) => `${d.field}: ${d.message}`).join('；') ||
        error.message;
    } finally {
      busy.value = false;
    }
  }
  async function toggleAccount(account) {
    await api(`/accounts/${encodeURIComponent(account.id)}`, {
      method: 'PATCH',
      body: {
        name: account.name,
        role_id: account.role_id,
        enabled: !account.enabled,
      },
    });
    await loadAccounts();
  }
  async function toggleRoleEditing() {
    if (!roleEditing.value) {
      roleEditing.value = true;
      return;
    }
    for (const [name, allowed] of Object.entries(rolePermissions))
      if (roleIds[name] !== 'admin')
        await api(`/roles/${roleIds[name]}`, {
          method: 'PATCH',
          body: { permissions: allowed },
        });
    roleEditing.value = false;
    await loadRoles();
    notify('角色权限已保存，后端立即执行。');
  }
  async function savePersonal() {
    Object.assign(
      personal,
      await api('/auth/me', {
        method: 'PATCH',
        body: {
          name: personal.name,
          email: personal.email,
          team: personal.team,
          note: personal.note,
        },
      }),
    );
    notify('个人资料已保存。');
  }
  function showPassword(account = null) {
    passwordForm.password = '';
    passwordForm.current_password = '';
    accountError.value = '';
    openDialog({
      kind: 'password',
      title: account ? `重置 ${account.id} 的密码` : '修改登录密码',
      account: account?.id,
    });
  }
  async function savePassword() {
    busy.value = true;
    accountError.value = '';
    try {
      const id = dialog.value.account;
      await api(
        id ? `/accounts/${encodeURIComponent(id)}/password` : '/auth/password',
        {
          method: 'POST',
          body: id ? { password: passwordForm.password } : { ...passwordForm },
        },
      );
      closeDialog();
      notify('密码已修改，该账号需要重新登录。');
      if (!id || id === personal.id) loseSession();
    } catch (error) {
      accountError.value = error.message;
    } finally {
      busy.value = false;
    }
  }
  async function recoverJob(job) {
    await api(`/jobs/${job.id}/recover`, { method: 'POST' });
    await loadJobs();
  }
  function retentionStyle(value) {
    return value === null
      ? {}
      : {
          background: `rgba(62, 145, 165, ${0.07 + value * 0.65})`,
          color: value > 0.68 ? '#fff' : '#427482',
        };
  }
  const percentage = (n, d) => (d ? `${((n / d) * 100).toFixed(1)}%` : '—');
  const behaviorText = (type) => behaviorLabels[type] || type;
  watch(recUser, async (id) => {
    recReady.value = false;
    recItems.value = [];
    recommendationError.value = '';
    selectedProfile.value = null;
    const sequence = ++profileSequence;
    if (!id || !activeDataset.value || !can('行为分析')) return;
    try {
      const result = await api(
        `/users/${encodeURIComponent(id)}/profile${datasetQuery()}`,
      );
      if (sequence === profileSequence) selectedProfile.value = result;
    } catch (error) {
      if (error.status !== 404) notify(error.message);
    }
  });
  watch(segmentFilter, () => {
    if (authenticated.value && activeDataset.value) run(() => loadRF(1));
  });
  watch(dataPage, () => run(loadBehaviors));
  watch(logModule, () => {
    logPage.value = 1;
    run(loadLogs);
  });
  onMounted(async () => {
    document.addEventListener('keydown', handleKeys);
    setSession('', loseSession);
    try {
      acceptSession(await api('/auth/me'));
      await hydrate();
    } catch (error) {
      if (error.status !== 401) authError.value = error.message;
    } finally {
      authReady.value = true;
    }
    taskTimer = setInterval(() => {
      if (
        authenticated.value &&
        current.value === 'data' &&
        dataTab.value === 'jobs'
      )
        run(loadJobs);
    }, 5000);
  });
  onBeforeUnmount(() => {
    document.removeEventListener('keydown', handleKeys);
    clearTimeout(toastTimer);
    clearInterval(taskTimer);
    authenticated.value = false;
  });

  return {
    authenticated,
    authReady,
    authBusy,
    authError,
    login,
    logout,
    loading,
    loadError,
    busy,
    can,
    run,
    visibleNavigation,
    personal,
    current,
    tabs,
    collapsed,
    expanded,
    page,
    showData,
    toast,
    stats,
    activeDataset,
    datasetList,
    datasetPage,
    datasetTotal,
    source,
    sourceFile,
    dateRange,
    kpis,
    hours,
    path,
    cohorts,
    categoryRank,
    productRank,
    segments,
    retentionAverage,
    profiles,
    userSearch,
    userPage,
    userTotal,
    rfmUsers,
    segmentFilter,
    rfPage,
    rfTotal,
    recUser,
    recReady,
    recItems,
    selectedProfile,
    recommendationError,
    modelInfo,
    trainingStatus,
    dialog,
    modalElement,
    dialogUser,
    dialogLoading,
    importPreview,
    importError,
    importName,
    importInput,
    pasteCSV,
    importJob,
    query,
    dataPage,
    dataTab,
    visibleRows,
    behaviorTotal,
    pageCount,
    jobs,
    jobPage,
    jobTotal,
    filteredLogs,
    logModule,
    logPage,
    logTotal,
    messages,
    messagePage,
    messageTotal,
    messageForm,
    accounts,
    accountPage,
    accountTotal,
    accountError,
    accountForm,
    passwordForm,
    permissions,
    rolePermissions,
    roleIds,
    roleEditing,
    notify,
    navigate,
    closeTab,
    refreshPage,
    openDialog,
    closeDialog,
    generateRecommendation,
    inspectUser,
    recommendForUser,
    showImport,
    checkText,
    readFile,
    applyImport,
    activateDataset,
    restoreDemo,
    exportRows,
    exportDaily,
    downloadTemplate,
    postMessage,
    toggleMessage,
    editAccount,
    saveAccount,
    toggleAccount,
    toggleRoleEditing,
    savePersonal,
    showPassword,
    savePassword,
    applyQuery,
    resetQuery,
    loadUsers,
    loadRF,
    loadDatasets,
    loadJobs,
    loadLogs,
    loadMessages,
    loadAccounts,
    downloadJob,
    recoverJob,
    behaviorText,
    percentage,
    retentionStyle,
    serverFiles,
    serverForm,
    showServerImport,
    applyServerImport,
  };
}
