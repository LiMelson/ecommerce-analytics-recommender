# 后端服务 · v0.2

FastAPI API + MySQL 业务库 + ClickHouse 行为分析库 + 独立任务 worker。账号、权限、导入/导出、版本切换、统计、画像、留言、日志和模型元数据接口已实现。推荐算法与数据清洗由其他成员交付。

## 环境与配置

需要 Python 3.11、MySQL 8、Docker Desktop（Linux containers）。当前依赖已锁定在 `requirements.txt`，其中包含测试和代码检查工具；`requirements.in` 记录直接依赖。前端使用 Node.js 24 与 Vue 3。

在仓库根目录安装依赖：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd backend
.venv/Scripts/python.exe -m app.cli init-config
```

编辑本地 `backend/.env` 的 `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`，默认项目数据库为 `commerce_insights`。初始化会按名称创建项目数据库并运行 Alembic，不修改其他数据库；连接账号需要相应建库、建表权限。ClickHouse 密码由 init-config 随机生成，不要手填公开默认密码。

`.env`、首次管理员密码、数据库文件、收件文件、导出和日志均被 Git 忽略。不要复制个人 `.env` 给组员，每位成员配置自己的开发环境。

## 启动（Windows）

先打开 Docker Desktop，确认本机 MySQL 已启动。在仓库根目录执行：

```powershell
./scripts/start-local.ps1
```

该脚本启动 ClickHouse、执行数据库迁移，并在隐藏窗口启动 API 和 worker；若 5173 未占用还会启动前端。仅记录自己创建的进程，遇到已有服务不会盲目结束它。

- 前端：<http://127.0.0.1:5173/>
- API 文档：<http://127.0.0.1:8000/api/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>
- 首次管理员：`admin`；随机密码保存在 `backend/instance/first-admin.txt`。重复初始化不重置管理员密码。

```powershell
./scripts/stop-local.ps1
```

停止脚本只停止它记录且验证过的本项目进程，MySQL 和 ClickHouse 保持运行，数据不删除。

## 分别启动服务（各开一个终端）

在根目录启动 ClickHouse：

```sh
docker compose --env-file backend/.env -f infra/compose.yaml up -d --wait
```

进入 backend 并激活虚拟环境后：

```sh
python -m app.cli init
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个终端同样进入 backend、使用同一个虚拟环境：

```sh
python -m app.worker
```

Linux/macOS 可用 `.venv/bin/python` 替代 Windows 的 `.venv/Scripts/python.exe`。API 查询与导入任务分进程执行，关闭网页不取消后台任务。健康检查只检测数据库，不检测 worker；若任务一直 queued，检查 worker 是否运行。

## 数据导入

接受标准 UTF-8 CSV 或 Parquet，字段为 `user_id,item_id,category_id,behavior_type,timestamp`，category_id 可选。行为仅接受 `pv/fav/cart/buy`。时间使用秒、毫秒或明确带时区的 ISO 时间。完整约定见 [数据与算法交付约定](../docs/数据与算法交付约定.md)。

- 网页上传上限默认 100 MB；先入队，后台逐批校验、入库和生成汇总。
- 大文件先放入 `backend/instance/inbox/`，管理员在“数据管理 → 后台任务”提交服务器文件导入；也可在 backend 目录运行 `python -m app.cli import-file 文件名.parquet --name 数据版本名称`。
- 只做格式校验，不清洗、不跳过坏行、不去重。失败版本不发布，已写入的失败数据暂留在 ClickHouse，供排错和后续统一清理。
- 同一任务不在部分写入后原地重试。进程中断且十分钟无心跳时，管理员可结束任务，再提交新版本。
- 导出为后台任务，最多 100 万条，训练不通过网页导出全量记录。

保留原前端虚构数据用于联调，可在根目录显式运行：

```sh
node frontend/scripts/export-demo.mjs
cd backend
python -m app.cli import-file demo.csv --name 虚构演示样本 --demo
```

该数据不是 Kaggle 真实记录，也不是训练结果；使用 `--demo` 后界面会标注虚构样本。

## 数据库与版本

- MySQL：账号、角色、会话、数据集、任务、操作日志、留言、模型登记和活动版本设置。
- ClickHouse：行为明细、日统计、小时统计、用户汇总和购买排行；数据导入完成后生成，页面无需反复全表计算。
- 文件：清洗和训练 Parquet 由数据组管理；上传暂存、导出和密码文件保留本地。

MySQL 结构由 `migrations/versions/` 中的冻结迁移维护。修改 ORM 后须新建 Alembic 迁移，不改历史迁移。ClickHouse 初始结构在 `app/analytics.py` 的 DDL 中；后续结构升级应单独编写版本迁移，不能只修改已部署表的 CREATE IF NOT EXISTS。

参考建表 SQL 与架构见 [数据库设计](../docs/数据库设计.md)。跨库不使用分布式事务，采用“先写分析库、成功后通过 MySQL 发布”的方式，查询只接受 ready 版本。

## 验证

在 backend 目录，使用虚拟环境执行：

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check app tests migrations
.venv/Scripts/ruff.exe format --check app tests migrations
$env:RUN_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q tests/test_integration.py
```

真实数据库测试使用随机 `commerce_test_*` 库，结束时仅删除自己创建的隔离测试库，不读取或清空项目行为数据。另设 `RUN_SCALE_TESTS=1` 才执行百万条虚构数据负载测试；它较慢，非每次修改都需要运行。结果保存本机 `instance/scale-report.json`，范围与限制见 [后端验收记录](../docs/后端验收记录.md)。

## 当前边界

算法登记和推理 HTTP 适配已实现，但没有训练代码或生产推荐结果。配置 `RECOMMENDER_URL` 并登记兼容的模型后才会请求算法服务；未接入时返回明确状态。未实现数据清洗、自动修补、自动去重、金额 M、真实订单/支付或实时推荐效果监测。

当前为单机、单 API 进程、单 worker 的本地开发部署。ClickHouse 容器内存限制 3 GiB，查询线程、执行时间和内存也有限制，并启用聚合及排序溢写磁盘。百万级验证不等于亿级容量保证；全量采用前必须按真实数据、磁盘、基数与查询范围继续压测。

本地服务默认只监听回环地址。正式部署还需 HTTPS、专用数据库账号、共享限流、反向代理请求体限制、备份与监控；API 已实现 Cookie 会话、CSRF、来源校验及账号权限控制。
