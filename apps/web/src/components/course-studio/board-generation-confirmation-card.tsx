import { Check, FileText, X } from "lucide-react";

import type { ChatRequestPayload, EvidenceBundle, LearningClarificationStatus, RetrievalEvidence } from "@/types";

type BoardGenerationConfirmationCardProps = {
  clarityStatus: LearningClarificationStatus;
  isChatBusy: boolean;
  isPendingEvidenceLoading: boolean;
  candidateEvidenceBundle: EvidenceBundle | null;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
  onEvidenceAction: (bundleId: string, action: "confirm" | "skip") => void | Promise<void>;
};

export function BoardGenerationConfirmationCard({
  clarityStatus,
  isChatBusy,
  isPendingEvidenceLoading,
  candidateEvidenceBundle,
  onSubmitChat,
  onEvidenceAction,
}: BoardGenerationConfirmationCardProps) {
  const requiresEvidenceConfirmation =
    candidateEvidenceBundle?.status === "candidate" && candidateEvidenceBundle.purpose === "board_generation";
  const startDisabled = isChatBusy || isPendingEvidenceLoading || requiresEvidenceConfirmation;

  function submitBoardGeneration() {
    const payload: ChatRequestPayload = {
      message: "开始生成板书",
      interaction_mode: "ask",
      board_generation_action: "start",
    };
    void onSubmitChat(payload);
  }

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
      <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">学习需求已清晰</p>
      <p className="mt-2 text-sm leading-6 text-emerald-950">{clarityStatus.summary || clarityStatus.reason}</p>
      {isPendingEvidenceLoading ? (
        <p className="mt-3 border-t border-emerald-100 pt-3 text-xs leading-6 text-emerald-900/80">正在核对本轮资料证据。</p>
      ) : candidateEvidenceBundle ? (
        <div className="mt-3 border-t border-emerald-100 pt-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">本轮资料依据</p>
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
              {requiresEvidenceConfirmation ? "待确认" : "已确认"}
            </span>
          </div>
          <div className="mt-3 space-y-3">
            {candidateEvidenceBundle.evidence_items.slice(0, 4).map((item) => (
              <EvidenceSummary key={item.id} item={item} />
            ))}
          </div>
          {requiresEvidenceConfirmation ? (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => void onEvidenceAction(candidateEvidenceBundle.id, "confirm")}
                disabled={isChatBusy}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-300 bg-white px-3 text-xs font-semibold text-emerald-800 transition hover:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Check className="h-4 w-4" />
                使用资料
              </button>
              <button
                type="button"
                onClick={() => void onEvidenceAction(candidateEvidenceBundle.id, "skip")}
                disabled={isChatBusy}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 text-xs font-semibold text-gray-700 transition hover:border-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <X className="h-4 w-4" />
                跳过资料
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="mt-2 text-xs leading-6 text-emerald-900/80">接下来将基于这份学习需求生成板书，是否开始？</p>
      )}
      <button
        type="button"
        onClick={submitBoardGeneration}
        disabled={startDisabled}
        className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        开始生成板书
      </button>
    </div>
  );
}

function EvidenceSummary({ item }: { item: RetrievalEvidence }) {
  const location = [item.source_title, item.section_path.join(" > "), item.page_range].filter(Boolean).join(" / ");
  return (
    <div className="grid gap-1 text-xs leading-5 text-emerald-950">
      <p className="flex items-start gap-2 font-semibold">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
        <span className="min-w-0 break-words">{location || "未命名资料"}</span>
      </p>
      {item.chapter_id ? <p className="text-[11px] font-semibold text-emerald-700">已按验证目录定位正文</p> : null}
      <p className="max-h-16 overflow-hidden text-gray-600">{item.excerpt}</p>
    </div>
  );
}
