"use client";

import { Bot, ChevronDown } from "lucide-react";

import type { AIAgentBackendOption } from "@/types";

export const FALLBACK_AGENT_BACKENDS: AIAgentBackendOption[] = [
  {
    id: "codex",
    label: "Codex Agent",
    description: "使用当前 Codex Agent 运行框架。",
    enabled: true,
  },
  {
    id: "pi",
    label: "Pi Agent",
    description: "Pi Agent 运行框架正在接入。",
    enabled: false,
  },
];

export function AgentBackendPicker({
  ariaLabel,
  options,
  value,
  disabled = false,
  onChange,
  testId,
}: {
  ariaLabel: string;
  options: AIAgentBackendOption[];
  value: AIAgentBackendOption["id"];
  disabled?: boolean;
  onChange: (backend: AIAgentBackendOption["id"]) => void;
  testId: string;
}) {
  const selected = options.find((option) => option.id === value) ?? options[0];

  return (
    <label
      className="relative flex h-10 min-w-0 items-center rounded-full bg-gray-100 text-sm text-gray-800 transition focus-within:ring-2 focus-within:ring-gray-300"
      title={selected?.description}
    >
      <Bot className="pointer-events-none ml-3 h-4 w-4 shrink-0 text-gray-600" />
      <select
        aria-label={ariaLabel}
        data-testid={testId}
        value={selected?.id ?? "codex"}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as AIAgentBackendOption["id"])}
        className="h-full min-w-0 flex-1 appearance-none bg-transparent pl-2 pr-7 font-medium outline-none disabled:cursor-not-allowed disabled:opacity-50"
      >
        {options.map((option) => (
          <option key={option.id} value={option.id} disabled={!option.enabled}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 h-3.5 w-3.5 text-gray-400" />
    </label>
  );
}
