import clsx from "clsx";
import { Check, Cpu } from "lucide-react";

import {
  PROVIDER_LABELS,
  modelButtonLabel,
  optionToSelection,
} from "@/components/course-studio/model-catalog";
import type { AIModelOption, AIModelSelection } from "@/types";

type ModelSelectionPanelProps = {
  options: AIModelOption[];
  selectedModel: AIModelSelection;
  selectedOption: AIModelOption | null;
  onSelect: (selection: AIModelSelection) => void;
};

export function ModelSelectionPanel({
  options,
  selectedModel,
  selectedOption,
  onSelect,
}: ModelSelectionPanelProps) {
  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">
              当前模型
            </p>
            <p className="mt-1 text-sm font-semibold text-gray-950">
              {modelButtonLabel(selectedOption, selectedModel)}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              此处与聊天输入框共用同一个模型选择状态。
            </p>
          </div>
          <Cpu className="h-5 w-5 shrink-0 text-gray-400" />
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-950">可用模型</h3>
        <div className="mt-3 space-y-2">
          {options.map((option) => {
            const active =
              option.provider === selectedModel.provider &&
              option.model === selectedModel.model;
            return (
              <button
                key={`${option.provider}:${option.model}`}
                type="button"
                disabled={!option.enabled}
                onClick={() => onSelect(optionToSelection(option))}
                className={clsx(
                  "w-full rounded-lg border p-3 text-left transition",
                  active
                    ? "border-gray-950 bg-gray-950 text-white"
                    : "border-gray-200 bg-white hover:border-gray-400",
                  !option.enabled && "cursor-not-allowed opacity-50",
                )}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="font-semibold">{option.label}</span>
                  {active ? <Check className="h-4 w-4" /> : null}
                </span>
                <span
                  className={clsx(
                    "mt-1 block text-xs",
                    active ? "text-gray-300" : "text-gray-500",
                  )}
                >
                  {PROVIDER_LABELS[option.provider]} · {option.enabled ? "可用" : "尚未配置"}
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
