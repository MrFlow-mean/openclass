"use client";

import {
  findModelOption,
  modelOptionKey,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type { AIModelOption, AIModelSelection } from "@/types";

const REASONING_EFFORT_LABELS: Record<string, string> = {
  none: "无",
  minimal: "极低",
  low: "轻度",
  medium: "中",
  high: "高",
  xhigh: "极高",
  max: "最高",
  ultra: "极高（更多用量）",
};

function reasoningEffortLabel(effort: string) {
  return REASONING_EFFORT_LABELS[effort] ?? effort;
}

function serviceTierLabel(id: string, name: string) {
  return id === "priority" ? "快速" : name || id;
}

export function SourceCatalogModelPicker({
  options,
  selection,
  disabled,
  onChange,
}: {
  options: AIModelOption[];
  selection: AIModelSelection;
  disabled: boolean;
  onChange: (selection: AIModelSelection) => void;
}) {
  const selectedOption = findModelOption(options, selection);
  const enabledOption = selectedOption?.enabled
    ? selectedOption
    : options.find((option) => option.enabled) ?? null;
  const normalizedSelection = enabledOption
    ? selectionForModelOption(enabledOption, selection)
    : selection;
  const reasoningOptions = enabledOption?.supported_reasoning_efforts ?? [];
  const serviceTiers = enabledOption?.service_tiers ?? [];
  const controlsDisabled = disabled || !enabledOption;

  return (
    <div className="mt-3 grid grid-cols-2 gap-2">
      <label className="col-span-2 block text-[11px] font-bold uppercase tracking-widest text-gray-500">
        目录提取模型
        <select
          value={enabledOption ? modelOptionKey(enabledOption) : ""}
          onChange={(event) => {
            const option = options.find(
              (candidate) => modelOptionKey(candidate) === event.target.value
            );
            if (option?.enabled) {
              onChange(selectionForModelOption(option, normalizedSelection));
            }
          }}
          disabled={disabled || !options.some((option) => option.enabled)}
          className="mt-2 h-9 w-full rounded-md border border-gray-200 bg-white px-3 text-sm font-normal normal-case tracking-normal text-gray-900 outline-none transition focus:border-black disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
          aria-label="目录提取模型"
        >
          {options.map((option) => (
            <option key={modelOptionKey(option)} value={modelOptionKey(option)} disabled={!option.enabled}>
              {option.label}{option.enabled ? "" : "（未配置）"}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-[11px] font-bold uppercase tracking-widest text-gray-500">
        推理强度
        <select
          value={normalizedSelection.reasoning_effort ?? ""}
          onChange={(event) =>
            onChange({
              ...normalizedSelection,
              reasoning_effort: event.target.value || null,
            })
          }
          disabled={controlsDisabled || reasoningOptions.length === 0}
          className="mt-2 h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-sm font-normal normal-case tracking-normal text-gray-900 outline-none transition focus:border-black disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
          aria-label="目录提取推理强度"
        >
          {reasoningOptions.length === 0 ? <option value="">模型默认</option> : null}
          {reasoningOptions.map((option) => (
            <option key={option.reasoning_effort} value={option.reasoning_effort}>
              {reasoningEffortLabel(option.reasoning_effort)}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-[11px] font-bold uppercase tracking-widest text-gray-500">
        速度
        <select
          value={normalizedSelection.service_tier ?? ""}
          onChange={(event) =>
            onChange({
              ...normalizedSelection,
              service_tier: event.target.value || null,
            })
          }
          disabled={controlsDisabled || serviceTiers.length === 0}
          className="mt-2 h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-sm font-normal normal-case tracking-normal text-gray-900 outline-none transition focus:border-black disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
          aria-label="目录提取速度"
        >
          <option value="">{serviceTiers.length === 0 ? "仅标准" : "标准"}</option>
          {serviceTiers.map((option) => (
            <option key={option.id} value={option.id}>
              {serviceTierLabel(option.id, option.name)}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
