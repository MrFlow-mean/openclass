import Link from "next/link";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Boxes,
  Code2,
  Database,
  FileText,
  GitBranchPlus,
  GitPullRequest,
  Route,
  ServerCog,
  ShieldCheck,
  Workflow,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";

const GITHUB_REPOSITORY_URL = "https://github.com/MrFlow-mean/openclass";

const architectureSections = [
  {
    title: "Web 前端",
    icon: Code2,
    body: "Next.js App Router 承载学习首页、课程工作台、个人主页、热门课程和技术文档页面。组件层负责交互组合，API client 统一放在 src/lib。",
    items: ["src/app 负责路由入口", "src/components 承载产品级视图", "src/hooks 收拢工作台副作用"],
  },
  {
    title: "API 后端",
    icon: ServerCog,
    body: "FastAPI 负责 workspace、documents、chat、resources、realtime 等接口。router 只组装请求，事务和状态更新留在 services。",
    items: ["app/main.py 组装应用", "app/routers 暴露 HTTP 边界", "app/services 执行业务逻辑"],
  },
  {
    title: "数据与文件",
    icon: Database,
    body: "本地运行数据默认写入 apps/api/data，SQLite 保存课程、资料、历史和账号状态，上传与导出目录应指向持久化位置。",
    items: ["openclass.sqlite3 是默认主库", "uploads 与 exports 不进 Git", "ai-usage.jsonl 记录模型调用"],
  },
  {
    title: "AI 协作边界",
    icon: Workflow,
    body: "默认工作流按角色分工推进：Chatbot 承接可见对话，BoardEditor 写右侧文档，Resolver 定位板书或资料证据。",
    items: ["先判断任务与状态", "再定位目标和构造上下文", "最后执行角色并持久化历史"],
  },
] as const;

const workflowSteps = [
  ["TurnDecision", "判断本轮请求属于普通对话、板书生成、局部讲解、编辑、资料问答还是互动任务。"],
  ["ResolveTarget", "定位板书、选区、资料证据或对话上下文；不确定时回到澄清。"],
  ["BuildContext", "只构造本轮需要的最小上下文，避免无关资料污染模型输入。"],
  ["ExecuteRole", "由唯一主角色执行动作，保持 Chatbot、BoardEditor、Resolver 权责清晰。"],
  ["PersistHistory", "写入 lesson commit、任务版本、事件和可追踪 metadata。"],
  ["UpdateRequirement", "记录需求清单或板书任务清单的变化、消费、失败和取消。"],
] as const;

const guardrails = [
  "核心路径只处理通用学习能力、内容形态、资料结构、用户意图、文档操作和模型调用。",
  "不得为具体学科、教材、考试、demo 或单次测试样例写默认分支。",
  "右侧板书正文以 Markdown / 普通文本为事实来源，HTML 只能由系统渲染层派生。",
  "course-studio.tsx 只做顶层组合，复杂 state、effect、realtime、editor 与 model selection 逻辑继续拆到边界模块。",
] as const;

const verificationCommands = [
  ["npm run lint:web", "前端 lint"],
  ["npm run typecheck:web", "TypeScript 类型检查"],
  ["npm run test:api", "后端 pytest"],
  ["npm run build:web", "Next.js 生产构建"],
  ["npm run verify", "完整仓库门禁"],
] as const;

export function TechnicalDocsPage() {
  return (
    <main className="min-h-screen bg-[#f8fafc] text-slate-950">
      <section className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-8 sm:px-8 lg:flex-row lg:items-end lg:justify-between lg:py-12">
          <div className="max-w-3xl">
            <Link
              href="/"
              className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-slate-950"
            >
              <ArrowLeft className="h-4 w-4" />
              返回开放课堂
            </Link>
            <div className="mt-8 flex items-center gap-4">
              <BrandMark className="h-14 w-14 rounded-xl border border-slate-200 bg-white shadow-sm" priority size={112} />
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">OpenClass Docs</p>
                <h1 className="mt-2 text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">
                  技术文档介绍
                </h1>
              </div>
            </div>
            <p className="mt-6 max-w-2xl text-base leading-8 text-slate-600">
              这里介绍 OpenClass 当前的工程结构、运行边界、AI 协作协议和验证方式。它是项目技术入口页，不承载学科模板、固定讲义或 demo 内容。
            </p>
          </div>

          <a
            href={GITHUB_REPOSITORY_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-11 w-fit items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
          >
            查看 GitHub
            <ArrowUpRight className="h-4 w-4" />
          </a>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-6 px-5 py-8 sm:px-8 lg:grid-cols-[17rem_minmax(0,1fr)] lg:py-10">
        <aside className="h-fit border-b border-slate-200 pb-5 lg:sticky lg:top-6 lg:border-b-0 lg:pb-0">
          <nav aria-label="技术文档目录" className="grid gap-2 text-sm font-medium text-slate-600">
            <a href="#architecture" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              架构总览
            </a>
            <a href="#workflow" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              AI 工作流
            </a>
            <a href="#guardrails" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              工程边界
            </a>
            <a href="#verification" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              验证命令
            </a>
          </nav>
        </aside>

        <div className="min-w-0 space-y-10">
          <section id="architecture" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <Boxes className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">架构总览</h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {architectureSections.map((section) => {
                const Icon = section.icon;
                return (
                  <article key={section.title} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-950 text-white">
                        <Icon className="h-5 w-5" />
                      </span>
                      <h3 className="text-base font-semibold text-slate-950">{section.title}</h3>
                    </div>
                    <p className="mt-4 text-sm leading-7 text-slate-600">{section.body}</p>
                    <ul className="mt-4 space-y-2 text-sm text-slate-600">
                      {section.items.map((item) => (
                        <li key={item} className="flex gap-2">
                          <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </article>
                );
              })}
            </div>

            <div className="mt-5 rounded-lg border border-slate-200 bg-slate-950 p-5 text-slate-100 shadow-sm">
              <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
                <FileText className="h-4 w-4" />
                仓库地图
              </div>
              <pre className="overflow-x-auto text-sm leading-7 text-slate-200">
{`.
├── apps/api        FastAPI 后端、services、routers、本地 data
├── apps/web        Next.js 前端、App Router、组件和 hooks
├── launcher        本地桌面入口 HTML
├── package.json    workspace 脚本和验证命令
├── pyproject.toml  Python 依赖和 pytest 配置
└── AGENTS.md       AI 协作与工程边界`}
              </pre>
            </div>
          </section>

          <section id="workflow" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <Route className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">AI 工作流</h2>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <ol className="grid gap-4">
                {workflowSteps.map(([name, description], index) => (
                  <li key={name} className="grid gap-3 sm:grid-cols-[9rem_minmax(0,1fr)]">
                    <div className="flex items-center gap-2">
                      <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-700">
                        {index + 1}
                      </span>
                      <span className="font-mono text-sm font-semibold text-slate-950">{name}</span>
                    </div>
                    <p className="text-sm leading-7 text-slate-600">{description}</p>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section id="guardrails" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <ShieldCheck className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">工程边界</h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {guardrails.map((item) => (
                <div key={item} className="rounded-lg border border-slate-200 bg-white p-5 text-sm leading-7 text-slate-600 shadow-sm">
                  {item}
                </div>
              ))}
            </div>
          </section>

          <section id="verification" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <GitPullRequest className="h-5 w-5 text-slate-500" />
              <h2 className="text-2xl font-semibold tracking-tight text-slate-950">验证命令</h2>
            </div>
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
              {verificationCommands.map(([command, description]) => (
                <div key={command} className="grid gap-2 border-b border-slate-200 px-5 py-4 last:border-b-0 sm:grid-cols-[13rem_minmax(0,1fr)]">
                  <code className="rounded-md bg-slate-100 px-2 py-1 font-mono text-sm font-semibold text-slate-900">
                    {command}
                  </code>
                  <p className="text-sm text-slate-600">{description}</p>
                </div>
              ))}
            </div>

            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                href="/"
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
              >
                <BookOpen className="h-4 w-4" />
                回到产品主页
              </Link>
              <a
                href={GITHUB_REPOSITORY_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                <GitBranchPlus className="h-4 w-4" />
                打开源码仓库
              </a>
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
