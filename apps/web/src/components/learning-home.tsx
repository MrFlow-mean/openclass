"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDeferredValue, useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Bell,
  BookOpen,
  BookText,
  ChevronDown,
  ChevronRight,
  FolderClosed,
  FolderPlus,
  LoaderCircle,
  MoreHorizontal,
  Search,
  Share2,
  Sparkles,
  Trash2,
  Trophy,
  UserRound,
} from "lucide-react";

import { api } from "@/lib/api";
import type { CommitRecord, CoursePackage, Lesson, ResourceLibraryItem, WorkspaceState } from "@/types";

const CONTRIBUTION_WEEKS = 32;

const FEATURED_MARKETPLACE = [
  {
    id: "node-patterns",
    topic: "Node.js 设计模式",
    badge: "精品专题",
    summary: "从事件循环到服务拆分，做一套真正能扩展的 Node.js 工程结构。",
    details: ["12 节模块", "项目驱动", "后端架构"],
    accent: "from-orange-100 via-white to-amber-50",
  },
  {
    id: "react-performance",
    topic: "React 性能优化实战",
    badge: "热门课",
    summary: "围绕渲染链路、状态分层和真实性能瓶颈，建立前端调优方法论。",
    details: ["9 节模块", "案例拆解", "前端进阶"],
    accent: "from-sky-100 via-white to-cyan-50",
  },
  {
    id: "product-writing",
    topic: "AI 课程脚本与讲义设计",
    badge: "工作流",
    summary: "把知识点、讲义结构、练习设计和 AI 互动链路串成一套标准流程。",
    details: ["8 节模块", "可落地模板", "内容生产"],
    accent: "from-emerald-100 via-white to-lime-50",
  },
] as const;

type ActivityDay = {
  key: string;
  date: Date;
  count: number;
  level: 0 | 1 | 2 | 3 | 4;
};

type FeedKind = "commit" | "resource";
type FeedFilter = "all" | FeedKind;

type FeedItem = {
  id: string;
  kind: FeedKind;
  timestamp: string;
  actor: string;
  action: string;
  title: string;
  detailTitle: string;
  detailBody: string;
  pills: string[];
  lessonId?: string;
};

type LessonShelfItem = {
  lesson: Lesson;
  packageId: string;
  packageTitle: string;
  isPackaged: boolean;
};

type LessonMenuState = {
  lessonId: string;
  top: number;
  left: number;
};

function sortByUpdatedAt(items: Lesson[]) {
  return [...items].sort((left, right) => {
    const leftTime = new Date(left.updated_at).getTime();
    const rightTime = new Date(right.updated_at).getTime();
    return rightTime - leftTime;
  });
}

function matchesQuery(query: string, ...values: Array<string | null | undefined>) {
  if (!query) {
    return true;
  }

  return values.some((value) => value?.toLowerCase().includes(query));
}

function formatRelativeTime(value: string | Date | null | undefined) {
  if (!value) {
    return "刚刚";
  }

  const date = value instanceof Date ? value : new Date(value);
  const timestamp = date.getTime();

  if (Number.isNaN(timestamp)) {
    return "刚刚";
  }

  const minutes = Math.floor((Date.now() - timestamp) / 60000);

  if (minutes <= 0) {
    return "刚刚";
  }

  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }

  const days = Math.floor(hours / 24);
  if (days < 7) {
    return `${days} 天前`;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

function dayKey(date: Date) {
  return date.toISOString().slice(0, 10);
}

function getActivityLevel(count: number, maxCount: number): ActivityDay["level"] {
  if (count <= 0) {
    return 0;
  }

  if (maxCount <= 1) {
    return count > 0 ? 4 : 0;
  }

  const ratio = count / maxCount;

  if (ratio >= 0.75) {
    return 4;
  }
  if (ratio >= 0.5) {
    return 3;
  }
  if (ratio >= 0.25) {
    return 2;
  }
  return 1;
}

function buildActivitySummary(coursePackage: CoursePackage | null) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const activityByDay = new Map<string, number>();
  const track = (value?: string | null) => {
    if (!value) {
      return;
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return;
    }

    const key = dayKey(date);
    activityByDay.set(key, (activityByDay.get(key) ?? 0) + 1);
  };

  coursePackage?.lessons.forEach((lesson) => {
    track(lesson.created_at);
    track(lesson.updated_at);
    lesson.history_graph.commits.forEach((commit) => {
      track(commit.created_at);
    });
  });
  coursePackage?.resources.forEach((resource) => {
    track(resource.uploaded_at);
  });

  const days: ActivityDay[] = [];
  const totalDays = CONTRIBUTION_WEEKS * 7;
  for (let offset = totalDays - 1; offset >= 0; offset -= 1) {
    const date = new Date(today);
    date.setDate(today.getDate() - offset);
    const count = activityByDay.get(dayKey(date)) ?? 0;
    days.push({
      key: dayKey(date),
      date,
      count,
      level: 0,
    });
  }

  const maxCount = days.reduce((max, day) => Math.max(max, day.count), 0);
  const leveledDays = days.map((day) => ({
    ...day,
    level: getActivityLevel(day.count, maxCount),
  }));

  const weeks = Array.from({ length: CONTRIBUTION_WEEKS }, (_, index) =>
    leveledDays.slice(index * 7, index * 7 + 7)
  );

  return {
    total: days.reduce((sum, day) => sum + day.count, 0),
    recentActiveDay: [...leveledDays].reverse().find((day) => day.count > 0) ?? null,
    weeks,
  };
}

function truncateText(value: string, maxLength = 160) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  if (normalized.length <= maxLength) {
    return normalized;
  }

  return `${normalized.slice(0, maxLength).trimEnd()}...`;
}

function humanizeCommitLabel(label: string) {
  switch (label) {
    case "Initial document draft":
      return "初始课程草稿";
    case "Manual document edit":
      return "手动编辑已保存";
    case "Restore snapshot":
      return "恢复历史快照";
    case "AI document edit":
      return "AI 更新文稿";
    case "Cloned lesson snapshot":
      return "克隆课程快照";
    default:
      return label;
  }
}

function humanizeCommitMessage(commit: CommitRecord, lesson: Lesson) {
  const normalized = commit.message.trim();

  if (!normalized) {
    return `已更新《${lesson.title}》的课程内容，可以继续进入工作台完善讲义与分支。`;
  }

  const rewritten = normalized
    .replace(/^Generated starter rich document for\s+/i, "已生成课程初稿：")
    .replace(/^Saved Word-like rich document changes from the editor$/i, "已保存 Word 风格编辑器中的文稿改动。")
    .replace(/^Saved rich document changes from the editor$/i, "已保存编辑器中的文稿改动。")
    .replace(/^Cloned lesson into an isolated workspace$/i, "已复制到独立工作区，方便继续扩展。");

  return truncateText(rewritten, 180);
}

function activityTone(level: ActivityDay["level"]) {
  return {
    0: "bg-stone-200",
    1: "bg-stone-300",
    2: "bg-slate-400/70",
    3: "bg-slate-700/70",
    4: "bg-slate-950",
  }[level];
}

function resourceSummary(resource: ResourceLibraryItem) {
  return resource.outline[0]?.summary ?? "已进入资料库，可在工作台中继续引用和扩展。";
}

function resourceTypeLabel(resource: ResourceLibraryItem) {
  if (resource.mime_type.includes("pdf")) {
    return "PDF";
  }
  if (resource.mime_type.includes("word") || resource.mime_type.includes("document")) {
    return "Word";
  }
  if (resource.mime_type.startsWith("image/")) {
    return "图片";
  }

  return resource.resource_type || "资料";
}

function buildRecentFeed(lessons: Lesson[], resources: ResourceLibraryItem[]) {
  const commitItems: FeedItem[] = lessons.flatMap((lesson) =>
    lesson.history_graph.commits.map((commit) => ({
      id: `commit:${commit.id}`,
      kind: "commit",
      timestamp: commit.created_at,
      actor: lesson.title,
      action: "更新了课程文稿",
      title: humanizeCommitLabel(commit.label),
      detailTitle: commit.branch_name === "main" ? "主分支 main" : `分支 ${commit.branch_name}`,
      detailBody: humanizeCommitMessage(commit, lesson),
      pills: [lesson.tags[0] ?? "课程内容", `${lesson.history_graph.commits.length} 次提交`],
      lessonId: lesson.id,
    }))
  );

  const resourceItems: FeedItem[] = resources.map((resource) => ({
    id: `resource:${resource.id}`,
    kind: "resource",
    timestamp: resource.uploaded_at,
    actor: "资料库",
    action: "收录了新资料",
    title: resource.name,
    detailTitle: resource.outline[0]?.title ?? "资料摘要",
    detailBody: truncateText(resourceSummary(resource), 180),
    pills: [
      resourceTypeLabel(resource),
      resource.outline.length ? `${resource.outline.length} 个索引片段` : "等待生成索引",
    ],
  }));

  return [...commitItems, ...resourceItems]
    .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())
    .slice(0, 4);
}

export function LearningHome() {
  const router = useRouter();
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const deferredQuery = useDeferredValue(searchQuery.trim().toLowerCase());
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [feedCollapsed, setFeedCollapsed] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [lessonMenuState, setLessonMenuState] = useState<LessonMenuState | null>(null);
  const [lessonMoveMenuState, setLessonMoveMenuState] = useState<LessonMenuState | null>(null);

  useEffect(() => {
    let isDisposed = false;

    async function load() {
      try {
        const payload = await api.getWorkspace();
        if (isDisposed) {
          return;
        }
        setWorkspaceState(payload);
        setError(null);
      } catch (loadError) {
        if (isDisposed) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "加载主页数据失败");
      } finally {
        if (!isDisposed) {
          setIsLoading(false);
        }
      }
    }

    void load();

    return () => {
      isDisposed = true;
    };
  }, []);

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (target.closest("[data-lesson-menu-root]")) {
        return;
      }
      setLessonMenuState(null);
      setLessonMoveMenuState(null);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") {
        return;
      }
      setLessonMenuState(null);
      setLessonMoveMenuState(null);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const packages = workspaceState?.packages ?? [];
  const activePackageId = workspaceState?.active_package_id ?? packages[0]?.id ?? null;
  const coursePackage = packages.find((item) => item.id === activePackageId) ?? packages[0] ?? null;
  const standalonePackage = packages[0] ?? null;
  const isStandaloneSelected = coursePackage?.id === standalonePackage?.id;
  const movablePackages = packages.filter((packageItem) => packageItem.id !== standalonePackage?.id);
  const lessons = coursePackage?.lessons ?? [];
  const resources = coursePackage?.resources ?? [];
  const activeLesson =
    lessons.find((lesson) => lesson.id === coursePackage?.active_lesson_id) ?? lessons[0] ?? null;
  const recentLessons = sortByUpdatedAt(lessons).slice(0, 6);
  const selectedPackageLessons = sortByUpdatedAt(lessons).slice(0, 8);
  const standaloneLessonItems: LessonShelfItem[] = sortByUpdatedAt(standalonePackage?.lessons ?? []).map((lesson) => ({
    lesson,
    packageId: standalonePackage?.id ?? "standalone",
    packageTitle: standalonePackage?.title ?? "单独课程",
    isPackaged: false,
  }));
  const filteredLessonItems = standaloneLessonItems.filter(({ lesson, packageTitle }) =>
    matchesQuery(
      deferredQuery,
      lesson.title,
      lesson.summary,
      lesson.tags.join(" "),
      lesson.board_document.title,
      lesson.board_document.content_text,
      packageTitle
    )
  );

  const filteredMarketplace = FEATURED_MARKETPLACE.filter((course) =>
    matchesQuery(deferredQuery, course.topic, course.summary, course.badge, course.details.join(" "))
  );

  const quickResultLessons = standaloneLessonItems
    .filter(({ lesson, packageTitle }) =>
      matchesQuery(
        deferredQuery,
        lesson.title,
        lesson.summary,
        lesson.tags.join(" "),
        lesson.board_document.content_text,
        packageTitle
      )
    )
    .slice(0, 3);

  const quickResultResources = resources
    .filter((resource) =>
      matchesQuery(
        deferredQuery,
        resource.name,
        resourceSummary(resource),
        resource.outline.map((chapter) => `${chapter.title} ${chapter.summary}`).join(" "),
        Object.keys(resource.concept_index).join(" ")
      )
    )
    .slice(0, 2);

  const activity = buildActivitySummary(coursePackage);
  const latestLesson = recentLessons[0] ?? null;
  const lessonMenuLesson =
    lessonMenuState ? standaloneLessonItems.find(({ lesson }) => lesson.id === lessonMenuState.lessonId)?.lesson ?? null : null;
  const latestResource =
    [...resources].sort(
      (left, right) => new Date(right.uploaded_at).getTime() - new Date(left.uploaded_at).getTime()
    )[0] ?? null;
  const feedItems = buildRecentFeed(lessons, resources);
  const visibleFeedItems = feedFilter === "all" ? feedItems : feedItems.filter((item) => item.kind === feedFilter);

  async function handleOpenLesson(lessonId: string) {
    setBusyKey(`lesson:${lessonId}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);

    try {
      await api.openLesson(lessonId);
      router.push("/studio");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "打开课程失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleMoveLesson(lesson: Lesson, targetPackageId: string) {
    if (!targetPackageId) {
      return;
    }

    setBusyKey(`move:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.moveLesson(lesson.id, targetPackageId);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "移动课程失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDeleteLesson(lesson: Lesson) {
    if (typeof window !== "undefined" && !window.confirm(`确定删除《${lesson.title}》吗？`)) {
      return;
    }

    setBusyKey(`delete:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.deleteLesson(lesson.id);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "删除课程失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleCreatePackage() {
    const title = window.prompt("请输入课程包名称，例如：法考冲刺包");
    if (!title?.trim()) {
      return;
    }

    setBusyKey("package:create");
    try {
      const payload = await api.createPackage(title.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "新建课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleOpenPackage(packageItem: CoursePackage) {
    setBusyKey(`package:${packageItem.id}`);

    try {
      if (packageItem.id === activePackageId) {
        router.push("/studio");
        return;
      }
      const payload = await api.openPackage(packageItem.id);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "打开课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleGenerateFromMarketplace(topic: string, cardId: string) {
    setBusyKey(`market:${cardId}`);

    try {
      await api.generateLesson(topic, coursePackage?.active_lesson_id ?? undefined, true);
      router.push("/studio");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "创建精品课程失败");
    } finally {
      setBusyKey(null);
    }
  }
  const feedFilters = [
    { id: "all" as const, label: "全部" },
    { id: "commit" as const, label: "课程提交" },
    { id: "resource" as const, label: "资料更新" },
  ];

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f7f5ef] text-[#171717]">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute left-[-7rem] top-16 h-64 w-64 rounded-full bg-sky-200/45 blur-3xl" />
        <div className="absolute right-[-5rem] top-0 h-80 w-80 rounded-full bg-orange-200/40 blur-3xl" />
        <div className="absolute bottom-[-8rem] right-32 h-72 w-72 rounded-full bg-emerald-200/30 blur-3xl" />
      </div>

      <div className="relative mx-auto flex min-h-screen max-w-[1600px] flex-col lg:flex-row">
        <aside className="border-b border-stone-200/80 bg-[#fcfbf8]/85 backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:w-80 lg:border-b-0 lg:border-r">
          <div className="flex h-full flex-col p-4 sm:p-5">
            <div className="mb-8 flex items-center justify-between px-2">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">
                  Blackboard AI
                </p>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight text-stone-950">Learning Hub</h1>
              </div>
              <button
                type="button"
                className="rounded-2xl border border-stone-200 bg-white p-2 text-stone-500 transition hover:border-stone-300 hover:text-stone-950"
                aria-label="更多设置"
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            </div>

            <div className="mb-8">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">课程包</h2>
                <button
                  type="button"
                  onClick={() => void handleCreatePackage()}
                  className="rounded-xl p-1.5 text-stone-400 transition hover:bg-stone-200/60 hover:text-stone-950"
                  aria-label="添加课程包"
                >
                  {busyKey === "package:create" ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <FolderPlus className="h-4 w-4" />
                  )}
                </button>
              </div>
              <div className="space-y-2">
                {packages.length ? (
                  packages.map((packageItem) => {
                    const isActive = packageItem.id === activePackageId;
                    const isBusy = busyKey === `package:${packageItem.id}`;
                    return (
                      <button
                        key={packageItem.id}
                        type="button"
                        onClick={() => void handleOpenPackage(packageItem)}
                        className={clsx(
                          "group w-full rounded-2xl border px-4 py-3 text-left transition",
                          isActive
                            ? "border-stone-950 bg-stone-950 text-white shadow-[0_18px_35px_rgba(23,23,23,0.14)]"
                            : "border-transparent bg-white/75 text-stone-700 hover:border-stone-200 hover:bg-white"
                        )}
                      >
                        <div className="flex items-start gap-3">
                          <div
                            className={clsx(
                              "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border transition",
                              isActive
                                ? "border-white/10 bg-white/10 text-white"
                                : "border-stone-200 bg-stone-100 text-stone-500 group-hover:text-stone-950"
                            )}
                          >
                            {isBusy ? (
                              <LoaderCircle className="h-4 w-4 animate-spin" />
                            ) : (
                              <FolderClosed className="h-4 w-4" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-2">
                              <p className="truncate text-sm font-semibold">{packageItem.title}</p>
                              <span
                                className={clsx(
                                  "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                                  isActive ? "bg-white/10 text-white" : "bg-stone-100 text-stone-500"
                                )}
                              >
                                {packageItem.lessons.length}
                              </span>
                            </div>
                            <p
                              className={clsx(
                                "mt-1 line-clamp-2 text-xs leading-5",
                                isActive ? "text-white/75" : "text-stone-500"
                              )}
                            >
                              {isActive
                                ? "已选中，再点一次进入工作台。"
                                : packageItem.summary || "空课程包，点一下先选中它。"}
                            </p>
                          </div>
                        </div>
                      </button>
                    );
                  })
                ) : (
                  <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-4 py-6 text-sm text-stone-500">
                    还没有课程包，先点右上角的加号创建一个空课程包。
                  </div>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">新课程</h2>
                <div className="flex items-center gap-2">
                  <span className="rounded-full border border-stone-200 bg-white px-3 py-1 text-[10px] font-medium text-stone-500">
                    默认仅显示未入包课程
                  </span>
                  <Link
                    href="/studio"
                    className="rounded-xl p-1.5 text-stone-400 transition hover:bg-stone-200/60 hover:text-stone-950"
                    aria-label="进入工作台"
                  >
                    <BookOpen className="h-4 w-4" />
                  </Link>
                </div>
              </div>

              <div className="custom-scrollbar max-h-[24rem] space-y-2 overflow-y-auto pr-1">
                {isLoading ? (
                  Array.from({ length: 4 }).map((_, index) => (
                    <div key={index} className="rounded-2xl border border-stone-200 bg-white px-4 py-4">
                      <div className="h-4 w-2/3 animate-pulse rounded bg-stone-200" />
                      <div className="mt-3 h-3 w-full animate-pulse rounded bg-stone-100" />
                    </div>
                  ))
                ) : filteredLessonItems.length ? (
                  filteredLessonItems.map(({ lesson }) => {
                    const isActive = lesson.id === activeLesson?.id;
                    const buttonBusy = busyKey === `lesson:${lesson.id}`;
                    const isMenuOpen = lessonMenuState?.lessonId === lesson.id;
                    return (
                      <article
                        key={lesson.id}
                        className={clsx(
                          "relative rounded-2xl border bg-white transition",
                          isActive
                            ? "border-stone-950 shadow-[0_12px_30px_rgba(0,0,0,0.06)]"
                            : "border-transparent bg-white/65 hover:border-stone-200 hover:bg-white"
                        )}
                      >
                        <div className="absolute right-2 top-2 z-20" data-lesson-menu-root>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              const rect = event.currentTarget.getBoundingClientRect();
                              setLessonMoveMenuState(null);
                              setLessonMenuState((current) =>
                                current?.lessonId === lesson.id
                                  ? null
                                  : {
                                      lessonId: lesson.id,
                                      top: rect.bottom + 6,
                                      left: Math.max(16, rect.right - 192),
                                    }
                              );
                            }}
                            className={clsx(
                              "flex h-8 w-8 items-center justify-center rounded-lg text-stone-400 transition hover:bg-stone-100 hover:text-stone-950",
                              isMenuOpen && "bg-stone-100 text-stone-950"
                            )}
                            aria-label="打开课程操作菜单"
                            title="更多操作"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </button>
                        </div>

                        <button
                          type="button"
                          onClick={() => void handleOpenLesson(lesson.id)}
                          className="group w-full px-4 py-3 pr-14 text-left"
                        >
                          <div className="flex items-start gap-3">
                            <div
                              className={clsx(
                                "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                                isActive
                                  ? "border-stone-950 bg-stone-950 text-white"
                                  : "border-stone-200 bg-stone-100 text-stone-500 group-hover:text-stone-950"
                              )}
                            >
                              {buttonBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <BookText className="h-4 w-4" />}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-3">
                                <p className="truncate text-sm font-medium text-stone-950">{lesson.title}</p>
                                <span className="shrink-0 text-[10px] text-stone-400">
                                  {formatRelativeTime(lesson.updated_at)}
                                </span>
                              </div>
                              <p className="mt-1 line-clamp-2 text-xs leading-5 text-stone-500">{lesson.summary}</p>
                            </div>
                          </div>
                        </button>
                      </article>
                    );
                  })
                ) : (
                  <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-4 py-6 text-sm text-stone-500">
                    {standaloneLessonItems.length
                      ? "还没有匹配到课程。试试换个关键词，或者去工作台创建一节新课。"
                      : "现在没有未被存入课程包的单独课程。你可以先新建课程，或者把包内课程移回单独课程池。"}
                  </div>
                )}
              </div>
            </div>

            <div className="mt-6 rounded-[24px] border border-stone-200 bg-white/85 p-3">
              <button
                type="button"
                className="flex w-full items-center gap-3 rounded-[18px] px-2 py-2 text-left transition hover:bg-stone-50"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-stone-950 text-white">
                  <UserRound className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-stone-950">个人工作区</p>
                  <p className="text-xs text-stone-400">产品主页 / 学习管理入口</p>
                </div>
              </button>
            </div>
          </div>
        </aside>

        <main className="relative flex-1 px-4 py-5 sm:px-6 lg:px-8 xl:pr-[25rem]">
          <div className="mx-auto max-w-4xl">
            {error ? (
              <div className="mb-6 rounded-[24px] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
                {error}
              </div>
            ) : null}

            <div className="mb-8 xl:hidden">
              <div className="w-full max-w-sm">{renderNotificationPanel()}</div>
            </div>

            <section className="mb-12">
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4 text-stone-400">
                  <Search className="h-4 w-4" />
                </div>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="搜索你的课程、笔记、资料或想学的主题..."
                  className="w-full rounded-[28px] border border-white/70 bg-white/80 py-4 pl-11 pr-24 text-sm text-stone-950 shadow-[0_18px_40px_rgba(15,23,42,0.06)] outline-none transition placeholder:text-stone-400 focus:border-stone-950 focus:bg-white"
                />
                <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
                  <span className="rounded-md border border-stone-200 bg-white px-2 py-1 font-mono text-[10px] text-stone-400">
                    ⌘K
                  </span>
                </div>
              </div>

              {deferredQuery ? (
                <div className="mt-4 rounded-[28px] border border-white/70 bg-white/80 p-4 shadow-[0_18px_40px_rgba(15,23,42,0.06)] backdrop-blur">
                  <div className="mb-3 flex items-center justify-between">
                    <p className="text-xs font-semibold uppercase tracking-[0.24em] text-stone-400">快速结果</p>
                    <p className="text-xs text-stone-400">
                      共 {quickResultLessons.length + quickResultResources.length + filteredMarketplace.length} 项
                    </p>
                  </div>

                  <div className="space-y-2">
                    {quickResultLessons.map(({ lesson, packageTitle, isPackaged }) => (
                      <button
                        key={lesson.id}
                        type="button"
                        onClick={() => void handleOpenLesson(lesson.id)}
                        className="flex w-full items-center justify-between rounded-2xl bg-stone-50 px-4 py-3 text-left transition hover:bg-stone-100"
                      >
                        <div>
                          <p className="text-sm font-medium text-stone-950">{lesson.title}</p>
                          <p className="mt-1 text-xs text-stone-500">
                            {isPackaged ? `已存入 ${packageTitle} · ` : ""}
                            {lesson.summary}
                          </p>
                        </div>
                        <span className="text-xs text-stone-400">课程</span>
                      </button>
                    ))}

                    {quickResultResources.map((resource) => (
                      <Link
                        key={resource.id}
                        href="/studio"
                        className="flex items-center justify-between rounded-2xl bg-stone-50 px-4 py-3 transition hover:bg-stone-100"
                      >
                        <div>
                          <p className="text-sm font-medium text-stone-950">{resource.name}</p>
                          <p className="mt-1 text-xs text-stone-500">{resourceSummary(resource)}</p>
                        </div>
                        <span className="text-xs text-stone-400">资料</span>
                      </Link>
                    ))}

                    {filteredMarketplace.slice(0, 2).map((course) => (
                      <button
                        key={course.id}
                        type="button"
                        onClick={() => void handleGenerateFromMarketplace(course.topic, course.id)}
                        className="flex w-full items-center justify-between rounded-2xl bg-stone-50 px-4 py-3 text-left transition hover:bg-stone-100"
                      >
                        <div>
                          <p className="text-sm font-medium text-stone-950">{course.topic}</p>
                          <p className="mt-1 text-xs text-stone-500">{course.summary}</p>
                        </div>
                        <span className="text-xs text-stone-400">商城</span>
                      </button>
                    ))}

                    {!quickResultLessons.length && !quickResultResources.length && !filteredMarketplace.length ? (
                      <div className="rounded-2xl bg-stone-50 px-4 py-6 text-sm text-stone-500">
                        没有找到匹配内容，可以直接去工作台创建一节新课。
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </section>

            <section className="mb-12 rounded-[30px] border border-white/70 bg-white/80 p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] backdrop-blur sm:p-7">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="flex items-center gap-2 text-base font-semibold text-stone-950">
                    <Activity className="h-4 w-4" />
                    学习活跃度
                  </h3>
                  <p className="mt-1 text-sm text-stone-500">过去 32 周内课程编辑、提交与资料接入的活动分布。</p>
                </div>
                <span className="text-xs font-medium text-stone-500">
                  累计 {activity.total.toLocaleString("zh-CN")} 次活动
                </span>
              </div>

              <div className="mt-6 overflow-x-auto">
                <div className="flex min-w-max gap-[4px]">
                  {activity.weeks.map((week, index) => (
                    <div key={index} className="flex flex-col gap-[4px]">
                      {week.map((day) => (
                        <div
                          key={day.key}
                          className={clsx("h-3 w-3 rounded-[3px]", activityTone(day.level))}
                          title={`${day.key} · ${day.count} 次活动`}
                        />
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-5 flex flex-col gap-3 text-xs text-stone-400 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-1.5">
                  <span>Less</span>
                  <div className="h-3 w-3 rounded-[3px] bg-stone-200" />
                  <div className="h-3 w-3 rounded-[3px] bg-stone-300" />
                  <div className="h-3 w-3 rounded-[3px] bg-slate-400/70" />
                  <div className="h-3 w-3 rounded-[3px] bg-slate-700/70" />
                  <div className="h-3 w-3 rounded-[3px] bg-slate-950" />
                  <span>More</span>
                </div>
                <p>
                  最近一次活跃：
                  <span className="ml-1 text-stone-500">
                    {activity.recentActiveDay ? formatRelativeTime(activity.recentActiveDay.date) : "暂无记录"}
                  </span>
                </p>
              </div>
            </section>

            <section className="mb-12">
              <div className="rounded-[30px] border border-white/70 bg-[linear-gradient(180deg,#ffffff_0%,#faf8f2_100%)] p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] sm:p-7">
                <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="flex items-center gap-2 text-lg font-semibold text-stone-950">
                        <Activity className="h-5 w-5" />
                        Feed
                      </h3>
                      <button
                        type="button"
                        onClick={() => setFeedCollapsed((current) => !current)}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-stone-300 hover:text-stone-950"
                        aria-label={feedCollapsed ? "展开 Feed" : "收起 Feed"}
                        aria-expanded={!feedCollapsed}
                        aria-controls="learning-home-feed-content"
                        title={feedCollapsed ? "展开 Feed" : "收起 Feed"}
                      >
                        <ChevronDown
                          className={clsx(
                            "h-4 w-4 transition-transform duration-200",
                            feedCollapsed ? "rotate-0" : "rotate-180"
                          )}
                        />
                      </button>
                    </div>
                    <p className="mt-1 text-sm text-stone-500">
                      最近的课程提交、资料收录和工作台推进会按时间排在这里。
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    {feedFilters.map((filter) => {
                      const isActive = feedFilter === filter.id;
                      return (
                        <button
                          key={filter.id}
                          type="button"
                          onClick={() => setFeedFilter(filter.id)}
                          disabled={feedCollapsed}
                          className={clsx(
                            "rounded-full border px-4 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-45",
                            isActive
                              ? "border-stone-950 bg-stone-950 text-white"
                              : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:text-stone-950"
                          )}
                        >
                          {filter.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div
                  id="learning-home-feed-content"
                  className={clsx(
                    "overflow-hidden transition-all duration-300 ease-out",
                    feedCollapsed ? "max-h-28" : "max-h-[240rem]"
                  )}
                >
                  {feedCollapsed ? (
                    <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-5 text-sm text-stone-500">
                      Feed 已收起，当前共 {visibleFeedItems.length} 条可见动态。
                      {visibleFeedItems[0] ? ` 最近一条是 ${visibleFeedItems[0].title}。` : ""}
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {visibleFeedItems.length ? (
                        visibleFeedItems.map((item) => {
                          const buttonBusy = item.lessonId ? busyKey === `lesson:${item.lessonId}` : false;

                          return (
                            <article
                              key={item.id}
                              className="rounded-[24px] border border-stone-200 bg-white p-4 shadow-[0_10px_30px_rgba(15,23,42,0.05)] sm:p-5"
                            >
                              <div className="flex items-start justify-between gap-4">
                                <div className="flex min-w-0 gap-3">
                                  <div
                                    className={clsx(
                                      "mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl text-white",
                                      item.kind === "commit" ? "bg-rose-500" : "bg-emerald-500"
                                    )}
                                  >
                                    {item.kind === "commit" ? (
                                      <BookText className="h-4 w-4" />
                                    ) : (
                                      <FolderClosed className="h-4 w-4" />
                                    )}
                                  </div>

                                  <div className="min-w-0">
                                    <p className="text-sm text-stone-600">
                                      <span className="font-semibold text-stone-950">{item.actor}</span> {item.action}
                                    </p>
                                    <p className="mt-1 text-xs text-stone-400">{formatRelativeTime(item.timestamp)}</p>
                                  </div>
                                </div>

                                <span className="rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-stone-400">
                                  {item.kind === "commit" ? "Commit" : "Resource"}
                                </span>
                              </div>

                              <h4 className="mt-5 text-2xl font-semibold tracking-tight text-stone-950 sm:text-[2rem]">
                                {item.title}
                              </h4>

                              <div className="mt-4 rounded-[22px] border border-stone-200 bg-stone-50/90 p-4">
                                <div className="border-b border-stone-200 pb-3">
                                  <p className="text-base font-semibold text-stone-950">{item.detailTitle}</p>
                                </div>
                                <p className="mt-3 text-sm leading-7 text-stone-600">{item.detailBody}</p>
                              </div>

                              <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                <div className="flex flex-wrap gap-2">
                                  {item.pills.map((pill) => (
                                    <span
                                      key={pill}
                                      className="rounded-full bg-stone-100 px-3 py-1 text-[11px] font-medium text-stone-500"
                                    >
                                      {pill}
                                    </span>
                                  ))}
                                </div>

                                {item.lessonId ? (
                                  <button
                                    type="button"
                                    onClick={() => void handleOpenLesson(item.lessonId!)}
                                    className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                                  >
                                    {buttonBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                                    <span>进入工作台</span>
                                    {!buttonBusy ? <ArrowUpRight className="h-4 w-4" /> : null}
                                  </button>
                                ) : (
                                  <Link
                                    href="/studio"
                                    className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                                  >
                                    查看资料入口
                                    <ArrowUpRight className="h-4 w-4" />
                                  </Link>
                                )}
                              </div>
                            </article>
                          );
                        })
                      ) : (
                        <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-8 text-sm text-stone-500">
                          还没有可以展示的更新。新建课程、编辑文稿或上传资料后，这里会自动变成最近活动流。
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </section>

          </div>
        </main>
      </div>

      {lessonMenuState && lessonMenuLesson ? (
        <div
          data-lesson-menu-root
          className="fixed z-[140]"
          style={{ top: lessonMenuState.top, left: lessonMenuState.left }}
        >
          <div className="w-48 rounded-[22px] border border-stone-200 bg-white p-2 shadow-[0_18px_40px_rgba(15,23,42,0.14)]">
            <button
              type="button"
              disabled
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-300"
              title="分享功能稍后提供"
            >
              <Share2 className="h-4 w-4" />
              分享
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                setLessonMoveMenuState((current) =>
                  current?.lessonId === lessonMenuLesson.id
                    ? null
                    : {
                        lessonId: lessonMenuLesson.id,
                        top: rect.top,
                        left: rect.right + 8,
                      }
                );
              }}
              disabled={!movablePackages.length || busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FolderClosed className="h-4 w-4" />
              <span className="flex-1">移动到课程包</span>
              <ChevronRight className="h-4 w-4 text-stone-400" />
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={() => void handleDeleteLesson(lessonMenuLesson)}
              disabled={busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-rose-600 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busyKey === `delete:${lessonMenuLesson.id}` ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              删除
            </button>
          </div>
        </div>
      ) : null}

      {lessonMoveMenuState && lessonMenuLesson ? (
        <div
          data-lesson-menu-root
          className="fixed z-[145]"
          style={{ top: lessonMoveMenuState.top, left: lessonMoveMenuState.left }}
        >
          <div className="w-44 rounded-[20px] border border-stone-200 bg-white p-2 shadow-[0_18px_40px_rgba(15,23,42,0.14)]">
            {movablePackages.length ? (
              movablePackages.map((packageItem) => (
                <button
                  key={packageItem.id}
                  type="button"
                  onClick={() => void handleMoveLesson(lessonMenuLesson, packageItem.id)}
                  disabled={busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
                  className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="truncate">{packageItem.title}</span>
                  {busyKey === `move:${lessonMenuLesson.id}` ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                </button>
              ))
            ) : (
              <p className="px-3 py-2 text-sm text-stone-400">暂无可移动课程包</p>
            )}
          </div>
        </div>
      ) : null}

      <div className="hidden xl:block">
        <div className="fixed right-8 top-6 z-40 w-80">{renderNotificationPanel()}</div>
      </div>
    </div>
  );

  function renderNotificationPanel() {
    return (
      <div className="flex flex-col items-end gap-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setNotificationOpen((current) => !current)}
            className="relative rounded-full border border-stone-200 bg-white p-3 text-stone-700 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)]"
            aria-label="切换消息面板"
          >
            <Bell className="h-5 w-5" />
            <span className="absolute right-2 top-2 h-2 w-2 rounded-full border border-white bg-rose-500" />
          </button>
          <button
            type="button"
            className="h-11 w-11 overflow-hidden rounded-full border-2 border-white bg-stone-200 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:scale-[1.03]"
            aria-label="用户头像"
          >
            <Image
              src="https://api.dicebear.com/9.x/glass/svg?seed=Blackboard-AI"
              alt="Avatar"
              className="h-full w-full object-cover"
              width={44}
              height={44}
              unoptimized
            />
          </button>
        </div>

        {notificationOpen ? (
          <div className="w-full rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur">
            <div className="mb-4 flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">消息推送</h4>
              <span className="rounded-full bg-stone-950 px-2 py-1 text-[10px] font-semibold text-white">NEW</span>
            </div>

            <div className="space-y-4">
              <div className="flex gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-orange-50 text-orange-500">
                  <Sparkles className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-stone-950">
                    {latestLesson ? `最近更新：${latestLesson.title}` : "创建你的第一节课程"}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-stone-500">
                    {latestLesson
                      ? latestLesson.summary
                      : "去工作台新建课程后，这里会给你展示最近的推进动态。"}
                  </p>
                  <p className="mt-1 text-[11px] text-stone-400">
                    {latestLesson ? formatRelativeTime(latestLesson.updated_at) : "随时"}
                  </p>
                </div>
              </div>

              <div className="flex gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-sky-50 text-sky-500">
                  <Trophy className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-stone-950">
                    {latestResource ? `资料已索引：${latestResource.name}` : "上传教材，激活资料库联动"}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-stone-500">
                    {latestResource
                      ? `已提取 ${latestResource.outline.length} 个章节入口，可在工作台中继续引用。`
                      : "图片、PDF 和文档上传后，会自动形成资料入口和引用上下文。"}
                  </p>
                  <p className="mt-1 text-[11px] text-stone-400">
                    {latestResource ? formatRelativeTime(latestResource.uploaded_at) : "现在就可以开始"}
                  </p>
                </div>
              </div>
            </div>

            <Link
              href="/studio"
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full border border-stone-200 bg-stone-50 px-4 py-3 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
            >
              打开课程工作台
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        ) : null}

        {coursePackage ? (
          <div className="w-full rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">
                  {isStandaloneSelected ? "当前课池" : "当前课程包"}
                </p>
                <h4 className="mt-2 truncate text-lg font-semibold text-stone-950">{coursePackage.title}</h4>
                <p className="mt-1 text-xs leading-5 text-stone-500">
                  {isStandaloneSelected
                    ? "这里展示当前未入包的单独课程列表。"
                    : lessons.length
                      ? `右侧已展开包内课程，当前共 ${lessons.length} 节。`
                      : "这个课程包还是空的，等待你继续添加课程。"}
                </p>
              </div>
              <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-semibold text-stone-600">
                {lessons.length} 课
              </span>
            </div>

            <div className="mt-4 space-y-2">
              {selectedPackageLessons.length ? (
                selectedPackageLessons.map((lesson) => {
                  const isPreviewActive = lesson.id === activeLesson?.id;
                  return (
                    <div
                      key={lesson.id}
                      className={clsx(
                        "rounded-2xl border px-3 py-3 transition",
                        isPreviewActive
                          ? "border-stone-950 bg-stone-950 text-white"
                          : "border-stone-200 bg-stone-50/90 text-stone-800"
                      )}
                    >
                      <div className="flex items-start gap-3">
                        <div
                          className={clsx(
                            "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                            isPreviewActive
                              ? "border-white/10 bg-white/10 text-white"
                              : "border-stone-200 bg-white text-stone-500"
                          )}
                        >
                          <BookText className="h-4 w-4" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <p className="truncate text-sm font-semibold">{lesson.title}</p>
                            <span
                              className={clsx(
                                "shrink-0 text-[10px]",
                                isPreviewActive ? "text-white/70" : "text-stone-400"
                              )}
                            >
                              {formatRelativeTime(lesson.updated_at)}
                            </span>
                          </div>
                          <p
                            className={clsx(
                              "mt-1 line-clamp-2 text-xs leading-5",
                              isPreviewActive ? "text-white/75" : "text-stone-500"
                            )}
                          >
                            {lesson.summary || "已创建课程文档，等待继续补充内容。"}
                          </p>
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50/80 px-4 py-5 text-sm text-stone-500">
                  {isStandaloneSelected
                    ? "当前还没有未入包课程。去工作台新建后，这里会立即出现。"
                    : "这个课程包还是空的，先把课程移动进来，或者进入工作台新建一页。"}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>
    );
  }
}
