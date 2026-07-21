"use client";

import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";

import { documentsEqual } from "@/components/course-studio/history-utils";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { AppliedCoursePackage, CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import { api } from "@/lib/api";
import type { BoardDocument, CommitRecord, CoursePackage, Lesson } from "@/types";

export type LessonPlaybackStepKind =
  | "user"
  | "source"
  | "activity"
  | "assistant"
  | "board"
  | "merge"
  | "branch"
  | "complete";

export type LessonPlaybackStep = {
  id: string;
  kind: LessonPlaybackStepKind;
  title: string;
  detail: string;
  commitId: string;
  checkpointCommitId: string;
  boardCommitId: string;
  messageCommitId: string;
};

type UseLessonPackageOptions = {
  activeLesson: Lesson | null;
  mergeActive: boolean;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  setPreviewCommitId: Dispatch<SetStateAction<string | null>>;
  setPreviewDocument: (document: BoardDocument) => void;
  resetPreview: () => void;
  createBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  applyCoursePackage: (
    nextPackage: CoursePackage,
    options?: CoursePackageApplyOptions
  ) => AppliedCoursePackage;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
};

function metadataText(commit: CommitRecord, key: string): string {
  const value = commit.metadata?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function sourceDetail(commit: CommitRecord): string {
  const selection = commit.metadata?.selection;
  if (!selection || typeof selection !== "object") {
    return "已使用冻结的课程资料证据";
  }
  const record = selection as Record<string, unknown>;
  const parts = [record.source_title, record.source_chapter_title, record.source_page_range]
    .filter((value): value is string => typeof value === "string" && Boolean(value.trim()))
    .map((value) => value.trim());
  return parts.join(" · ") || "已使用冻结的课程资料证据";
}

function firstParentLineage(lesson: Lesson): CommitRecord[] {
  const commitsById = new Map(lesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const headId = lesson.history_graph.branches[lesson.history_graph.current_branch]?.head_commit_id;
  const lineage: CommitRecord[] = [];
  const seen = new Set<string>();
  let currentId = headId ?? null;
  while (currentId && !seen.has(currentId)) {
    seen.add(currentId);
    const commit = commitsById.get(currentId);
    if (!commit) {
      break;
    }
    lineage.push(commit);
    currentId = commit.parent_ids[0] ?? null;
  }
  return lineage.reverse();
}

export function buildLessonPlaybackSteps(lesson: Lesson): LessonPlaybackStep[] {
  const commitsById = new Map(lesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const steps: LessonPlaybackStep[] = [];

  firstParentLineage(lesson).forEach((commit) => {
    const parent = commitsById.get(commit.parent_ids[0] ?? "") ?? null;
    const beforeCommit = parent ?? commit;
    const boardChanged = !parent || !documentsEqual(parent.snapshot, commit.snapshot);
    const beforeCheckpointId = boardChanged ? beforeCommit.id : commit.id;
    const userMessage = metadataText(commit, "user_message");
    const assistantMessage = metadataText(commit, "assistant_message");
    const selection = commit.metadata?.selection;
    const hasSource =
      (selection && typeof selection === "object" && (selection as Record<string, unknown>).kind === "source") ||
      commit.metadata?.verified_source_reference_used === true;

    const addStep = (
      kind: LessonPlaybackStepKind,
      title: string,
      detail: string,
      checkpointCommitId: string,
      boardCommitId: string,
      messageCommitId: string,
      suffix: string
    ) => {
      steps.push({
        id: `${commit.id}:${suffix}:${steps.length}`,
        kind,
        title,
        detail,
        commitId: commit.id,
        checkpointCommitId,
        boardCommitId,
        messageCommitId,
      });
    };

    if (userMessage) {
      addStep("user", "用户发言", userMessage, beforeCommit.id, beforeCommit.id, beforeCommit.id, "user");
    }
    if (hasSource) {
      addStep(
        "source",
        "资料引用",
        sourceDetail(commit),
        beforeCommit.id,
        beforeCommit.id,
        beforeCommit.id,
        "source"
      );
    }
    const activity = Array.isArray(commit.metadata?.agent_activity) ? commit.metadata.agent_activity : [];
    activity.forEach((item, index) => {
      if (!item || typeof item !== "object") {
        return;
      }
      const record = item as Record<string, unknown>;
      const label = typeof record.label === "string" ? record.label.trim() : "";
      if (!label) {
        return;
      }
      addStep(
        "activity",
        "AI 工作过程",
        label,
        beforeCommit.id,
        beforeCommit.id,
        beforeCommit.id,
        `activity-${index}`
      );
    });
    if (assistantMessage) {
      addStep(
        "assistant",
        "AI 回复",
        assistantMessage,
        beforeCheckpointId,
        beforeCommit.id,
        commit.id,
        "assistant"
      );
    }
    if (boardChanged) {
      addStep("board", "板书变化", commit.label, commit.id, commit.id, commit.id, "board");
    }
    if (commit.parent_ids.length > 1) {
      addStep("merge", "分支合并", commit.message, commit.id, commit.id, commit.id, "merge");
    }
    Object.entries(lesson.history_graph.branches).forEach(([branchName, branch]) => {
      if (branch.base_commit_id === commit.id && branchName !== commit.branch_name) {
        addStep("branch", "创建分支", branchName, commit.id, commit.id, commit.id, `branch-${branchName}`);
      }
    });
    addStep("complete", "回合完成", commit.message, commit.id, commit.id, commit.id, "complete");
  });

  return steps;
}

function safeFileName(value: string): string {
  const normalized = value.trim().replace(/[^\p{L}\p{N}._-]+/gu, "-").replace(/^-+|-+$/g, "");
  return normalized || "lesson";
}

function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function useLessonPackage({
  activeLesson,
  mergeActive,
  flushAutoSave,
  setPreviewCommitId,
  setPreviewDocument,
  resetPreview,
  createBranchFromCommit,
  applyCoursePackage,
  setError,
  setBusyAction,
}: UseLessonPackageOptions) {
  const steps = useMemo(() => (activeLesson ? buildLessonPlaybackSteps(activeLesson) : []), [activeLesson]);
  const [stepIndex, setStepIndex] = useState(-1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [sessionIdentity, setSessionIdentity] = useState("");
  const [speed, setSpeed] = useState(1);
  const [operation, setOperation] = useState<"export" | "import" | null>(null);
  const playbackIdentity = activeLesson
    ? `${activeLesson.id}:${activeLesson.history_graph.current_branch}:${activeLesson.history_graph.branches[activeLesson.history_graph.current_branch]?.head_commit_id ?? ""}`
    : "";
  const effectiveStepIndex = sessionIdentity === playbackIdentity ? stepIndex : -1;
  const currentStep = effectiveStepIndex >= 0 ? steps[effectiveStepIndex] ?? null : null;
  const currentCommit = currentStep
    ? activeLesson?.history_graph.commits.find((commit) => commit.id === currentStep.commitId) ?? null
    : null;
  const effectiveIsPlaying =
    sessionIdentity === playbackIdentity && isPlaying && effectiveStepIndex < steps.length - 1;

  useEffect(() => {
    if (!currentStep || !activeLesson) {
      return;
    }
    const boardCommit = activeLesson.history_graph.commits.find(
      (commit) => commit.id === currentStep.boardCommitId
    );
    if (!boardCommit) {
      return;
    }
    setPreviewCommitId(boardCommit.id);
    setPreviewDocument(boardCommit.snapshot);
  }, [activeLesson, currentStep, setPreviewCommitId, setPreviewDocument]);

  useEffect(() => {
    if (!effectiveIsPlaying || effectiveStepIndex < 0) {
      return;
    }
    const timer = window.setTimeout(() => setStepIndex((current) => current + 1), 1400 / speed);
    return () => window.clearTimeout(timer);
  }, [effectiveIsPlaying, effectiveStepIndex, speed]);

  async function startOrTogglePlayback() {
    if (mergeActive || !activeLesson || !steps.length) {
      return;
    }
    if (effectiveStepIndex >= 0) {
      if (effectiveStepIndex >= steps.length - 1) {
        setSessionIdentity(playbackIdentity);
        setStepIndex(0);
        setIsPlaying(true);
        return;
      }
      setIsPlaying((current) => !current);
      return;
    }
    if (!(await flushAutoSave("preview"))) {
      return;
    }
    setSessionIdentity(playbackIdentity);
    setStepIndex(0);
    setIsPlaying(true);
  }

  async function movePlayback(delta: number) {
    if (mergeActive || !activeLesson || !steps.length) {
      return;
    }
    if (effectiveStepIndex < 0 && !(await flushAutoSave("preview"))) {
      return;
    }
    setSessionIdentity(playbackIdentity);
    setIsPlaying(false);
    setStepIndex(Math.max(0, Math.min(steps.length - 1, (effectiveStepIndex < 0 ? 0 : effectiveStepIndex) + delta)));
  }

  function exitPlayback() {
    setIsPlaying(false);
    setStepIndex(-1);
    setSessionIdentity("");
    resetPreview();
  }

  async function forkFromCurrentStep() {
    if (mergeActive || !activeLesson || !currentStep) {
      return;
    }
    const checkpoint = activeLesson.history_graph.commits.find(
      (commit) => commit.id === currentStep.checkpointCommitId
    );
    if (!checkpoint) {
      setError("当前播放步骤没有可用的历史检查点。");
      return;
    }
    setIsPlaying(false);
    await createBranchFromCommit(checkpoint);
  }

  async function exportRidoc() {
    if (mergeActive || !activeLesson) {
      setError("合并草案期间不能导出课程包，请先提交或放弃合并。");
      return;
    }
    if (!(await flushAutoSave("export"))) {
      return;
    }
    setOperation("export");
    setBusyAction("ridoc-export");
    try {
      const blob = await api.exportRidoc(activeLesson.id);
      downloadBlob(blob, `${safeFileName(activeLesson.slug || activeLesson.title)}.ridoc`);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "课程包导出失败");
    } finally {
      setOperation(null);
      setBusyAction(null);
    }
  }

  async function importRidoc(file: File) {
    if (mergeActive) {
      setError("合并草案期间不能导入课程包，请先提交或放弃合并。");
      return;
    }
    if (!(await flushAutoSave("import"))) {
      return;
    }
    setOperation("import");
    setBusyAction("ridoc-import");
    try {
      const nextPackage = await api.importRidoc(file);
      const lessonId = nextPackage.active_lesson_id ?? nextPackage.lessons[0]?.id ?? null;
      applyCoursePackage(nextPackage, {
        activeLessonId: lessonId,
        rebuildMessageLessonIds: nextPackage.lessons.map((lesson) => lesson.id),
      });
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "课程包导入失败");
    } finally {
      setOperation(null);
      setBusyAction(null);
    }
  }

  return {
    steps,
    currentStep,
    currentCommit,
    stepIndex: effectiveStepIndex,
    isPlaying: effectiveIsPlaying,
    isPlaybackActive: effectiveStepIndex >= 0,
    playbackMessageCommitId: currentStep?.messageCommitId ?? null,
    speed,
    operation,
    setSpeed,
    startOrTogglePlayback,
    movePlayback,
    exitPlayback,
    forkFromCurrentStep,
    exportRidoc,
    importRidoc,
  };
}
