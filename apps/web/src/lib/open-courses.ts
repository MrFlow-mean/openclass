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
};

export type OpenCourseSort = "best-match" | "stars" | "updated";

export const OPEN_COURSE_COLLECTION_STORAGE_KEY = "blackboard-ai:collected-open-courses";
export const DEFAULT_COLLECTED_COURSE_IDS = ["concept-explainer", "source-handout", "practice-builder"];

export const OPEN_SOURCE_COURSES: OpenCourse[] = [
  {
    id: "concept-explainer",
    owner: "openlearn-cn",
    title: "concept-explanation-kit",
    summary: "An open course package for concept explanation, organizing learning paths from entry questions to definitions, examples, and checks.",
    topics: ["concept", "example", "knowledge-map", "handout"],
    language: "Course structure",
    languageColor: "#2563eb",
    category: "Concept explanation",
    level: "Beginner to advanced",
    lessons: 42,
    stars: 36400,
    forks: 3100,
    watchers: 824,
    updatedAt: "2026-04-25T08:18:00.000Z",
    license: "CC BY-SA 4.0",
    avatarSeed: "concept-explainer",
  },
  {
    id: "source-handout",
    owner: "coursecraft",
    title: "source-based-handout-workflow",
    summary: "A full workflow for turning uploaded resources into scripts, notes, classroom prompts, and review checks.",
    topics: ["source", "teaching", "handout", "workflow"],
    language: "Resource teaching",
    languageColor: "#16a34a",
    category: "Resource explanation",
    level: "Applied",
    lessons: 28,
    stars: 18200,
    forks: 1260,
    watchers: 438,
    updatedAt: "2026-04-22T16:40:00.000Z",
    license: "MIT",
    avatarSeed: "source-handout",
  },
  {
    id: "project-workshop",
    owner: "workflow-labs",
    title: "project-practice-course",
    summary: "A reusable project-based course built from goals, constraints, steps, and delivery checklists.",
    topics: ["project", "workflow", "delivery", "review"],
    language: "Project practice",
    languageColor: "#3178c6",
    category: "Project learning",
    level: "Advanced",
    lessons: 36,
    stars: 27100,
    forks: 2400,
    watchers: 516,
    updatedAt: "2026-04-24T10:02:00.000Z",
    license: "Apache-2.0",
    avatarSeed: "project-workshop",
  },
  {
    id: "practice-builder",
    owner: "practicestack",
    title: "transfer-practice-lab",
    summary: "A practice-training package that uses interactive tasks to unpack concepts, steps, transfer, and review.",
    topics: ["practice", "exercise", "transfer", "feedback"],
    language: "Practice training",
    languageColor: "#7c3aed",
    category: "Practice",
    level: "Advanced",
    lessons: 31,
    stars: 15500,
    forks: 980,
    watchers: 302,
    updatedAt: "2026-04-20T09:12:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "practice-builder",
  },
  {
    id: "data-story",
    owner: "dataschool",
    title: "data-to-explanation-open",
    summary: "An open course for organizing resources, explaining data fragments, and creating visual reports.",
    topics: ["data", "visualization", "explanation", "report"],
    language: "Data explanation",
    languageColor: "#3776ab",
    category: "Data communication",
    level: "Beginner",
    lessons: 33,
    stars: 22900,
    forks: 2130,
    watchers: 481,
    updatedAt: "2026-04-23T13:27:00.000Z",
    license: "BSD-3-Clause",
    avatarSeed: "data-story",
  },
  {
    id: "case-analysis",
    owner: "buildbetter",
    title: "case-analysis-path",
    summary: "Train fact extraction, evidence location, step breakdown, and conclusion writing with replaceable cases.",
    topics: ["case-study", "reasoning", "rubric", "roadmap"],
    language: "Case analysis",
    languageColor: "#f97316",
    category: "Case learning",
    level: "Applied",
    lessons: 24,
    stars: 11900,
    forks: 760,
    watchers: 197,
    updatedAt: "2026-04-18T18:05:00.000Z",
    license: "CC BY-NC 4.0",
    avatarSeed: "case-analysis",
  },
  {
    id: "writing-studio",
    owner: "langopen",
    title: "structured-writing-studio",
    summary: "A structured writing course covering argument organization, paragraph rhythm, citations, and revision feedback.",
    topics: ["writing", "structure", "revision", "feedback"],
    language: "Expression training",
    languageColor: "#0ea5e9",
    category: "Expression",
    level: "Intermediate",
    lessons: 26,
    stars: 9800,
    forks: 610,
    watchers: 162,
    updatedAt: "2026-04-19T11:44:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "writing-studio",
  },
  {
    id: "build-from-scratch",
    owner: "makerlab",
    title: "build-from-scratch",
    summary: "An introductory course from requirements and prototypes to test paths, with simulated tasks and practical checklists.",
    topics: ["build", "prototype", "simulation", "checklist"],
    language: "Practical course",
    languageColor: "#00599c",
    category: "Practice",
    level: "Beginner to advanced",
    lessons: 39,
    stars: 13200,
    forks: 1040,
    watchers: 226,
    updatedAt: "2026-04-17T07:52:00.000Z",
    license: "MIT",
    avatarSeed: "build-from-scratch",
  },
];

export function formatCompactNumber(value: number) {
  if (value >= 10000) {
    return `${(value / 1000).toFixed(value >= 100000 ? 0 : 1)}k`;
  }

  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }

  return value.toLocaleString("en-US");
}

export function courseFullName(course: OpenCourse) {
  return `${course.owner}/${course.title}`;
}

export function courseDetailHref(course: Pick<OpenCourse, "id">) {
  return `/courses/${course.id}`;
}

export function courseAvatarUrl(course: OpenCourse) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(course.avatarSeed)}`;
}

export function searchOpenCourses(query: string) {
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);

  if (!terms.length) {
    return OPEN_SOURCE_COURSES;
  }

  return OPEN_SOURCE_COURSES.filter((course) => {
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
    const scoreLeft = left.stars * 0.7 + left.watchers * 12 + left.lessons * 100;
    const scoreRight = right.stars * 0.7 + right.watchers * 12 + right.lessons * 100;
    return scoreRight - scoreLeft;
  });
}
