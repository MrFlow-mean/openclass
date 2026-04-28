# 开放课堂（OpenClass）

把 AI 聊天、富文本讲义编辑、版本历史、资料库和实时语音讲解放在同一个课程空间里。

- 左侧聊天确认学习目标和上下文。
- 右侧类 Word 富文本讲义，支持手动编辑、AI 局部改写、DOCX 导入导出。
- 每节课有 commit / branch / restore，可以试不同讲法再回退。
- 课程包支持多 lesson、标签页工作区、课程图谱、资料引用。
- 文本模型：OpenAI / Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义 OpenAI 兼容 / 自定义 Anthropic 兼容。
- 实时语音：OpenAI Realtime、Google Gemini Live。

## 本地运行

需要 Node.js 20+ 和 Python 3.13+。

```bash
npm run setup            # 首次安装：npm install + .venv + editable 装后端
cp .env.example .env     # 配置至少一个 provider
npm run dev              # 同时启动前后端
```

- 前端 http://localhost:3000，后端 http://localhost:8000（健康检查 `/health`）。
- SQLite 主库：`apps/api/data/openclass.sqlite3`。首次启动会从旧 `apps/api/data/store.json` 导入并归档旧文件；线上部署可用 `OPENCLASS_DATABASE_PATH`、`OPENCLASS_UPLOAD_DIR`、`OPENCLASS_EXPORT_DIR` 指到持久化目录。
- AI 调用日志：`apps/api/data/logs/ai-usage.jsonl`。
- 也可以双击 `start-ai-board.command`，它会用后台守护进程启动前后端并打开 `launcher/personal-home.html`。日常长时间使用优先用这个入口。

## 模型配置

最小配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.bupt8.com/v1
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.4-mini  # 可在前端选择 GPT-5.5
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=google
```

OpenAI/GPT 文本与 GPT Image 2 默认走 `https://api.bupt8.com/v1` 这个 OpenAI-compatible gateway；其他 provider（Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义兼容网关）和默认模型见 `.env.example`。

前端"选择模型"调 `/api/ai-models`，未配置 key 的 provider 会标为未配置；没有可用文本模型时后端回退到本地启发式逻辑，方便离线开发。

## 协作

工程与 AI 协作约定见 `AGENTS.md`（根）和 `apps/web/AGENTS.md`（前端）。提交前跑 `npm run verify`。
