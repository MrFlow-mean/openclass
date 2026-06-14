# 开放课堂（OpenClass）AI 协作指南

OpenClass 是通用 AI 课程工作台，不是学科模板系统。产品、安装和 provider 配置见 `README.md` 与 `.env.example`；本文件只保留协作硬边界。

## 仓库地图

- `apps/api/`：FastAPI 后端；routers 处理 HTTP，services 承担业务、状态、AI、存储、历史。
- `apps/web/`：Next.js 前端；课程工作台、编辑器、聊天、资料、历史等 UI。
- `launcher/`：本地入口 HTML。
- `docs/`：架构契约与设计说明。

## 修改前自检

先判断问题属于哪类：通用产品能力、内容形态、UI 交互、prompt 质量、schema / 数据结构、资料解析、专属 plugin / adapter、测试样例。

如果需要特殊处理，先回答：

1. 这是通用能力，还是特例？
2. 能不能用内容形态抽象解决？
3. 能不能放进 prompt，而不是核心 Python / TypeScript？
4. 能不能放进 plugin / adapter，而不是默认路径？
5. 换成任意主题后是否仍成立？

禁止进入核心默认路径：学科 / 教材 / 考试关键词分支、固定讲义 HTML、固定课程模板、demo 内容、单样例分支、固定兜底话术或对话模板库。用户可见回复应由 AI 按上下文自主生成。

允许的抽象：通用教学形态、资料结构、用户目标，例如 `request.intent == "generate_dialogue_practice"`、`document_shape == "procedural"`、`reference_context.has_numbered_sections`、`user_goal == "exam_review"`。

## 标准回合

新增 AI 链路、功能、角色或工具调用时，只能接入现有协议，不得抢占或绕过。

```text
用户输入 → TurnDecision → ResolveTarget → BuildContext → ExecuteRole → PersistHistory → UpdateRequirement
```

改动若影响触发条件、角色顺序、上下文来源、写入权限、状态时机、历史格式或用户可见回复边界，必须补旧链路回归测试或说明为什么不需要。

## 角色边界

- `Chatbot`：左侧对话、追问、确认、承接、被授权讲解和互动；不得生成整篇右侧板书，不得假装已写入。
- `BoardEditor`：右侧板书 / 文档生成、替换、扩写、改写、简化和结构化写入；不得消费未冻结原始聊天。
- `PM / Requirement Manager`：维护学习需求、动作意图、任务状态、版本和事件；不得事后改变已执行动作。
- `FocusResolver`：定位目标段落、标题、列表项、选区或上下文；不确定时要求确认。
- `ResourceResolver`：选择资料证据、章节、片段和引用范围；不得污染无关 Chatbot prompt。
- `StrongReasoning`：隐藏推理工具，不成为可见老师。
- `Realtime`：同一个 Chatbot 的实时形态，不是独立教师。

## 全局门禁

- 每轮先判断 `board_document` 是否为空；空白板书和已有板书是两条链路。
- Chatbot 不得在需求未完整、目标未定位、资料未选择、写入未确认或板书侧未授权时抢先最终回答。
- Chatbot / Realtime / StrongReasoning 不得直接读板书全文、摘要、选区正文或候选片段；只能接收 directive、InteractionSession 或后端工具明确交接的摘录、边界和指令。
- BoardEditor 只接收 frozen requirement、BoardTaskRequirementSheet、定位证据、当前文档、目标摘录、资料摘要和结构化 action instruction。
- 清单变动、冻结、确认、消费、取消、失败都必须可追溯；无变动聊天可不新增版本。

## 第一层：空白板书

仅在 `board_document` 为空时启用。先由 `InitialLearningIntentGate` 判断通用学习形态和目标颗粒度，再决定生成或澄清。

门禁字段：`learning_mode = learn_concept / practice_activity / undecided`；`target_granularity = specific_concept / broad_domain / ambiguous`；`next_action = freeze_minimal_and_generate_board / ask_specific_concept / collect_practice_requirements / ask_learning_mode`。

规则：

- 具体知识点或明确问题：形成 `minimal frozen requirement`，写 ready / frozen 版本，再让 BoardEditor 生成板书。
- 宽泛领域：只追问具体知识点、问题或范围，不生成默认课程路径。
- 练习型教学：进入练习型需求清单，补齐练习内容、水平、场景、形式、反馈和成功标准。
- 学习形态不明：先问是学习知识内容还是做练习型教学。
- 用户明确“直接生成 / 别问了”可 forced frozen，但仍要落库、留版本、写 commit metadata。

严禁 BoardEditor 直接吃原始聊天生成首版板书；严禁需求未冻结就生成；严禁学科、教材、考试或 demo 分支。详见 `docs/initial-learning-intent-contract.md`。

## 第二层：已有板书

只要 `board_document` 非空，普通请求默认进入 `BoardTaskRequirementSheet`，不再走第一层。

四字段：目标位置、动作类型 `write / edit / explain / chat`、问题 / 主题内容、特殊交互方式要求。

固定顺序：维护清单；未完整时写 collecting 并只追问一个关键缺项；完整时写 ready；FocusResolver 定位；Board AI route decision；执行后写 commit metadata；成功 consume run 并清空 active task，失败 / 取消 / 拒绝留事件。

Board AI 只能裁决 `write / edit / explain / chat / clarify_location / await_write_confirmation`。

路线约束：

- `await_write_confirmation`：全文无相关内容时先询问是否扩写；确认后复用同一 task 写入。
- `write`：只在目标位置扩写；目标缺失、多候选或不可靠时先澄清。
- `edit`：只改目标范围；不存在内容不能当编辑执行。
- `explain`：必须先有 `BoardExplanationDirective`，Chatbot 只按 directive 讲解。
- `chat`：仅在明确练习、问答、角色、轮次、测验、纠错等互动要求时启动 `InteractionSession`。

用户明确要求行动时可按最低可执行条件推进，但绝不取消任务清单、定位、route decision、directive / 编辑器门禁和历史审计。

## 文档格式

- 右侧板书正文事实来源是 `content_text`。
- AI 正文必须是 Markdown / 普通文本；除真实数学公式外，不得输出 HTML 标签、`style=`、`class=` 或把普通语言包成数学格式。
- `content_html` / `content_json` 只能由系统从受信任 Markdown / 普通文本派生；BoardEditor 不得采信模型直出 HTML。
- 混入 HTML 时做通用 Markdown 化、富文本结构保持和安全清洗，不写样例分支。
- 改写、缩短、扩写不得压扁标题、列表、加粗、表格等结构；格式门禁失败时重试或失败，不污染快照。

## 工程治理

- 禁止“行为坏了就往核心链路补一个判断”。
- AI 路由或自然语言规则改动前，先定位属于 `TurnDecision / ResolveTarget / BuildContext / ExecuteRole / PersistHistory / UpdateRequirement` 的哪一步。
- 先写 golden fixture；每个正例至少两个反例。
- 证明新增信号是通用意图、动作、目标定位或内容形态，不是单句补丁。
- response 或 commit metadata 保留 / 增加 `DecisionTrace`：匹配信号、规则、选中 action、未选原因、执行角色、文档是否变更。
- 一次只解决一个问题，尽量不超过 5 个文件；不顺手改数据库、认证、部署、依赖、无关 UI。
- 预计超过 5 个文件、新增依赖或改环境变量时，先拆方案并请求确认。
- 修改后默认同步 GitHub，除非用户明确要求不要。

## 文件边界

- `lesson_factory.py`：lesson、requirements、teaching guide 初始化。
- `fallback_generator.py`：领域无关 fallback，不做模板仓库。
- `renderer.py`：渲染路径选择，不写课程内容。
- `ai_workflow.py`：通用流程编排，不写学科知识。
- `resource_library.py`：通用资料解析，不内置教材目录。
- `openai_course_ai.py`：模型调用、prompt、schema 解析，不写学科分支。
- `chatbot.py`：薄编排器，不新增自然语言正则或大 handler。
- `course-studio.tsx`：顶层组合，不继续堆 state、effect、realtime、editor、model selection。

## 后端、前端、数据

- 新后端接口归入 `workspace / documents / chat / realtime / resources`；写路径复用 service 事务；返回前端前剥离资料原文与本地路径。
- 状态读写走 `workspace_state.py` helper；新增路径优先经 `get_store()` / `get_course_store()`。
- 前端 API 调用走 `apps/web/src/lib/` client；视图类型放 `apps/web/src/types/`；更多见 `apps/web/AGENTS.md`。
- service 层细则见 `apps/api/app/services/AGENTS.md`。
- SQLite 默认 `apps/api/data/openclass.sqlite3`；上传、导出和线上数据使用持久化目录；AI 日志写 `apps/api/data/logs/ai-usage.jsonl`。
- 不提交 `.env`、`.venv/`、`apps/api/data/`、`node_modules/`、`.next/`。

## 命令与部署

常用命令：`npm run setup`、`npm run dev`、`npm run lint:web`、`npm run typecheck:web`、`npm run test:api`、`npm run build:web`、`npm run test:e2e`、`npm run verify`。

生产：`https://class.bupt8.com`，服务器 `188.166.185.136`，应用 `/opt/openclass`，源码 `/opt/openclass/repo`，配置 `/opt/openclass/.env`，数据 `/opt/openclass/data`。部署前跑 `npm run verify`；线上保持单后端写入进程 + 文件级备份 + WAL。

## 完成标准

- 行为有测试、fixture 或明确验证方式。
- 改动范围尽量小，公共行为变化已记录。
- 相关检查已运行并报告结果。
- 剩余风险已说明。
