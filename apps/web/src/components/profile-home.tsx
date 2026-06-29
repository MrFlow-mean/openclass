"use client";

import Link from "next/link";
import { ArrowLeft, CircleUserRound, KeyRound, ShieldCheck } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { ProfileSettingsPanel } from "@/components/profile-settings-panel";

const profileNotes = [
  "个人页不再展示旧课程包、lesson 仓库和工作台跳转。",
  "账号、语言、显示偏好等基础设置继续保留。",
  "新产品工作链路确定后，再按新的数据模型接回个人空间。",
];

export function ProfileHome() {
  return (
    <main className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
          <div className="flex items-center gap-3">
            <BrandMark className="h-9 w-9 rounded-md" size={72} />
            <span className="text-sm font-semibold text-zinc-900">Account base</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/"
              className="inline-flex h-9 items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
            >
              <ArrowLeft className="h-4 w-4" />
              返回重建区
            </Link>
            <AccountMenu />
          </div>
        </div>
      </header>

      <section className="mx-auto grid max-w-6xl gap-6 px-5 py-8 lg:grid-cols-[0.85fr_1.15fr]">
        <aside className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-md bg-zinc-950 text-white">
              <CircleUserRound className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-zinc-950">账号基座</p>
              <p className="text-xs text-zinc-500">旧工作台仓库已从个人页移除</p>
            </div>
          </div>

          <div className="mt-6 space-y-3">
            {profileNotes.map((item) => (
              <div key={item} className="flex gap-3 rounded-md border border-zinc-200 bg-zinc-50 p-3 text-sm leading-6 text-zinc-600">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                <span>{item}</span>
              </div>
            ))}
          </div>
        </aside>

        <section className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm">
          <div className="mb-5 flex items-center gap-2 text-sm font-semibold text-zinc-900">
            <KeyRound className="h-4 w-4 text-sky-600" />
            基础设置
          </div>
          <ProfileSettingsPanel avatarUrl="/openclass-mark.png" favoriteCount={0} repositoryCount={0} />
        </section>
      </section>
    </main>
  );
}
