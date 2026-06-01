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
