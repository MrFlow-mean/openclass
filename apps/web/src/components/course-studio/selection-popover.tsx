"use client";

import { TextQuote } from "lucide-react";

import type { SelectionRef } from "@/types";
import type { SelectionPopoverPosition } from "@/components/course-studio/selection-utils";

type SelectionPopoverProps = {
  selection: SelectionRef | null;
  position: SelectionPopoverPosition | null;
  onFocusComposerWithSelection: () => void;
};

export function SelectionPopover({
  selection,
  position,
  onFocusComposerWithSelection,
}: SelectionPopoverProps) {
  if (!selection || !position) {
    return null;
  }

  return (
    <div
      className="fixed z-[90] flex -translate-x-1/2 items-center overflow-hidden rounded-xl border border-gray-200 bg-white text-[13px] font-medium text-gray-800 shadow-lg"
      style={{ left: position.left, top: position.top }}
      onMouseDown={(event) => event.preventDefault()}
    >
      <button
        type="button"
        onClick={onFocusComposerWithSelection}
        className="inline-flex h-10 items-center gap-2 px-3.5 transition-colors hover:bg-gray-50"
      >
        <TextQuote className="h-4 w-4" />
        标记 TargetRange
      </button>
    </div>
  );
}
