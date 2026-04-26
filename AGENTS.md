# OpenClass — AI 协作指南

OpenClass 是一个 AI 课程工作台。产品介绍、安装与 provider 配置见 `README.md` 和 `.env.example`。本文件只列协作时必须知道的事。

## 仓库地图

```text
.
├── apps/
│   ├── api/              # FastAPI 后端（Python 3.13）
│   │   ├── app/main.py       # 应用组装 + 健康检查
│   │   ├── app/routers/      # workspace / documents / chat / realtime / resources
│   │   ├── app/services/     # 业务逻辑、状态、AI、存储、历史
│   │   └── data/             # 本地运行数据，已 gitignore
│   └── web/              # Next.js 前端，详见 apps/web/AGENTS.md
├── launcher/             # 可双击的本地入口 HTML
├── package.json          # 根 workspace 脚本
├── pyproject.toml        # 后端依赖 + pytest（单一来源）
└── .env.example          # 环境变量示例
```

## 常用命令（从仓库根执行）

```bash
npm run setup            # 首次安装：npm install + .venv + editable 装后端
npm run dev              # 同时启动前后端（:3000 / :8000）
npm run dev:web | dev:api
npm run lint:web | typecheck:web | test:api | build:web
npm run verify           # 提交前 gate：lint + typecheck + test:api + build:web
```

后端虚拟环境固定在仓库根 `.venv/`，不要在子目录另建。

## 后端约定：router 只处理 HTTP，service 承担业务

- 新接口先归到 `workspace / documents / chat / realtime / resources` 之一。
- 状态读写走 `app/services/workspace_state.py` 的 helper，不要在 router 里直接碰数据库。
- 课程包持久化用 `SqliteCourseStore`；新增写路径优先复用 service 层事务，不要恢复 `store.json` 写入。
- 返回前端前剥离资料原文与本地路径。

## 环境与日志

- 复制 `.env.example` 为仓库根 `.env`，不要提交。
- SQLite 主库默认在 `apps/api/data/openclass.sqlite3`，线上用 `OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3` 指到持久化目录；上传/导出可用 `OPENCLASS_UPLOAD_DIR`、`OPENCLASS_EXPORT_DIR` 同步指到 `/var/lib/openclass/` 下。
- AI 输入输出日志：`apps/api/data/logs/ai-usage.jsonl`。
- 前端"选择模型"读 `/api/ai-models`，未配置 key 的 provider 会显示为未配置。

## 提交前

- 跑 `npm run verify`（或至少 `lint:web` + `typecheck:web` + 受影响的 `test:api`）。
- 不要提交 `.env`、`.venv/`、`apps/api/data/` 下的运行数据、`node_modules/`、`.next/`。

## 风格

- 注释只解释非显而易见的意图或约束，不复述代码。
- 不主动新建 README / 文档；扩充本指南或对应 README 即可。
