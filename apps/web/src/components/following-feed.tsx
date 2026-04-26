"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  BookText,
  FolderClosed,
  LoaderCircle,
  Search,
} from "lucide-react";

import { api } from "@/lib/api";
import {
  buildRecentFeed,
  type RecentFeedFilter,
  type RecentFeedItem,
} from "@/lib/recent-feed";
import type { WorkspaceState } from "@/types";

const feedFilters = [
  { id: "all" as const, label: "全部" },
  { id: "commit" as const, label: "我的" },
  { id: "resource" as const, label: "热门" },
];

function formatRelativeTime(value: string | Date | null | undefined) {
  if (!value) {
    return "刚刚";
  }

  const date = value instanceof Date ? value : new Date(value);
  const timestamp = date.getTime();

  if (Number.isNaN(timestamp)) {
    return "刚刚";
  }

  const minutes = Math.floor((Date.now() - timestamp) / 60000);
  if (minutes <= 0) {
    return "刚刚";
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }

  const days = Math.floor(hours / 24);
  if (days < 7) {
    return `${days} 天前`;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

function feedItemMatchesSearch(item: RecentFeedItem, normalizedQuery: string) {
  return (
    !normalizedQuery ||
    [
      item.actor,
      item.action,
      item.title,
      item.detailTitle,
      item.detailBody,
      item.pills.join(" "),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedQuery)
  );
}

export function FollowingFeedContent() {
  const router = useRouter();
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedFilter, setFeedFilter] = useState<RecentFeedFilter>("all");
  const [query, setQuery] = useState("");
  const [openingLessonId, setOpeningLessonId] = useState<string | null>(null);
  const normalizedQuery = query.trim().toLowerCase();

  useEffect(() => {
    let isMounted = true;

    api
      .getWorkspace()
      .then((payload) => {
        if (isMounted) {
          setWorkspaceState(payload);
          setError(null);
        }
      })
      .catch((loadError) => {
        if (isMounted) {
          setError(loadError instanceof Error ? loadError.message : "加载动态失败");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const feedItems = useMemo(() => {
    const packages = workspaceState?.packages ?? [];
    const feedLessons = packages.flatMap((packageItem) =>
      packageItem.lessons.map((lesson) => ({
        lesson,
        packageTitle: packageItem.title,
      }))
    );
    const feedResources = packages.flatMap((packageItem) =>
      packageItem.resources.map((resource) => ({
        resource,
        packageTitle: packageItem.title,
      }))
    );

    return buildRecentFeed(feedLessons, feedResources);
  }, [workspaceState]);
  const visibleFeedItems = feedItems.filter((item) => {
    const matchesFilter = feedFilter === "all" || item.kind === feedFilter;
    return matchesFilter && feedItemMatchesSearch(item, normalizedQuery);
  });

  async function handleOpenLesson(lessonId: string) {
    setOpeningLessonId(lessonId);

    try {
      await api.openLesson(lessonId);
      router.push("/studio");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "打开课程失败");
    } finally {
      setOpeningLessonId(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <section className="rounded-[30px] border border-white/70 bg-[linear-gradient(180deg,#ffffff_0%,#faf8f2_100%)] p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] sm:p-7">
        <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-stone-950">
              <Activity className="h-5 w-5" />
              Feed
            </h1>
            <p className="mt-1 text-sm text-stone-500">
              最近的课程提交、资料收录和工作台推进会按时间排在这里。
            </p>
          </div>

          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="relative min-w-0 md:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索课程、资料或分支"
                className="w-full rounded-full border border-stone-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {feedFilters.map((filter) => {
                const isActive = feedFilter === filter.id;
                return (
                  <button
                    key={filter.id}
                    type="button"
                    onClick={() => setFeedFilter(filter.id)}
                    className={clsx(
                      "rounded-full border px-4 py-2 text-xs font-semibold transition",
                      isActive
                        ? "border-stone-950 bg-stone-950 text-white"
                        : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:text-stone-950"
                    )}
                  >
                    {filter.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {error ? (
          <div className="mb-4 rounded-[20px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}

        <div className="space-y-4">
          {isLoading ? (
            Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="rounded-[24px] border border-stone-200 bg-white p-5">
                <div className="h-4 w-1/3 animate-pulse rounded bg-stone-200" />
                <div className="mt-4 h-8 w-2/3 animate-pulse rounded bg-stone-100" />
                <div className="mt-4 h-20 w-full animate-pulse rounded-[20px] bg-stone-100" />
              </div>
            ))
          ) : visibleFeedItems.length ? (
            visibleFeedItems.map((item) => renderFeedCard(item))
          ) : (
            <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-8 text-sm text-stone-500">
              还没有可以展示的更新。新建课程、编辑文稿或上传资料后，这里会自动变成最近活动流。
            </div>
          )}
        </div>
      </section>
    </div>
  );

  function renderFeedCard(item: RecentFeedItem) {
    const buttonBusy = item.lessonId ? openingLessonId === item.lessonId : false;

    return (
      <article
        key={item.id}
        className="rounded-[24px] border border-stone-200 bg-white p-4 shadow-[0_10px_30px_rgba(15,23,42,0.05)] sm:p-5"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 gap-3">
            <div
              className={clsx(
                "mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl text-white",
                item.kind === "commit" ? "bg-rose-500" : "bg-emerald-500"
              )}
            >
              {item.kind === "commit" ? <BookText className="h-4 w-4" /> : <FolderClosed className="h-4 w-4" />}
            </div>

            <div className="min-w-0">
              <p className="text-sm text-stone-600">
                <span className="font-semibold text-stone-950">{item.actor}</span> {item.action}
              </p>
              <p className="mt-1 text-xs text-stone-400">{formatRelativeTime(item.timestamp)}</p>
            </div>
          </div>

          <span className="rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-stone-400">
            {item.kind === "commit" ? "Commit" : "Resource"}
          </span>
        </div>

        <h2 className="mt-5 text-2xl font-semibold tracking-tight text-stone-950 sm:text-[2rem]">
          {item.title}
        </h2>

        <div className="mt-4 rounded-[22px] border border-stone-200 bg-stone-50/90 p-4">
          <div className="border-b border-stone-200 pb-3">
            <p className="text-base font-semibold text-stone-950">{item.detailTitle}</p>
          </div>
          <p className="mt-3 text-sm leading-7 text-stone-600">{item.detailBody}</p>
        </div>

        <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {item.pills.map((pill, pillIndex) => (
              <span
                key={`${item.id}:pill:${pillIndex}:${pill}`}
                className="rounded-full bg-stone-100 px-3 py-1 text-[11px] font-medium text-stone-500"
              >
                {pill}
              </span>
            ))}
          </div>

          {item.lessonId ? (
            <button
              type="button"
              onClick={() => void handleOpenLesson(item.lessonId!)}
              className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              {buttonBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
              <span>进入工作台</span>
              {!buttonBusy ? <ArrowUpRight className="h-4 w-4" /> : null}
            </button>
          ) : (
            <Link
              href="/studio"
              className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              查看资料入口
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          )}
        </div>
      </article>
    );
  }
}

export function FollowingFeed() {
  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-40 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            产品主页
          </Link>

          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            <BookOpen className="h-4 w-4" />
            Learning Hub
          </Link>
        </div>
      </header>

      <div className="px-4 py-6 sm:px-6">
        <FollowingFeedContent />
      </div>
    </main>
  );
}
