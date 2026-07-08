import type { ChatRequestPayload, LearningClarificationStatus } from "@/types";

type BoardGenerationConfirmationCardProps = {
  clarityStatus: LearningClarificationStatus;
  isChatBusy: boolean;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
};

export function BoardGenerationConfirmationCard({
  clarityStatus,
  isChatBusy,
  onSubmitChat,
}: BoardGenerationConfirmationCardProps) {
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
      <p className="mt-2 text-xs leading-6 text-emerald-900/80">接下来将基于这份学习需求生成板书，是否开始？</p>
      <button
        type="button"
        onClick={submitBoardGeneration}
        disabled={isChatBusy}
        className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        开始生成板书
      </button>
    </div>
  );
}
