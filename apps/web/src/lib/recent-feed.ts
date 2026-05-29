import type { CommitRecord, Lesson, ResourceLibraryItem } from "@/types";

export type RecentFeedKind = "commit" | "resource";
export type RecentFeedFilter = "all" | RecentFeedKind;

export type RecentFeedLesson = {
  lesson: Lesson;
  packageId: string;
  packageTitle: string;
  isStandalone?: boolean;
};

export type RecentFeedResource = {
  resource: ResourceLibraryItem;
  packageTitle: string;
};

export type RecentFeedUpdate = {
  id: string;
  timestamp: string;
  title: string;
  detailTitle: string;
  detailBody: string;
  lessonTitle?: string;
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
  updates?: RecentFeedUpdate[];
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
      return "Initial course draft";
    case "Manual document edit":
      return "Manual edit saved";
    case "Restore snapshot":
      return "Historical snapshot restored";
    case "AI document edit":
      return "AI updated the notes";
    case "Cloned lesson snapshot":
      return "Course snapshot cloned";
    default:
      return label;
  }
}

function humanizeCommitMessage(commit: CommitRecord, lesson: Lesson) {
  const normalized = commit.message.trim();

  if (!normalized) {
    return `${lesson.title} was updated. Continue in Studio to refine notes and branches.`;
  }

  const rewritten = normalized
    .replace(/^Generated starter rich document for\s+/i, "Generated starter draft for ")
    .replace(/^Saved Word-like rich document changes from the editor$/i, "Saved Word-style editor changes.")
    .replace(/^Saved rich document changes from the editor$/i, "Saved editor changes.")
    .replace(/^Cloned lesson into an isolated workspace$/i, "Copied into an isolated workspace for follow-up.");

  return truncateText(rewritten, 180);
}

function resourceSummary(resource: ResourceLibraryItem) {
  return resource.outline[0]?.summary ?? "Added to the resource library for citation and expansion in Studio.";
}

function resourceTypeLabel(resource: ResourceLibraryItem) {
  if (resource.mime_type.includes("pdf")) {
    return "PDF";
  }
  if (resource.mime_type.includes("word") || resource.mime_type.includes("document")) {
    return "Word";
  }
  if (resource.mime_type.startsWith("image/")) {
    return "Image";
  }

  return resource.resource_type || "Resource";
}

export function buildRecentFeed(lessons: RecentFeedLesson[], resources: RecentFeedResource[]) {
  const commitGroups = new Map<
    string,
    {
      id: string;
      packageTitle: string;
      isStandalone: boolean;
      updates: RecentFeedUpdate[];
      lessonIdsByUpdateId: Map<string, string>;
      lessonTitles: Set<string>;
      tags: Set<string>;
    }
  >();

  lessons.forEach(({ lesson, packageId, packageTitle, isStandalone = false }) => {
    const groupId = isStandalone ? `lesson:${lesson.id}` : `package:${packageId}`;
    const group =
      commitGroups.get(groupId) ??
      {
        id: groupId,
        packageTitle,
        isStandalone,
        updates: [],
        lessonIdsByUpdateId: new Map<string, string>(),
        lessonTitles: new Set<string>(),
        tags: new Set<string>(),
      };

    lesson.history_graph.commits.forEach((commit) => {
      const update: RecentFeedUpdate = {
        id: `commit:${commit.id}`,
        timestamp: commit.created_at,
        title: humanizeCommitLabel(commit.label),
        detailTitle: commit.branch_name === "main" ? "Main branch" : `Branch ${commit.branch_name}`,
        detailBody: humanizeCommitMessage(commit, lesson),
        lessonTitle: lesson.title,
      };

      group.updates.push(update);
      group.lessonIdsByUpdateId.set(update.id, lesson.id);
    });

    group.lessonTitles.add(lesson.title);
    if (lesson.tags[0]) {
      group.tags.add(lesson.tags[0]);
    }
    commitGroups.set(groupId, group);
  });

  const commitItems: RecentFeedItem[] = Array.from(commitGroups.values()).flatMap((group) => {
    const updates = [...group.updates].sort(
      (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime()
    );
    const latestUpdate = updates[0];

    if (!latestUpdate) {
      return [];
    }

    const commitCount = updates.length;
    const lessonCount = group.lessonTitles.size;
    const actor =
      group.isStandalone && lessonCount === 1
        ? Array.from(group.lessonTitles)[0] ?? group.packageTitle
        : group.packageTitle;
    const lessonPill = lessonCount > 1 ? `${lessonCount} lesson pages` : latestUpdate.lessonTitle ?? "Course content";
    const tagPill = Array.from(group.tags)[0] ?? "Course content";

    return [
      {
        id: `commit-group:${group.id}`,
        kind: "commit",
        timestamp: latestUpdate.timestamp,
        actor,
        action: commitCount > 1 ? `${commitCount} lesson note updates` : "Updated lesson notes",
        title: commitCount > 1 ? "Recent updates" : latestUpdate.title,
        detailTitle: latestUpdate.detailTitle,
        detailBody: latestUpdate.detailBody,
        pills: [group.packageTitle, lessonPill, tagPill, `${commitCount} commits`],
        lessonId: group.lessonIdsByUpdateId.get(latestUpdate.id),
        updates,
      } satisfies RecentFeedItem,
    ];
  });

  const resourceItems: RecentFeedItem[] = resources.map(({ resource, packageTitle }) => ({
    id: `resource:${resource.id}`,
    kind: "resource",
    timestamp: resource.uploaded_at,
    actor: packageTitle,
    action: "Added a resource",
    title: resource.name,
    detailTitle: resource.outline[0]?.title ?? "Resource summary",
    detailBody: truncateText(resourceSummary(resource), 180),
    pills: [
      resourceTypeLabel(resource),
      resource.outline.length ? `${resource.outline.length} indexed sections` : "Waiting for index",
    ],
  }));

  return [...commitItems, ...resourceItems].sort(
    (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime()
  );
}
