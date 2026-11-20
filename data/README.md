# 数据获取与本地存放

仓库不包含真实电商数据或已训练模型。网站联调可使用明确标记的虚构样本，
不能当作 Kaggle 真实数据或算法效果。

## v0.1 主数据集

**天池 220316 · eCommerce behavior data from multi category store（REES46/Open CDP）**

- 数据页：<https://tianchi.aliyun.com/dataset/220316>
- 本地文件：
  - `data/raw/2019-Oct.csv`（约 5.3GB）
  - `data/raw/2019-Nov.csv`（约 8.4GB）
- 覆盖时间：2019-10-01 至 2019-11-30（UTC）
- 字段：`event_time, event_type, product_id, category_id, category_code, brand, price, user_id, user_session`
- 行为：view、cart、purchase；这两个月没有 fav 和 remove_from_cart
- 全量：109,950,743 行，5,316,649 用户，206,876 商品
- 抽样：按用户 1%（52,687 人 / 1,079,354 条），保留完整历史
- 许可：天池标注 CC BY-ND 4.0；Kaggle 原页面标注数据文件版权归原作者。课程内使用并注明来源，不再公开分发原始数据或用户级明细。

质量、清洗规则、六类分析口径和已知限制见 [数据选型与处理说明](../docs/数据选型与处理说明.md)。

### 这份数据的边界

- 没有年龄、性别、城市 → 不推断人口属性；
- 没有订单号、数量、优惠、运费 → 金额是"购买事件单价汇总"，不是订单 GMV；
- 类目名缺失 32.2%、品牌缺失 13.9% → 保留 null，不编造；
- 留存是"样本首现留存"，不是注册留存；
- 原始时间是 UTC，界面和统计按北京时间（UTC+8）。

## 备选数据集

以下保留作为回退或对照，不代表已下载：

- [RetailRocket](https://www.kaggle.com/datasets/retailrocket/ecommerce-dataset)：浏览、加购和交易事件；无价格，类目需关联商品属性。
- [REES46 化妆品店](https://www.kaggle.com/datasets/mkechinov/ecommerce-events-history-in-cosmetics-shop)：字段与 220316 相同，单类目、体量更小。
- [淘宝 UserBehavior 649](https://tianchi.aliyun.com/dataset/649)：9 天、5 列，适合做经典对照，但留存和金额分析弱。

不要并行适配多个大数据集；不同数据源中数值相同的用户/商品 ID 不能直接拼接。

## 本地目录约定

```text
data/
├── README.md          # 本文件提交到 Git
├── raw/               # 原始 CSV，本地保存，不提交
│   ├── 2019-Oct.csv
│   └── 2019-Nov.csv
├── processed/         # 清洗与抽样后的 Parquet，本地保存，不提交
│   └── events_sample.parquet
├── exports/           # 网站标准入库 CSV/Parquet，本地生成，不提交
│   └── website_events.csv
└── splits/            # 固定训练/验证/测试划分，本地保存，不提交
```

网站入库文件由 `data_algo.cli export-website` 生成，只含
`user_id,item_id,category_id,behavior_type,timestamp` 五列；
timestamp 为 UTC 毫秒，未知类目为空，remove_from_cart 等非核心行为排除。
完整训练文件仍保留品牌、价格、会话等特征。

除本说明外，`data/` 内文件均已加入根目录忽略规则。数据下载地址、版本、清洗方式、质量统计和评估口径写入文档，不通过 Git 传递大文件。

## 约定

- 用户、商品、类目 ID 统一使用字符串；
- 时间保存为 UTC，界面按北京时间展示；
- 缺失的收藏或金额字段不人工补造；
- 统计与训练使用固定数据版本、抽样规则和随机种子；
- 原始数据、抽样 Parquet、用户级画像和模型权重不提交到仓库。
