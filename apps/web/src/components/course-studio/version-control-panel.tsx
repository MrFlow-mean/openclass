import clsx from "clsx";
import {
  Circle,
  Eye,
  FileText,
  GitBranch,
  GitCommitHorizontal,
  MessageCircle,
  RotateCcw,
} from "lucide-react";
import type { CSSProperties } from "react";

import {
  buildLearningRequirementDisplay,
  learningRequirementStatusLabel,
  type LearningRequirementDisplayFactor,
} from "@/lib/learning-requirement-display";
import {
  buildHistoryGraphRows,
  historyNodeKindLabel,
  type HistoryGraphLane,
  type HistoryGraphRow,
  type HistoryNodeKind,
} from "@/components/course-studio/history-graph-utils";
import { compactText, formatDate } from "@/components/course-studio/history-utils";
import type { BoardDecision, CommitRecord, Lesson } from "@/types";

type VersionControlPanelProps = {
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  activeBoardTask: Lesson["board_task_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  onCreateBranch: () => void | Promise<void>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
};

function FactorList({ title, factors }: { title: string; factors: LearningRequirementDisplayFactor[] }) {
  if (!factors.length) {
    return null;
  }
  return (
    <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-4">
      <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">{title}</p>
      <dl className="mt-3 space-y-2 text-[11px] leading-6">
        {factors.map((factor) => (
          <div key={factor.key} className="grid grid-cols-[72px_minmax(0,1fr)] gap-3">
            <dt className={clsx("font-semibold", factor.required ? "text-gray-300" : "text-gray-500")}>
              {factor.label}
            </dt>
            <dd className={clsx("min-w-0 break-words", factor.filled ? "text-gray-200" : "text-gray-500")}>
              {factor.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function HistoryNodeIcon({ kind }: { kind: HistoryNodeKind }) {
  if (kind === "chat") {
    return <MessageCircle className="h-3.5 w-3.5" />;
  }
  if (kind === "document") {
    return <FileText className="h-3.5 w-3.5" />;
  }
  if (kind === "restore") {
    return <RotateCcw className="h-3.5 w-3.5" />;
  }
  return <Circle className="h-3.5 w-3.5" />;
}

function nodeKindClasses(kind: HistoryNodeKind) {
  if (kind === "chat") {
    return "bg-sky-500/15 text-sky-200";
  }
  if (kind === "document") {
    return "bg-emerald-500/15 text-emerald-200";
  }
  if (kind === "restore") {
    return "bg-amber-500/15 text-amber-200";
  }
  return "bg-white/10 text-gray-200";
}

function GraphCell({ row, lanes }: { row: HistoryGraphRow; lanes: HistoryGraphLane[] }) {
  const laneWidth = 16;
  const width = Math.max(lanes.length * laneWidth, laneWidth);
  const dotLeft = row.lane.index * laneWidth + laneWidth / 2;

  return (
    <div className="relative min-h-12 shrink-0" style={{ width }}>
      {row.continuationLaneIndexes.map((laneIndex) => {
        const lane = lanes[laneIndex];
        return (
          <span
            key={`${row.commit.id}:lane:${laneIndex}`}
            className="absolute inset-y-[-9px] w-px opacity-90"
            style={
              {
                left: laneIndex * laneWidth + laneWidth / 2,
                backgroundColor: lane?.color ?? "#6b7280",
              } satisfies CSSProperties
            }
          />
        );
      })}
      {row.connectors.map((connector) => {
        const fromLeft = connector.fromLane * laneWidth + laneWidth / 2;
        const toLeft = connector.toLane * laneWidth + laneWidth / 2;
        return (
          <span
            key={`${row.commit.id}:connector:${connector.fromLane}:${connector.toLane}`}
            className="absolute top-7 h-px"
            style={
              {
                left: Math.min(fromLeft, toLeft),
                width: Math.abs(toLeft - fromLeft),
                backgroundColor: connector.color,
              } satisfies CSSProperties
            }
          />
        );
      })}
      <span
        className={clsx(
          "absolute top-[17px] h-3 w-3 rounded-full border-2 bg-[#101318]",
          row.active && "ring-2 ring-white/80 ring-offset-2 ring-offset-[#101318]",
          row.head && "shadow-[0_0_0_3px_rgba(255,255,255,0.08)]"
        )}
        style={
          {
            left: dotLeft - 6,
            borderColor: row.lane.color,
          } satisfies CSSProperties
        }
      />
    </div>
  );
}

function HistoryGraphRowItem({
  row,
  lanes,
  currentBranchName,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
}: {
  row: HistoryGraphRow;
  lanes: HistoryGraphLane[];
  currentBranchName: string;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
}) {
  return (
    <div
      onClick={() => void onPreviewCommit(row.commit)}
      className={clsx(
        "group grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-md px-2 py-2 text-left font-mono transition",
        row.active ? "bg-white/10" : "hover:bg-white/[0.06]"
      )}
    >
      <GraphCell row={row} lanes={lanes} />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className={clsx("inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em]", nodeKindClasses(row.nodeKind))}>
            <HistoryNodeIcon kind={row.nodeKind} />
            {historyNodeKindLabel(row.nodeKind)}
          </span>
          <span
            className={clsx(
              "shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em]",
              row.lane.branchName === currentBranchName ? "bg-white text-[#101318]" : "bg-white/10 text-gray-300"
            )}
          >
            {row.lane.branchName}
          </span>
          {row.head ? (
            <span className="shrink-0 rounded bg-white/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em] text-gray-300">
              Head
            </span>
          ) : null}
          <p className="min-w-0 flex-1 truncate text-[12px] font-bold text-gray-50">{row.title}</p>
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-2 text-[11px] leading-5 text-gray-400">
          <span className="shrink-0 text-gray-500">{formatDate(row.commit.created_at)}</span>
          <span className="min-w-0 flex-1 truncate">{row.summary}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-label="Preview"
            title="Preview"
            onClick={(event) => {
              event.stopPropagation();
              void onPreviewCommit(row.commit);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-white/10 bg-white/[0.04] text-gray-400 hover:border-white/25 hover:text-white"
          >
            <Eye className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Restore"
            title="Restore"
            onClick={(event) => {
              event.stopPropagation();
              void onRestoreCommit(row.commit.id);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-white/10 bg-white/[0.04] text-gray-400 hover:border-white/25 hover:text-white"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Branch"
            title="Branch"
            onClick={(event) => {
              event.stopPropagation();
              void onCreateBranchFromCommit(row.commit);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-white/10 bg-white/[0.04] text-gray-400 hover:border-white/25 hover:text-white"
          >
            <GitBranch className="h-3.5 w-3.5" />
          </button>
          {row.lane.branchName !== currentBranchName ? (
            <button
              type="button"
              aria-label="Checkout"
              title="Checkout"
              onClick={(event) => {
                event.stopPropagation();
                void onSwitchBranch(row.lane.branchName);
              }}
              className="inline-flex h-6 w-6 items-center justify-center rounded border border-white/10 bg-white/[0.04] text-gray-400 hover:border-white/25 hover:text-white"
            >
              <GitCommitHorizontal className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function VersionControlPanel({
  activeLesson,
  previewCommit,
  previewCommitId,
  activeRequirements,
  activeBoardTask,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
}: VersionControlPanelProps) {
  const learningRequirementDisplay = activeRequirements
    ? buildLearningRequirementDisplay({ requirementSheet: activeRequirements })
    : null;
  const { lanes, rows } = buildHistoryGraphRows(activeLesson, previewCommitId);

  return (
    <div className="space-y-7 text-gray-200">
      <section>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">历史分支图</p>
            <p className="mt-1 text-xs font-semibold text-gray-200">
              {activeLesson.history_graph.commits.length} nodes · {lanes.length} branches
            </p>
          </div>
          <GitCommitHorizontal className="h-4 w-4 text-gray-500" />
        </div>

        <div className="mt-4 flex gap-2">
          <input
            value={newBranchName}
            onChange={(event) => onNewBranchNameChange(event.target.value)}
            placeholder="新分支名"
            className="min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-white/30"
          />
          <button
            type="button"
            onClick={() => void onCreateBranch()}
            className="inline-flex items-center gap-1.5 rounded-md bg-white px-3 py-2 text-[11px] font-bold uppercase tracking-wider text-[#101318]"
          >
            <GitBranch className="h-3.5 w-3.5" />
            开分支
          </button>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {lanes.map((lane) => (
            <button
              key={lane.branchName}
              type="button"
              onClick={() => void onSwitchBranch(lane.branchName)}
              className={clsx(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                lane.isCurrent
                  ? "border-white bg-white text-[#101318]"
                  : "border-white/10 bg-white/[0.04] text-gray-400 hover:border-white/25 hover:text-white"
              )}
            >
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: lane.color }} />
              {lane.branchName}
            </button>
          ))}
        </div>
      </section>

      <section>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">修订记录</p>
        <div className="mt-4 rounded-md border border-[#20242d] bg-[#101318] p-2 shadow-sm">
          {rows.map((row) => (
            <HistoryGraphRowItem
              key={row.commit.id}
              row={row}
              lanes={lanes}
              currentBranchName={activeLesson.history_graph.current_branch}
              onPreviewCommit={onPreviewCommit}
              onRestoreCommit={onRestoreCommit}
              onCreateBranchFromCommit={onCreateBranchFromCommit}
              onSwitchBranch={onSwitchBranch}
            />
          ))}
        </div>
      </section>

      <section className="border-t border-white/10 pt-6">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-gray-500" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">当前上下文</p>
        </div>
        {previewCommit ? (
          <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs font-semibold text-gray-100">{previewCommit.label}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{compactText(previewCommit.message, 180)}</p>
          </div>
        ) : null}
        {activeBoardTask ? (
          <>
            <p className="mt-4 text-sm leading-7 text-gray-300">{activeBoardTask.question_or_topic}</p>
            <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs font-semibold text-gray-100">{activeBoardTask.requested_action ?? "暂无待执行任务"}</p>
              <p className="mt-2 text-[11px] leading-6 text-gray-500">
                {activeBoardTask.target_hint || "执行完成后，当前清单会归档到历史并清空。"}
              </p>
            </div>
          </>
        ) : learningRequirementDisplay ? (
          <>
            <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">教学类型</p>
                  <p className="mt-1 text-sm font-semibold text-gray-100">{learningRequirementDisplay.teachingType}</p>
                </div>
                <span className="rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold text-gray-300">
                  {learningRequirementStatusLabel(learningRequirementDisplay.status)}
                </span>
              </div>
              {learningRequirementDisplay.summary ? (
                <p className="mt-3 text-[11px] leading-6 text-gray-500">{learningRequirementDisplay.summary}</p>
              ) : null}
            </div>
            <FactorList title="核心因素" factors={learningRequirementDisplay.coreFactors} />
            <FactorList title="辅助因素" factors={learningRequirementDisplay.auxiliaryFactors} />
          </>
        ) : (
          <p className="mt-4 text-sm leading-7 text-gray-400">
            等待下一次任务需求：说明要操作的位置、动作类型，以及希望怎么讲解或怎么编写。
          </p>
        )}
        {latestBoardDecision ? (
          <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">当前讲义决策</p>
            <p className="mt-2 text-xs font-semibold text-gray-100">{latestBoardDecision.action}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
