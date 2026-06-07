# 开放课堂（OpenClass）— AI 协作指南

开放课堂（OpenClass）是一个 AI 课程工作台。产品介绍、安装与 provider 配置见 `README.md` 和 `.env.example`。本文件只列协作必须知道的事。

## 仓库地图

```text
.
├── apps/
│   ├── api/              # FastAPI 后端（Python 3.13）
│   │   ├── app/main.py       # 应用组装 + 健康检查
│   │   ├── app/routers/      # workspace / documents / chat / realtime / resources
│   │   ├── app/services/     # 业务逻辑、状态、AI、存储、历史
│   │   └── data/             # 本地运行数据，已 gitignore
│   └── web/              # Next.js 前端，详见 apps/web/AGENTS.md
├── launcher/             # 可双击的本地入口 HTML
├── package.json          # 根 workspace 脚本
├── pyproject.toml        # 后端依赖 + pytest（单一来源）
└── .env.example          # 环境变量示例
```
## 不要做

- 不要在 router 里直接拼 SQL 或绕过 service 事务。
- 不要把 SQLite 文件、上传文件、日志放在 repo / `.next/` / 临时目录 / 会被部署覆盖的位置。
- 不要在线上手改 sqlite，除非已停服务并备份。
- 不要让多个独立后端进程同时写同一 sqlite。
- 不要在迁移到 SQLite 时顺手大改前端 UI；先收口存储与一致性。
- 不要为了单个 demo、单份资料或单次测试把特殊规则写进核心 service；其余见「AI 生成架构约束」。

## OpenClass 宪法：通用能力优先

OpenClass 是通用 AI 课程工作台，不是学科模板系统。最高优先级是保持系统通用性。

核心原则：

- 不要为案例写代码，要为能力写代码。
- 核心代码只处理通用学习能力、内容形态、资料结构、用户意图、文档操作和模型调用。
- 具体学科、具体教材、具体考试、具体语法点、demo 样例不得进入核心默认路径。

核心代码严禁加入：

- 学科关键词分支
- 教材关键词分支
- 固定讲义 HTML
- 固定课程模板
- demo 内容
- 针对单一测试样例的分支

严禁出现类似逻辑：

```python
if "法语" in topic:
if "数学" in topic:
if "文科" in topic:
if "计算机" in topic:
if "考试" in topic:
if "高考" in user_message:
if "CSAPP" in resource_name:
if "统计学习理论" in chapter_title:
```

允许的抽象：

- `request.intent == "generate_dialogue_practice"`
- `document_shape == "procedural"`
- `reference_context.has_numbered_sections`
- `user_goal == "exam_review"`

这些是通用教学形态、资料结构或用户目标，不是具体学科、教材或样例特例。

修改前必须先判断需求属于哪一类：

1. 通用能力
2. 内容形态
3. UI 交互
4. prompt 质量
5. schema / 数据结构
6. 资料解析
7. 专属插件 / adapter
8. 测试样例

如果你认为需要特殊处理，必须先回答：

1. 这是通用能力，还是某个特例？
2. 能不能用内容形态抽象解决？
3. 能不能放进 prompt，而不是 Python/TypeScript 核心代码？
4. 能不能放进 plugin / adapter，而不是默认核心路径？
5. 换成任意主题后，这段逻辑是否仍然成立？

改动前必填自检表：

```text
需求：
要改的文件：
问题属于：
[ ] 通用产品能力
[ ] 内容形态抽象
[ ] prompt 质量问题
[ ] schema / 数据结构问题
[ ] UI 交互问题
[ ] 资料解析问题
[ ] 特定教材 adapter
[ ] demo / 测试样例

是否引入以下内容：
[ ] 学科关键词
[ ] 教材关键词
[ ] 固定 HTML
[ ] 固定讲义内容
[ ] demo 内容
[ ] 针对单一测试样例的分支
```

如果上面任何一项为是，必须停止，重新设计。

文件边界：

- `lesson_factory.py`：只做 lesson、requirements、teaching guide 初始化。
- `fallback_generator.py`：只做领域无关 fallback，不得成为模板仓库。
- `renderer.py`：只做渲染路径选择，不写具体课程内容。
- `resource_library.py`：只做通用资料解析，不内置教材目录。
- `openai_course_ai.py`：只做模型调用、prompt、schema 解析，不写学科分支。
- `course-studio.tsx`：只做顶层组合，不继续堆状态、effect、realtime、editor、model selection 逻辑。

修改后必须说明：

1. 改了哪些文件。
2. 为什么这些改动是通用的。
3. 有没有新增领域硬编码。
4. 有没有让某个文件继续膨胀。
5. 如何验证。

## OpenClass 宪法：链路兼容与 AI 多角色协作

新增 AI 链路、功能、角色或工具调用时，最高约束是：**新能力必须接入现有协作协议，而不是重写、抢占或绕过现有协作协议。**

开发任何新能力前，必须先识别当前已经稳定存在的旧链路，并保证新能力不会隐式改变旧链路的触发条件、角色调用顺序、上下文来源、写入权限、状态归属、历史记录和用户可见输出边界。

标准回合流程：

```text
用户输入
→ TurnDecision 判断本轮任务
→ ResolveTarget 定位板书、选区、资料证据或对话上下文
→ BuildContext 构造最小必要上下文
→ ExecuteRole 执行唯一主动作
→ PersistHistory 写入历史、commit 与可追踪 metadata
→ UpdateRequirement 记录需求变化
```

固定工作流程架构（宪法级约束，普通功能开发不得更改、绕过、弱化或用 prompt 替代）：

本节描述 OpenClass 当前不可动摇的 AI 协作工作流。任何新增功能、bugfix、prompt 调整、UI 优化、模型替换、性能优化、历史记录改动，都必须服从本节；不得以“临时兜底”“体验优化”“为了某个测试更快通过”“模型自己能判断”为理由绕开本节。实现者必须把这些规则当作代码边界，而不是建议。

### 术语与状态边界

- `board_document` 为空：右侧板书文档没有可学习内容，系统处于“从零到有板书”的第一层链路。此时用户真正需要的是先把学习需求说清楚，而不是局部讲解、局部编辑或互动练习。
- `board_document` 非空：右侧已经有板书内容，系统处于“围绕已有板书处理具体任务”的第二层链路。此时不再用第一层学习需求清单决定本轮动作，而必须用第二层 `BoardTaskRequirementSheet` 决定“在什么地方、做什么、围绕什么、是否有特殊交互方式要求”。
- `LearningRequirementSheet`：第一层学习需求清单，只服务于空白板书首次生成。它记录用户想学什么、目标、水平、背景、输出偏好、成功标准等。
- `LearningClarificationStatus`：第一层需求澄清状态，只描述空白板书生成前的需求完整度、缺项、下一问、是否可生成。
- `BoardTaskRequirementSheet`：第二层已有板书任务清单，只服务于已有板书后的具体任务。它固定记录四个字段：目标位置、动作类型、问题 / 主题内容、是否有练习或特殊交互方式要求。
- `frozen requirement payload`：第一层清单生成板书前的冻结快照。它必须来自数据库版本记录，不得由当前运行态对象、原始对话、临时 prompt 拼接或 Chatbot 自由判断替代。
- `frozen board task` / board task version：第二层任务执行前的可追溯任务快照。写、改、讲、聊执行 commit 必须能追溯到该任务清单版本。
- `Board AI`：包含 BoardEditor、BoardTask route decision、BoardExplanationDirective 等板书侧智能。它拥有板书内容写入、目标内容截取、讲解授权和路线裁决权。
- `Chatbot`：左侧用户可见对话角色。它可以追问、解释状态、承接确认、执行被授权的讲解或互动回复，但不得自行生成右侧板书正文，不得自行裁决跳过板书侧流程。
- `FocusResolver`：目标位置定位角色。它负责把用户的“这里、第二节、刚才那段、选中的内容、某个标题”等定位成具体 `target_focus`，或在多候选 / 缺位置时要求澄清。
- `InteractionSession`：第二层 `chat` 路线启动后的规则互动循环。它必须有目标板书内容、互动规则、合规输入判定、进度和来源 board task 记录。

### 全局不可变门禁

- 每轮必须先判断 `board_document` 是否为空。空白板书和已有板书是两条不同链路，不得混用清单、状态机或生成入口。
- Chatbot 不得在需求未完整、目标未定位、资料未选择、写入动作未确认、板书侧未授权讲解时抢先给最终回答。
- Chatbot、Realtime Chatbot、StrongReasoning 等 Chatbot 侧能力不得直接读取 `board_document` 全文、摘要、选区正文或候选片段正文。它们只能接收板书侧 directive、InteractionSession 或后端工具结果中明确交接的目标摘录、边界和指令。
- BoardEditor / 板书文档编辑 AI 不得读取用户和 Chatbot 的原始聊天记录、recent conversation 或自由对话摘要；它只能接收 frozen requirement payload、BoardTaskRequirementSheet、定位证据、当前文档、目标摘录、资料摘要和结构化 action instruction。
- BoardEditor 不得直接消费原始用户对话来首次生成板书；首次生成只能消费 frozen requirement payload。
- 已有板书后的写、改、讲、聊不得直接由旧 action handler 触发；必须从 `BoardTaskRequirementSheet`、定位证据、Board AI route decision 进入。
- 清单变动必须可追溯。第一层需求清单和第二层任务清单都必须有 run / version / event 或 commit metadata 记录；执行后不得静默清空。
- “没有变动的聊天”可以不新增清单版本，但任何真实变动、完成、冻结、确认、消费、取消、失败都必须有历史痕迹。
- prompt 只能帮助模型完成某个角色的判断或生成，不能替代状态机、数据库历史、定位器、确认门禁和 commit metadata。

### 第一层：空白板书从零到有

第一层链路只在 `board_document` 为空时启用。它的目标不是讲解，也不是编辑，而是像服务员记录点菜单一样，先把用户学习需求问清楚、记录成清单、冻结，再交给板书文档编辑 AI 生成右侧板书。

固定顺序：

1. 用户输入进入 chat orchestrator。
2. 系统确认当前 `board_document` 为空，进入第一层需求清单链路。
3. Chatbot / Requirement Manager 从用户话语中探寻学习需求，只能围绕用户想学什么、为什么学、水平如何、已有背景、希望深度、输出形式、成功标准等继续澄清。
4. Requirement Manager 维护 `LearningRequirementSheet + LearningClarificationStatus`。
5. 如果清单发生真实变动，必须写入 `learning_requirement_versions`，并追加 `learning_requirement_events`。
6. 如果清单未完整，Chatbot 只能追问关键缺项或进行需求澄清，不得让 BoardEditor 生成板书。
7. 当 `ready_for_board=true` 或用户明确强制生成时，必须先写 completed / forced_frozen 版本，再写 frozen 快照。
8. frozen 快照必须在调用 BoardEditor 前落库，并通过 SSE / response 让前端可见进度变化。
9. BoardEditor 只能接收 frozen requirement payload、冻结澄清状态和必要资源摘要；不得接收未冻结 conversation、临时 PM 状态或 Chatbot 自由总结作为生成依据。
10. BoardEditor 生成右侧板书，Chatbot 不生成板书正文。
11. 生成成功后，必须写 lesson commit，commit metadata 必须包含 requirement run / frozen version 追踪信息。
12. requirement run 必须标记为 consumed；当前 active requirement 清单清空，但历史版本保留。
13. 生成失败时，必须写 generation_failed 事件，不写成功 commit，并允许同一 frozen version 后续重试。
14. 从零生成完板书后，Chatbot 默认承接询问用户是否要从头开始讲解。

第一层严禁：

- 空白板书时让 BoardEditor 直接吃原始聊天记录生成板书。
- 需求清单还没完整就偷偷生成板书。
- 生成后用运行态板书标题、摘录、临时 runtime 字段反写成“需求清单快照”。
- 生成成功后静默清空清单而不消费 run、不留版本历史。
- 为某个学科、教材、考试、demo 编写特殊第一层生成分支。

### 第二层入口：已有板书四字段任务清单

只要 `board_document` 非空，普通用户请求默认不再走第一层“从零到有板书”的需求清单闭环，而进入第二层已有板书任务链路。第二层的第一步永远是维护 `BoardTaskRequirementSheet`，而不是直接写、改、讲、聊。

四字段固定含义：

1. 目标位置：用户要处理板书中的哪里，例如选区、标题、段落、编号、前后文、某个明确片段。它最终必须解析为 `target_focus`，除非是无目标位置且经确认的内容缺失扩写。
2. 动作类型：用户到底要 `write`、`edit`、`explain` 还是 `chat`。
3. 问题 / 主题内容：用户围绕目标位置想补写、修改、理解、练习或互动的具体内容。
4. 特殊交互方式要求：用户是否要求练习、角色扮演、轮流读、问答、测验、纠错等特定互动规则；没有特殊互动要求时必须明确为无，而不是空悬。

第二层固定顺序：

1. 用户输入进入 orchestrator。
2. 系统确认 `board_document` 非空。
3. Chatbot / task manager 从用户话语、选区和上下文中维护 `BoardTaskRequirementSheet`。
4. 清单未完整时，写 collecting version / event，前端显示进度；Chatbot 只追问一个最关键缺项，不执行写、改、讲、聊。
5. 清单完整时，先写 ready 版本，再调用 FocusResolver 定位目标位置。
6. 定位结果和完整清单交给 Board AI route decision。
7. Board AI 只能裁决 `write / edit / explain / chat / clarify_location / await_write_confirmation`。
8. 多候选、位置缺失或定位不可靠时，只能 `clarify_location`，不得执行写、改、讲、聊。
9. 执行成功后，必须写 commit metadata：`board_task_run_id`、`board_task_version_id`、`board_task_route`、`board_task_decision`、`board_task_cleared`。
10. 执行成功后，board task run 标记 consumed；当前 active board task 清空，但历史保留。
11. 未执行、取消、失败必须写 not_executed / archived / execution_failed 类事件或 commit metadata，不得静默消失。

### 第二层 A：已有板书但目标内容不存在

这个链路用于：用户想学、问、讲某个主题，但当前板书全文找不到相关位置或目标内容。此时系统必须先承认板书没有对应内容，再询问是否扩写板书；不能假装板书里有内容，也不能让 Chatbot 自己凭常识讲。

固定顺序：

1. `BoardTaskRequirementSheet` 记录用户想处理的主题 / 问题内容。
2. FocusResolver 尝试从全文定位相关目标内容。
3. 如果全文没有相关内容，Board AI 必须裁决 `await_write_confirmation`，并设置 `location_status=content_absent`。
4. task run 进入 `awaiting_confirmation`，写入版本和事件。
5. Chatbot 只能自然说明当前板书里没有对应内容，并询问用户是否先扩写板书。
6. 用户确认扩写后，仍然复用同一条可追溯任务链路，不得新开无来源的临时写入。
7. BoardEditor 根据 `write_proposal` 和当前板书选择合适位置写入缺失内容。
8. 写入成功后写 lesson commit，commit metadata 必须记录原 board task run / version / route / decision。
9. 写入成功后，板书侧新内容或 directive 交给 Chatbot；Chatbot 只能围绕新写入内容讲解。
10. task run 标记 consumed，active board task 清空。
11. 用户拒绝、不确认或取消时，run 必须标记 not_executed / archived，active board task 清空或等待下一任务，历史保留。

此链路严禁：

- 全文没有对应内容时直接让 Chatbot 讲解。
- 全文没有对应内容时直接让 BoardEditor 写入而不先确认。
- 用户拒绝扩写后继续补写。
- 将“编辑不存在的内容”直接当成编辑执行；连续定位失败的编辑任务必须不执行，并按规则转入扩写确认或澄清。

### 第二层 B：已有板书且找到目标位置

这个链路用于：用户已经给出或系统已经定位到板书中的目标位置。此时动作类型可能是有目标位置的 `write`、`edit`、`explain` 或 `chat`。四条路线都必须从完整 board task 清单和定位证据进入，不允许旧执行器绕过。

#### B1. 有目标位置的 `write`

- 语义固定为：在目标位置扩写特定内容。
- 必须具备完整 `BoardTaskRequirementSheet`、`target_focus`、`write_proposal`。
- BoardEditor 只能围绕目标位置扩写，不得无理由改写全文、替换无关段落或追加到任意位置。
- 写入成功后必须刷新板书 runtime、写 commit、记录 `board_task_route=write`，并 consume board task。
- 如果目标位置缺失、多候选或不可靠，必须先澄清位置；不得把有目标位置的写降级成无目标追加。

#### B2. `edit`

- 语义固定为：改写板书中的目标文段。
- 必须具备 `target_focus + edit instruction`。
- BoardEditor 只能修改目标范围；不得凭用户一句“改一下”改全文。
- 多候选时必须让用户选择；找不到位置时必须追问位置。
- 如果用户明确要编辑某内容但该内容全文没有，连续定位失败后旧 edit task 必须 not_executed；新开的扩写任务只继承用户想补充的主题 / 问题内容，不继承原编辑动作要求。
- 编辑成功后写 commit，记录 route / decision / focus，consume board task 并清空 active board task。

#### B3. `explain`

- 语义固定为：对目标位置的板书内容进行讲解。
- 必须具备 `target_focus` 或等价目标摘录。
- 板书侧必须先产生 `BoardExplanationDirective`，包含目标摘要、目标摘录、讲解边界、允许 / 不允许讲解状态和给 Chatbot 的 teaching instruction。
- Chatbot 只能依据 directive 讲解；directive 不允许或需要澄清时，Chatbot 只能追问或说明状态。
- Chatbot 不得凭原始 conversation、自己的常识、未定位文本、未冻结清单直接讲解。
- 讲解成功后写 chat commit，记录 directive、route、focus 和 board task metadata，consume board task。

#### B4. `chat`

- 语义固定为：围绕目标位置的板书内容，按用户指定的特殊交互规则进行循环互动。
- 只有用户明确提出练习、问答、角色、轮次、朗读、测验、纠错等特殊互动方式时，才允许 `requested_action=chat`。
- 必须具备 `target_focus`、目标文段、`interaction_rule_draft.should_start=true`、规则文本、互动目标、合规输入说明、assistant 行为说明。
- 启动 `InteractionSession` 时必须保存：规则、目标文段、合规输入判定、进度、来源 `board_task_run_id`、来源 `board_task_version_id`。
- 启动成功后 board task 必须 consumed，active board task 清空，但 session 保持 active。
- 每轮互动先由 interaction decision 判断用户输入是否符合规则。
- `continue_rule`：用户输入合规，Chatbot 按规则和目标文段继续互动。
- `rule_violation`：用户仍在当前互动任务内，但输入格式、顺序或内容不符合规则；Chatbot 只做规则内纠错，不跳出到普通讲解。
- `exit_rule`：用户明确结束互动；session 结束，不再继续按规则互动。
- `new_task`：用户输入脱离当前互动规则，或提出新的写 / 改 / 讲 / 学习需求；session 必须结束，并把本轮用户输入重新送回第二层四字段任务清单。
- 规则外内容默认回到 board task 清单，不保留“暂停后旁路讲解”作为默认链路。

### 不可动摇的讲解约束

- Chatbot 在聊天框中执行任何“讲解”动作时，必须依照板书 AI / `BoardExplanationDirective` 给出的目标内容、摘录、边界和指令。
- 只有板书 AI 给 Chatbot 反馈并允许讲解后，Chatbot 才能进行讲解；否则 Chatbot 只能探寻需求、确认位置、请求选择资料、请求确认写入或说明当前状态。
- Chatbot 不得凭原始对话、自己的常识、未冻结清单、未定位目标、未授权的资料摘要绕过板书侧反馈直接讲解板书内容。
- Chatbot 可以生成用户可见的承接话术，但承接话术不得包含未被板书侧授权的实质讲解。
- 任何代码路径只要会让 Chatbot 输出实质讲解，就必须能在 commit metadata 或运行上下文中追溯到 board directive、target focus 或 interaction session reference context。

### 不可动摇的文档生成格式约束

本约束用于防止板书生成、改写、缩短、扩写、导入、流式预览或历史恢复时出现“模型直接输出 HTML”“普通文本被渲染成特殊格式”“旧 Markdown 层级被压扁”等问题。它是通用文档能力约束，不属于某个学科、教材、题型或 demo 的特殊规则。

- AI 生成给右侧板书的正文必须是 ChatGPT 风格的 Markdown / 普通文本：标题用 Markdown 标题，列表用 Markdown 列表，强调用 Markdown 加粗，表格用 Markdown 表格。
- 除了真实数学公式外，AI 生成的正文不得包含 HTML 格式内容或 HTML 标签，例如 `<h1>`、`<p>`、`<strong>`、`<em>`、`<ul>`、`<ol>`、`<li>`、`<table>`、`<span>`、`style=`、`class=`。
- 真实数学公式才允许使用 LaTeX 数学定界符或数学节点；普通语言、例句、语法说明、箭头说明、纠错说明、角色台词、编号、等号、括号、斜杠等都必须保持普通可见文字。
- `content_text` 是 AI 正文的事实来源。模型不得把 `content_text` 写成 HTML；如果模型返回 HTML，后端必须转换成 Markdown / 普通文本或拒绝本次写入，不能把原始 HTML 当成正式板书正文保存。
- `content_html` 和 `content_json` 只能是系统从受信任的 Markdown / 普通文本派生出来的内部渲染结果；BoardEditor 不得采信模型直接给出的 `content_html` 作为正式文档。
- 前端编辑器可以用 HTML DOM 呈现富文本，这是 UI 渲染层职责；这不等于允许 AI 生成 HTML 正文，也不等于允许把 HTML 作为文档语义源头。
- 历史板书、导入文档或模型输出中如果混入了 HTML，修复路径必须优先做通用 Markdown 化、富文本结构保持和安全清洗；不得为某个语言、课程名、测试样例或固定文本写特殊修复分支。
- 文档结构质量必须被保护。已有标题、列表、加粗、表格等结构时，改写 / 缩短不得把整篇文档压扁成无层级普通段落；如果模型结果明显丢失结构，后端必须拒绝、重试或标记失败，而不是污染当前快照。
- 质量门禁不合格时，不得通过放松审核让不好看的板书通关。后端必须把不合格原因、上一版不合格输出和格式合同交回 BoardEditor，要求其重写不合格篇幅；最多有限次修复，仍不合格时写失败并保留可重试状态。
- prompt 可以提醒模型输出 Markdown，但 prompt 不能替代后端格式门禁。任何写入正式板书的路径都必须在 service 层保证：非公式内容不以 HTML 或数学格式落库。

### 主动讲解与被动行动边界

本边界用于防止系统在需求清单还不充分时擅自开始教学，同时保证用户已经明确要求行动时不会被“继续完善清单”无限拖住。

- 主动讲解：指用户没有明确要求本轮开始讲解、写入、改写或规则互动时，系统根据自己判断想推进教学内容。主动讲解只允许在对应清单已经足够完整、目标已经定位、板书侧已经授权时发生。
- 被动行动：指用户本轮已经明确要求“讲解、解释、说明、开始讲、写、补充、修改、改写、练习、互动、按规则来”等动作。被动行动不要求清单达到理想丰满度，但必须达到可执行最低条件，并且仍然必须经过第二层任务清单、定位、route decision 和讲解 directive / 编辑器门禁。
- 第一层空白板书中，如果学习需求清单未完整且用户没有明确要求生成，Chatbot 只能继续澄清学习需求，不得主动生成板书或展开教学。
- 第一层空白板书中，如果用户明确要求“直接生成、开始生成、别问了”，系统可以强制冻结当前清单并生成，但必须写 forced_frozen / frozen 历史；这不是绕过清单，而是把不完整清单以强制开始的方式审计下来。
- 第二层已有板书中，如果四字段任务清单未完整且用户没有明确要求执行，Chatbot 只能追问缺项，不得主动讲解、写入、改写或启动互动。
- 第二层已有板书中，如果用户明确要求讲解某个内容，例如“是什么意思、什么含义、为什么、解释这里、讲第几句”，系统必须进入 `BoardTaskRequirementSheet` 链路，而不能落回普通 Chatbot 自由回答。
- 第二层已有板书中，如果用户在多候选澄清后明确说“都讲、全部讲、逐个、按顺序”，这表示目标是这些候选的顺序集合；对 `explain` 路线可以先从第一个候选生成 board directive 并开始讲解，不得无意义地反复要求用户选择单一位置。此规则只适用于讲解，不适用于写入或改写。
- “尽可能完善清单”是主动阶段的策略；“用户明确要求行动”是被动阶段的触发。两者冲突时，以被动行动触发为准，但绝不取消板书侧授权、定位和历史审计门禁。

### 不可更改声明

- 本固定工作流程架构是 OpenClass 默认 AI 协作宪法，不属于普通业务逻辑、普通 prompt、普通 UI 体验或单点 bugfix 的可修改范围。
- 后续开发只能在这些链路内增加更细的能力、测试、UI 展示、日志或模型质量改进；不得改变链路先后顺序、角色权限、清单状态机、定位门禁、冻结门禁、讲解门禁和历史审计要求。
- 任何实现如果绕过本节，即使测试通过，也视为架构违规，必须重做。
- 如果未来用户明确要求设计新层级或替换本宪法，必须先用独立设计说明列出旧链路、替换原因、兼容方案、迁移方式和回归测试；在新宪法落库前，不得通过代码偷偷改变现有链路。

核心原则：

- 新增能力只能作为明确的新分支、新步骤或可替换模块接入，不得隐式抢占旧流程。
- 不得让 Chatbot 在目标未定位、资料未选择、写入动作未确认时先行生成最终回答。
- 不得让 PM / Requirement Manager 在用户可见回答之后才决定本轮动作类型。
- 不得用 prompt 弥补缺失的流程判断、目标定位、资料证据或写入确认。
- 如果新能力必须调整旧链路，必须先说明旧链路原本如何工作、为什么必须替换、如何兼容旧行为，以及用哪些回归测试证明没有退化。
- 如果本轮只需要当前板书，就不要默认注入无关资料摘要；如果本轮需要资料证据，必须先由 ResourceResolver 明确选择相关资料。

AI 角色权责：

- `Chatbot`：只负责左侧可见对话、讲解、确认、承接和状态说明；不得生成整篇板书正文，不得假装已写入文档。
- `BoardEditor`：只负责右侧板书 / 文档的生成、替换、扩写、改写、简化和结构化写入。
- `PM / Requirement Manager`：只负责维护学习需求、动作意图和任务状态；不得事后改变已经执行的本轮动作。
- `FocusResolver`：只负责定位板书中的目标段落、标题、列表项、选区或上下文，并在不确定时要求确认。
- `ResourceResolver`：只负责选择资料证据、章节、片段和引用范围；不得默认污染与资料无关的 Chatbot prompt。
- `StrongReasoning`：只作为隐藏工具提供推理材料，不直接成为新的可见老师，不改变 Chatbot 的 learner-facing 身份。
- `Realtime`：只是同一个 Chatbot 的实时形态，不是另一个独立教师角色。

新功能接入前必须回答：

1. 这个能力改变标准回合流程的哪一步？
2. 本轮谁拥有最终决策权？
3. 这个角色的输入、输出、写权限是什么？
4. 它是否需要板书定位？如果需要，是否已经在回答或写入前完成？
5. 它是否需要资料证据？如果需要，是否已经明确选择相关资料？
6. 它失败或不确定时，是追问用户、降级处理，还是停止？
7. 它会不会让 Chatbot、PM、BoardEditor、FocusResolver、ResourceResolver 的职责重叠？
8. 它有没有破坏普通聊天、板书生成、板书局部讲解、板书编辑、资料问答、资料写入板书这些主链路？

链路兼容自检表：

```text
本次新增的是：
[ ] 新链路
[ ] 旧链路增强
[ ] 旧链路替换
[ ] 单点 bugfix

会影响哪些旧链路：
[ ] 普通 Chatbot 问答
[ ] 板书生成
[ ] 板书局部讲解
[ ] 板书编辑 / 改写 / 扩写 / 简化
[ ] 资料问答
[ ] 资料写入板书
[ ] 互动规则 / session
[ ] 强推理辅助
[ ] 历史记录 / commit
[ ] 流式输出 / loading 状态

是否改变以下内容：
[ ] 触发条件
[ ] 角色调用顺序
[ ] 上下文来源
[ ] 写入权限
[ ] 状态更新时机
[ ] 历史记录格式
[ ] 用户可见回复边界
```

如果上面任何一项为是，必须补充旧链路回归测试或明确说明为什么不需要。不能因为新增一个场景，就让既有链路里的目标定位、资料证据、写入确认、历史记录和状态更新步骤被绕过。

新增或修改链路后必须说明：

1. 保留了哪些旧链路。
2. 新链路接入在标准回合流程的哪一步。
3. 每个 AI 角色的输入、输出和权限有没有变化。
4. 是否存在 PM 事后决策、Chatbot 抢先回答、资料上下文污染或板书定位绕过。
5. 用哪些测试或日志证明旧链路协作流程没有被破坏。

### Natural Language Rule Governance

自然语言规则是 OpenClass 最容易补丁化的区域。任何新增或修改自然语言行为，都必须先证明它是通用信号、通用动作、通用目标定位或通用内容形态，而不是某个单句、单资料、单 demo 的特殊分支。

- Do not add a new regex directly inside `chatbot.py`.
- When adding or changing natural-language behavior, add a golden fixture first.
- Add at least two negative examples for each new positive fixture.
- Put signal extraction in `turn_intent.py`.
- Put action decisions in `board_task_decider.py`.
- Put target location in `target_resolvers/`.
- Put sequence decisions in `sequence_planner.py`.
- Put exercise / paragraph atom extraction in `explanation_atom_extractors/`.
- Include the matched rule name in `DecisionTrace`.
- A regex without positive and negative tests is not acceptable.

自然语言规则的职责边界：

- `turn_intent.py` 只抽取用户话语中的意图信号，例如 `wants_explain`、`wants_collection`、`wants_edit`、`wants_interaction`；不得直接决定写、改、讲、聊。
- `board_task_decider.py` 只根据意图信号、板书状态、任务清单和定位状态决定动作；不得直接做目标定位或生成回复。
- `target_resolvers/` 只做目标位置解析，例如选区、标题、编号、段落、练习集合、前后文；不得直接执行讲解或写入。
- `sequence_planner.py` 只决定是否进入顺序讲解、逐段讲解、逐题讲解或继续当前 sequence；不得直接生成最终讲解。
- `explanation_atom_extractors/` 只把板书内容切成可讲解的原子单元，例如段落、条目、练习题、问答对；不得写入板书或改变任务清单。

### DecisionTrace

AI 路由必须可审计。每次修改 AI 路由时，必须保证 response 或 commit metadata 里能看到本轮为什么走到这个行为。可追踪信息至少应覆盖：

```json
{
  "intent_signals": ["wants_explain", "wants_collection"],
  "matched_rules": ["collection_explanation_request"],
  "selected_action": "explain",
  "target_resolver": "ExerciseCollectionResolver",
  "sequence_mode": "atomic_explanation",
  "role_executed": "chatbot",
  "document_changed": false,
  "reason": "collection explanation requested for exercise group"
}
```

- Any AI routing change must preserve or improve `DecisionTrace`.
- If a behavior is hard to debug, add trace fields instead of adding hidden branching.
- `DecisionTrace` 必须描述通用决策原因，不得记录学科关键词、教材关键词、demo 内容或固定讲义内容作为路由依据。
- 如果新增规则会改变 `TurnDecision -> ResolveTarget -> BuildContext -> ExecuteRole -> PersistHistory -> UpdateRequirement` 中任一步，必须在 `DecisionTrace` 中标明被改变的步骤和原因。
- 如果某条规则匹配了用户输入，但最终没有被选为动作，也应在 `DecisionTrace` 或测试断言中说明它为什么被拒绝，防止多个自然语言规则静默抢占。

## 常用命令（仓库根执行）

```bash
npm run setup            # 首次安装：npm install + .venv + editable 装后端
npm run dev              # 同时启动前后端（:3000 / :8000）
npm run dev:web | dev:api
npm run lint:web | typecheck:web | test:api | build:web
npm run test:e2e         # Playwright 主流程 smoke（默认 :3110 / :8110）
npm run verify           # 提交前 gate：file-size guard + lint + typecheck + test:api + build:web
```

后端虚拟环境固定在仓库根 `.venv/`，不要在子目录另建。

## 后端约定：router 处理 HTTP，service 承担业务

- 新接口归入 `workspace / documents / chat / realtime / resources` 之一。
- 状态读写走 `app/services/workspace_state.py` 的 helper；新增代码优先经 `get_store()` / `get_course_store()` 取得 store，为后续依赖注入保留替换点。
- 课程包持久化用 `SqliteCourseStore`；新增写路径复用 service 层事务，不要恢复 `store.json` 写入。
- auth 表读写收口在 `AuthStore`；`auth_service.py` 负责认证流程、密码/OAuth 规则和错误转换，不继续新增裸 SQL。
- 任何改动课程包 / lesson / 文档 / 版本历史 / 资源库的操作必须在事务内。
- 返回前端前剥离资料原文与本地路径。

## AI 生成架构约束

- 核心 service 必须遵守「OpenClass 宪法：通用能力优先」。
- 不得写入 demo、教材、学科专属生成逻辑；不得把固定讲义全文或「关键词→专用模板」作为默认路径。
- 线上行为只能由用户输入、上传资料、课程 metadata、模型输出与通用规则驱动。
- 术语表、章节目录、知识点扩展从资料或模型来，不写死在 workflow / factory / resource_library。
- 任何课程级示例与 fixture 仅允许在 tests、fixtures、文档中出现，不得污染真实请求的默认逻辑。
- 当前真实启用的 AI 入口以 `/api/ai-models`、`/api/lessons/{lesson_id}/chat` 和文档相关 service 为准；realtime 后端默认关闭，只有 `OPENCLASS_REALTIME_ENABLED=true` 时才会接入 OpenAI WebRTC，且仍作为同一个 Chatbot 的实时形态。`BoardTeachingGuide` / `BoardTeachingProgress` 一类类型属于保留兼容 / future workflow schema，不能当作已完整接入的教学运行框架。

## 数据存储

- SQLite 主库默认 `apps/api/data/openclass.sqlite3`，线上设 `OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3`。开 WAL，设合理 `busy_timeout`。
- 上传文件落盘到持久化目录（线上 `/var/lib/openclass/uploads/`），DB 只存 metadata、原始文件名、mime、大小、路径。
- 旧 `apps/api/data/store.json` 仅作首次迁移来源，导入后归档为 `store.migrated-*.json`，不再作运行存储。
- AI 输入输出走 `apps/api/data/logs/ai-usage.jsonl`，不入主业务表。

主要表（`SqliteCourseStore`）：


| 表                    | 内容                                    |
| -------------------- | ------------------------------------- |
| `course_packages`    | 课程包标题、摘要、排序、当前打开状态                    |
| `lessons`            | lesson 基础信息、所属 package、当前文档、学习需求、教学指南 |
| `lesson_commits`     | 历史快照、commit metadata、父 commit、分支名     |
| `lesson_branches`    | 分支名、head commit、base commit           |
| `course_graph_edges` | 课程图谱关系                                |
| `resources`          | 上传资料 metadata、抽取状态、文件路径               |
| `resource_chapters`  | 资料章节 outline                          |
| `workspace_settings` | active package、打开标签页等全局 workspace 状态  |


富文本 `content_json` / `content_html` / `content_text` 暂作 JSON/text 字段存在 `lessons` 与 `lesson_commits`，不拆 block 表。

## 环境与日志

- 复制 `.env.example` 为仓库根 `.env`，不要提交。
- 线上额外配置：`OPENCLASS_DATABASE_PATH`、`OPENCLASS_UPLOAD_DIR`、`OPENCLASS_EXPORT_DIR` 都指到 `/var/lib/openclass/` 下。
- 前端"选择模型"读 `/api/ai-models`，未配置 key 的 provider 显示为未配置。

## 线上部署

当前生产入口：

- 域名：`https://class.bupt8.com`
- 服务器：`188.166.185.136`
- 登录：`ssh root@188.166.185.136`
- 反代与证书：Caddy，配置文件 `/etc/caddy/Caddyfile`，证书自动签发和续期。
- 应用目录：`/opt/openclass`
- Git 源码：`/opt/openclass/repo`，由 `git clone git@github.com:MrFlow-mean/openclass.git` 部署。
- 运行配置：`/opt/openclass/.env`，从本地仓库 `.env` 同步，不要打印或提交密钥。
- 持久化数据：`/opt/openclass/data` 挂载到容器内 `/var/lib/openclass`。
- 数据库：容器内 `/var/lib/openclass/openclass.sqlite3`（WAL，`busy_timeout=5000`）。
- 上传与导出目录放在容器内 `/var/lib/openclass/uploads/`、`/var/lib/openclass/exports/`，不要放进仓库、`.next/` 或临时目录。
- 拓扑约束：单后端写入进程 + 文件级备份 + WAL；不允许多机/多进程同时写同一 sqlite。
- 容器：`openclass-api` 绑定 `127.0.0.1:8000`，`openclass-web` 绑定 `127.0.0.1:3000`。

本地部署前 gate：

```bash
npm run verify
```

同步本地环境变量到服务器：

```bash
scp .env root@188.166.185.136:/opt/openclass/.env
ssh root@188.166.185.136 'chmod 600 /opt/openclass/.env && if grep -q "^OPENCLASS_PUBLIC_ORIGIN=" /opt/openclass/.env; then sed -i "s#^OPENCLASS_PUBLIC_ORIGIN=.*#OPENCLASS_PUBLIC_ORIGIN=https://class.bupt8.com#" /opt/openclass/.env; else printf "\nOPENCLASS_PUBLIC_ORIGIN=https://class.bupt8.com\n" >> /opt/openclass/.env; fi'
```

更新线上代码并重启：

```bash
ssh -A root@188.166.185.136
cd /opt/openclass/repo
git fetch origin main
git checkout main
git pull --ff-only origin main

cd /opt/openclass
docker compose build api web
docker compose up -d
docker compose ps
```

仓库是私有仓库，服务器没有长期保存 GitHub 私钥；需要从本机更新时用 `ssh -A` 走 agent forwarding。若以后改为服务器自主拉取，再单独配置 GitHub deploy key。

仅重启现有容器：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose restart'
```

仅重建前端（改域名或前端环境变量后）：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose build web && docker compose up -d web'
```

Caddy 配置检查与重载：

```bash
ssh root@188.166.185.136 'caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy'
```

线上验证：

```bash
curl -fsSI https://class.bupt8.com/
curl -fsS https://class.bupt8.com/health
curl -fsS https://class.bupt8.com/api/ai-models
echo | openssl s_client -servername class.bupt8.com -connect class.bupt8.com:443 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

查看日志：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose logs --tail=100 api web'
ssh root@188.166.185.136 'journalctl -u caddy -n 100 --no-pager'
```

写入异常先停服务，保留 sqlite、WAL、日志和上传文件证据，再回滚：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose stop api web'
```

## 提交前

- 跑 `npm run verify`（或至少 `lint:web` + `typecheck:web` + 受影响的 `test:api`）。
- 不要提交 `.env`、`.venv/`、`apps/api/data/` 下的运行数据、`node_modules/`、`.next/`。


## 风格

- 注释只解释非显而易见的意图或约束，不复述代码。
- 不主动新建 README / 文档；扩充本指南或对应 README 即可。
