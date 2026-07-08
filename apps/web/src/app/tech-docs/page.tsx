import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Boxes,
  Code2,
  Database,
  GitBranchPlus,
  Route,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";

const GITHUB_REPOSITORY_URL = "https://github.com/MrFlow-mean/openclass";

const architectureSections = [
  {
    title: "Web 前端",
    icon: Code2,
    body: "Next.js App Router 承载学习首页、课程工作台、个人主页和热门课程；组件负责界面组合，API client 与纯工具放在 src/lib。",
  },
  {
    title: "API 后端",
    icon: TerminalSquare,
    body: "FastAPI 暴露 workspace、documents、chat 和 realtime 边界；router 处理 HTTP，业务状态和事务留在 services。",
  },
  {
    title: "数据与文件",
    icon: Database,
    body: "SQLite 保存课程、历史和账号状态；导出文件和 AI 调用日志都写入持久化目录，不进入代码仓库。",
  },
  {
    title: "AI 协作边界",
    icon: ShieldCheck,
    body: "Chatbot、BoardEditor、Resolver 和 Requirement Manager 按固定协议协作，避免把板书写入、定位和讲解授权混在一个自由回答里。",
  },
] as const;

const workflowSteps = [
  ["TurnDecision", "判断本轮请求类型和当前板书状态。"],
  ["ResolveTarget", "定位板书、选区、资料证据或对话上下文。"],
  ["BuildContext", "只构造本轮动作需要的最小上下文。"],
  ["ExecuteRole", "由唯一主角色执行写入、讲解、澄清或互动。"],
  ["PersistHistory", "写入 lesson commit、任务版本、事件和 metadata。"],
] as const;

const verificationCommands = [
  ["npm run lint:web", "前端 lint"],
  ["npm run typecheck:web", "TypeScript 类型检查"],
  ["npm run test:api", "后端 pytest"],
  ["npm run build:web", "Next.js 生产构建"],
  ["npm run verify", "提交前完整门禁"],
] as const;

export const metadata: Metadata = {
  title: "项目文档",
  description: "OpenClass 的工程结构、AI 协作边界、本地运行和验证命令说明。",
};

export default function TechDocsPage() {
  return (
    <main className="min-h-screen bg-[#f8fafc] text-slate-950">
      <section className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-7 px-5 py-8 sm:px-8 lg:flex-row lg:items-end lg:justify-between lg:py-12">
          <div className="max-w-3xl">
            <Link
              href="/home"
              className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-slate-950"
            >
              <ArrowLeft className="h-4 w-4" />
              返回学习首页
            </Link>
            <div className="mt-8 flex items-center gap-4">
              <BrandMark className="h-14 w-14 rounded-lg border border-slate-200 bg-white shadow-sm" priority size={112} />
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">OpenClass Docs</p>
                <h1 className="mt-2 text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">项目文档</h1>
              </div>
            </div>
            <p className="mt-6 max-w-2xl text-base leading-8 text-slate-600">
              这里汇总 OpenClass 当前的仓库结构、运行边界、AI 协作协议和验证方式。它是项目工程入口，不承载学科模板、固定讲义或 demo 内容。
            </p>
          </div>

          <a
            href={GITHUB_REPOSITORY_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-11 w-fit items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800"
          >
            打开 GitHub
            <ArrowUpRight className="h-4 w-4" />
          </a>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-6 px-5 py-8 sm:px-8 lg:grid-cols-[16rem_minmax(0,1fr)] lg:py-10">
        <aside className="h-fit border-b border-slate-200 pb-5 lg:sticky lg:top-6 lg:border-b-0 lg:pb-0">
          <nav aria-label="项目文档目录" className="grid gap-2 text-sm font-medium text-slate-600">
            <a href="#architecture" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              架构总览
            </a>
            <a href="#workflow" className="rounded-lg px-3 py-2 hover:bg-white hover:text-slate-950">
              AI 工作流
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
                  </article>
                );
              })}
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
                      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-xs font-semibold text-slate-700">
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

          <section id="verification" className="scroll-mt-8">
            <div className="mb-5 flex items-center gap-3">
              <TerminalSquare className="h-5 w-5 text-slate-500" />
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
                href="/home"
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
              >
                <BookOpen className="h-4 w-4" />
                回到学习首页
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
