"use client";

import clsx from "clsx";
import { GitMerge, X } from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type {
  MergeBranchChoice,
  MergeBranchChoices,
  MergeBranchPreviewResponse,
  MergeBranchSectionKey,
  MergeBranchSectionPreview,
} from "@/types";

type BranchMergeReviewCardProps = {
  preview: MergeBranchPreviewResponse;
  choices: MergeBranchChoices;
  busyAction: string | null;
  onChoiceChange: (section: MergeBranchSectionKey, choice: MergeBranchChoice) => void;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
};

function statusTone(status: MergeBranchSectionPreview["status"]) {
  if (status === "conflict") {
    return "bg-amber-50 text-amber-700";
  }
  if (status === "source_only") {
    return "bg-blue-50 text-blue-700";
  }
  return "bg-gray-100 text-gray-500";
}

function SectionChoice({
  section,
  preview,
  value,
  onChange,
}: {
  section: MergeBranchSectionKey;
  preview: MergeBranchSectionPreview;
  value: MergeBranchChoice;
  onChange: (section: MergeBranchSectionKey, choice: MergeBranchChoice) => void;
}) {
  const { texts: txt } = useInterfaceLanguage();
  const m = txt.studio.merge;
  const sectionLabel = m.sectionLabels[section];
  const choiceLabels = {
    target: txt.common.current,
    source: txt.common.source,
  } as const;
  return (
    <div className="rounded-md border border-gray-200 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-gray-950">{sectionLabel}</p>
        <span className={clsx("rounded px-1.5 py-0.5 text-[9px] font-bold", statusTone(preview.status))}>
          {m.statusLabels[preview.status]}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1">
        {(["target", "source"] as const).map((choice) => (
          <button
            key={choice}
            type="button"
            aria-label={m.useChoiceAria(sectionLabel, choiceLabels[choice])}
            onClick={() => onChange(section, choice)}
            className={clsx(
              "min-w-0 rounded border px-2 py-1.5 text-left text-[10px] transition",
              value === choice
                ? "border-gray-950 bg-gray-950 text-white"
                : "border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-gray-950"
            )}
          >
            <span className="block font-bold">{choiceLabels[choice]}</span>
            <span className="mt-0.5 block truncate opacity-80">
              {choice === "target" ? preview.target_summary : preview.source_summary}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function BranchMergeReviewCard({
  preview,
  choices,
  busyAction,
  onChoiceChange,
  onCancel,
  onConfirm,
}: BranchMergeReviewCardProps) {
  const { texts: txt } = useInterfaceLanguage();
  const m = txt.studio.merge;
  const isBusy = busyAction === "merge";
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{m.reviewTitle}</p>
          <h5 className="mt-1 truncate text-sm font-semibold text-gray-950">
            {preview.source_branch} → {preview.target_branch}
          </h5>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-950"
          aria-label={m.closePreview}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3 grid gap-2">
        {(["document", "requirements", "session"] as const).map((section) => (
          <SectionChoice
            key={section}
            section={section}
            preview={preview[section]}
            value={choices[section]}
            onChange={onChoiceChange}
          />
        ))}
      </div>

      {preview.already_merged ? (
        <p className="mt-3 rounded-md bg-gray-50 px-3 py-2 text-[11px] leading-5 text-gray-500">
          {m.alreadyMerged}
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => void onConfirm()}
        disabled={!preview.can_merge || isBusy}
        className="mt-3 inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md bg-gray-950 px-3 text-[10px] font-bold uppercase tracking-wider text-white transition hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400"
      >
        <GitMerge className="h-3.5 w-3.5" />
        {isBusy ? m.merging : m.confirmMerge}
      </button>
    </section>
  );
}
