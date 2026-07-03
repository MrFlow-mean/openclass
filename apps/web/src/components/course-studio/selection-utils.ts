import type { SelectionRef } from "@/types";

export type SelectionPopoverPosition = {
  top: number;
  left: number;
};

export function sameSelection(left: SelectionRef | null, right: SelectionRef | null) {
  if (!left || !right) {
    return left === right;
  }
  return (
    left.kind === right.kind &&
    left.location_kind === right.location_kind &&
    left.lesson_id === right.lesson_id &&
    left.block_id === right.block_id &&
    left.document_id === right.document_id &&
    left.segment_id === right.segment_id &&
    left.before_text === right.before_text &&
    left.after_text === right.after_text &&
    left.text_hash === right.text_hash &&
    left.excerpt === right.excerpt
  );
}

export function samePopoverPosition(
  left: SelectionPopoverPosition | null,
  right: SelectionPopoverPosition | null
) {
  if (!left || !right) {
    return left === right;
  }
  return Math.abs(left.left - right.left) < 1 && Math.abs(left.top - right.top) < 1;
}

function clampSelectionPopover(left: number, top: number): SelectionPopoverPosition {
  if (typeof window === "undefined") {
    return { left, top };
  }
  return {
    left: Math.max(88, Math.min(left, window.innerWidth - 88)),
    top: Math.max(12, Math.min(top, window.innerHeight - 80)),
  };
}

export function popoverPositionFromDomSelection(): SelectionPopoverPosition | null {
  if (typeof window === "undefined") {
    return null;
  }
  const activeSelection = window.getSelection();
  if (!activeSelection || activeSelection.rangeCount === 0) {
    return null;
  }
  const rect = activeSelection.getRangeAt(0).getBoundingClientRect();
  if (!rect.width && !rect.height) {
    return null;
  }
  return clampSelectionPopover(rect.left + rect.width / 2, rect.top - 44);
}
