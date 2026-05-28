"use client";

import { useMemo } from "react";
import clsx from "clsx";
import { BookOpen, BrainCircuit, Clock3, GitBranch } from "lucide-react";

import {
  buildLessonHistoryGraphModel,
  graphHeadCommit,
  graphNodeForCommit,
  type GraphNode,
} from "@/components/course-studio/lesson-history-graph-model";
import { compactText, formatDate } from "@/components/course-studio/history-utils";
import type { BoardDecision, CommitRecord, Lesson } from "@/types";

type LessonHistoryGraphPanelProps = {
  activeLesson: Lesson;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
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

export function LessonHistoryGraphPanel({
  activeLesson,
  previewCommitId,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
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

        <div className="flex flex-wrap gap-1.5">
          {model.branches.map((branch) => {
            const headCommit = graphHeadCommit(activeLesson, branch.name);
            return (
              <button
                key={branch.name}
                type="button"
                onClick={() => void onSwitchBranch(branch.name)}
                disabled={branch.isCurrent}
                title={headCommit ? `${branch.name}: ${headCommit.label}` : branch.name}
                className={clsx(
                  "inline-flex h-7 items-center gap-1 rounded-md border px-2 text-[10px] font-bold transition",
                  branch.isCurrent
                    ? "border-black bg-black text-white"
                    : "border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-gray-950"
                )}
              >
                <GitBranch className="h-3 w-3" />
                {branch.name}
              </button>
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
              <line
                key={edge.id}
                x1={edge.parentX}
                y1={edge.parentY}
                x2={edge.childX}
                y2={edge.childY}
                stroke={edgeColor(edge.sameLane)}
                strokeWidth={edge.sameLane ? 2 : 1.5}
                strokeLinecap="round"
                opacity={edge.sameLane ? 0.8 : 0.65}
              />
            ))}
          </svg>

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
                "absolute rounded-lg border bg-white px-3 py-2 shadow-sm transition",
                node.isPreviewed
                  ? "border-blue-200 ring-1 ring-blue-200"
                  : node.isCurrentHead
                    ? "border-gray-900"
                    : "border-gray-200"
              )}
              style={{
                left: `${model.laneCount * 32 + 50}px`,
                top: `${node.y - 26}px`,
                width: `${Math.max(210, model.graphWidth - (model.laneCount * 32 + 66))}px`,
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
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
              <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-gray-500">{node.summary}</p>
              {node.branchLabels.length ? (
                <div className="mt-2 flex flex-wrap gap-1">
                  {node.branchLabels.map((branch) => (
                    <button
                      key={branch.name}
                      type="button"
                      onClick={() => void onSwitchBranch(branch.name)}
                      disabled={branch.isCurrent}
                      className={clsx(
                        "inline-flex h-5 items-center gap-1 rounded px-1.5 text-[9px] font-bold transition",
                        branch.isCurrent
                          ? "bg-black text-white"
                          : "bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-950"
                      )}
                    >
                      <GitBranch className="h-2.5 w-2.5" />
                      {branch.name}
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
