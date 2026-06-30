import { BrainCircuit } from "lucide-react";

import { modelOptionKey, modelSelectionKey, PROVIDER_LABELS } from "@/components/course-studio/model-catalog";
import type { AIModelOption, AIModelSelection } from "@/types";

type BoardModelPickerProps = {
  modelOptions: AIModelOption[];
  selectedBoardModel: AIModelSelection;
  selectedBoardOption: AIModelOption | null;
  onSelectBoardModel: (option: AIModelOption) => void;
};

export function BoardModelPicker({
  modelOptions,
  selectedBoardModel,
  selectedBoardOption,
  onSelectBoardModel,
}: BoardModelPickerProps) {
  const selectedBoardModelKey = modelSelectionKey(selectedBoardModel);
  const boardModelValue = modelOptions.some((option) => modelOptionKey(option) === selectedBoardModelKey)
    ? selectedBoardModelKey
    : "";
  const boardModelLabel =
    selectedBoardOption?.label ?? `${PROVIDER_LABELS[selectedBoardModel.provider]} ${selectedBoardModel.model}`;

  return (
    <label
      title={`板书编辑 AI：${boardModelLabel}`}
      className="flex h-10 min-w-[236px] max-w-[286px] items-center gap-2 rounded-lg border-2 border-amber-400 bg-amber-50 px-3 text-amber-950 shadow-sm transition hover:border-amber-500 hover:bg-amber-100"
    >
      <BrainCircuit className="h-4 w-4 shrink-0 text-amber-700" />
      <span className="shrink-0 text-[11px] font-bold uppercase tracking-wider text-amber-800">板书编辑 AI</span>
      <select
        aria-label="板书编辑 AI 模型"
        value={boardModelValue}
        disabled={!modelOptions.length}
        onChange={(event) => {
          const nextOption = modelOptions.find((option) => modelOptionKey(option) === event.target.value);
          if (nextOption) {
            onSelectBoardModel(nextOption);
          }
        }}
        className="min-w-0 flex-1 border-0 bg-transparent text-[11px] font-semibold text-amber-950 outline-none"
      >
        {boardModelValue ? null : <option value="">{boardModelLabel}</option>}
        {modelOptions.map((option) => (
          <option key={`board-${modelOptionKey(option)}`} value={modelOptionKey(option)} disabled={!option.enabled}>
            {option.label}
            {option.configured ? "" : " / 未配置"}
          </option>
        ))}
      </select>
    </label>
  );
}
