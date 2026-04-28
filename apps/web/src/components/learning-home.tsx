"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Bell,
  BookOpen,
  BookText,
  Bookmark,
  ChevronDown,
  ChevronRight,
  Code2,
  Eye,
  Flame,
  FolderClosed,
  FolderPlus,
  GitFork,
  GraduationCap,
  Layers,
  LoaderCircle,
  MoreHorizontal,
  PencilLine,
  Search,
  Share2,
  Star,
  Trash2,
} from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { InlineNameForm } from "@/components/inline-name-form";
import { api } from "@/lib/api";
import {
  DEFAULT_COLLECTED_COURSE_IDS,
  OPEN_COURSE_COLLECTION_STORAGE_KEY,
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
  searchOpenCourses,
  sortOpenCourses,
  type OpenCourse,
  type OpenCourseSort,
} from "@/lib/open-courses";
import {
  FOLLOWED_UPDATE_KIND_LABELS,
  buildFollowedCourseUpdateItems,
} from "@/lib/following";
import {
  buildRecentFeed,
  type RecentFeedFilter,
} from "@/lib/recent-feed";
import type { CoursePackage, Lesson, WorkspaceState } from "@/types";

const CONTRIBUTION_WEEKS = 32;

type ActivityDay = {
  key: string;
  date: Date;
  count: number;
  level: 0 | 1 | 2 | 3 | 4;
};

type SearchFacet = { kind: "all" } | { kind: "category" | "language"; value: string };

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

function countOpenCourseFacet(courses: OpenCourse[], getValue: (course: OpenCourse) => string) {
  const counts = new Map<string, number>();

  courses.forEach((course) => {
    const value = getValue(course);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  });

  return Array.from(counts, ([value, count]) => ({ value, count })).sort((left, right) => {
    if (right.count !== left.count) {
      return right.count - left.count;
    }
    return left.value.localeCompare(right.value, "zh-CN");
  });
}

function persistCollectedCourseIds(courseIds: Set<string>) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(OPEN_COURSE_COLLECTION_STORAGE_KEY, JSON.stringify(Array.from(courseIds)));
  } catch {
    // Local storage can be unavailable in private browsing contexts.
  }
}

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

function activityTone(level: ActivityDay["level"]) {
  return {
    0: "bg-stone-200",
    1: "bg-stone-300",
    2: "bg-slate-400/70",
    3: "bg-slate-700/70",
    4: "bg-slate-950",
  }[level];
}

export function LearningHome() {
  const router = useRouter();
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const deferredQuery = useDeferredValue(searchQuery.trim().toLowerCase());
  const [openCourseSort, setOpenCourseSort] = useState<OpenCourseSort>("best-match");
  const [openCourseFacet, setOpenCourseFacet] = useState<SearchFacet>({ kind: "all" });
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );
  const [feedFilter, setFeedFilter] = useState<RecentFeedFilter>("all");
  const [feedCollapsed, setFeedCollapsed] = useState(true);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<string | null>(null);
  const [packageLessonsExpanded, setPackageLessonsExpanded] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [lessonMenuState, setLessonMenuState] = useState<LessonMenuState | null>(null);
  const [lessonMoveMenuState, setLessonMoveMenuState] = useState<LessonMenuState | null>(null);
  const [isCreatingPackageInline, setIsCreatingPackageInline] = useState(false);

  useEffect(() => {
    let isDisposed = false;

    async function load() {
      try {
        const payload = await api.getWorkspace();
        if (isDisposed) {
          return;
        }
        setWorkspaceState(payload);
        if (typeof window !== "undefined") {
          const packageIdFromUrl = new URLSearchParams(window.location.search).get("package");
          const standalonePackageId = payload.packages.find((packageItem) => packageItem.is_standalone)?.id ?? payload.packages[0]?.id;
          if (
            packageIdFromUrl &&
            packageIdFromUrl !== standalonePackageId &&
            payload.packages.some((packageItem) => packageItem.id === packageIdFromUrl)
          ) {
            setSelectedPackageId(packageIdFromUrl);
            setPackageLessonsExpanded(true);
          }
        }
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
    if (typeof window === "undefined") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      try {
        const stored = window.localStorage.getItem(OPEN_COURSE_COLLECTION_STORAGE_KEY);
        if (!stored) {
          return;
        }
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
          setCollectedCourseIds(new Set(parsed));
        }
      } catch {
        setCollectedCourseIds(new Set(DEFAULT_COLLECTED_COURSE_IDS));
      }
    }, 0);

    return () => window.clearTimeout(timeoutId);
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
      if (!target.closest("[data-package-selection-root]")) {
        setSelectedPackageId(null);
        setPackageLessonsExpanded(false);
      }
      if (!target.closest("[data-lesson-selection-root]")) {
        setSelectedLessonId(null);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") {
        return;
      }
      setLessonMenuState(null);
      setLessonMoveMenuState(null);
      setIsCreatingPackageInline(false);
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const packages = workspaceState?.packages ?? [];
  const workspaceActivePackageId = workspaceState?.active_package_id ?? packages[0]?.id ?? null;
  const standalonePackage = packages.find((packageItem) => packageItem.is_standalone) ?? packages[0] ?? null;
  const coursePackages = packages.filter((packageItem) => packageItem.id !== standalonePackage?.id);
  const selectedCoursePackage = selectedPackageId
    ? coursePackages.find((item) => item.id === selectedPackageId) ?? null
    : null;
  const coursePackage =
    selectedCoursePackage ?? coursePackages.find((item) => item.id === workspaceActivePackageId) ?? coursePackages[0] ?? null;
  const movablePackages = coursePackages;
  const feedLessons = packages.flatMap((packageItem) =>
    packageItem.lessons.map((lesson) => ({
      lesson,
      packageId: packageItem.id,
      packageTitle: packageItem.title,
      isStandalone: packageItem.id === standalonePackage?.id,
    }))
  );
  const feedResources = packages.flatMap((packageItem) =>
    packageItem.resources.map((resource) => ({
      resource,
      packageTitle: packageItem.title,
    }))
  );
  const selectedPackageLessons = sortByUpdatedAt(selectedCoursePackage?.lessons ?? []);
  const selectedPackageActiveLesson = selectedLessonId
    ? selectedCoursePackage?.lessons.find((lesson) => lesson.id === selectedLessonId) ?? null
    : null;
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

  const matchingOpenCourses = useMemo(() => searchOpenCourses(deferredQuery), [deferredQuery]);
  const categoryFacetCounts = useMemo(
    () => countOpenCourseFacet(matchingOpenCourses, (course) => course.category),
    [matchingOpenCourses]
  );
  const languageFacetCounts = useMemo(
    () => countOpenCourseFacet(matchingOpenCourses, (course) => course.language),
    [matchingOpenCourses]
  );
  const openCourseResults = useMemo(() => {
    const facetedCourses = matchingOpenCourses.filter((course) => {
      if (openCourseFacet.kind === "all") {
        return true;
      }
      if (openCourseFacet.kind === "category") {
        return course.category === openCourseFacet.value;
      }
      return course.language === openCourseFacet.value;
    });

    return sortOpenCourses(facetedCourses, openCourseSort);
  }, [matchingOpenCourses, openCourseFacet, openCourseSort]);
  const collectedOpenCourseCount = collectedCourseIds.size;

  const activity = buildActivitySummary(coursePackage);
  const lessonMenuLesson =
    lessonMenuState ? standaloneLessonItems.find(({ lesson }) => lesson.id === lessonMenuState.lessonId)?.lesson ?? null : null;
  const feedItems = buildRecentFeed(feedLessons, feedResources);
  const visibleFeedItems = feedFilter === "all" ? feedItems : feedItems.filter((item) => item.kind === feedFilter);
  const followedProjectUpdates = useMemo(() => buildFollowedCourseUpdateItems(), []);
  const followingUnreadCount = followedProjectUpdates.length;
  const followingBadge = followingUnreadCount > 99 ? "99+" : followingUnreadCount.toString();
  const notificationUpdates = followedProjectUpdates.slice(0, 4);

  async function handleOpenLesson(lessonId: string) {
    setSelectedLessonId(lessonId);
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

  async function handleOpenStandaloneWorkspace() {
    if (!standalonePackage) {
      router.push("/studio");
      return;
    }

    setBusyKey(`package:${standalonePackage.id}`);
    setSelectedPackageId(null);
    setSelectedLessonId(null);
    setPackageLessonsExpanded(false);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.openPackage(standalonePackage.id);
      setWorkspaceState(payload);
      setError(null);
      router.push("/studio");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "打开单独课程工作台失败");
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

  async function handleCreatePackage(title: string) {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      return;
    }

    setBusyKey("package:create");
    try {
      const payload = await api.createPackage(trimmedTitle);
      setWorkspaceState(payload);
      setSelectedPackageId(payload.active_package_id ?? null);
      setPackageLessonsExpanded(true);
      setIsCreatingPackageInline(false);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "新建课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleOpenPackage(packageItem: CoursePackage) {
    if (packageItem.id === selectedPackageId) {
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
      return;
    }

    setSelectedPackageId(packageItem.id);
    setSelectedLessonId(null);
    setPackageLessonsExpanded(true);
    setBusyKey(`package:${packageItem.id}`);

    try {
      const payload = await api.openPackage(packageItem.id);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "打开课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRenameSelectedPackage() {
    if (!selectedCoursePackage) {
      return;
    }

    const nextTitle = window.prompt("请输入新的课程包名称", selectedCoursePackage.title);
    if (!nextTitle?.trim() || nextTitle.trim() === selectedCoursePackage.title) {
      return;
    }

    setBusyKey(`package:rename:${selectedCoursePackage.id}`);
    try {
      const payload = await api.renamePackage(selectedCoursePackage.id, nextTitle.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "重命名课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDeleteSelectedPackage() {
    if (!selectedCoursePackage) {
      return;
    }

    const lessonCount = selectedCoursePackage.lessons.length;
    const message = lessonCount
      ? `确定删除《${selectedCoursePackage.title}》吗？包内 ${lessonCount} 节单课也会一起删除。`
      : `确定删除《${selectedCoursePackage.title}》吗？`;
    if (typeof window !== "undefined" && !window.confirm(message)) {
      return;
    }

    setBusyKey(`package:delete:${selectedCoursePackage.id}`);
    try {
      const payload = await api.deletePackage(selectedCoursePackage.id);
      setWorkspaceState(payload);
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "删除课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleShareSelectedPackage() {
    if (!selectedCoursePackage || typeof window === "undefined") {
      return;
    }

    const shareUrl = new URL(window.location.href);
    shareUrl.searchParams.set("package", selectedCoursePackage.id);
    const shareData = {
      title: selectedCoursePackage.title,
      text: `分享课程包：${selectedCoursePackage.title}`,
      url: shareUrl.toString(),
    };

    try {
      if (typeof navigator.share === "function") {
        await navigator.share(shareData);
        return;
      }
      window.prompt("复制课程包链接", shareData.url);
    } catch (shareError) {
      if (shareError instanceof DOMException && shareError.name === "AbortError") {
        return;
      }
      setError(shareError instanceof Error ? shareError.message : "分享课程包失败");
    }
  }

  function handleToggleCollectCourse(courseId: string) {
    setCollectedCourseIds((current) => {
      const next = new Set(current);
      if (next.has(courseId)) {
        next.delete(courseId);
      } else {
        next.add(courseId);
      }
      persistCollectedCourseIds(next);
      return next;
    });
  }

  const feedFilters = [
    { id: "all" as const, label: "全部" },
    { id: "commit" as const, label: "我的" },
    { id: "resource" as const, label: "热门" },
  ];

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f7f5ef] text-[#171717]">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute left-[-7rem] top-16 h-64 w-64 rounded-full bg-sky-200/45 blur-3xl" />
        <div className="absolute right-[-5rem] top-0 h-80 w-80 rounded-full bg-orange-200/40 blur-3xl" />
        <div className="absolute bottom-[-8rem] right-32 h-72 w-72 rounded-full bg-emerald-200/30 blur-3xl" />
      </div>

      <div className="relative flex min-h-screen w-full flex-col lg:flex-row">
        <aside className="relative z-[90] h-[100dvh] border-b border-stone-200/80 bg-[#fcfbf8]/85 backdrop-blur lg:fixed lg:left-0 lg:top-0 lg:h-screen lg:w-80 lg:border-b-0 lg:border-r">
          <div className="flex h-full min-h-0 flex-col p-4 sm:p-5">
            <div className="mb-8 flex items-center justify-between gap-4 px-2">
              <div className="flex min-w-0 items-center gap-3">
                <BrandMark
                  alt=""
                  className="h-11 w-11 rounded-xl border border-stone-200 bg-white shadow-sm"
                  priority
                  size={88}
                />
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">AI 课程工作台</p>
                  <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-stone-950">开放课堂</h1>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <AccountMenu compact />
              </div>
            </div>

            <div className="mb-6 shrink-0">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">课程包</h2>
                <button
                  type="button"
                  onClick={() => setIsCreatingPackageInline(true)}
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
                {coursePackages.length ? (
                  coursePackages.map((packageItem) => {
                    const isActive = packageItem.id === selectedPackageId;
                    const isBusy = busyKey === `package:${packageItem.id}`;
                    return (
                      <div key={packageItem.id} className="relative" data-package-selection-root>
                        <button
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
                                <span className="flex shrink-0 items-center gap-1">
                                  <span
                                    className={clsx(
                                      "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                                      isActive ? "bg-white/10 text-white" : "bg-stone-100 text-stone-500"
                                    )}
                                  >
                                    {packageItem.lessons.length}
                                  </span>
                                  {isActive ? (
                                    <ChevronDown
                                      className={clsx(
                                        "h-3.5 w-3.5 transition-transform",
                                        packageLessonsExpanded ? "rotate-180" : "rotate-0"
                                      )}
                                    />
                                  ) : null}
                                </span>
                              </div>
                              <p
                                className={clsx(
                                  "mt-1 line-clamp-2 text-xs leading-5",
                                  isActive ? "text-white/75" : "text-stone-500"
                                )}
                              >
                                {isActive
                                  ? packageLessonsExpanded
                                    ? "已选中，右侧正在展示包内单课；再点可取消选中。"
                                    : "已选中，再点可展开包内单课列表。"
                                  : packageItem.summary || "空课程包，点一下先选中它。"}
                              </p>
                            </div>
                          </div>
                        </button>

                        {isActive && packageLessonsExpanded && selectedCoursePackage ? (
                          <>
                            <div className="mt-3 lg:hidden">{renderSelectedPackagePanel()}</div>
                            <div className="absolute left-[calc(100%+0.875rem)] top-0 z-[120] hidden w-80 lg:block">
                              {renderSelectedPackagePanel()}
                            </div>
                          </>
                        ) : null}
                      </div>
                    );
                  })
                ) : isCreatingPackageInline ? null : (
                  <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-4 py-6 text-sm text-stone-500">
                    还没有课程包，先点右上角的加号创建一个空课程包。
                  </div>
                )}
                {isCreatingPackageInline ? (
                  <div data-package-selection-root>
                    <InlineNameForm
                      label="课程包名称"
                      placeholder="输入课程包名称"
                      isBusy={busyKey === "package:create"}
                      onCancel={() => setIsCreatingPackageInline(false)}
                      onSubmit={handleCreatePackage}
                    />
                  </div>
                ) : null}
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">单独课程</h2>
                <div className="flex items-center gap-2">
                  <span className="rounded-full border border-stone-200 bg-white px-3 py-1 text-[10px] font-medium text-stone-500">
                    默认仅显示未入包课程
                  </span>
                  <button
                    type="button"
                    onClick={() => void handleOpenStandaloneWorkspace()}
                    disabled={standalonePackage ? busyKey === `package:${standalonePackage.id}` : false}
                    className="rounded-xl p-1.5 text-stone-400 transition hover:bg-stone-200/60 hover:text-stone-950"
                    aria-label="进入单独课程工作台"
                  >
                    {standalonePackage && busyKey === `package:${standalonePackage.id}` ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <BookOpen className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              <div className="custom-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto pb-4 pr-1">
                {isLoading ? (
                  Array.from({ length: 4 }).map((_, index) => (
                    <div key={index} className="rounded-2xl border border-stone-200 bg-white px-4 py-4">
                      <div className="h-4 w-2/3 animate-pulse rounded bg-stone-200" />
                      <div className="mt-3 h-3 w-full animate-pulse rounded bg-stone-100" />
                    </div>
                  ))
                ) : filteredLessonItems.length ? (
                  filteredLessonItems.map(({ lesson }) => {
                    const isActive = lesson.id === selectedLessonId;
                    const buttonBusy = busyKey === `lesson:${lesson.id}`;
                    const isMenuOpen = lessonMenuState?.lessonId === lesson.id;
                    return (
                      <article
                        key={lesson.id}
                        data-lesson-selection-root
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

          </div>
        </aside>

        <main className="relative flex-1 px-4 py-5 sm:px-6 lg:ml-80 lg:px-8 xl:pr-[25rem]">
          <div className="mx-auto max-w-4xl">
            {error ? (
              <div className="mb-6 rounded-[24px] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
                {error}
              </div>
            ) : null}

            <section className="mb-12">
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4 text-stone-400">
                  <Search className="h-4 w-4" />
                </div>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="搜索别人的开源课程、作者、主题或知识方向..."
                  className="w-full rounded-[28px] border border-white/70 bg-white/80 py-4 pl-11 pr-24 text-sm text-stone-950 shadow-[0_18px_40px_rgba(15,23,42,0.06)] outline-none transition placeholder:text-stone-400 focus:border-stone-950 focus:bg-white"
                />
                <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
                  <span className="rounded-md border border-stone-200 bg-white px-2 py-1 font-mono text-[10px] text-stone-400">
                    ⌘K
                  </span>
                </div>
              </div>
            </section>

            {deferredQuery ? (
              renderOpenCourseSearchResults()
            ) : (
              <>
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

                  {feedCollapsed ? null : (
                    <div className="flex flex-wrap items-center gap-2">
                      {feedFilters.map((filter) => {
                        const isActive = feedFilter === filter.id;
                        return (
                          <button
                            key={filter.id}
                            type="button"
                            onClick={() => setFeedFilter(filter.id)}
                            className={clsx(
                              "rounded-full border px-4 py-2 text-xs font-semibold transition",
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
                  )}
                </div>

                <div
                  id="learning-home-feed-content"
                  className={clsx(
                    "overflow-hidden transition-all duration-300 ease-out",
                    feedCollapsed ? "max-h-0" : "max-h-[240rem]"
                  )}
                >
                  {!feedCollapsed ? (
                    <div className="space-y-4">
                      {visibleFeedItems.length ? (
                        visibleFeedItems.map((item) => {
                          const buttonBusy = item.lessonId ? busyKey === `lesson:${item.lessonId}` : false;
                          const hasCommitTimeline = item.kind === "commit" && Boolean(item.updates?.length);

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

                              <h4
                                className={clsx(
                                  "mt-5 font-semibold text-stone-950",
                                  hasCommitTimeline ? "text-xl sm:text-2xl" : "text-2xl sm:text-[2rem]"
                                )}
                              >
                                {item.title}
                              </h4>

                              {hasCommitTimeline ? (
                                <ol className="mt-5">
                                  {item.updates?.map((update, updateIndex) => {
                                    const isLast = updateIndex === (item.updates?.length ?? 0) - 1;

                                    return (
                                      <li key={update.id} className="relative flex gap-3 pb-5 last:pb-0">
                                        {!isLast ? (
                                          <span className="absolute left-[5px] top-4 h-full w-px bg-stone-200" />
                                        ) : null}
                                        <span className="relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-rose-500 ring-4 ring-rose-50" />
                                        <div className="min-w-0 flex-1">
                                          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                                            {update.lessonTitle ? (
                                              <p className="text-sm font-semibold text-stone-950">
                                                {update.lessonTitle}
                                              </p>
                                            ) : null}
                                            <span className="text-xs text-stone-400">
                                              {formatRelativeTime(update.timestamp)}
                                            </span>
                                          </div>
                                          <div className="mt-1 flex flex-wrap items-center gap-2">
                                            <p className="text-sm font-medium text-stone-800">{update.title}</p>
                                            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] font-semibold text-stone-500">
                                              {update.detailTitle}
                                            </span>
                                          </div>
                                          <p className="mt-1 text-sm leading-6 text-stone-600">{update.detailBody}</p>
                                        </div>
                                      </li>
                                    );
                                  })}
                                </ol>
                              ) : (
                                <div className="mt-4 rounded-[22px] border border-stone-200 bg-stone-50/90 p-4">
                                  <div className="border-b border-stone-200 pb-3">
                                    <p className="text-base font-semibold text-stone-950">{item.detailTitle}</p>
                                  </div>
                                  <p className="mt-3 text-sm leading-7 text-stone-600">{item.detailBody}</p>
                                </div>
                              )}

                              <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                <div className="flex flex-wrap gap-2">
                                  {item.pills.map((pill, pillIndex) => (
                                    <span
                                      key={`${item.id}:pill:${pillIndex}:${pill}`}
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
                  ) : null}
                </div>
              </div>
            </section>
              </>
            )}

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
        <div className="fixed right-8 top-6 z-40 w-[27rem]">{renderNotificationPanel()}</div>
      </div>
    </div>
  );

  function renderOpenCourseSearchResults() {
    const searchText = searchQuery.trim();
    const activeFacetLabel = openCourseFacet.kind === "all" ? "全部开源课程" : openCourseFacet.value;
    const totalStars = openCourseResults.reduce((sum, course) => sum + course.stars, 0);

    return (
      <section className="mb-12">
        <div className="grid gap-5 lg:grid-cols-[15rem_minmax(0,1fr)] 2xl:grid-cols-[15rem_minmax(0,1fr)_18rem]">
          <aside className="h-fit rounded-lg border border-stone-200 bg-white/88 p-3 shadow-[0_12px_28px_rgba(15,23,42,0.04)] backdrop-blur">
            <div className="mb-3 flex items-center gap-2 px-2 text-sm font-semibold text-stone-950">
              <Code2 className="h-4 w-4" />
              <span>Filter by</span>
            </div>

            <button
              type="button"
              onClick={() => setOpenCourseFacet({ kind: "all" })}
              className={clsx(
                "flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm transition",
                openCourseFacet.kind === "all"
                  ? "bg-stone-950 text-white"
                  : "text-stone-700 hover:bg-stone-100 hover:text-stone-950"
              )}
            >
              <span className="inline-flex items-center gap-2">
                <BookOpen className="h-4 w-4" />
                Open courses
              </span>
              <span
                className={clsx(
                  "rounded-full px-2 py-0.5 text-[10px]",
                  openCourseFacet.kind === "all" ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                )}
              >
                {matchingOpenCourses.length}
              </span>
            </button>

            <div className="mt-4 border-t border-stone-200 pt-4">
              <p className="px-2 text-xs font-semibold text-stone-500">课程方向</p>
              <div className="mt-2 space-y-1">
                {categoryFacetCounts.map((facet) => {
                  const isActive = openCourseFacet.kind === "category" && openCourseFacet.value === facet.value;
                  return (
                    <button
                      key={facet.value}
                      type="button"
                      onClick={() => setOpenCourseFacet({ kind: "category", value: facet.value })}
                      className={clsx(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition",
                        isActive ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50 hover:text-stone-950"
                      )}
                    >
                      <span className="inline-flex items-center gap-2">
                        <Layers className="h-3.5 w-3.5" />
                        {facet.value}
                      </span>
                      <span className="text-xs text-stone-400">{facet.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="mt-4 border-t border-stone-200 pt-4">
              <p className="px-2 text-xs font-semibold text-stone-500">语言 / 学科</p>
              <div className="mt-2 space-y-1">
                {languageFacetCounts.map((facet) => {
                  const isActive = openCourseFacet.kind === "language" && openCourseFacet.value === facet.value;
                  const sampleCourse = matchingOpenCourses.find((course) => course.language === facet.value);
                  return (
                    <button
                      key={facet.value}
                      type="button"
                      onClick={() => setOpenCourseFacet({ kind: "language", value: facet.value })}
                      className={clsx(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition",
                        isActive ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50 hover:text-stone-950"
                      )}
                    >
                      <span className="inline-flex min-w-0 items-center gap-2">
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: sampleCourse?.languageColor ?? "#94a3b8" }}
                        />
                        <span className="truncate">{facet.value}</span>
                      </span>
                      <span className="text-xs text-stone-400">{facet.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </aside>

          <div className="min-w-0">
            <div className="mb-4 flex flex-col gap-3 rounded-lg border border-stone-200 bg-white/88 px-4 py-3 shadow-[0_12px_28px_rgba(15,23,42,0.04)] backdrop-blur sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-stone-950">
                  {openCourseResults.length.toLocaleString("zh-CN")} 个开源课程结果
                </h2>
                <p className="mt-1 text-xs text-stone-500">
                  搜索 “{searchText}” · 当前筛选：{activeFacetLabel}
                </p>
              </div>

              <label className="inline-flex items-center gap-2 text-xs font-medium text-stone-500">
                Sort by
                <select
                  value={openCourseSort}
                  onChange={(event) => setOpenCourseSort(event.target.value as OpenCourseSort)}
                  className="rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-stone-700 outline-none transition focus:border-stone-950"
                >
                  <option value="best-match">Best match</option>
                  <option value="stars">Most stars</option>
                  <option value="updated">Recently updated</option>
                </select>
              </label>
            </div>

            <div className="space-y-3">
              {openCourseResults.length ? (
                openCourseResults.map((course) => {
                  const isCollected = collectedCourseIds.has(course.id);
                  return (
                    <article
                      key={course.id}
                      className="rounded-lg border border-stone-200 bg-white/92 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)] transition hover:border-stone-300 hover:bg-white"
                    >
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex min-w-0 gap-3">
                          <Image
                            src={courseAvatarUrl(course)}
                            alt=""
                            className="mt-0.5 h-8 w-8 rounded-md border border-stone-200 bg-stone-100"
                            width={32}
                            height={32}
                            unoptimized
                          />

                          <div className="min-w-0">
                            <Link
                              href={courseDetailHref(course)}
                              className="block truncate text-base font-semibold text-blue-600 hover:underline"
                            >
                              {courseFullName(course)}
                            </Link>
                            <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-700">{course.summary}</p>

                            <div className="mt-3 flex flex-wrap gap-1.5">
                              {course.topics.map((topic) => (
                                <span
                                  key={`${course.id}:${topic}`}
                                  className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700"
                                >
                                  {topic}
                                </span>
                              ))}
                            </div>

                            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
                              <span className="inline-flex items-center gap-1.5">
                                <span
                                  className="h-2.5 w-2.5 rounded-full"
                                  style={{ backgroundColor: course.languageColor }}
                                />
                                {course.language}
                              </span>
                              <span className="inline-flex items-center gap-1">
                                <Star className="h-3.5 w-3.5" />
                                {formatCompactNumber(course.stars)}
                              </span>
                              <span className="inline-flex items-center gap-1">
                                <GitFork className="h-3.5 w-3.5" />
                                {formatCompactNumber(course.forks)}
                              </span>
                              <span className="inline-flex items-center gap-1">
                                <GraduationCap className="h-3.5 w-3.5" />
                                {course.lessons} lessons
                              </span>
                              <span>{course.license}</span>
                              <span>Updated {formatRelativeTime(course.updatedAt)}</span>
                            </div>
                          </div>
                        </div>

                        <div className="flex shrink-0 items-center gap-2">
                          <Link
                            href={courseDetailHref(course)}
                            className="inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
                          >
                            打开
                            <ArrowUpRight className="h-3.5 w-3.5" />
                          </Link>
                          <button
                            type="button"
                            onClick={() => handleToggleCollectCourse(course.id)}
                            className={clsx(
                              "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold transition",
                              isCollected
                                ? "border-amber-200 bg-amber-50 text-amber-700 hover:border-amber-300"
                                : "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:text-stone-950"
                            )}
                          >
                            <Star className={clsx("h-3.5 w-3.5", isCollected && "fill-current")} />
                            {isCollected ? "已收藏" : "收藏"}
                          </button>
                        </div>
                      </div>
                    </article>
                  );
                })
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 bg-white/88 px-5 py-10 text-sm text-stone-500">
                  没有找到匹配的开源课程。换个关键词，或清除左侧筛选后再试。
                </div>
              )}
            </div>
          </div>

          <aside className="hidden h-fit space-y-3 2xl:block">
            <div className="rounded-lg border border-stone-200 bg-white/88 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
                <Bookmark className="h-4 w-4 text-amber-500" />
                收藏的开源课程
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                已收藏 {collectedOpenCourseCount} 个项目，可在个人主页继续查看和管理。
              </p>
              <Link
                href="/profile"
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
              >
                打开个人主页
                <ArrowUpRight className="h-4 w-4" />
              </Link>
            </div>

            <div className="rounded-lg border border-stone-200 bg-white/88 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
                <Eye className="h-4 w-4 text-sky-600" />
                搜索概览
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-stone-400">Stars</dt>
                  <dd className="mt-1 font-semibold text-stone-900">{formatCompactNumber(totalStars)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-stone-400">Topics</dt>
                  <dd className="mt-1 font-semibold text-stone-900">
                    {new Set(openCourseResults.flatMap((course) => course.topics)).size}
                  </dd>
                </div>
              </dl>
            </div>
          </aside>
        </div>
      </section>
    );
  }

  function renderSelectedPackagePanel() {
    if (!selectedCoursePackage) {
      return null;
    }
    const isDeletingPackage = busyKey === `package:delete:${selectedCoursePackage.id}`;
    const isRenamingPackage = busyKey === `package:rename:${selectedCoursePackage.id}`;
    const packageActionBusy = isDeletingPackage || isRenamingPackage;

    return (
      <div className="w-full rounded-[28px] border border-white/80 bg-white/95 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.1)] backdrop-blur">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">
              当前课程包
            </p>
            <h4 className="mt-2 truncate text-lg font-semibold text-stone-950">{selectedCoursePackage.title}</h4>
            <div className="mt-2 flex h-3.5 origin-left scale-[0.82] flex-nowrap items-center gap-0.5">
              <button
                type="button"
                onClick={() => void handleDeleteSelectedPackage()}
                disabled={packageActionBusy}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-rose-100 bg-rose-50 px-1 text-[8px] font-normal leading-none text-rose-600 transition hover:border-rose-200 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-45"
                title="删除课程包"
              >
                {isDeletingPackage ? <LoaderCircle className="h-2 w-2 animate-spin" /> : <Trash2 className="h-2 w-2" />}
                删除
              </button>
              <button
                type="button"
                onClick={() => void handleShareSelectedPackage()}
                disabled={packageActionBusy}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-stone-200 bg-white px-1 text-[8px] font-normal leading-none text-stone-600 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-45"
                title="分享课程包"
              >
                <Share2 className="h-2 w-2" />
                分享
              </button>
              <button
                type="button"
                onClick={() => void handleRenameSelectedPackage()}
                disabled={packageActionBusy}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-stone-200 bg-white px-1 text-[8px] font-normal leading-none text-stone-600 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-45"
                title="重命名课程包"
              >
                {isRenamingPackage ? <LoaderCircle className="h-2 w-2 animate-spin" /> : <PencilLine className="h-2 w-2" />}
                重命名
              </button>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-semibold text-stone-600">
              {selectedPackageLessons.length} 课
            </span>
            <button
              type="button"
              onClick={() => {
                setSelectedPackageId(null);
                setSelectedLessonId(null);
                setPackageLessonsExpanded(false);
              }}
              className="flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-stone-300 hover:text-stone-950"
              aria-label="收起单课列表"
              title="收起单课列表"
            >
              <ChevronDown className="h-4 w-4 rotate-180" />
            </button>
          </div>
        </div>

        <div className="mt-4">
          <div className="custom-scrollbar max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {selectedPackageLessons.length ? (
              selectedPackageLessons.map((lesson) => {
                const isPreviewActive = lesson.id === selectedPackageActiveLesson?.id;
                const buttonBusy = busyKey === `lesson:${lesson.id}`;
                return (
                  <button
                    key={lesson.id}
                    type="button"
                    data-lesson-selection-root
                    onClick={() => void handleOpenLesson(lesson.id)}
                    disabled={buttonBusy}
                    className={clsx(
                      "w-full rounded-2xl border px-3 py-3 text-left transition disabled:cursor-wait",
                      isPreviewActive
                        ? "border-stone-950 bg-stone-950 text-white"
                        : "border-stone-200 bg-stone-50/90 text-stone-800 hover:border-stone-300 hover:bg-white"
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
                        {buttonBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <BookText className="h-4 w-4" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-semibold">{lesson.title}</p>
                          <span className={clsx("shrink-0 text-[10px]", isPreviewActive ? "text-white/70" : "text-stone-400")}>
                            {formatRelativeTime(lesson.updated_at)}
                          </span>
                        </div>
                        <p className={clsx("mt-1 line-clamp-2 text-xs leading-5", isPreviewActive ? "text-white/75" : "text-stone-500")}>
                          {lesson.summary || "已创建课程文档，等待继续补充内容。"}
                        </p>
                      </div>
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50/80 px-4 py-5 text-sm text-stone-500">
                这个课程包还是空的，先把课程移动进来，或者进入工作台新建一页。
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  function renderNotificationPanel() {
    return (
      <div className="flex flex-col items-end gap-4">
        <div className="flex items-center gap-3">
          <Link
            href="/trending"
            className="group relative flex h-11 items-center gap-2 rounded-full border border-orange-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(249,115,22,0.10)] transition hover:-translate-y-0.5 hover:bg-orange-500 hover:text-white hover:shadow-[0_14px_28px_rgba(249,115,22,0.18)]"
            aria-label="打开热门项目"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-orange-50 text-orange-500 transition group-hover:bg-white group-hover:text-orange-500">
              <Flame className="h-4 w-4" />
            </span>
            <span>热门</span>
          </Link>
          <Link
            href="/profile?tab=stars"
            className="group relative flex h-11 items-center gap-2 rounded-full border border-amber-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(245,158,11,0.10)] transition hover:-translate-y-0.5 hover:bg-amber-500 hover:text-white hover:shadow-[0_14px_28px_rgba(245,158,11,0.18)]"
            aria-label="打开 Stars 收藏"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-amber-50 text-amber-500 transition group-hover:bg-white group-hover:text-amber-500">
              <Star className="h-4 w-4" />
              {collectedOpenCourseCount ? (
                <span className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                  {collectedOpenCourseCount > 99 ? "99+" : collectedOpenCourseCount}
                </span>
              ) : null}
            </span>
            <span>Star</span>
          </Link>
          <Link
            href="/following"
            className="group relative flex h-11 items-center gap-2 rounded-full border border-rose-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(244,63,94,0.10)] transition hover:-translate-y-0.5 hover:bg-rose-500 hover:text-white hover:shadow-[0_14px_28px_rgba(244,63,94,0.18)]"
            aria-label="打开关注动态"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-rose-50 text-rose-500 transition group-hover:bg-white group-hover:text-rose-500">
              <Activity className="h-4 w-4" />
              {followingUnreadCount ? (
                <span className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                  {followingBadge}
                </span>
              ) : null}
            </span>
            <span>动态</span>
          </Link>
          <button
            type="button"
            onClick={() => setNotificationOpen((current) => !current)}
            className="relative rounded-full border border-stone-200 bg-white p-3 text-stone-700 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)]"
            aria-label="切换消息面板"
          >
            <Bell className="h-5 w-5" />
            {followingUnreadCount ? (
              <span className="absolute -right-1 -top-1 min-w-5 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                {followingBadge}
              </span>
            ) : null}
          </button>
          <Link
            href="/profile"
            className="h-11 w-11 overflow-hidden rounded-full border-2 border-white bg-stone-200 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:scale-[1.03]"
            aria-label="用户头像"
          >
            <Image
              src="https://api.dicebear.com/9.x/glass/svg?seed=kai-fang-ke-tang"
              alt="开放课堂用户头像"
              className="h-full w-full object-cover"
              width={44}
              height={44}
              unoptimized
            />
          </Link>
        </div>

        {notificationOpen ? (
          <div className="w-full rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur">
            <div className="mb-4 flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">消息推送</h4>
              <span className="rounded-full bg-rose-500 px-2 py-1 text-[10px] font-semibold text-white">
                {followingUnreadCount ? `${followingBadge} NEW` : "已同步"}
              </span>
            </div>

            <div className="space-y-3">
              {notificationUpdates.length ? (
                notificationUpdates.map((item) => (
                  <Link
                    key={item.update.id}
                    href="/following"
                    className="group flex gap-3 rounded-2xl border border-transparent p-2 transition hover:border-stone-100 hover:bg-stone-50"
                  >
                    <div
                      className={clsx(
                        "flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl text-white",
                        item.update.updateKind === "resource_added" ? "bg-emerald-500" : "bg-rose-500"
                      )}
                    >
                      {item.update.updateKind === "resource_added" ? (
                        <FolderClosed className="h-4 w-4" />
                      ) : (
                        <BookText className="h-4 w-4" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold text-stone-950">{item.creator.name}</p>
                        <span className="shrink-0 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-semibold text-rose-600">
                          {FOLLOWED_UPDATE_KIND_LABELS[item.update.updateKind]}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-1 text-sm font-semibold text-stone-800 group-hover:text-stone-950">
                        {item.update.courseTitle}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-stone-500">{item.update.summary}</p>
                      <p className="mt-1 truncate text-[11px] text-stone-400">
                        {item.update.moduleTitle} · {formatRelativeTime(item.update.updatedAt)}
                      </p>
                    </div>
                  </Link>
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-stone-200 bg-stone-50 px-4 py-5 text-sm text-stone-500">
                  关注他人项目后，这里只显示这些项目的更新推送。
                </div>
              )}
            </div>

            <Link
              href="/following"
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full border border-stone-200 bg-stone-50 px-4 py-3 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
            >
              查看全部动态
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        ) : null}

      </div>
    );
  }
}
