"use client";

import { useMemo } from "react";
import clsx from "clsx";
import { BookOpen, BrainCircuit, Clock3, GitBranch, GitMerge } from "lucide-react";

import { BranchMergeReviewCard } from "@/components/course-studio/branch-merge-review-card";
import {
  buildLessonHistoryGraphModel,
  graphHeadCommit,
  graphNodeForCommit,
  type GraphBranchSprout,
  type GraphEdge,
  type GraphNode,
} from "@/components/course-studio/lesson-history-graph-model";
import { compactText, formatDate } from "@/components/course-studio/history-utils";
import type {
  BoardDecision,
  CommitRecord,
  Lesson,
  MergeBranchChoice,
  MergeBranchChoices,
  MergeBranchPreviewResponse,
  MergeBranchSectionKey,
} from "@/types";

type LessonHistoryGraphPanelProps = {
  activeLesson: Lesson;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  mergePreview: MergeBranchPreviewResponse | null;
  mergeChoices: MergeBranchChoices;
  busyAction: string | null;
  onNewBranchNameChange: (value: string) => void;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onOpenMergePreview: (branchName: string) => void | Promise<void>;
  onMergeChoiceChange: (section: MergeBranchSectionKey, choice: MergeBranchChoice) => void;
  onCancelMerge: () => void;
  onConfirmMerge: () => void | Promise<void>;
};

function nodeTone(node: GraphNode) {
  if (node.isCurrentHead) {
    return "border-black bg-black text-white";
  }
  if (node.isPreviewed) {
    return "border-blue-500 bg-blue-500 text-white";
  }
  if (node.isBranchHead) {
    return "border-gray-700 bg-white text-gray-950";
  }
  return "border-gray-300 bg-white text-gray-500";
}

function edgeColor(sameLane: boolean) {
  return sameLane ? "#111827" : "#9ca3af";
}

function edgePath(edge: GraphEdge) {
  if (edge.sameLane) {
    return `M ${edge.parentX} ${edge.parentY} L ${edge.childX} ${edge.childY}`;
  }
  return `M ${edge.parentX} ${edge.parentY} C ${edge.childX} ${edge.parentY}, ${edge.childX} ${edge.childY}, ${edge.childX} ${edge.childY}`;
}

function sproutPath(sprout: GraphBranchSprout) {
  return `M ${sprout.baseX} ${sprout.baseY} C ${sprout.x} ${sprout.baseY}, ${sprout.x} ${sprout.y}, ${sprout.x} ${sprout.y}`;
}

function sproutColor(sprout: GraphBranchSprout) {
  return sprout.branch.isCurrent ? "#111827" : "#2563eb";
}

export function LessonHistoryGraphPanel({
  activeLesson,
  previewCommitId,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  mergePreview,
  mergeChoices,
  busyAction,
  onNewBranchNameChange,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onOpenMergePreview,
  onMergeChoiceChange,
  onCancelMerge,
  onConfirmMerge,
}: LessonHistoryGraphPanelProps) {
  const model = useMemo(
    () => buildLessonHistoryGraphModel(activeLesson, previewCommitId),
    [activeLesson, previewCommitId]
  );
  const selectedNode =
    graphNodeForCommit(model, previewCommitId) ??
    graphNodeForCommit(model, model.currentHeadCommitId) ??
    model.nodes[0] ??
    null;

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">历史分支图</p>
            <p className="mt-1 text-sm font-semibold text-gray-950">{activeLesson.history_graph.current_branch}</p>
          </div>
          <div className="flex shrink-0 gap-1 text-[10px] font-semibold text-gray-500">
            <span className="rounded-md border border-gray-200 bg-white px-2 py-1">
              {model.nodes.length} commits
            </span>
            <span className="rounded-md border border-gray-200 bg-white px-2 py-1">
              {model.branches.length} branches
            </span>
          </div>
        </div>

        <div className="flex flex-wrap gap-1">
          {model.branches.map((branch) => {
            const headCommit = graphHeadCommit(activeLesson, branch.name);
            return (
              <div
                key={branch.name}
                className={clsx(
                  "inline-flex h-5 items-center overflow-hidden rounded border",
                  branch.isCurrent ? "border-black bg-black" : "border-gray-200 bg-white"
                )}
              >
                <button
                  type="button"
                  onClick={() => void onSwitchBranch(branch.name)}
                  disabled={branch.isCurrent}
                  title={headCommit ? `${branch.name}: ${headCommit.label}` : branch.name}
                  className={clsx(
                    "inline-flex h-full max-w-[92px] items-center gap-0.5 px-1.5 text-[9px] font-bold transition",
                    branch.isCurrent
                      ? "text-white"
                      : "text-gray-500 hover:bg-gray-50 hover:text-gray-950"
                  )}
                >
                  <GitBranch className="h-2.5 w-2.5 shrink-0" />
                  <span className="truncate">{compactText(branch.name, 16)}</span>
                </button>
                {!branch.isCurrent ? (
                  <button
                    type="button"
                    data-testid="history-branch-merge"
                    onClick={() => void onOpenMergePreview(branch.name)}
                    title={`合并 ${branch.name}`}
                    aria-label={`合并分支 ${branch.name}`}
                    className="flex h-full w-5 items-center justify-center border-l border-gray-200 text-gray-400 transition hover:bg-gray-50 hover:text-gray-950"
                  >
                    <GitMerge className="h-2.5 w-2.5" />
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>

      <section className="overflow-x-auto rounded-lg border border-gray-200 bg-white custom-scrollbar">
        <div
          className="relative"
          style={{
            width: `${model.graphWidth}px`,
            height: `${model.graphHeight}px`,
          }}
        >
          <svg
            className="pointer-events-none absolute inset-0"
            width={model.graphWidth}
            height={model.graphHeight}
            aria-hidden="true"
          >
            {model.edges.map((edge) => (
              <path
                key={edge.id}
                d={edgePath(edge)}
                stroke={edgeColor(edge.sameLane)}
                strokeWidth={edge.sameLane ? 2 : 1.5}
                strokeLinecap="round"
                fill="none"
                opacity={edge.sameLane ? 0.8 : 0.65}
              />
            ))}
            {model.branchSprouts.map((sprout) => (
              <path
                key={sprout.id}
                data-testid="history-branch-sprout"
                d={sproutPath(sprout)}
                stroke={sproutColor(sprout)}
                strokeWidth={2}
                strokeLinecap="round"
                fill="none"
                opacity={sprout.branch.isCurrent ? 0.9 : 0.75}
              />
            ))}
          </svg>

          {model.branchSprouts.map((sprout) => (
            <button
              key={`${sprout.id}:dot`}
              type="button"
              onClick={() => void onSwitchBranch(sprout.branch.name)}
              disabled={sprout.branch.isCurrent}
              title={`切换到 ${sprout.branch.name}`}
              aria-label={`切换到分支 ${sprout.branch.name}`}
              className={clsx(
                "absolute z-20 flex h-4 w-4 items-center justify-center rounded-full border-2 bg-white shadow-sm transition hover:scale-110",
                sprout.branch.isCurrent ? "border-black text-black" : "border-blue-600 text-blue-600"
              )}
              style={{
                left: `${sprout.x - 8}px`,
                top: `${sprout.y - 8}px`,
              }}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
            </button>
          ))}

          {model.branchSprouts.map((sprout) => (
            <button
              key={`${sprout.id}:label`}
              data-testid="history-branch-sprout-label"
              type="button"
              onClick={() => void onSwitchBranch(sprout.branch.name)}
              disabled={sprout.branch.isCurrent}
              title={`切换到 ${sprout.branch.name}`}
              className={clsx(
                "absolute z-20 inline-flex h-4 max-w-[72px] items-center gap-0.5 rounded border px-1 text-[8px] font-bold transition",
                sprout.branch.isCurrent
                  ? "border-black bg-black text-white"
                  : "border-blue-100 bg-blue-50 text-blue-700 hover:border-blue-200 hover:bg-blue-100"
              )}
              style={{
                left: `${sprout.labelX}px`,
                top: `${sprout.labelY}px`,
              }}
            >
              <GitBranch className="h-2.5 w-2.5 shrink-0" />
              <span className="truncate">{compactText(sprout.branch.name, 14)}</span>
            </button>
          ))}

          {model.nodes.map((node) => (
            <button
              key={node.commit.id}
              type="button"
              onClick={() => void onPreviewCommit(node.commit)}
              title={`${node.kindLabel}: ${node.title}`}
              aria-label={`查看历史节点 ${node.title}`}
              className={clsx(
                "absolute z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 shadow-sm transition hover:scale-110",
                nodeTone(node)
              )}
              style={{
                left: `${node.x - 10}px`,
                top: `${node.y - 10}px`,
              }}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
            </button>
          ))}

          {model.nodes.map((node) => (
            <div
              key={`${node.commit.id}:card`}
              className={clsx(
                "absolute rounded-md border bg-white px-2.5 py-1.5 shadow-sm transition",
                node.isPreviewed
                  ? "border-blue-200 ring-1 ring-blue-200"
                  : node.isCurrentHead
                    ? "border-gray-900"
                    : "border-gray-200"
              )}
              style={{
                left: `${model.contentLeft}px`,
                top: `${node.y - 22}px`,
                width: `${Math.max(168, model.graphWidth - model.contentLeft - 12)}px`,
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-gray-500">
                      {node.kindLabel}
                    </span>
                    {node.isCurrentHead ? (
                      <span className="rounded bg-black px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
                        Head
                      </span>
                    ) : null}
                    {node.isPreviewed ? (
                      <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-blue-700">
                        Preview
                      </span>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => void onPreviewCommit(node.commit)}
                    className="mt-1 block max-w-full truncate text-left text-xs font-semibold text-gray-950 transition hover:text-blue-700"
                  >
                    {node.title}
                  </button>
                </div>
                <span className="shrink-0 text-[10px] text-gray-400">{formatDate(node.commit.created_at)}</span>
              </div>
              <p className="mt-1 line-clamp-1 text-[11px] leading-5 text-gray-500">{node.summary}</p>
              {node.branchLabels.length ? (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {node.branchLabels.map((branch) => (
                    <button
                      key={branch.name}
                      type="button"
                      onClick={() => void onSwitchBranch(branch.name)}
                      disabled={branch.isCurrent}
                      title={`切换到 ${branch.name}`}
                      className={clsx(
                        "inline-flex h-4 max-w-[78px] items-center gap-0.5 rounded px-1 text-[8px] font-bold transition",
                        branch.isCurrent
                          ? "bg-black text-white"
                          : "bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-950"
                      )}
                    >
                      <GitBranch className="h-2.5 w-2.5 shrink-0" />
                      <span className="truncate">{compactText(branch.name, 14)}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      {selectedNode ? (
        <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">选中节点</p>
              <h5 className="mt-1 break-words text-sm font-semibold text-gray-950">{selectedNode.title}</h5>
            </div>
            <span className="shrink-0 rounded bg-gray-100 px-2 py-1 text-[10px] font-bold text-gray-500">
              {selectedNode.kindLabel}
            </span>
          </div>
          <p className="mt-3 whitespace-pre-wrap text-xs leading-6 text-gray-600">
            {selectedNode.detail || compactText(selectedNode.commit.message, 220)}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void onPreviewCommit(selectedNode.commit)}
              className="inline-flex h-8 items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300 hover:text-gray-950"
            >
              <BookOpen className="h-3.5 w-3.5" />
              Preview
            </button>
            <button
              type="button"
              onClick={() => void onRestoreCommit(selectedNode.commit.id)}
              className="inline-flex h-8 items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300 hover:text-gray-950"
            >
              <Clock3 className="h-3.5 w-3.5" />
              Restore
            </button>
            <button
              type="button"
              onClick={() => void onCreateBranchFromCommit(selectedNode.commit)}
              className="inline-flex h-8 items-center gap-1 rounded-md bg-gray-950 px-2.5 text-[10px] font-bold uppercase tracking-wider text-white transition hover:bg-black"
            >
              <GitBranch className="h-3.5 w-3.5" />
              Branch
            </button>
          </div>
          <div className="mt-3 flex gap-2">
            <input
              value={newBranchName}
              onChange={(event) => onNewBranchNameChange(event.target.value)}
              placeholder="新分支名"
              className="min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-3 py-2 text-xs outline-none transition focus:border-gray-500"
            />
          </div>
        </section>
      ) : null}

      {mergePreview ? (
        <BranchMergeReviewCard
          preview={mergePreview}
          choices={mergeChoices}
          busyAction={busyAction}
          onChoiceChange={onMergeChoiceChange}
          onCancel={onCancelMerge}
          onConfirm={onConfirmMerge}
        />
      ) : null}

      <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-gray-400" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">需求状态</p>
        </div>
        <p className="mt-3 text-xs leading-6 text-gray-600">
          {activeRequirements?.learning_goal ?? "当前没有待执行的学习需求。"}
        </p>
        {latestBoardDecision ? (
          <div className="mt-3 rounded-md bg-gray-50 px-3 py-2">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
            <p className="mt-1 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
            <p className="mt-1 text-[11px] leading-5 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
