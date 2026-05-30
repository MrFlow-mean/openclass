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

- 域名：`https://open-classes.com`
- 服务器：`198.20.0.53`
- 登录：`ssh root@198.20.0.53`
- 反代与证书：Nginx，配置文件 `/etc/nginx/sites-available/openclass.conf`，证书由 Certbot 管理。
- 应用目录：`/opt/openclass`
- 当前代码：`/opt/openclass/repo`，指向 `/opt/openclass/releases/<release>`。
- 运行配置：`/opt/openclass/.env`，不要打印或提交密钥。
- 持久化数据：`/var/lib/openclass/`。
- 数据库：`/var/lib/openclass/openclass.sqlite3`（WAL，`busy_timeout=5000`）。
- 上传与导出目录放在 `/var/lib/openclass/uploads/`、`/var/lib/openclass/exports/`，不要放进仓库、`.next/` 或临时目录。
- 拓扑约束：单后端写入进程 + 文件级备份 + WAL；不允许多机/多进程同时写同一 sqlite。
- 服务：`openclass-api.service` 绑定 `127.0.0.1:8000`，`openclass-web.service` 绑定 `127.0.0.1:3000`。

本地部署前 gate：

```bash
npm run verify
```

同步本地环境变量到服务器：

```bash
scp .env root@198.20.0.53:/opt/openclass/.env
ssh root@198.20.0.53 'chmod 600 /opt/openclass/.env && if grep -q "^OPENCLASS_PUBLIC_ORIGIN=" /opt/openclass/.env; then sed -i "s#^OPENCLASS_PUBLIC_ORIGIN=.*#OPENCLASS_PUBLIC_ORIGIN=https://open-classes.com#" /opt/openclass/.env; else printf "\nOPENCLASS_PUBLIC_ORIGIN=https://open-classes.com\n" >> /opt/openclass/.env; fi && if grep -q "^OPENCLASS_WEB_ORIGIN=" /opt/openclass/.env; then sed -i "s#^OPENCLASS_WEB_ORIGIN=.*#OPENCLASS_WEB_ORIGIN=https://open-classes.com#" /opt/openclass/.env; else printf "\nOPENCLASS_WEB_ORIGIN=https://open-classes.com\n" >> /opt/openclass/.env; fi'
```

更新线上代码并重启：

```bash
ssh root@198.20.0.53
cd /opt/openclass/repo
npm ci
npm run build --workspace apps/web

systemctl restart openclass-api.service openclass-web.service
systemctl status openclass-api.service openclass-web.service --no-pager
```

仓库是私有仓库；若以后需要服务器自主拉取，再单独配置 GitHub deploy key。

仅重启现有服务：

```bash
ssh root@198.20.0.53 'systemctl restart openclass-api.service openclass-web.service'
```

仅重建前端（改域名或前端环境变量后）：

```bash
ssh root@198.20.0.53 'cd /opt/openclass/repo && npm run build --workspace apps/web && systemctl restart openclass-web.service'
```

Nginx 配置检查与重载：

```bash
ssh root@198.20.0.53 'nginx -t && systemctl reload nginx'
```

线上验证：

```bash
curl -fsSI https://open-classes.com/
curl -fsS https://open-classes.com/health
curl -fsS https://open-classes.com/api/ai-models
echo | openssl s_client -servername open-classes.com -connect open-classes.com:443 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

查看日志：

```bash
ssh root@198.20.0.53 'journalctl -u openclass-api.service -u openclass-web.service -n 100 --no-pager'
ssh root@198.20.0.53 'journalctl -u nginx -n 100 --no-pager'
```

写入异常先停服务，保留 sqlite、WAL、日志和上传文件证据，再回滚：

```bash
ssh root@198.20.0.53 'systemctl stop openclass-api.service openclass-web.service'
```

## 提交前

- 跑 `npm run verify`（或至少 `lint:web` + `typecheck:web` + 受影响的 `test:api`）。
- 不要提交 `.env`、`.venv/`、`apps/api/data/` 下的运行数据、`node_modules/`、`.next/`。


## 风格

- 注释只解释非显而易见的意图或约束，不复述代码。
- 不主动新建 README / 文档；扩充本指南或对应 README 即可。
