"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  BookText,
  Eye,
  FolderClosed,
  Heart,
  MessageCircle,
  MoreHorizontal,
  Search,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import {
  FOLLOWED_CREATORS,
  buildFollowedCourseUpdateItems,
  creatorAvatarUrl,
  type FollowedCreator,
  type FollowedCourseUpdate,
  type FollowedCourseUpdateItem,
} from "@/lib/following";
import type { ProfileSettingsTexts } from "@/lib/i18n/product-ui";

type FollowingTexts = ProfileSettingsTexts["following"];

type CreatorFilter = "all" | string;

function formatRelativeTime(
  value: string | Date | null | undefined,
  txt: FollowingTexts,
  intlLocale: string
) {
  if (!value) {
    return txt.justNow;
  }

  const date = value instanceof Date ? value : new Date(value);
  const timestamp = date.getTime();

  if (Number.isNaN(timestamp)) {
    return txt.justNow;
  }

  const minutes = Math.floor((Date.now() - timestamp) / 60000);
  if (minutes <= 0) {
    return txt.justNow;
  }
  if (minutes < 60) {
    return txt.minutesAgo(minutes);
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return txt.hoursAgo(hours);
  }

  const days = Math.floor(hours / 24);
  if (days < 7) {
    return txt.daysAgo(days);
  }

  return new Intl.DateTimeFormat(intlLocale, {
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

function formatCompactCount(value: number, intlLocale: string) {
  return new Intl.NumberFormat(intlLocale, {
    maximumFractionDigits: 1,
    notation: "compact",
  }).format(value);
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
      return "bg-emerald-100 text-emerald-700";
    case "note_added":
      return "bg-sky-100 text-sky-700";
    case "course_revision":
      return "bg-rose-100 text-rose-700";
    case "new_lesson":
    default:
      return "bg-stone-100 text-stone-700";
  }
}

function updateActionLabel(kind: FollowedCourseUpdate["updateKind"], txt: FollowingTexts) {
  return txt.updateActions[kind];
}

function updatePreviewHeading(kind: FollowedCourseUpdate["updateKind"], txt: FollowingTexts) {
  return txt.previewHeadings[kind];
}

export function FollowingFeedContent() {
  const { texts: txt, intlLocale } = useInterfaceLanguage();
  const f = txt.following;
  const [selectedCreatorId, setSelectedCreatorId] = useState<CreatorFilter>("all");
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const feedItems = useMemo(() => buildFollowedCourseUpdateItems(), []);
  const updateCountByCreator = useMemo(() => {
    return feedItems.reduce((counts, item) => {
      counts.set(item.creator.id, (counts.get(item.creator.id) ?? 0) + 1);
      return counts;
    }, new Map<string, number>());
  }, [feedItems]);
  const selectedCreator =
    selectedCreatorId === "all" ? null : FOLLOWED_CREATORS.find((creator) => creator.id === selectedCreatorId) ?? null;
  const totalUnreadCount = FOLLOWED_CREATORS.reduce((total, creator) => total + creator.unreadCount, 0);
  const visibleFeedItems = feedItems.filter((item) => {
    const matchesCreator = selectedCreatorId === "all" || item.creator.id === selectedCreatorId;
    return matchesCreator && feedItemMatchesSearch(item, normalizedQuery);
  });

  return (
    <div className="mx-auto grid max-w-7xl gap-5 lg:grid-cols-[250px_minmax(0,1fr)] lg:items-start">
      <FollowingCreatorRail
        creators={FOLLOWED_CREATORS}
        selectedCreatorId={selectedCreatorId}
        totalUnreadCount={totalUnreadCount}
        updateCountByCreator={updateCountByCreator}
        onSelectCreator={setSelectedCreatorId}
        txt={f}
      />

      <section className="min-w-0 rounded-[30px] border border-white/70 bg-[linear-gradient(180deg,#ffffff_0%,#faf8f2_100%)] p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] sm:p-7">
        <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-stone-950">
              <Activity className="h-5 w-5" />
              {selectedCreator ? selectedCreator.name : f.allUpdates}
            </h1>
            <p className="mt-1 text-sm text-stone-500">
              {selectedCreator
                ? f.creatorSummary(selectedCreator.field, formatCompactCount(selectedCreator.followers, intlLocale))
                : f.allCreatorsSummary(FOLLOWED_CREATORS.length)}
            </p>
          </div>

          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="relative min-w-0 md:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={f.searchPlaceholder}
                className="w-full rounded-full border border-stone-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
              />
            </div>

          </div>
        </div>

        <div className="space-y-4">
          {visibleFeedItems.length ? (
            visibleFeedItems.map((item) => renderFeedCard(item, f, intlLocale))
          ) : (
            <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-8 text-sm text-stone-500">
              {f.noMatches}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

type FollowingCreatorRailProps = {
  creators: FollowedCreator[];
  selectedCreatorId: CreatorFilter;
  totalUnreadCount: number;
  updateCountByCreator: Map<string, number>;
  onSelectCreator: (creatorId: CreatorFilter) => void;
  txt: FollowingTexts;
};

function FollowingCreatorRail({
  creators,
  selectedCreatorId,
  totalUnreadCount,
  updateCountByCreator,
  onSelectCreator,
  txt,
}: FollowingCreatorRailProps) {
  const isAllActive = selectedCreatorId === "all";

  return (
    <aside className="min-w-0 overflow-hidden border-y border-stone-200 bg-[#eef0f3] sm:rounded-[24px] sm:border lg:sticky lg:top-[82px]">
      <div className="flex gap-2 overflow-x-auto p-3 lg:max-h-[calc(100vh-7rem)] lg:flex-col lg:gap-1 lg:overflow-y-auto lg:p-2">
        <button
          type="button"
          aria-pressed={isAllActive}
          onClick={() => onSelectCreator("all")}
          className={clsx(
            "flex min-w-[178px] shrink-0 items-center gap-3 rounded-[18px] px-3 py-3 text-left transition lg:min-w-0 lg:w-full",
            isAllActive ? "bg-white text-stone-950 shadow-sm" : "text-stone-600 hover:bg-white/70 hover:text-stone-950"
          )}
        >
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#ff6699] text-white shadow-sm">
            <Activity className="h-5 w-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">{txt.allUpdates}</span>
            <span className="mt-0.5 block truncate text-xs text-stone-400">{txt.followedCount(creators.length)}</span>
          </span>
          {totalUnreadCount ? (
            <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-[#ff6699] px-1.5 text-[11px] font-semibold text-white">
              {totalUnreadCount > 99 ? "99+" : totalUnreadCount}
            </span>
          ) : null}
        </button>

        {creators.map((creator) => {
          const isActive = selectedCreatorId === creator.id;
          const updateCount = updateCountByCreator.get(creator.id) ?? 0;

          return (
            <button
              key={creator.id}
              type="button"
              aria-pressed={isActive}
              onClick={() => onSelectCreator(creator.id)}
              className={clsx(
                "flex min-w-[210px] shrink-0 items-center gap-3 rounded-[18px] px-3 py-3 text-left transition lg:min-w-0 lg:w-full",
                isActive ? "bg-white text-stone-950 shadow-sm" : "text-stone-600 hover:bg-white/70 hover:text-stone-950"
              )}
            >
              <span className="relative h-12 w-12 shrink-0">
                <Image
                  src={creatorAvatarUrl(creator)}
                  alt={txt.avatarAlt(creator.name)}
                  className="h-12 w-12 rounded-full border border-white bg-stone-100 object-cover shadow-sm"
                  width={48}
                  height={48}
                  unoptimized
                />
                {creator.unreadCount ? (
                  <span
                    className={clsx(
                      "absolute bottom-0 right-0 h-3.5 w-3.5 rounded-full bg-[#ff6699] ring-2",
                      isActive ? "ring-white" : "ring-[#eef0f3]"
                    )}
                  />
                ) : null}
              </span>

              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold">{creator.name}</span>
                <span className="mt-0.5 block truncate text-xs text-stone-400">{creator.field}</span>
              </span>

              <span
                className={clsx(
                  "flex h-6 min-w-6 shrink-0 items-center justify-center rounded-full px-1.5 text-xs font-semibold",
                  isActive ? "bg-stone-100 text-stone-600" : "bg-white/80 text-stone-400"
                )}
              >
                {updateCount}
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function renderFeedCard(item: FollowedCourseUpdateItem, txt: FollowingTexts, intlLocale: string) {
  const { creator, update } = item;
  const isResourceUpdate = update.updateKind === "resource_added";
  const numberLocale = intlLocale;

  return (
    <article
      key={update.id}
      className="rounded-lg border border-stone-300 bg-white p-4 shadow-sm"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 gap-3">
          <span className="relative h-10 w-10 shrink-0">
            <Image
              src={creatorAvatarUrl(creator)}
              alt=""
              className="h-10 w-10 rounded-full border border-stone-200 bg-stone-100"
              width={40}
              height={40}
              unoptimized
            />
            <span
              className={clsx(
                "absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full text-white ring-2 ring-white",
                updateTone(update.updateKind)
              )}
            >
              {isResourceUpdate ? <FolderClosed className="h-3 w-3" /> : <BookText className="h-3 w-3" />}
            </span>
          </span>

          <div className="min-w-0">
            <p className="text-sm text-stone-600">
              <span className="font-semibold text-stone-950">{creator.name}</span>{" "}
              {updateActionLabel(update.updateKind, txt)}{" "}
              <span className="font-semibold text-stone-950">{update.courseTitle}</span>
            </p>
            <p className="mt-1 text-xs text-stone-400">
              @{creator.handle} · {creator.field} · {formatRelativeTime(update.updatedAt, txt, intlLocale)}
            </p>
          </div>
        </div>

        <button
          type="button"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-stone-700"
          aria-label={txt.moreActions}
          title={txt.moreActions}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </div>

      <h2 className="mt-4 text-lg font-semibold text-stone-950">{update.moduleTitle}</h2>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span
          className={clsx("rounded-full px-2.5 py-1 text-[10px] font-semibold", updateLabelTone(update.updateKind))}
        >
          {txt.updateKinds[update.updateKind]}
        </span>
        <span className="text-xs text-stone-400">
          {txt.lessonCountViews(update.lessonCount, update.views.toLocaleString(numberLocale))}
        </span>
      </div>

      <div className="mt-4 rounded-md bg-[#f6f8fa] p-4">
        <div className="border-b border-stone-200 pb-3">
          <p className="text-base font-semibold text-stone-950">{updatePreviewHeading(update.updateKind, txt)}</p>
        </div>
        <p className="mt-3 text-sm leading-7 text-stone-600">{update.summary}</p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-stone-600">
          {update.tags.map((tag) => (
            <li key={`${update.id}:tag:${tag}`}>{tag}</li>
          ))}
        </ul>
        <Link href="/" className="mt-4 inline-flex text-xs font-semibold text-stone-800 underline underline-offset-2">
          {txt.readMore}
        </Link>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-stone-500">
        <span className="inline-flex h-7 items-center gap-1 rounded-md border border-stone-200 bg-white px-2.5">
          <Eye className="h-3.5 w-3.5" />
          {update.views.toLocaleString(numberLocale)}
        </span>
        <span className="inline-flex h-7 items-center gap-1 rounded-md border border-stone-200 bg-white px-2.5">
          <MessageCircle className="h-3.5 w-3.5" />
          {update.comments.toLocaleString(numberLocale)}
        </span>
        <span className="inline-flex h-7 items-center gap-1 rounded-md border border-stone-200 bg-white px-2.5">
          <Heart className="h-3.5 w-3.5" />
          {update.likes.toLocaleString(numberLocale)}
        </span>
      </div>
    </article>
  );
}

export function FollowingFeed() {
  const { texts: txt } = useInterfaceLanguage();
  const brand = txt.following.brand;

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-40 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            {brand}
          </Link>

          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            <BrandMark alt="" className="h-5 w-5 rounded bg-white" size={40} />
            {brand}
          </Link>
        </div>
      </header>

      <div className="px-4 py-6 sm:px-6">
        <FollowingFeedContent />
      </div>
    </main>
  );
}
