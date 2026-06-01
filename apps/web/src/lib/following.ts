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
  creator_id: string;
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
  new_lesson: "New lesson",
  course_revision: "Update",
  resource_added: "Resource",
  note_added: "Note",
};

export const FOLLOWED_CREATORS: FollowedCreator[] = [
  {
    id: "case-lab",
    name: "Case Lab",
    handle: "case-lab",
    bio: "Turns resources, cases, and classroom questions into transferable learning paths.",
    field: "Case learning",
    followers: 128400,
    avatarSeed: "case-lab",
    unreadCount: 3,
  },
  {
    id: "source-map",
    name: "Resource Map",
    handle: "source-map",
    bio: "Extracts outlines, evidence, and teaching cues from uploaded resources.",
    field: "Resource explanation",
    followers: 84200,
    avatarSeed: "source-map",
    unreadCount: 1,
  },
  {
    id: "project-studio",
    name: "Project Studio",
    handle: "project-studio",
    bio: "Breaks complex tasks into goals, steps, outputs, and review loops.",
    field: "Project learning",
    followers: 96300,
    avatarSeed: "project-studio",
    unreadCount: 2,
  },
  {
    id: "pen-review",
    name: "Notes Review",
    handle: "pen-review",
    bio: "Learning tools, note methods, and resource organization workflows.",
    field: "Learning methods",
    followers: 67200,
    avatarSeed: "pen-review",
    unreadCount: 1,
  },
  {
    id: "practice-daily",
    name: "Daily Practice",
    handle: "practice-daily",
    bio: "Turns any topic into daily executable practice.",
    field: "Practice training",
    followers: 73900,
    avatarSeed: "practice-daily",
    unreadCount: 0,
  },
  {
    id: "concept-bridge",
    name: "Concept Bridge",
    handle: "concept-bridge",
    bio: "Explains abstract concepts through definitions, relations, examples, and checks.",
    field: "Concept explanation",
    followers: 101800,
    avatarSeed: "concept-bridge",
    unreadCount: 4,
  },
];

export const FOLLOWED_COURSE_UPDATES: FollowedCourseUpdate[] = [
  {
    id: "case-lab-01",
    creator_id: "case-lab",
    courseTitle: "Case Analysis Course Package",
    moduleTitle: "New: From Material Facts to Judgments",
    summary: "Uses three replaceable cases to unpack fact extraction, rule location, and conclusion writing, with an analysis path map.",
    updatedAt: "2026-04-26T08:30:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 18,
    views: 7440,
    comments: 42,
    likes: 610,
    tags: ["Case", "Reasoning", "Transfer"],
    coverSeed: "case-analysis-path",
  },
  {
    id: "concept-bridge-01",
    creator_id: "concept-bridge",
    courseTitle: "Concept Explanation Course Package",
    moduleTitle: "Update: Five Common Concept-Relation Pitfalls",
    summary: "Places definitions, applicable conditions, and counterexamples in one handout for quick review calibration.",
    updatedAt: "2026-04-26T07:40:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 24,
    views: 5820,
    comments: 31,
    likes: 428,
    tags: ["Concepts", "Relations", "Review"],
    coverSeed: "concept-map-update",
  },
  {
    id: "project-studio-01",
    creator_id: "project-studio",
    courseTitle: "Project Practice Course Package",
    moduleTitle: "New: From Task Goals to Delivery Checklist",
    summary: "Explains how learning outputs and review metrics evolve together through goals, constraints, and steps.",
    updatedAt: "2026-04-25T21:18:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 31,
    views: 9100,
    comments: 67,
    likes: 820,
    tags: ["Project", "Steps", "Review"],
    coverSeed: "project-delivery-system",
  },
  {
    id: "pen-review-01",
    creator_id: "pen-review",
    courseTitle: "Efficient Notes and Resource Library Setup",
    moduleTitle: "New Resource Pack: Paper Reading Annotation Templates",
    summary: "Adds three reusable reading templates covering concept cards, evidence excerpts, and review question lists.",
    updatedAt: "2026-04-25T18:06:00.000+08:00",
    updateKind: "resource_added",
    lessonCount: 12,
    views: 4860,
    comments: 19,
    likes: 306,
    tags: ["Notes", "Templates", "Resources"],
    coverSeed: "paper-note-kit",
  },
  {
    id: "source-map-01",
    creator_id: "source-map",
    courseTitle: "Resource-Based Teaching Course Package",
    moduleTitle: "Class Notes: From Resource Fragments to Teaching Thread",
    summary: "Organizes outlines, material fragments, and data tables, with discussion prompts for class use.",
    updatedAt: "2026-04-25T14:12:00.000+08:00",
    updateKind: "note_added",
    lessonCount: 9,
    views: 3520,
    comments: 26,
    likes: 211,
    tags: ["Resources", "Data", "Discussion"],
    coverSeed: "source-context-data",
  },
  {
    id: "practice-daily-01",
    creator_id: "practice-daily",
    courseTitle: "Daily Practice Course Package",
    moduleTitle: "Update: Day 18 Scenario Practice and Review Questions",
    summary: "Adds replaceable practice scenarios with slow walkthroughs, keyword cards, and 12 transfer tasks.",
    updatedAt: "2026-04-24T22:05:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 18,
    views: 6290,
    comments: 38,
    likes: 472,
    tags: ["Practice", "Scenarios", "Transfer"],
    coverSeed: "practice-day-18",
  },
  {
    id: "case-lab-02",
    creator_id: "case-lab",
    courseTitle: "Expression Training Course Package",
    moduleTitle: "New: Turning Material Facts into Structured Answers",
    summary: "Works backward from scoring criteria to split fact extraction, evidence location, and conclusion writing into three steps.",
    updatedAt: "2026-04-24T10:35:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 15,
    views: 6880,
    comments: 35,
    likes: 540,
    tags: ["Expression", "Structure", "Scoring"],
    coverSeed: "structured-answer-writing",
  },
];

export function creatorAvatarUrl(creator: FollowedCreator) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(creator.avatarSeed)}`;
}

export function buildFollowedCourseUpdateItems(): FollowedCourseUpdateItem[] {
  const creatorById = new Map(FOLLOWED_CREATORS.map((creator) => [creator.id, creator]));

  return FOLLOWED_COURSE_UPDATES.map((update) => {
    const creator = creatorById.get(update.creator_id);
    return creator ? { update, creator } : null;
  })
    .filter((item): item is FollowedCourseUpdateItem => item !== null)
    .sort((left, right) => new Date(right.update.updatedAt).getTime() - new Date(left.update.updatedAt).getTime());
}
