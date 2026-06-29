"use client";

import Link from "next/link";
import { ArrowUpRight, Boxes, CheckCircle2, GitBranch, MessageSquareText, Settings2, Trash2 } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";

const removedAreas = [
  "旧课程工作台页面、课程标签页和右侧文档编辑工作流",
  "旧 workspace 包/lesson 创建、移动、删除和排序入口",
  "旧白板任务、学习需求收集和互动编排界面",
  "旧工作流本地缓存与实验目录",
];

const retainedAreas = [
  "登录、游客会话和账号基础能力",
  "模型目录、Codex provider 和基础 AI 调用能力",
  "Chatbot 基础对话入口的后端骨架",
  "资料解析、文档模型和历史数据结构，供新链路参考",
];

export function LearningHome() {
  return (
    <main className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
          <div className="flex items-center gap-3">
            <BrandMark className="h-9 w-9 rounded-md" size={72} />
            <span className="text-sm font-semibold text-zinc-900">Product workflow reset</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/tech-docs"
              className="inline-flex h-9 items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
            >
              <Settings2 className="h-4 w-4" />
              技术文档
            </Link>
            <AccountMenu />
          </div>
        </div>
      </header>

      <section className="mx-auto grid max-w-6xl gap-6 px-5 py-8 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
            <GitBranch className="h-4 w-4" />
            当前分支正在重建产品工作链路
          </div>
          <h1 className="mt-4 text-3xl font-semibold tracking-normal text-zinc-950">
            旧链路入口已下线，新产品工作流从这里重新设计。
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-zinc-600">
            这一页现在只保留重建状态和基座入口，避免旧课程工作台继续影响新链路判断。后续新功能会按新的状态对象、角色边界、写入权限和审计合同重新接入。
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/profile"
              className="inline-flex h-10 items-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-medium text-white hover:bg-zinc-800"
            >
              <ArrowUpRight className="h-4 w-4" />
              查看账号基座
            </Link>
            <Link
              href="/tech-docs"
              className="inline-flex h-10 items-center gap-2 rounded-md border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-800 hover:bg-zinc-100"
            >
              <MessageSquareText className="h-4 w-4" />
              查看项目说明
            </Link>
          </div>
        </div>

        <div className="grid gap-6">
          <section className="rounded-lg border border-zinc-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-900">
              <Trash2 className="h-4 w-4 text-rose-600" />
              已清掉的旧链路
            </div>
            <ul className="mt-4 space-y-3 text-sm leading-6 text-zinc-600">
              {removedAreas.map((item) => (
                <li key={item} className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-lg border border-zinc-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-900">
              <Boxes className="h-4 w-4 text-sky-600" />
              暂时保留的基座
            </div>
            <ul className="mt-4 space-y-3 text-sm leading-6 text-zinc-600">
              {retainedAreas.map((item) => (
                <li key={item} className="flex gap-3">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </section>
    </main>
  );
}
