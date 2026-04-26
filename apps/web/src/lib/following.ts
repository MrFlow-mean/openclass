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
    id: "civil-moon",
    name: "春山小月亮",
    handle: "civil-moon",
    bio: "用案例讲民法、合同和侵权责任。",
    field: "法律课程",
    followers: 128400,
    avatarSeed: "civil-moon",
    unreadCount: 3,
  },
  {
    id: "nav-bclass",
    name: "NAV 看广州 B 站版",
    handle: "nav-bclass",
    bio: "城市观察、公共议题和开放资料课程。",
    field: "社会观察",
    followers: 84200,
    avatarSeed: "nav-bclass",
    unreadCount: 1,
  },
  {
    id: "react-uncle",
    name: "本子在隔壁",
    handle: "react-uncle",
    bio: "前端工程和 React 设计系统实战。",
    field: "前端工程",
    followers: 96300,
    avatarSeed: "react-uncle",
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
    id: "logic-daily",
    name: "每天学点法语听说读写",
    handle: "logic-daily",
    bio: "语言学习路径和每日可执行练习。",
    field: "语言学习",
    followers: 73900,
    avatarSeed: "logic-daily",
    unreadCount: 0,
  },
  {
    id: "math-proof",
    name: "乐与侃数学",
    handle: "math-proof",
    bio: "数学证明、线代和概率论讲义。",
    field: "数学",
    followers: 101800,
    avatarSeed: "math-proof",
    unreadCount: 4,
  },
];

export const FOLLOWED_COURSE_UPDATES: FollowedCourseUpdate[] = [
  {
    id: "civil-moon-01",
    creatorId: "civil-moon",
    courseTitle: "民法请求权基础训练营",
    moduleTitle: "新增：合同解除后的返还与损害赔偿",
    summary: "用三个真实案例拆开解除权、返还义务和可得利益损失，附带一张请求权检索图。",
    updatedAt: "2026-04-26T08:30:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 18,
    views: 7440,
    comments: 42,
    likes: 610,
    tags: ["民法", "合同编", "案例"],
    coverSeed: "civil-contract-return",
  },
  {
    id: "math-proof-01",
    creatorId: "math-proof",
    courseTitle: "线性代数证明课",
    moduleTitle: "更新：特征值与对角化的五个常见误区",
    summary: "把对角化条件、几何重数和最小多项式放在同一张讲义里，适合复习前快速校准。",
    updatedAt: "2026-04-26T07:40:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 24,
    views: 5820,
    comments: 31,
    likes: 428,
    tags: ["线性代数", "证明", "复习"],
    coverSeed: "matrix-proof-update",
  },
  {
    id: "react-uncle-01",
    creatorId: "react-uncle",
    courseTitle: "React 设计系统实战",
    moduleTitle: "新增：组件变体和 token 命名规范",
    summary: "从 Button、Tabs、Dialog 三个组件入手，解释如何让设计变量和代码 API 一起演进。",
    updatedAt: "2026-04-25T21:18:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 31,
    views: 9100,
    comments: 67,
    likes: 820,
    tags: ["React", "Design System", "TypeScript"],
    coverSeed: "react-token-system",
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
    id: "nav-bclass-01",
    creatorId: "nav-bclass",
    courseTitle: "城市公共议题观察课",
    moduleTitle: "课堂笔记：从交通数据看城市生活半径",
    summary: "整理了地图案例和数据表，补充了几个适合做课堂讨论的问题。",
    updatedAt: "2026-04-25T14:12:00.000+08:00",
    updateKind: "note_added",
    lessonCount: 9,
    views: 3520,
    comments: 26,
    likes: 211,
    tags: ["城市", "数据", "讨论课"],
    coverSeed: "city-radius-data",
  },
  {
    id: "logic-daily-01",
    creatorId: "logic-daily",
    courseTitle: "法语听说读写 100 天",
    moduleTitle: "更新：第 18 天情景对话和跟读音频",
    summary: "新增餐厅点单情景，配套慢速音频、关键词卡片和 12 个替换练习。",
    updatedAt: "2026-04-24T22:05:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 18,
    views: 6290,
    comments: 38,
    likes: 472,
    tags: ["法语", "听力", "口语"],
    coverSeed: "french-day-18",
  },
  {
    id: "civil-moon-02",
    creatorId: "civil-moon",
    courseTitle: "法考主观题表达课",
    moduleTitle: "新增：如何把案例事实改写成三段论",
    summary: "用主观题评分标准反推答题结构，把事实提炼、规范定位和结论表达拆成三步。",
    updatedAt: "2026-04-24T10:35:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 15,
    views: 6880,
    comments: 35,
    likes: 540,
    tags: ["法考", "主观题", "表达"],
    coverSeed: "law-exam-writing",
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
