"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  closestCenter,
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  horizontalListSortingStrategy,
  SortableContext,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import clsx from "clsx";
import {
  AlignCenter,
  AlignLeft,
  Baseline,
  Bold,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  Cpu,
  FileText,
  GitBranch,
  GripHorizontal,
  Highlighter,
  Italic,
  Languages,
  List,
  MessageSquare,
  PanelRight,
  Plus,
  Radio,
  Save,
  Send,
  Sparkles,
  Target,
  Underline,
  Volume2,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  BoardDecision,
  BoardBlock,
  BlockStyle,
  BlockType,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  DiffPreviewItem,
  Lesson,
  PatchOperation,
  PatchProposal,
  ResourceMatch,
  ScopeOption,
  SelectionRef,
} from "@/types";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type SidebarTab = "history" | "branch" | "library";
type DragTarget = "left" | "right" | null;
type SelectionMode = "block" | "text";

const blockTypeOptions: Array<{ value: BlockType; label: string }> = [
  { value: "paragraph", label: "段落" },
  { value: "heading", label: "标题" },
  { value: "note", label: "注释" },
  { value: "formula", label: "公式" },
  { value: "exercise", label: "练习" },
  { value: "dialogue", label: "对话" },
  { value: "table", label: "表格" },
];

function createBlock(type: BlockType): BoardBlock {
  return {
    id: `block_${crypto.randomUUID().slice(0, 8)}`,
    type,
    title: `新${blockTypeOptions.find((option) => option.value === type)?.label ?? "块"}`,
    content: "在这里继续扩写板书内容。",
    style: {
      font_family: "sans",
      font_size: type === "heading" ? "xl" : "md",
      alignment: type === "formula" ? "center" : "left",
      emphasis: type === "note" ? "callout" : "plain",
      width: "normal",
    },
    metadata: {},
  };
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function rowsForBlock(block: BoardBlock) {
  const newlineCount = block.content.split("\n").length;
  const lengthBasedRows = Math.ceil(block.content.length / 40);
  if (block.type === "heading") {
    return Math.max(2, Math.min(4, newlineCount));
  }
  if (block.type === "formula") {
    return Math.max(2, Math.min(3, newlineCount));
  }
  return Math.max(3, Math.min(10, Math.max(newlineCount, lengthBasedRows)));
}

function HeaderLessonTab({
  lesson,
  active,
  onSelect,
  onClose,
}: {
  lesson: Lesson;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({
    id: lesson.id,
  });

  return (
    <button
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={clsx(
        "group flex h-12 items-center gap-2 border-r border-gray-100 px-4 text-left text-[10px] font-bold uppercase tracking-[0.2em] transition-colors",
        active
          ? "border-b-2 border-black bg-white text-black"
          : "bg-white text-gray-400 hover:bg-gray-50 hover:text-black"
      )}
      onClick={onSelect}
      type="button"
      {...attributes}
      {...listeners}
    >
      <GripHorizontal className="h-3 w-3 shrink-0 text-gray-300 opacity-0 transition group-hover:opacity-100" />
      <span className="max-w-[160px] truncate">{lesson.title}</span>
      <span className="max-w-[52px] truncate text-[9px] font-medium tracking-[0.16em] text-gray-300">
        {lesson.history_graph.current_branch}
      </span>
      <span
        className="rounded-md p-1 text-gray-300 opacity-0 transition hover:bg-gray-100 hover:text-black group-hover:opacity-100"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        <X className="h-3 w-3" />
      </span>
    </button>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const isAssistant = message.role === "assistant";

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {isAssistant ? <Sparkles className="h-3.5 w-3.5 text-gray-800" /> : <MessageSquare className="h-3.5 w-3.5 text-gray-800" />}
        <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
          {isAssistant ? "AI 讲师" : "用户"}
        </span>
      </div>
      <div
        className={clsx(
          "max-w-[92%] rounded-2xl p-4 text-[13px] leading-relaxed shadow-sm",
          isAssistant
            ? "rounded-tl-sm border border-gray-100 bg-gray-50 text-gray-800"
            : "ml-auto rounded-tr-sm bg-[#1a1a1a] text-white"
        )}
      >
        {message.content}
      </div>
    </div>
  );
}

function DiffPreviewCard({ item }: { item: DiffPreviewItem }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-600">{item.op}</p>
      <p className="mt-2 text-sm font-semibold text-gray-900">{item.summary}</p>
      <div className="mt-3 grid gap-3">
        {item.before ? (
          <div className="rounded-xl bg-gray-50 p-3 text-xs text-gray-500">
            <p className="font-semibold text-gray-800">Before</p>
            <p className="mt-1 font-medium text-gray-700">{item.before.title}</p>
            <p className="mt-1 whitespace-pre-wrap leading-6">{item.before.content}</p>
          </div>
        ) : null}
        {item.after ? (
          <div className="rounded-xl bg-emerald-50 p-3 text-xs text-gray-600">
            <p className="font-semibold text-emerald-700">After</p>
            <p className="mt-1 font-medium text-gray-900">{item.after.title}</p>
            <p className="mt-1 whitespace-pre-wrap leading-6">{item.after.content}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CommitTimelineItem({
  commit,
  active,
  latest,
  onPreview,
  onRestore,
}: {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
  onPreview: () => void;
  onRestore: () => void;
}) {
  return (
    <div className="relative flex gap-4 pl-3">
      <div className={clsx("absolute left-0 top-1.5 h-full w-px", latest ? "bg-black" : "bg-gray-200")} />
      <div
        className={clsx(
          "absolute -left-[4px] top-1.5 h-2 w-2 rounded-full",
          latest ? "bg-black" : active ? "bg-gray-500" : "bg-gray-300"
        )}
      />
      <div className={clsx("flex-1 pb-4", active && "opacity-100")}>
        <p className={clsx("text-xs font-bold", latest ? "text-black" : "text-gray-800")}>{commit.label}</p>
        <p className="mt-1 text-[11px] text-gray-500">{commit.message}</p>
        <p className="mt-1 text-[11px] text-gray-400">{formatDate(commit.created_at)}</p>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={onPreview}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Preview
          </button>
          <button
            type="button"
            onClick={onRestore}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Restore
          </button>
        </div>
      </div>
    </div>
  );
}

function DocumentBlock({
  block,
  selected,
  selectionMode,
  disabled,
  onSelect,
  onSelectTextQuote,
  onTitleChange,
  onContentChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  onStyleChange,
  onTextSelect,
}: {
  block: BoardBlock;
  selected: boolean;
  selectionMode: SelectionMode | null;
  disabled: boolean;
  onSelect: () => void;
  onSelectTextQuote: () => void;
  onTitleChange: (value: string) => void;
  onContentChange: (value: string) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDelete: () => void;
  onStyleChange: (style: Partial<BlockStyle>) => void;
  onTextSelect: (excerpt: string) => void;
}) {
  const framed = ["note", "exercise", "formula", "table", "image"].includes(block.type);
  const isPartialTextSelected = selected && selectionMode === "text";
  const contentTone =
    block.type === "exercise"
      ? "border-gray-200 bg-[#fdfdfd]"
      : block.type === "note"
        ? "border-blue-100/80 bg-[#f7faff]"
        : block.type === "formula"
          ? "border-gray-200 bg-white"
          : "border-gray-200 bg-white";

  return (
    <article className={clsx("group relative", !framed && "content-block -mx-4 rounded-lg px-4 py-4")}>
      <div
        className={clsx(
          "transition-colors",
          framed ? `rounded-2xl border p-6 shadow-sm ${contentTone}` : "",
          selected && "ring-1 ring-black/80"
        )}
      >
        <div className="absolute right-3 top-3 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
          {isPartialTextSelected ? (
            <button
              type="button"
              onClick={onSelectTextQuote}
              className="rounded-md border border-black bg-black px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-white hover:bg-gray-900"
            >
              局部引用
            </button>
          ) : null}
          <button
            type="button"
            onClick={onSelect}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:text-black"
          >
            整块引用
          </button>
          <button
            type="button"
            onClick={onMoveUp}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:text-black"
          >
            上移
          </button>
          <button
            type="button"
            onClick={onMoveDown}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:text-black"
          >
            下移
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md border border-rose-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-rose-500 hover:bg-rose-50"
          >
            删除
          </button>
        </div>

        <div className="mb-3 flex items-center gap-2">
          <span className="rounded-full border border-gray-200 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.2em] text-gray-400">
            {block.type}
          </span>
          {selected ? (
            <span className="rounded-full bg-black px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.2em] text-white">
              {isPartialTextSelected ? "局部文字" : "当前选区"}
            </span>
          ) : null}
        </div>

        <input
          disabled={disabled}
          value={block.title}
          onChange={(event) => onTitleChange(event.target.value)}
          onSelect={(event) => {
            const target = event.currentTarget;
            const start = target.selectionStart ?? 0;
            const end = target.selectionEnd ?? 0;
            const excerpt = target.value.slice(start, end).trim();
            if (excerpt) {
              onTextSelect(excerpt);
            }
          }}
          className={clsx(
            "w-full border-0 bg-transparent p-0 outline-none placeholder:text-gray-300",
            block.type === "heading"
              ? "text-[2rem] font-bold leading-tight tracking-tight text-gray-900"
              : "text-lg font-bold text-gray-900"
          )}
        />

        <textarea
          disabled={disabled}
          value={block.content}
          rows={rowsForBlock(block)}
          onChange={(event) => onContentChange(event.target.value)}
          onSelect={(event) => {
            const target = event.currentTarget;
            const start = target.selectionStart ?? 0;
            const end = target.selectionEnd ?? 0;
            const excerpt = target.value.slice(start, end).trim();
            if (excerpt) {
              onTextSelect(excerpt);
            }
          }}
          className={clsx(
            "mt-4 w-full resize-none border-0 bg-transparent p-0 outline-none placeholder:text-gray-300",
            block.type === "formula"
              ? "font-mono text-xl leading-relaxed text-gray-900"
              : "text-[15px] leading-loose text-gray-700",
            block.style.alignment === "center" && "text-center",
            block.style.alignment === "right" && "text-right",
            block.style.font_size === "sm" && "text-sm",
            block.style.font_size === "lg" && "text-base",
            block.style.font_size === "xl" && "text-lg",
            block.style.emphasis === "callout" && "text-gray-800",
            block.style.font_family === "mono" && "font-mono"
          )}
        />

        {selected && !disabled ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
            <button
              type="button"
              onClick={() => onStyleChange({ font_size: "sm" })}
              className="rounded-md border border-gray-200 px-2 py-1 hover:border-gray-300 hover:text-black"
            >
              小号
            </button>
            <button
              type="button"
              onClick={() => onStyleChange({ font_size: "lg" })}
              className="rounded-md border border-gray-200 px-2 py-1 hover:border-gray-300 hover:text-black"
            >
              大号
            </button>
            <button
              type="button"
              onClick={() => onStyleChange({ emphasis: "callout" })}
              className="rounded-md border border-gray-200 px-2 py-1 hover:border-gray-300 hover:text-black"
            >
              高亮
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export function CourseStudio() {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const mainContainerRef = useRef<HTMLDivElement | null>(null);

  const [coursePackage, setCoursePackage] = useState<CoursePackage | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [newBranchName, setNewBranchName] = useState("");
  const [selection, setSelection] = useState<SelectionRef | null>(null);
  const [selectionMode, setSelectionMode] = useState<SelectionMode | null>(null);
  const [pendingManualOps, setPendingManualOps] = useState<PatchOperation[]>([]);
  const [pendingProposal, setPendingProposal] = useState<PatchProposal | null>(null);
  const [scopeOptions, setScopeOptions] = useState<ScopeOption[]>([]);
  const [resourceMatches, setResourceMatches] = useState<ResourceMatch[]>([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [previewCommitId, setPreviewCommitId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: crypto.randomUUID(),
      role: "assistant",
      content:
        "你好！你可以从学习目标出发提问，我会围绕当前板书解释、扩写、生成练习，并把所有变更记录进版本历史。",
    },
    {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "默认工作流会优先局部修改现有板书；如果问题超出范围，我会先建议你新增章节或新开一节课。",
    },
  ]);
  const [topCollapsed, setTopCollapsed] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("history");
  const [voiceActive, setVoiceActive] = useState(false);
  const [leftWidth, setLeftWidth] = useState(30);
  const [rightWidth, setRightWidth] = useState(22);
  const [dragTarget, setDragTarget] = useState<DragTarget>(null);

  useEffect(() => {
    async function load() {
      try {
        const payload = await api.getCoursePackage();
        setCoursePackage(payload);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "加载失败");
      } finally {
        setIsLoading(false);
      }
    }

    void load();
  }, []);

  useEffect(() => {
    if (!dragTarget) {
      return;
    }

    function handleMouseMove(event: MouseEvent) {
      const rect = mainContainerRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }

      if (dragTarget === "left") {
        const next = ((event.clientX - rect.left) / rect.width) * 100;
        setLeftWidth(Math.max(20, Math.min(next, 50)));
        return;
      }

      if (dragTarget === "right") {
        const next = ((rect.right - event.clientX) / rect.width) * 100;
        setRightWidth(Math.max(15, Math.min(next, 40)));
      }
    }

    function handleMouseUp() {
      setDragTarget(null);
    }

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.classList.add("select-none");

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.classList.remove("select-none");
    };
  }, [dragTarget]);

  const lessonMap = new Map<string, Lesson>();
  coursePackage?.lessons.forEach((lesson) => lessonMap.set(lesson.id, lesson));

  const activeLesson = coursePackage?.active_lesson_id
    ? lessonMap.get(coursePackage.active_lesson_id) ?? coursePackage.lessons[0] ?? null
    : coursePackage?.lessons[0] ?? null;

  const previewCommit =
    previewCommitId && activeLesson
      ? activeLesson.history_graph.commits.find((commit) => commit.id === previewCommitId) ?? null
      : null;

  const displayedDocument = previewCommit?.snapshot ?? activeLesson?.board_document ?? null;
  const openLessons = (coursePackage?.workspace_tab_order
    .map((lessonId) => lessonMap.get(lessonId))
    .filter(Boolean) as Lesson[]) ?? [];
  const activeRequirements = activeLesson?.learning_requirements ?? null;
  const isPreviewMode = Boolean(previewCommit);
  const selectedBoardBlock =
    selection?.kind === "board" && activeLesson
      ? activeLesson.board_document.blocks.find((block) => block.id === selection.block_id) ?? null
      : null;
  const latestAssistantMessage = [...messages].reverse().find((message) => message.role === "assistant");
  const relatedEdges =
    activeLesson && coursePackage
      ? coursePackage.course_graph.filter(
          (edge) =>
            edge.source_lesson_id === activeLesson.id || edge.target_lesson_id === activeLesson.id
        )
      : [];
  const selectionKindLabel =
    selection?.kind === "board"
      ? selectionMode === "block"
        ? "板书整块引用"
        : "板书局部文字"
      : selection?.kind === "chat"
        ? "聊天局部文字"
        : "";
  const selectionCharCount = selection?.excerpt.length ?? 0;
  const selectionUsageHint =
    selectionMode === "block"
      ? "当前会把整块板书一起带给 AI，适合整体改写、扩展或迁移结构。"
      : "当前会把这段局部文字精确带给 AI，适合针对几句话做解释、改写或补充。";

  const learningGoalItems = [
    activeRequirements?.learning_goal ?? activeLesson?.summary ?? "围绕当前课程主线推进学习",
    activeRequirements?.success_criteria ?? "先建立概念，再进入例题与练习",
  ];

  function applySelection(nextSelection: SelectionRef, mode: SelectionMode) {
    setSelection(nextSelection);
    setSelectionMode(mode);
  }

  function clearSelection() {
    setSelection(null);
    setSelectionMode(null);
  }

  function resetTransientUi() {
    setPreviewCommitId(null);
    setPendingManualOps([]);
    setPendingProposal(null);
    setScopeOptions([]);
    setResourceMatches([]);
    setClarificationQuestions([]);
    setLatestBoardDecision(null);
    setLastScopedRequest(null);
    clearSelection();
  }

  function updateCoursePackage(nextPackage: CoursePackage) {
    setCoursePackage(nextPackage);
    resetTransientUi();
    setError(null);
  }

  function updateActiveLesson(mutator: (lesson: Lesson) => void) {
    setCoursePackage((current) => {
      if (!current || !activeLesson) {
        return current;
      }
      const next = structuredClone(current) as CoursePackage;
      const lesson = next.lessons.find((candidate) => candidate.id === activeLesson.id);
      if (!lesson) {
        return current;
      }
      mutator(lesson);
      return next;
    });
  }

  function queueOperation(operation: PatchOperation) {
    setPendingManualOps((current) => {
      if (operation.op === "update_block_content" || operation.op === "update_block_style") {
        const next = [...current];
        const index = next.findIndex(
          (candidate) => candidate.op === operation.op && candidate.block_id === operation.block_id
        );
        if (index >= 0) {
          next[index] = operation;
          return next;
        }
        return [...current, operation];
      }
      return [...current, operation];
    });
  }

  function handleBlockContentChange(blockId: string, field: "title" | "content", value: string) {
    if (!activeLesson || isPreviewMode) {
      return;
    }
    const block = activeLesson.board_document.blocks.find((candidate) => candidate.id === blockId);
    if (!block) {
      return;
    }

    const title = field === "title" ? value : block.title;
    const content = field === "content" ? value : block.content;

    updateActiveLesson((lesson) => {
      const target = lesson.board_document.blocks.find((candidate) => candidate.id === blockId);
      if (!target) {
        return;
      }
      target.title = title;
      target.content = content;
    });

    queueOperation({
      op: "update_block_content",
      block_id: blockId,
      title,
      content,
    });
  }

  function handleStyleChange(blockId: string, partialStyle: Partial<BlockStyle>) {
    if (!activeLesson || isPreviewMode) {
      return;
    }
    const block = activeLesson.board_document.blocks.find((candidate) => candidate.id === blockId);
    if (!block) {
      return;
    }

    const nextStyle = { ...block.style, ...partialStyle };
    updateActiveLesson((lesson) => {
      const target = lesson.board_document.blocks.find((candidate) => candidate.id === blockId);
      if (!target) {
        return;
      }
      target.style = nextStyle;
    });

    queueOperation({
      op: "update_block_style",
      block_id: blockId,
      style: nextStyle,
    });
  }

  function handleAddBlock(type: BlockType) {
    if (!activeLesson || !displayedDocument || isPreviewMode) {
      return;
    }
    const block = createBlock(type);
    const afterBlockId =
      selection?.block_id ?? displayedDocument.blocks[displayedDocument.blocks.length - 1]?.id ?? null;

    updateActiveLesson((lesson) => {
      const targetIndex = afterBlockId
        ? lesson.board_document.blocks.findIndex((candidate) => candidate.id === afterBlockId)
        : lesson.board_document.blocks.length - 1;
      lesson.board_document.blocks.splice(targetIndex + 1, 0, block);
    });

    queueOperation({
      op: "insert_block",
      after_block_id: afterBlockId,
      block,
    });

    applySelection(
      {
        kind: "board",
        lesson_id: activeLesson.id,
        block_id: block.id,
        excerpt: `${block.title}\n${block.content}`,
      },
      "block"
    );
  }

  function handleDeleteBlock(blockId: string) {
    if (!activeLesson || isPreviewMode) {
      return;
    }

    updateActiveLesson((lesson) => {
      lesson.board_document.blocks = lesson.board_document.blocks.filter((block) => block.id !== blockId);
    });
    setPendingManualOps((current) => [
      ...current.filter((candidate) => candidate.block_id !== blockId),
      { op: "delete_block", block_id: blockId },
    ]);
  }

  function handleMoveBlock(blockId: string, direction: "up" | "down") {
    if (!activeLesson || isPreviewMode) {
      return;
    }
    const blocks = activeLesson.board_document.blocks;
    const currentIndex = blocks.findIndex((block) => block.id === blockId);
    if (currentIndex < 0) {
      return;
    }
    const nextIndex = direction === "up" ? currentIndex - 1 : currentIndex + 1;
    if (nextIndex < 0 || nextIndex >= blocks.length) {
      return;
    }
    const afterBlockId =
      direction === "up"
        ? nextIndex - 1 >= 0
          ? blocks[nextIndex - 1].id
          : null
        : blocks[nextIndex].id;

    updateActiveLesson((lesson) => {
      const workingBlocks = lesson.board_document.blocks;
      const sourceIndex = workingBlocks.findIndex((block) => block.id === blockId);
      if (sourceIndex < 0) {
        return;
      }
      const [block] = workingBlocks.splice(sourceIndex, 1);
      const destinationIndex =
        afterBlockId === null
          ? 0
          : workingBlocks.findIndex((candidate) => candidate.id === afterBlockId) + 1;
      workingBlocks.splice(destinationIndex, 0, block);
    });

    queueOperation({
      op: "move_block",
      block_id: blockId,
      after_block_id: afterBlockId,
    });
  }

  async function saveGeneratedLesson(topic: string) {
    if (!topic.trim() || !activeLesson) {
      return;
    }
    setBusyAction("generate");
    try {
      const nextPackage = await api.generateLesson(topic.trim(), activeLesson.id);
      updateCoursePackage(nextPackage);
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "生成 lesson 失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateLessonFromPrompt() {
    const topic = window.prompt("请输入新单课主题，例如：交换代数中的理想");
    if (!topic) {
      return;
    }
    await saveGeneratedLesson(topic);
  }

  async function handleSaveManualEdits() {
    if (!activeLesson || pendingManualOps.length === 0) {
      return;
    }
    setBusyAction("save");
    try {
      const nextPackage = await api.manualCommit(
        activeLesson.id,
        pendingManualOps,
        "Manual board edit",
        "Saved editor changes as a new commit"
      );
      updateCoursePackage(nextPackage);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload) {
    if (!activeLesson) {
      return;
    }

    const payload =
      payloadOverride ??
      ({
        message: chatInput.trim(),
        selection,
      } satisfies ChatRequestPayload);
    const payloadWithConversation: ChatRequestPayload = {
      ...payload,
      conversation: messages.slice(-8).map(({ role, content }) => ({ role, content })),
    };

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    setBusyAction("chat");
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: "user",
        content:
          payloadOverride && payloadOverride.scope_action
            ? `继续执行：${payloadOverride.scope_action}`
            : payloadWithConversation.message,
      },
    ]);

    try {
      const response = await api.chatOnLesson(activeLesson.id, payloadWithConversation);
      updateCoursePackage(response.course_package);
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.teacher_message,
        },
      ]);
      setLatestBoardDecision(response.board_decision);
      setClarificationQuestions(response.clarification_questions);
      setPendingProposal(response.patch_proposal ?? null);
      setScopeOptions(response.scope_options);
      setResourceMatches(response.resource_matches);
      setLastScopedRequest(response.scope_options.length ? payloadWithConversation : null);
      if (!payloadOverride) {
        setChatInput("");
      }
      const createdLesson = response.created_lesson;
      if (createdLesson) {
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: `我已经为这个更大的知识问题新开了一节课：《${createdLesson.title}》。`,
          },
        ]);
      }
      if (!payloadWithConversation.scope_action) {
        clearSelection();
      }
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : "聊天失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleScopeAction(option: ScopeOption) {
    if (!activeLesson || !lastScopedRequest) {
      return;
    }
    await handleSubmitChat({
      message: lastScopedRequest.message,
      selection: lastScopedRequest.selection,
      scope_action: option.action,
      resource_chapter_id: option.resource_chapter_id ?? undefined,
    });
    setScopeOptions([]);
    setLastScopedRequest(null);
  }

  async function handleApplyProposal() {
    if (!activeLesson || !pendingProposal) {
      return;
    }
    setBusyAction("apply-proposal");
    try {
      const nextPackage = await api.applyProposal(
        activeLesson.id,
        pendingProposal.operations,
        pendingProposal.commit_label,
        pendingProposal.rationale
      );
      updateCoursePackage(nextPackage);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "应用 AI 补丁失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateBranch() {
    if (!activeLesson || !newBranchName.trim()) {
      return;
    }
    setBusyAction("branch");
    try {
      const nextPackage = await api.createBranch(activeLesson.id, newBranchName.trim(), previewCommitId);
      updateCoursePackage(nextPackage);
      setNewBranchName("");
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "创建分支失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSwitchBranch(branchName: string) {
    if (!activeLesson) {
      return;
    }
    setBusyAction("switch-branch");
    try {
      const nextPackage = await api.switchBranch(activeLesson.id, branchName);
      updateCoursePackage(nextPackage);
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "切换分支失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRestoreCommit(commitId: string) {
    if (!activeLesson) {
      return;
    }
    setBusyAction("restore");
    try {
      const nextPackage = await api.restoreCommit(activeLesson.id, commitId);
      updateCoursePackage(nextPackage);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "恢复版本失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleOpenLesson(lessonId: string) {
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
    setBusyAction("close-lesson");
    try {
      const nextPackage = await api.closeLesson(lessonId);
      updateCoursePackage(nextPackage);
    } catch (closeError) {
      setError(closeError instanceof Error ? closeError.message : "关闭课程失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleTabsDragEnd(event: DragEndEvent) {
    if (!coursePackage || !event.over || event.active.id === event.over.id) {
      return;
    }
    const currentOrder = [...coursePackage.workspace_tab_order];
    const oldIndex = currentOrder.indexOf(String(event.active.id));
    const newIndex = currentOrder.indexOf(String(event.over.id));
    const reordered = arrayMove(currentOrder, oldIndex, newIndex);

    try {
      const nextPackage = await api.reorderWorkspace(reordered, activeLesson?.id);
      updateCoursePackage(nextPackage);
    } catch (reorderError) {
      setError(reorderError instanceof Error ? reorderError.message : "调整标签顺序失败");
    }
  }

  async function handleUploadResource(file: File | null) {
    if (!file) {
      return;
    }
    setBusyAction("upload");
    try {
      const nextPackage = await api.uploadResource(file);
      updateCoursePackage(nextPackage);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传资料失败");
    } finally {
      setBusyAction(null);
    }
  }

  function handleVoiceToggle() {
    if (typeof window === "undefined") {
      return;
    }
    if (voiceActive) {
      window.speechSynthesis.cancel();
      setVoiceActive(false);
      setBusyAction(null);
      return;
    }

    setVoiceActive(true);
    if (!latestAssistantMessage) {
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(latestAssistantMessage.content);
    utterance.lang = "zh-CN";
    utterance.onstart = () => setBusyAction("speak");
    utterance.onend = () => {
      setBusyAction(null);
      setVoiceActive(false);
    };
    utterance.onerror = () => {
      setBusyAction(null);
      setVoiceActive(false);
    };
    window.speechSynthesis.speak(utterance);
  }

  function handleSelectLesson(lessonId: string) {
    resetTransientUi();
    setCoursePackage((current) =>
      current ? { ...current, active_lesson_id: lessonId } : current
    );
  }

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">正在载入课程工作台…</div>;
  }

  if (!coursePackage || !activeLesson || !displayedDocument) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">没有找到可用课程。</div>;
  }

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-[#f9fafb] text-[#1a1a1a]">
      <CreateLessonDialog
        open={isCreateLessonDialogOpen}
        form={createLessonForm}
        activeLessonTitle={activeLesson.title}
        busy={busyAction === "create-lesson"}
        onClose={closeCreateLessonDialog}
        onChange={updateCreateLessonForm}
        onSubmit={() => void handleCreateLesson()}
      />

      <div
        className={clsx(
          "relative z-[60] flex shrink-0 flex-col bg-white transition-all duration-300",
          topCollapsed && "-translate-y-full -mb-12"
        )}
      >
        <header className="flex h-12 items-center justify-between border-b border-gray-200 px-4">
          <div className="flex min-w-0 items-center gap-6">
            <div className="flex shrink-0 items-center gap-2">
              <div className="flex h-6 w-6 items-center justify-center rounded bg-black text-white shadow-sm">
                <Cpu className="h-3.5 w-3.5" />
              </div>
              <span className="text-[13px] font-semibold tracking-tight">{coursePackage.title}</span>
            </div>

            <nav className="flex min-w-0 items-center overflow-hidden">
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleTabsDragEnd}>
                <SortableContext
                  items={openLessons.map((lesson) => lesson.id)}
                  strategy={horizontalListSortingStrategy}
                >
                  <div className="flex min-w-0 items-center overflow-x-auto custom-scrollbar">
                    {openLessons.map((lesson) => (
                      <HeaderLessonTab
                        key={lesson.id}
                        lesson={lesson}
                        active={lesson.id === activeLesson.id}
                        onSelect={() => handleSelectLesson(lesson.id)}
                        onClose={() => void handleCloseLesson(lesson.id)}
                      />
                    ))}
                  </div>
                </SortableContext>
              </DndContext>

              <button
                type="button"
                onClick={() => setIsCreateLessonDialogOpen(true)}
                className="flex items-center gap-2 px-3 text-gray-400 transition-colors hover:text-black"
                title="新建课程"
              >
                <Plus className="h-4 w-4" />
                <span className="text-[10px] font-bold uppercase tracking-[0.2em]">新建课程</span>
              </button>
            </nav>
          </div>

          <div className="flex shrink-0 items-center gap-4">
            {unsavedChanges ? (
              <div className="flex items-center gap-2 rounded-md border border-amber-100 bg-amber-50 px-2.5 py-1">
                <div className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-amber-700">
                  {unsavedChanges} Changes
                </span>
              </div>
            ) : null}
            <div className="flex items-center gap-2 rounded-md border border-green-100/50 bg-green-50/60 px-2 py-1">
              <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
              <span className="text-[10px] font-bold uppercase tracking-widest text-green-700">
                Teacher AI Online
              </span>
            </div>
            <div className="ml-2 flex items-center gap-1 border-l border-gray-200 pl-4">
              <button
                type="button"
                onClick={() => setRightSidebarOpen((current) => !current)}
                className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100"
                title="展开/隐藏右侧栏"
              >
                <PanelRight className="h-4.5 w-4.5" />
              </button>
              <button
                type="button"
                onClick={() => setTopCollapsed(true)}
                className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100"
                title="收起工具栏"
              >
                <ChevronUp className="h-4.5 w-4.5" />
              </button>
            </div>
            <Image
              src="https://api.dicebear.com/7.x/avataaars/svg?seed=Codex"
              alt="User Avatar"
              className="h-7 w-7 rounded-full border border-gray-200"
              width={28}
              height={28}
              unoptimized
            />
          </div>
        </header>
      </div>

      <button
        type="button"
        onClick={() => setTopCollapsed(false)}
        className={clsx(
          "fixed left-1/2 top-0 z-[70] flex h-4 w-16 -translate-x-1/2 items-center justify-center rounded-b-lg border border-t-0 border-gray-200 bg-white shadow-sm transition-all hover:h-5 hover:bg-gray-50",
          topCollapsed ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
        title="展开顶部工具栏"
      >
        <ChevronDown className="h-3 w-3 text-gray-400" />
      </button>

      {error ? (
        <div className="mx-4 mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 md:mx-6">
          {error}
        </div>
      ) : null}

      <div ref={mainContainerRef} className="relative flex min-h-0 flex-1 overflow-hidden">
        <aside
          className="z-20 flex h-full shrink-0 flex-col border-r border-gray-200 bg-white"
          style={{ width: `${leftWidth}%` }}
        >
          <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
            <div className="space-y-6">
              <div className="rounded-xl border border-blue-100/50 bg-[#f4f6ff] p-4">
                <div className="mb-3 flex items-center gap-2">
                  <Target className="h-3.5 w-3.5 text-blue-600" />
                  <h3 className="text-[11px] font-bold uppercase tracking-widest text-blue-900">
                    当前学习目标
                  </h3>
                </div>
                <ul className="space-y-2">
                  {learningGoalItems.map((item, index) => (
                    <li key={item} className="flex items-start gap-2.5 text-xs leading-relaxed text-blue-800">
                      {index === 0 ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                      ) : (
                        <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-300" />
                      )}
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-[11px] font-bold uppercase tracking-[0.24em] text-gray-400">课程目录</p>
                    <p className="mt-1 text-xs leading-6 text-gray-500">快速切换、打开或继续完善其它课件。</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsCreateLessonDialogOpen(true)}
                    className="rounded-lg border border-gray-200 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-500 transition hover:border-gray-300 hover:text-black"
                  >
                    新建
                  </button>
                </div>
                <div className="space-y-2">
                  {coursePackage.lessons.map((lesson) => {
                    const opened = coursePackage.open_lesson_ids.includes(lesson.id);
                    const active = lesson.id === activeLesson.id;
                    return (
                      <button
                        key={lesson.id}
                        type="button"
                        onClick={() => {
                          if (opened) {
                            handleSelectLesson(lesson.id);
                            return;
                          }
                          void handleOpenLesson(lesson.id);
                        }}
                        className={clsx(
                          "w-full rounded-2xl border px-4 py-3 text-left transition",
                          active
                            ? "border-black bg-black text-white"
                            : "border-gray-200 bg-white text-gray-700 hover:border-gray-300"
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-semibold">{lesson.title}</p>
                            <p className={clsx("mt-1 truncate text-xs", active ? "text-white/70" : "text-gray-500")}>
                              {lesson.board_document.blocks[0]?.content ?? lesson.summary}
                            </p>
                          </div>
                          <span
                            className={clsx(
                              "shrink-0 rounded-full px-2 py-1 text-[9px] font-bold uppercase tracking-[0.18em]",
                              active
                                ? "bg-white/15 text-white"
                                : opened
                                  ? "bg-gray-100 text-gray-500"
                                  : "bg-sky-50 text-sky-700"
                            )}
                          >
                            {active ? "当前" : opened ? "已打开" : "未打开"}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div
                className="space-y-6"
              >
                {messages.map((message) => (
                  <div
                    key={message.id}
                    onMouseUp={() => {
                      const excerpt = window.getSelection()?.toString().trim();
                      if (excerpt) {
                        applySelection(
                          {
                            kind: "chat",
                            lesson_id: activeLesson.id,
                            excerpt,
                          },
                          "text"
                        );
                      }
                    }}
                  >
                    <ChatBubble message={message} />
                  </div>
                ))}
              </div>

              {latestBoardDecision ? (
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-violet-700">板书策略</p>
                  <p className="mt-2 text-xs leading-6 text-violet-900">
                    {latestBoardDecision.reason}
                  </p>
                </div>
              ) : null}

              {scopeOptions.length ? (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-amber-700">范围升级建议</p>
                  <div className="mt-3 space-y-2">
                    {scopeOptions.map((option) => (
                      <button
                        key={option.action}
                        type="button"
                        onClick={() => void handleScopeAction(option)}
                        className="w-full rounded-xl border border-amber-200 bg-white px-4 py-3 text-left transition hover:border-amber-300"
                      >
                        <span className="block text-sm font-semibold text-gray-900">{option.label}</span>
                        <span className="mt-1 block text-xs leading-6 text-gray-500">{option.description}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {clarificationQuestions.length ? (
                <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">需求澄清</p>
                  <p className="mt-2 text-xs leading-6 text-sky-900">
                    {latestBoardDecision?.reason ?? "AI 还需要再确认一点学习目标，才能决定后面的板书策略。"}
                  </p>
                  <div className="mt-3 space-y-2">
                    {clarificationQuestions.map((question, index) => (
                      <div key={`${question}-${index}`} className="rounded-lg bg-white px-3 py-2 text-xs leading-6 text-gray-700">
                        {index + 1}. {question}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {resourceMatches.length ? (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">参考资料命中</p>
                  <div className="mt-3 space-y-2">
                    {resourceMatches.map((match) => (
                      <div key={`${match.resource_id}-${match.chapter_id}`} className="rounded-lg bg-white px-3 py-3 text-xs leading-6 text-gray-700">
                        <p className="font-semibold text-gray-900">
                          {match.resource_name} / {match.chapter_title}
                        </p>
                        <p className="mt-1 text-gray-500">{match.reason}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {selection ? (
                <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">
                        {selectionKindLabel}
                      </p>
                      <p className="mt-1 text-[11px] leading-6 text-gray-500">
                        {selectionMode === "block"
                          ? "已选中整块内容，接下来的提问会自动携带这一整块板书。"
                          : `已框选 ${selectionCharCount} 个字符，接下来的提问会自动携带这段内容。`}
                      </p>
                      <p className="mt-1 text-[11px] leading-6 text-gray-400">{selectionUsageHint}</p>
                    </div>
                    <button
                      type="button"
                      onClick={clearSelection}
                      className="rounded-md border border-gray-200 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-gray-500 hover:border-gray-300 hover:text-black"
                    >
                      清除
                    </button>
                  </div>
                  <p className="mt-3 rounded-lg bg-gray-50 px-3 py-3 text-xs leading-6 text-gray-700">
                    {selection.excerpt.slice(0, 240)}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setChatInput((current) =>
                          current.trim()
                            ? `${current}\n\n请围绕我框选的内容处理。`
                            : "请围绕我框选的内容处理。"
                        )
                      }
                      className="rounded-md border border-gray-200 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-gray-500 hover:border-gray-300 hover:text-black"
                    >
                      引导当前提问
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setChatInput(`请解释我框选的这段内容，并结合当前板书说明。`)
                      }
                      className="rounded-md border border-gray-200 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-gray-500 hover:border-gray-300 hover:text-black"
                    >
                      解释选区
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="shrink-0 border-t border-gray-100 bg-white p-5">
            <div className="group relative mb-4 flex justify-center">
              <button
                type="button"
                onClick={handleVoiceToggle}
                className={clsx(
                  "relative flex h-12 w-12 items-center justify-center rounded-full text-white shadow-md transition-all hover:scale-105 hover:shadow-lg",
                  voiceActive ? "bg-gray-800 ring-4 ring-gray-200" : "bg-[#1a1a1a]"
                )}
              >
                {voiceActive ? <Radio className="h-5 w-5" /> : <Volume2 className="h-5 w-5" />}
              </button>
              <div className="pointer-events-none absolute bottom-full mb-3 hidden rounded-md bg-gray-900 px-3 py-1.5 text-[10px] text-white shadow-xl group-hover:block">
                Start Real-time Voice Conversation
              </div>
            </div>

            <div className="flex items-end gap-2">
              <textarea
                value={chatInput}
                rows={2}
                onChange={(event) => setChatInput(event.target.value)}
                placeholder="提问或下达修改指令..."
                className="custom-scrollbar flex-1 resize-none rounded-xl border border-gray-200 p-3.5 text-[13px] leading-relaxed shadow-sm outline-none transition placeholder:text-gray-400 focus:border-black focus:ring-1 focus:ring-black"
              />
              <button
                type="button"
                onClick={() => void handleSubmitChat()}
                disabled={busyAction === "chat"}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#1a1a1a] text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Send className="h-4 w-4 -translate-x-[1px]" />
              </button>
            </div>
          </div>
        </aside>

        <div
          className={clsx(
            "z-40 w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-[#1a1a1a]",
            dragTarget === "left" && "bg-[#1a1a1a]"
          )}
          onMouseDown={() => setDragTarget("left")}
        />

        <section className="relative z-10 flex min-w-0 flex-1 flex-col overflow-hidden bg-white shadow-[0_0_20px_rgba(0,0,0,0.02)]">
          <div
            className={clsx(
              "shrink-0 border-b border-gray-200 bg-white transition-all duration-300",
              topCollapsed && "-mt-[84px] pointer-events-none opacity-0"
            )}
          >
            <div className="flex h-10 items-center justify-between border-b border-gray-100 px-6">
              <div className="flex items-center gap-5">
                <button className="h-full border-b-2 border-black px-2 text-[10px] font-bold uppercase tracking-widest text-black">
                  开始 (HOME)
                </button>
                <button
                  type="button"
                  onClick={() => setIsCreateLessonDialogOpen(true)}
                  className="h-full px-2 text-[10px] font-bold uppercase tracking-widest text-gray-400 transition-colors hover:text-black"
                >
                  新建 (CREATE)
                </button>
                <button
                  type="button"
                  onClick={() => void handleExportLesson("docx")}
                  className="h-full px-2 text-[10px] font-bold uppercase tracking-widest text-gray-400 transition-colors hover:text-black"
                >
                  导出 Word
                </button>
                <button
                  type="button"
                  onClick={() => void handleExportLesson("pdf")}
                  className="h-full px-2 text-[10px] font-bold uppercase tracking-widest text-gray-400 transition-colors hover:text-black"
                >
                  导出 PDF
                </button>
              </div>
              <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-gray-300">
                {selectedBoardBlock ? "已选中模块" : "先选中一个板书块再编辑样式"}
              </p>
            </div>

            <div className="custom-scrollbar flex min-h-[70px] items-center gap-3 overflow-x-auto px-5 py-3 whitespace-nowrap">
              <div className="flex shrink-0 items-center gap-1.5 border-r border-gray-100 pr-4">
                <select
                  disabled={!selectedBoardBlock || isPreviewMode}
                  value={selectedBoardBlock?.style.font_family ?? "sans"}
                  onChange={(event) =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { font_family: event.target.value })
                      : undefined
                  }
                  className="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-medium outline-none transition-colors hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="sans">Sans</option>
                  <option value="serif">Serif</option>
                  <option value="mono">Mono</option>
                  <option value="display">Display</option>
                </select>
                <select
                  disabled={!selectedBoardBlock || isPreviewMode}
                  value={selectedBoardBlock?.style.font_size ?? "md"}
                  onChange={(event) =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, {
                          font_size: event.target.value as BlockStyle["font_size"],
                        })
                      : undefined
                  }
                  className="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-medium outline-none transition-colors hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="sm">12 px</option>
                  <option value="md">14 px</option>
                  <option value="lg">18 px</option>
                  <option value="xl">22 px</option>
                </select>
              </div>

              <div className="flex shrink-0 items-center gap-1 border-r border-gray-100 px-3">
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { bold: !selectedBoardBlock.style.bold })
                      : undefined
                  }
                  className={clsx(toolbarButtonClass(selectedBoardBlock?.style.bold), "px-2")}
                >
                  <Bold className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { italic: !selectedBoardBlock.style.italic })
                      : undefined
                  }
                  className={clsx(toolbarButtonClass(selectedBoardBlock?.style.italic), "px-2")}
                >
                  <Italic className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, {
                          underline: !selectedBoardBlock.style.underline,
                        })
                      : undefined
                  }
                  className={clsx(toolbarButtonClass(selectedBoardBlock?.style.underline), "px-2")}
                >
                  <Underline className="h-3.5 w-3.5" />
                </button>
              </div>

              <div className="flex shrink-0 items-center gap-1 border-r border-gray-100 px-3">
                <div className="mr-1 flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
                  <Baseline className="h-3.5 w-3.5" />
                  字色
                </div>
                {textColorPresets.map((color) => (
                  <button
                    key={color}
                    type="button"
                    disabled={!selectedBoardBlock || isPreviewMode}
                    onClick={() =>
                      selectedBoardBlock ? handleStyleChange(selectedBoardBlock.id, { text_color: color }) : undefined
                    }
                    className={clsx(
                      "h-5 w-5 rounded-full border transition",
                      selectedBoardBlock?.style.text_color === color ? "border-black" : "border-gray-200"
                    )}
                    style={{ backgroundColor: color }}
                  />
                ))}
                <label className="relative ml-1 flex h-6 w-6 cursor-pointer items-center justify-center rounded-full border border-gray-200 bg-white">
                  <input
                    type="color"
                    disabled={!selectedBoardBlock || isPreviewMode}
                    value={selectedBoardBlock?.style.text_color ?? "#111827"}
                    onChange={(event) =>
                      selectedBoardBlock
                        ? handleStyleChange(selectedBoardBlock.id, { text_color: event.target.value })
                        : undefined
                    }
                    className="absolute inset-0 cursor-pointer opacity-0"
                  />
                  <span
                    className="h-3 w-3 rounded-full border border-gray-300"
                    style={{ backgroundColor: selectedBoardBlock?.style.text_color ?? "#111827" }}
                  />
                </label>
              </div>

              <div className="flex shrink-0 items-center gap-1 border-r border-gray-100 px-3">
                <div className="mr-1 flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
                  <Highlighter className="h-3.5 w-3.5" />
                  高亮
                </div>
                {highlightPresets.map((color) => (
                  <button
                    key={color}
                    type="button"
                    disabled={!selectedBoardBlock || isPreviewMode}
                    onClick={() =>
                      selectedBoardBlock
                        ? handleStyleChange(selectedBoardBlock.id, { highlight_color: color })
                        : undefined
                    }
                    className={clsx(
                      "h-5 w-5 rounded-full border transition",
                      selectedBoardBlock?.style.highlight_color === color ? "border-black" : "border-gray-200"
                    )}
                    style={{ backgroundColor: color === "transparent" ? "#ffffff" : color }}
                    title={color === "transparent" ? "清除高亮" : "设置高亮"}
                  />
                ))}
              </div>

              <div className="flex shrink-0 items-center gap-1 border-r border-gray-100 px-3">
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { alignment: "left" })
                      : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.alignment === "left")}
                >
                  <AlignLeft className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { alignment: "center" })
                      : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.alignment === "center")}
                >
                  <AlignCenter className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock
                      ? handleStyleChange(selectedBoardBlock.id, { alignment: "right" })
                      : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.alignment === "right")}
                >
                  右
                </button>
              </div>

              <div className="flex shrink-0 items-center gap-1 border-r border-gray-100 px-3">
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock ? handleStyleChange(selectedBoardBlock.id, { width: "normal" }) : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.width === "normal")}
                >
                  常规
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock ? handleStyleChange(selectedBoardBlock.id, { width: "wide" }) : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.width === "wide")}
                >
                  宽栏
                </button>
                <button
                  type="button"
                  disabled={!selectedBoardBlock || isPreviewMode}
                  onClick={() =>
                    selectedBoardBlock ? handleStyleChange(selectedBoardBlock.id, { width: "full" }) : undefined
                  }
                  className={toolbarButtonClass(selectedBoardBlock?.style.width === "full")}
                >
                  全宽
                </button>
              </div>

              <div className="flex shrink-0 items-center gap-2 px-3">
                <select
                  disabled={isPreviewMode}
                  defaultValue=""
                  onChange={(event) => {
                    const value = event.target.value as BlockType;
                    if (value) {
                      handleAddBlock(value);
                    }
                    event.target.value = "";
                  }}
                  className="rounded-md border border-gray-200 bg-white px-3 py-2 text-[11px] font-medium outline-none transition-colors hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">插入模块</option>
                  {blockTypeOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => void handleExportLesson("docx")}
                  className={toolbarButtonClass(false)}
                >
                  Word
                </button>
                <button
                  type="button"
                  onClick={() => void handleExportLesson("pdf")}
                  className={toolbarButtonClass(false)}
                >
                  PDF
                </button>
              </div>

              <div className="min-w-[20px] flex-1" />

              <button
                type="button"
                onClick={() => void handleSaveManualEdits()}
                disabled={!pendingManualOps.length || busyAction === "save"}
                className="shrink-0 rounded-md bg-[#1a1a1a] px-3.5 py-1.5 text-[10px] font-bold uppercase tracking-widest text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Save className="mr-1.5 inline h-3.5 w-3.5" />
                保存 {pendingManualOps.length ? `(${pendingManualOps.length})` : ""}
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
            <div className="mx-auto w-full max-w-3xl px-10 py-16 md:px-14 lg:px-20">
              {isPreviewMode ? (
                <div className="mb-8 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-700">
                  正在预览历史快照：{previewCommit?.label}
                  <button
                    type="button"
                    className="ml-3 rounded-md border border-violet-200 bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-violet-700"
                    onClick={() => setPreviewCommitId(null)}
                  >
                    回到当前版本
                  </button>
                </div>
              ) : null}

              {pendingProposal ? (
                <div className="mb-8 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-700">
                        AI Patch Preview
                      </p>
                      <p className="mt-2 text-sm leading-7 text-gray-700">{pendingProposal.rationale}</p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setPendingProposal(null)}
                        className="rounded-md border border-gray-200 bg-white px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-gray-500"
                      >
                        拒绝
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleApplyProposal()}
                        disabled={busyAction === "apply-proposal"}
                        className="rounded-md bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
                      >
                        接受并提交
                      </button>
                    </div>
                  </div>
                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {pendingProposal.diff_preview.map((item) => (
                      <DiffPreviewCard key={`${item.op}-${item.block_id ?? item.summary}`} item={item} />
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="mb-8 overflow-hidden rounded-[30px] border border-slate-200 bg-[linear-gradient(135deg,#fff7ed_0%,#ffffff_48%,#eff6ff_100%)] p-6 shadow-[0_20px_60px_rgba(15,23,42,0.06)]">
                <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
                  <div className="max-w-2xl">
                    <p className="text-[11px] font-bold uppercase tracking-[0.28em] text-slate-500">
                      Course Overview
                    </p>
                    <h2 className="mt-3 text-3xl font-bold tracking-tight text-slate-900">
                      {activeLesson.title}
                    </h2>
                    <p className="mt-3 text-sm leading-8 text-slate-600">{activeOverview}</p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {activeLesson.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full border border-white/70 bg-white/70 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setIsCreateLessonDialogOpen(true)}
                      className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-black"
                    >
                      新建课程
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportLesson("docx")}
                      className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-black"
                    >
                      导出 Word
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportLesson("pdf")}
                      className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-black"
                    >
                      导出 PDF
                    </button>
                  </div>
                </div>

                <div className="mt-6 grid gap-3 md:grid-cols-4">
                  {[
                    { label: "课程数", value: String(coursePackage.lessons.length) },
                    { label: "打开标签", value: String(totalOpenTabs) },
                    { label: "资料数", value: String(totalResources) },
                    { label: "当前版本", value: String(totalCommitCount) },
                  ].map((item) => (
                    <div
                      key={item.label}
                      className="rounded-2xl border border-white/80 bg-white/75 px-4 py-4 shadow-sm backdrop-blur"
                    >
                      <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400">
                        {item.label}
                      </p>
                      <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{item.value}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <div className="space-y-4">
                  <h1 className="text-[2rem] font-bold leading-tight tracking-tight text-gray-900">
                    {displayedDocument.title}
                  </h1>
                  <div className="h-px w-full bg-gray-200" />
                </div>

                <div className="flex flex-wrap gap-2 pt-2">
                  {activeLesson.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full border border-gray-200 bg-white px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400"
                    >
                      {tag}
                    </span>
                  ))}
                </div>

                <div className="space-y-8 pt-6">
                  {displayedDocument.blocks.map((block) => (
                    <DocumentBlock
                      key={block.id}
                      block={block}
                      selected={selection?.block_id === block.id}
                      selectionMode={
                        selection?.kind === "board" && selection?.block_id === block.id
                          ? selectionMode
                          : null
                      }
                      disabled={isPreviewMode}
                      onSelect={() =>
                        applySelection(
                          {
                            kind: "board",
                            lesson_id: activeLesson.id,
                            block_id: block.id,
                            excerpt: `${block.title}\n${block.content}`,
                          },
                          "block"
                        )
                      }
                      onSelectTextQuote={() =>
                        applySelection(
                          {
                            kind: "board",
                            lesson_id: activeLesson.id,
                            block_id: block.id,
                            excerpt: selection?.block_id === block.id ? selection.excerpt : `${block.title}\n${block.content}`,
                          },
                          "text"
                        )
                      }
                      onTitleChange={(value) => handleBlockContentChange(block.id, "title", value)}
                      onContentChange={(value) => handleBlockContentChange(block.id, "content", value)}
                      onMoveUp={() => handleMoveBlock(block.id, "up")}
                      onMoveDown={() => handleMoveBlock(block.id, "down")}
                      onDelete={() => handleDeleteBlock(block.id)}
                      onStyleChange={(style) => handleStyleChange(block.id, style)}
                      onTextSelect={(excerpt) =>
                        applySelection(
                          {
                            kind: "board",
                            lesson_id: activeLesson.id,
                            block_id: block.id,
                            excerpt,
                          },
                          "text"
                        )
                      }
                    />
                  ))}
                </div>
              </div>

              <div className="h-32" />
            </div>
          </div>
        </section>

        {rightSidebarOpen ? (
          <div
            className={clsx(
              "z-40 w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-[#1a1a1a]",
              dragTarget === "right" && "bg-[#1a1a1a]"
            )}
            onMouseDown={() => setDragTarget("right")}
          />
        ) : null}

        <aside
          className={clsx(
            "z-20 flex h-full shrink-0 flex-col border-l border-gray-200 bg-[#fcfcfc] transition-all duration-300",
            !rightSidebarOpen && "pointer-events-none opacity-0"
          )}
          style={{ width: rightSidebarOpen ? `${rightWidth}%` : "0%" }}
        >
          <div className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-5">
            <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">课程工作台辅助</h4>
            <button
              type="button"
              onClick={() => setRightSidebarOpen(false)}
              className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="flex border-b border-gray-200 bg-white">
            {[
              { value: "history", label: "History" },
              { value: "branch", label: "Branch" },
              { value: "library", label: "Library" },
            ].map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => setSidebarTab(tab.value as SidebarTab)}
                className={clsx(
                  "flex-1 py-3 text-[10px] font-bold uppercase tracking-wider transition-colors",
                  sidebarTab === tab.value
                    ? "border-b-2 border-black text-black"
                    : "text-gray-400 hover:text-black"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-5 custom-scrollbar">
            {sidebarTab === "history" ? (
              <div className="space-y-8">
                <div className="space-y-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
                  {[...activeLesson.history_graph.commits].reverse().map((commit, index) => (
                    <CommitTimelineItem
                      key={commit.id}
                      commit={commit}
                      active={commit.id === previewCommitId}
                      latest={index === 0}
                      onPreview={() => setPreviewCommitId(commit.id)}
                      onRestore={() => void handleRestoreCommit(commit.id)}
                    />
                  ))}
                </div>
              </div>
            ) : null}

            {sidebarTab === "branch" ? (
              <div className="space-y-8">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">分支管理</p>
                  <div className="mt-4 flex gap-2">
                    <input
                      value={newBranchName}
                      onChange={(event) => setNewBranchName(event.target.value)}
                      placeholder="新分支名"
                      className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm outline-none focus:border-black"
                    />
                    <button
                      type="button"
                      onClick={() => void handleCreateBranch()}
                      className="rounded-xl bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
                    >
                      <GitBranch className="mr-1.5 inline h-3.5 w-3.5" />
                      开分支
                    </button>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {Object.values(activeLesson.history_graph.branches).map((branch) => (
                      <button
                        key={branch.name}
                        type="button"
                        onClick={() => void handleSwitchBranch(branch.name)}
                        className={clsx(
                          "rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                          activeLesson.history_graph.current_branch === branch.name
                            ? "border-black bg-black text-white"
                            : "border-gray-200 bg-white text-gray-500 hover:text-black"
                        )}
                      >
                        {branch.name}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="border-t border-gray-200 pt-6">
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="h-4 w-4 text-gray-400" />
                    <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">需求清单</p>
                  </div>
                  <p className="mt-4 text-sm leading-7 text-gray-700">
                    {activeRequirements?.learning_goal ?? "围绕当前板书主线推进学习。"}
                  </p>
                  <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                    <p className="text-xs font-semibold text-gray-900">
                      {activeRequirements?.target_depth ?? "先建立概念，再进入例题与练习"}
                    </p>
                    <p className="mt-2 text-[11px] leading-6 text-gray-500">
                      {activeRequirements?.success_criteria ?? "先讲清当前问题，再决定是否要扩展板书。"}
                    </p>
                  </div>
                  {latestBoardDecision ? (
                    <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                      <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前板书决策</p>
                      <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
                      <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {sidebarTab === "library" ? (
              <div className="space-y-8">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">关联资料库</p>
                  <label className="mt-4 block rounded-xl border border-dashed border-gray-300 bg-white px-4 py-5 text-center text-sm text-gray-500 hover:border-gray-400">
                    上传文件或图片
                    <input
                      type="file"
                      className="hidden"
                      onChange={(event) => void handleUploadResource(event.target.files?.[0] ?? null)}
                    />
                  </label>
                  <div className="mt-4 space-y-3">
                    {coursePackage.resources.length ? (
                      coursePackage.resources.map((resource) => (
                        <div
                          key={resource.id}
                          className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-gray-300"
                        >
                          <div className="flex items-start gap-3">
                            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                              {resource.mime_type.startsWith("image/") ? (
                                <Languages className="h-4 w-4" />
                              ) : (
                                <FileText className="h-4 w-4" />
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-xs font-bold text-gray-900">{resource.name}</p>
                              <p className="mt-1 text-[11px] text-gray-500">
                                {resource.extracted_text_available
                                  ? `已索引 ${resource.outline.length} 个章节入口`
                                  : "当前仅做入口索引"}
                              </p>
                              <p className="mt-1 text-[10px] uppercase tracking-[0.18em] text-gray-400">
                                {resource.mime_type} / {formatBytes(resource.size_bytes)}
                              </p>
                            </div>
                          </div>
                          <div className="mt-3 space-y-2">
                            {resource.outline.slice(0, 3).map((chapter) => (
                              <div key={chapter.id} className="rounded-lg bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
                                <p className="font-semibold text-gray-800">{chapter.title}</p>
                                <p className="mt-1 leading-6">{chapter.summary}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">
                        还没有上传资料。可以先把教材、讲义或图片放进当前课程资料库。
                      </div>
                    )}
                  </div>
                </div>

                <div className="border-t border-gray-200 pt-6">
                  <div className="mb-4 flex items-center gap-2">
                    <BookOpen className="h-4 w-4 text-gray-400" />
                    <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">课程图谱</p>
                  </div>
                  <div className="space-y-3">
                    {relatedEdges.length ? (
                      relatedEdges.map((edge) => {
                        const source = lessonMap.get(edge.source_lesson_id);
                        const target = lessonMap.get(edge.target_lesson_id);
                        if (!source || !target) {
                          return null;
                        }

                        const nextLesson = edge.source_lesson_id === activeLesson.id ? target : source;
                        return (
                          <button
                            key={edge.id}
                            type="button"
                            onClick={() => void handleOpenLesson(nextLesson.id)}
                            className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:border-gray-300"
                          >
                            <p className="text-xs font-bold text-gray-900">
                              {source.title} → {target.title}
                            </p>
                            <p className="mt-1 text-[11px] text-gray-500">关系：{edge.relationship}</p>
                          </button>
                        );
                      })
                    ) : (
                      <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">
                        当前 lesson 还没有更多图谱关系。
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </main>
  );
}
