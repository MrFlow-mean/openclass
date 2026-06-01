# HTTP Routers

FastAPI 路由层：解析 HTTP、鉴权依赖、调用 service，**不写业务 SQL**。

## 模块一览

| 文件 | 前缀/路径 | 职责 |
|------|-----------|------|
| [`auth.py`](auth.py) | `/api/auth/*`、`/api/admin/*` | 注册、登录、游客、OAuth、管理员 |
| [`workspace.py`](workspace.py) | `/api/workspace`、`/api/packages/*`、`/api/lessons/*` | 课程包、lesson、标签页、生成 |
| [`documents.py`](documents.py) | `/api/lessons/{id}/document/*`、`/api/documents/*` | 保存、分支、restore、DOCX、搜索 |
| [`chat.py`](chat.py) | `/api/lessons/{id}/chat` | 同步 Chat 与 SSE stream |
| [`collaboration.py`](collaboration.py) | `/api/packages/{id}/publish`、`/api/open-courses/*` | 发布、fork、贡献、维护者 |
| [`resources.py`](resources.py) | `/api/resources/*` | 资料删除、页预览（上传 API 已移除） |
| [`realtime.py`](realtime.py) | `/api/realtime/*` | WebRTC / Gemini Live（默认关闭） |

## 鉴权依赖

- `current_user`：必须登录（含游客）
- `current_admin`：管理员
- `optional_current_user`：可选登录（如发现页详情）

定义在 [`auth.py`](auth.py)，底层走 [`../services/auth_service.py`](../services/auth_service.py)。

## 注意

- 课程包 / lesson / 文档 / 历史写入必须在 service 事务内完成
- 返回前端前剥离资料原文与本地路径（由 service / store 负责）
- 新增接口归入上表之一，不要在 router 堆业务分支
