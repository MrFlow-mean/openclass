"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Bookmark,
  ChevronDown,
  FolderClosed,
  GitFork,
  GraduationCap,
  KeyRound,
  LoaderCircle,
  Search,
  Settings,
  ShieldCheck,
  Star,
} from "lucide-react";

import { ProfileSettingsPanel } from "@/components/profile-settings-panel";
import { api } from "@/lib/api";
import {
  DEFAULT_COLLECTED_COURSE_IDS,
  OPEN_COURSE_COLLECTION_STORAGE_KEY,
  OPEN_SOURCE_COURSES,
  courseAvatarUrl,
  courseFullName,
  formatCompactNumber,
} from "@/lib/open-courses";
import type { CoursePackage, Lesson, WorkspaceState } from "@/types";

type ProfileTab = "repositories" | "stars" | "settings";
type RepositoryTypeFilter = "all" | "lessons" | "packages";

type ProfileHomeProps = {
  initialTab?: ProfileTab;
};

const PROFILE_AVATAR_URL = "https://api.dicebear.com/9.x/glass/svg?seed=Blackboard-AI";

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

function getPackageUpdatedAt(coursePackage: CoursePackage) {
  const timestamps = [
    ...coursePackage.lessons.map((lesson) => lesson.updated_at),
    ...coursePackage.resources.map((resource) => resource.uploaded_at),
  ]
    .map((value) => new Date(value).getTime())
    .filter((value) => !Number.isNaN(value));

  if (!timestamps.length) {
    return null;
  }

  return new Date(Math.max(...timestamps));
}

function getPackageTopics(coursePackage: CoursePackage) {
  const topics = new Set<string>();
  coursePackage.lessons.forEach((lesson) => {
    lesson.tags.slice(0, 3).forEach((tag) => topics.add(tag));
  });
  if (!topics.size && coursePackage.lessons.length) {
    topics.add("course");
  }
  if (coursePackage.resources.length) {
    topics.add("resources");
  }
  return Array.from(topics).slice(0, 5);
}

function getLessonTopics(lesson: Lesson) {
  return lesson.tags.length ? lesson.tags.slice(0, 5) : ["lesson"];
}

function sortLessonsByUpdatedAt(lessons: Lesson[]) {
  return [...lessons].sort((left, right) => {
    const leftTime = new Date(left.updated_at).getTime();
    const rightTime = new Date(right.updated_at).getTime();
    return rightTime - leftTime;
  });
}

function matchesQuery(query: string, ...values: Array<string | null | undefined>) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return true;
  }

  return values.some((value) => value?.toLowerCase().includes(normalized));
}

function persistCollectedCourseIds(courseIds: Set<string>) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(OPEN_COURSE_COLLECTION_STORAGE_KEY, JSON.stringify(Array.from(courseIds)));
  } catch {
    // Local storage may be unavailable in private browsing contexts.
  }
}

export function ProfileHome({ initialTab = "settings" }: ProfileHomeProps) {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<ProfileTab>(initialTab);
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [repositoryQuery, setRepositoryQuery] = useState("");
  const [repositoryTypeFilter, setRepositoryTypeFilter] = useState<RepositoryTypeFilter>("all");
  const [expandedPackageIds, setExpandedPackageIds] = useState<Set<string>>(() => new Set());
  const [starQuery, setStarQuery] = useState("");
  const [openingLessonId, setOpeningLessonId] = useState<string | null>(null);
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );

  useEffect(() => {
    let isDisposed = false;

    async function loadWorkspace() {
      try {
        const payload = await api.getWorkspace();
        if (isDisposed) {
          return;
        }
        setWorkspaceState(payload);
        setError(null);
      } catch (loadError) {
        if (!isDisposed) {
          setError(loadError instanceof Error ? loadError.message : "加载个人项目失败");
        }
      } finally {
        if (!isDisposed) {
          setIsLoading(false);
        }
      }
    }

    void loadWorkspace();

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

  const packages = useMemo(() => workspaceState?.packages ?? [], [workspaceState]);
  const standalonePackage = packages[0] ?? null;
  const coursePackageProjects = useMemo(
    () =>
      packages.filter((coursePackage) => coursePackage.id !== standalonePackage?.id).sort((left, right) => {
        const leftTime = getPackageUpdatedAt(left)?.getTime() ?? 0;
        const rightTime = getPackageUpdatedAt(right)?.getTime() ?? 0;
        return rightTime - leftTime;
      }),
    [packages, standalonePackage?.id]
  );
  const standaloneLessonProjects = useMemo(
    () => sortLessonsByUpdatedAt(standalonePackage?.lessons ?? []),
    [standalonePackage]
  );
  const favoriteProjects = useMemo(
    () => OPEN_SOURCE_COURSES.filter((course) => collectedCourseIds.has(course.id)),
    [collectedCourseIds]
  );
  const repositoryItems = useMemo(
    () =>
      [
        ...standaloneLessonProjects.map((lesson) => ({
          id: `lesson:${lesson.id}`,
          kind: "lesson" as const,
          updatedAt: lesson.updated_at,
          lesson,
        })),
        ...coursePackageProjects.map((coursePackage) => ({
          id: `package:${coursePackage.id}`,
          kind: "package" as const,
          updatedAt: getPackageUpdatedAt(coursePackage)?.toISOString() ?? "",
          coursePackage,
        })),
      ].sort((left, right) => {
        const leftTime = new Date(left.updatedAt).getTime();
        const rightTime = new Date(right.updatedAt).getTime();
        return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
      }),
    [coursePackageProjects, standaloneLessonProjects]
  );
  const filteredRepositoryItems = repositoryItems.filter((item) => {
    const matchesType =
      repositoryTypeFilter === "all" ||
      (repositoryTypeFilter === "lessons" && item.kind === "lesson") ||
      (repositoryTypeFilter === "packages" && item.kind === "package");

    if (!matchesType) {
      return false;
    }

    if (item.kind === "lesson") {
      return matchesQuery(
        repositoryQuery,
        item.lesson.title,
        item.lesson.summary,
        item.lesson.board_document.title,
        item.lesson.board_document.content_text,
        getLessonTopics(item.lesson).join(" "),
        standalonePackage?.title
      );
    }

    return matchesQuery(
      repositoryQuery,
      item.coursePackage.title,
      item.coursePackage.summary,
      getPackageTopics(item.coursePackage).join(" "),
      item.coursePackage.lessons.map((lesson) => lesson.title).join(" ")
    );
  });
  const repositoryTypeFilters = [
    { id: "all" as const, label: "全部", count: repositoryItems.length },
    { id: "lessons" as const, label: "单独课程", count: standaloneLessonProjects.length },
    { id: "packages" as const, label: "课程包", count: coursePackageProjects.length },
  ];
  const repositoryCount = repositoryItems.length;
  const filteredFavoriteProjects = favoriteProjects.filter((course) =>
    matchesQuery(starQuery, courseFullName(course), course.summary, course.topics.join(" "), course.language)
  );

  const profileTabs = [
    { id: "settings" as const, label: "个人设置", icon: Settings, count: null },
    { id: "repositories" as const, label: "Repositories", icon: FolderClosed, count: repositoryCount },
    { id: "stars" as const, label: "Stars", icon: Star, count: favoriteProjects.length },
  ];

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

  async function handleOpenLesson(lessonId: string) {
    setOpeningLessonId(lessonId);

    try {
      await api.openLesson(lessonId);
      router.push("/studio");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "打开课程失败");
    } finally {
      setOpeningLessonId(null);
    }
  }

  function handleTogglePackageLessons(packageId: string) {
    setExpandedPackageIds((current) => {
      const next = new Set(current);
      if (next.has(packageId)) {
        next.delete(packageId);
      } else {
        next.add(packageId);
      }
      return next;
    });
  }

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            Learning Hub
          </Link>

          <div className="flex items-center gap-2">
            <Link
              href="/studio"
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <BookOpen className="h-4 w-4" />
              工作台
            </Link>
            <Link
              href="/login"
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <KeyRound className="h-4 w-4" />
              登录
            </Link>
            <Link
              href="/admin"
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <ShieldCheck className="h-4 w-4" />
              后台
            </Link>
            <div className="h-10 w-10 overflow-hidden rounded-full border-2 border-white bg-stone-200 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
              <Image
                src={PROFILE_AVATAR_URL}
                alt="用户头像"
                className="h-full w-full object-cover"
                width={40}
                height={40}
                unoptimized
              />
            </div>
          </div>
        </div>

        <nav className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 sm:px-6" aria-label="个人主页内容导航">
          {profileTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={clsx(
                  "inline-flex min-h-11 items-center gap-2 border-b-2 px-3 text-sm font-semibold transition",
                  isActive
                    ? "border-orange-500 text-stone-950"
                    : "border-transparent text-stone-600 hover:border-stone-300 hover:text-stone-950"
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
                {tab.count !== null ? (
                  <span className="rounded-full bg-stone-200 px-2 py-0.5 text-[11px] font-semibold text-stone-700">
                    {tab.count}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>
      </header>

      {activeTab === "settings" ? (
        <ProfileSettingsPanel
          avatarUrl={PROFILE_AVATAR_URL}
          favoriteCount={favoriteProjects.length}
          repositoryCount={repositoryCount}
        />
      ) : (
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-8 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="h-fit">
          <div className="flex items-start gap-4 lg:block">
            <Image
              src={PROFILE_AVATAR_URL}
              alt="Blackboard AI 用户头像"
              className="h-24 w-24 rounded-full border-4 border-white bg-stone-200 shadow-[0_16px_34px_rgba(15,23,42,0.08)] lg:h-48 lg:w-48"
              width={192}
              height={192}
              unoptimized
            />
            <div className="min-w-0 flex-1 lg:mt-4">
              <h1 className="truncate text-2xl font-semibold tracking-tight text-stone-950">Flow-mean</h1>
              <p className="mt-1 text-sm text-stone-500">@blackboard-student</p>
              <button
                type="button"
                onClick={() => setActiveTab("settings")}
                className="mt-4 w-full rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                Edit profile
              </button>
              <p className="mt-4 text-sm leading-6 text-stone-600">
                管理自己的课程项目，Stars 只收纳你收藏的他人开源课程。
              </p>
              <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-sm text-stone-600">
                <span>{repositoryCount} repositories</span>
                <span>{favoriteProjects.length} stars</span>
              </div>
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          {activeTab === "repositories" ? renderRepositories() : null}
          {activeTab === "stars" ? renderStars() : null}
        </section>
      </div>
      )}
    </main>
  );

  function renderRepositories() {
    return (
      <div>
        <div className="mb-4 flex flex-col gap-3 border-b border-stone-200 pb-4 md:flex-row md:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
            <input
              type="text"
              value={repositoryQuery}
              onChange={(event) => setRepositoryQuery(event.target.value)}
              placeholder="Find a repository..."
              className="w-full rounded-md border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {repositoryTypeFilters.map((filter) => {
              const isActive = repositoryTypeFilter === filter.id;
              return (
                <button
                  key={filter.id}
                  type="button"
                  onClick={() => setRepositoryTypeFilter(filter.id)}
                  className={clsx(
                    "inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition",
                    isActive
                      ? "border-stone-950 bg-stone-950 text-white"
                      : "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:text-stone-950"
                  )}
                >
                  {filter.label}
                  <span
                    className={clsx(
                      "rounded-full px-2 py-0.5 text-[11px]",
                      isActive ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                    )}
                  >
                    {filter.count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-0">
          {isLoading ? (
            Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="border-b border-stone-200 bg-white/60 p-5">
                <div className="h-4 w-1/3 animate-pulse rounded bg-stone-200" />
                <div className="mt-3 h-3 w-2/3 animate-pulse rounded bg-stone-100" />
              </div>
            ))
          ) : filteredRepositoryItems.length ? (
            filteredRepositoryItems.map((item) =>
              item.kind === "lesson" ? renderLessonCard(item.lesson) : renderRepositoryCard(item.coursePackage, "list")
            )
          ) : (
            <div className="rounded-lg border border-dashed border-stone-300 bg-white/82 px-5 py-8 text-sm text-stone-500">
              {error ? `个人项目暂时无法加载：${error}` : "没有匹配到课程或课程包。"}
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderStars() {
    return (
      <div className="space-y-7">
        <section className="rounded-lg border border-stone-200 bg-white p-8 text-center">
          <Star className="mx-auto h-7 w-7 text-stone-400" />
          <h2 className="mt-4 text-lg font-semibold text-stone-950">Create your first list</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-stone-600">
            Stars 只显示你收藏的他人课程包；自己创建的课程包会保留在 Repositories。
          </p>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-base font-semibold text-stone-950">
              <Bookmark className="h-4 w-4 text-amber-500" />
              Stars
            </h2>
            <span className="text-xs font-medium text-stone-500">{filteredFavoriteProjects.length} starred</span>
          </div>

          <div className="mb-4 flex flex-col gap-3 border-b border-stone-200 pb-4 md:flex-row md:items-center">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                type="text"
                value={starQuery}
                onChange={(event) => setStarQuery(event.target.value)}
                placeholder="Search stars"
                className="w-full rounded-md border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
              />
            </div>
            <button type="button" className="rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700">
              Sort by: Recently starred
            </button>
          </div>

          <div className="space-y-0">
            {filteredFavoriteProjects.length ? (
              filteredFavoriteProjects.map((course) => renderStarCard(course))
            ) : (
              <div className="rounded-lg border border-dashed border-stone-300 bg-white/82 px-5 py-8 text-sm text-stone-500">
                没有匹配到已收藏的他人课程包。
              </div>
            )}
          </div>
        </section>
      </div>
    );
  }

  function renderLessonCard(lesson: Lesson) {
    const topics = getLessonTopics(lesson);
    const isOpening = openingLessonId === lesson.id;

    return (
      <article key={lesson.id} className="border-b border-stone-200 bg-white p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void handleOpenLesson(lesson.id)}
                className="text-left text-base font-semibold text-blue-600 hover:underline"
              >
                {lesson.title}
              </button>
              <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700">
                单独课程
              </span>
            </div>
            <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">
              {lesson.summary || "单独课程文档，可进入工作台继续编辑、分支和讲解。"}
            </p>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {topics.map((topic) => (
                <span
                  key={`${lesson.id}:${topic}`}
                  className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
                >
                  {topic}
                </span>
              ))}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1">
                <BookOpen className="h-3.5 w-3.5" />
                单独课程
              </span>
              <span>{lesson.history_graph.commits.length} commits</span>
              <span>Updated {formatRelativeTime(lesson.updated_at)}</span>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void handleOpenLesson(lesson.id)}
            disabled={isOpening}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950 disabled:cursor-wait disabled:opacity-70"
          >
            {isOpening ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            打开
            {!isOpening ? <ArrowUpRight className="h-4 w-4" /> : null}
          </button>
        </div>
      </article>
    );
  }

  function renderRepositoryCard(coursePackage: CoursePackage, variant: "compact" | "list") {
    const topics = getPackageTopics(coursePackage);
    const updatedAt = getPackageUpdatedAt(coursePackage);
    const isCompact = variant === "compact";
    const isExpanded = !isCompact && expandedPackageIds.has(coursePackage.id);
    const lessonsListId = `package-lessons-${coursePackage.id}`;

    return (
      <article
        key={coursePackage.id}
        className={clsx(
          "border-stone-200 bg-white",
          isCompact ? "min-h-32 rounded-lg border p-4" : "border-b p-5"
        )}
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/?package=${coursePackage.id}`} className="text-base font-semibold text-blue-600 hover:underline">
                {coursePackage.title}
              </Link>
              <button
                type="button"
                onClick={() => handleTogglePackageLessons(coursePackage.id)}
                aria-expanded={isExpanded}
                aria-controls={lessonsListId}
                className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 transition hover:border-emerald-300 hover:bg-emerald-100"
              >
                <FolderClosed className="h-3 w-3" />
                课程包
                <span className="rounded-full bg-white/75 px-1.5 py-px text-[10px] text-emerald-700">
                  {coursePackage.lessons.length}
                </span>
                <ChevronDown className={clsx("h-3 w-3 transition", isExpanded ? "rotate-180" : null)} />
              </button>
              <span className="rounded-full border border-stone-200 px-2 py-0.5 text-[11px] font-semibold text-stone-500">
                Public
              </span>
            </div>
            <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">
              {coursePackage.summary || "个人课程项目，包含课程文档、资料索引和学习活动记录。"}
            </p>

            {!isCompact ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {topics.map((topic) => (
                  <span
                    key={`${coursePackage.id}:${topic}`}
                    className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
                  >
                    {topic}
                  </span>
                ))}
              </div>
            ) : null}

            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1">
                <GraduationCap className="h-3.5 w-3.5" />
                {coursePackage.lessons.length} lessons
              </span>
              <span>{coursePackage.resources.length} resources</span>
              <span>Updated {updatedAt ? formatRelativeTime(updatedAt) : "暂无更新"}</span>
            </div>
          </div>

          <Link
            href={`/?package=${coursePackage.id}`}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            打开
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </div>

        {isExpanded ? (
          <ul
            id={lessonsListId}
            className="mt-4 divide-y divide-stone-200 rounded-md border border-stone-200 bg-stone-50/80"
            aria-label={`${coursePackage.title} 的单独课程列表`}
          >
            {coursePackage.lessons.length ? (
              coursePackage.lessons.map((lesson, index) => {
                const isOpening = openingLessonId === lesson.id;

                return (
                  <li
                    key={lesson.id}
                    className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[11px] font-semibold text-stone-400">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <button
                          type="button"
                          onClick={() => void handleOpenLesson(lesson.id)}
                          className="min-w-0 text-left text-sm font-semibold text-blue-600 hover:underline"
                        >
                          {lesson.title}
                        </button>
                        <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700">
                          单独课程
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-1 text-xs leading-5 text-stone-500">
                        {lesson.summary || lesson.board_document.title || "课程文档，可进入工作台继续编辑。"}
                      </p>
                    </div>

                    <button
                      type="button"
                      onClick={() => void handleOpenLesson(lesson.id)}
                      disabled={isOpening}
                      className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-wait disabled:opacity-70"
                    >
                      {isOpening ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
                      打开
                      {!isOpening ? <ArrowUpRight className="h-3.5 w-3.5" /> : null}
                    </button>
                  </li>
                );
              })
            ) : (
              <li className="px-3 py-3 text-sm text-stone-500">这个课程包还没有单独课程。</li>
            )}
          </ul>
        ) : null}
      </article>
    );
  }

  function renderStarCard(course: (typeof OPEN_SOURCE_COURSES)[number]) {
    return (
      <article key={course.id} className="border-b border-stone-200 bg-white p-5">
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
              <p className="truncate text-base font-semibold text-blue-600">{courseFullName(course)}</p>
              <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-600">{course.summary}</p>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: course.languageColor }} />
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
                <span>Updated {formatRelativeTime(course.updatedAt)}</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => handleToggleCollectCourse(course.id)}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700 transition hover:border-amber-300"
          >
            <Star className="h-3.5 w-3.5 fill-current" />
            Starred
          </button>
        </div>
      </article>
    );
  }
}
