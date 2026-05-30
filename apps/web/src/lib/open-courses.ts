import type { OpenCourseSummary } from "@/types";

export type OpenCourse = {
  id: string;
  owner: string;
  title: string;
  summary: string;
  topics: string[];
  language: string;
  languageColor: string;
  category: string;
  level: string;
  lessons: number;
  stars: number;
  forks: number;
  watchers: number;
  updatedAt: string;
  license: string;
  avatarSeed: string;
  raw: OpenCourseSummary;
};

export type OpenCourseSort = "best-match" | "stars" | "updated";

export const OPEN_COURSE_COLLECTION_STORAGE_KEY = "blackboard-ai:collected-open-courses";
export const DEFAULT_COLLECTED_COURSE_IDS: string[] = [];
export const OPEN_SOURCE_COURSES: OpenCourse[] = [];

export function openCourseFromSummary(course: OpenCourseSummary): OpenCourse {
  return {
    id: course.id,
    owner: course.owner.display_name,
    title: course.title,
    summary: course.summary,
    topics: course.topics,
    language: "Course package",
    languageColor: "#2563eb",
    category: "Open course",
    level: `${course.stats.lessons} lesson${course.stats.lessons === 1 ? "" : "s"}`,
    lessons: course.stats.lessons,
    stars: course.stats.contributors,
    forks: course.stats.forks,
    watchers: course.stats.open_contributions,
    updatedAt: course.updated_at,
    license: "Maintainer reviewed",
    avatarSeed: course.id,
    raw: course,
  };
}

export function formatCompactNumber(value: number) {
  if (value >= 10000) {
    return `${(value / 1000).toFixed(value >= 100000 ? 0 : 1)}k`;
  }

  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }

  return value.toLocaleString("en-US");
}

export function courseFullName(course: Pick<OpenCourse, "owner" | "title">) {
  return `${course.owner}/${course.title}`;
}

export function courseDetailHref(course: Pick<OpenCourse, "id">) {
  return `/courses/${course.id}`;
}

export function courseAvatarUrl(course: Pick<OpenCourse, "avatarSeed">) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(course.avatarSeed)}`;
}

export function searchOpenCourses(query: string): OpenCourse[];
export function searchOpenCourses(courses: OpenCourse[], query: string): OpenCourse[];
export function searchOpenCourses(coursesOrQuery: OpenCourse[] | string, query = "") {
  const courses = Array.isArray(coursesOrQuery) ? coursesOrQuery : OPEN_SOURCE_COURSES;
  const searchQuery = Array.isArray(coursesOrQuery) ? query : coursesOrQuery;
  const terms = searchQuery
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);

  if (!terms.length) {
    return courses;
  }

  return courses.filter((course) => {
    const searchable = [
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
      .toLowerCase();

    return terms.every((term) => searchable.includes(term));
  });
}

export function sortOpenCourses(courses: OpenCourse[], sort: OpenCourseSort) {
  const sorted = [...courses];

  if (sort === "stars") {
    return sorted.sort((left, right) => right.stars - left.stars);
  }

  if (sort === "updated") {
    return sorted.sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime());
  }

  return sorted.sort((left, right) => {
    const scoreLeft = left.stars * 0.7 + left.watchers * 12 + left.lessons * 100 + left.forks * 8;
    const scoreRight = right.stars * 0.7 + right.watchers * 12 + right.lessons * 100 + right.forks * 8;
    return scoreRight - scoreLeft;
  });
}
