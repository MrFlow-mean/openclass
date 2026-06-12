"use client";

import type { Dispatch, SetStateAction } from "react";

import { api } from "@/lib/api";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type { CoursePackage, Lesson } from "@/types";

type UseWorkspaceActionsOptions = {
  coursePackage: CoursePackage | null;
  activeLesson: Lesson | null;
  lessonMap: Map<string, Lesson>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  updateCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => void;
  selectLocalLesson: (lessonId: string) => void;
  resetDraftToLesson: (lesson: Lesson | null) => void;
  resetTransientUi: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  onLessonCreated: () => void;
};

export function useWorkspaceActions({
  coursePackage,
  activeLesson,
  lessonMap,
  flushAutoSave,
  updateCoursePackage,
  selectLocalLesson,
  resetDraftToLesson,
  resetTransientUi,
  setError,
  setBusyAction,
  onLessonCreated,
}: UseWorkspaceActionsOptions) {
  async function saveGeneratedLesson(topic: string): Promise<boolean> {
    if (!topic.trim()) {
      return false;
    }
    setBusyAction("generate");
    try {
      const nextPackage = await api.generateLesson(topic.trim(), {
        branchFromLessonId: coursePackage?.is_standalone ? null : activeLesson?.id,
        startBlank: true,
        targetPackageId: coursePackage?.id,
      });
      updateCoursePackage(nextPackage, {
        blankLessonIds: nextPackage.active_lesson_id ? [nextPackage.active_lesson_id] : [],
      });
      return true;
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "生成 lesson 失败");
      return false;
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateLessonFromName(topic: string) {
    if (!(await flushAutoSave("create-lesson"))) {
      return false;
    }
    const isCreated = await saveGeneratedLesson(topic);
    if (isCreated) {
      onLessonCreated();
    }
    return isCreated;
  }

  async function handleOpenLesson(lessonId: string) {
    if (!(await flushAutoSave("open-lesson"))) {
      return;
    }
    setBusyAction("open-lesson");
    try {
      const nextPackage = await api.openLesson(lessonId);
      updateCoursePackage(nextPackage);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "打开课程失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCloseLesson(lessonId: string) {
    if (activeLesson?.id === lessonId && !(await flushAutoSave("close-lesson"))) {
      return;
    }
    setBusyAction("close-lesson");
    try {
      const nextPackage = await api.closeLesson(lessonId);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson && activeLesson.id !== lessonId ? activeLesson.id : undefined,
      });
    } catch (closeError) {
      setError(closeError instanceof Error ? closeError.message : "关闭课程失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSelectLesson(lessonId: string) {
    if (activeLesson?.id !== lessonId && !(await flushAutoSave("select-lesson"))) {
      return;
    }
    resetTransientUi();
    selectLocalLesson(lessonId);
    resetDraftToLesson(lessonMap.get(lessonId) ?? null);
  }

  async function handleUploadResource(file: File) {
    if (!(await flushAutoSave("upload-resource"))) {
      return false;
    }
    setBusyAction("upload-resource");
    try {
      const nextPackage = await api.uploadResource(file);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson?.id,
      });
      return true;
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传资料失败");
      return false;
    } finally {
      setBusyAction(null);
    }
  }

  return {
    handleCreateLessonFromName,
    handleOpenLesson,
    handleCloseLesson,
    handleSelectLesson,
    handleUploadResource,
  };
}
