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
      className="flex h-10 max-w-[260px] items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 text-gray-700 transition hover:border-gray-300 hover:bg-gray-50"
    >
      <BrainCircuit className="h-4 w-4 shrink-0 text-gray-500" />
      <span className="shrink-0 text-[11px] font-bold uppercase tracking-wider text-gray-600">板书 AI</span>
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
        className="min-w-0 max-w-36 flex-1 border-0 bg-transparent text-[11px] font-medium text-gray-500 outline-none"
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
