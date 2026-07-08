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
    summary: "概念解释型开源课程包，从问题入口、定义边界到例子和检查题组织学习路径。",
    topics: ["concept", "example", "knowledge-map", "handout"],
    language: "课程结构",
    languageColor: "#2563eb",
    category: "概念解释",
    level: "入门到进阶",
    lessons: 42,
    stars: 36400,
    forks: 3100,
    watchers: 824,
    updatedAt: "2026-04-25T08:18:00.000Z",
    license: "CC BY-SA 4.0",
    avatarSeed: "concept-explainer",
  },
  {
    id: "structured-handout",
    owner: "coursecraft",
    title: "structured-handout-workflow",
    summary: "围绕学习目标生成课程脚本、讲义、课堂提问和复盘测验的完整工作流。",
    topics: ["teaching", "handout", "workflow"],
    language: "讲义设计",
    languageColor: "#16a34a",
    category: "课程设计",
    level: "实战",
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
    summary: "从目标、约束、步骤到交付清单，构建一套可复用的项目实战课程。",
    topics: ["project", "workflow", "delivery", "review"],
    language: "项目实战",
    languageColor: "#3178c6",
    category: "项目学习",
    level: "进阶",
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
    summary: "练习训练型课程包，用交互式任务拆解概念、步骤、迁移和复盘。",
    topics: ["practice", "exercise", "transfer", "feedback"],
    language: "练习训练",
    languageColor: "#7c3aed",
    category: "练习",
    level: "进阶",
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
    summary: "面向资料整理、数据片段解释和可视化报告的开放课程。",
    topics: ["data", "visualization", "explanation", "report"],
    language: "数据讲解",
    languageColor: "#3776ab",
    category: "数据表达",
    level: "入门",
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
    summary: "用可替换案例训练事实提取、依据定位、步骤拆解和结论表达。",
    topics: ["case-study", "reasoning", "rubric", "roadmap"],
    language: "案例分析",
    languageColor: "#f97316",
    category: "案例学习",
    level: "实战",
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
    summary: "结构化写作训练课，覆盖论点组织、段落节奏、引用表达和修改反馈。",
    topics: ["writing", "structure", "revision", "feedback"],
    language: "表达训练",
    languageColor: "#0ea5e9",
    category: "表达",
    level: "中级",
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
    summary: "从需求、原型到测试路径的入门课程，包含模拟任务和实践清单。",
    topics: ["build", "prototype", "simulation", "checklist"],
    language: "实践课程",
    languageColor: "#00599c",
    category: "实践",
    level: "入门到进阶",
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
    return `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}万`;
  }

  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }

  return value.toLocaleString("zh-CN");
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
