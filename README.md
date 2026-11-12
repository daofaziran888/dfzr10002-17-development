# 数据分析工作台

面向企业数据分析师与数据产品经理的一站式数据分析工作台。提供指标口径管理、聚合查询、分析实验设计、数据服务发布与应用集成的完整链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
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
| 指标口径管理 | `indicator` | 业务指标 | 指标编号、指标名称、计算公式 |
| 分析数据源 | `datasource` | 数据源连接 | 连接编号、连接名称、数据库类型 |
| 聚合查询配置 | `query` | 查询模板 | 模板编号、模板名称、查询语句 |
| 分析实验管理 | `experiment` | 分析实验 | 实验编号、实验名称、分析数据集 |
| 分析数据集 | `dataset` | 数据集 | 数据集编号、数据集名称、来源表名 |
| 可视化图表配置 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 分析报告管理 | `report` | 分析报告 | 报告编号、报告标题、关联数据集 |
| 数据服务发布 | `service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 数据看板 | `dashboard` | 看板面板 | 面板编号、面板名称、布局配置 |
| 分析维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度表名 |
| 全局筛选器 | `filter` | 筛选器 | 筛选器编号、筛选器名称、关联字段 |
| 定时分析任务 | `schedule` | 定时任务 | 任务编号、任务名称、分析类型 |
| 数据导出配置 | `export` | 导出模板 | 模板编号、模板名称、数据源绑定 |
| 查询缓存管理 | `cache` | 缓存规则 | 规则编号、缓存目标、缓存时间 |
| 分析模型管理 | `model` | 分析模型 | 模型编号、模型名称、模型类型 |
| 分析操作日志 | `log` | 操作日志 | 日志编号、操作类型、操作对象 |

## 看板权限编排

数据看板在同一面板内按团队控制可见范围，授权与访问判定集中在
`backend/app/services/dashboard_access.py`，接口在
`backend/app/routers/dashboard_access.py`：

| 接口 | 用途 |
| --- | --- |
| `GET /api/dashboard/{id}/permissions` | 授权列表、生效范围、继承链、图表可见状态 |
| `POST /api/dashboard/{id}/permissions` | 发布授权：团队、可见范围、允许维度/图表、继承来源 |
| `POST /api/dashboard/{id}/permissions/{授权编号}/actions` | 审批通过、驳回、收紧范围、撤销授权 |
| `POST /api/dashboard/{id}/access/evaluate` | 判定成员可见维度与参与刷新的图表，并写入访问历史 |
| `GET /api/dashboard/{id}/charts/visibility` | 图表配置清单的可见状态 |
| `GET/POST /api/dashboard/{id}/shares` | 只读分享链接；写操作按 403 拒绝 |
| `GET /api/dashboard/{id}/access-history` | 历史访问（保留访问当时授权快照） |
| `GET /api/dashboard/{id}/approvals` | 审批台账（团队、继承来源、审批人、变更前后维度） |

冲突裁决按**最严优先**：同团队多条生效授权取交集；继承来源只能缩小范围；
只读来源（本部门只读、跨部门只读、只读角色、分享链接）优先；分享链接范围 =
创建快照 ∩ 当前授权，永不因新授权扩容；继承来源撤销后下游授权与图表可见状态
同步失效，历史访问快照不回写。

示例面板 `DASH-0004` 同时含财务图表 `CHAR-0004A` 与运营图表 `CHAR-0004B`：
财务部成员只能看到销售额与地区维度、只刷新财务图表；运营部成员只能看到渠道
维度、只刷新运营图表。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.tsx`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
