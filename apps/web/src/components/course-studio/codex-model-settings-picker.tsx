import clsx from "clsx";
import { Check, ChevronDown, ChevronRight, RotateCcw, Zap } from "lucide-react";
import { useState } from "react";

import {
  modelOptionKey,
  modelSelectionKey,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type {
  AIModelOption,
  AIModelSelection,
  AIReasoningEffortOption,
  AIServiceTierOption,
} from "@/types";

type SettingsMenu = "model" | "reasoning" | "speed" | null;

const REASONING_EFFORT_LABELS: Record<string, string> = {
  none: "无",
  minimal: "极低",
  low: "轻度",
  medium: "中",
  high: "高",
  xhigh: "极高",
  max: "最高",
  ultra: "极高",
};

function reasoningEffortLabel(effort: string | null | undefined) {
  if (!effort) {
    return "默认";
  }
  return REASONING_EFFORT_LABELS[effort] ?? effort;
}

function reasoningEffortDescription(option: AIReasoningEffortOption) {
  return option.reasoning_effort === "ultra" ? "使用更多额度" : "";
}

function shortModelLabel(option: AIModelOption | null, selection: AIModelSelection) {
  const source = option?.label || selection.model;
  return source
    .replace(/^OpenAI Codex\s+/i, "")
    .replace(/^GPT-/i, "")
    .replaceAll("-", " ");
}

function serviceTierLabel(option: AIServiceTierOption) {
  return option.id === "priority" ? "快速" : option.name || option.id;
}

function serviceTierDescription(option: AIServiceTierOption) {
  return option.id === "priority" ? "1.5 倍速，用量更多" : option.description;
}

function SettingsRow({
  label,
  value,
  active,
  disabled = false,
  onClick,
}: {
  label: string;
  value: string;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={active}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "flex h-10 w-full items-center gap-3 rounded-lg px-2.5 text-left text-sm transition-colors",
        active ? "bg-gray-100" : "hover:bg-gray-50",
        disabled && "cursor-not-allowed opacity-45"
      )}
    >
      <span className="font-semibold text-gray-950">{label}</span>
      <span className="ml-auto max-w-28 truncate text-gray-500">{value}</span>
      <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" />
    </button>
  );
}

function OptionButton({
  label,
  description,
  selected,
  ariaLabel,
  disabled = false,
  onClick,
}: {
  label: string;
  description?: string;
  selected: boolean;
  ariaLabel: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      className="flex min-h-10 w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm text-gray-900 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{label}</span>
        {description ? <span className="mt-0.5 block text-xs leading-4 text-gray-400">{description}</span> : null}
      </span>
      {selected ? <Check className="h-4 w-4 shrink-0 text-gray-900" /> : null}
    </button>
  );
}

export function CodexModelSettingsPicker({
  open,
  onOpenChange,
  selectedModel,
  selectedOption,
  defaultSelection,
  options,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedModel: AIModelSelection;
  selectedOption: AIModelOption | null;
  defaultSelection: AIModelSelection;
  options: AIModelOption[];
  onChange: (selection: AIModelSelection) => void;
}) {
  const [activeMenu, setActiveMenu] = useState<SettingsMenu>(null);
  const normalizedSelection = selectedOption
    ? selectionForModelOption(selectedOption, selectedModel)
    : selectedModel;
  const reasoningOptions = selectedOption?.supported_reasoning_efforts ?? [];
  const serviceTiers = selectedOption?.service_tiers ?? [];
  const selectedServiceTier =
    serviceTiers.find((option) => option.id === normalizedSelection.service_tier) ?? null;
  const hasSelectableSpeed = serviceTiers.length > 0;
  const modelLabel = shortModelLabel(selectedOption, normalizedSelection);
  const effortLabel = reasoningEffortLabel(normalizedSelection.reasoning_effort);
  const speedLabel = selectedServiceTier ? serviceTierLabel(selectedServiceTier) : "标准";

  function applySelection(selection: AIModelSelection) {
    setActiveMenu(null);
    onChange(selection);
  }

  function togglePicker() {
    setActiveMenu(null);
    onOpenChange(!open);
  }

  function resetDefaults() {
    const defaultOption =
      options.find((option) => modelOptionKey(option) === modelSelectionKey(defaultSelection)) ?? null;
    applySelection(defaultOption ? selectionForModelOption(defaultOption, defaultSelection) : defaultSelection);
  }

  return (
    <div className="relative min-w-0">
      <button
        type="button"
        data-testid="codex-model-settings-button"
        aria-expanded={open}
        aria-label={`模型设置，当前 ${modelLabel}，推理强度 ${effortLabel}，速度 ${speedLabel}`}
        onClick={togglePicker}
        className="flex h-10 w-full items-center justify-center gap-1.5 rounded-full bg-gray-100 px-3 text-sm text-gray-900 transition-colors hover:bg-gray-200"
      >
        <Zap className="h-3.5 w-3.5 shrink-0 fill-current" />
        <span className="truncate font-medium">{modelLabel}</span>
        <span className="shrink-0 font-medium text-violet-600">{effortLabel}</span>
        <ChevronDown className={clsx("ml-auto h-4 w-4 shrink-0 text-gray-400 transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <>
          <div
            data-testid="codex-model-settings-menu"
            className="absolute bottom-full left-0 z-40 mb-2 w-56 rounded-xl border border-gray-200 bg-white p-1.5 shadow-[0_18px_50px_rgba(0,0,0,0.16)]"
          >
            <SettingsRow
              label="模型"
              value={modelLabel}
              active={activeMenu === "model"}
              onClick={() => setActiveMenu((current) => (current === "model" ? null : "model"))}
            />
            <SettingsRow
              label="推理强度"
              value={effortLabel}
              active={activeMenu === "reasoning"}
              disabled={!reasoningOptions.length}
              onClick={() => setActiveMenu((current) => (current === "reasoning" ? null : "reasoning"))}
            />
            <SettingsRow
              label="速度"
              value={hasSelectableSpeed ? speedLabel : "仅标准"}
              active={activeMenu === "speed"}
              disabled={!hasSelectableSpeed}
              onClick={() => setActiveMenu((current) => (current === "speed" ? null : "speed"))}
            />
            <div className="my-1 h-px bg-gray-100" />
            <button
              type="button"
              onClick={resetDefaults}
              className="flex h-10 w-full items-center rounded-lg px-2.5 text-left text-sm text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-900"
            >
              <span>重置为默认设置</span>
              <RotateCcw className="ml-auto h-4 w-4" />
            </button>
          </div>

          {activeMenu ? (
            <div
              data-testid={`codex-model-${activeMenu}-menu`}
              className="absolute bottom-full left-[calc(14rem+0.5rem)] z-50 mb-2 max-h-[420px] w-72 overflow-y-auto rounded-xl border border-gray-200 bg-white p-1.5 shadow-[0_18px_50px_rgba(0,0,0,0.16)]"
            >
              <p className="px-2.5 pb-1 pt-1.5 text-sm text-gray-400">
                {activeMenu === "model" ? "模型" : activeMenu === "reasoning" ? "推理强度" : "速度"}
              </p>

              {activeMenu === "model"
                ? options.map((option) => (
                    <OptionButton
                      key={modelOptionKey(option)}
                      label={shortModelLabel(option, normalizedSelection)}
                      selected={modelOptionKey(option) === modelSelectionKey(normalizedSelection)}
                      ariaLabel={`选择模型 ${shortModelLabel(option, normalizedSelection)}`}
                      disabled={!option.enabled}
                      onClick={() => applySelection(selectionForModelOption(option, normalizedSelection))}
                    />
                  ))
                : null}

              {activeMenu === "reasoning"
                ? reasoningOptions.map((option) => (
                    <OptionButton
                      key={option.reasoning_effort}
                      label={reasoningEffortLabel(option.reasoning_effort)}
                      description={reasoningEffortDescription(option)}
                      selected={option.reasoning_effort === normalizedSelection.reasoning_effort}
                      ariaLabel={`推理强度 ${reasoningEffortLabel(option.reasoning_effort)}${option.reasoning_effort === "ultra" ? " Ultra" : ""}`}
                      onClick={() =>
                        applySelection({
                          ...normalizedSelection,
                          reasoning_effort: option.reasoning_effort,
                        })
                      }
                    />
                  ))
                : null}

              {activeMenu === "speed" ? (
                <>
                  <OptionButton
                    label="标准"
                    description="默认速度"
                    selected={!normalizedSelection.service_tier}
                    ariaLabel="速度 标准"
                    onClick={() => applySelection({ ...normalizedSelection, service_tier: null })}
                  />
                  {serviceTiers.map((option) => (
                    <OptionButton
                      key={option.id}
                      label={serviceTierLabel(option)}
                      description={serviceTierDescription(option)}
                      selected={option.id === normalizedSelection.service_tier}
                      ariaLabel={`速度 ${serviceTierLabel(option)}`}
                      onClick={() => applySelection({ ...normalizedSelection, service_tier: option.id })}
                    />
                  ))}
                </>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
