<script setup>
import { ref, reactive, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import AppIcon from './components/AppIcon.vue';
import AppChart from './components/AppChart.vue';
import { navigation, pageMap } from './navigation.js';
import { createDemoData, summarize, getProfiles, hourly, purchaseRanking, conversionPath, retention, demoRecommendations, behaviorLabels, number, dateTime, dayOf, labelCategory } from './data/behavior.js';
import { parseBehaviorCSV, toCSV } from './data/csv.js';
import { trendOption, funnelOption, hourlyOption, rankingOption, segmentOption, interestOption } from './data/charts.js';

const rows = ref(createDemoData());
const source = ref('演示数据');
const sourceFile = ref('用户行为演示样本.csv');
const current = ref(pageMap[location.hash.slice(1)] ? location.hash.slice(1) : 'home');
const tabs = ref(current.value === 'home' ? ['home'] : ['home', current.value]);
const collapsed = ref(false);
const expanded = reactive({ analysis: Boolean(pageMap[current.value]?.parent === '数据分析'), system: Boolean(pageMap[current.value]?.parent === '系统管理') });
const showData = ref(false);
const toast = ref('');
let toastTimer, trainTimer;
const stats = computed(() => summarize(rows.value));
const profiles = computed(() => getProfiles(rows.value));
const hours = computed(() => hourly(rows.value));
const path = computed(() => conversionPath(rows.value));
const cohorts = computed(() => retention(rows.value));
const categoryRank = computed(() => purchaseRanking(rows.value));
const productRank = computed(() => purchaseRanking(rows.value, 'item'));
const page = computed(() => pageMap[current.value]);
const dateRange = computed(() => stats.value.days.length ? `${stats.value.days[0].day} 至 ${stats.value.days.at(-1).day}` : '暂无日期');
const kpis = computed(() => [
  { label: '浏览量', value: stats.value.counts.pv, icon: 'Eye', color: 'blue', note: '浏览事件累计' },
  { label: '活跃用户', value: stats.value.users, icon: 'UserRound', color: 'teal', note: '按用户 ID 去重' },
  { label: '加购量', value: stats.value.counts.cart, icon: 'ShoppingCart', color: 'gold', note: '加购事件累计' },
  { label: '购买量', value: stats.value.counts.buy, icon: 'ShoppingBag', color: 'purple', note: '购买事件累计' },
]);
const logs = ref([
  { id: 1, time: '2026/11/14 15:30:00', module: '系统', action: '打开展示工作台', status: '成功', detail: '前端展示环境已就绪' },
  { id: 2, time: '2026/11/14 15:30:01', module: '数据管理', action: '加载演示样本', status: '成功', detail: '已载入可交互的虚构用户行为数据' },
]);
const logModule = ref('全部模块');
const filteredLogs = computed(() => logs.value.filter(l => logModule.value === '全部模块' || l.module === logModule.value).slice().reverse());
const recUser = ref('1003707');
const strategy = ref('bpr');
const recReady = ref(true);
const recItems = ref(demoRecommendations(recUser.value, rows.value));
const selectedProfile = computed(() => profiles.value.find(p => p.user === recUser.value));
const recVersion = ref(1);
const training = ref(false);
const trainingStatus = ref('模型待接入');
const trainingForm = reactive({ rank: '32', iterations: '20', method: 'bpr' });
const dialog = ref(null);
const modalElement = ref(null);
let previousFocus;
const dialogUser = computed(() => profiles.value.find(p => p.user === dialog.value?.user));
const importPreview = ref(null), importError = ref(''), importName = ref('');
const importInput = ref(null);
const pasteCSV = ref('');
const query = reactive({ keyword: '', type: '', from: '', to: '' });
const applied = ref({ ...query });
const dataPage = ref(1), dataTab = ref('records');
const filteredRows = computed(() => rows.value.filter(r => {
  const q = applied.value, d = dayOf(r.t), term = q.keyword.trim();
  return (!term || r.user.includes(term) || r.item.includes(term)) && (!q.type || r.type === q.type) && (!q.from || d >= q.from) && (!q.to || d <= q.to);
}).slice().sort((a, b) => b.t - a.t));
const pageCount = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / 10)));
const visibleRows = computed(() => filteredRows.value.slice((dataPage.value - 1) * 10, dataPage.value * 10));
const segmentFilter = ref('全部分群');
const rfmUsers = computed(() => profiles.value.filter(p => segmentFilter.value === '全部分群' || p.segment === segmentFilter.value));
const retentionAverage = computed(() => { const available = cohorts.value.filter(c => c.retention[1] !== null); const total = available.reduce((sum, c) => sum + c.users, 0); return total ? available.reduce((sum, c) => sum + c.users * c.retention[1], 0) / total : null; });
const messages = ref([
  { id: 1, name: '项目组', type: '功能建议', content: '先完成数据分析与推荐页面，后续逐步接入真实数据和推荐模型。', time: '2026/11/14 14:20:00', done: false },
  { id: 2, name: '管理员', type: '数据说明', content: '当前为前端展示。匿名商品只展示商品 ID 和类目 ID，不编造商品价格。', time: '2026/11/14 14:25:00', done: true },
]);
const messageForm = reactive({ content: '', type: '功能建议' });
const personal = reactive({ name: '管理员', email: 'admin@example.com', team: '软件工程项目组', note: '电商平台用户行为分析与精准推荐系统' });
const accounts = ref([
  { id: 'admin', name: '管理员', role: '管理员', enabled: true, created: '2026-11-01' },
  { id: 'analyst', name: '数据分析员', role: '分析员', enabled: true, created: '2026-11-02' },
  { id: 'viewer', name: '项目演示账号', role: '访客', enabled: true, created: '2026-11-03' },
]);
const accountForm = reactive({ id: '', name: '', role: '分析员', enabled: true });
const accountError = ref('');
const permissions = ['数据概览', '行为分析', '推荐查看', '数据导入', '任务管理', '账号管理'];
const rolePermissions = reactive({ '管理员': permissions.slice(), '分析员': permissions.slice(0, 4), '访客': permissions.slice(0, 3) });
const roleEditing = ref(false);

function notify(message) { clearTimeout(toastTimer); toast.value = message; toastTimer = setTimeout(() => { toast.value = ''; }, 4200); }
function addLog(module, action, detail, status = '成功') { logs.value.push({ id: Date.now() + logs.value.length, time: dateTime(Date.now()), module, action, detail, status }); }
function navigate(id) {
  if (!pageMap[id]) return;
  current.value = id;
  if (!tabs.value.includes(id)) tabs.value.push(id);
  if (pageMap[id].parent === '数据分析') expanded.analysis = true;
  if (pageMap[id].parent === '系统管理') expanded.system = true;
  showData.value = false;
  history.replaceState(null, '', `#${id}`);
}
function closeTab(id) { const index = tabs.value.indexOf(id); tabs.value = tabs.value.filter(t => t !== id); if (current.value === id) navigate(tabs.value[Math.max(0, index - 1)] || 'home'); }
function refreshPage() { addLog('系统', '刷新当前视图', page.value.title); notify('当前视图已刷新，展示数据已保留。'); }
function openDialog(value) { previousFocus = document.activeElement; dialog.value = value; nextTick(() => modalElement.value?.querySelector('button')?.focus()); }
function closeDialog() { dialog.value = null; nextTick(() => previousFocus?.isConnected && previousFocus.focus()); }
function handleKeys(event) {
  if (!dialog.value) return;
  if (event.key === 'Escape') { closeDialog(); return; }
  if (event.key === 'Tab') {
    const elements = [...modalElement.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(el => el.getClientRects().length);
    if (!elements.length) return;
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
}
function generateRecommendation() { recItems.value = demoRecommendations(recUser.value, rows.value, strategy.value); recReady.value = true; addLog('智能推荐', '生成推荐', `用户 ${recUser.value}，前端规则预览 ${recItems.value.length} 件商品`); notify('推荐结果已更新。本次使用前端演示规则。'); }
function startTraining() {
  closeDialog(); training.value = true; trainingStatus.value = '演示任务进行中';
  addLog('智能推荐', '创建训练演示任务', `${trainingForm.method.toUpperCase()} / rank=${trainingForm.rank} / 迭代=${trainingForm.iterations}`, '演示');
  trainTimer = setTimeout(() => { training.value = false; recVersion.value++; trainingStatus.value = '演示任务完成 · 模型待接入'; addLog('智能推荐', '训练流程演示完成', '仅更新演示任务状态，未训练真实模型', '演示'); notify('训练流程演示完成，真实训练服务尚未接入。'); }, 1400);
}
function download(name, content) { const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8;' })); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); notify('导出文件已生成。'); }
function exportRows() { download('用户行为数据.csv', toCSV(filteredRows.value, [
  { label: 'user_id', value: r => r.user }, { label: 'item_id', value: r => r.item }, { label: 'category_id', value: r => r.category }, { label: 'behavior_type', value: r => r.type }, { label: 'timestamp', value: r => Math.floor(r.t / 1000) },
])); addLog('数据管理', '导出行为数据', `已导出 ${filteredRows.value.length} 条当前筛选记录`); }
function exportDaily() { download('每日趋势统计.csv', toCSV(stats.value.days, [ { label: '日期', value: d => d.day }, { label: '浏览数', value: d => d.pv }, { label: '用户数', value: d => d.users }, { label: '收藏数', value: d => d.fav }, { label: '加购数', value: d => d.cart }, { label: '购买数', value: d => d.buy } ])); }
function downloadTemplate() { download('行为数据导入模板.csv', 'user_id,item_id,category_id,behavior_type,timestamp\r\n1003707,1325619,982926,pv,1794614400\r\n1003707,1325619,982926,cart,1794614700\r\n1003707,1325619,982926,buy,1794615000'); }
function showImport() { importPreview.value = null; importError.value = ''; importName.value = ''; pasteCSV.value = ''; openDialog({ kind: 'import', title: '导入用户行为数据' }); }
function checkText(text, name) { try { importPreview.value = parseBehaviorCSV(text); importName.value = name; importError.value = ''; } catch (error) { importPreview.value = null; importError.value = error.message; } }
async function readFile(event) { const file = event.target.files?.[0]; if (!file) return; if (file.size > 10 * 1024 * 1024) { importError.value = '文件超过 10 MB，请先抽样。'; return; } try { checkText(await file.text(), file.name); } catch (error) { importError.value = `读取文件失败：${error.message}`; } }
function applyImport() {
  if (!importPreview.value) return;
  rows.value = importPreview.value.rows; source.value = '自定义数据'; sourceFile.value = importName.value;
  recUser.value = profiles.value[0]?.user || ''; recReady.value = false; recItems.value = [];
  Object.assign(query, { keyword: '', type: '', from: '', to: '' }); applied.value = { ...query }; dataPage.value = 1;
  addLog('数据管理', '导入行为数据', `${rows.value.length} 条有效记录，跳过 ${importPreview.value.invalid} 条无效记录，合并 ${importPreview.value.duplicate} 条完全重复记录`);
  closeDialog(); navigate('data'); notify('数据已导入，首页、分析和用户画像已同步更新。刷新页面将恢复演示数据。');
}
function restoreDemo() { rows.value = createDemoData(); source.value = '演示数据'; sourceFile.value = '用户行为演示样本.csv'; recUser.value = '1003707'; recItems.value = demoRecommendations(recUser.value, rows.value, strategy.value); recReady.value = true; Object.assign(query, { keyword: '', type: '', from: '', to: '' }); applied.value = { ...query }; dataPage.value = 1; addLog('数据管理', '恢复演示样本', '当前会话已切回虚构演示数据'); notify('已恢复演示样本。'); }
function applyQuery() { if (query.from && query.to && query.from > query.to) { notify('开始日期不能晚于结束日期。'); return; } applied.value = { ...query }; dataPage.value = 1; }
function resetQuery() { Object.assign(query, { keyword: '', type: '', from: '', to: '' }); applyQuery(); }
function inspectUser(id) { openDialog({ kind: 'user', title: '用户画像', user: id }); }
function recommendForUser(id) { closeDialog(); recUser.value = id; generateRecommendation(); navigate('recommend'); }
function postMessage() { if (!messageForm.content.trim()) { notify('请先填写留言内容。'); return; } messages.value.unshift({ id: Date.now(), name: personal.name || '管理员', type: messageForm.type, content: messageForm.content.trim(), time: dateTime(Date.now()), done: false }); messageForm.content = ''; notify('留言已保存至本次前端会话。'); }
function editAccount(account) { accountError.value = ''; Object.assign(accountForm, account || { id: '', name: '', role: '分析员', enabled: true }); openDialog({ kind: 'account', title: account ? '编辑账号' : '添加展示账号', editing: Boolean(account) }); }
function saveAccount() { if (!accountForm.id.trim() || !accountForm.name.trim()) { accountError.value = '账号和显示名称不能为空。'; return; } const found = accounts.value.find(a => a.id === accountForm.id); if (found && !dialog.value.editing) { accountError.value = '该账号已存在。'; return; } if (found) Object.assign(found, accountForm); else accounts.value.push({ ...accountForm, created: dayOf(Date.now()) }); addLog('系统管理', '保存展示账号', accountForm.id, '演示'); closeDialog(); notify('展示账号已更新，实际登录与权限校验待接入。'); }
function savePersonal() { if (!personal.name.trim()) { notify('显示名称不能为空。'); return; } addLog('系统管理', '修改个人资料', '已更新前端显示名称', '演示'); notify('个人资料已更新，仅保留在本次会话。'); }
function retentionStyle(value) { return value === null ? {} : { background: `rgba(62, 145, 165, ${.07 + value * .65})`, color: value > .68 ? '#fff' : '#427482' }; }
const percentage = (n, d) => d ? `${(n / d * 100).toFixed(1)}%` : '—';
const behaviorText = type => behaviorLabels[type] || type;
watch(recUser, () => { recReady.value = false; }, { flush: 'sync' });
watch(strategy, () => { recReady.value = false; }, { flush: 'sync' });
onMounted(() => document.addEventListener('keydown', handleKeys));
onBeforeUnmount(() => { document.removeEventListener('keydown', handleKeys); clearTimeout(toastTimer); clearTimeout(trainTimer); });
</script>

<template>
  <div class="application" :class="{ 'sidebar-collapsed': collapsed }">
    <aside class="sidebar" aria-label="主导航">
      <a class="brand" href="#home" @click.prevent="navigate('home')"><span class="brand-logo"><AppIcon name="ChartNoAxesCombined" :size="26" /></span><span class="brand-name">用户行为分析<span>COMMERCE INSIGHTS</span></span></a>
      <div class="sidebar-section-label">工作空间</div>
      <nav class="side-nav">
        <template v-for="item in navigation" :key="item.id">
          <button v-if="!item.children" class="nav-item" :class="{ active: current === item.id }" :aria-current="current === item.id ? 'page' : undefined" :aria-label="item.title" @click="navigate(item.id)"><AppIcon :name="item.icon" /><span>{{ item.title }}</span><span v-if="item.id === 'recommend'" class="nav-new">AI</span></button>
          <template v-else>
            <button class="nav-item nav-parent" :class="{ 'parent-active': item.children.some(c => c.id === current) }" :aria-expanded="expanded[item.id]" :aria-label="item.title" @click="expanded[item.id] = !expanded[item.id]; if (collapsed) collapsed = false"><AppIcon :name="item.icon" /><span>{{ item.title }}</span><AppIcon name="ChevronDown" class="nav-chevron" :class="{ rotated: expanded[item.id] }" :size="14" /></button>
            <div v-if="expanded[item.id] && !collapsed" class="nav-children"><button v-for="child in item.children" :key="child.id" class="nav-item nav-child" :class="{ active: current === child.id }" :aria-current="current === child.id ? 'page' : undefined" @click="navigate(child.id)"><span class="nav-child-dot"></span>{{ child.title }}</button></div>
          </template>
        </template>
      </nav>
      <div class="sidebar-foot"><span class="live-dot"></span><span>前端展示环境 <small>v0.1.0</small></span></div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb"><button class="icon-button" aria-label="折叠或展开侧栏" @click="collapsed = !collapsed"><AppIcon name="PanelLeft" /></button><button @click="navigate('home')">首页</button><template v-if="current !== 'home'"><span>/</span><span v-if="page.parent">{{ page.parent }}</span><span v-if="page.parent">/</span><span class="breadcrumb-current">{{ page.title }}</span></template></div>
        <div class="topbar-actions"><span class="environment-badge"><span></span>前端演示</span><button class="icon-button" aria-label="刷新当前视图" @click="refreshPage"><AppIcon name="RefreshCw" :size="17" /></button><span class="topbar-divider"></span><button class="account-button" @click="navigate('personal')"><span class="avatar avatar-small">{{ personal.name.slice(0, 1) }}</span><span>{{ personal.name }}</span><AppIcon name="ChevronDown" :size="14" /></button></div>
      </header>
      <div class="tabs-bar" aria-label="已打开页面"><div v-for="id in tabs" :key="id" class="workspace-tab" :class="{ selected: current === id }"><button :aria-current="current === id ? 'page' : undefined" @click="navigate(id)">{{ pageMap[id].title }}</button><button v-if="id !== 'home'" class="tab-close" :aria-label="`关闭${pageMap[id].title}页签`" @click="closeTab(id)"><AppIcon name="X" :size="12" /></button></div></div>

      <main class="main-content" :class="{ 'screen-content': current === 'screen' }">
        <!-- 首页 -->
        <template v-if="current === 'home'">
          <section class="welcome-card panel"><div><span class="eyebrow">用户行为洞察与推荐工作台</span><h1>你好，{{ personal.name }}<span class="welcome-period">.</span></h1><p>从用户行为中发现规律，让每一次推荐更有依据。</p><div class="welcome-meta"><span><AppIcon name="Database" :size="13" />{{ source }}</span><span class="meta-separator"></span><span><AppIcon name="CalendarDays" :size="13" />{{ dateRange }}</span></div></div><div class="welcome-visual" aria-hidden="true"><span class="visual-orbit orbit-one"></span><span class="visual-orbit orbit-two"></span><span class="visual-column column-one"></span><span class="visual-column column-two"></span><span class="visual-column column-three"></span><span class="visual-bubble"><AppIcon name="ChartNoAxesCombined" :size="28" /></span></div></section>
          <section class="stats-grid"><article v-for="kpi in kpis" :key="kpi.label" class="stat-card panel" :class="kpi.color"><div class="stat-label">{{ kpi.label }}<span class="stat-icon"><AppIcon :name="kpi.icon" :size="19" /></span></div><strong>{{ number(kpi.value) }}</strong><div class="stat-footer"><span>{{ kpi.note }}</span><span class="sample-label">{{ source }}</span></div></article></section>
          <section class="two-column dashboard-charts"><article class="panel chart-panel"><div class="panel-heading"><h2><span class="heading-mark"></span>每日行为趋势</h2><button class="text-button" @click="navigate('daily')">查看分析<AppIcon name="ArrowUpRight" :size="14" /></button></div><AppChart :option="trendOption(stats.days)" :height="310" label="每日浏览和购买事件趋势，左右坐标轴分别计量" /></article><article class="panel chart-panel"><div class="panel-heading"><h2><span class="heading-mark teal"></span>行为转化漏斗</h2><span class="muted small">各阶段行为量</span></div><AppChart :option="funnelOption([{ name: '浏览数', value: stats.counts.pv }, { name: '加购数', value: stats.counts.cart }, { name: '购买数', value: stats.counts.buy }])" :height="310" label="浏览、加购与购买行为量对比" /><p class="chart-footnote">行为量对比；严格路径转化见漏斗分析。</p></article></section>
          <section class="quick-links panel"><span class="muted small">快捷入口</span><button @click="showImport"><AppIcon name="Upload" :size="16" />导入行为数据<AppIcon name="ArrowRight" :size="14" /></button><button @click="navigate('rfm')"><AppIcon name="Users" :size="16" />查看用户画像<AppIcon name="ArrowRight" :size="14" /></button><button @click="navigate('recommend')"><AppIcon name="Sparkles" :size="16" />生成智能推荐<AppIcon name="ArrowRight" :size="14" /></button></section>
        </template>

        <!-- 数据大屏 -->
        <template v-else-if="current === 'screen'">
          <div class="screen-header"><div><span class="eyebrow">COMMERCE BEHAVIOR ANALYTICS</span><h1>电商用户行为数据大屏</h1><p>{{ dateRange }} <span>· {{ source }}</span></p></div><span class="screen-live"><span class="live-dot"></span>本次样本快照</span></div>
          <section class="stats-grid screen-stats"><article v-for="kpi in kpis" :key="kpi.label" class="stat-card"><div class="stat-label">{{ kpi.label }}<AppIcon :name="kpi.icon" /></div><strong>{{ number(kpi.value) }}</strong><span class="small muted">{{ kpi.note }}</span></article></section>
          <div class="two-column"><section class="screen-panel"><h2>用户活跃趋势</h2><AppChart :option="trendOption(stats.days, { dark: true })" :height="330" label="数据大屏浏览和购买趋势" /></section><section class="screen-panel"><h2>热门购买类目</h2><AppChart :option="rankingOption(categoryRank, 'category', true)" :height="330" label="热门购买类目排行" /></section></div>
          <div class="screen-bottom"><span>样本行为 {{ number(stats.total) }} 条 · 商品 {{ number(stats.items) }} 件 · 类目 {{ new Set(rows.map(r => r.category)).size }} 个</span><button class="screen-link" @click="navigate('home')">返回工作台<AppIcon name="ArrowUpRight" :size="14" /></button></div>
        </template>

        <!-- 通用页面标题 -->
        <template v-else>
          <section class="page-heading panel"><div><div class="page-kicker">{{ page.parent || '工作空间' }}<span>/</span>{{ page.title }}</div><h1>{{ page.title }}</h1><p>{{ page.description }}</p></div><div class="page-heading-actions"><span class="source-badge"><span class="live-dot"></span>{{ source }}</span><button v-if="['daily', 'hourly'].includes(current)" class="button" @click="showData = !showData"><AppIcon :name="showData ? 'ChartColumn' : 'Layers'" :size="15" />{{ showData ? '收起数据' : '展开数据' }}</button><button v-if="current === 'recommend'" class="button primary" :disabled="training" @click="openDialog({ kind: 'train', title: '推荐模型训练配置' })"><AppIcon name="RefreshCw" :size="15" :class="{ spinning: training }" />{{ training ? '演示任务进行中' : '重新训练模型' }}</button><button v-if="current === 'data'" class="button primary" @click="showImport"><AppIcon name="Upload" :size="15" />导入数据</button><button v-if="current === 'accounts'" class="button primary" @click="editAccount()"><AppIcon name="Plus" :size="15" />添加账号</button></div></section>

          <template v-if="current === 'daily' || current === 'hourly'">
            <section class="panel chart-panel full-chart"><div class="panel-heading"><h2>{{ current === 'daily' ? '每日趋势分析' : '每时趋势分析' }}</h2><div class="inline-actions"><span class="muted small">{{ dateRange }}</span><button v-if="current === 'daily'" class="icon-button" aria-label="导出每日统计数据" @click="exportDaily"><AppIcon name="Download" :size="16" /></button></div></div><AppChart :option="current === 'daily' ? trendOption(stats.days, { detailed: true }) : hourlyOption(hours)" :height="440" :label="current === 'daily' ? '每日浏览、用户、收藏、加购与购买趋势' : '按北京时间统计的每小时用户行为分布'" /><div class="chart-footnote">{{ current === 'daily' ? '浏览数使用左轴，用户数及其余行为使用右轴；点击图例可显隐系列。' : '按北京时间统计，包含当前数据日期范围内的所有事件。' }}</div></section>
            <section v-if="showData" class="panel table-panel"><div class="panel-heading"><h2>趋势数据明细</h2><span class="muted small">{{ current === 'daily' ? stats.days.length : 24 }} 个时间分组</span></div><div class="table-scroll"><table><thead><tr><th>{{ current === 'daily' ? '日期' : '小时' }}</th><th>浏览数</th><th v-if="current === 'daily'">用户数</th><th>收藏数</th><th>加购数</th><th>购买数</th></tr></thead><tbody><tr v-for="item in current === 'daily' ? stats.days : hours" :key="item.day || item.hour"><td>{{ item.day || `${String(item.hour).padStart(2, '0')}:00` }}</td><td>{{ number(item.pv) }}</td><td v-if="current === 'daily'">{{ number(item.users) }}</td><td>{{ number(item.fav) }}</td><td>{{ number(item.cart) }}</td><td>{{ number(item.buy) }}</td></tr></tbody></table></div></section>
          </template>

          <template v-else-if="current === 'funnel'">
            <div class="info-strip"><AppIcon name="Info" :size="16" /><span>以「同一用户 + 同一商品」为单位，按时间顺序识别浏览 → 加购 → 购买。事件可以跳过其他行为，但不能颠倒顺序。</span></div>
            <section class="mini-stats"><article class="panel"><span>进入浏览阶段</span><strong>{{ number(path[0].value) }}<small>组用户商品对</small></strong></article><article class="panel"><span>浏览 → 加购</span><strong>{{ percentage(path[1].value, path[0].value) }}</strong></article><article class="panel"><span>浏览 → 购买</span><strong>{{ percentage(path[2].value, path[0].value) }}</strong></article></section>
            <div class="two-column"><section class="panel chart-panel"><div class="panel-heading"><h2>用户行为路径漏斗</h2><span class="muted small">全样本观察窗口</span></div><AppChart :option="funnelOption(path)" :height="355" label="同用户同商品的顺序行为转化漏斗" /></section><section class="panel table-panel"><div class="panel-heading"><h2>转化明细</h2></div><table><thead><tr><th>阶段</th><th>数量</th><th>较上一阶段</th></tr></thead><tbody><tr v-for="(item, index) in path" :key="item.name"><td>{{ item.name }}</td><td>{{ number(item.value) }}</td><td>{{ index ? percentage(item.value, path[index - 1].value) : '100%' }}</td></tr></tbody></table><p class="section-note">当前为样本范围内的顺序转化统计，不代表全平台长期转化率。首页漏斗展示的是事件总量，两者口径不同。</p></section></div>
          </template>

          <template v-else-if="current === 'rfm'">
            <div class="info-strip amber"><AppIcon name="Info" :size="16" /><span>当前行为数据没有订单金额，暂使用 R（最近购买间隔）和 F（购买频次）分群，M 显示为缺失。接入订单金额后补齐完整 RFM。</span></div>
            <div class="two-column"><section class="panel chart-panel"><div class="panel-heading"><h2>用户价值分群</h2><span class="badge teal">RF 分群</span></div><AppChart :option="segmentOption(profiles)" :height="250" label="按最近购买时间和购买频次划分的用户分群" /></section><section class="panel rule-panel"><div class="panel-heading"><h2>分群口径</h2><AppIcon name="SlidersHorizontal" :size="17" /></div><div class="rule-row"><span class="rule-letter">R</span><div><strong>最近购买间隔</strong><p>距样本最后时刻的天数，≤ 2 天记为近期购买。</p></div></div><div class="rule-row"><span class="rule-letter teal">F</span><div><strong>购买频次</strong><p>样本内购买事件数量，≥ 12 次记为高频。</p></div></div><div class="rule-row"><span class="rule-letter gold">M</span><div><strong>消费金额 <span class="muted small">待接入</span></strong><p>不使用商品价格或行为次数替代真实金额。</p></div></div></section></div>
            <section class="panel table-panel"><div class="panel-heading"><h2>用户分群与画像</h2><label class="compact-select">分群<select v-model="segmentFilter"><option>全部分群</option><option v-for="segment in [...new Set(profiles.map(p => p.segment))]" :key="segment">{{ segment }}</option></select></label></div><div class="table-scroll"><table><thead><tr><th>用户 ID</th><th>主要兴趣</th><th>R / 天</th><th>F / 次</th><th>M / 元</th><th>用户分群</th><th>操作</th></tr></thead><tbody><tr v-for="user in rfmUsers" :key="user.user"><td class="mono">{{ user.user }}</td><td>{{ labelCategory(user.cats[0]?.[0] || '未分类') }}</td><td>{{ user.recency === null ? '无购买' : user.recency }}</td><td>{{ user.frequency }}</td><td class="muted">—</td><td><span class="badge" :class="user.segment.includes('活跃') ? 'teal' : 'blue'">{{ user.segment }}</span></td><td><button class="text-button" @click="inspectUser(user.user)">查看画像<AppIcon name="ArrowUpRight" :size="13" /></button></td></tr><tr v-if="!rfmUsers.length"><td colspan="7" class="empty-table">没有符合分群条件的用户</td></tr></tbody></table></div></section>
          </template>

          <template v-else-if="current === 'retention'">
            <section class="mini-stats"><article class="panel"><span>首次出现用户</span><strong>{{ number(stats.users) }}</strong></article><article class="panel"><span>已观察到的次日留存</span><strong>{{ retentionAverage === null ? '—' : `${(retentionAverage * 100).toFixed(1)}%` }}</strong></article><article class="panel"><span>用户首现日期分组</span><strong>{{ cohorts.length }}<small>组</small></strong></article></section>
            <section class="panel table-panel"><div class="panel-heading"><h2>用户留存矩阵</h2><span class="muted small">颜色越深，留存越高</span></div><div class="table-scroll"><table class="retention-table"><thead><tr><th>首次出现日期</th><th>用户数</th><th v-for="d in 8" :key="d">{{ d === 1 ? '当日' : `第 ${d - 1} 天` }}</th></tr></thead><tbody><tr v-for="cohort in cohorts" :key="cohort.day"><td>{{ cohort.day }}</td><td>{{ cohort.users }}</td><td v-for="(value, index) in cohort.retention" :key="index"><span class="retention-cell" :style="retentionStyle(value)">{{ value === null ? '—' : `${(value * 100).toFixed(0)}%` }}</span></td></tr></tbody></table></div><p class="section-note">“首次出现”指在当前样本中第一次被观察到，并非注册日期。— 表示观察窗口尚未覆盖该天，不按 0% 计算。</p></section>
          </template>

          <template v-else-if="current === 'categories' || current === 'products'">
            <section class="panel chart-panel"><div class="panel-heading"><h2>{{ current === 'categories' ? '热门购买类目 Top 8' : '热门购买商品 Top 8' }}</h2><span class="muted small">按购买事件数降序</span></div><AppChart :option="rankingOption(current === 'categories' ? categoryRank : productRank, current === 'categories' ? 'category' : 'item')" :height="340" label="按购买事件数量统计的排行" /></section>
            <section class="panel table-panel"><div class="panel-heading"><h2>购买排名明细</h2><span class="muted small">展示前 20 名</span></div><div class="table-scroll"><table><thead><tr><th>排名</th><th>{{ current === 'categories' ? '类目' : '商品 ID' }}</th><th>类目 ID</th><th>购买事件</th><th>购买用户数</th><th>事件占比</th></tr></thead><tbody><tr v-for="(item, index) in (current === 'categories' ? categoryRank : productRank).slice(0, 20)" :key="item.id"><td><span class="rank-number" :class="{ 'rank-top': index < 3 }">{{ String(index + 1).padStart(2, '0') }}</span></td><td>{{ current === 'categories' ? labelCategory(item.id) : item.id }}</td><td class="mono muted">{{ item.category }}</td><td>{{ item.count }}</td><td>{{ item.users }}</td><td>{{ percentage(item.count, stats.counts.buy) }}</td></tr><tr v-if="!(current === 'categories' ? categoryRank : productRank).length"><td colspan="6" class="empty-table">暂无购买记录，无法生成购买排行</td></tr></tbody></table></div></section>
          </template>

          <!-- 智能推荐 -->
          <template v-else-if="current === 'recommend'">
            <section class="recommend-controls panel"><div><AppIcon name="UserRound" :size="17" /><label for="recommend-user">选择用户</label><select id="recommend-user" v-model="recUser"><option v-for="user in profiles" :key="user.user" :value="user.user">{{ user.user }} · {{ user.rows.length }} 条行为</option><option value="new">新用户 · 无历史记录</option></select></div><div><label for="recommend-strategy">推荐方案</label><select id="recommend-strategy" v-model="strategy"><option value="bpr">BPR · 模型待接入</option><option value="popular">热门推荐 · 规则预览</option></select><button class="button primary" @click="generateRecommendation"><AppIcon name="Sparkles" :size="15" />生成推荐</button><button class="button" @click="inspectUser(recUser)">用户画像</button></div></section>
            <section class="stats-grid recommend-stats"><article class="stat-card blue"><span>当前用户 ID</span><strong class="user-id">{{ recUser === 'new' ? '新用户' : recUser }}</strong></article><article class="stat-card teal"><span>用户交互商品</span><strong>{{ number(selectedProfile?.itemCount || 0) }}</strong></article><article class="stat-card gold"><span>样本用户</span><strong>{{ number(stats.users) }}</strong></article><article class="stat-card purple"><span>样本商品</span><strong>{{ number(stats.items) }}</strong></article></section>
            <section class="panel recommendation-panel"><div class="panel-heading"><div><h2><span class="heading-mark"></span>商品推荐结果</h2><p class="muted small">{{ strategy === 'bpr' ? 'BPR 隐式反馈矩阵分解 · 服务待接入' : '全体用户行为热度 · 前端规则' }}<span class="model-separator">·</span>{{ trainingStatus }}<span class="model-separator">·</span>预览 v0.{{ recVersion }}</p></div><span class="badge blue">{{ recReady ? `推荐 ${recItems.length} 件商品` : '等待生成' }}</span></div>
              <div v-if="recReady && recItems.length" class="recommendation-grid"><button v-for="item in recItems" :key="item.id" class="recommendation-card" :class="`top-${item.rank}`" @click="openDialog({ kind: 'recommendation', title: '推荐商品明细', item })"><div class="recommendation-card-top"><span class="rank-badge">TOP {{ String(item.rank).padStart(2, '0') }}</span><span class="category-id">类目 {{ item.category }}</span></div><h3>商品 {{ item.id }}</h3><p>{{ item.reason }}</p><div class="score-label"><span>推荐指数</span><strong>{{ item.score }}</strong></div><div class="score-track"><span :style="{ width: `${item.score}%` }"></span></div></button></div>
              <div v-else class="empty-state"><span class="empty-icon"><AppIcon name="Sparkles" :size="30" /></span><h3>{{ recReady ? '暂无可推荐商品' : '准备生成新的推荐' }}</h3><p>{{ recReady ? '当前商品均已购买或样本不足，可尝试其他用户。' : '选择用户或切换方案后，点击“生成推荐”。' }}</p><button v-if="!recReady" class="button primary" @click="generateRecommendation">生成推荐</button></div>
              <div class="recommendation-disclaimer"><AppIcon name="Info" :size="15" /><p>当前使用类目兴趣与行为热度生成前端演示结果，未运行 BPR。推荐指数按本次最高分归一到 100，仅用于比较商品顺序，不表示购买概率。商品只展示匿名 ID 与类目 ID。</p></div>
            </section>
          </template>

          <!-- 数据管理 -->
          <template v-else-if="current === 'data'">
            <section class="panel data-panel"><div class="local-tabs"><button v-for="tab in [{ id: 'records', name: '行为明细' }, { id: 'datasets', name: '当前数据集' }, { id: 'schema', name: '字段说明' }]" :key="tab.id" :class="{ active: dataTab === tab.id }" @click="dataTab = tab.id">{{ tab.name }}</button></div>
              <template v-if="dataTab === 'records'"><form class="filter-bar" @submit.prevent="applyQuery"><label class="search-field"><AppIcon name="Search" :size="16" /><input v-model="query.keyword" aria-label="搜索用户或商品ID" placeholder="搜索用户 / 商品 ID" /></label><select v-model="query.type" aria-label="行为类型"><option value="">全部行为类型</option><option v-for="(label, type) in behaviorLabels" :key="type" :value="type">{{ label }}</option></select><label class="date-field"><span>起</span><input v-model="query.from" type="date" aria-label="开始日期" /></label><label class="date-field"><span>止</span><input v-model="query.to" type="date" aria-label="结束日期" /></label><button class="button primary" type="submit">查询</button><button class="button" type="button" @click="resetQuery">重置</button></form><div class="table-toolbar"><span>行为明细 <span class="muted small">共 {{ number(filteredRows.length) }} 条</span></span><button class="button small-button" @click="exportRows"><AppIcon name="Download" :size="14" />导出结果</button></div><div class="table-scroll"><table><thead><tr><th>用户 ID</th><th>商品 ID</th><th>类目 ID</th><th>行为类型</th><th>行为时间</th><th>操作</th></tr></thead><tbody><tr v-for="record in visibleRows" :key="record.id"><td class="mono">{{ record.user }}</td><td class="mono">{{ record.item }}</td><td class="mono muted">{{ record.category }}</td><td><span class="badge" :class="record.type">{{ behaviorText(record.type) }}</span></td><td class="muted">{{ dateTime(record.t) }}</td><td><button class="text-button" @click="inspectUser(record.user)">查看用户</button></td></tr><tr v-if="!visibleRows.length"><td colspan="6" class="empty-table">没有符合条件的行为记录，请调整筛选条件。</td></tr></tbody></table></div><div class="pagination"><span>每页 10 条，共 {{ pageCount }} 页</span><div><button class="icon-button" aria-label="上一页" :disabled="dataPage <= 1" @click="dataPage--"><AppIcon name="ChevronLeft" :size="16" /></button><span class="page-number">{{ dataPage }}</span><button class="icon-button" aria-label="下一页" :disabled="dataPage >= pageCount" @click="dataPage++"><AppIcon name="ChevronRight" :size="16" /></button></div></div></template>
              <div v-else-if="dataTab === 'datasets'" class="dataset-content"><div class="dataset-file"><span class="dataset-icon"><AppIcon name="FileSpreadsheet" :size="30" /></span><div><h2>{{ sourceFile }}</h2><p>{{ source }} · {{ number(stats.total) }} 条行为 · {{ stats.users }} 位用户 · {{ stats.items }} 件商品</p></div><span class="badge teal">当前使用</span></div><div class="description-grid"><span>日期范围</span><strong>{{ dateRange }}</strong><span>数据存储</span><strong>当前浏览器会话；刷新后恢复演示样本</strong><span>金额字段</span><strong>未提供，不计算销售额与消费金额</strong><span>时间口径</span><strong>北京时间 UTC+8</strong></div><div class="inline-actions"><button class="button" @click="downloadTemplate"><AppIcon name="Download" :size="15" />下载导入模板</button><button class="button" @click="restoreDemo"><AppIcon name="RefreshCw" :size="15" />恢复演示数据</button></div></div>
              <div v-else class="schema-content"><table><thead><tr><th>字段</th><th>含义</th><th>示例 / 说明</th><th>要求</th></tr></thead><tbody><tr><td class="mono">user_id</td><td>用户标识</td><td>1003707</td><td>必填</td></tr><tr><td class="mono">item_id</td><td>商品标识</td><td>1325619，兼容 product_id</td><td>必填</td></tr><tr><td class="mono">category_id</td><td>商品类目</td><td>982926</td><td>可选</td></tr><tr><td class="mono">behavior_type</td><td>行为类型</td><td>pv / fav / cart / buy</td><td>必填</td></tr><tr><td class="mono">timestamp</td><td>发生时间</td><td>10 位秒时间戳、13 位毫秒或日期时间</td><td>必填</td></tr></tbody></table><p class="section-note">支持有表头 CSV，或按 user_id、item_id、category_id、behavior_type、timestamp 排列的无表头五列文件。</p></div>
            </section>
          </template>

          <template v-else-if="current === 'logs'">
            <section class="panel table-panel"><div class="panel-heading"><h2>操作日志</h2><label class="compact-select">模块<select v-model="logModule"><option>全部模块</option><option>系统</option><option>数据管理</option><option>智能推荐</option><option>系统管理</option></select></label></div><div class="table-scroll"><table><thead><tr><th>操作时间</th><th>模块</th><th>操作</th><th>状态</th><th>详情</th></tr></thead><tbody><tr v-for="log in filteredLogs" :key="log.id"><td class="muted nowrap">{{ log.time }}</td><td>{{ log.module }}</td><td>{{ log.action }}</td><td><span class="badge" :class="log.status === '演示' ? 'gold' : 'teal'">{{ log.status }}</span></td><td class="muted">{{ log.detail }}</td></tr><tr v-if="!filteredLogs.length"><td colspan="5" class="empty-table">该模块暂无操作记录</td></tr></tbody></table></div><p class="section-note">日志记录本次前端会话中的交互；真实审计与训练任务日志待接入后台。</p></section>
          </template>

          <template v-else-if="current === 'messages'">
            <div class="message-layout"><section class="panel message-editor"><div class="panel-heading"><h2>发布留言</h2><AppIcon name="MessageSquare" :size="18" /></div><label class="form-field">留言类型<select v-model="messageForm.type"><option>功能建议</option><option>问题反馈</option><option>项目待办</option><option>数据说明</option></select></label><label class="form-field">留言内容<textarea v-model="messageForm.content" maxlength="500" rows="7" placeholder="写下对数据分析、推荐功能或页面交互的建议…"></textarea><span class="input-count">{{ messageForm.content.length }} / 500</span></label><button class="button primary" @click="postMessage"><AppIcon name="Plus" :size="15" />发布留言</button><p class="section-note">仅保留在本次会话中。</p></section><section class="message-list"><article v-for="message in messages" :key="message.id" class="panel message-card"><div class="message-head"><span class="avatar avatar-small">{{ message.name.slice(0, 1) }}</span><div><strong>{{ message.name }}</strong><small>{{ message.time }}</small></div><span class="badge blue">{{ message.type }}</span></div><p>{{ message.content }}</p><button class="text-button" :class="{ 'is-resolved': message.done }" @click="message.done = !message.done"><AppIcon :name="message.done ? 'CircleCheck' : 'Check'" :size="14" />{{ message.done ? '已处理 · 撤回标记' : '标记为已处理' }}</button></article></section></div>
          </template>

          <template v-else-if="current === 'accounts'">
            <div class="info-strip"><AppIcon name="Info" :size="16" /><span>这里演示系统操作账号的管理，与行为数据中的匿名用户 ID 分开。真实登录和权限校验将在后端接入。</span></div><section class="panel table-panel"><div class="panel-heading"><h2>系统账号</h2><span class="muted small">{{ accounts.length }} 个展示账号</span></div><div class="table-scroll"><table><thead><tr><th>账号</th><th>显示名称</th><th>角色</th><th>状态</th><th>创建日期</th><th>操作</th></tr></thead><tbody><tr v-for="account in accounts" :key="account.id"><td class="mono">{{ account.id }}</td><td>{{ account.name }}</td><td><span class="badge blue">{{ account.role }}</span></td><td><span class="badge" :class="account.enabled ? 'teal' : 'gray'">{{ account.enabled ? '启用' : '停用' }}</span></td><td class="muted">{{ account.created }}</td><td><div class="inline-actions"><button class="text-button" @click="editAccount(account)">编辑</button><button class="text-button muted" :disabled="account.id === 'admin'" @click="account.enabled = !account.enabled; notify('展示状态已更新，尚未接入后端鉴权。')">{{ account.enabled ? '停用' : '启用' }}</button></div></td></tr></tbody></table></div></section>
          </template>

          <template v-else-if="current === 'roles'">
            <section class="panel table-panel"><div class="panel-heading"><h2>角色权限矩阵</h2><button class="button" @click="roleEditing = !roleEditing; if (!roleEditing) notify('权限配置已保存至本次演示，未改变真实访问权限。')"><AppIcon :name="roleEditing ? 'Check' : 'Pencil'" :size="14" />{{ roleEditing ? '保存演示配置' : '编辑配置' }}</button></div><div class="table-scroll"><table><thead><tr><th>功能权限</th><th v-for="role in Object.keys(rolePermissions)" :key="role">{{ role }}</th></tr></thead><tbody><tr v-for="permission in permissions" :key="permission"><td>{{ permission }}</td><td v-for="(allowed, role) in rolePermissions" :key="role"><input v-if="roleEditing" v-model="rolePermissions[role]" type="checkbox" :value="permission" :aria-label="`${role}的${permission}权限`" /><AppIcon v-else-if="allowed.includes(permission)" name="CircleCheck" class="permission-check" :size="17" /><span v-else class="muted">—</span></td></tr></tbody></table></div><p class="section-note">权限矩阵为前端设计展示，后续由后端接口实施访问控制。</p></section>
          </template>

          <template v-else-if="current === 'personal'">
            <div class="personal-layout"><section class="panel personal-summary"><span class="avatar avatar-large">{{ personal.name.slice(0, 1) }}</span><h2>{{ personal.name }}</h2><span class="badge blue">管理员 · 展示账号</span><div class="personal-meta"><p><AppIcon name="ShieldCheck" :size="15" />系统管理与数据分析</p><p><AppIcon name="Users" :size="15" />{{ personal.team }}</p></div></section><section class="panel personal-form"><div class="panel-heading"><h2>基本资料</h2><span class="muted small">仅更新本次前端会话</span></div><form @submit.prevent="savePersonal"><label class="form-field">显示名称<input v-model="personal.name" maxlength="20" required /></label><label class="form-field">邮箱<input v-model="personal.email" type="email" /></label><label class="form-field">所属团队<input v-model="personal.team" maxlength="40" /></label><label class="form-field">备注<textarea v-model="personal.note" rows="3" maxlength="200"></textarea></label><button type="submit" class="button primary">保存资料</button></form></section></div>
          </template>
        </template>
        <footer class="page-footer"><span>电商平台用户行为分析与精准推荐系统</span><span>前端展示 v0.1 <span class="footer-dot">·</span> {{ source }}</span></footer>
      </main>
    </div>

    <!-- 详情与操作弹窗 -->
    <Teleport to="body"><div v-if="dialog" class="modal-backdrop" @click.self="closeDialog"><section ref="modalElement" class="modal" :class="{ 'wide-modal': dialog.kind === 'user' || dialog.kind === 'import' }" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-heading"><div><span class="eyebrow">{{ dialog.kind === 'user' ? 'USER PORTRAIT' : '工作台' }}</span><h2 id="modal-title">{{ dialog.title }}</h2></div><button class="icon-button" aria-label="关闭弹窗" @click="closeDialog"><AppIcon name="X" :size="19" /></button></header>
      <div v-if="dialog.kind === 'user'" class="modal-body"><template v-if="dialogUser"><div class="user-profile-header"><span class="avatar"><AppIcon name="UserRound" :size="25" /></span><div><h2>用户 {{ dialogUser.user }}</h2><span class="muted small">{{ dialogUser.rows.length }} 条行为 · {{ dialogUser.itemCount }} 件商品</span></div><span class="badge teal">{{ dialogUser.segment }}</span></div><div class="profile-behaviors"><div v-for="(label, type) in behaviorLabels" :key="type"><span>{{ label }}</span><strong>{{ dialogUser.counts[type] }}</strong></div></div><div class="profile-columns"><section><h3>兴趣类目分布</h3><AppChart :option="interestOption(dialogUser)" :height="245" label="当前用户的类目兴趣分布" /></section><section><h3>最近行为</h3><div class="timeline"><div v-for="event in dialogUser.rows.slice().sort((a, b) => b.t - a.t).slice(0, 5)" :key="event.id" class="timeline-row"><span class="timeline-dot"></span><div><strong>商品 {{ event.item }}<span class="badge" :class="event.type">{{ behaviorText(event.type) }}</span></strong><small>{{ dateTime(event.t) }}</small></div></div></div></section></div><p class="section-note">兴趣分布按行为权重和 3 天半衰期计算，仅反映样本中的相对兴趣。</p></template><div v-else class="empty-state"><AppIcon name="UserRound" :size="32" /><h3>该用户暂无历史行为</h3><p>新用户将使用热门商品兜底。</p></div></div>
      <div v-else-if="dialog.kind === 'recommendation'" class="modal-body"><div class="item-detail-title"><span class="rank-badge">TOP {{ dialog.item.rank }}</span><h2>商品 {{ dialog.item.id }}</h2><p>{{ labelCategory(dialog.item.category) }} · 类目 ID {{ dialog.item.category }}</p></div><div class="description-grid"><span>目标用户</span><strong>{{ recUser }}</strong><span>推荐指数</span><strong>{{ dialog.item.score }} / 100</strong><span>前端规则依据</span><strong>{{ dialog.item.reason }}</strong><span>候选过滤</span><strong>排除该用户样本内已购买商品</strong></div><div class="info-strip"><AppIcon name="Info" :size="16" /><span>这是前端演示规则生成的相对排序结果，BPR 模型与真实推荐解释服务待接入。</span></div></div>
      <div v-else-if="dialog.kind === 'train'" class="modal-body"><div class="info-strip amber"><AppIcon name="Info" :size="16" /><span>本次仅演示任务创建、等待和完成状态，不执行真实模型训练。</span></div><label class="form-field">模型方案<select v-model="trainingForm.method"><option value="bpr">BPR 隐式反馈矩阵分解</option><option value="als">ALS 隐式反馈矩阵分解</option></select></label><div class="form-grid"><label class="form-field">隐向量维度<select v-model="trainingForm.rank"><option>16</option><option>32</option><option>64</option></select></label><label class="form-field">训练迭代次数<select v-model="trainingForm.iterations"><option>10</option><option>20</option><option>50</option></select></label></div><div class="description-grid"><span>样本用户</span><strong>{{ stats.users }}</strong><span>样本商品</span><strong>{{ stats.items }}</strong><span>输入行为</span><strong>浏览、收藏、加购、购买</strong></div></div>
      <div v-else-if="dialog.kind === 'import'" class="modal-body"><div class="upload-zone"><span class="dataset-icon"><AppIcon name="Upload" :size="27" /></span><h3>导入 CSV 行为数据</h3><p>最大 10 MB / 25,000 条。文件在当前浏览器会话中处理。</p><button class="button primary" @click="importInput?.click()">选择文件</button><button class="text-button" @click="downloadTemplate">下载模板<AppIcon name="Download" :size="13" /></button><input ref="importInput" type="file" accept=".csv,text/csv" hidden @change="readFile" /></div><details class="paste-details"><summary>或粘贴 CSV 内容</summary><label class="form-field">行为数据<textarea v-model="pasteCSV" rows="4" placeholder="user_id,item_id,category_id,behavior_type,timestamp"></textarea></label><button class="button" @click="checkText(pasteCSV, '粘贴的行为数据.csv')">检查内容</button></details><p v-if="importError" class="error-message" role="alert">{{ importError }}</p><div v-if="importPreview" class="import-success"><AppIcon name="CircleCheck" :size="18" /><div><strong>{{ importName }}</strong><p>{{ number(importPreview.rows.length) }} 条有效记录；跳过 {{ importPreview.invalid }} 条无效记录，合并 {{ importPreview.duplicate }} 条完全重复记录。</p></div></div><div v-if="importPreview" class="table-scroll"><table><thead><tr><th>用户</th><th>商品</th><th>类目</th><th>行为</th></tr></thead><tbody><tr v-for="record in importPreview.rows.slice(0, 4)" :key="record.id"><td>{{ record.user }}</td><td>{{ record.item }}</td><td>{{ record.category }}</td><td>{{ behaviorText(record.type) }}</td></tr></tbody></table></div><p class="section-note">确认后替换当前样本，相关统计与画像同步更新。刷新页面会恢复演示数据。</p></div>
      <div v-else-if="dialog.kind === 'account'" class="modal-body"><label class="form-field">账号<input v-model="accountForm.id" :disabled="dialog.editing" placeholder="例如 analyst02" maxlength="30" /></label><label class="form-field">显示名称<input v-model="accountForm.name" maxlength="30" /></label><label class="form-field">角色<select v-model="accountForm.role"><option>管理员</option><option>分析员</option><option>访客</option></select></label><label class="checkbox-field"><input v-model="accountForm.enabled" type="checkbox" />启用账号</label><p v-if="accountError" class="error-message" role="alert">{{ accountError }}</p><p class="section-note">此操作仅调整展示数据，不创建真实登录凭据。</p></div>
      <footer class="modal-footer"><button class="button" @click="closeDialog">{{ ['user', 'recommendation'].includes(dialog.kind) ? '关闭' : '取消' }}</button><button v-if="dialog.kind === 'user'" class="button primary" @click="recommendForUser(dialog.user)"><AppIcon name="Sparkles" :size="15" />为该用户生成推荐</button><button v-if="dialog.kind === 'train'" class="button primary" @click="startTraining">创建演示任务</button><button v-if="dialog.kind === 'import'" class="button primary" :disabled="!importPreview" @click="applyImport">确认导入</button><button v-if="dialog.kind === 'account'" class="button primary" @click="saveAccount">保存</button></footer>
    </section></div><Transition name="toast"><div v-if="toast" class="toast-message" role="status" aria-live="polite"><AppIcon name="CircleCheck" :size="18" /><span>{{ toast }}</span><button aria-label="关闭提示" @click="toast = ''"><AppIcon name="X" :size="14" /></button></div></Transition></Teleport>
  </div>
</template>
