"use client";

import clsx from "clsx";
import Link from "next/link";
import { BookText, ChevronDown, FolderClosed, LoaderCircle, MoreHorizontal } from "lucide-react";
import { useId, useState } from "react";

import type { RecentFeedItem, RecentFeedUpdate } from "@/lib/recent-feed";

type RelativeTimeFormatter = (value: string | Date | null | undefined) => string;

type RecentFeedCardLabels = {
  moreUpdatesAria: string;
  timelineSummary: (count: number) => string;
  timelineLatestLabel: string;
  timelineExpand: (count: number) => string;
  timelineCollapse: string;
  readMore: string;
};

type RecentFeedCardProps = {
  item: RecentFeedItem;
  buttonBusy: boolean;
  formatRelativeTime: RelativeTimeFormatter;
  labels: RecentFeedCardLabels;
  onOpenLesson: (lessonId: string) => Promise<void> | void;
};

function feedKindTone(kind: RecentFeedItem["kind"]) {
  return kind === "commit" ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-700";
}

function feedKindLabel(kind: RecentFeedItem["kind"]) {
  return kind === "commit" ? "Commit" : "Resource";
}

function feedPreviewHeading(kind: RecentFeedItem["kind"]) {
  return kind === "commit" ? "What's Changed" : "Resource Summary";
}

function CommitTimeline({
  updates,
  formatRelativeTime,
}: {
  updates: RecentFeedUpdate[];
  formatRelativeTime: RelativeTimeFormatter;
}) {
  return (
    <ol className="mt-3">
      {updates.map((update, updateIndex) => {
        const isLast = updateIndex === updates.length - 1;

        return (
          <li key={update.id} className="relative flex gap-3 pb-4 last:pb-0">
            {!isLast ? <span className="absolute left-[5px] top-4 h-full w-px bg-stone-200" /> : null}
            <span className="relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-rose-500 ring-4 ring-white" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <p className="text-sm font-semibold text-stone-950">{update.lessonTitle ?? update.title}</p>
                <span className="text-xs text-stone-400">{formatRelativeTime(update.timestamp)}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-stone-800">{update.title}</p>
                <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-stone-500">
                  {update.detailTitle}
                </span>
              </div>
              <p className="mt-1 text-sm leading-6 text-stone-600">{update.detailBody}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function CollapsedCommitTimeline({
  latestUpdate,
  updateCount,
  formatRelativeTime,
  labels,
}: {
  latestUpdate: RecentFeedUpdate;
  updateCount: number;
  formatRelativeTime: RelativeTimeFormatter;
  labels: RecentFeedCardLabels;
}) {
  return (
    <div className="mt-3 rounded-md border border-stone-200 bg-white/80 px-3 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <p className="text-sm font-semibold text-stone-950">{labels.timelineSummary(updateCount)}</p>
        <span className="text-xs text-stone-400">
          {labels.timelineLatestLabel} · {formatRelativeTime(latestUpdate.timestamp)}
        </span>
      </div>
      <p className="mt-1 truncate text-sm font-medium text-stone-800">
        {latestUpdate.lessonTitle ?? latestUpdate.title}
      </p>
      <p className="mt-1 truncate text-sm text-stone-600">
        {latestUpdate.title} · {latestUpdate.detailBody}
      </p>
    </div>
  );
}

export function RecentFeedCard({
  item,
  buttonBusy,
  formatRelativeTime,
  labels,
  onOpenLesson,
}: RecentFeedCardProps) {
  const [timelineExpanded, setTimelineExpanded] = useState(false);
  const timelineId = useId();
  const updates = item.updates ?? [];
  const hasCollapsibleTimeline = item.kind === "commit" && updates.length > 1;
  const latestUpdate = updates[0];

  return (
    <article className="rounded-lg border border-stone-300 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 gap-3">
          <div
            className={clsx(
              "mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-white",
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

        <button
          type="button"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-stone-400 transition hover:bg-stone-100 hover:text-stone-700"
          aria-label={labels.moreUpdatesAria}
          title={labels.moreUpdatesAria}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </div>

      <h4 className="mt-4 text-lg font-semibold text-stone-950">{item.title}</h4>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className={clsx("rounded-full px-2.5 py-1 text-[10px] font-semibold", feedKindTone(item.kind))}>
          {feedKindLabel(item.kind)}
        </span>
        <span className="text-xs text-stone-400">{item.pills.slice(0, 2).join(" · ")}</span>
      </div>

      <div className="mt-4 rounded-md bg-[#f6f8fa] p-4">
        <div className="border-b border-stone-200 pb-3">
          <p className="text-base font-semibold text-stone-950">{feedPreviewHeading(item.kind)}</p>
        </div>

        {hasCollapsibleTimeline && latestUpdate ? (
          <div id={timelineId}>
            {timelineExpanded ? (
              <CommitTimeline updates={updates} formatRelativeTime={formatRelativeTime} />
            ) : (
              <CollapsedCommitTimeline
                latestUpdate={latestUpdate}
                updateCount={updates.length}
                formatRelativeTime={formatRelativeTime}
                labels={labels}
              />
            )}
          </div>
        ) : (
          <div className="mt-3">
            <p className="text-sm font-semibold text-stone-950">{item.detailTitle}</p>
            <p className="mt-2 text-sm leading-7 text-stone-600">{item.detailBody}</p>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {hasCollapsibleTimeline ? (
            <button
              type="button"
              onClick={() => setTimelineExpanded((current) => !current)}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-stone-200 bg-white px-3 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              aria-expanded={timelineExpanded}
              aria-controls={timelineId}
              title={timelineExpanded ? labels.timelineCollapse : labels.timelineExpand(updates.length)}
            >
              <ChevronDown
                className={clsx(
                  "h-3.5 w-3.5 shrink-0 transition-transform duration-200",
                  timelineExpanded ? "rotate-180" : "rotate-0"
                )}
              />
              <span className="truncate">
                {timelineExpanded ? labels.timelineCollapse : labels.timelineExpand(updates.length)}
              </span>
            </button>
          ) : null}

          {item.lessonId ? (
            <button
              type="button"
              onClick={() => void onOpenLesson(item.lessonId!)}
              className="inline-flex items-center gap-2 text-xs font-semibold text-stone-800 underline underline-offset-2"
            >
              {buttonBusy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
              <span>{labels.readMore}</span>
            </button>
          ) : (
            <Link href="/studio" className="inline-flex text-xs font-semibold text-stone-800 underline underline-offset-2">
              {labels.readMore}
            </Link>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {item.pills.map((pill, pillIndex) => (
          <span
            key={`${item.id}:pill:${pillIndex}:${pill}`}
            className="rounded-md border border-stone-200 bg-white px-2.5 py-1 text-[11px] font-medium text-stone-500"
          >
            {pill}
          </span>
        ))}
      </div>
    </article>
  );
}
