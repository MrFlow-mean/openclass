"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { WorkspaceState } from "@/types";

type HomeLessonBatchOptions = {
  availableLessonIds: string[];
  visibleLessonIds: string[];
  onWorkspaceChange: (workspace: WorkspaceState) => void;
  onError: (message: string) => void;
  moveErrorMessage: string;
  deleteErrorMessage: string;
  deleteConfirmMessage: (count: number) => string;
};

export function useHomeLessonBatch({
  availableLessonIds,
  visibleLessonIds,
  onWorkspaceChange,
  onError,
  moveErrorMessage,
  deleteErrorMessage,
  deleteConfirmMessage,
}: HomeLessonBatchOptions) {
  const [isActive, setIsActive] = useState(false);
  const [selectedLessonIds, setSelectedLessonIds] = useState<Set<string>>(() => new Set());
  const [targetPackageId, setTargetPackageId] = useState("");
  const [action, setAction] = useState<"move" | "delete" | null>(null);
  const available = new Set(availableLessonIds);
  const effectiveSelectedLessonIds = new Set(
    Array.from(selectedLessonIds).filter((lessonId) => available.has(lessonId))
  );

  useEffect(() => {
    if (!isActive) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsActive(false);
        setSelectedLessonIds(new Set());
        setTargetPackageId("");
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isActive]);

  function start() {
    setIsActive(true);
    setSelectedLessonIds(new Set());
    setTargetPackageId("");
  }

  function cancel() {
    setIsActive(false);
    setSelectedLessonIds(new Set());
    setTargetPackageId("");
  }

  function toggleLesson(lessonId: string) {
    setSelectedLessonIds((current) => {
      const next = new Set(Array.from(current).filter((currentId) => available.has(currentId)));
      if (next.has(lessonId)) {
        next.delete(lessonId);
      } else {
        next.add(lessonId);
      }
      return next;
    });
  }

  function toggleAllVisible() {
    setSelectedLessonIds((current) => {
      const next = new Set(Array.from(current).filter((lessonId) => available.has(lessonId)));
      const allVisibleSelected =
        visibleLessonIds.length > 0 && visibleLessonIds.every((lessonId) => current.has(lessonId));
      visibleLessonIds.forEach((lessonId) => {
        if (allVisibleSelected) {
          next.delete(lessonId);
        } else {
          next.add(lessonId);
        }
      });
      return next;
    });
  }

  async function moveSelected() {
    const lessonIds = Array.from(effectiveSelectedLessonIds);
    if (!lessonIds.length || !targetPackageId) {
      return;
    }
    setAction("move");
    try {
      const workspace = await api.batchLessons({
        action: "move",
        lesson_ids: lessonIds,
        target_package_id: targetPackageId,
      });
      onWorkspaceChange(workspace);
      cancel();
    } catch (error) {
      onError(error instanceof Error ? error.message : moveErrorMessage);
    } finally {
      setAction(null);
    }
  }

  async function deleteSelected() {
    const lessonIds = Array.from(effectiveSelectedLessonIds);
    if (!lessonIds.length || !window.confirm(deleteConfirmMessage(lessonIds.length))) {
      return;
    }
    setAction("delete");
    try {
      const workspace = await api.batchLessons({
        action: "delete",
        lesson_ids: lessonIds,
      });
      onWorkspaceChange(workspace);
      cancel();
    } catch (error) {
      onError(error instanceof Error ? error.message : deleteErrorMessage);
    } finally {
      setAction(null);
    }
  }

  return {
    isActive,
    selectedLessonIds: effectiveSelectedLessonIds,
    selectedCount: effectiveSelectedLessonIds.size,
    targetPackageId,
    action,
    isBusy: action !== null,
    allVisibleSelected:
      visibleLessonIds.length > 0 &&
      visibleLessonIds.every((lessonId) => effectiveSelectedLessonIds.has(lessonId)),
    start,
    cancel,
    toggleLesson,
    toggleAllVisible,
    clearSelection: () => setSelectedLessonIds(new Set()),
    setTargetPackageId,
    moveSelected,
    deleteSelected,
  };
}
