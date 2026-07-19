import clsx from "clsx";
import { ArrowUpDown, ListChecks, LoaderCircle, Trash2, X } from "lucide-react";

export type SourceSortOption = "uploaded_desc" | "uploaded_asc" | "name_asc" | "name_desc";

type SourceBatchControlsProps = {
  sourceCount: number;
  selectedCount: number;
  allSelected: boolean;
  isActive: boolean;
  isRemoving: boolean;
  disabled: boolean;
  sortOption: SourceSortOption;
  onSortChange: (sortOption: SourceSortOption) => void;
  onStart: () => void;
  onCancel: () => void;
  onToggleAll: () => void;
  onClear: () => void;
  onRemove: () => void;
};

export function SourceBatchControls({
  sourceCount,
  selectedCount,
  allSelected,
  isActive,
  isRemoving,
  disabled,
  sortOption,
  onSortChange,
  onStart,
  onCancel,
  onToggleAll,
  onClear,
  onRemove,
}: SourceBatchControlsProps) {
  const sortControl = (
    <label className="relative inline-flex h-7 min-w-0 items-center">
      <ArrowUpDown className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-gray-400" />
      <select
        value={sortOption}
        onChange={(event) => onSortChange(event.target.value as SourceSortOption)}
        disabled={isRemoving}
        aria-label="资料排序"
        className="h-7 max-w-32 appearance-none rounded-md border border-gray-200 bg-white py-0 pl-7 pr-2 text-[11px] font-medium text-gray-600 outline-none transition hover:border-gray-300 hover:text-black focus:border-gray-400 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <option value="uploaded_desc">上传时间：最新</option>
        <option value="uploaded_asc">上传时间：最早</option>
        <option value="name_asc">名称：A–Z</option>
        <option value="name_desc">名称：Z–A</option>
      </select>
    </label>
  );

  if (!isActive) {
    return (
      <div className="flex items-center justify-between gap-2 px-1 py-0.5">
        <span className="text-[11px] text-gray-400">已上传 {sourceCount} 份资料</span>
        <div className="flex min-w-0 items-center gap-1.5">
          {sortControl}
          <button
            type="button"
            onClick={onStart}
            disabled={disabled || sourceCount === 0}
            className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2 text-[11px] font-medium text-gray-600 transition hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ListChecks className="h-3.5 w-3.5" />
            批量管理
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-blue-100 bg-blue-50/60 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-blue-950">
          已选 {selectedCount} / {sourceCount}
        </span>
        <div className="flex min-w-0 items-center gap-1.5">
          {sortControl}
          <button
            type="button"
            onClick={onCancel}
            disabled={isRemoving}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-500 transition hover:bg-white hover:text-black disabled:opacity-40"
            aria-label="退出批量管理"
          >
            <X className="h-3.5 w-3.5" />
            完成
          </button>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <button
          type="button"
          onClick={onToggleAll}
          disabled={isRemoving}
          className="rounded-md border border-blue-100 bg-white px-2 py-1.5 text-[11px] font-medium text-blue-700 transition hover:border-blue-200 hover:bg-blue-50 disabled:opacity-40"
        >
          {allSelected ? "取消全选" : "全选"}
        </button>
        {selectedCount > 0 && !allSelected ? (
          <button
            type="button"
            onClick={onClear}
            disabled={isRemoving}
            className="rounded-md px-2 py-1.5 text-[11px] font-medium text-gray-500 transition hover:bg-white hover:text-black disabled:opacity-40"
          >
            清空
          </button>
        ) : null}
        <button
          type="button"
          onClick={onRemove}
          disabled={disabled || selectedCount === 0 || isRemoving}
          className={clsx(
            "ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition",
            "border-rose-200 bg-rose-50 text-rose-600 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-40"
          )}
          aria-label="批量删除已选资料"
        >
          {isRemoving ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          {isRemoving ? "删除中" : "删除"}
        </button>
      </div>
    </div>
  );
}
