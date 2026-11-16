<script setup>
import AppIcon from './components/AppIcon.vue';
import AppChart from './components/AppChart.vue';
import LoginPanel from './components/LoginPanel.vue';
import ListPager from './components/ListPager.vue';
import { pageMap } from './navigation.js';
import {
  behaviorLabels,
  number,
  dateTime,
  labelCategory,
} from './data/behavior.js';
import {
  trendOption,
  funnelOption,
  hourlyOption,
  rankingOption,
  segmentOption,
  interestOption,
} from './data/charts.js';
import { useConsole } from './useConsole.js';
const {
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
} = useConsole();
</script>

<template>
  <LoginPanel
    v-if="!authenticated"
    :ready="authReady"
    :busy="authBusy"
    :error="authError"
    @login="login"
  />
  <div v-else class="application" :class="{ 'sidebar-collapsed': collapsed }">
    <aside class="sidebar" aria-label="主导航">
      <a class="brand" href="#home" @click.prevent="navigate('home')"
        ><span class="brand-logo"
          ><AppIcon name="ChartNoAxesCombined" :size="26" /></span
        ><span class="brand-name"
          >用户行为分析<span>COMMERCE INSIGHTS</span></span
        ></a
      >
      <div class="sidebar-section-label">工作空间</div>
      <nav class="side-nav">
        <template v-for="item in visibleNavigation" :key="item.id">
          <button
            v-if="!item.children"
            class="nav-item"
            :class="{ active: current === item.id }"
            :aria-current="current === item.id ? 'page' : undefined"
            :aria-label="item.title"
            @click="navigate(item.id)"
          >
            <AppIcon :name="item.icon" /><span>{{ item.title }}</span
            ><span v-if="item.id === 'recommend'" class="nav-new">AI</span>
          </button>
          <template v-else>
            <button
              class="nav-item nav-parent"
              :class="{
                'parent-active': item.children.some((c) => c.id === current),
              }"
              :aria-expanded="expanded[item.id]"
              :aria-label="item.title"
              @click="
                expanded[item.id] = !expanded[item.id];
                if (collapsed) collapsed = false;
              "
            >
              <AppIcon :name="item.icon" /><span>{{ item.title }}</span
              ><AppIcon
                name="ChevronDown"
                class="nav-chevron"
                :class="{ rotated: expanded[item.id] }"
                :size="14"
              />
            </button>
            <div v-if="expanded[item.id] && !collapsed" class="nav-children">
              <button
                v-for="child in item.children"
                :key="child.id"
                class="nav-item nav-child"
                :class="{ active: current === child.id }"
                :aria-current="current === child.id ? 'page' : undefined"
                @click="navigate(child.id)"
              >
                <span class="nav-child-dot"></span>{{ child.title }}
              </button>
            </div>
          </template>
        </template>
      </nav>
      <div class="sidebar-foot">
        <span class="live-dot"></span
        ><span>数据库已连接 <small>v0.2.0</small></span>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button"
            aria-label="折叠或展开侧栏"
            @click="collapsed = !collapsed"
          >
            <AppIcon name="PanelLeft" /></button
          ><button @click="navigate('home')">首页</button
          ><template v-if="current !== 'home'"
            ><span>/</span><span v-if="page.parent">{{ page.parent }}</span
            ><span v-if="page.parent">/</span
            ><span class="breadcrumb-current">{{ page.title }}</span></template
          >
        </div>
        <div class="topbar-actions">
          <span class="environment-badge"><span></span>本地开发</span
          ><button
            class="icon-button"
            aria-label="刷新当前视图"
            @click="refreshPage"
          >
            <AppIcon name="RefreshCw" :size="17" /></button
          ><span class="topbar-divider"></span
          ><button class="account-button" @click="navigate('personal')">
            <span class="avatar avatar-small">{{
              personal.name.slice(0, 1)
            }}</span
            ><span>{{ personal.name }}</span
            ><AppIcon name="ChevronDown" :size="14" /></button
          ><button class="text-button" @click="run(logout)">退出登录</button>
        </div>
      </header>
      <div class="tabs-bar" aria-label="已打开页面">
        <div
          v-for="id in tabs"
          :key="id"
          class="workspace-tab"
          :class="{ selected: current === id }"
        >
          <button
            :aria-current="current === id ? 'page' : undefined"
            @click="navigate(id)"
          >
            {{ pageMap[id].title }}</button
          ><button
            v-if="id !== 'home'"
            class="tab-close"
            :aria-label="`关闭${pageMap[id].title}页签`"
            @click="closeTab(id)"
          >
            <AppIcon name="X" :size="12" />
          </button>
        </div>
      </div>

      <main
        class="main-content"
        :class="{ 'screen-content': current === 'screen' }"
      >
        <div v-if="loadError" class="info-strip amber" role="alert">
          <AppIcon name="Info" :size="16" /><span>{{ loadError }}</span
          ><button class="text-button" @click="refreshPage">重试</button>
        </div>
        <div v-if="loading" class="loading-notice" role="status">
          正在读取数据库…
        </div>
        <div v-if="!activeDataset && !loading && !loadError" class="info-strip">
          <AppIcon name="Database" :size="16" /><span
            >还没有可用数据集。导入数据完成后，这里会展示统计与画像。</span
          ><button
            v-if="can('数据导入')"
            class="text-button"
            @click="showImport"
          >
            导入数据
          </button>
        </div>
        <!-- 首页 -->
        <template v-if="current === 'home'">
          <section class="welcome-card panel">
            <div>
              <span class="eyebrow">用户行为洞察与推荐工作台</span>
              <h1>
                你好，{{ personal.name }}<span class="welcome-period">.</span>
              </h1>
              <p>从用户行为中发现规律，让每一次推荐更有依据。</p>
              <div class="welcome-meta">
                <span><AppIcon name="Database" :size="13" />{{ source }}</span
                ><span class="meta-separator"></span
                ><span
                  ><AppIcon name="CalendarDays" :size="13" />{{
                    dateRange
                  }}</span
                >
              </div>
            </div>
            <div class="welcome-visual" aria-hidden="true">
              <span class="visual-orbit orbit-one"></span
              ><span class="visual-orbit orbit-two"></span
              ><span class="visual-column column-one"></span
              ><span class="visual-column column-two"></span
              ><span class="visual-column column-three"></span
              ><span class="visual-bubble"
                ><AppIcon name="ChartNoAxesCombined" :size="28"
              /></span>
            </div>
          </section>
          <section class="stats-grid">
            <article
              v-for="kpi in kpis"
              :key="kpi.label"
              class="stat-card panel"
              :class="kpi.color"
            >
              <div class="stat-label">
                {{ kpi.label
                }}<span class="stat-icon"
                  ><AppIcon :name="kpi.icon" :size="19"
                /></span>
              </div>
              <strong>{{ number(kpi.value) }}</strong>
              <div class="stat-footer">
                <span>{{ kpi.note }}</span
                ><span class="sample-label">{{ source }}</span>
              </div>
            </article>
          </section>
          <section class="two-column dashboard-charts">
            <article class="panel chart-panel">
              <div class="panel-heading">
                <h2><span class="heading-mark"></span>每日行为趋势</h2>
                <button class="text-button" @click="navigate('daily')">
                  查看分析<AppIcon name="ArrowUpRight" :size="14" />
                </button>
              </div>
              <AppChart
                :option="trendOption(stats.days)"
                :height="310"
                label="每日浏览和购买事件趋势，左右坐标轴分别计量"
              />
            </article>
            <article class="panel chart-panel">
              <div class="panel-heading">
                <h2><span class="heading-mark teal"></span>行为转化漏斗</h2>
                <span class="muted small">各阶段行为量</span>
              </div>
              <AppChart
                :option="
                  funnelOption([
                    { name: '浏览数', value: stats.counts.pv },
                    { name: '加购数', value: stats.counts.cart },
                    { name: '购买数', value: stats.counts.buy },
                  ])
                "
                :height="310"
                label="浏览、加购与购买行为量对比"
              />
              <p class="chart-footnote">行为量对比；严格路径转化见漏斗分析。</p>
            </article>
          </section>
          <section class="quick-links panel">
            <span class="muted small">快捷入口</span
            ><button v-if="can('数据导入')" @click="showImport">
              <AppIcon name="Upload" :size="16" />导入行为数据<AppIcon
                name="ArrowRight"
                :size="14"
              /></button
            ><button @click="navigate('rfm')">
              <AppIcon name="Users" :size="16" />查看用户画像<AppIcon
                name="ArrowRight"
                :size="14"
              /></button
            ><button @click="navigate('recommend')">
              <AppIcon name="Sparkles" :size="16" />生成智能推荐<AppIcon
                name="ArrowRight"
                :size="14"
              />
            </button>
          </section>
        </template>

        <!-- 数据大屏 -->
        <template v-else-if="current === 'screen'">
          <div class="screen-header">
            <div>
              <span class="eyebrow">COMMERCE BEHAVIOR ANALYTICS</span>
              <h1>电商用户行为数据大屏</h1>
              <p>
                {{ dateRange }} <span>· {{ source }}</span>
              </p>
            </div>
            <span class="screen-live"
              ><span class="live-dot"></span>本次样本快照</span
            >
          </div>
          <section class="stats-grid screen-stats">
            <article v-for="kpi in kpis" :key="kpi.label" class="stat-card">
              <div class="stat-label">
                {{ kpi.label }}<AppIcon :name="kpi.icon" />
              </div>
              <strong>{{ number(kpi.value) }}</strong
              ><span class="small muted">{{ kpi.note }}</span>
            </article>
          </section>
          <div class="two-column">
            <section class="screen-panel">
              <h2>用户活跃趋势</h2>
              <AppChart
                :option="trendOption(stats.days, { dark: true })"
                :height="330"
                label="数据大屏浏览和购买趋势"
              />
            </section>
            <section class="screen-panel">
              <h2>热门购买类目</h2>
              <AppChart
                :option="rankingOption(categoryRank, 'category', true)"
                :height="330"
                label="热门购买类目排行"
              />
            </section>
          </div>
          <div class="screen-bottom">
            <span
              >样本行为 {{ number(stats.total) }} 条 · 商品
              {{ number(stats.items) }} 件 · 类目
              {{ stats.categories }} 个</span
            ><button class="screen-link" @click="navigate('home')">
              返回工作台<AppIcon name="ArrowUpRight" :size="14" />
            </button>
          </div>
        </template>

        <!-- 通用页面标题 -->
        <template v-else>
          <section class="page-heading panel">
            <div>
              <div class="page-kicker">
                {{ page.parent || '工作空间' }}<span>/</span>{{ page.title }}
              </div>
              <h1>{{ page.title }}</h1>
              <p>{{ page.description }}</p>
            </div>
            <div class="page-heading-actions">
              <span class="source-badge"
                ><span class="live-dot"></span>{{ source }}</span
              ><button
                v-if="['daily', 'hourly'].includes(current)"
                class="button"
                @click="showData = !showData"
              >
                <AppIcon
                  :name="showData ? 'ChartColumn' : 'Layers'"
                  :size="15"
                />{{ showData ? '收起数据' : '展开数据' }}</button
              ><button
                v-if="current === 'data' && can('数据导入')"
                class="button primary"
                @click="showImport"
              >
                <AppIcon name="Upload" :size="15" />导入数据</button
              ><button
                v-if="current === 'accounts'"
                class="button primary"
                @click="editAccount()"
              >
                <AppIcon name="Plus" :size="15" />添加账号
              </button>
            </div>
          </section>

          <template v-if="current === 'daily' || current === 'hourly'">
            <section class="panel chart-panel full-chart">
              <div class="panel-heading">
                <h2>
                  {{ current === 'daily' ? '每日趋势分析' : '每时趋势分析' }}
                </h2>
                <div class="inline-actions">
                  <span class="muted small">{{ dateRange }}</span
                  ><button
                    v-if="current === 'daily'"
                    class="icon-button"
                    aria-label="导出每日统计数据"
                    @click="exportDaily"
                  >
                    <AppIcon name="Download" :size="16" />
                  </button>
                </div>
              </div>
              <AppChart
                :option="
                  current === 'daily'
                    ? trendOption(stats.days, { detailed: true })
                    : hourlyOption(hours)
                "
                :height="440"
                :label="
                  current === 'daily'
                    ? '每日浏览、用户、收藏、加购与购买趋势'
                    : '按北京时间统计的每小时用户行为分布'
                "
              />
              <div class="chart-footnote">
                {{
                  current === 'daily'
                    ? '浏览数使用左轴，用户数及其余行为使用右轴；点击图例可显隐系列。'
                    : '按北京时间统计，包含当前数据日期范围内的所有事件。'
                }}
              </div>
            </section>
            <section v-if="showData" class="panel table-panel">
              <div class="panel-heading">
                <h2>趋势数据明细</h2>
                <span class="muted small"
                  >{{
                    current === 'daily' ? stats.days.length : 24
                  }}
                  个时间分组</span
                >
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>{{ current === 'daily' ? '日期' : '小时' }}</th>
                      <th>浏览数</th>
                      <th v-if="current === 'daily'">用户数</th>
                      <th>收藏数</th>
                      <th>加购数</th>
                      <th>购买数</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="item in current === 'daily' ? stats.days : hours"
                      :key="item.day || item.hour"
                    >
                      <td>
                        {{
                          item.day || `${String(item.hour).padStart(2, '0')}:00`
                        }}
                      </td>
                      <td>{{ number(item.pv) }}</td>
                      <td v-if="current === 'daily'">
                        {{ number(item.users) }}
                      </td>
                      <td>{{ number(item.fav) }}</td>
                      <td>{{ number(item.cart) }}</td>
                      <td>{{ number(item.buy) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </template>

          <template v-else-if="current === 'funnel'">
            <div class="info-strip">
              <AppIcon name="Info" :size="16" /><span
                >以「同一用户 + 同一商品」为单位，按时间顺序识别浏览 → 加购 →
                购买。事件可以跳过其他行为，但不能颠倒顺序。</span
              >
            </div>
            <section class="mini-stats">
              <article class="panel">
                <span>进入浏览阶段</span
                ><strong
                  >{{ number(path[0].value)
                  }}<small>组用户商品对</small></strong
                >
              </article>
              <article class="panel">
                <span>浏览 → 加购</span
                ><strong>{{ percentage(path[1].value, path[0].value) }}</strong>
              </article>
              <article class="panel">
                <span>浏览 → 购买</span
                ><strong>{{ percentage(path[2].value, path[0].value) }}</strong>
              </article>
            </section>
            <div class="two-column">
              <section class="panel chart-panel">
                <div class="panel-heading">
                  <h2>用户行为路径漏斗</h2>
                  <span class="muted small">全样本观察窗口</span>
                </div>
                <AppChart
                  :option="funnelOption(path)"
                  :height="355"
                  label="同用户同商品的顺序行为转化漏斗"
                />
              </section>
              <section class="panel table-panel">
                <div class="panel-heading"><h2>转化明细</h2></div>
                <table>
                  <thead>
                    <tr>
                      <th>阶段</th>
                      <th>数量</th>
                      <th>较上一阶段</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(item, index) in path" :key="item.name">
                      <td>{{ item.name }}</td>
                      <td>{{ number(item.value) }}</td>
                      <td>
                        {{
                          index
                            ? percentage(item.value, path[index - 1].value)
                            : '100%'
                        }}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p class="section-note">
                  当前为样本范围内的顺序转化统计，不代表全平台长期转化率。首页漏斗展示的是事件总量，两者口径不同。
                </p>
              </section>
            </div>
          </template>

          <template v-else-if="current === 'rfm'">
            <div class="info-strip amber">
              <AppIcon name="Info" :size="16" /><span
                >当前行为数据没有订单金额，暂使用 R（最近购买间隔）和
                F（购买频次）分群，M 显示为缺失。接入订单金额后补齐完整
                RFM。</span
              >
            </div>
            <div class="two-column">
              <section class="panel chart-panel">
                <div class="panel-heading">
                  <h2>用户价值分群</h2>
                  <span class="badge teal">RF 分群</span>
                </div>
                <AppChart
                  :option="segmentOption(segments)"
                  :height="250"
                  label="按最近购买时间和购买频次划分的用户分群"
                />
              </section>
              <section class="panel rule-panel">
                <div class="panel-heading">
                  <h2>分群口径</h2>
                  <AppIcon name="SlidersHorizontal" :size="17" />
                </div>
                <div class="rule-row">
                  <span class="rule-letter">R</span>
                  <div>
                    <strong>最近购买间隔</strong>
                    <p>距样本最后时刻的天数，≤ 2 天记为近期购买。</p>
                  </div>
                </div>
                <div class="rule-row">
                  <span class="rule-letter teal">F</span>
                  <div>
                    <strong>购买频次</strong>
                    <p>样本内购买事件数量，≥ 12 次记为高频。</p>
                  </div>
                </div>
                <div class="rule-row">
                  <span class="rule-letter gold">M</span>
                  <div>
                    <strong
                      >消费金额 <span class="muted small">待接入</span></strong
                    >
                    <p>不使用商品价格或行为次数替代真实金额。</p>
                  </div>
                </div>
              </section>
            </div>
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>用户分群与画像</h2>
                <label class="compact-select"
                  >分群<select v-model="segmentFilter">
                    <option>全部分群</option>
                    <option v-for="segment in segments" :key="segment.name">
                      {{ segment.name }}
                    </option>
                  </select></label
                >
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>用户 ID</th>
                      <th>主要兴趣</th>
                      <th>R / 天</th>
                      <th>F / 次</th>
                      <th>M / 元</th>
                      <th>用户分群</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="user in rfmUsers" :key="user.user">
                      <td class="mono">{{ user.user }}</td>
                      <td>在画像中查看</td>
                      <td>
                        {{ user.recency === null ? '无购买' : user.recency }}
                      </td>
                      <td>{{ user.frequency }}</td>
                      <td class="muted">—</td>
                      <td>
                        <span
                          class="badge"
                          :class="
                            user.segment.includes('活跃') ? 'teal' : 'blue'
                          "
                          >{{ user.segment }}</span
                        >
                      </td>
                      <td>
                        <button
                          class="text-button"
                          @click="inspectUser(user.user)"
                        >
                          查看画像<AppIcon name="ArrowUpRight" :size="13" />
                        </button>
                      </td>
                    </tr>
                    <tr v-if="!rfmUsers.length">
                      <td colspan="7" class="empty-table">
                        没有符合分群条件的用户
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ListPager
                :page="rfPage"
                :total="rfTotal"
                :page-size="50"
                @change="(value) => run(() => loadRF(value))"
              />
            </section>
          </template>

          <template v-else-if="current === 'retention'">
            <section class="mini-stats">
              <article class="panel">
                <span>首次出现用户</span
                ><strong>{{ number(stats.users) }}</strong>
              </article>
              <article class="panel">
                <span>已观察到的次日留存</span
                ><strong>{{
                  retentionAverage === null
                    ? '—'
                    : `${(retentionAverage * 100).toFixed(1)}%`
                }}</strong>
              </article>
              <article class="panel">
                <span>用户首现日期分组</span
                ><strong>{{ cohorts.length }}<small>组</small></strong>
              </article>
            </section>
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>用户留存矩阵</h2>
                <span class="muted small">颜色越深，留存越高</span>
              </div>
              <div class="table-scroll">
                <table class="retention-table">
                  <thead>
                    <tr>
                      <th>首次出现日期</th>
                      <th>用户数</th>
                      <th v-for="d in 8" :key="d">
                        {{ d === 1 ? '当日' : `第 ${d - 1} 天` }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="cohort in cohorts" :key="cohort.day">
                      <td>{{ cohort.day }}</td>
                      <td>{{ cohort.users }}</td>
                      <td
                        v-for="(value, index) in cohort.retention"
                        :key="index"
                      >
                        <span
                          class="retention-cell"
                          :style="retentionStyle(value)"
                          >{{
                            value === null
                              ? '—'
                              : `${(value * 100).toFixed(0)}%`
                          }}</span
                        >
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="section-note">
                “首次出现”指在当前样本中第一次被观察到，并非注册日期。—
                表示观察窗口尚未覆盖该天，不按 0% 计算。
              </p>
            </section>
          </template>

          <template
            v-else-if="current === 'categories' || current === 'products'"
          >
            <section class="panel chart-panel">
              <div class="panel-heading">
                <h2>
                  {{
                    current === 'categories'
                      ? '热门购买类目 Top 8'
                      : '热门购买商品 Top 8'
                  }}
                </h2>
                <span class="muted small">按购买事件数降序</span>
              </div>
              <AppChart
                :option="
                  rankingOption(
                    current === 'categories' ? categoryRank : productRank,
                    current === 'categories' ? 'category' : 'item',
                  )
                "
                :height="340"
                label="按购买事件数量统计的排行"
              />
            </section>
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>购买排名明细</h2>
                <span class="muted small">展示前 20 名</span>
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>排名</th>
                      <th>
                        {{ current === 'categories' ? '类目' : '商品 ID' }}
                      </th>
                      <th>类目 ID</th>
                      <th>购买事件</th>
                      <th>购买用户数</th>
                      <th>事件占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(item, index) in (current === 'categories'
                        ? categoryRank
                        : productRank
                      ).slice(0, 20)"
                      :key="item.id"
                    >
                      <td>
                        <span
                          class="rank-number"
                          :class="{ 'rank-top': index < 3 }"
                          >{{ String(index + 1).padStart(2, '0') }}</span
                        >
                      </td>
                      <td>
                        {{
                          current === 'categories'
                            ? labelCategory(item.id)
                            : item.id
                        }}
                      </td>
                      <td class="mono muted">{{ item.category }}</td>
                      <td>{{ item.count }}</td>
                      <td>{{ item.users }}</td>
                      <td>{{ percentage(item.count, stats.counts.buy) }}</td>
                    </tr>
                    <tr
                      v-if="
                        !(current === 'categories' ? categoryRank : productRank)
                          .length
                      "
                    >
                      <td colspan="6" class="empty-table">
                        暂无购买记录，无法生成购买排行
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </template>

          <!-- 智能推荐 -->
          <template v-else-if="current === 'recommend'">
            <section class="recommend-controls panel">
              <div>
                <AppIcon name="UserRound" :size="17" /><label
                  for="recommend-user"
                  >用户 ID</label
                ><input
                  id="recommend-user"
                  v-model="recUser"
                  list="recommend-users"
                  maxlength="128"
                  placeholder="填写或选择匿名用户 ID"
                /><datalist id="recommend-users">
                  <option
                    v-for="user in profiles"
                    :key="user.user"
                    :value="user.user"
                  >
                    {{ user.total }} 条行为
                  </option>
                </datalist>
              </div>
              <div>
                <span class="badge blue">{{ trainingStatus }}</span
                ><button
                  class="button primary"
                  :disabled="busy || !recUser || !activeDataset"
                  @click="generateRecommendation"
                >
                  <AppIcon name="Sparkles" :size="15" />{{
                    busy ? '正在查询…' : '生成推荐'
                  }}</button
                ><button
                  v-if="can('行为分析')"
                  class="button"
                  :disabled="!recUser"
                  @click="inspectUser(recUser)"
                >
                  用户画像
                </button>
              </div>
            </section>
            <section
              v-if="activeDataset && can('行为分析')"
              class="panel user-picker"
            >
              <form @submit.prevent="run(() => loadUsers(1))">
                <input
                  v-model="userSearch"
                  placeholder="按用户 ID 前缀查找候选用户"
                  maxlength="128"
                /><button class="button" type="submit">查找用户</button>
              </form>
              <ListPager
                :page="userPage"
                :total="userTotal"
                :page-size="50"
                @change="(value) => run(() => loadUsers(value))"
              />
            </section>
            <section class="stats-grid recommend-stats">
              <article class="stat-card blue">
                <span>当前用户 ID</span
                ><strong class="user-id">{{
                  recUser === 'new' ? '新用户' : recUser
                }}</strong>
              </article>
              <article class="stat-card teal">
                <span>用户交互商品</span
                ><strong>{{ number(selectedProfile?.itemCount || 0) }}</strong>
              </article>
              <article class="stat-card gold">
                <span>样本用户</span><strong>{{ number(stats.users) }}</strong>
              </article>
              <article class="stat-card purple">
                <span>样本商品</span><strong>{{ number(stats.items) }}</strong>
              </article>
            </section>
            <section class="panel recommendation-panel">
              <div class="panel-heading">
                <div>
                  <h2><span class="heading-mark"></span>商品推荐结果</h2>
                  <p class="muted small">
                    {{ trainingStatus
                    }}<span v-if="modelInfo.model" class="model-separator"
                      >·</span
                    >{{ modelInfo.model?.id || '尚无已登记模型' }}
                  </p>
                </div>
                <span class="badge blue">{{
                  recReady ? `推荐 ${recItems.length} 件商品` : '等待生成'
                }}</span>
              </div>
              <div
                v-if="recReady && recItems.length"
                class="recommendation-grid"
              >
                <button
                  v-for="item in recItems"
                  :key="item.id"
                  class="recommendation-card"
                  :class="`top-${item.rank}`"
                  @click="
                    openDialog({
                      kind: 'recommendation',
                      title: '推荐商品明细',
                      item,
                    })
                  "
                >
                  <div class="recommendation-card-top">
                    <span class="rank-badge"
                      >TOP {{ String(item.rank).padStart(2, '0') }}</span
                    ><span class="category-id">类目 {{ item.category }}</span>
                  </div>
                  <h3>商品 {{ item.id }}</h3>
                  <p>{{ item.reason }}</p>
                  <div v-if="item.score !== null" class="score-label">
                    <span>模型分数</span><strong>{{ item.score }}</strong>
                  </div>
                </button>
              </div>
              <div v-else class="empty-state">
                <span class="empty-icon"
                  ><AppIcon name="Sparkles" :size="30"
                /></span>
                <h3>
                  {{
                    recommendationError
                      ? '推荐服务尚未就绪'
                      : recReady
                        ? '暂无可推荐商品'
                        : '准备查询推荐'
                  }}
                </h3>
                <p>
                  {{
                    recommendationError ||
                    (recReady
                      ? '算法服务没有返回商品，可尝试其他用户。'
                      : '选择或填写用户 ID 后，点击“生成推荐”。')
                  }}
                </p>
                <button
                  v-if="!recReady"
                  class="button primary"
                  @click="generateRecommendation"
                >
                  生成推荐
                </button>
              </div>
              <div class="recommendation-disclaimer">
                <AppIcon name="Info" :size="15" />
                <p>
                  推荐算法由开发团队固定。模型未接入时显示实际状态；接入后的分数含义由算法组约定，不作为购买概率。商品展示匿名
                  ID 与类目 ID。
                </p>
              </div>
            </section>
          </template>

          <!-- 数据管理 -->
          <template v-else-if="current === 'data'">
            <section class="panel data-panel">
              <div class="local-tabs">
                <button
                  v-for="tab in [
                    { id: 'records', name: '行为明细' },
                    { id: 'datasets', name: '当前数据集' },
                    { id: 'schema', name: '字段说明' },
                    ...(can('数据导入') || can('任务管理')
                      ? [{ id: 'jobs', name: '后台任务' }]
                      : []),
                  ]"
                  :key="tab.id"
                  :class="{ active: dataTab === tab.id }"
                  @click="dataTab = tab.id"
                >
                  {{ tab.name }}
                </button>
              </div>
              <template v-if="dataTab === 'records'"
                ><form class="filter-bar" @submit.prevent="applyQuery">
                  <label class="search-field"
                    ><AppIcon name="Search" :size="16" /><input
                      v-model="query.keyword"
                      aria-label="搜索用户或商品ID"
                      placeholder="精确查询用户 / 商品 ID" /></label
                  ><select v-model="query.type" aria-label="行为类型">
                    <option value="">全部行为类型</option>
                    <option
                      v-for="(label, type) in behaviorLabels"
                      :key="type"
                      :value="type"
                    >
                      {{ label }}
                    </option></select
                  ><label class="date-field"
                    ><span>起</span
                    ><input
                      v-model="query.from"
                      type="date"
                      aria-label="开始日期" /></label
                  ><label class="date-field"
                    ><span>止</span
                    ><input
                      v-model="query.to"
                      type="date"
                      aria-label="结束日期" /></label
                  ><button class="button primary" type="submit">查询</button
                  ><button class="button" type="button" @click="resetQuery">
                    重置
                  </button>
                </form>
                <div class="table-toolbar">
                  <span
                    >行为明细
                    <span class="muted small"
                      >共 {{ number(behaviorTotal) }} 条</span
                    ></span
                  ><button
                    v-if="can('数据导入')"
                    class="button small-button"
                    :disabled="busy || !activeDataset"
                    @click="run(exportRows)"
                  >
                    <AppIcon name="Download" :size="14" />导出结果
                  </button>
                </div>
                <div class="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>用户 ID</th>
                        <th>商品 ID</th>
                        <th>类目 ID</th>
                        <th>行为类型</th>
                        <th>行为时间</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="record in visibleRows" :key="record.id">
                        <td class="mono">{{ record.user }}</td>
                        <td class="mono">{{ record.item }}</td>
                        <td class="mono muted">{{ record.category }}</td>
                        <td>
                          <span class="badge" :class="record.type">{{
                            behaviorText(record.type)
                          }}</span>
                        </td>
                        <td class="muted">{{ dateTime(record.t) }}</td>
                        <td>
                          <button
                            class="text-button"
                            @click="inspectUser(record.user)"
                          >
                            查看用户
                          </button>
                        </td>
                      </tr>
                      <tr v-if="!visibleRows.length">
                        <td colspan="6" class="empty-table">
                          没有符合条件的行为记录，请调整筛选条件。
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div class="pagination">
                  <span>每页 10 条，共 {{ pageCount }} 页</span>
                  <div>
                    <button
                      class="icon-button"
                      aria-label="上一页"
                      :disabled="dataPage <= 1"
                      @click="dataPage--"
                    >
                      <AppIcon name="ChevronLeft" :size="16" /></button
                    ><span class="page-number">{{ dataPage }}</span
                    ><button
                      class="icon-button"
                      aria-label="下一页"
                      :disabled="dataPage >= pageCount"
                      @click="dataPage++"
                    >
                      <AppIcon name="ChevronRight" :size="16" />
                    </button>
                  </div></div
              ></template>
              <div v-else-if="dataTab === 'datasets'" class="dataset-content">
                <div class="dataset-file">
                  <span class="dataset-icon"
                    ><AppIcon name="FileSpreadsheet" :size="30"
                  /></span>
                  <div>
                    <h2>{{ sourceFile }}</h2>
                    <p>
                      {{ source }} · {{ number(stats.total) }} 条行为 ·
                      {{ stats.users }} 位用户 · {{ stats.items }} 件商品
                    </p>
                  </div>
                  <span class="badge teal">当前使用</span>
                </div>
                <div class="description-grid">
                  <span>日期范围</span><strong>{{ dateRange }}</strong
                  ><span>数据存储</span
                  ><strong>服务器数据库，刷新或重启后仍保留</strong
                  ><span>数据版本</span
                  ><strong class="mono">{{
                    activeDataset?.id || '暂无'
                  }}</strong
                  ><span>时间口径</span><strong>北京时间 UTC+8</strong>
                </div>
                <div class="panel-heading">
                  <h2>数据集版本</h2>
                  <button
                    v-if="can('数据导入')"
                    class="button"
                    @click="run(downloadTemplate)"
                  >
                    下载导入模板
                  </button>
                </div>
                <div class="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>名称 / 版本</th>
                        <th>状态</th>
                        <th>行为数</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="dataset in datasetList" :key="dataset.id">
                        <td>
                          {{ dataset.name
                          }}<small class="dataset-version">{{
                            dataset.id
                          }}</small>
                        </td>
                        <td>
                          {{ dataset.status
                          }}<span v-if="dataset.is_demo" class="badge gold"
                            >虚构样本</span
                          >
                        </td>
                        <td>{{ number(dataset.row_count) }}</td>
                        <td>
                          <span v-if="dataset.active" class="badge teal"
                            >当前使用</span
                          ><button
                            v-else-if="
                              dataset.status === 'ready' && can('数据导入')
                            "
                            class="text-button"
                            @click="run(() => activateDataset(dataset.id))"
                          >
                            切换使用
                          </button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <ListPager
                  :page="datasetPage"
                  :total="datasetTotal"
                  @change="
                    (value) => {
                      datasetPage = value;
                      run(loadDatasets);
                    }
                  "
                />
              </div>
              <div v-else-if="dataTab === 'jobs'" class="dataset-content">
                <div class="panel-heading">
                  <h2>导入与导出任务</h2>
                  <button
                    v-if="can('任务管理')"
                    class="button"
                    @click="run(showServerImport)"
                  >
                    导入服务器文件</button
                  ><button class="button" @click="run(loadJobs)">
                    刷新任务
                  </button>
                </div>
                <div class="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>任务</th>
                        <th>类型</th>
                        <th>状态</th>
                        <th>已处理行</th>
                        <th>详情 / 操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="job in jobs" :key="job.id">
                        <td class="mono">{{ job.id.slice(0, 12) }}</td>
                        <td>{{ job.kind === 'import' ? '导入' : '导出' }}</td>
                        <td>
                          <span
                            class="badge"
                            :class="job.status === 'failed' ? 'gold' : 'teal'"
                            >{{ job.status }}</span
                          >
                        </td>
                        <td>{{ number(job.processed_rows) }}</td>
                        <td>
                          <span v-if="job.error" class="error-message">{{
                            job.error
                          }}</span
                          ><button
                            v-if="
                              job.kind === 'export' && job.status === 'ready'
                            "
                            class="text-button"
                            @click="run(() => downloadJob(job.id))"
                          >
                            下载文件</button
                          ><button
                            v-if="
                              job.status === 'running' &&
                              can('任务管理') &&
                              Date.now() / 1000 - job.updated_at > 600
                            "
                            class="text-button"
                            @click="run(() => recoverJob(job))"
                          >
                            结束中断任务
                          </button>
                        </td>
                      </tr>
                      <tr v-if="!jobs.length">
                        <td colspan="5" class="empty-table">暂无后台任务</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <ListPager
                  :page="jobPage"
                  :total="jobTotal"
                  @change="
                    (value) => {
                      jobPage = value;
                      run(loadJobs);
                    }
                  "
                />
                <p class="section-note">
                  任务持续在后台执行。失败版本不会替换当前数据；请修正源文件后重新导入。
                </p>
              </div>
              <div v-else class="schema-content">
                <table>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>含义</th>
                      <th>示例 / 说明</th>
                      <th>要求</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td class="mono">user_id</td>
                      <td>用户标识</td>
                      <td>1003707</td>
                      <td>必填</td>
                    </tr>
                    <tr>
                      <td class="mono">item_id</td>
                      <td>商品标识</td>
                      <td>1325619，字符串 ID</td>
                      <td>必填</td>
                    </tr>
                    <tr>
                      <td class="mono">category_id</td>
                      <td>商品类目</td>
                      <td>982926</td>
                      <td>可选</td>
                    </tr>
                    <tr>
                      <td class="mono">behavior_type</td>
                      <td>行为类型</td>
                      <td>pv / fav / cart / buy</td>
                      <td>必填</td>
                    </tr>
                    <tr>
                      <td class="mono">timestamp</td>
                      <td>发生时间</td>
                      <td>10 位秒、13 位毫秒或带时区的 ISO 时间</td>
                      <td>必填</td>
                    </tr>
                  </tbody>
                </table>
                <p class="section-note">
                  接受 UTF-8 带表头 CSV 或相同字段的
                  Parquet。字段和行为类型须先由数据组转换；导入原样保留重复事件，不自动清洗。网页明细默认显示最近
                  7 天；全量统计使用整个数据版本。
                </p>
              </div>
            </section>
          </template>

          <template v-else-if="current === 'logs'">
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>操作日志</h2>
                <label class="compact-select"
                  >模块<select v-model="logModule">
                    <option>全部模块</option>
                    <option>系统</option>
                    <option>数据管理</option>
                    <option>智能推荐</option>
                    <option>系统管理</option>
                  </select></label
                >
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>操作时间</th>
                      <th>模块</th>
                      <th>操作</th>
                      <th>状态</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="log in filteredLogs" :key="log.id">
                      <td class="muted nowrap">{{ log.time }}</td>
                      <td>{{ log.module }}</td>
                      <td>{{ log.action }}</td>
                      <td>
                        <span
                          class="badge"
                          :class="log.status === '演示' ? 'gold' : 'teal'"
                          >{{ log.status }}</span
                        >
                      </td>
                      <td class="muted">{{ log.detail }}</td>
                    </tr>
                    <tr v-if="!filteredLogs.length">
                      <td colspan="5" class="empty-table">
                        该模块暂无操作记录
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ListPager
                :page="logPage"
                :total="logTotal"
                @change="
                  (value) => {
                    logPage = value;
                    run(loadLogs);
                  }
                "
              />
              <p class="section-note">
                日志由服务器记录并持久保存。管理员查看全部操作，其他账号查看自己的操作。
              </p>
            </section>
          </template>

          <template v-else-if="current === 'messages'">
            <div class="message-layout">
              <section class="panel message-editor">
                <div class="panel-heading">
                  <h2>发布留言</h2>
                  <AppIcon name="MessageSquare" :size="18" />
                </div>
                <label class="form-field"
                  >留言类型<select v-model="messageForm.type">
                    <option>功能建议</option>
                    <option>问题反馈</option>
                    <option>项目待办</option>
                    <option>数据说明</option>
                  </select></label
                ><label class="form-field"
                  >留言内容<textarea
                    v-model="messageForm.content"
                    maxlength="500"
                    rows="7"
                    placeholder="写下对数据分析、推荐功能或页面交互的建议…"
                  ></textarea
                  ><span class="input-count"
                    >{{ messageForm.content.length }} / 500</span
                  ></label
                ><button class="button primary" @click="run(postMessage)">
                  <AppIcon name="Plus" :size="15" />发布留言
                </button>
                <p class="section-note">留言保存到数据库。</p>
              </section>
              <section class="message-list">
                <article
                  v-for="message in messages"
                  :key="message.id"
                  class="panel message-card"
                >
                  <div class="message-head">
                    <span class="avatar avatar-small">{{
                      message.name.slice(0, 1)
                    }}</span>
                    <div>
                      <strong>{{ message.name }}</strong
                      ><small>{{ message.time }}</small>
                    </div>
                    <span class="badge blue">{{ message.type }}</span>
                  </div>
                  <p>{{ message.content }}</p>
                  <button
                    class="text-button"
                    :class="{ 'is-resolved': message.done }"
                    v-if="message.author_id === personal.id || can('账号管理')"
                    @click="run(() => toggleMessage(message))"
                  >
                    <AppIcon
                      :name="message.done ? 'CircleCheck' : 'Check'"
                      :size="14"
                    />{{ message.done ? '已处理 · 撤回标记' : '标记为已处理' }}
                  </button>
                </article>
                <ListPager
                  :page="messagePage"
                  :total="messageTotal"
                  @change="
                    (value) => {
                      messagePage = value;
                      run(loadMessages);
                    }
                  "
                />
              </section>
            </div>
          </template>

          <template v-else-if="current === 'accounts'">
            <div class="info-strip">
              <AppIcon name="Info" :size="16" /><span
                >系统账号用于登录和权限管理，与行为数据中的匿名用户 ID
                分开。停用账号或修改角色会立即使其已有登录失效。</span
              >
            </div>
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>系统账号</h2>
                <span class="muted small">{{ accountTotal }} 个系统账号</span>
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>账号</th>
                      <th>显示名称</th>
                      <th>角色</th>
                      <th>状态</th>
                      <th>创建日期</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="account in accounts" :key="account.id">
                      <td class="mono">{{ account.id }}</td>
                      <td>{{ account.name }}</td>
                      <td>
                        <span class="badge blue">{{ account.role }}</span>
                      </td>
                      <td>
                        <span
                          class="badge"
                          :class="account.enabled ? 'teal' : 'gray'"
                          >{{ account.enabled ? '启用' : '停用' }}</span
                        >
                      </td>
                      <td class="muted">{{ account.created }}</td>
                      <td>
                        <div class="inline-actions">
                          <button
                            class="text-button"
                            @click="editAccount(account)"
                          >
                            编辑</button
                          ><button
                            class="text-button"
                            @click="showPassword(account)"
                          >
                            重置密码</button
                          ><button
                            class="text-button muted"
                            :disabled="account.id === personal.id"
                            @click="run(() => toggleAccount(account))"
                          >
                            {{ account.enabled ? '停用' : '启用' }}
                          </button>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ListPager
                :page="accountPage"
                :total="accountTotal"
                @change="
                  (value) => {
                    accountPage = value;
                    run(loadAccounts);
                  }
                "
              />
            </section>
          </template>

          <template v-else-if="current === 'roles'">
            <section class="panel table-panel">
              <div class="panel-heading">
                <h2>角色权限矩阵</h2>
                <button class="button" @click="run(toggleRoleEditing)">
                  <AppIcon
                    :name="roleEditing ? 'Check' : 'Pencil'"
                    :size="14"
                  />{{ roleEditing ? '保存权限配置' : '编辑配置' }}
                </button>
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>功能权限</th>
                      <th
                        v-for="role in Object.keys(rolePermissions)"
                        :key="role"
                      >
                        {{ role }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="permission in permissions" :key="permission">
                      <td>{{ permission }}</td>
                      <td
                        v-for="(allowed, role) in rolePermissions"
                        :key="role"
                      >
                        <input
                          v-if="roleEditing"
                          :disabled="
                            roleIds[role] === 'admin' ||
                            permission === '数据概览'
                          "
                          v-model="rolePermissions[role]"
                          type="checkbox"
                          :value="permission"
                          :aria-label="`${role}的${permission}权限`"
                        /><AppIcon
                          v-else-if="allowed.includes(permission)"
                          name="CircleCheck"
                          class="permission-check"
                          :size="17"
                        /><span v-else class="muted">—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="section-note">
                权限保存后由服务端实时校验。管理员保留全部权限；所有角色保留数据概览权限。
              </p>
            </section>
          </template>

          <template v-else-if="current === 'personal'">
            <div class="personal-layout">
              <section class="panel personal-summary">
                <span class="avatar avatar-large">{{
                  personal.name.slice(0, 1)
                }}</span>
                <h2>{{ personal.name }}</h2>
                <span class="badge blue">{{ personal.role }}</span>
                <div class="personal-meta">
                  <p>
                    <AppIcon name="ShieldCheck" :size="15" />系统管理与数据分析
                  </p>
                  <p><AppIcon name="Users" :size="15" />{{ personal.team }}</p>
                </div>
              </section>
              <section class="panel personal-form">
                <div class="panel-heading">
                  <h2>基本资料</h2>
                  <span class="muted small">资料保存到数据库</span>
                </div>
                <form @submit.prevent="run(savePersonal)">
                  <label class="form-field"
                    >显示名称<input
                      v-model="personal.name"
                      maxlength="20"
                      required /></label
                  ><label class="form-field"
                    >邮箱<input v-model="personal.email" type="email" /></label
                  ><label class="form-field"
                    >所属团队<input
                      v-model="personal.team"
                      maxlength="40" /></label
                  ><label class="form-field"
                    >备注<textarea
                      v-model="personal.note"
                      rows="3"
                      maxlength="200"
                    ></textarea></label
                  ><button type="submit" class="button primary">保存资料</button
                  ><button type="button" class="button" @click="showPassword()">
                    修改密码
                  </button>
                </form>
              </section>
            </div>
          </template>
        </template>
        <footer class="page-footer">
          <span>电商平台用户行为分析与精准推荐系统</span
          ><span
            >分析工作台 v0.2 <span class="footer-dot">·</span>
            {{ source }}</span
          >
        </footer>
      </main>
    </div>

    <!-- 详情与操作弹窗 -->
    <Teleport to="body"
      ><div v-if="dialog" class="modal-backdrop" @click.self="closeDialog">
        <section
          ref="modalElement"
          class="modal"
          :class="{
            'wide-modal': dialog.kind === 'user' || dialog.kind === 'import',
          }"
          role="dialog"
          aria-modal="true"
          aria-labelledby="modal-title"
        >
          <header class="modal-heading">
            <div>
              <span class="eyebrow">{{
                dialog.kind === 'user' ? 'USER PORTRAIT' : '工作台'
              }}</span>
              <h2 id="modal-title">{{ dialog.title }}</h2>
            </div>
            <button
              class="icon-button"
              aria-label="关闭弹窗"
              @click="closeDialog"
            >
              <AppIcon name="X" :size="19" />
            </button>
          </header>
          <div v-if="dialog.kind === 'user'" class="modal-body">
            <template v-if="dialogUser"
              ><div class="user-profile-header">
                <span class="avatar"
                  ><AppIcon name="UserRound" :size="25"
                /></span>
                <div>
                  <h2>用户 {{ dialogUser.user }}</h2>
                  <span class="muted small"
                    >{{ dialogUser.total }} 条行为 ·
                    {{ dialogUser.itemCount }} 件商品</span
                  >
                </div>
                <span class="badge teal">{{ dialogUser.segment }}</span>
              </div>
              <div class="profile-behaviors">
                <div v-for="(label, type) in behaviorLabels" :key="type">
                  <span>{{ label }}</span
                  ><strong>{{ dialogUser.counts[type] }}</strong>
                </div>
              </div>
              <div class="profile-columns">
                <section>
                  <h3>兴趣类目分布</h3>
                  <AppChart
                    :option="interestOption(dialogUser)"
                    :height="245"
                    label="当前用户的类目兴趣分布"
                  />
                </section>
                <section>
                  <h3>最近行为</h3>
                  <div class="timeline">
                    <div
                      v-for="event in dialogUser.rows
                        .slice()
                        .sort((a, b) => b.t - a.t)
                        .slice(0, 5)"
                      :key="event.id"
                      class="timeline-row"
                    >
                      <span class="timeline-dot"></span>
                      <div>
                        <strong
                          >商品 {{ event.item
                          }}<span class="badge" :class="event.type">{{
                            behaviorText(event.type)
                          }}</span></strong
                        ><small>{{ dateTime(event.t) }}</small>
                      </div>
                    </div>
                  </div>
                </section>
              </div>
              <p class="section-note">
                类目分布按用户行为事件次数汇总；图中展示前 5 类，分母为前 20
                类事件总数。此处是描述性统计，不是推荐模型结果。
              </p></template
            >
            <div v-else class="empty-state">
              <AppIcon name="UserRound" :size="32" />
              <h3>
                {{ dialogLoading ? '正在读取用户画像…' : '该用户暂无历史行为' }}
              </h3>
              <p>冷启动推荐由后续算法服务处理。</p>
            </div>
          </div>
          <div v-else-if="dialog.kind === 'recommendation'" class="modal-body">
            <div class="item-detail-title">
              <span class="rank-badge">TOP {{ dialog.item.rank }}</span>
              <h2>商品 {{ dialog.item.id }}</h2>
              <p>
                {{ labelCategory(dialog.item.category) }} · 类目 ID
                {{ dialog.item.category }}
              </p>
            </div>
            <div class="description-grid">
              <span>目标用户</span><strong>{{ recUser }}</strong
              ><span>模型分数</span
              ><strong>{{ dialog.item.score ?? '未提供' }}</strong
              ><span>推荐说明</span><strong>{{ dialog.item.reason }}</strong
              ><span>候选过滤</span><strong>由算法服务按固定配置执行</strong>
            </div>
            <div class="info-strip">
              <AppIcon name="Info" :size="16" /><span
                >结果由已登记的算法服务返回，具体分数和推荐依据以算法组说明为准。</span
              >
            </div>
          </div>
          <div v-else-if="dialog.kind === 'import'" class="modal-body">
            <div class="upload-zone">
              <span class="dataset-icon"
                ><AppIcon name="Upload" :size="27"
              /></span>
              <h3>导入行为数据</h3>
              <p>网页上传最多 100 MB，支持标准 CSV 和 Parquet。</p>
              <button
                class="button primary"
                :disabled="busy"
                @click="importInput?.click()"
              >
                选择文件</button
              ><button class="text-button" @click="run(downloadTemplate)">
                下载模板<AppIcon name="Download" :size="13" /></button
              ><input
                ref="importInput"
                type="file"
                accept=".csv,.parquet"
                hidden
                @change="readFile"
              />
            </div>
            <details v-if="!busy" class="paste-details">
              <summary>或粘贴标准 CSV 内容</summary>
              <label class="form-field"
                >行为数据<textarea
                  v-model="pasteCSV"
                  rows="4"
                  placeholder="user_id,item_id,category_id,behavior_type,timestamp"
                ></textarea></label
              ><button
                class="button"
                @click="checkText(pasteCSV, '粘贴的行为数据.csv')"
              >
                使用此内容
              </button>
            </details>
            <p v-if="importError" class="error-message" role="alert">
              {{ importError }}
            </p>
            <div v-if="importPreview" class="import-success">
              <AppIcon name="FileSpreadsheet" :size="18" />
              <div>
                <strong>{{ importName }}</strong>
                <p>
                  {{
                    number(importPreview.bytes)
                  }}
                  字节，确认后由后台逐批校验入库。
                </p>
              </div>
            </div>
            <div v-if="importJob" class="info-strip">
              <span
                >任务 {{ importJob.id.slice(0, 12) }} · {{ importJob.status }} ·
                已写入 {{ number(importJob.processed_rows) }} 条</span
              >
            </div>
            <p class="section-note">
              不会去重、修补或推断行为。遇到无效记录时整个版本失败，当前数据保持可用。大文件请放到服务器指定目录后提交导入任务；导入成功的数据会持久保存。
            </p>
          </div>
          <div v-else-if="dialog.kind === 'server-import'" class="modal-body">
            <p class="section-note">
              先将数据组处理好的 CSV 或 Parquet 放入本机 backend/instance/inbox
              文件夹。这里仅提交文件名，数据由后台分批读取，不经过浏览器上传。
            </p>
            <label class="form-field"
              >文件名<input
                v-model="serverForm.relative_path"
                list="server-inbox-files"
                maxlength="300"
                placeholder="例如 cleaned-events.parquet"
              /><datalist id="server-inbox-files">
                <option
                  v-for="file in serverFiles"
                  :key="file.relative_path"
                  :value="file.relative_path"
                >
                  {{ number(file.bytes) }} 字节
                </option>
              </datalist></label
            ><label class="form-field"
              >数据集名称<input
                v-model="serverForm.name"
                maxlength="100"
                placeholder="可选，方便区分数据版本"
            /></label>
            <p v-if="importError" class="error-message" role="alert">
              {{ importError }}
            </p>
            <p v-if="importJob" class="section-note">
              任务 {{ importJob.id.slice(0, 12) }} · {{ importJob.status }} ·
              已处理 {{ number(importJob.processed_rows) }} 条
            </p>
          </div>
          <div v-else-if="dialog.kind === 'account'" class="modal-body">
            <label class="form-field"
              >账号<input
                v-model="accountForm.id"
                :disabled="dialog.editing"
                placeholder="小写字母开头，3–30 位字母、数字、下划线或短横线"
                maxlength="30" /></label
            ><label class="form-field"
              >显示名称<input
                v-model="accountForm.name"
                maxlength="40" /></label
            ><label v-if="!dialog.editing" class="form-field"
              >初始密码<input
                v-model="accountForm.password"
                type="password"
                autocomplete="new-password"
                minlength="10"
                maxlength="128"
                placeholder="至少 10 个字符" /></label
            ><label class="form-field"
              >角色<select v-model="accountForm.role_id">
                <option value="admin">管理员</option>
                <option value="analyst">分析员</option>
                <option value="viewer">访客</option>
              </select></label
            ><label class="checkbox-field"
              ><input
                v-model="accountForm.enabled"
                type="checkbox"
              />启用账号</label
            >
            <p v-if="accountError" class="error-message" role="alert">
              {{ accountError }}
            </p>
            <p class="section-note">
              密码加密保存，不提供原密码查询。请为每位成员单独创建账号。
            </p>
          </div>
          <div v-else-if="dialog.kind === 'password'" class="modal-body">
            <label v-if="!dialog.account" class="form-field"
              >当前密码<input
                v-model="passwordForm.current_password"
                type="password"
                autocomplete="current-password" /></label
            ><label class="form-field"
              >新密码<input
                v-model="passwordForm.password"
                type="password"
                minlength="10"
                maxlength="128"
                autocomplete="new-password"
                placeholder="至少 10 个字符"
            /></label>
            <p v-if="accountError" class="error-message" role="alert">
              {{ accountError }}
            </p>
            <p class="section-note">保存后，该账号的所有已有登录会立即失效。</p>
          </div>
          <footer class="modal-footer">
            <button class="button" @click="closeDialog">
              {{
                busy && dialog.kind === 'import'
                  ? '转到后台运行'
                  : ['user', 'recommendation'].includes(dialog.kind)
                    ? '关闭'
                    : '取消'
              }}</button
            ><button
              v-if="dialog.kind === 'user' && can('推荐查看')"
              class="button primary"
              @click="recommendForUser(dialog.user)"
            >
              <AppIcon name="Sparkles" :size="15" />为该用户生成推荐</button
            ><button
              v-if="dialog.kind === 'import'"
              class="button primary"
              :disabled="busy || !importPreview"
              @click="applyImport"
            >
              确认导入</button
            ><button
              v-if="dialog.kind === 'server-import'"
              class="button primary"
              :disabled="busy || !serverForm.relative_path"
              @click="applyServerImport"
            >
              提交导入</button
            ><button
              v-if="dialog.kind === 'account'"
              class="button primary"
              :disabled="busy"
              @click="saveAccount"
            >
              保存</button
            ><button
              v-if="dialog.kind === 'password'"
              class="button primary"
              :disabled="busy"
              @click="savePassword"
            >
              保存密码
            </button>
          </footer>
        </section>
      </div>
      <Transition name="toast"
        ><div
          v-if="toast"
          class="toast-message"
          role="status"
          aria-live="polite"
        >
          <AppIcon name="CircleCheck" :size="18" /><span>{{ toast }}</span
          ><button aria-label="关闭提示" @click="toast = ''">
            <AppIcon name="X" :size="14" />
          </button></div></Transition
    ></Teleport>
  </div>
</template>
