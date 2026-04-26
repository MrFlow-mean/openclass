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
export const DEFAULT_COLLECTED_COURSE_IDS = ["open-civics", "react-system", "math-proof-lab"];

export const OPEN_SOURCE_COURSES: OpenCourse[] = [
  {
    id: "open-civics",
    owner: "openlearn-cn",
    title: "civil-law-foundation",
    summary: "民法基础开源课程，从请求权基础、合同编到侵权责任，用案例和图谱组织学习路径。",
    topics: ["civil-law", "case-study", "knowledge-map", "chinese"],
    language: "法学",
    languageColor: "#2563eb",
    category: "法律",
    level: "入门到进阶",
    lessons: 42,
    stars: 36400,
    forks: 3100,
    watchers: 824,
    updatedAt: "2026-04-25T08:18:00.000Z",
    license: "CC BY-SA 4.0",
    avatarSeed: "open-civics",
  },
  {
    id: "ai-teacher-kit",
    owner: "coursecraft",
    title: "ai-teacher-workflow",
    summary: "用 AI 生成课程脚本、讲义、课堂提问和复盘测验的完整工作流，适合教师和内容团队共建。",
    topics: ["ai", "teaching", "prompt", "workflow"],
    language: "教育技术",
    languageColor: "#16a34a",
    category: "AI 教学",
    level: "实战",
    lessons: 28,
    stars: 18200,
    forks: 1260,
    watchers: 438,
    updatedAt: "2026-04-22T16:40:00.000Z",
    license: "MIT",
    avatarSeed: "ai-teacher-kit",
  },
  {
    id: "react-system",
    owner: "frontend-labs",
    title: "react-design-system-course",
    summary: "从组件 API、可访问性、主题变量到复杂表单，构建一套可维护的 React 设计系统课程。",
    topics: ["react", "design-system", "typescript", "frontend"],
    language: "TypeScript",
    languageColor: "#3178c6",
    category: "前端工程",
    level: "进阶",
    lessons: 36,
    stars: 27100,
    forks: 2400,
    watchers: 516,
    updatedAt: "2026-04-24T10:02:00.000Z",
    license: "Apache-2.0",
    avatarSeed: "react-system",
  },
  {
    id: "math-proof-lab",
    owner: "proofstack",
    title: "linear-algebra-proof-lab",
    summary: "线性代数证明训练营，用交互式练习拆解向量空间、线性映射、特征值和正交分解。",
    topics: ["linear-algebra", "proof", "math", "exercise"],
    language: "数学",
    languageColor: "#7c3aed",
    category: "数学",
    level: "进阶",
    lessons: 31,
    stars: 15500,
    forks: 980,
    watchers: 302,
    updatedAt: "2026-04-20T09:12:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "math-proof-lab",
  },
  {
    id: "python-data",
    owner: "dataschool",
    title: "python-data-analysis-open",
    summary: "面向真实数据清洗、统计建模和可视化报告的 Python 数据分析公开课。",
    topics: ["python", "pandas", "visualization", "statistics"],
    language: "Python",
    languageColor: "#3776ab",
    category: "数据科学",
    level: "入门",
    lessons: 33,
    stars: 22900,
    forks: 2130,
    watchers: 481,
    updatedAt: "2026-04-23T13:27:00.000Z",
    license: "BSD-3-Clause",
    avatarSeed: "python-data",
  },
  {
    id: "product-thinking",
    owner: "buildbetter",
    title: "product-thinking-cases",
    summary: "用真实产品案例训练需求判断、指标设计、实验拆解和路线图表达。",
    topics: ["product", "case-study", "metrics", "roadmap"],
    language: "产品",
    languageColor: "#f97316",
    category: "产品设计",
    level: "实战",
    lessons: 24,
    stars: 11900,
    forks: 760,
    watchers: 197,
    updatedAt: "2026-04-18T18:05:00.000Z",
    license: "CC BY-NC 4.0",
    avatarSeed: "product-thinking",
  },
  {
    id: "english-writing",
    owner: "langopen",
    title: "academic-writing-studio",
    summary: "英语学术写作开源训练课，覆盖论点组织、段落节奏、引用表达和审稿回复。",
    topics: ["english", "writing", "academic", "research"],
    language: "English",
    languageColor: "#0ea5e9",
    category: "语言学习",
    level: "中级",
    lessons: 26,
    stars: 9800,
    forks: 610,
    watchers: 162,
    updatedAt: "2026-04-19T11:44:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "english-writing",
  },
  {
    id: "open-robotics",
    owner: "makerlab",
    title: "robotics-from-scratch",
    summary: "从传感器、运动控制到路径规划的机器人入门课程，包含仿真任务和硬件实践清单。",
    topics: ["robotics", "control", "simulation", "hardware"],
    language: "C++",
    languageColor: "#00599c",
    category: "工程实践",
    level: "入门到进阶",
    lessons: 39,
    stars: 13200,
    forks: 1040,
    watchers: 226,
    updatedAt: "2026-04-17T07:52:00.000Z",
    license: "MIT",
    avatarSeed: "open-robotics",
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
