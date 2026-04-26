"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import {
  ArrowLeft,
  Bell,
  BookOpen,
  GraduationCap,
  Heart,
  MessageCircle,
  MoreHorizontal,
  Search,
  Share2,
  Sparkles,
  UsersRound,
} from "lucide-react";

import {
  FOLLOWED_COURSE_UPDATES,
  FOLLOWED_CREATORS,
  type FollowedCourseUpdate,
  type FollowedCreator,
  creatorAvatarUrl,
  updateCoverUrl,
} from "@/lib/following";
import { formatCompactNumber } from "@/lib/open-courses";

function formatRelativeTime(value: string) {
  const date = new Date(value);
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

function updateKindLabel(kind: FollowedCourseUpdate["updateKind"]) {
  return {
    new_lesson: "新增课程",
    course_revision: "课程更新",
    resource_added: "资料上新",
    live_note: "直播笔记",
  }[kind];
}

function getCreatorById(creatorId: string) {
  return FOLLOWED_CREATORS.find((creator) => creator.id === creatorId) ?? FOLLOWED_CREATORS[0];
}

function updateMatchesSearch(update: FollowedCourseUpdate, creator: FollowedCreator, normalizedQuery: string) {
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

export function FollowingFeedContent() {
  const [selectedCreatorId, setSelectedCreatorId] = useState<"all" | string>("all");
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();

  const sortedUpdates = useMemo(
    () =>
      [...FOLLOWED_COURSE_UPDATES].sort(
        (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime()
      ),
    []
  );
  const latestCreatorUpdates = useMemo(
    () =>
      FOLLOWED_CREATORS.map((creator) => {
        const update = sortedUpdates.find((candidate) => candidate.creatorId === creator.id);
        return update ? { creator, update } : null;
      })
        .filter((item): item is { creator: FollowedCreator; update: FollowedCourseUpdate } => Boolean(item))
        .sort((left, right) => new Date(right.update.updatedAt).getTime() - new Date(left.update.updatedAt).getTime()),
    [sortedUpdates]
  );
  const visibleCreatorFeed = latestCreatorUpdates.filter(({ update, creator }) =>
    updateMatchesSearch(update, creator, normalizedQuery)
  );
  const visibleUpdates = sortedUpdates.filter((update) => {
    const creator = getCreatorById(update.creatorId);
    const matchesCreator = selectedCreatorId === "all" || update.creatorId === selectedCreatorId;
    const matchesSearch = updateMatchesSearch(update, creator, normalizedQuery);

    return matchesCreator && matchesSearch;
  });
  const selectedCreator =
    selectedCreatorId === "all" ? null : FOLLOWED_CREATORS.find((creator) => creator.id === selectedCreatorId) ?? null;
  const totalUnreadCount = FOLLOWED_CREATORS.reduce((sum, creator) => sum + creator.unreadCount, 0);
  const latestUpdate = visibleUpdates[0] ?? null;

  return (
      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_18rem]">
        <aside className="h-fit rounded-lg border border-stone-200 bg-white/90 p-3 shadow-[0_12px_28px_rgba(15,23,42,0.04)] lg:sticky lg:top-24">
          <div className="mb-3 flex items-center justify-between px-2">
            <h1 className="flex items-center gap-2 text-base font-semibold text-stone-950">
              <UsersRound className="h-4 w-4" />
              关注列表
            </h1>
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs font-semibold text-stone-500">
              {FOLLOWED_CREATORS.length}
            </span>
          </div>

          <button
            type="button"
            onClick={() => setSelectedCreatorId("all")}
            className={clsx(
              "mb-2 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition",
              selectedCreatorId === "all"
                ? "bg-rose-50 text-stone-950 ring-1 ring-rose-100"
                : "text-stone-700 hover:bg-stone-50 hover:text-stone-950"
            )}
          >
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-rose-500 text-white">
              <Sparkles className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">全部动态</p>
              <p className="mt-0.5 text-xs text-stone-500">{latestCreatorUpdates.length} 位创作者最近更新</p>
            </div>
            {totalUnreadCount ? <span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> : null}
          </button>

          <div className="custom-scrollbar max-h-[calc(100vh-15rem)] space-y-1 overflow-y-auto pr-1">
            {FOLLOWED_CREATORS.map((creator) => {
              const isActive = selectedCreatorId === creator.id;
              const creatorUpdateCount = sortedUpdates.filter((update) => update.creatorId === creator.id).length;
              return (
                <button
                  key={creator.id}
                  type="button"
                  onClick={() => setSelectedCreatorId(creator.id)}
                  className={clsx(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition",
                    isActive
                      ? "bg-stone-950 text-white shadow-[0_12px_24px_rgba(15,23,42,0.12)]"
                      : "text-stone-700 hover:bg-stone-50 hover:text-stone-950"
                  )}
                >
                  <div className="relative shrink-0">
                    <Image
                      src={creatorAvatarUrl(creator)}
                      alt=""
                      className="h-11 w-11 rounded-full border border-stone-200 bg-stone-100"
                      width={44}
                      height={44}
                      unoptimized
                    />
                    {creator.unreadCount ? (
                      <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-white bg-rose-500" />
                    ) : null}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{creator.name}</p>
                    <p className={clsx("mt-0.5 truncate text-xs", isActive ? "text-white/65" : "text-stone-500")}>
                      {creator.field} · {creatorUpdateCount} 更新
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        <section className="min-w-0">
          <div className="mb-4 rounded-lg border border-stone-200 bg-white/90 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-stone-400">
                  {selectedCreator ? `@${selectedCreator.handle}` : "Following feed"}
                </p>
                <h2 className="mt-1 text-xl font-semibold tracking-tight text-stone-950">
                  {selectedCreator ? `${selectedCreator.name} 的最近课程更新` : "全部动态"}
                </h2>
              </div>

              <div className="relative min-w-0 md:w-72">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索课程、创作者或标签"
                  className="w-full rounded-md border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
                />
              </div>
            </div>

            {selectedCreator ? (
              <p className="mt-3 text-sm leading-6 text-stone-600">
                {selectedCreator.bio} · {formatCompactNumber(selectedCreator.followers)} 关注者
              </p>
            ) : null}
          </div>

          {selectedCreator ? (
            <div className="space-y-4">
              {visibleUpdates.length ? (
                visibleUpdates.map((update) => renderUpdateCard(update, getCreatorById(update.creatorId)))
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 bg-white/82 px-5 py-10 text-sm text-stone-500">
                  暂时没有匹配到课程动态。
                </div>
              )}
            </div>
          ) : (
            renderAllCreatorsFeed(visibleCreatorFeed)
          )}
        </section>

        <aside className="hidden h-fit space-y-3 xl:block">
          <div className="rounded-lg border border-stone-200 bg-white/90 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Bell className="h-4 w-4 text-rose-500" />
              今日提醒
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-600">
              {latestUpdate ? `最近更新：${latestUpdate.moduleTitle}` : "关注创作者后，这里会显示最新课程提醒。"}
            </p>
          </div>

          <div className="rounded-lg border border-stone-200 bg-white/90 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <GraduationCap className="h-4 w-4 text-emerald-600" />
              关注概览
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-stone-400">Creators</dt>
                <dd className="mt-1 font-semibold text-stone-900">{FOLLOWED_CREATORS.length}</dd>
              </div>
              <div>
                <dt className="text-xs text-stone-400">Updates</dt>
                <dd className="mt-1 font-semibold text-stone-900">{sortedUpdates.length}</dd>
              </div>
            </dl>
          </div>
        </aside>
      </div>
  );

  function renderAllCreatorsFeed(items: { creator: FollowedCreator; update: FollowedCourseUpdate }[]) {
    return (
      <div className="overflow-hidden rounded-lg border border-stone-200 bg-white shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
        <div className="flex flex-col gap-3 border-b border-stone-100 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Sparkles className="h-4 w-4 text-rose-500" />
              关注创作者最近更新
            </div>
            <p className="mt-1 text-sm leading-6 text-stone-500">
              汇总你关注的所有创作者，每人展示最近一次课程动态。
            </p>
          </div>
          <span className="w-fit rounded-full bg-stone-100 px-3 py-1 text-xs font-semibold text-stone-500">
            {items.length} / {FOLLOWED_CREATORS.length} 位创作者
          </span>
        </div>

        {items.length ? (
          <div className="divide-y divide-stone-100">
            {items.map(({ creator, update }) => (
              <article key={update.id} className="grid gap-3 px-4 py-4 transition hover:bg-stone-50/70 md:grid-cols-[minmax(0,1fr)_9.5rem]">
                <div className="flex min-w-0 gap-3">
                  <div className="relative shrink-0">
                    <Image
                      src={creatorAvatarUrl(creator)}
                      alt=""
                      className="h-11 w-11 rounded-full border border-stone-200 bg-stone-100"
                      width={44}
                      height={44}
                      unoptimized
                    />
                    {creator.unreadCount ? (
                      <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-white bg-rose-500" />
                    ) : null}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <p className="truncate text-sm font-semibold text-stone-950">{creator.name}</p>
                      <span className="text-xs text-stone-400">@{creator.handle}</span>
                      <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-semibold text-rose-600">
                        {updateKindLabel(update.updateKind)}
                      </span>
                    </div>

                    <p className="mt-1 text-xs text-stone-500">
                      {formatRelativeTime(update.updatedAt)} · {update.courseTitle}
                    </p>
                    <h3 className="mt-2 line-clamp-2 text-base font-semibold leading-6 text-stone-950">
                      {update.moduleTitle}
                    </h3>
                    <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-600">{update.summary}</p>

                    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
                      {update.tags.slice(0, 3).map((tag) => (
                        <span key={`${update.id}:feed:${tag}`} className="rounded-full bg-sky-50 px-2.5 py-1 font-semibold text-sky-700">
                          {tag}
                        </span>
                      ))}
                      <span className="px-1">{formatCompactNumber(update.views)} 次学习</span>
                    </div>
                  </div>
                </div>

                <div className="relative hidden overflow-hidden rounded-md border border-stone-200 bg-stone-950 md:block">
                  <Image
                    src={updateCoverUrl(update)}
                    alt=""
                    className="h-full min-h-24 w-full object-cover"
                    width={304}
                    height={192}
                    unoptimized
                  />
                  <span className="absolute bottom-2 right-2 rounded bg-black/50 px-2 py-1 text-[11px] font-semibold text-white">
                    {update.durationLabel}
                  </span>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="px-5 py-10 text-sm text-stone-500">暂时没有匹配到关注创作者的最近动态。</div>
        )}
      </div>
    );
  }

  function renderUpdateCard(update: FollowedCourseUpdate, creator: FollowedCreator) {
    return (
      <article
        key={update.id}
        className="overflow-hidden rounded-lg border border-stone-200 bg-white shadow-[0_12px_28px_rgba(15,23,42,0.04)]"
      >
        <div className="flex items-start justify-between gap-4 px-4 py-4">
          <div className="flex min-w-0 gap-3">
            <Image
              src={creatorAvatarUrl(creator)}
              alt=""
              className="h-11 w-11 rounded-full border border-stone-200 bg-stone-100"
              width={44}
              height={44}
              unoptimized
            />
            <div className="min-w-0">
              <p className="truncate text-base font-semibold text-stone-950">{creator.name}</p>
              <p className="mt-0.5 text-xs text-stone-500">
                {formatRelativeTime(update.updatedAt)} · {updateKindLabel(update.updateKind)}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="rounded-md p-2 text-stone-400 transition hover:bg-stone-100 hover:text-stone-950"
            aria-label="更多动态操作"
          >
            <MoreHorizontal className="h-4 w-4" />
          </button>
        </div>

        <div className="px-4 pb-4">
          <div className="overflow-hidden rounded-lg border border-stone-200 bg-stone-950">
            <div className="relative aspect-video">
              <Image
                src={updateCoverUrl(update)}
                alt=""
                className="h-full w-full object-cover"
                width={960}
                height={540}
                unoptimized
              />
              <div className="absolute inset-x-0 bottom-0 flex items-end justify-between bg-gradient-to-t from-black/70 via-black/20 to-transparent p-4 text-white">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/70">{update.courseTitle}</p>
                  <h3 className="mt-1 max-w-2xl text-xl font-semibold tracking-tight">{update.moduleTitle}</h3>
                </div>
                <span className="shrink-0 rounded bg-black/45 px-2 py-1 text-xs font-semibold">{update.durationLabel}</span>
              </div>
            </div>
          </div>

          <p className="mt-3 text-sm leading-7 text-stone-700">{update.summary}</p>

          <div className="mt-3 flex flex-wrap gap-1.5">
            {update.tags.map((tag) => (
              <span key={`${update.id}:${tag}`} className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">
                {tag}
              </span>
            ))}
          </div>

          <div className="mt-4 flex items-center justify-between border-t border-stone-100 pt-3 text-sm text-stone-500">
            <span>{formatCompactNumber(update.views)} 次学习</span>
            <div className="flex items-center gap-5">
              <button type="button" className="inline-flex items-center gap-1.5 transition hover:text-stone-950">
                <Share2 className="h-4 w-4" />
                转发
              </button>
              <button type="button" className="inline-flex items-center gap-1.5 transition hover:text-stone-950">
                <MessageCircle className="h-4 w-4" />
                {update.comments}
              </button>
              <button type="button" className="inline-flex items-center gap-1.5 transition hover:text-stone-950">
                <Heart className="h-4 w-4" />
                {update.likes}
              </button>
            </div>
          </div>
        </div>
      </article>
    );
  }
}

export function FollowingFeed() {
  const totalUnreadCount = FOLLOWED_CREATORS.reduce((sum, creator) => sum + creator.unreadCount, 0);

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

          <div className="flex items-center gap-2">
            <span className="hidden rounded-full bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-600 sm:inline-flex">
              {totalUnreadCount} 条新动态
            </span>
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <BookOpen className="h-4 w-4" />
              Learning Hub
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <FollowingFeedContent />
      </div>
    </main>
  );
}
