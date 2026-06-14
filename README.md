# 开放课堂（OpenClass）

<p align="center">
  <img src="docs/assets/openclass-product-cover.png" alt="OpenClass 产品封面" width="280" />
</p>

OpenClass 是面向课程设计、讲义创作和资料管理的 AI 课程工作台。当前保留工作台 UI、富文本讲义编辑、资料库、版本历史、课程图谱、模型配置入口和持久化后端；AI 工作流按 `AGENTS.md` 的通用能力约束继续接入。

## 核心能力

- 课程包与 lesson 管理：按主题、章节或教学单元组织内容。
- 富文本讲义编辑：右侧类 Word 编辑器支持手动编辑、DOCX 导入导出。
- 资料库：上传课程资料，保存 metadata 并抽取章节结构。
- 版本与分支：lesson 支持 commit / branch / restore。
- 课程图谱：用结构化关系串联 lesson 和课程路径。
- 模型配置：通过 provider catalog 和健康检查展示可用模型。

## AI 架构方向

OpenClass 默认保持通用，不把学科、教材、考试或 demo 写进核心路径。空白板书先走 `InitialLearningIntentGate`，判断用户是在学习知识内容、做练习型教学，还是目标仍过宽；已有板书则走 `BoardTaskRequirementSheet`、目标定位、route decision 和可追溯执行。

详细约束见 `AGENTS.md`；空白板书第一层契约见 `docs/initial-learning-intent-contract.md`。

## 本地运行

需要 Node.js 20+ 和 Python 3.13+。

```bash
npm run setup
cp .env.example .env
npm run dev
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000，健康检查 `/health`
- SQLite：`apps/api/data/openclass.sqlite3`
- AI 调用日志：`apps/api/data/logs/ai-usage.jsonl`
- 本地桌面入口：双击 `start-ai-board.command` 启动守护进程并打开 `launcher/personal-home.html`

## 配置

最小配置见 `.env.example`。常用项：

```bash
AI_TEXT_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_COMPAT_API=chat_completions
OPENAI_MODEL=gpt-5.5
OPENAI_CATALOG_MODEL=gpt-5.4-mini
OPENCLASS_REALTIME_ENABLED=false
OPENCLASS_REALTIME_TOOLS_ENABLED=false
```

文本模型、目录模型、Realtime、强推理、图像模型和其他 provider 都从 `.env.example` 扩展配置。Realtime 默认关闭；开启后仍是同一个 Chatbot 的实时形态，不是新的教学角色。

## 测试

```bash
npm run lint:web
npm run typecheck:web
npm run test:api
npm run build:web
npm run test:e2e
npm run verify
```

`npm run verify` 是提交前 gate：文件尺寸检查、前端 lint/typecheck、后端测试和前端构建。

## 协作

工程与 AI 协作约定见根 `AGENTS.md`、`apps/web/AGENTS.md` 和 `apps/api/app/services/AGENTS.md`。提交前不要包含 `.env`、`.venv/`、`apps/api/data/`、`node_modules/`、`.next/`。
