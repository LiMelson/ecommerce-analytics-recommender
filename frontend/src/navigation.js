export const navigation = [
  { id: 'home', title: '首页', icon: 'House', description: '用户行为概览与工作空间' },
  { id: 'screen', title: '数据大屏', icon: 'PanelsTopLeft', description: '集中查看核心指标、行为趋势与购买排行' },
  { id: 'analysis', title: '数据分析', icon: 'ChartNoAxesCombined', children: [
    { id: 'daily', title: '每日趋势分析', description: '按日期查看流量及用户行为趋势。' },
    { id: 'hourly', title: '每时趋势分析', description: '了解用户在一天中何时最活跃。' },
    { id: 'funnel', title: '漏斗转化分析', description: '沿同用户、同商品的行为路径观察转化。' },
    { id: 'rfm', title: 'RFM客户分析', description: '从最近购买时间与购买频次理解用户价值。' },
    { id: 'retention', title: '用户留存分析', description: '以首次出现日期分组，观察用户后续活跃。' },
    { id: 'categories', title: '购买类目排名', description: '发现用户偏好的商品类目。' },
    { id: 'products', title: '购买商品排名', description: '查看购买事件最多的商品。' },
  ] },
  { id: 'recommend', title: '智能推荐', icon: 'Sparkles', description: '根据浏览、收藏、加购及购买行为，为指定用户生成推荐。' },
  { id: 'data', title: '数据管理', icon: 'Database', description: '导入和查询用户行为数据，检查字段与数据质量。' },
  { id: 'logs', title: '日志管理', icon: 'ScrollText', description: '查看数据导入、推荐生成及演示任务的操作记录。' },
  { id: 'messages', title: '系统留言', icon: 'MessageSquare', description: '记录项目反馈、待办事项与改进建议。' },
  { id: 'system', title: '系统管理', icon: 'Settings', children: [
    { id: 'accounts', title: '用户管理', description: '管理展示版账号及角色。' },
    { id: 'roles', title: '角色权限', description: '查看管理员与分析员的角色划分。' },
  ] },
  { id: 'personal', title: '个人信息', icon: 'UserRound', description: '查看与编辑当前展示账号的资料。' },
];
export const pageMap = Object.fromEntries(navigation.flatMap(p => p.children ? p.children.map(c => [c.id, { ...c, icon: 'ChartNoAxesCombined', parent: p.title }]) : [[p.id, p]]));
