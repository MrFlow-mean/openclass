# OpenClass API（FastAPI）

Python 3.13 后端，负责课程包持久化、认证、Chat、协作与资料解析。

## 入口

- [`app/main.py`](app/main.py)：组装 FastAPI、CORS、健康检查、挂载 routers
- [`app/models.py`](app/models.py)：Pydantic 请求/响应与领域视图类型

## 分层约定

```text
HTTP 请求 → app/routers/*.py → app/services/*.py → SQLite / 文件系统
```

- Router 只做参数校验、依赖注入（`current_user` 等）、调用 service
- **禁止**在 router 里直接拼 SQL 或绕过 service 事务
- 状态读写走 [`app/services/workspace_state.py`](app/services/workspace_state.py) 与 [`app/services/course_store.py`](app/services/course_store.py)

## 目录

| 目录 | 说明 |
|------|------|
| [`app/routers/`](app/routers/README.md) | HTTP 路由（workspace / documents / chat / auth / collaboration / resources / realtime） |
| [`app/services/`](app/services/README.md) | 业务逻辑、AI 调用、存储、历史 |
| [`tests/`](tests/) | pytest；共享 fixture 见 [`tests/conftest.py`](tests/conftest.py) 的 `isolated_app` |

## 本地运行

从仓库根目录：

```bash
npm run setup:api   # 首次
npm run dev:api     # :8000
npm run test:api    # pytest
```

## 数据与持久化

- 默认 SQLite：`apps/api/data/openclass.sqlite3`（WAL）
- 线上通过 `OPENCLASS_DATABASE_PATH`、`OPENCLASS_UPLOAD_DIR`、`OPENCLASS_EXPORT_DIR` 指到持久化目录
- 详见根目录 [`AGENTS.md`](../../AGENTS.md) 与 [`.env.example`](../../.env.example)
