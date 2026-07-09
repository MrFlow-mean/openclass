"use client";

import { Check, FileText, X } from "lucide-react";

import type { EvidenceBundle, RetrievalEvidence } from "@/types";

type EvidenceConfirmationCardProps = {
  bundle: EvidenceBundle;
  isBusy: boolean;
  onConfirm: (bundleId: string) => void | Promise<void>;
  onSkip: (bundleId: string) => void | Promise<void>;
};

export function EvidenceConfirmationCard({ bundle, isBusy, onConfirm, onSkip }: EvidenceConfirmationCardProps) {
  const requiresConfirmation =
    bundle.status === "candidate" && (bundle.purpose === "board_generation" || bundle.purpose === "board_edit");
  return (
    <div className="rounded-lg border border-violet-200 bg-violet-50 p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-bold uppercase tracking-widest text-violet-700">
          {requiresConfirmation ? "待确认资料证据" : "本轮参考资料"}
        </p>
        <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-violet-700">
          {bundle.status === "confirmed" ? "已确认" : bundle.evidence_items.length}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {bundle.evidence_items.slice(0, 4).map((item) => (
          <EvidenceItem key={item.id} item={item} />
        ))}
      </div>
      {requiresConfirmation ? (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => void onConfirm(bundle.id)}
            disabled={isBusy}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm font-semibold text-gray-900 transition hover:border-violet-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check className="h-4 w-4" />
            使用
          </button>
          <button
            type="button"
            onClick={() => void onSkip(bundle.id)}
            disabled={isBusy}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm font-semibold text-gray-900 transition hover:border-violet-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-4 w-4" />
            跳过
          </button>
        </div>
      ) : null}
    </div>
  );
}

function EvidenceItem({ item }: { item: RetrievalEvidence }) {
  const location = [item.source_title, item.section_path.join(" > "), item.page_range]
    .filter(Boolean)
    .join(" / ");
  return (
    <div className="rounded-lg bg-white p-3">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 shrink-0 text-violet-600" />
        <p className="min-w-0 truncate text-xs font-semibold text-gray-800">{location || "未命名资料"}</p>
      </div>
      <p className="mt-2 max-h-16 overflow-hidden text-xs leading-5 text-gray-600">{item.excerpt}</p>
    </div>
  );
}
