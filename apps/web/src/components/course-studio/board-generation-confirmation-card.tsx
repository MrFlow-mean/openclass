import { Check, FileText, ImageIcon, X } from "lucide-react";

import type {
  BoardTaskRequirementSheet,
  ChatRequestPayload,
  EvidenceBundle,
  LearningClarificationStatus,
  RetrievalEvidence,
  RetrievalVisualEvidence,
} from "@/types";

type BoardGenerationConfirmationCardProps = {
  clarityStatus: LearningClarificationStatus;
  boardTask?: BoardTaskRequirementSheet | null;
  isChatBusy: boolean;
  isPendingEvidenceLoading: boolean;
  candidateEvidenceBundle: EvidenceBundle | null;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
  onEvidenceAction: (bundleId: string, action: "confirm" | "skip") => void | Promise<void>;
};

export function BoardGenerationConfirmationCard({
  clarityStatus,
  boardTask = null,
  isChatBusy,
  isPendingEvidenceLoading,
  candidateEvidenceBundle,
  onSubmitChat,
  onEvidenceAction,
}: BoardGenerationConfirmationCardProps) {
  const isReadyExistingBoardWrite = Boolean(
    boardTask &&
      ["write", "edit"].includes(boardTask.requested_action ?? "") &&
      boardTask.progress >= 100 &&
      boardTask.missing_items.length === 0 &&
      !boardTask.clarification_question.trim()
  );
  const isExistingBoardWrite = Boolean(isReadyExistingBoardWrite && candidateEvidenceBundle?.purpose === "board_edit");
  const requiresEvidenceConfirmation =
    candidateEvidenceBundle?.status === "candidate" &&
    (candidateEvidenceBundle.purpose === "board_generation" || isExistingBoardWrite);
  const startDisabled = isChatBusy || isPendingEvidenceLoading || requiresEvidenceConfirmation;
  const visualItems = candidateEvidenceBundle?.visual_items ?? [];

  function submitBoardGeneration() {
    const payload: ChatRequestPayload = {
      message: "开始生成板书",
      interaction_mode: "ask",
      board_generation_action: "start",
    };
    void onSubmitChat(payload);
  }

  function resumeConfirmedBoardTask() {
    if (!boardTask) {
      return;
    }
    void onSubmitChat({
      message: boardTask.question_or_topic || "继续执行当前板书写入任务",
      interaction_mode: "ask",
      board_task_execution_action: "resume_confirmed",
    });
  }

  return (
    <div data-board-generation-confirmation-card className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
      <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">
        {isExistingBoardWrite ? "板书写入已准备好" : "学习需求已清晰"}
      </p>
      <p className="mt-2 text-sm leading-6 text-emerald-950">
        {isExistingBoardWrite ? boardTask?.question_or_topic || "当前板书写入任务已准备好。" : clarityStatus.summary || clarityStatus.reason}
      </p>
      {isPendingEvidenceLoading ? (
        <p className="mt-3 border-t border-emerald-100 pt-3 text-xs leading-6 text-emerald-900/80">正在核对本轮资料证据。</p>
      ) : candidateEvidenceBundle ? (
        <div className="mt-3 border-t border-emerald-100 pt-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">本轮资料依据</p>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {visualItems.length ? (
                <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
                  图表 {visualItems.length}
                </span>
              ) : null}
              <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
                {requiresEvidenceConfirmation ? "待确认" : "已确认"}
              </span>
            </div>
          </div>
          <div className="mt-3 space-y-3">
            {candidateEvidenceBundle.evidence_items.slice(0, 4).map((item) => (
              <EvidenceSummary key={item.id} item={item} />
            ))}
          </div>
          {visualItems.length ? (
            <div className="mt-3 border-t border-emerald-100 pt-3">
              <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">本轮资料图表</p>
              <div className="mt-2 space-y-2">
                {visualItems.map((item) => (
                  <VisualEvidenceSummary key={item.visual_id} item={item} />
                ))}
              </div>
            </div>
          ) : null}
          {requiresEvidenceConfirmation ? (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => void onEvidenceAction(candidateEvidenceBundle.id, "confirm")}
                disabled={isChatBusy}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-300 bg-white px-3 text-xs font-semibold text-emerald-800 transition hover:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Check className="h-4 w-4" />
                {isExistingBoardWrite ? "确认并写入" : "使用资料"}
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
      {isExistingBoardWrite && candidateEvidenceBundle?.status === "confirmed" ? (
        <button
          type="button"
          onClick={resumeConfirmedBoardTask}
          disabled={isChatBusy}
          className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          继续写入
        </button>
      ) : isExistingBoardWrite ? (
        <p className="mt-3 text-xs leading-6 text-emerald-900/80">确认资料后会自动继续写入右侧板书。</p>
      ) : (
        <button
          type="button"
          onClick={submitBoardGeneration}
          disabled={startDisabled}
          className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          开始生成板书
        </button>
      )}
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

function VisualEvidenceSummary({ item }: { item: RetrievalVisualEvidence }) {
  const pageLabel = item.page_range || (item.page_no != null ? `第 ${item.page_no} 页` : "");
  const location = [item.source_title, pageLabel].filter(Boolean).join(" / ");
  return (
    <div className="grid gap-1 text-xs leading-5 text-emerald-950">
      <p className="flex items-start gap-2 font-semibold">
        <ImageIcon className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
        <span className="min-w-0 break-words">{item.caption || "资料图表"}</span>
      </p>
      {location ? <p className="pl-6 text-[11px] text-gray-600">{location}</p> : null}
    </div>
  );
}
