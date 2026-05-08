export type FollowedCreator = {
  id: string;
  name: string;
  handle: string;
  bio: string;
  field: string;
  followers: number;
  avatarSeed: string;
  unreadCount: number;
};

export type FollowedCourseUpdate = {
  id: string;
  creatorId: string;
  courseTitle: string;
  moduleTitle: string;
  summary: string;
  updatedAt: string;
  updateKind: "new_lesson" | "course_revision" | "resource_added" | "note_added";
  lessonCount: number;
  views: number;
  comments: number;
  likes: number;
  tags: string[];
  coverSeed: string;
};

export type FollowedCourseUpdateItem = {
  update: FollowedCourseUpdate;
  creator: FollowedCreator;
};

export const FOLLOWED_UPDATE_KIND_LABELS: Record<FollowedCourseUpdate["updateKind"], string> = {
  new_lesson: "新课",
  course_revision: "更新",
  resource_added: "资料",
  note_added: "笔记",
};

export const FOLLOWED_CREATORS: FollowedCreator[] = [
  {
    id: "case-lab",
    name: "案例拆解室",
    handle: "case-lab",
    bio: "把资料、案例和课堂问题整理成可迁移的学习路径。",
    field: "案例学习",
    followers: 128400,
    avatarSeed: "case-lab",
    unreadCount: 3,
  },
  {
    id: "source-map",
    name: "资料地图",
    handle: "source-map",
    bio: "从上传资料中提取目录、证据和讲解线索。",
    field: "资料讲解",
    followers: 84200,
    avatarSeed: "source-map",
    unreadCount: 1,
  },
  {
    id: "project-studio",
    name: "项目实战间",
    handle: "project-studio",
    bio: "把复杂任务拆成目标、步骤、产出和复盘。",
    field: "项目学习",
    followers: 96300,
    avatarSeed: "project-studio",
    unreadCount: 2,
  },
  {
    id: "pen-review",
    name: "笔记评测室",
    handle: "pen-review",
    bio: "学习工具、笔记方法和资料整理流程。",
    field: "学习方法",
    followers: 67200,
    avatarSeed: "pen-review",
    unreadCount: 1,
  },
  {
    id: "practice-daily",
    name: "每日练习工坊",
    handle: "practice-daily",
    bio: "把任意主题拆成每日可执行练习。",
    field: "练习训练",
    followers: 73900,
    avatarSeed: "practice-daily",
    unreadCount: 0,
  },
  {
    id: "concept-bridge",
    name: "概念桥",
    handle: "concept-bridge",
    bio: "把抽象概念讲成定义、关系、例子和检查问题。",
    field: "概念解释",
    followers: 101800,
    avatarSeed: "concept-bridge",
    unreadCount: 4,
  },
];

export const FOLLOWED_COURSE_UPDATES: FollowedCourseUpdate[] = [
  {
    id: "case-lab-01",
    creatorId: "case-lab",
    courseTitle: "案例分析型课程包",
    moduleTitle: "新增：从材料事实到判断结论",
    summary: "用三个可替换案例拆开事实提取、规则定位和结论表达，附带一张分析路径图。",
    updatedAt: "2026-04-26T08:30:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 18,
    views: 7440,
    comments: 42,
    likes: 610,
    tags: ["案例", "推理", "迁移"],
    coverSeed: "case-analysis-path",
  },
  {
    id: "concept-bridge-01",
    creatorId: "concept-bridge",
    courseTitle: "概念解释型课程包",
    moduleTitle: "更新：概念关系的五个常见误区",
    summary: "把定义、适用条件和反例放在同一张讲义里，适合复习前快速校准。",
    updatedAt: "2026-04-26T07:40:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 24,
    views: 5820,
    comments: 31,
    likes: 428,
    tags: ["概念", "关系", "复习"],
    coverSeed: "concept-map-update",
  },
  {
    id: "project-studio-01",
    creatorId: "project-studio",
    courseTitle: "项目实战型课程包",
    moduleTitle: "新增：从任务目标到交付清单",
    summary: "从目标、约束、步骤三个层面入手，解释如何让学习产出和复盘指标一起演进。",
    updatedAt: "2026-04-25T21:18:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 31,
    views: 9100,
    comments: 67,
    likes: 820,
    tags: ["项目", "步骤", "复盘"],
    coverSeed: "project-delivery-system",
  },
  {
    id: "pen-review-01",
    creatorId: "pen-review",
    courseTitle: "高效笔记和资料库搭建",
    moduleTitle: "新增资料包：论文阅读标注模板",
    summary: "上传了三套可复用的阅读模板，覆盖概念卡片、证据摘录和复盘问题清单。",
    updatedAt: "2026-04-25T18:06:00.000+08:00",
    updateKind: "resource_added",
    lessonCount: 12,
    views: 4860,
    comments: 19,
    likes: 306,
    tags: ["笔记", "模板", "资料库"],
    coverSeed: "paper-note-kit",
  },
  {
    id: "source-map-01",
    creatorId: "source-map",
    courseTitle: "资料扩讲型课程包",
    moduleTitle: "课堂笔记：从资料片段到讲解主线",
    summary: "整理了目录、材料片段和数据表，补充了几个适合做课堂讨论的问题。",
    updatedAt: "2026-04-25T14:12:00.000+08:00",
    updateKind: "note_added",
    lessonCount: 9,
    views: 3520,
    comments: 26,
    likes: 211,
    tags: ["资料", "数据", "讨论课"],
    coverSeed: "source-context-data",
  },
  {
    id: "practice-daily-01",
    creatorId: "practice-daily",
    courseTitle: "每日练习型课程包",
    moduleTitle: "更新：第 18 天场景练习和复盘问题",
    summary: "新增可替换练习场景，配套慢速讲解、关键词卡片和 12 个迁移任务。",
    updatedAt: "2026-04-24T22:05:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 18,
    views: 6290,
    comments: 38,
    likes: 472,
    tags: ["练习", "场景", "迁移"],
    coverSeed: "practice-day-18",
  },
  {
    id: "case-lab-02",
    creatorId: "case-lab",
    courseTitle: "表达训练型课程包",
    moduleTitle: "新增：如何把材料事实改写成结构化回答",
    summary: "用评分标准反推答题结构，把事实提炼、依据定位和结论表达拆成三步。",
    updatedAt: "2026-04-24T10:35:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 15,
    views: 6880,
    comments: 35,
    likes: 540,
    tags: ["表达", "结构", "评分"],
    coverSeed: "structured-answer-writing",
  },
];

export function creatorAvatarUrl(creator: FollowedCreator) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(creator.avatarSeed)}`;
}

export function updateCoverUrl(update: FollowedCourseUpdate) {
  return `https://api.dicebear.com/9.x/shapes/svg?seed=${encodeURIComponent(update.coverSeed)}`;
}

export function buildFollowedCourseUpdateItems(): FollowedCourseUpdateItem[] {
  const creatorById = new Map(FOLLOWED_CREATORS.map((creator) => [creator.id, creator]));

  return FOLLOWED_COURSE_UPDATES.map((update) => {
    const creator = creatorById.get(update.creatorId);
    return creator ? { update, creator } : null;
  })
    .filter((item): item is FollowedCourseUpdateItem => item !== null)
    .sort((left, right) => new Date(right.update.updatedAt).getTime() - new Date(left.update.updatedAt).getTime());
}
