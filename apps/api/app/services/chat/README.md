# Chat 模块

课程 Chatbot：同步回复、SSE 流、学习需求澄清、文档编辑意图。实现文件在上级目录。

## 核心文件

| 文件 | 职责 |
|------|------|
| [`../chatbot.py`](../chatbot.py) | 对外入口，导出 `process_chat_on_lesson` |
| [`../chatbot_flow.py`](../chatbot_flow.py) | 主流程：`interaction_mode` 分支、requirement 更新、stream 事件 |
| [`../chatbot_handlers.py`](../chatbot_handlers.py) | 资料导入、board 生成等 intent handler |
| [`../chatbot_support.py`](../chatbot_support.py) | 共享 helper、回复组装 |
| [`../chatbot_patterns.py`](../chatbot_patterns.py) | 通用模式匹配（无学科硬编码） |
| [`../chat_service.py`](../chat_service.py) | router 调用的薄封装 |
| [`../openai_course_ai.py`](../openai_course_ai.py) | 模型调用、prompt、schema 解析 |
| [`../learning_requirement_manager.py`](../learning_requirement_manager.py) | 学习需求 sheet 更新 |

## HTTP 入口

[`../../routers/chat.py`](../../routers/chat.py) — `POST /api/lessons/{id}/chat`、`/chat/stream`

## 注意

- 不要在 router 或 handler 里写学科/教材分支；意图用通用 enum（如 `interaction_mode`）
- Chat 产生的 commit metadata `kind` 见 [`../../constants.py`](../../constants.py)（若已提取）
- 深度逻辑测试主要在 [`../../../tests/test_ai_logging.py`](../../../tests/test_ai_logging.py)
