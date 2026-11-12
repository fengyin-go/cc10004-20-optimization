# 数据洞察分析工作台

面向多源数据接入、交互式查询分析、可视化看板搭建、分析报告生成与数据服务编排的数据洞察分析工作台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 交互查询 | `query` | 查询语句 | 语句编号、查询表达式、目标数据集 |
| 可视化图表 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 数据源接入 | `source` | 接入配置 | 配置编号、连接类型、接入地址 |
| 聚合计算 | `aggregate` | 聚合方案 | 方案编号、聚合函数、目标字段 |
| 数据筛选 | `filter` | 筛选规则 | 规则编号、目标数据集、筛选字段 |
| 报告生成 | `reportgen` | 报告模板 | 模板编号、模板名称、包含图表 |
| 异常检测 | `anomaly` | 检测规则 | 规则编号、监控指标、基线均值 |
| 多维下钻 | `drill` | 下钻路径 | 路径编号、起点维度、下钻层数 |
| 对比分析 | `compare` | 对比方案 | 方案编号、基准数据集、对照数据集 |
| 分析流水线 | `pipeline` | 流水线节点 | 节点编号、节点类型、输入源 |
| 数据集管理 | `datastore` | 数据集定义 | 数据集编号、数据集名称、来源表名 |
| 趋势预测 | `forecast` | 预测模型 | 模型编号、模型类型、训练数据集 |
| 数据分片 | `segment` | 分片规则 | 规则编号、目标数据集、分片字段 |
| 调度管理 | `scheduler` | 调度配置 | 配置编号、关联任务、调度表达式 |
| 通知分发 | `notify` | 通知规则 | 规则编号、触发条件、通知渠道 |
| 查询缓存 | `cache` | 缓存策略 | 策略编号、目标查询、缓存键规则 |
| 数据标签 | `tag` | 标签定义 | 标签编号、标签名称、计算表达式 |
| 数据分享 | `share` | 分享链接 | 链接编号、目标数据集、访问密码 |

## 字段影响反查（数据集管理 → 数据筛选）

数据集管理列表每行的「字段引用」打开字段面板，可以查看某个字段被哪些启用规则引用：

- 直接使用：数据筛选的筛选字段/排序字段、聚合计算的目标字段、数据分片的分片字段；
- 间接依赖：组合表达式/筛选条件里以完整标识符出现的字段（`amount` 不会命中 `amount_total`）；
- 同一规则无论表达式有几个分支都只出现一份，关系以“直接使用优先于间接依赖”；
- 字段身份以 `field_id` 为准，改名后旧名称保留为别名，旧规则按名称仍可定位；
  同一数据集里名称命中多个字段时不猜测，进入「待确认」。

接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/datastore/{id}/fields` | 字段定义、别名与字段版本 |
| `PATCH /api/datastore/{id}/fields/{field_id}` | 字段改名（field_id 不变） |
| `GET /api/datastore/{id}/field-references?field_id=&name=&snapshot_id=&cursor=&size=` | 反查（分页片段，需带首次返回的 snapshot_id） |
| `POST /api/datastore/{id}/field-references/consume` | 用 `selection_token` 进入交互查询，消费前重新校验规则启用状态与版本 |
| `PATCH /api/filter/{id}`、`/api/aggregate/{id}`、`/api/segment/{id}` | 修订规则，版本递增并使旧反查快照失效 |

反查一致性：

- 响应里的 `snapshot` 固定了本次反查采用的规则版本（`rule_versions`）；
- 分页期间规则被修订/启停、字段定义变化，续页返回 409 并给出当前快照，前端必须刷新，不做新旧拼接；
- 每次点选签发一次性 `selection_token`；消费时校验规则仍为启用状态且版本未变，
  已停用或已修订的选择返回 `rule_disabled` / `selection_stale`，不会登记交互查询；
  字段改名不改变 field_id，选择仍有效，登记的新查询会记录来源规则版本与字段版本。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
