# Services（业务逻辑）

所有持久化、AI 调用、事务与领域规则在此层实现。Router 只转发。

## 按域分组

| 域 | 核心文件 | 说明 |
|----|----------|------|
| **Auth** | `auth_service.py`、`auth_store.py`、`email_delivery.py` | 详见 [`auth/README.md`](auth/README.md) |
| **Workspace** | `workspace_state.py`、`course_store.py`、`course_runtime.py`、`lesson_factory.py`、`history.py` | SQLite 课程包、commit/branch、用户隔离 |
| **Chat** | `chatbot*.py`、`chat_service.py`、`openai_course_ai.py`、`learning_requirement_manager.py` | 详见 [`chat/README.md`](chat/README.md) |
| **Collaboration** | `collaboration.py` | 详见 [`collaboration/README.md`](collaboration/README.md) |
| **Documents** | `rich_document.py`、`document_*`、`renderer.py`、`segment_resolver.py` | 富文本、DOCX、渲染 |
| **Resources** | `resource_*.py`（12 个模块） | 详见 [`resources/README.md`](resources/README.md) |
| **Realtime** | `openai_realtime.py`、`realtime_tool_bridge.py` | 语音实时（需 env 显式开启） |
| **Infra** | `config.py`、`ai_logging.py`、`ai_model_catalog.py`、`route_context.py` | 配置、用量日志、模型目录 |

## 大文件警告（>800 行）

修改前先读分区注释；避免继续堆逻辑：

| 文件 | 行数级 | 域 |
|------|--------|-----|
| `resource_library.py` | ~2400 | 资料解析 |
| `rich_document.py` | ~2100 | 文档模型 |
| `openai_course_ai.py` | ~1900 | 模型 prompt |
| `chatbot_flow.py` | ~1300 | Chat 编排 |
| `auth_service.py` | ~1200 | 认证 |
| `course_store.py` | ~1100 | SQLite |
| `collaboration.py` | ~1100 | 协作 |

## OpenClass 宪法

核心 service **不得**写入学科关键词、教材名、固定讲义或 demo 分支。详见根 [`AGENTS.md`](../../../../AGENTS.md)。
