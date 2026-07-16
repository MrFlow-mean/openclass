import clsx from "clsx";
import { FolderInput, ListChecks, LoaderCircle, Trash2, X } from "lucide-react";

import type { CoursePackage } from "@/types";

type BatchToggleProps = {
  isActive: boolean;
  disabled: boolean;
  manageLabel: string;
  cancelLabel: string;
  onStart: () => void;
  onCancel: () => void;
};

export function HomeLessonBatchToggle({
  isActive,
  disabled,
  manageLabel,
  cancelLabel,
  onStart,
  onCancel,
}: BatchToggleProps) {
  return (
    <button
      type="button"
      onClick={isActive ? onCancel : onStart}
      disabled={disabled}
      className={clsx(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-[10px] font-semibold transition",
        isActive
          ? "border-stone-950 bg-stone-950 text-white"
          : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:text-stone-950",
        "disabled:cursor-not-allowed disabled:opacity-40"
      )}
      aria-pressed={isActive}
    >
      {isActive ? <X className="h-3.5 w-3.5" /> : <ListChecks className="h-3.5 w-3.5" />}
      {isActive ? cancelLabel : manageLabel}
    </button>
  );
}

type BatchToolbarProps = {
  selectedCount: number;
  allVisibleSelected: boolean;
  targetPackageId: string;
  packages: CoursePackage[];
  action: "move" | "delete" | null;
  selectedLabel: (count: number) => string;
  selectAllLabel: string;
  clearLabel: string;
  choosePackageLabel: string;
  moveLabel: string;
  deleteLabel: string;
  onToggleAll: () => void;
  onClear: () => void;
  onTargetPackageChange: (packageId: string) => void;
  onMove: () => void;
  onDelete: () => void;
};

export function HomeLessonBatchToolbar({
  selectedCount,
  allVisibleSelected,
  targetPackageId,
  packages,
  action,
  selectedLabel,
  selectAllLabel,
  clearLabel,
  choosePackageLabel,
  moveLabel,
  deleteLabel,
  onToggleAll,
  onClear,
  onTargetPackageChange,
  onMove,
  onDelete,
}: BatchToolbarProps) {
  const isBusy = action !== null;

  return (
    <div className="mb-3 rounded-2xl border border-stone-200 bg-white p-2.5 shadow-[0_10px_24px_rgba(15,23,42,0.06)]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-stone-800">{selectedLabel(selectedCount)}</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleAll}
            disabled={isBusy}
            className="rounded-lg px-2 py-1 text-[10px] font-semibold text-stone-600 transition hover:bg-stone-100 disabled:opacity-40"
          >
            {allVisibleSelected ? clearLabel : selectAllLabel}
          </button>
          {selectedCount > 0 && !allVisibleSelected ? (
            <button
              type="button"
              onClick={onClear}
              disabled={isBusy}
              className="rounded-lg px-2 py-1 text-[10px] font-semibold text-stone-400 transition hover:bg-stone-100 hover:text-stone-700 disabled:opacity-40"
            >
              {clearLabel}
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-2 flex items-center gap-1.5">
        <select
          value={targetPackageId}
          onChange={(event) => onTargetPackageChange(event.target.value)}
          disabled={!selectedCount || !packages.length || isBusy}
          className="min-w-0 flex-1 rounded-xl border border-stone-200 bg-stone-50 px-2 py-2 text-[11px] text-stone-700 outline-none transition focus:border-stone-950 disabled:opacity-45"
          aria-label={choosePackageLabel}
        >
          <option value="">{choosePackageLabel}</option>
          {packages.map((packageItem) => (
            <option key={packageItem.id} value={packageItem.id}>
              {packageItem.title}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onMove}
          disabled={!selectedCount || !targetPackageId || isBusy}
          className="inline-flex h-9 items-center gap-1 rounded-xl bg-stone-950 px-2.5 text-[11px] font-semibold text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:opacity-35"
        >
          {action === "move" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <FolderInput className="h-3.5 w-3.5" />}
          {moveLabel}
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={!selectedCount || isBusy}
          className="inline-flex h-9 items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-2.5 text-rose-600 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-35"
          aria-label={deleteLabel}
          title={deleteLabel}
        >
          {action === "delete" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        </button>
      </div>
    </div>
  );
}
