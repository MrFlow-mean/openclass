"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Flame,
  GitFork,
  GraduationCap,
  Search,
  Sparkles,
  Star,
  TrendingUp,
  UsersRound,
} from "lucide-react";

import {
  DEFAULT_COLLECTED_COURSE_IDS,
  OPEN_COURSE_COLLECTION_STORAGE_KEY,
  OPEN_SOURCE_COURSES,
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
  type OpenCourse,
} from "@/lib/open-courses";

type TrendWindow = "today" | "week" | "month";
type CategoryFilter = "all" | string;

const trendWindows: Array<{ id: TrendWindow; label: string }> = [
  { id: "today", label: "今日" },
  { id: "week", label: "本周" },
  { id: "month", label: "本月" },
];

function readCollectedCourseIds() {
  if (typeof window === "undefined") {
    return new Set(DEFAULT_COLLECTED_COURSE_IDS);
  }

  try {
    const stored = window.localStorage.getItem(OPEN_COURSE_COLLECTION_STORAGE_KEY);
    if (!stored) {
      return new Set(DEFAULT_COLLECTED_COURSE_IDS);
    }

    const parsed = JSON.parse(stored);
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
      return new Set(parsed);
    }
  } catch {
    return new Set(DEFAULT_COLLECTED_COURSE_IDS);
  }

  return new Set(DEFAULT_COLLECTED_COURSE_IDS);
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

function daysSince(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return 30;
  }

  return Math.max(0, Math.floor((Date.now() - timestamp) / 86400000));
}

function formatRelativeTime(value: string) {
  const days = daysSince(value);
  if (days <= 0) {
    return "今天";
  }
  if (days === 1) {
    return "昨天";
  }
  if (days < 7) {
    return `${days} 天前`;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(new Date(value));
}

function getTrendingScore(course: OpenCourse, windowId: TrendWindow) {
  const age = daysSince(course.updatedAt);
  const recency = Math.max(0, 21 - age);
  const baseScore = course.stars * 0.58 + course.forks * 3.4 + course.watchers * 9 + course.lessons * 130;

  if (windowId === "today") {
    return baseScore * 0.5 + recency * 1800 + course.watchers * 12;
  }

  if (windowId === "month") {
    return baseScore * 1.08 + recency * 520 + course.forks * 2;
  }

  return baseScore * 0.78 + recency * 1050 + course.watchers * 8;
}

function getGrowthPercent(course: OpenCourse, windowId: TrendWindow) {
  const age = daysSince(course.updatedAt);
  const recency = Math.max(1, 14 - Math.min(age, 13));
  const activitySignal = course.watchers * 0.08 + course.forks * 0.015 + course.lessons * 0.45;
  const multiplier = windowId === "today" ? 0.9 : windowId === "month" ? 0.42 : 0.62;
  return Math.max(3, Math.round((recency + activitySignal) * multiplier));
}

function getRecommendedScore(course: OpenCourse, favoriteCourses: OpenCourse[]) {
  if (!favoriteCourses.length) {
    return getTrendingScore(course, "week");
  }

  const favoriteTopics = new Set(favoriteCourses.flatMap((favorite) => favorite.topics));
  const favoriteCategories = new Set(favoriteCourses.map((favorite) => favorite.category));
  const favoriteLanguages = new Set(favoriteCourses.map((favorite) => favorite.language));
  const topicOverlap = course.topics.filter((topic) => favoriteTopics.has(topic)).length;
  const categoryBoost = favoriteCategories.has(course.category) ? 1 : 0;
  const languageBoost = favoriteLanguages.has(course.language) ? 1 : 0;

  return (
    topicOverlap * 2600 +
    categoryBoost * 1800 +
    languageBoost * 1100 +
    Math.max(0, 14 - daysSince(course.updatedAt)) * 420 +
    course.watchers * 4 +
    course.stars * 0.16
  );
}

function getRecommendationReason(course: OpenCourse, favoriteCourses: OpenCourse[]) {
  if (!favoriteCourses.length) {
    return "根据近期热度和课程完整度推荐";
  }

  const favoriteTopics = new Set(favoriteCourses.flatMap((favorite) => favorite.topics));
  const sharedTopic = course.topics.find((topic) => favoriteTopics.has(topic));
  if (sharedTopic) {
    return `因为你关注了 ${sharedTopic}`;
  }

  const favoriteCategory = favoriteCourses.find((favorite) => favorite.category === course.category);
  if (favoriteCategory) {
    return `和 ${favoriteCategory.title} 同属${course.category}`;
  }

  const favoriteLanguage = favoriteCourses.find((favorite) => favorite.language === course.language);
  if (favoriteLanguage) {
    return `与你收藏的 ${favoriteLanguage.language} 内容相近`;
  }

  return "和你的收藏项目受众相近";
}

function courseMatchesQuery(course: OpenCourse, normalizedQuery: string) {
  if (!normalizedQuery) {
    return true;
  }

  return [
    course.owner,
    course.title,
    course.summary,
    course.category,
    course.level,
    course.language,
    course.license,
    course.topics.join(" "),
  ]
    .join(" ")
    .toLowerCase()
    .includes(normalizedQuery);
}

function categoryCounts() {
  const counts = new Map<string, number>();
  OPEN_SOURCE_COURSES.forEach((course) => {
    counts.set(course.category, (counts.get(course.category) ?? 0) + 1);
  });

  return Array.from(counts, ([category, count]) => ({ category, count })).sort((left, right) => {
    if (right.count !== left.count) {
      return right.count - left.count;
    }
    return left.category.localeCompare(right.category, "zh-CN");
  });
}

export function TrendingCourses() {
  const [trendWindow, setTrendWindow] = useState<TrendWindow>("week");
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [query, setQuery] = useState("");
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );
  const normalizedQuery = query.trim().toLowerCase();
  const categories = useMemo(() => categoryCounts(), []);
  const trendingCourses = useMemo(() => {
    return OPEN_SOURCE_COURSES.filter((course) => {
      const matchesCategory = categoryFilter === "all" || course.category === categoryFilter;
      return matchesCategory && courseMatchesQuery(course, normalizedQuery);
    }).sort((left, right) => getTrendingScore(right, trendWindow) - getTrendingScore(left, trendWindow));
  }, [categoryFilter, normalizedQuery, trendWindow]);
  const favoriteCourses = useMemo(
    () => OPEN_SOURCE_COURSES.filter((course) => collectedCourseIds.has(course.id)),
    [collectedCourseIds]
  );
  const recommendedProjectRows = useMemo(() => {
    const recommended = OPEN_SOURCE_COURSES.filter((course) => {
      const matchesCategory = categoryFilter === "all" || course.category === categoryFilter;
      return matchesCategory && courseMatchesQuery(course, normalizedQuery) && !collectedCourseIds.has(course.id);
    }).sort((left, right) => getRecommendedScore(right, favoriteCourses) - getRecommendedScore(left, favoriteCourses));

    if (recommended.length) {
      return recommended;
    }

    const fallbackCourses = trendingCourses.filter((course) => !collectedCourseIds.has(course.id));
    return fallbackCourses.length ? fallbackCourses : trendingCourses;
  }, [categoryFilter, collectedCourseIds, favoriteCourses, normalizedQuery, trendingCourses]);
  const recommendedCourses = recommendedProjectRows.slice(0, 3);
  const featuredCourses = trendingCourses.slice(0, 3);
  const totalStars = trendingCourses.reduce((sum, course) => sum + course.stars, 0);
  const totalWatchers = trendingCourses.reduce((sum, course) => sum + course.watchers, 0);
  const totalTopics = new Set(trendingCourses.flatMap((course) => course.topics)).size;

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setCollectedCourseIds(readCollectedCourseIds());
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

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

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            返回主页
          </Link>

          <div className="flex items-center gap-2">
            <Link
              href="/profile?tab=stars"
              className="inline-flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-700 transition hover:border-amber-300"
            >
              <Star className="h-4 w-4" />
              Star
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)] lg:px-8">
        <aside className="min-w-0 lg:sticky lg:top-[88px] lg:self-start">
          <div className="rounded-lg border border-stone-200 bg-white p-4 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Sparkles className="h-4 w-4 text-sky-600" />
              发现
            </div>

            <div className="mt-4 grid gap-2">
              <a
                href="#top-trending"
                className="flex items-center justify-between rounded-md border border-orange-100 bg-orange-50 px-3 py-2 text-sm font-semibold text-orange-700 transition hover:border-orange-200 hover:bg-white"
              >
                <span className="inline-flex items-center gap-2">
                  <Flame className="h-4 w-4" />
                  热度最高
                </span>
                <span className="text-xs">{trendingCourses.length}</span>
              </a>
              <a
                href="#recommended"
                className="flex items-center justify-between rounded-md border border-sky-100 bg-sky-50 px-3 py-2 text-sm font-semibold text-sky-700 transition hover:border-sky-200 hover:bg-white"
              >
                <span className="inline-flex items-center gap-2">
                  <Sparkles className="h-4 w-4" />
                  推荐
                </span>
                <span className="text-xs">{recommendedProjectRows.length}</span>
              </a>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-1 rounded-md border border-stone-200 bg-stone-50 p-1">
              {trendWindows.map((item) => {
                const isActive = trendWindow === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setTrendWindow(item.id)}
                    className={clsx(
                      "rounded px-2 py-1.5 text-xs font-semibold transition",
                      isActive ? "bg-white text-stone-950 shadow-sm" : "text-stone-500 hover:text-stone-950"
                    )}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>

            <div className="mt-5 border-t border-stone-200 pt-4">
              <button
                type="button"
                aria-pressed={categoryFilter === "all"}
                onClick={() => setCategoryFilter("all")}
                className={clsx(
                  "flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm transition",
                  categoryFilter === "all"
                    ? "bg-stone-950 text-white"
                    : "text-stone-700 hover:bg-stone-100 hover:text-stone-950"
                )}
              >
                <span>全部项目</span>
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[10px]",
                    categoryFilter === "all" ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                  )}
                >
                  {OPEN_SOURCE_COURSES.length}
                </span>
              </button>

              <div className="mt-2 space-y-1">
                {categories.map((item) => {
                  const isActive = categoryFilter === item.category;
                  return (
                    <button
                      key={item.category}
                      type="button"
                      aria-pressed={isActive}
                      onClick={() => setCategoryFilter(item.category)}
                      className={clsx(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition",
                        isActive ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50 hover:text-stone-950"
                      )}
                    >
                      <span className="truncate">{item.category}</span>
                      <span className="text-xs text-stone-400">{item.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-col">
          <section
            id="recommended"
            className="order-2 mt-5 scroll-mt-24 rounded-lg border border-sky-100 bg-[linear-gradient(180deg,#ffffff_0%,#f3f9ff_100%)] p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)] sm:p-6"
          >
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
              <div>
                <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-stone-950 sm:text-3xl">
                  <Sparkles className="h-5 w-5 text-sky-600" />
                  推荐给你
                </h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
                  根据你 Star 的课程方向、主题标签和学科偏好，优先推送可能感兴趣的项目。
                </p>
              </div>
              <Link
                href="/profile?tab=stars"
                className="inline-flex w-fit items-center gap-2 rounded-md border border-sky-100 bg-white px-3 py-2 text-sm font-semibold text-sky-700 transition hover:border-sky-200 hover:text-sky-800"
              >
                管理 Stars
                <ArrowUpRight className="h-4 w-4" />
              </Link>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-3">
              {recommendedCourses.length ? (
                recommendedCourses.map((course, index) => (
                  <RecommendedCourseCard
                    key={course.id}
                    course={course}
                    isCollected={collectedCourseIds.has(course.id)}
                    reason={getRecommendationReason(course, favoriteCourses)}
                    rank={index + 1}
                    onToggleCollect={handleToggleCollectCourse}
                  />
                ))
              ) : (
                <div className="rounded-lg border border-dashed border-sky-200 bg-white/80 px-5 py-8 text-sm text-stone-500 lg:col-span-3">
                  暂时没有匹配的推荐项目。换个分区或搜索词后再看看。
                </div>
              )}
            </div>
          </section>

          <section
            id="top-trending"
            className="order-1 scroll-mt-24 rounded-lg border border-stone-200 bg-white p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)] sm:p-6"
          >
            <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-semibold text-orange-600">
                  <TrendingUp className="h-4 w-4" />
                  Explore
                </div>
                <h2 className="mt-2 text-3xl font-semibold tracking-tight text-stone-950 sm:text-4xl">热门项目</h2>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-stone-600">
                  按 stars、forks、watchers、课程体量和近期更新综合排序的开源课程项目。
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-3 xl:w-[28rem]">
                <MetricCard label="Stars" value={formatCompactNumber(totalStars)} Icon={Star} />
                <MetricCard label="Watchers" value={formatCompactNumber(totalWatchers)} Icon={UsersRound} />
                <MetricCard label="Topics" value={totalTopics.toString()} Icon={BookOpen} />
              </div>
            </div>

            <div className="mt-6 grid gap-4 lg:grid-cols-3">
              {featuredCourses.map((course, index) => (
                <FeaturedCourseCard
                  key={course.id}
                  course={course}
                  growthPercent={getGrowthPercent(course, trendWindow)}
                  isCollected={collectedCourseIds.has(course.id)}
                  rank={index + 1}
                  onToggleCollect={handleToggleCollectCourse}
                />
              ))}
            </div>
          </section>

          <section className="order-3 mt-5 rounded-lg border border-stone-200 bg-white p-4 shadow-[0_16px_34px_rgba(15,23,42,0.05)] sm:p-5">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-base font-semibold text-stone-950">
                  <Sparkles className="h-4 w-4 text-sky-600" />
                  推荐项目
                </h2>
                <p className="mt-1 text-xs text-stone-500">{recommendedProjectRows.length} 个项目正在展示</p>
              </div>

              <div className="relative min-w-0 md:w-80">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索项目、作者、主题"
                  className="w-full rounded-full border border-stone-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
                />
              </div>
            </div>

            <div className="mt-5 space-y-3">
              {recommendedProjectRows.length ? (
                recommendedProjectRows.map((course, index) => (
                  <TrendingCourseRow
                    key={course.id}
                    course={course}
                    growthPercent={getGrowthPercent(course, trendWindow)}
                    isCollected={collectedCourseIds.has(course.id)}
                    rank={index + 1}
                    onToggleCollect={handleToggleCollectCourse}
                  />
                ))
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 bg-stone-50 px-5 py-10 text-sm text-stone-500">
                  没有找到匹配的推荐项目。
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

type MetricCardProps = {
  label: string;
  value: string;
  Icon: typeof Star;
};

function MetricCard({ label, value, Icon }: MetricCardProps) {
  return (
    <div className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-3">
      <p className="flex items-center gap-1.5 text-xs text-stone-500">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </p>
      <p className="mt-2 text-lg font-semibold text-stone-950">{value}</p>
    </div>
  );
}

type CourseCardProps = {
  course: OpenCourse;
  growthPercent: number;
  isCollected: boolean;
  rank: number;
  onToggleCollect: (courseId: string) => void;
};

type RecommendedCourseCardProps = {
  course: OpenCourse;
  isCollected: boolean;
  reason: string;
  rank: number;
  onToggleCollect: (courseId: string) => void;
};

function RecommendedCourseCard({ course, isCollected, reason, rank, onToggleCollect }: RecommendedCourseCardProps) {
  return (
    <article className="flex min-h-full flex-col rounded-lg border border-sky-100 bg-white p-4 shadow-[0_10px_24px_rgba(14,165,233,0.06)] transition hover:border-sky-200">
      <div className="flex items-start justify-between gap-3">
        <Image
          src={courseAvatarUrl(course)}
          alt=""
          className="h-11 w-11 rounded-md border border-stone-200 bg-stone-100"
          width={44}
          height={44}
          unoptimized
        />
        <span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-semibold text-sky-700">推荐 {rank}</span>
      </div>

      <Link href={courseDetailHref(course)} className="mt-4 line-clamp-2 text-base font-semibold text-blue-600 hover:underline">
        {courseFullName(course)}
      </Link>
      <p className="mt-2 line-clamp-3 text-sm leading-6 text-stone-600">{course.summary}</p>

      <div className="mt-4 rounded-md border border-sky-100 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-700">
        {reason}
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {course.topics.slice(0, 3).map((topic) => (
          <span
            key={`${course.id}:recommended:${topic}`}
            className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
          >
            {topic}
          </span>
        ))}
      </div>

      <div className="mt-auto pt-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-stone-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: course.languageColor }} />
            {course.language}
          </span>
          <span className="inline-flex items-center gap-1">
            <Star className="h-3.5 w-3.5" />
            {formatCompactNumber(course.stars)}
          </span>
          <span className="inline-flex items-center gap-1">
            <GraduationCap className="h-3.5 w-3.5" />
            {course.lessons} lessons
          </span>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <Link
            href={courseDetailHref(course)}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            打开
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
          <button
            type="button"
            onClick={() => onToggleCollect(course.id)}
            className={clsx(
              "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-xs font-semibold transition",
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
}

function FeaturedCourseCard({ course, growthPercent, isCollected, rank, onToggleCollect }: CourseCardProps) {
  return (
    <article className="flex min-h-full flex-col rounded-lg border border-stone-200 bg-[linear-gradient(180deg,#fff_0%,#faf8f2_100%)] p-4">
      <div className="flex items-start justify-between gap-3">
        <Image
          src={courseAvatarUrl(course)}
          alt=""
          className="h-11 w-11 rounded-md border border-stone-200 bg-stone-100"
          width={44}
          height={44}
          unoptimized
        />
        <span className="rounded-full bg-orange-50 px-2.5 py-1 text-xs font-semibold text-orange-700">#{rank}</span>
      </div>

      <Link href={courseDetailHref(course)} className="mt-4 line-clamp-2 text-base font-semibold text-blue-600 hover:underline">
        {courseFullName(course)}
      </Link>
      <p className="mt-2 line-clamp-3 text-sm leading-6 text-stone-600">{course.summary}</p>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {course.topics.slice(0, 3).map((topic) => (
          <span key={`${course.id}:featured:${topic}`} className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">
            {topic}
          </span>
        ))}
      </div>

      <div className="mt-auto pt-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-stone-500">
          <span className="inline-flex items-center gap-1">
            <Star className="h-3.5 w-3.5" />
            {formatCompactNumber(course.stars)}
          </span>
          <span className="inline-flex items-center gap-1 text-emerald-700">
            <TrendingUp className="h-3.5 w-3.5" />+{growthPercent}%
          </span>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <Link
            href={courseDetailHref(course)}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            打开
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
          <button
            type="button"
            onClick={() => onToggleCollect(course.id)}
            className={clsx(
              "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-xs font-semibold transition",
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
}

function TrendingCourseRow({ course, growthPercent, isCollected, rank, onToggleCollect }: CourseCardProps) {
  return (
    <article className="rounded-lg border border-stone-200 bg-white p-4 transition hover:border-stone-300 hover:bg-stone-50/40">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 gap-3">
          <div className="flex w-9 shrink-0 justify-center pt-1 text-sm font-semibold text-stone-400">#{rank}</div>
          <Image
            src={courseAvatarUrl(course)}
            alt=""
            className="mt-0.5 h-10 w-10 rounded-md border border-stone-200 bg-stone-100"
            width={40}
            height={40}
            unoptimized
          />

          <div className="min-w-0">
            <Link href={courseDetailHref(course)} className="block truncate text-base font-semibold text-blue-600 hover:underline">
              {courseFullName(course)}
            </Link>
            <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-700">{course.summary}</p>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {course.topics.map((topic) => (
                <span key={`${course.id}:topic:${topic}`} className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">
                  {topic}
                </span>
              ))}
            </div>

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
              <span className="inline-flex items-center gap-1">
                <GraduationCap className="h-3.5 w-3.5" />
                {course.lessons} lessons
              </span>
              <span className="inline-flex items-center gap-1 text-emerald-700">
                <TrendingUp className="h-3.5 w-3.5" />+{growthPercent}%
              </span>
              <span>更新 {formatRelativeTime(course.updatedAt)}</span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 md:pt-1">
          <Link
            href={courseDetailHref(course)}
            className="inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            打开
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
          <button
            type="button"
            onClick={() => onToggleCollect(course.id)}
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
}
