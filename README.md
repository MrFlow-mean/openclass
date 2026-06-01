# 开放课堂（OpenClass）

开放课堂（OpenClass）是一个围绕自然学习方式设计的 AI 课程工作台。它把 AI 学习从单纯的聊天框，推进到一个更像真实上课的环境：左侧是一块可以编辑、保存、导出的黑板，用来沉淀核心知识；右侧是可以持续互动的 AI 对话区，用来讲解、追问、纠错、改写和回答问题。

OpenClass 关注的不只是“回答得好不好”，而是一次学习能不能留下来。系统希望把用户的学习目标、对话过程、黑板文档、资料引用和版本历史沉淀成可以回看、复用、分支和继续演化的课程资产。

## 核心理念

OpenClass 把学习过程拆成两个互相配合的空间：

- BlackBoard：保存真正需要留下来的知识，承载结构化讲义、重点内容、推导过程、练习材料和课程文档。
- Dialogue：承载实时交流，负责讲解、追问、纠错、澄清目标、解释改动和触发文档操作。
- Course Asset：把一次学习过程沉淀为 lesson、课程包、资料库、版本历史和可发布的开放课程。

这对应的是最自然的学习场景：一位老师，一块黑板，一段围绕目标展开的教学过程。老师负责讲，黑板负责留下重点；用户可以听、问、打断、编辑，也可以和 AI 一起完成一份结构化的学习内容。

## 当前能力

- AI 课堂界面：前端工作台围绕课程包、lesson、资料、黑板文档和 AI 对话组织成统一操作界面。
- 目标澄清：AI 可以根据用户输入整理学习需求，在目标、边界、输出形态和资料范围不清楚时继续追问或给出下一步建议。
- 黑板文档：类 Word 富文本编辑器支持手动编辑、格式化、高亮、选区上下文、自动保存、DOCX 导入和 DOCX 导出。
- AI 文档操作：对话可以触发黑板生成、选区改写、整篇重写、追加段落和基于资料的内容整理，Chatbot 负责解释过程，黑板负责承载正文。
- 资料库与引用：支持上传课程资料，抽取章节结构和文本片段，并作为后续讲解、检索、引用和文档生成的上下文。
- 版本与分支：每节 lesson 支持 commit、branch、checkout、restore 和 merge preview，可以探索不同讲法或文档版本，再安全回退或合并。
- 课程包与课程图谱：课程包可以组织多节 lesson，课程图谱用于表达 lesson、知识点和课程路径之间的结构关系。
- 开放课程协作：支持发布课程包、浏览开放课程、fork 课程、提交改进、维护者审核、合并贡献和维护者管理。
- 模型目录：前端模型选择来自 `/api/ai-models`，未配置 key 的 provider 会显示为不可用；文本、资料目录、强推理、图像和实时模型可以分开配置。
- 持久化后端：FastAPI + SQLite 保存课程包、lesson、文档、资料、历史、账号和开放课程协作数据。

## 典型流程

1. 创建课程包：为一门课、一个专题、一次培训或一段个人学习建立独立空间。
2. 添加 lesson：按章节、目标、任务或学习阶段拆分内容。
3. 明确目标：通过对话把模糊需求整理成可执行的学习目标、资料范围和输出形态。
4. 上传资料：导入参考文档、讲义、案例材料或课堂素材，系统记录 metadata 并抽取可检索结构。
5. 生成与打磨黑板：AI 生成或改写结构化内容，用户也可以直接编辑、复制、粘贴、高亮和整理。
6. 保存版本：对阶段性结果创建 commit；需要尝试另一种讲法时创建 branch，满意后再合并或回退。
7. 组织课程路径：用课程包、标签页和课程图谱把多个 lesson 串成完整学习路径。
8. 导入导出：通过 DOCX 导入导出进入线下备课、分享、归档或二次编辑流程。
9. 发布协作：把课程包发布为开放课程，其他用户可以 fork、修改并向维护者提交改进。

## 当前边界

- OpenClass 是通用 AI 课程工作台，不内置学科模板、教材分支、固定讲义或 demo 课程内容。
- Realtime 语音后端默认关闭，只有设置 `OPENCLASS_REALTIME_ENABLED=true` 后才会暴露实时连接能力；实时输入仍属于同一个 Chatbot 的交互形态。
- 黑板当前支持 DOCX 导入导出；PDF 导出不是当前 README 声明的已完成能力。
- “重点笔记本”、学习时间点回放、苏格拉底模式、费曼学习法和课程商业化属于产品方向或 Roadmap，不应被理解为当前默认完整功能。
- `BoardTeachingGuide` / `BoardTeachingProgress` 等 schema 主要用于兼容和 future workflow 预留，不代表固定教学模板系统。

## Roadmap

- 更强的学习记录回放：回到某一次学习过程中的特定黑板、目标、对话和资料状态继续学习。
- 重点内容沉淀：把高亮、选区、关键概念、易错点和复习材料组织成长期可复习的知识资产。
- 更多教学互动形态：通过 prompt 和通用交互协议支持启发式提问、用户讲解、即时纠错和练习闭环，而不是在核心代码里写学科模板。
- 课程工坊完善：围绕开放课程继续增强发布、fork、贡献审核、维护者协作、版本演化和发现机制。
- 更完整的导入导出：在保持黑板文档结构化的前提下，扩展更多归档、分享和迁移格式。

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
OPENAI_CATALOG_MODEL=gpt-5.4-mini
OPENCLASS_REALTIME_ENABLED=false
OPENCLASS_REALTIME_TOOLS_ENABLED=false
OPENAI_REALTIME_MODEL=gpt-realtime-2
OPENAI_IMAGE_MODEL=gpt-image-2
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
```

OpenAI/GPT 文本交互和 GPT Image 2 默认走官方 OpenAI API：`https://api.openai.com/v1`。交互 AI 文本默认用 GPT-5.5；上传资料的目录 AI 通过 `OPENAI_CATALOG_MODEL` 独立配置，默认用 GPT-5.4 mini。复杂问题的隐藏强推理工具默认使用 `OPENAI_STRONG_REASONING_MODEL=gpt-5.5` 和 `OPENAI_STRONG_REASONING_EFFORT=high`，只有设置 `OPENCLASS_STRONG_REASONING_ALLOW_PRO=true` 时才会使用 `OPENAI_PRO_REASONING_MODEL`。语音交互默认模型是 GPT Realtime 2，但需要显式设置 `OPENCLASS_REALTIME_ENABLED=true` 才会启用后端 WebRTC 连接，`OPENCLASS_REALTIME_TOOLS_ENABLED=true` 才允许 Realtime 调用后端 Chatbot 工具。

其他 provider（Anthropic / Google / DeepSeek / Kimi / MiniMax / 自定义兼容网关）和默认模型见 `.env.example`。前端“选择模型”读取 `/api/ai-models`，未配置 key 的 provider 会标为未配置。

## 仓库结构

```text
.
├── apps/
│   ├── api/          # FastAPI 后端：workspace、documents、chat、realtime、resources、collaboration
│   └── web/          # Next.js 前端：课程首页、Studio、黑板编辑器、开放课程界面
├── launcher/         # 本地启动入口
├── scripts/          # 校验、资源重建和维护脚本
├── package.json      # 根 workspace 脚本
├── pyproject.toml    # 后端依赖与 pytest 配置
└── .env.example      # 环境变量示例
```

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

工程与 AI 协作约定见 `AGENTS.md`（根）和 `apps/web/AGENTS.md`（前端）。核心原则是通用能力优先：不要为单个案例、学科、教材、考试或 demo 写核心默认路径。提交前跑 `npm run verify`。
