"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import {
  DEFAULT_LESSON_COMPOSER_STATE,
  buildLessonMessagesFromHistory,
  createLessonComposerState,
  type ChatMessage,
  type LessonComposerState,
  type LessonComposerStateMap,
  type LessonMessageMap,
} from "@/components/course-studio/history-utils";
import type { CoursePackage, Lesson } from "@/types";

export type CoursePackageApplyOptions = {
  blankLessonIds?: string[];
  activeLessonId?: string | null;
  rebuildMessageLessonIds?: string[];
};

export type AppliedCoursePackage = {
  coursePackage: CoursePackage;
  activeLesson: Lesson | null;
};

type AutoSavedPackageResult = {
  coursePackage: CoursePackage;
  savedLesson: Lesson | null;
};

function createMessageMap(nextPackage: CoursePackage, current: LessonMessageMap): LessonMessageMap {
  const next: LessonMessageMap = {};
  nextPackage.lessons.forEach((lesson) => {
    next[lesson.id] = current[lesson.id] ?? buildLessonMessagesFromHistory(lesson);
  });
  return next;
}

function createComposerStateMap(nextPackage: CoursePackage, current: LessonComposerStateMap): LessonComposerStateMap {
  const next: LessonComposerStateMap = {};
  nextPackage.lessons.forEach((lesson) => {
    next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
  });
  return next;
}

export function useCourseWorkspace() {
  const [coursePackage, setCoursePackage] = useState<CoursePackage | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lessonMessages, setLessonMessages] = useState<LessonMessageMap>({});
  const [lessonComposerStates, setLessonComposerStates] = useState<LessonComposerStateMap>({});

  useEffect(() => {
    async function load() {
      try {
        const payload = await api.getCoursePackage();
        setCoursePackage(payload);
        setLessonMessages((current) => createMessageMap(payload, current));
        setLessonComposerStates((current) => createComposerStateMap(payload, current));
        setError(null);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "加载失败");
      } finally {
        setIsLoading(false);
      }
    }
    void load();
  }, []);

  const lessonMap = useMemo(() => {
    const next = new Map<string, Lesson>();
    coursePackage?.lessons.forEach((lesson) => next.set(lesson.id, lesson));
    return next;
  }, [coursePackage]);

  const activeLesson = useMemo(() => {
    if (!coursePackage) {
      return null;
    }
    return coursePackage.active_lesson_id
      ? lessonMap.get(coursePackage.active_lesson_id) ?? coursePackage.lessons[0] ?? null
      : coursePackage.lessons[0] ?? null;
  }, [coursePackage, lessonMap]);

  const openLessons = useMemo(
    () =>
      (coursePackage?.workspace_tab_order
        .map((lessonId) => lessonMap.get(lessonId))
        .filter(Boolean) as Lesson[]) ?? [],
    [coursePackage?.workspace_tab_order, lessonMap]
  );

  const activeMessages = activeLesson
    ? lessonMessages[activeLesson.id] ?? buildLessonMessagesFromHistory(activeLesson)
    : [];

  const activeComposerState = activeLesson
    ? lessonComposerStates[activeLesson.id] ?? DEFAULT_LESSON_COMPOSER_STATE
    : DEFAULT_LESSON_COMPOSER_STATE;

  const syncLessonMessages = useCallback(
    (nextPackage: CoursePackage, options?: { blankLessonIds?: string[]; rebuildLessonIds?: string[] }) => {
      const blankLessonIds = new Set(options?.blankLessonIds ?? []);
      const rebuildLessonIds = new Set(options?.rebuildLessonIds ?? []);
      setLessonMessages((current) => {
        const next: LessonMessageMap = {};
        nextPackage.lessons.forEach((lesson) => {
          next[lesson.id] = blankLessonIds.has(lesson.id)
            ? []
            : rebuildLessonIds.has(lesson.id)
              ? buildLessonMessagesFromHistory(lesson)
              : current[lesson.id] ?? buildLessonMessagesFromHistory(lesson);
        });
        return next;
      });
    },
    []
  );

  const syncLessonComposerStates = useCallback((lessons: Lesson[]) => {
    setLessonComposerStates((current) => {
      const next: LessonComposerStateMap = {};
      lessons.forEach((lesson) => {
        next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
      });
      return next;
    });
  }, []);

  const updateLessonMessages = useCallback(
    (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => {
      setLessonMessages((current) => ({
        ...current,
        [lessonId]: updater(current[lessonId] ?? []),
      }));
    },
    []
  );

  const updateLessonComposerState = useCallback(
    (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => {
      setLessonComposerStates((current) => ({
        ...current,
        [lessonId]: updater(current[lessonId] ?? createLessonComposerState()),
      }));
    },
    []
  );

  const updateActiveLessonComposerState = useCallback(
    (updater: (current: LessonComposerState) => LessonComposerState) => {
      if (!activeLesson) {
        return;
      }
      updateLessonComposerState(activeLesson.id, updater);
    },
    [activeLesson, updateLessonComposerState]
  );

  const applyCoursePackage = useCallback(
    (nextPackage: CoursePackage, options?: CoursePackageApplyOptions): AppliedCoursePackage => {
      const requestedActiveLessonId = options?.activeLessonId;
      const effectiveActiveLessonId =
        requestedActiveLessonId && nextPackage.workspace_tab_order.includes(requestedActiveLessonId)
          ? requestedActiveLessonId
          : nextPackage.active_lesson_id;
      const mergedPackage =
        effectiveActiveLessonId === nextPackage.active_lesson_id
          ? nextPackage
          : { ...nextPackage, active_lesson_id: effectiveActiveLessonId };
      const nextActiveLesson =
        mergedPackage.lessons.find((lesson) => lesson.id === mergedPackage.active_lesson_id) ??
        mergedPackage.lessons[0] ??
        null;

      setCoursePackage(mergedPackage);
      syncLessonMessages(mergedPackage, {
        blankLessonIds: options?.blankLessonIds,
        rebuildLessonIds: options?.rebuildMessageLessonIds,
      });
      syncLessonComposerStates(mergedPackage.lessons);
      setError(null);

      return { coursePackage: mergedPackage, activeLesson: nextActiveLesson };
    },
    [syncLessonComposerStates, syncLessonMessages]
  );

  const applyAutoSavedCoursePackage = useCallback(
    (
      nextPackage: CoursePackage,
      lessonId: string,
      currentActiveLessonId: string | null
    ): AutoSavedPackageResult => {
      const effectiveActiveLessonId =
        currentActiveLessonId && nextPackage.workspace_tab_order.includes(currentActiveLessonId)
          ? currentActiveLessonId
          : nextPackage.active_lesson_id;
      const mergedPackage =
        effectiveActiveLessonId === nextPackage.active_lesson_id
          ? nextPackage
          : { ...nextPackage, active_lesson_id: effectiveActiveLessonId };
      const savedLesson = mergedPackage.lessons.find((lesson) => lesson.id === lessonId) ?? null;

      setCoursePackage(mergedPackage);
      syncLessonMessages(mergedPackage);
      syncLessonComposerStates(mergedPackage.lessons);
      setError(null);

      return { coursePackage: mergedPackage, savedLesson };
    },
    [syncLessonComposerStates, syncLessonMessages]
  );

  const selectLocalLesson = useCallback((lessonId: string) => {
    setCoursePackage((current) => (current ? { ...current, active_lesson_id: lessonId } : current));
  }, []);

  return {
    coursePackage,
    isLoading,
    error,
    setError,
    lessonMap,
    activeLesson,
    openLessons,
    activeMessages,
    activeComposerState,
    lessonMessages,
    lessonComposerStates,
    syncLessonMessages,
    syncLessonComposerStates,
    updateLessonMessages,
    updateLessonComposerState,
    updateActiveLessonComposerState,
    applyCoursePackage,
    applyAutoSavedCoursePackage,
    selectLocalLesson,
  };
}
