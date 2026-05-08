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

## 常用命令（仓库根执行）

```bash
npm run setup            # 首次安装：npm install + .venv + editable 装后端
npm run dev              # 同时启动前后端（:3000 / :8000）
npm run dev:web | dev:api
npm run lint:web | typecheck:web | test:api | build:web
npm run verify           # 提交前 gate：lint + typecheck + test:api + build:web
```

后端虚拟环境固定在仓库根 `.venv/`，不要在子目录另建。

## 后端约定：router 处理 HTTP，service 承担业务

- 新接口归入 `workspace / documents / chat / realtime / resources` 之一。
- 状态读写走 `app/services/workspace_state.py` 的 helper，router 不直接碰 DB。
- 课程包持久化用 `SqliteCourseStore`；新增写路径复用 service 层事务，不要恢复 `store.json` 写入。
- 任何改动课程包 / lesson / 文档 / 版本历史 / 资源库的操作必须在事务内。
- 返回前端前剥离资料原文与本地路径。
## AI 产品抽象与反硬编码规则

OpenClass 是面向任意课程、任意资料、任意学习目标的通用 AI 课程工作台，不是关键词模板机。开发时必须优先保证系统的通用性、可维护性和可扩展性。

### 最高原则

不要为案例写代码，要为能力写代码。

当某个具体主题、某份资料、某个 demo 或某次测试效果不好时，不要直接往核心代码里增加关键词判断或固定模板。必须先判断问题属于哪一层：

- prompt 不够清楚
- 数据结构不能表达用户目标
- 资料抽取或检索不够好
- workflow 路由逻辑不够通用
- fallback 太弱
- UI 交互没有让用户表达清楚需求

只有确定是通用能力缺失时，才允许改核心代码。

### 严禁行为

禁止在核心业务代码中写死以下内容：

- 特定学科判断，例如：法语、数学、机器学习、模式识别、历史、文科等。
- 特定课程内容，例如：咖啡厅点餐、过去将来时、统计学习理论、概率密度估计等。
- 特定教材章节判断，例如：第一章、概论、第七章、CSAPP 某章等。
- 特定输出模板绑定到某个关键词。
- 为了修复一个样例，把样例内容写进 Python / TypeScript 代码。
- 在 factory / service / router 里拼整篇固定 HTML 课程内容。
- 用越来越多 if-else 解决内容生成质量问题。
- 在通用 workflow 中内置某本教材、某个学科或某种语言的专用术语表。

### 正确做法

核心代码只负责通用能力：

- lesson 初始化
- requirement 默认结构
- document 数据结构转换
- prompt 组装
- reference context 整理
- fallback 生成
- 版本保存
- API 请求与响应
- workflow 状态流转

具体讲什么、怎么讲、怎么组织内容，应该由以下输入决定：

- 用户输入
- 上传资料
- 学习目标
- 学生背景
- 当前 lesson 上下文
- LLM 生成结果

不要由代码里的关键词规则决定。

### 正确抽象方向

不要把系统抽象成：

- 法语课模板
- 机器学习课模板
- 文科课模板
- CSAPP 模板

应该抽象成更通用的教学结构：

- 概念解释型
- 场景对话型
- 资料扩讲型
- 练习训练型
- 案例分析型
- 项目实战型
- 考试复习型
- 章节讲义型

这些结构可以适用于不同学科，而不是绑定某个具体内容。

### 文件职责边界

- `lesson_factory.py`：只做 lesson / requirement / teaching guide 的通用初始化，不写具体课程内容。
- `ai_workflow.py`：只做通用流程路由，不内置某学科、某教材、某语言的专用判断。
- `openai_course_ai.py`：可以写通用 prompt 质量标准，但不要堆某个学科的特殊规则。
- `resource_library.py`：只做通用资料解析。某本教材的专用 outline 必须移到 plugin / adapter / example，不得污染核心资源库。
- `openai_realtime.py`：不要默认只服务中文课堂；语音语言和转写提示应来自 lesson / user / settings。
- router：只处理 HTTP，不写业务逻辑。
- service：处理业务流程，但不直接写死具体课程内容。

### 修改前必须先输出计划

动手改代码前，先说明：

1. 这个问题属于哪一层：产品逻辑、prompt、数据结构、渲染、存储、workflow，还是 UI？
2. 准备改哪些文件？
3. 每个文件的职责是什么？
4. 是否会引入特定学科、特定案例、特定关键词规则？
5. 有没有更通用的解决方式？

没有完成这一步，不要直接写代码。

### 修改后必须自查

每次改完代码后，必须检查：

1. 有没有新增硬编码学科关键词？
2. 有没有新增固定课程内容？
3. 有没有把 demo 内容写进核心逻辑？
4. 有没有让一个文件承担太多职责？
5. 有没有新增过深的 if-else 内容分支？
6. 如果用户换成完全不同主题，例如法律、物理、日语、创业、文学、医学，这套逻辑是否仍然合理？
7. 是否可以通过 prompt / schema / config / plugin 解决，而不是写死在代码里？

如果答案不确定，必须停止并重构。
## 数据存储

- SQLite 主库默认 `apps/api/data/openclass.sqlite3`，线上设 `OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3`。开 WAL，设合理 `busy_timeout`。
- 上传文件落盘到持久化目录（线上 `/var/lib/openclass/uploads/`），DB 只存 metadata、原始文件名、mime、大小、路径。
- 旧 `apps/api/data/store.json` 仅作首次迁移来源，导入后归档为 `store.migrated-*.json`，不再作运行存储。
- AI 输入输出走 `apps/api/data/logs/ai-usage.jsonl`，不入主业务表。

主要表（`SqliteCourseStore`）：

| 表 | 内容 |
| --- | --- |
| `course_packages` | 课程包标题、摘要、排序、当前打开状态 |
| `lessons` | lesson 基础信息、所属 package、当前文档、学习需求、教学指南 |
| `lesson_commits` | 历史快照、commit metadata、父 commit、分支名 |
| `lesson_branches` | 分支名、head commit、base commit |
| `course_graph_edges` | 课程图谱关系 |
| `resources` | 上传资料 metadata、抽取状态、文件路径 |
| `resource_chapters` | 资料章节 outline |
| `workspace_settings` | active package、打开标签页等全局 workspace 状态 |

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

## 不要做

- 不要在 router 里直接拼 SQL 或绕过 service 事务。
- 不要把 SQLite 文件、上传文件、日志放在 repo / `.next/` / 临时目录 / 会被部署覆盖的位置。
- 不要在线上手改 sqlite，除非已停服务并备份。
- 不要让多个独立后端进程同时写同一 sqlite。
- 不要在迁移到 SQLite 时顺手大改前端 UI；先收口存储与一致性。
- 不要为了单个 demo、单份资料、单个学科或单次测试，把特殊规则写进核心 service。
- 不要在 `lesson_factory.py`、`ai_workflow.py`、`openai_course_ai.py`、`resource_library.py`、`openai_realtime.py` 里继续堆具体学科、具体教材、具体语言的判断。
- 不要把固定讲义、固定 HTML、固定课程内容写进 factory 或 workflow。
- 不要用关键词堆叠模拟智能；优先用用户目标、资料上下文、结构化 schema 和 LLM 判断。
## 风格

- 注释只解释非显而易见的意图或约束，不复述代码。
- 不主动新建 README / 文档；扩充本指南或对应 README 即可。
