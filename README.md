<a id="中文"></a>

<p align="center">
  <a href="#中文"><img alt="中文" src="https://img.shields.io/badge/语言-中文-blue?style=for-the-badge"></a>
  <a href="#english"><img alt="English" src="https://img.shields.io/badge/Language-English-green?style=for-the-badge"></a>
</p>

# 开放课堂（OpenClass）

开放课堂（OpenClass）是一个面向课程设计、讲义创作和课堂讲解的 AI 课程工作台。它把 AI 对话、富文本讲义编辑、资料库、版本历史、课程图谱和实时语音讲解放进同一个工作空间，帮助老师、教研团队和知识创作者更快地把零散资料整理成可讲、可改、可复用的课程内容。

## 产品能力

- AI 课程共创：通过左侧对话确认学习目标、学生背景、课程风格和讲解重点。
- 富文本讲义编辑：右侧类 Word 编辑器支持手动编辑、AI 局部改写、DOCX 导入导出。
- 资料库与引用：上传课程资料，抽取章节结构，并在生成讲义时引用上下文。
- 课程包管理：一个课程包可以包含多节 lesson，适合按主题、章节或教学单元组织内容。
- 版本与分支：每节课支持 commit / branch / restore，可以尝试不同讲法再安全回退。
- 课程图谱：用结构化关系串联 lesson、知识点和课程路径。
- 实时语音讲解：支持 OpenAI Realtime 和 Google Gemini Live，让课堂内容从文档延展到讲解。
- 多模型接入：支持 OpenAI、Anthropic、Google、DeepSeek、Kimi、MiniMax、自定义 OpenAI 兼容和自定义 Anthropic 兼容 provider。

## 产品 Workflow

1. 创建课程包：为一门课、一个专题或一次培训建立独立课程空间。
2. 添加 lesson：按章节、知识点或教学任务拆分课程内容。
3. 上传资料：导入讲义、参考文档、案例材料或课堂素材，系统记录 metadata 并抽取结构。
4. 设定学习需求：在聊天区说明学生水平、教学目标、课时长度和希望采用的讲法。
5. AI 生成与改写：让 AI 基于资料和目标生成讲义，也可以选中局部内容进行重写、扩写或压缩。
6. 手动打磨：在富文本编辑器中直接调整标题、段落、重点、示例和课堂活动。
7. 保存版本：对稳定结果创建 commit；需要探索新讲法时创建 branch，满意后再保留或回退。
8. 组织课程路径：通过课程包、标签页和课程图谱把多个 lesson 串成完整教学流程。
9. 讲解与导出：使用实时语音辅助讲解，或导出 DOCX 进入线下备课、分享和归档流程。

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
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.4-mini  # 可在前端选择 GPT-5.5
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
```

OpenAI/GPT 文本、GPT Image 2 和 OpenAI Realtime 默认走官方 OpenAI API：`https://api.openai.com/v1`。其他 provider（Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义兼容网关）和默认模型见 `.env.example`。

前端"选择模型"调 `/api/ai-models`，未配置 key 的 provider 会标为未配置；没有可用文本模型时后端回退到本地启发式逻辑，方便离线开发。

## 协作

工程与 AI 协作约定见 `AGENTS.md`（根）和 `apps/web/AGENTS.md`（前端）。提交前跑 `npm run verify`。

---

<a id="english"></a>

<p align="center">
  <a href="#中文"><img alt="中文" src="https://img.shields.io/badge/语言-中文-blue?style=for-the-badge"></a>
  <a href="#english"><img alt="English" src="https://img.shields.io/badge/Language-English-green?style=for-the-badge"></a>
</p>

# OpenClass

OpenClass is an AI course workspace for curriculum design, lesson authoring, and live teaching preparation. It brings AI chat, rich-text lesson editing, a resource library, version history, course graphs, and realtime voice explanation into one workspace so teachers, curriculum teams, and knowledge creators can turn scattered materials into teachable, editable, and reusable course content.

## Product Capabilities

- AI-assisted course planning: use the chat panel to clarify learning goals, learner background, teaching style, and lesson context.
- Rich-text lesson editor: edit lesson handouts in a Word-like editor with manual editing, AI partial rewrites, and DOCX import/export.
- Resource library: upload course materials, extract chapter outlines, and reference source context while creating lessons.
- Course package management: organize multiple lessons inside one course package by topic, chapter, or teaching unit.
- Versioning and branching: use commit / branch / restore for each lesson to explore different explanations and safely roll back.
- Course graph: connect lessons, concepts, and teaching paths with structured relationships.
- Realtime voice teaching: extend written lesson content into live explanation with OpenAI Realtime or Google Gemini Live.
- Multi-provider AI support: OpenAI, Anthropic, Google, DeepSeek, Kimi, MiniMax, custom OpenAI-compatible services, and custom Anthropic-compatible services.

## Product Workflow

1. Create a course package for a course, topic, or training program.
2. Add lessons and split the course by chapter, concept, or teaching task.
3. Upload materials such as handouts, references, case studies, and classroom assets.
4. Define learning requirements in chat, including learner level, goals, duration, and preferred teaching style.
5. Generate and rewrite lesson content with AI using the uploaded resources and course goals.
6. Refine the lesson manually in the rich-text editor by adjusting headings, examples, activities, and key points.
7. Save versions with commits; create branches when exploring alternate explanations, then keep or restore as needed.
8. Organize the course path with packages, workspace tabs, and the course graph.
9. Teach or export: use realtime voice support for explanation, or export DOCX for preparation, sharing, and archiving.

## Local Development

Requires Node.js 20+ and Python 3.13+.

```bash
npm run setup            # First-time setup: npm install + .venv + editable backend install
cp .env.example .env     # Configure at least one AI provider
npm run dev              # Start frontend and backend together
```

- Frontend: http://localhost:3000. Backend: http://localhost:8000. Health check: `/health`.
- SQLite database: `apps/api/data/openclass.sqlite3`. On first startup, legacy `apps/api/data/store.json` is imported and archived. In production, set `OPENCLASS_DATABASE_PATH`, `OPENCLASS_UPLOAD_DIR`, and `OPENCLASS_EXPORT_DIR` to persistent directories.
- AI usage log: `apps/api/data/logs/ai-usage.jsonl`.
- You can also double-click `start-ai-board.command`, which starts the frontend and backend through background daemons and opens `launcher/personal-home.html`. This is the preferred entry point for long local sessions.

## Model Configuration

Minimal configuration:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.4-mini
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
```

OpenAI/GPT text, GPT Image 2, and OpenAI Realtime default to the official OpenAI API: `https://api.openai.com/v1`. Other providers and default models are documented in `.env.example`.

The frontend model picker reads `/api/ai-models`. Providers without configured keys are shown as unavailable. If no text model is available, the backend falls back to local heuristic behavior for offline development.

## Collaboration

Engineering and AI collaboration rules are documented in `AGENTS.md` and `apps/web/AGENTS.md`. Run `npm run verify` before submitting changes.
