import clsx from "clsx";
import { CheckCircle2, Radio } from "lucide-react";
import { useState } from "react";

import type {
  ChatRequestPayload,
  LearningClarificationStatus,
  ResourceReferenceAction,
  ResourceReferencePrompt,
} from "@/types";

type BoardGenerationConfirmationCardProps = {
  clarityStatus: LearningClarificationStatus;
  isChatBusy: boolean;
  referencePrompt: ResourceReferencePrompt | null;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
};

export function BoardGenerationConfirmationCard({
  clarityStatus,
  isChatBusy,
  referencePrompt,
  onSubmitChat,
}: BoardGenerationConfirmationCardProps) {
  const [referenceChoiceState, setReferenceChoiceState] = useState<{
    action: ResourceReferenceAction | null;
    promptKey: string;
  }>({ action: null, promptKey: "" });
  const referencePromptKey = referencePrompt ? `${referencePrompt.resource_id}:${referencePrompt.chapter_id}` : "";
  const referenceChoice =
    referenceChoiceState.promptKey === referencePromptKey ? referenceChoiceState.action : null;

  function submitBoardGeneration(referenceAction?: ResourceReferenceAction) {
    const payload: ChatRequestPayload = {
      message: "开始生成板书",
      interaction_mode: "ask",
      board_generation_action: "start",
    };
    if (referencePrompt && referenceAction) {
      payload.resource_reference_action = referenceAction;
      payload.resource_reference_resource_id = referencePrompt.resource_id;
      payload.resource_reference_chapter_id = referencePrompt.chapter_id;
    }
    void onSubmitChat(payload);
  }

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
      <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">学习需求已清晰</p>
      <p className="mt-2 text-sm leading-6 text-emerald-950">{clarityStatus.summary || clarityStatus.reason}</p>
      {referencePrompt ? (
        <div className="mt-3 border-t border-emerald-100 pt-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-violet-700">资料依据</p>
          <p className="mt-2 text-sm leading-6 text-violet-950">{referencePrompt.question}</p>
          <p className="mt-2 text-xs leading-6 text-violet-900/80">{referencePrompt.reason}</p>
          <div className="mt-3 grid gap-2">
            {(["confirm", "skip"] as const).map((action) => {
              const selected = referenceChoice === action;
              const label = action === "confirm" ? referencePrompt.confirm_label : referencePrompt.skip_label;
              return (
                <button
                  key={action}
                  type="button"
                  onClick={() => setReferenceChoiceState({ action, promptKey: referencePromptKey })}
                  className={clsx(
                    "flex w-full items-center gap-2 rounded-xl border bg-white px-4 py-3 text-left text-sm font-semibold transition",
                    selected
                      ? "border-emerald-400 text-emerald-950 shadow-sm"
                      : "border-emerald-100 text-gray-900 hover:border-emerald-300"
                  )}
                >
                  {selected ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  ) : (
                    <Radio className="h-4 w-4 shrink-0 text-gray-400" />
                  )}
                  <span>{label}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs leading-6 text-emerald-900/80">接下来将基于这份学习需求生成板书，是否开始？</p>
      )}
      <button
        type="button"
        onClick={() => {
          if (!referencePrompt) {
            submitBoardGeneration();
            return;
          }
          if (referenceChoice) {
            submitBoardGeneration(referenceChoice);
          }
        }}
        disabled={isChatBusy || Boolean(referencePrompt && !referenceChoice)}
        className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        开始生成板书
      </button>
    </div>
  );
}
