"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  BookText,
  Eye,
  FolderClosed,
  GraduationCap,
  Heart,
  MessageCircle,
  Search,
} from "lucide-react";

import {
  FOLLOWED_UPDATE_KIND_LABELS,
  buildFollowedCourseUpdateItems,
  creatorAvatarUrl,
  updateCoverUrl,
  type FollowedCourseUpdate,
  type FollowedCourseUpdateItem,
} from "@/lib/following";

type FollowedFeedFilter = "all" | FollowedCourseUpdate["updateKind"];

const feedFilters: Array<{ id: FollowedFeedFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "new_lesson", label: "新课" },
  { id: "course_revision", label: "更新" },
  { id: "resource_added", label: "资料" },
  { id: "note_added", label: "笔记" },
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

function feedItemMatchesSearch(item: FollowedCourseUpdateItem, normalizedQuery: string) {
  const { creator, update } = item;

  return (
    !normalizedQuery ||
    [
      creator.name,
      creator.handle,
      creator.field,
      update.courseTitle,
      update.moduleTitle,
      update.summary,
      update.tags.join(" "),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedQuery)
  );
}

function updateTone(kind: FollowedCourseUpdate["updateKind"]) {
  switch (kind) {
    case "resource_added":
      return "bg-emerald-500";
    case "note_added":
      return "bg-sky-500";
    case "course_revision":
      return "bg-rose-500";
    case "new_lesson":
    default:
      return "bg-stone-950";
  }
}

function updateLabelTone(kind: FollowedCourseUpdate["updateKind"]) {
  switch (kind) {
    case "resource_added":
      return "bg-emerald-50 text-emerald-700";
    case "note_added":
      return "bg-sky-50 text-sky-700";
    case "course_revision":
      return "bg-rose-50 text-rose-700";
    case "new_lesson":
    default:
      return "bg-stone-100 text-stone-700";
  }
}

export function FollowingFeedContent() {
  const [feedFilter, setFeedFilter] = useState<FollowedFeedFilter>("all");
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const feedItems = useMemo(() => buildFollowedCourseUpdateItems(), []);
  const visibleFeedItems = feedItems.filter((item) => {
    const matchesFilter = feedFilter === "all" || item.update.updateKind === feedFilter;
    return matchesFilter && feedItemMatchesSearch(item, normalizedQuery);
  });

  return (
    <div className="mx-auto max-w-5xl">
      <section className="rounded-[30px] border border-white/70 bg-[linear-gradient(180deg,#ffffff_0%,#faf8f2_100%)] p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] sm:p-7">
        <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-stone-950">
              <Activity className="h-5 w-5" />
              Following
            </h1>
            <p className="mt-1 text-sm text-stone-500">只显示你关注的他人课程项目更新，不混入本地工作台提交。</p>
          </div>

          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="relative min-w-0 md:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索创作者、课程或更新内容"
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

        <div className="space-y-4">
          {visibleFeedItems.length ? (
            visibleFeedItems.map((item) => renderFeedCard(item))
          ) : (
            <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-8 text-sm text-stone-500">
              没有找到匹配的他人项目更新。
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function renderFeedCard(item: FollowedCourseUpdateItem) {
  const { creator, update } = item;
  const isResourceUpdate = update.updateKind === "resource_added";

  return (
    <article
      key={update.id}
      className="rounded-[24px] border border-stone-200 bg-white p-4 shadow-[0_10px_30px_rgba(15,23,42,0.05)] sm:p-5"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 gap-3">
          <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-2xl border border-stone-200 bg-stone-100">
            <Image
              src={updateCoverUrl(update)}
              alt=""
              className="h-full w-full object-cover"
              width={56}
              height={56}
              unoptimized
            />
            <div
              className={clsx(
                "absolute bottom-1 right-1 flex h-6 w-6 items-center justify-center rounded-full text-white ring-2 ring-white",
                updateTone(update.updateKind)
              )}
            >
              {isResourceUpdate ? <FolderClosed className="h-3.5 w-3.5" /> : <BookText className="h-3.5 w-3.5" />}
            </div>
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Image
                src={creatorAvatarUrl(creator)}
                alt=""
                className="h-6 w-6 rounded-full border border-stone-200 bg-stone-100"
                width={24}
                height={24}
                unoptimized
              />
              <p className="truncate text-sm text-stone-600">
                <span className="font-semibold text-stone-950">{creator.name}</span> 发布了项目更新
              </p>
            </div>
            <p className="mt-1 text-xs text-stone-400">
              @{creator.handle} · {creator.field} · {formatRelativeTime(update.updatedAt)}
            </p>
          </div>
        </div>

        <span
          className={clsx(
            "w-fit rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
            updateLabelTone(update.updateKind)
          )}
        >
          {FOLLOWED_UPDATE_KIND_LABELS[update.updateKind]}
        </span>
      </div>

      <h2 className="mt-5 text-2xl font-semibold tracking-tight text-stone-950 sm:text-[2rem]">{update.courseTitle}</h2>

      <div className="mt-4 rounded-[22px] border border-stone-200 bg-stone-50/90 p-4">
        <div className="border-b border-stone-200 pb-3">
          <p className="text-base font-semibold text-stone-950">{update.moduleTitle}</p>
        </div>
        <p className="mt-3 text-sm leading-7 text-stone-600">{update.summary}</p>
      </div>

      <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap gap-2">
          {update.tags.map((tag) => (
            <span key={`${update.id}:${tag}`} className="rounded-full bg-stone-100 px-3 py-1 text-[11px] font-medium text-stone-500">
              {tag}
            </span>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs text-stone-500">
          <span className="inline-flex items-center gap-1">
            <GraduationCap className="h-3.5 w-3.5" />
            {update.lessonCount} 课
          </span>
          <span className="inline-flex items-center gap-1">
            <Eye className="h-3.5 w-3.5" />
            {update.views.toLocaleString("zh-CN")}
          </span>
          <span className="inline-flex items-center gap-1">
            <MessageCircle className="h-3.5 w-3.5" />
            {update.comments.toLocaleString("zh-CN")}
          </span>
          <span className="inline-flex items-center gap-1">
            <Heart className="h-3.5 w-3.5" />
            {update.likes.toLocaleString("zh-CN")}
          </span>
        </div>
      </div>

      <div className="mt-4 flex justify-end">
        <Link
          href="/"
          className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
        >
          发现更多项目
          <ArrowUpRight className="h-4 w-4" />
        </Link>
      </div>
    </article>
  );
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
