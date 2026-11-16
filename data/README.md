# 数据获取与本地存放

仓库不包含真实电商数据或已训练模型。前端默认数据由 `frontend/src/data/behavior.js` 生成，是虚构演示样本。

候选公开数据集：

- [RetailRocket](https://www.kaggle.com/datasets/retailrocket/ecommerce-dataset)：浏览、加购和交易事件；类目需关联商品属性。
- [REES46 化妆品店](https://www.kaggle.com/datasets/mkechinov/ecommerce-events-history-in-cosmetics-shop)：浏览、加购、移出购物车和购买，包含会话等字段。

主数据集尚未最终确定。已核验的字段、大小、使用说明和限制见 [Kaggle 数据集调研](../docs/后端开发清单与Kaggle数据集调研.md)。下载和使用时保留发布者的来源与许可要求。

约定本地目录：

```text
data/
├── README.md          # 本文件提交到 Git
├── raw/               # 原始文件，本地保存
├── processed/         # 清洗与统一字段后的文件，本地保存
└── splits/            # 固定训练、验证与测试划分，本地保存
```

除本说明外，`data/` 内文件均已加入忽略规则。将下载地址、数据版本、清洗方式和数据质量统计写入项目文档，避免通过 Git 传递大文件。

用户、商品和类目 ID 统一使用字符串，时间统一明确时区；缺失的收藏或金额字段不人工补造。不要将不同数据源中数值相同的 ID 直接合并。
