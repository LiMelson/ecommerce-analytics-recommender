# 团队协作指南

## 获取项目

组长先在 GitHub 仓库的 Settings → Collaborators 中邀请组员，组员接受邀请后即可向本仓库推送自己的分支。

```sh
git clone https://github.com/LiMelson/ecommerce-analytics-recommender.git
cd ecommerce-analytics-recommender
```

`main` 保存能够运行和演示的版本。每项任务使用一个独立分支，例如 `feat/behavior-import`、`feat/recommendation-api`、`feat/itemcf-baseline`、`fix/retention-chart`。

## 日常开发与提交

开始新任务前，先更新主分支；以下命令假设工作区中没有未提交的修改：

```sh
git switch main
git pull --ff-only origin main
git switch -c feat/your-task
```

开发完成后检查文件清单，只暂存与本任务有关的文件。例如修改了首页：

```sh
git status
git diff
git add frontend/src/App.vue
git commit -m "feat(frontend): 接入首页统计接口"
git push -u origin feat/your-task
```

分支名请换成自己的实际任务名。常用提交前缀为 `feat`（新功能）、`fix`（修复）、`docs`（文档）、`test`（测试）、`chore`（维护）。

## 合并代码

1. 推送分支后，在 GitHub 打开 Pull Request，目标分支选择 `main`。
2. 描述本次改动、关联接口、运行方式和验证结果；界面改动可附截图。
3. 由组长或另一位成员检查；前端改动运行 `npm test`、`npm run test:ui` 和 `npm run build`（在 `frontend/` 内）。后端运行 pytest 和 Ruff；涉及数据库、导入与统计时同时运行隔离数据库集成测试，具体命令见 `backend/README.md`。
4. 修复反馈后合并，优先使用 Squash and merge，保留清楚的功能记录。
5. 组员再更新本地 `main`，下一项任务从最新 `main` 创建分支。

建议组长在 GitHub 中给 `main` 配置分支保护或规则集，要求通过 PR 合并及至少一位成员审核。该设置尚未自动配置；配置时保留组长处理初始化与紧急修复的方式。

## 同步正在开发的分支

当别人的代码已合并到 `main`，在自己的任务分支且已提交本地修改后执行：

```sh
git fetch origin
git merge origin/main
```

出现冲突时，根据双方修改处理冲突标记，再测试、提交并推送。不能直接用一方的整个文件覆盖另一方。多人同时编辑 `frontend/src/App.vue` 前先协调；后续将页面拆分为独立组件可减少冲突。

## 前后端联调约定

- 先在 `docs/` 确认接口方法、路径、参数、响应示例、错误格式及分页方式，再各自实现。
- API 计划统一前缀为 `/api/v1`；当前前端还没有调用这些接口。
- 用户 ID、商品 ID、类目 ID 使用字符串；时间格式和展示时区应统一。
- 统计结果标明数据版本，模型产物同时保存 ID 映射、训练配置与数据版本。
- 训练算法和参数由开发团队固定，推荐页面不让操作人员选择超参数。
- 修改现有接口时，在 PR 中说明影响并同步调用方，避免同名字段含义发生变化。
- 当前接口协议见 [接口文档](docs/接口文档.md) 与 [OpenAPI JSON](docs/openapi.json)，数据和模型交付见 [交付约定](docs/数据与算法交付约定.md)。修改接口后同步更新 JSON 与文档。
- 仓库提供 GitHub Actions，检查前端测试、构建、后端检查及 MySQL + ClickHouse 集成；工作流中的固定口令仅用于隔离 CI 服务，不是开发或生产密码。

## 各模块交付内容

| 目录 | 交付内容 |
|---|---|
| `frontend/` | 页面、图表、API 调用、交互与前端检查 |
| `backend/` | API、数据库结构、数据查询、模型加载与服务启动说明 |
| `recommendation/` | 清洗适配、训练评估、配置、模型导出与可复现实验说明 |
| `docs/` | 需求、接口协议、数据字典、实验记录和部署说明 |

模块负责人可按实际分工调整。推荐代码和后端需要共同约定模型文件格式及 ID 映射，不能只交一个无法解释或加载的权重文件。

## 不提交的文件

实际数据集、训练权重、数据库备份、密码和 Token、`.env`、依赖目录、生成的 `dist/`、本地缓存及 Office 临时锁文件均不提交。公开数据来源与许可要求写入说明；必要的小型测试样本应经过检查后另行约定存放位置。
