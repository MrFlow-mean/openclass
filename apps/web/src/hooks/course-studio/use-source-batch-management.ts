"use client";

import { useState } from "react";

import { api } from "@/lib/api";

type UseSourceBatchManagementOptions = {
  packageId: string;
  sourceIds: string[];
  disabled: boolean;
  onRemoved: (sourceIds: string[]) => void;
  onError: (message: string) => void;
};

export function useSourceBatchManagement({
  packageId,
  sourceIds,
  disabled,
  onRemoved,
  onError,
}: UseSourceBatchManagementOptions) {
  const [isActive, setIsActive] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);
  const [storedSelectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set());
  const availableSourceIds = new Set(sourceIds);
  const selectedSourceIds = new Set(
    [...storedSelectedSourceIds].filter((sourceId) => availableSourceIds.has(sourceId))
  );

  const selectedCount = selectedSourceIds.size;
  const allSelected = sourceIds.length > 0 && selectedCount === sourceIds.length;

  function start() {
    if (!disabled && sourceIds.length > 0) {
      setIsActive(true);
    }
  }

  function cancel() {
    if (isRemoving) {
      return;
    }
    setIsActive(false);
    setSelectedSourceIds(new Set());
  }

  function clear() {
    if (!isRemoving) {
      setSelectedSourceIds(new Set());
    }
  }

  function toggle(sourceId: string) {
    if (disabled || isRemoving) {
      return;
    }
    setSelectedSourceIds((current) => {
      const next = new Set(current);
      if (next.has(sourceId)) {
        next.delete(sourceId);
      } else {
        next.add(sourceId);
      }
      return next;
    });
  }

  function toggleAll() {
    if (disabled || isRemoving) {
      return;
    }
    setSelectedSourceIds(allSelected ? new Set() : new Set(sourceIds));
  }

  async function removeSelected() {
    const selectedIds = sourceIds.filter((sourceId) => selectedSourceIds.has(sourceId));
    if (disabled || isRemoving || selectedIds.length === 0) {
      return;
    }
    if (!window.confirm(`确定删除选中的 ${selectedIds.length} 份资料吗？删除后将无法在本课程中检索或引用。`)) {
      return;
    }

    setIsRemoving(true);
    const removedIds: string[] = [];
    const failedIds: string[] = [];
    try {
      for (const sourceId of selectedIds) {
        try {
          await api.deletePackageSource(packageId, sourceId);
          removedIds.push(sourceId);
        } catch {
          failedIds.push(sourceId);
        }
      }
      if (removedIds.length > 0) {
        onRemoved(removedIds);
      }
      setSelectedSourceIds(new Set(failedIds));
      if (failedIds.length === 0) {
        setIsActive(false);
      } else {
        onError(`${removedIds.length} 份资料删除成功，${failedIds.length} 份删除失败，请重试。`);
      }
    } finally {
      setIsRemoving(false);
    }
  }

  return {
    isActive,
    isRemoving,
    selectedSourceIds,
    selectedCount,
    allSelected,
    start,
    cancel,
    clear,
    toggle,
    toggleAll,
    removeSelected,
  };
}
