# 开放课堂（OpenClass）

开放课堂（OpenClass）是一个面向课程设计、讲义创作和资料管理的课程工作台。当前代码保留前端工作台、富文本讲义编辑、资料库、版本历史、课程图谱和持久化后端；旧的后端 AI 工作流程运行框架已经移除，新的产品工作架构等待重新接入。

## 产品能力

- 前端课程工作台：围绕课程包、lesson、资料和文档编辑提供统一操作界面。
- 富文本讲义编辑：右侧类 Word 编辑器支持手动编辑、DOCX 导入导出。
- 资料库与引用：课程资料可随协作 fork 复制、通过 Chat 导入或在测试/脚本中写入；资料库保存 metadata、抽取结果和章节入口，供文档整理与引用。
- 课程包管理：一个课程包可以包含多节 lesson，适合按主题、章节或教学单元组织内容。
- 版本与分支：每节课支持 commit / branch / restore，可以尝试不同讲法再安全回退。
- 课程图谱：用结构化关系串联 lesson、知识点和课程路径。
- 模型配置入口：保留文本模型配置与健康检查，供后续新架构复用。

## 产品 Workflow

1. 创建课程包：为一门课、一个专题或一次培训建立独立课程空间。
2. 添加 lesson：按章节、知识点或教学任务拆分课程内容。
3. 引入资料：通过协作 fork 复制资料、Chat 导入或 DOCX 导入把参考内容带入课程包；独立的上传 API（`POST /api/resources/upload`）已移除。
4. 整理资料：通过资料库查看 metadata、抽取结果和章节入口，并在 Chat 或编辑器中引用。
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
OPENAI_MODEL=gpt-5.5
OPENCLASS_REALTIME_ENABLED=false
OPENCLASS_REALTIME_TOOLS_ENABLED=false
OPENAI_REALTIME_MODEL=gpt-realtime-2
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
```

OpenAI/GPT 文本交互和 GPT Image 2 默认走官方 OpenAI API：`https://api.openai.com/v1`。交互 AI 文本默认用 GPT-5.5；语音交互默认用 GPT Realtime 2，但需要显式设置 `OPENCLASS_REALTIME_ENABLED=true` 才会启用后端 WebRTC 连接，`OPENCLASS_REALTIME_TOOLS_ENABLED=true` 才允许 Realtime 调用后端 Chatbot 工具。复杂问题的隐藏强推理工具默认使用 `OPENAI_STRONG_REASONING_MODEL=gpt-5.5` 和 `OPENAI_STRONG_REASONING_EFFORT=high`，只有设置 `OPENCLASS_STRONG_REASONING_ALLOW_PRO=true` 时才会使用 `OPENAI_PRO_REASONING_MODEL`。已有资料走确定性解析、页码导航和正文证据检索；新资料主要通过协作 fork、Chat 导入或 DOCX 进入课程包，不再提供独立的资料上传 HTTP 接口。其他 provider（Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义兼容网关）和默认模型见 `.env.example`。

前端"选择模型"调 `/api/ai-models`，未配置 key 的 provider 会标为未配置。当前保留并启用的是模型目录、课程聊天入口、文档保存/导入/导出和资料解析等工作台能力；Realtime 默认关闭，开启后仍作为同一个 Chatbot 的实时输入/输出形态，而不是新的教学角色。`BoardTeachingGuide` / `BoardTeachingProgress` 等教学工作流 schema 仅作为历史兼容和 future workflow 预留，不代表完整 AI 教学编排已经接回。

## 测试

```bash
npm run lint:web
npm run typecheck:web
npm run test:api
npm run build:web
npm run test:e2e          # Playwright 主流程，默认启动 127.0.0.1:3110 / 127.0.0.1:8110
npm run verify            # 文件尺寸安全线 + 前端 lint/typecheck + 后端测试 + 前端构建
```

## 协作

工程与 AI 协作约定见 `AGENTS.md`（根）和 `apps/web/AGENTS.md`（前端）。提交前跑 `npm run verify`。
