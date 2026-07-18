"use client";

import { useState } from "react";

import { CodexModelSettingsPicker } from "@/components/course-studio/codex-model-settings-picker";
import {
  findModelOption,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type { AIModelOption, AIModelSelection } from "@/types";

export function SourceCatalogModelPicker({
  options,
  selection,
  defaultSelection,
  disabled,
  onChange,
}: {
  options: AIModelOption[];
  selection: AIModelSelection;
  defaultSelection: AIModelSelection;
  disabled: boolean;
  onChange: (selection: AIModelSelection) => void;
}) {
  const [open, setOpen] = useState(false);
  const selectedOption = findModelOption(options, selection);
  const enabledOption = selectedOption?.enabled
    ? selectedOption
    : options.find((option) => option.enabled) ?? null;
  const displayOption = enabledOption ?? selectedOption ?? options[0] ?? null;
  const normalizedSelection = displayOption
    ? selectionForModelOption(displayOption, selection)
    : selection;

  return (
    <div className="mt-3" data-testid="source-catalog-model-picker">
      <p className="text-[11px] font-bold uppercase tracking-widest text-gray-500">
        目录提取模型
      </p>
      <div className="mt-2">
        <CodexModelSettingsPicker
          open={open}
          onOpenChange={setOpen}
          selectedModel={normalizedSelection}
          selectedOption={displayOption}
          defaultSelection={defaultSelection}
          options={options}
          onChange={onChange}
          disabled={disabled || !enabledOption}
          contextLabel="目录提取模型设置"
          testIdPrefix="source-catalog-model"
          preferredPlacement="below"
          preferredSubmenuSide="left"
        />
      </div>
    </div>
  );
}
