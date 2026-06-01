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
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
  openCourseFromSummary,
  type OpenCourse,
} from "@/lib/open-courses";
import { api } from "@/lib/api";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { ProfileSettingsTexts } from "@/lib/i18n/product-ui";

type TrendWindow = "today" | "week" | "month";
type CategoryFilter = "all" | string;
type TrendingTexts = ProfileSettingsTexts["trending"];

const trendWindowIds: TrendWindow[] = ["today", "week", "month"];

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

function formatRelativeTime(value: string, txt: TrendingTexts, intlLocale: string) {
  const days = daysSince(value);
  if (days <= 0) {
    return txt.today;
  }
  if (days === 1) {
    return txt.yesterday;
  }
  if (days < 7) {
    return txt.daysAgo(days);
  }

  return new Intl.DateTimeFormat(intlLocale, {
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

function getRecommendationReason(course: OpenCourse, favoriteCourses: OpenCourse[], txt: TrendingTexts) {
  if (!favoriteCourses.length) {
    return txt.defaultReason;
  }

  const favoriteTopics = new Set(favoriteCourses.flatMap((favorite) => favorite.topics));
  const sharedTopic = course.topics.find((topic) => favoriteTopics.has(topic));
  if (sharedTopic) {
    return txt.sharedTopicReason(sharedTopic);
  }

  const favoriteCategory = favoriteCourses.find((favorite) => favorite.category === course.category);
  if (favoriteCategory) {
    return txt.categoryReason(favoriteCategory.title, course.category);
  }

  const favoriteLanguage = favoriteCourses.find((favorite) => favorite.language === course.language);
  if (favoriteLanguage) {
    return txt.languageReason(favoriteLanguage.language);
  }

  return txt.audienceReason;
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

function categoryCounts(courses: OpenCourse[]) {
  const counts = new Map<string, number>();
  courses.forEach((course) => {
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
  const { texts: txt, intlLocale } = useInterfaceLanguage();
  const t = txt.trending;
  const loadOpenCoursesError = t.loadOpenCoursesError;
  const [openCourses, setOpenCourses] = useState<OpenCourse[]>([]);
  const [isLoadingCourses, setIsLoadingCourses] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [trendWindow, setTrendWindow] = useState<TrendWindow>("week");
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [query, setQuery] = useState("");
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );
  const normalizedQuery = query.trim().toLowerCase();
  const categories = useMemo(() => categoryCounts(openCourses), [openCourses]);
  const trendingCourses = useMemo(() => {
    return openCourses.filter((course) => {
      const matchesCategory = categoryFilter === "all" || course.category === categoryFilter;
      return matchesCategory && courseMatchesQuery(course, normalizedQuery);
    }).sort((left, right) => getTrendingScore(right, trendWindow) - getTrendingScore(left, trendWindow));
  }, [categoryFilter, normalizedQuery, openCourses, trendWindow]);
  const favoriteCourses = useMemo(
    () => openCourses.filter((course) => collectedCourseIds.has(course.id)),
    [collectedCourseIds, openCourses]
  );
  const recommendedProjectRows = useMemo(() => {
    const recommended = openCourses.filter((course) => {
      const matchesCategory = categoryFilter === "all" || course.category === categoryFilter;
      return matchesCategory && courseMatchesQuery(course, normalizedQuery) && !collectedCourseIds.has(course.id);
    }).sort((left, right) => getRecommendedScore(right, favoriteCourses) - getRecommendedScore(left, favoriteCourses));

    if (recommended.length) {
      return recommended;
    }

    const fallbackCourses = trendingCourses.filter((course) => !collectedCourseIds.has(course.id));
    return fallbackCourses.length ? fallbackCourses : trendingCourses;
  }, [categoryFilter, collectedCourseIds, favoriteCourses, normalizedQuery, openCourses, trendingCourses]);
  const recommendedCourses = recommendedProjectRows.slice(0, 3);
  const featuredCourses = trendingCourses.slice(0, 3);
  const totalStars = trendingCourses.reduce((sum, course) => sum + course.stars, 0);
  const totalWatchers = trendingCourses.reduce((sum, course) => sum + course.watchers, 0);
  const totalTopics = new Set(trendingCourses.flatMap((course) => course.topics)).size;

  useEffect(() => {
    let isDisposed = false;

    async function loadOpenCourses() {
      try {
        const response = await api.listOpenCourses();
        if (!isDisposed) {
          setOpenCourses(response.courses.map(openCourseFromSummary));
          setLoadError(null);
        }
      } catch (error) {
        if (!isDisposed) {
          setLoadError(error instanceof Error ? error.message : loadOpenCoursesError);
        }
      } finally {
        if (!isDisposed) {
          setIsLoadingCourses(false);
        }
      }
    }

    void loadOpenCourses();

    return () => {
      isDisposed = true;
    };
  }, [loadOpenCoursesError]);

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
            {t.backHome}
          </Link>

          <div className="flex items-center gap-2">
            <Link
              href="/profile?tab=stars"
              className="inline-flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-700 transition hover:border-amber-300"
            >
              <Star className="h-4 w-4" />
              {t.star}
            </Link>
          </div>
        </div>
      </header>

      {isLoadingCourses || loadError ? (
        <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6 lg:px-8">
          <div className="rounded-md border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600">
            {isLoadingCourses ? t.loadingOpenCourses : loadError}
          </div>
        </div>
      ) : null}

      <div className="mx-auto grid w-full max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)] lg:px-8">
        <aside className="min-w-0 lg:sticky lg:top-[88px] lg:self-start">
          <div className="rounded-lg border border-stone-200 bg-white p-4 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Sparkles className="h-4 w-4 text-sky-600" />
              {t.explore}
            </div>

            <div className="mt-4 grid gap-2">
              <a
                href="#top-trending"
                className="flex items-center justify-between rounded-md border border-orange-100 bg-orange-50 px-3 py-2 text-sm font-semibold text-orange-700 transition hover:border-orange-200 hover:bg-white"
              >
                <span className="inline-flex items-center gap-2">
                  <Flame className="h-4 w-4" />
                  {t.topMomentum}
                </span>
                <span className="text-xs">{trendingCourses.length}</span>
              </a>
              <a
                href="#recommended"
                className="flex items-center justify-between rounded-md border border-sky-100 bg-sky-50 px-3 py-2 text-sm font-semibold text-sky-700 transition hover:border-sky-200 hover:bg-white"
              >
                <span className="inline-flex items-center gap-2">
                  <Sparkles className="h-4 w-4" />
                  {t.recommended}
                </span>
                <span className="text-xs">{recommendedProjectRows.length}</span>
              </a>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-1 rounded-md border border-stone-200 bg-stone-50 p-1">
              {trendWindowIds.map((itemId) => {
                const isActive = trendWindow === itemId;
                return (
                  <button
                    key={itemId}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setTrendWindow(itemId)}
                    className={clsx(
                      "rounded px-2 py-1.5 text-xs font-semibold transition",
                      isActive ? "bg-white text-stone-950 shadow-sm" : "text-stone-500 hover:text-stone-950"
                    )}
                  >
                    {t.periods[itemId]}
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
                <span>{t.allProjects}</span>
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[10px]",
                    categoryFilter === "all" ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                  )}
                >
                  {openCourses.length}
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
                  {t.recommendedForYou}
                </h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
                  {t.recommendationBody}
                </p>
              </div>
              <Link
                href="/profile?tab=stars"
                className="inline-flex w-fit items-center gap-2 rounded-md border border-sky-100 bg-white px-3 py-2 text-sm font-semibold text-sky-700 transition hover:border-sky-200 hover:text-sky-800"
              >
                {t.manageStars}
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
                    reason={getRecommendationReason(course, favoriteCourses, t)}
                    rank={index + 1}
                    onToggleCollect={handleToggleCollectCourse}
                    txt={t}
                  />
                ))
              ) : (
                <div className="rounded-lg border border-dashed border-sky-200 bg-white/80 px-5 py-8 text-sm text-stone-500 lg:col-span-3">
                  {t.noRecommended}
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
                  {t.explore}
                </div>
                <h2 className="mt-2 text-3xl font-semibold tracking-tight text-stone-950 sm:text-4xl">{t.trendingProjects}</h2>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-stone-600">
                  {t.trendingBody}
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-3 xl:w-[28rem]">
                <MetricCard label={t.metrics.stars} value={formatCompactNumber(totalStars)} Icon={Star} />
                <MetricCard label={t.metrics.watchers} value={formatCompactNumber(totalWatchers)} Icon={UsersRound} />
                <MetricCard label={t.metrics.topics} value={totalTopics.toString()} Icon={BookOpen} />
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
                  txt={t}
                />
              ))}
            </div>
          </section>

          <section className="order-3 mt-5 rounded-lg border border-stone-200 bg-white p-4 shadow-[0_16px_34px_rgba(15,23,42,0.05)] sm:p-5">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-base font-semibold text-stone-950">
                  <Sparkles className="h-4 w-4 text-sky-600" />
                  {t.recommendedProjects}
                </h2>
                <p className="mt-1 text-xs text-stone-500">{t.showingProjects(recommendedProjectRows.length)}</p>
              </div>

              <div className="relative min-w-0 md:w-80">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t.searchPlaceholder}
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
                    txt={t}
                    intlLocale={intlLocale}
                  />
                ))
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 bg-stone-50 px-5 py-10 text-sm text-stone-500">
                  {t.noMatches}
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
  txt: TrendingTexts;
};

function RecommendedCourseCard({ course, isCollected, reason, rank, onToggleCollect, txt }: RecommendedCourseCardProps) {
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
        <span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-semibold text-sky-700">{txt.rank(rank)}</span>
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
            {txt.lessonsCount(course.lessons)}
          </span>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <Link
            href={courseDetailHref(course)}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            {txt.open}
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
            {isCollected ? txt.starred : txt.star}
          </button>
        </div>
      </div>
    </article>
  );
}

function FeaturedCourseCard({ course, growthPercent, isCollected, rank, onToggleCollect, txt }: CourseCardProps & { txt: TrendingTexts }) {
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
        <span className="rounded-full bg-orange-50 px-2.5 py-1 text-xs font-semibold text-orange-700">{txt.rankHash(rank)}</span>
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
            {txt.open}
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
            {isCollected ? txt.starred : txt.star}
          </button>
        </div>
      </div>
    </article>
  );
}

function TrendingCourseRow({
  course,
  growthPercent,
  isCollected,
  rank,
  onToggleCollect,
  txt,
  intlLocale,
}: CourseCardProps & { txt: TrendingTexts; intlLocale: string }) {
  return (
    <article className="rounded-lg border border-stone-200 bg-white p-4 transition hover:border-stone-300 hover:bg-stone-50/40">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 gap-3">
          <div className="flex w-9 shrink-0 justify-center pt-1 text-sm font-semibold text-stone-400">{txt.rankHash(rank)}</div>
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
                {txt.lessonsCount(course.lessons)}
              </span>
              <span className="inline-flex items-center gap-1 text-emerald-700">
                <TrendingUp className="h-3.5 w-3.5" />+{growthPercent}%
              </span>
              <span>{txt.updated(formatRelativeTime(course.updatedAt, txt, intlLocale))}</span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 md:pt-1">
          <Link
            href={courseDetailHref(course)}
            className="inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
          >
            {txt.open}
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
            {isCollected ? txt.starred : txt.star}
          </button>
        </div>
      </div>
    </article>
  );
}
