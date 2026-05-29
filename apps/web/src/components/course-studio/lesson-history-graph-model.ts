import {
  compactText,
  currentHeadCommitId,
  getLessonCommit,
  metadataBool,
  metadataText,
} from "@/components/course-studio/history-utils";
import type { CommitRecord, Lesson } from "@/types";

export type GraphBranch = {
  name: string;
  headCommitId: string;
  baseCommitId: string;
  isCurrent: boolean;
};

export type GraphNode = {
  commit: CommitRecord;
  index: number;
  lane: number;
  x: number;
  y: number;
  branchLabels: GraphBranch[];
  isCurrentHead: boolean;
  isPreviewed: boolean;
  isBranchHead: boolean;
  kindLabel: string;
  title: string;
  summary: string;
  detail: string;
};

export type GraphEdge = {
  id: string;
  parentId: string;
  childId: string;
  parentX: number;
  parentY: number;
  childX: number;
  childY: number;
  sameLane: boolean;
};

export type GraphBranchSprout = {
  id: string;
  branch: GraphBranch;
  baseCommitId: string;
  baseX: number;
  baseY: number;
  x: number;
  y: number;
  labelX: number;
  labelY: number;
};

export type LessonHistoryGraphViewModel = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  branchSprouts: GraphBranchSprout[];
  branches: GraphBranch[];
  laneCount: number;
  contentLeft: number;
  graphWidth: number;
  graphHeight: number;
  currentHeadCommitId: string | null;
};

const LANE_WIDTH = 38;
const ROW_HEIGHT = 78;
const NODE_TOP = 34;
const NODE_LEFT = 18;
const CONTENT_GAP = 48;
const BRANCH_SPROUT_RISE = 30;
const MIN_GRAPH_WIDTH = 300;

function sortCommitsForGraph(commits: CommitRecord[]) {
  return commits
    .map((commit, originalIndex) => ({ commit, originalIndex }))
    .sort((left, right) => {
      const timeDelta = new Date(right.commit.created_at).getTime() - new Date(left.commit.created_at).getTime();
      if (timeDelta !== 0) {
        return timeDelta;
      }
      return right.originalIndex - left.originalIndex;
    })
    .map((item) => item.commit);
}

function branchLaneOrder(lesson: Lesson) {
  const branchRefs = Object.values(lesson.history_graph.branches);
  const branchNames = new Set<string>();
  lesson.history_graph.commits.forEach((commit) => branchNames.add(commit.branch_name));
  branchRefs.forEach((branch) => branchNames.add(branch.name));

  const createdAtByBranch = new Map(branchRefs.map((branch) => [branch.name, branch.created_at]));
  return Array.from(branchNames).sort((left, right) => {
    if (left === "main") {
      return -1;
    }
    if (right === "main") {
      return 1;
    }
    const timeDelta =
      new Date(createdAtByBranch.get(left) ?? "").getTime() -
      new Date(createdAtByBranch.get(right) ?? "").getTime();
    if (Number.isFinite(timeDelta) && timeDelta !== 0) {
      return timeDelta;
    }
    return left.localeCompare(right, "zh-CN", { numeric: true });
  });
}

function commitKindLabel(commit: CommitRecord) {
  const kind = String(commit.metadata?.kind ?? "");
  if (kind === "chat_flow") {
    return "Chat";
  }
  if (kind === "board_document_generation") {
    return "Board";
  }
  if (kind === "board_document_edit") {
    return "Edit";
  }
  if (kind === "restore_snapshot") {
    return "Restore";
  }
  if (kind === "branch_merge") {
    return "Merge";
  }
  if (metadataBool(commit, "autosave") || kind === "auto_document_save") {
    return "Auto";
  }
  return "Commit";
}

function commitTitle(commit: CommitRecord) {
  const userMessage = metadataText(commit, "user_message");
  if (userMessage) {
    return compactText(userMessage, 74);
  }
  return compactText(commit.label || commit.message || "History commit", 74);
}

function commitSummary(commit: CommitRecord) {
  const assistantMessage = metadataText(commit, "assistant_message");
  if (assistantMessage) {
    return compactText(assistantMessage, 120);
  }
  const snapshotText = commit.snapshot.content_text || commit.snapshot.title || "";
  return compactText(commit.message || snapshotText || "This node has no additional summary.", 120);
}

function commitDetail(commit: CommitRecord) {
  const userMessage = metadataText(commit, "user_message");
  const assistantMessage = metadataText(commit, "assistant_message");
  if (userMessage && assistantMessage) {
    return `User: ${compactText(userMessage, 120)}\nAI: ${compactText(assistantMessage, 180)}`;
  }
  if (userMessage) {
    return `User: ${compactText(userMessage, 220)}`;
  }
  return compactText(commit.message || commit.snapshot.content_text || commit.snapshot.title || "", 260);
}

export function buildLessonHistoryGraphModel(
  lesson: Lesson,
  previewCommitId: string | null
): LessonHistoryGraphViewModel {
  const orderedCommits = sortCommitsForGraph(lesson.history_graph.commits);
  const laneNames = branchLaneOrder(lesson);
  const laneByBranch = new Map(laneNames.map((branchName, index) => [branchName, index]));
  const graphLaneCount = Math.max(1, laneNames.length);
  const contentLeft = graphLaneCount * LANE_WIDTH + CONTENT_GAP;
  const graphWidth = Math.max(MIN_GRAPH_WIDTH, contentLeft + 176);
  const graphHeight = Math.max(140, orderedCommits.length * ROW_HEIGHT + 18);
  const headCommitId = currentHeadCommitId(lesson);
  const branches = Object.values(lesson.history_graph.branches).map((branch) => ({
    name: branch.name,
    headCommitId: branch.head_commit_id,
    baseCommitId: branch.base_commit_id,
    isCurrent: branch.name === lesson.history_graph.current_branch,
  }));
  const branchHeadsByCommit = new Map<string, GraphBranch[]>();
  branches.forEach((branch) => {
    const next = branchHeadsByCommit.get(branch.headCommitId) ?? [];
    next.push(branch);
    branchHeadsByCommit.set(branch.headCommitId, next);
  });

  const nodes = orderedCommits.map((commit, index) => {
    const lane = laneByBranch.get(commit.branch_name) ?? 0;
    const branchLabels = (branchHeadsByCommit.get(commit.id) ?? []).sort((left, right) =>
      left.name.localeCompare(right.name, "zh-CN", { numeric: true })
    );
    return {
      commit,
      index,
      lane,
      x: NODE_LEFT + lane * LANE_WIDTH,
      y: NODE_TOP + index * ROW_HEIGHT,
      branchLabels,
      isCurrentHead: commit.id === headCommitId,
      isPreviewed: commit.id === previewCommitId,
      isBranchHead: branchLabels.length > 0,
      kindLabel: commitKindLabel(commit),
      title: commitTitle(commit),
      summary: commitSummary(commit),
      detail: commitDetail(commit),
    };
  });

  const nodeById = new Map(nodes.map((node) => [node.commit.id, node]));
  const sproutCountByBase = new Map<string, number>();
  const branchSprouts = branches.flatMap((branch) => {
    if (branch.name === "main" || branch.headCommitId !== branch.baseCommitId) {
      return [];
    }
    const baseNode = nodeById.get(branch.baseCommitId);
    const branchLane = laneByBranch.get(branch.name);
    if (!baseNode || branchLane == null || branchLane === baseNode.lane) {
      return [];
    }
    const sproutIndex = sproutCountByBase.get(branch.baseCommitId) ?? 0;
    sproutCountByBase.set(branch.baseCommitId, sproutIndex + 1);
    const x = NODE_LEFT + branchLane * LANE_WIDTH;
    const y = Math.max(14, baseNode.y - BRANCH_SPROUT_RISE - sproutIndex * 14);
    return [
      {
        id: `${branch.name}:${branch.baseCommitId}:sprout`,
        branch,
        baseCommitId: branch.baseCommitId,
        baseX: baseNode.x,
        baseY: baseNode.y,
        x,
        y,
        labelX: x + 9,
        labelY: y - 8,
      },
    ];
  });
  const edges = nodes.flatMap((childNode) =>
    childNode.commit.parent_ids.flatMap((parentId, parentIndex) => {
      const parentNode = nodeById.get(parentId);
      if (!parentNode) {
        return [];
      }
      return [
        {
          id: `${parentId}:${childNode.commit.id}:${parentIndex}`,
          parentId,
          childId: childNode.commit.id,
          parentX: parentNode.x,
          parentY: parentNode.y,
          childX: childNode.x,
          childY: childNode.y,
          sameLane: parentNode.lane === childNode.lane,
        },
      ];
    })
  );

  return {
    nodes,
    edges,
    branchSprouts,
    branches,
    laneCount: graphLaneCount,
    contentLeft,
    graphWidth,
    graphHeight,
    currentHeadCommitId: headCommitId,
  };
}

export function graphNodeForCommit(
  model: LessonHistoryGraphViewModel,
  commitId: string | null | undefined
): GraphNode | null {
  if (!commitId) {
    return null;
  }
  return model.nodes.find((node) => node.commit.id === commitId) ?? null;
}

export function graphHeadCommit(lesson: Lesson, branchName: string): CommitRecord | null {
  const branch = lesson.history_graph.branches[branchName];
  return getLessonCommit(lesson, branch?.head_commit_id);
}
