# 开放课堂（OpenClass）

<p align="center">
  <img src="docs/assets/openclass-product-cover.png" alt="OpenClass 产品封面" width="280" />
</p>

开放课堂（OpenClass）是一个面向严肃学习、研究、写作和知识工作的 AI document workspace（AI 文档工作台）。它把 AI conversation（AI 对话）和 AI document writing（AI 文档编写）放在同一个工作空间里：左侧 Chatbot（对话角色）理解需求、承接讲解和确认行动，右侧 Board（板书文档）沉淀结构化成果、支持继续编辑、导入导出和版本回退。

当前仓库是一个本地优先的课程 / 文档工作台：前端提供 OpenClass Studio、课程包、lesson（工作单元 / 文档单元）、富文本文档编辑器、模型选择、Realtime（实时输入输出）入口和版本历史；后端提供 FastAPI（Python API 服务框架）、SQLite（本地关系型数据库）持久化、AI workflow（AI 工作链路）、文档导入导出和审计日志。

OpenClass 不做固定学科模板系统，也不向 general agent（通用智能代理）方向扩张。产品需求、目标用户和边界见 [OpenClass PRD](docs/product/openclass-prd.md)。

## 当前能力

- 工作空间与课程包：创建、打开、重命名、删除课程包，按 lesson 组织严肃学习或文档工作。
- Chatbot + Board 双工作面：对话负责需求、讲解、确认和状态反馈；Board 负责生成、编辑和保存正式文档内容。
- 空白 Board 生成：空板书时先判断学习 / 文档工作模式，维护 LearningRequirementSheet（学习需求清单），冻结后交给 BoardEditor（板书文档编辑器）生成结构化文档。
- 已有 Board 任务：非空板书进入 BoardTaskRequirementSheet（已有板书任务清单），再通过 FocusResolver（目标定位器）定位目标内容，最后按 write / edit / explain / chat（写入 / 编辑 / 讲解 / 互动）路线执行。
- 规则互动：用户明确要求问答、练习、轮次、纠错等互动时，可启动 InteractionSession（规则互动会话），围绕目标文档内容继续对话。
- 富文本文档编辑：右侧类 Word 编辑器支持标题、段落、列表、表格、强调、数学公式、手动编辑和自动保存。
- 文档格式约束：正式 `content_text` 以 Markdown（轻量标记文本格式）/ 普通文本为事实来源；HTML（超文本标记语言）只作为渲染层或导出层结果。
- 导入导出：支持 DOCX（Word 文档格式）导入、DOCX 导出和 HTML 导出。
- 版本历史：lesson 支持 commit（提交记录）、branch（分支）、restore（恢复）和图谱化历史查看。
- 模型目录：`/api/ai-models` 暴露可用模型和 provider（模型提供方）状态，前端可切换文本 / 板书模型。
- Realtime：默认关闭；开启后作为同一个 Chatbot 的实时语音 / 实时输入输出形态，而不是新的独立教师角色。
- 登录与管理：支持邮箱登录、游客登录、可选 OAuth（第三方登录授权）和基础 admin（管理员）总览。

## 产品 Workflow

### 从空白 Board 到文档

1. 用户提出学习、研究、写作或文档任务。
2. 后端判断当前 Board 是否为空，并识别本轮是否需要生成文档。
3. Requirement Manager（需求管理器）维护最小必要需求清单；信息不足时只追问关键缺口。
4. 需求达到可执行条件后写入 frozen requirement（冻结需求快照）。
5. BoardEditor 只根据冻结快照和必要资料摘要生成右侧 Board。
6. 系统写入 lesson commit，并保留 requirement run（需求运行记录）与 metadata（元数据）。
7. Chatbot 承接下一步，不把临时聊天内容当作正式文档事实来源。

### 围绕已有 Board 工作

1. 用户发起讲解、补充、改写、练习或互动请求。
2. 系统维护 BoardTaskRequirementSheet，明确目标位置、动作类型、问题 / 主题和特殊互动方式。
3. FocusResolver 把“这里”“第 2 节”“选中的内容”等表达定位为具体 target focus（目标范围）。
4. Board AI（板书侧智能）裁决路线：write、edit、explain、chat、clarify_location（澄清位置）或 await_write_confirmation（等待写入确认）。
5. write / edit 路线由 BoardEditor 修改 Board；explain 路线先生成 BoardExplanationDirective（板书讲解指令）再交给 Chatbot 讲解；chat 路线启动 InteractionSession。
6. 成功执行后写入 commit / chat history（聊天历史）和 board task metadata，清空当前 active task（活跃任务），保留历史。

## 仓库地图

```text
.
├── apps/
│   ├── api/                         # FastAPI 后端
│   │   ├── app/main.py              # 应用组装、CORS（跨源资源共享）、健康检查、模型目录
│   │   ├── app/routers/             # API route（接口路由）：auth / workspace / documents / chat / realtime / resources
│   │   ├── app/services/            # service layer（服务层）：AI workflow、文档、资料、历史、模型、Realtime
│   │   ├── app/models.py            # model/schema（数据结构）：Board、lesson、资源、任务、响应模型
│   │   ├── tests/                   # pytest（Python 测试框架）用例
│   │   └── data/                    # 本地运行数据，已 gitignore（Git 忽略）
│   └── web/                         # Next.js（React 应用框架）前端
│       ├── src/app/                 # 页面路由：home / studio / course / auth / admin
│       ├── src/components/          # frontend UI（前端界面）组件
│       ├── src/components/course-studio/
│       ├── src/hooks/course-studio/ # hook（前端状态逻辑）
│       └── src/lib/                 # 前端 API、格式、模型和状态工具
├── docs/
│   ├── assets/                      # README 和产品展示素材
│   └── product/openclass-prd.md     # PRD（产品需求文档）
├── launcher/                        # 本地入口 HTML
├── launchd/                         # macOS 后台守护配置
├── scripts/                         # 本地守护、安装和 guard（守卫检查）脚本
├── package.json                     # 根 workspace（工作区）脚本
├── pyproject.toml                   # 后端依赖与 pytest 配置
└── .env.example                     # 环境变量示例
```

## 本地运行

需要 Node.js（JavaScript 运行时）20+ 和 Python（后端语言）3.13+。

```bash
npm run setup            # 首次安装：npm install + .venv + editable（可编辑模式）安装后端
cp .env.example .env     # 配置至少一个 provider（模型提供方）
npm run dev              # 同时启动前后端
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000
- 健康检查：http://localhost:8000/health
- SQLite 主库：`apps/api/data/openclass.sqlite3`
- AI 调用日志：`apps/api/data/logs/ai-usage.jsonl`

也可以双击 `start-ai-board.command`，它会通过本地守护进程启动前后端，并打开 `launcher/personal-home.html`。日常长时间使用优先用这个入口。

生产或长期运行时，建议把数据目录指到稳定持久化路径：

```bash
OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3
OPENCLASS_UPLOAD_DIR=/var/lib/openclass/uploads
OPENCLASS_EXPORT_DIR=/var/lib/openclass/exports
OPENCLASS_BOARD_ASSET_DIR=/var/lib/openclass/board-assets
OPENCLASS_LIBREOFFICE_PATH=/absolute/path/to/soffice
OPENCLASS_PUBLIC_ORIGIN=https://your-domain.example
OPENCLASS_WEB_ORIGIN=https://your-domain.example
```

`OPENCLASS_LIBREOFFICE_PATH` 用于忠实渲染 Office（办公文档）原生图表和组合图；如果显式配置的路径不可执行，API（应用程序接口）会在启动检查时拒绝运行。未配置时，文字与可直接提取的图片、表格仍可索引，无法可靠渲染或定位的原生图表只记入部分索引状态，不会猜测插入位置。

资料视觉索引只处理静态文件与网页结构。音频和视频继续只使用转录文本，不抽取视频帧，也不把画面登记为板书视觉证据。

## 模型与 Provider（模型提供方）

最小配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_API=chat_completions
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
OPENAI_MODEL=gpt-5.5
OPENAI_PM_MODEL=gpt-5.5
OPENAI_BOARD_MODEL=gpt-5.5
OPENAI_CHATBOT_MODEL=gpt-5.5
OPENCLASS_REALTIME_ENABLED=false
OPENCLASS_REALTIME_TOOLS_ENABLED=false
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_REASONING_EFFORT=low
OPENAI_IMAGE_MODEL=gpt-image-2
```

`.env.example` 还包含 Anthropic、Google、DeepSeek、Kimi、MiniMax、自定义 OpenAI-compatible（兼容 OpenAI 接口）网关、自定义 Anthropic-compatible（兼容 Anthropic 接口）网关，以及本地 Codex app-server（Codex 应用服务）适配器配置。

Realtime 默认关闭；只有设置 `OPENCLASS_REALTIME_ENABLED=true` 才会启用后端实时连接。`OPENCLASS_REALTIME_TOOLS_ENABLED=true` 时，Realtime 会通过服务端 sideband（旁路控制通道）调用同一条 Chatbot workflow；关闭时只做麦克风转写，再把文本交给普通 Chatbot。`OPENAI_REALTIME_REASONING_EFFORT=low` 是语音默认推理强度，可按延迟和复杂度调成 `medium` 或 `high`。

## 数据与文档格式

- AI 写入 Board 的正式正文必须是 Markdown / 普通文本，不能把模型直接返回的 HTML 保存为正式 `content_text`。
- 前端编辑器可以把文档渲染成 HTML DOM（浏览器文档对象模型），但这只是展示层，不改变后端事实来源。
- DOCX 导出走后端原生渲染路径，和网页富文本渲染保持分离。
- 资料解析以通用结构为边界：章节、页面、片段、引用范围和 evidence，不在核心代码里内置具体学科、教材或 demo 内容。

## 测试与验证

`npm run verify` 是本地和 CI（持续集成）的主验证入口，不需要真实 LLM（大语言模型）API key（接口密钥）：

```bash
npm run guard:file-sizes
npm run lint:web
npm run typecheck:web
npm run test:api
npm run build:web
npm run verify
```

GitHub Actions 会在 PR（Pull Request，合并请求）和 `main` 分支 push（推送）时运行 `.github/workflows/verify.yml` 中的 verify workflow（验证工作流）。

Playwright（浏览器端到端测试工具）主流程 smoke test（冒烟测试）是可选验证，默认不作为合并门禁：

```bash
npm run test:e2e          # 默认启动 127.0.0.1:3110 / 127.0.0.1:8110
```

## 协作约定

- 工程与 AI 协作规则见 [AGENTS.md](AGENTS.md)。
- 前端协作规则见 [apps/web/AGENTS.md](apps/web/AGENTS.md)。
- 提交前优先运行 `npm run verify`。
- 新功能应接入现有 AI workflow，不绕过需求清单、目标定位、资料选择、写入确认、讲解授权和历史审计。
- OpenClass 保持通用能力优先：不要把具体学科、教材、考试、固定讲义或 demo 样例写入核心默认路径。
