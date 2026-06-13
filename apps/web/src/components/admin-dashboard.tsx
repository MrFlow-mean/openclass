"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, Check, Database, LoaderCircle, ShieldAlert, ShieldCheck, UsersRound, X } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { api } from "@/lib/api";
import { userAccountLabel } from "@/lib/account";
import type { AdminOverview, ResourceCopyrightAppealDecision, ResourceCopyrightAppealView } from "@/types";

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "从未";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "从未";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function AdminDashboard() {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [appeals, setAppeals] = useState<ResourceCopyrightAppealView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [resolvingAppealId, setResolvingAppealId] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    async function loadOverview() {
      try {
        const [payload, appealPayload] = await Promise.all([
          api.getAdminOverview(),
          api.getAdminCopyrightAppeals(),
        ]);
        if (!disposed) {
          setOverview(payload);
          setAppeals(appealPayload);
          setError(null);
        }
      } catch (loadError) {
        if (!disposed) {
          setError(loadError instanceof Error ? loadError.message : "加载管理员后台失败");
        }
      } finally {
        if (!disposed) {
          setIsLoading(false);
        }
      }
    }

    void loadOverview();

    return () => {
      disposed = true;
    };
  }, []);

  async function resolveAppeal(appealId: string, decision: ResourceCopyrightAppealDecision) {
    if (resolvingAppealId) {
      return;
    }
    setResolvingAppealId(appealId);
    setActionError(null);
    try {
      await api.resolveAdminCopyrightAppeal(appealId, decision);
      setAppeals((current) => current.filter((appeal) => appeal.id !== appealId));
    } catch (resolveError) {
      setActionError(resolveError instanceof Error ? resolveError.message : "处理申诉失败");
    } finally {
      setResolvingAppealId(null);
    }
  }

  const statCards = overview
    ? [
        { label: "用户", value: overview.stats.users, icon: UsersRound },
        { label: "管理员", value: overview.stats.admins, icon: ShieldCheck },
        { label: "课程包", value: overview.stats.packages, icon: BookOpen },
        { label: "课程", value: overview.stats.lessons, icon: Database },
      ]
    : [];

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            <BrandMark alt="" className="h-5 w-5 rounded bg-white" size={40} />
            开放课堂
          </Link>
          <AccountMenu />
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-7">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">Admin Console</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-stone-950">管理员后台</h1>
        </div>

        {isLoading ? (
          <div className="rounded-lg border border-stone-200 bg-white p-8 text-sm text-stone-500">
            <LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />
            正在加载后台数据
          </div>
        ) : error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-6 text-sm leading-6 text-rose-700">
            {error}
            <div className="mt-4">
              <Link
                href="/login"
                className="inline-flex h-10 items-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
              >
                去登录
              </Link>
            </div>
          </div>
        ) : overview ? (
          <div className="space-y-7">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {statCards.map((card) => {
                const Icon = card.icon;
                return (
                  <article key={card.label} className="rounded-lg border border-stone-200 bg-white p-5">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-stone-500">{card.label}</p>
                      <Icon className="h-4 w-4 text-stone-400" />
                    </div>
                    <p className="mt-4 text-3xl font-semibold tracking-tight text-stone-950">{card.value}</p>
                  </article>
                );
              })}
            </div>

            <section className="overflow-hidden rounded-lg border border-stone-200 bg-white">
              <div className="flex items-center justify-between gap-3 border-b border-stone-200 px-5 py-4">
                <div>
                  <h2 className="text-base font-semibold text-stone-950">资料公开申诉</h2>
                  <p className="mt-1 text-xs text-stone-500">{appeals.length} 个待处理</p>
                </div>
                <ShieldAlert className="h-4 w-4 text-stone-400" />
              </div>
              {actionError ? (
                <div className="border-b border-rose-100 bg-rose-50 px-5 py-3 text-sm text-rose-700">{actionError}</div>
              ) : null}
              {appeals.length ? (
                <div className="divide-y divide-stone-200">
                  {appeals.map((appeal) => (
                    <article key={appeal.id} className="px-5 py-4">
                      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-stone-950">{appeal.resource_name || appeal.resource_id}</p>
                          <p className="mt-1 text-xs text-stone-500">
                            {appeal.owner_label} · {formatDate(appeal.created_at)}
                          </p>
                          {appeal.resource_audit.signals.length ? (
                            <p className="mt-2 text-xs text-stone-500">{appeal.resource_audit.signals.join(" · ")}</p>
                          ) : null}
                          {appeal.message || appeal.evidence_text ? (
                            <p className="mt-3 max-w-3xl text-sm leading-6 text-stone-700">
                              {[appeal.message, appeal.evidence_text].filter(Boolean).join(" ")}
                            </p>
                          ) : null}
                          {appeal.resource_audit.evidence_urls.length ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {appeal.resource_audit.evidence_urls.slice(0, 3).map((url) => (
                                <a
                                  key={url}
                                  href={url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="max-w-xs truncate rounded-md border border-stone-200 px-2.5 py-1 text-xs font-medium text-stone-600 transition hover:border-stone-300 hover:text-stone-950"
                                >
                                  {url}
                                </a>
                              ))}
                            </div>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <button
                            type="button"
                            onClick={() => void resolveAppeal(appeal.id, "approved")}
                            disabled={resolvingAppealId === appeal.id}
                            className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-emerald-700 px-3 text-xs font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {resolvingAppealId === appeal.id ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                            批准
                          </button>
                          <button
                            type="button"
                            onClick={() => void resolveAppeal(appeal.id, "rejected")}
                            disabled={resolvingAppealId === appeal.id}
                            className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 text-xs font-semibold text-stone-700 transition hover:border-stone-300 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <X className="h-4 w-4" />
                            拒绝
                          </button>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="px-5 py-8 text-sm text-stone-500">暂无待处理申诉</div>
              )}
            </section>

            <section className="overflow-hidden rounded-lg border border-stone-200 bg-white">
              <div className="border-b border-stone-200 px-5 py-4">
                <h2 className="text-base font-semibold text-stone-950">用户管理</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[46rem] text-left text-sm">
                  <thead className="bg-stone-50 text-xs font-semibold uppercase tracking-[0.16em] text-stone-500">
                    <tr>
                      <th className="px-5 py-3">账号</th>
                      <th className="px-5 py-3">权限</th>
                      <th className="px-5 py-3">注册时间</th>
                      <th className="px-5 py-3">最近登录</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-200">
                    {overview.users.map((user) => (
                      <tr key={user.id}>
                        <td className="px-5 py-4 font-medium text-stone-950">{userAccountLabel(user)}</td>
                        <td className="px-5 py-4">
                          <span className="rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs font-semibold text-stone-600">
                            {user.role === "admin" ? "管理员" : "用户"}
                          </span>
                        </td>
                        <td className="px-5 py-4 text-stone-600">{formatDate(user.created_at)}</td>
                        <td className="px-5 py-4 text-stone-600">{formatDate(user.last_login_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}
      </section>
    </main>
  );
}
