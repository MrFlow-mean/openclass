import type { CommitRecord, Lesson, ResourceLibraryItem } from "@/types";

export type RecentFeedKind = "commit" | "resource";
export type RecentFeedFilter = "all" | RecentFeedKind;

export type RecentFeedLesson = {
  lesson: Lesson;
  packageTitle: string;
};

export type RecentFeedResource = {
  resource: ResourceLibraryItem;
  packageTitle: string;
};

export type RecentFeedItem = {
  id: string;
  kind: RecentFeedKind;
  timestamp: string;
  actor: string;
  action: string;
  title: string;
  detailTitle: string;
  detailBody: string;
  pills: string[];
  lessonId?: string;
};

function truncateText(value: string, maxLength = 160) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  if (normalized.length <= maxLength) {
    return normalized;
  }

  return `${normalized.slice(0, maxLength).trimEnd()}...`;
}

function humanizeCommitLabel(label: string) {
  switch (label) {
    case "Initial document draft":
      return "初始课程草稿";
    case "Manual document edit":
      return "手动编辑已保存";
    case "Restore snapshot":
      return "恢复历史快照";
    case "AI document edit":
      return "AI 更新文稿";
    case "Cloned lesson snapshot":
      return "克隆课程快照";
    default:
      return label;
  }
}

function humanizeCommitMessage(commit: CommitRecord, lesson: Lesson) {
  const normalized = commit.message.trim();

  if (!normalized) {
    return `已更新《${lesson.title}》的课程内容，可以继续进入工作台完善讲义与分支。`;
  }

  const rewritten = normalized
    .replace(/^Generated starter rich document for\s+/i, "已生成课程初稿：")
    .replace(/^Saved Word-like rich document changes from the editor$/i, "已保存 Word 风格编辑器中的文稿改动。")
    .replace(/^Saved rich document changes from the editor$/i, "已保存编辑器中的文稿改动。")
    .replace(/^Cloned lesson into an isolated workspace$/i, "已复制到独立工作区，方便继续扩展。");

  return truncateText(rewritten, 180);
}

function resourceSummary(resource: ResourceLibraryItem) {
  return resource.outline[0]?.summary ?? "已进入资料库，可在工作台中继续引用和扩展。";
}

function resourceTypeLabel(resource: ResourceLibraryItem) {
  if (resource.mime_type.includes("pdf")) {
    return "PDF";
  }
  if (resource.mime_type.includes("word") || resource.mime_type.includes("document")) {
    return "Word";
  }
  if (resource.mime_type.startsWith("image/")) {
    return "图片";
  }

  return resource.resource_type || "资料";
}

export function buildRecentFeed(lessons: RecentFeedLesson[], resources: RecentFeedResource[]) {
  const commitItems: RecentFeedItem[] = lessons.flatMap(({ lesson, packageTitle }) =>
    lesson.history_graph.commits.map((commit) => ({
      id: `commit:${commit.id}`,
      kind: "commit",
      timestamp: commit.created_at,
      actor: lesson.title,
      action: "更新了课程文稿",
      title: humanizeCommitLabel(commit.label),
      detailTitle: commit.branch_name === "main" ? "主分支 main" : `分支 ${commit.branch_name}`,
      detailBody: humanizeCommitMessage(commit, lesson),
      pills: [packageTitle, lesson.tags[0] ?? "课程内容", `${lesson.history_graph.commits.length} 次提交`],
      lessonId: lesson.id,
    }))
  );

  const resourceItems: RecentFeedItem[] = resources.map(({ resource, packageTitle }) => ({
    id: `resource:${resource.id}`,
    kind: "resource",
    timestamp: resource.uploaded_at,
    actor: packageTitle,
    action: "收录了新资料",
    title: resource.name,
    detailTitle: resource.outline[0]?.title ?? "资料摘要",
    detailBody: truncateText(resourceSummary(resource), 180),
    pills: [
      resourceTypeLabel(resource),
      resource.outline.length ? `${resource.outline.length} 个索引片段` : "等待生成索引",
    ],
  }));

  return [...commitItems, ...resourceItems].sort(
    (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime()
  );
}
