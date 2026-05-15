<a id="中文"></a>

<p align="center">
  <a href="#中文"><img alt="中文" src="https://img.shields.io/badge/语言-中文-blue?style=for-the-badge"></a>
  <a href="#english"><img alt="English" src="https://img.shields.io/badge/Language-English-green?style=for-the-badge"></a>
</p>

# 开放课堂（OpenClass）

开放课堂（OpenClass）是一个面向课程设计、讲义创作和资料管理的课程工作台。当前代码保留前端工作台、富文本讲义编辑、资料库、版本历史、课程图谱和持久化后端；旧的后端 AI 工作流程运行框架已经移除，新的产品工作架构等待重新接入。

## 产品能力

- 前端课程工作台：围绕课程包、lesson、资料和文档编辑提供统一操作界面。
- 富文本讲义编辑：右侧类 Word 编辑器支持手动编辑、DOCX 导入导出。
- 资料库与引用：上传课程资料，抽取章节结构，作为后续文档整理和新架构接入的资料基础。
- 课程包管理：一个课程包可以包含多节 lesson，适合按主题、章节或教学单元组织内容。
- 版本与分支：每节课支持 commit / branch / restore，可以尝试不同讲法再安全回退。
- 课程图谱：用结构化关系串联 lesson、知识点和课程路径。
- 模型配置入口：保留文本模型配置与健康检查，供后续新架构复用。

## 产品 Workflow

1. 创建课程包：为一门课、一个专题或一次培训建立独立课程空间。
2. 添加 lesson：按章节、知识点或教学任务拆分课程内容。
3. 上传资料：导入讲义、参考文档、案例材料或课堂素材，系统记录 metadata 并抽取结构。
4. 整理资料：通过资料库保存上传文件 metadata、抽取结果和章节入口。
5. 手动打磨：在富文本编辑器中直接调整标题、段落、重点、示例和课堂活动。
6. 保存版本：对稳定结果创建 commit；需要探索新讲法时创建 branch，满意后再保留或回退。
7. 组织课程路径：通过课程包、标签页和课程图谱把多个 lesson 串成完整教学流程。
8. 导入导出：用 DOCX 导入导出进入线下备课、分享和归档流程。

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
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.4-mini  # 可在前端选择 GPT-5.5
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
```

OpenAI/GPT 文本和 GPT Image 2 默认走官方 OpenAI API：`https://api.openai.com/v1`。其他 provider（Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义兼容网关）和默认模型见 `.env.example`。

前端"选择模型"调 `/api/ai-models`，未配置 key 的 provider 会标为未配置。旧聊天、文档 AI 改写和实时语音接口当前会返回已移除状态，等待新的产品工作架构接入。

## 协作

工程与 AI 协作约定见 `AGENTS.md`（根）和 `apps/web/AGENTS.md`（前端）。提交前跑 `npm run verify`。

---

<a id="english"></a>

<p align="center">
  <a href="#中文"><img alt="中文" src="https://img.shields.io/badge/语言-中文-blue?style=for-the-badge"></a>
  <a href="#english"><img alt="English" src="https://img.shields.io/badge/Language-English-green?style=for-the-badge"></a>
</p>

# OpenClass

OpenClass is a course workspace for curriculum design, lesson authoring, and resource management. The current code keeps the frontend workbench, rich-text lesson editing, resource library, version history, course graph, and persistence backend; the former backend AI workflow runtime has been removed and is waiting for a new product architecture to be connected.

## Product Capabilities

- Frontend course workbench: manage course packages, lessons, resources, and documents in one interface.
- Rich-text lesson editor: edit lesson handouts in a Word-like editor with manual editing and DOCX import/export.
- Resource library: upload course materials and extract chapter outlines as the source foundation for document work and the future architecture.
- Course package management: organize multiple lessons inside one course package by topic, chapter, or teaching unit.
- Versioning and branching: use commit / branch / restore for each lesson to explore different explanations and safely roll back.
- Course graph: connect lessons, concepts, and teaching paths with structured relationships.
- Model configuration entry points: keep text model configuration and health checks for the future architecture.

## Product Workflow

1. Create a course package for a course, topic, or training program.
2. Add lessons and split the course by chapter, concept, or teaching task.
3. Upload materials such as handouts, references, case studies, and classroom assets.
4. Organize resources through metadata, extracted text, and chapter entry points.
5. Refine the lesson manually in the rich-text editor by adjusting headings, examples, activities, and key points.
6. Save versions with commits; create branches when exploring alternate drafts, then keep or restore as needed.
7. Organize the course path with packages, workspace tabs, and the course graph.
8. Import or export DOCX for preparation, sharing, and archiving.

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
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.4-mini
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
```

OpenAI/GPT text and GPT Image 2 default to the official OpenAI API: `https://api.openai.com/v1`. Other providers and default models are documented in `.env.example`.

The frontend model picker reads `/api/ai-models`. Providers without configured keys are shown as unavailable. The old chat, document AI editing, and realtime voice endpoints currently report that the workflow runtime has been removed until the new product architecture is connected.

## Collaboration

Engineering and AI collaboration rules are documented in `AGENTS.md` and `apps/web/AGENTS.md`. Run `npm run verify` before submitting changes.
