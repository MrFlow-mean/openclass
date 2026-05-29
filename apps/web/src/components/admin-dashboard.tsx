"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, Database, LoaderCircle, ShieldCheck, UsersRound } from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { api } from "@/lib/api";
import { userAccountLabel } from "@/lib/account";
import type { AdminOverview } from "@/types";

function formatDate(value: string | null | undefined, locale: string, fallback: string) {
  if (!value) {
    return fallback;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return fallback;
  }
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function AdminDashboard() {
  const { texts: txt, intlLocale } = useInterfaceLanguage();
  const a = txt.adminDashboard;
  const c = txt.common;
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let disposed = false;

    async function loadOverview() {
      try {
        const payload = await api.getAdminOverview();
        if (!disposed) {
          setOverview(payload);
          setError(null);
        }
      } catch (loadError) {
        if (!disposed) {
          setError(loadError instanceof Error ? loadError.message : a.loadErrorFallback);
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
  }, [a.loadErrorFallback]);

  const statCards = overview
    ? [
        { label: a.statsUsers, value: overview.stats.users, icon: UsersRound },
        { label: a.statsAdmins, value: overview.stats.admins, icon: ShieldCheck },
        { label: a.statsPackages, value: overview.stats.packages, icon: BookOpen },
        { label: a.statsLessons, value: overview.stats.lessons, icon: Database },
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
            {c.brand}
          </Link>
          <AccountMenu />
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-7">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">{a.consoleEyebrow}</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-stone-950">{a.title}</h1>
        </div>

        {isLoading ? (
          <div className="rounded-lg border border-stone-200 bg-white p-8 text-sm text-stone-500">
            <LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />
            {a.loading}
          </div>
        ) : error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-6 text-sm leading-6 text-rose-700">
            {error}
            <div className="mt-4">
              <Link
                href="/login"
                className="inline-flex h-10 items-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
              >
                {c.signIn}
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
              <div className="border-b border-stone-200 px-5 py-4">
                <h2 className="text-base font-semibold text-stone-950">{a.userManagement}</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[46rem] text-left text-sm">
                  <thead className="bg-stone-50 text-xs font-semibold uppercase tracking-[0.16em] text-stone-500">
                    <tr>
                      <th className="px-5 py-3">{a.account}</th>
                      <th className="px-5 py-3">{a.role}</th>
                      <th className="px-5 py-3">{a.createdAt}</th>
                      <th className="px-5 py-3">{a.lastLogin}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-200">
                    {overview.users.map((user) => (
                      <tr key={user.id}>
                        <td className="px-5 py-4 font-medium text-stone-950">{userAccountLabel(user)}</td>
                        <td className="px-5 py-4">
                          <span className="rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs font-semibold text-stone-600">
                            {user.role === "admin" ? c.admin : c.user}
                          </span>
                        </td>
                        <td className="px-5 py-4 text-stone-600">{formatDate(user.created_at, intlLocale, c.never)}</td>
                        <td className="px-5 py-4 text-stone-600">{formatDate(user.last_login_at, intlLocale, c.never)}</td>
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
