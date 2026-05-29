"use client";

import clsx from "clsx";
import { Plus, X } from "lucide-react";

import { InlineNameForm } from "@/components/inline-name-form";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { Lesson } from "@/types";

type LessonTabsProps = {
  lessons: Lesson[];
  activeLessonId: string | null;
  isCreatingLessonInline: boolean;
  isBusyCreating: boolean;
  onSelectLesson: (lessonId: string) => void;
  onCloseLesson: (lessonId: string) => void;
  onStartCreateLesson: () => void;
  onCancelCreateLesson: () => void;
  onCreateLesson: (topic: string) => Promise<boolean>;
};

export function LessonTabs({
  lessons,
  activeLessonId,
  isCreatingLessonInline,
  isBusyCreating,
  onSelectLesson,
  onCloseLesson,
  onStartCreateLesson,
  onCancelCreateLesson,
  onCreateLesson,
}: LessonTabsProps) {
  const { texts: txt } = useInterfaceLanguage();
  const t = txt.studio.tabs;
  return (
    <nav className="flex min-w-0 items-center overflow-x-auto custom-scrollbar">
      {lessons.map((lesson) => (
        <button
          key={lesson.id}
          type="button"
          onClick={() => onSelectLesson(lesson.id)}
          className={clsx(
            "group flex h-12 items-center gap-2 border-r border-gray-100 px-4 text-left text-[10px] font-bold uppercase tracking-[0.2em] transition-colors",
            lesson.id === activeLessonId
              ? "border-b-2 border-black bg-white text-black"
              : "bg-white text-gray-400 hover:bg-gray-50 hover:text-black"
          )}
        >
          <span className="max-w-[160px] truncate">{lesson.title}</span>
          <span className="max-w-[52px] truncate text-[9px] font-medium tracking-[0.16em] text-gray-300">
            {lesson.history_graph.current_branch}
          </span>
          <span
            className="rounded-md p-1 text-gray-300 opacity-0 transition hover:bg-gray-100 hover:text-black group-hover:opacity-100"
            onClick={(event) => {
              event.stopPropagation();
              onCloseLesson(lesson.id);
            }}
          >
            <X className="h-3 w-3" />
          </span>
        </button>
      ))}
      {isCreatingLessonInline && lessons.length > 0 ? (
        <InlineNameForm
          label={t.newPageName}
          placeholder={t.newPagePlaceholder}
          variant="tab"
          isBusy={isBusyCreating}
          onCancel={onCancelCreateLesson}
          onSubmit={onCreateLesson}
        />
      ) : null}
      <button
        type="button"
        onClick={onStartCreateLesson}
        className="p-3 text-gray-300 transition-colors hover:text-black"
        title={t.newPage}
      >
        <Plus className="h-4 w-4" />
      </button>
    </nav>
  );
}
