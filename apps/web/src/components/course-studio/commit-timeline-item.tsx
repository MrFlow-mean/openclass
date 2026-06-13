import clsx from "clsx";
import { Eye, GitBranch, MessageSquareText, RotateCcw, X } from "lucide-react";
import type { KeyboardEvent, MouseEvent } from "react";

import { BranchSequenceSelector, type BranchSequenceOption } from "@/components/branch-sequence-selector";
import {
  compactText,
  formatDate,
  metadataBool,
  metadataText,
} from "@/components/course-studio/history-utils";
import type { CommitRecord } from "@/types";

type CommitTimelineItemProps = {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
  current: boolean;
  first: boolean;
  last: boolean;
  branchLane: number;
  isBranchHead: boolean;
  isCurrentBranch: boolean;
  parentLabel: string | null;
  childCount: number;
  detailOpen: boolean;
  branchSequence: BranchSequenceOption[];
  currentBranchName: string;
  onPreview: () => void;
  onRestore: () => void;
  onBranch: () => void;
  onSwitchBranch: (branchName: string) => void;
  onOpenDetail: () => void;
  onCloseDetail: () => void;
};

function branchLabel(branchName: string) {
  return branchName === "main" ? "主分支" : branchName;
}

function commitKindLabel(commit: CommitRecord) {
  const kind = String(commit.metadata?.kind ?? "");
  if (kind === "chat_flow") {
    return "Chat turn";
  }
  if (kind === "board_document_generation") {
    return "Board generation";
  }
  if (kind === "board_document_edit") {
    return "Board edit";
  }
  if (kind === "auto_document_save" || metadataBool(commit, "autosave")) {
    return "Auto Save";
  }
  if (kind === "restore_snapshot") {
    return "Restore";
  }
  return "Version";
}

export function CommitTimelineItem({
  commit,
  active,
  latest,
  current,
  first,
  last,
  branchLane,
  isBranchHead,
  isCurrentBranch,
  parentLabel,
  childCount,
  detailOpen,
  branchSequence,
  currentBranchName,
  onPreview,
  onRestore,
  onBranch,
  onSwitchBranch,
  onOpenDetail,
  onCloseDetail,
}: CommitTimelineItemProps) {
  const userMessage = metadataText(commit, "user_message");
  const assistantMessage = metadataText(commit, "assistant_message");
  const boardAction = metadataText(commit, "board_action");
  const autoApplied = metadataBool(commit, "auto_applied");
  const kindLabel = commitKindLabel(commit);
  const hasChatTurn = Boolean(commit.metadata?.kind === "chat_flow" || userMessage || assistantMessage);
  const visibleLane = Math.min(branchLane, 3);
  const laneOffset = visibleLane * 10 + 12;
  const label = branchLabel(commit.branch_name);
  const snapshotPreview = compactText(
    commit.snapshot.content_text || commit.snapshot.title || "这个版本没有可预览正文。",
    260
  );
  const actionContext = compactText(commit.snapshot.content_text || commit.label, 56);

  function handleOpenDetail(event: MouseEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();
    onOpenDetail();
  }

  function handlePreviewKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    onPreview();
  }

  return (
    <div className="relative grid grid-cols-[2.75rem_minmax(0,1fr)] gap-2">
      <div className="relative min-h-11">
        {!first ? (
          <div
            className={clsx("absolute top-0 h-4 w-px", current ? "bg-black" : "bg-gray-200")}
            style={{ left: laneOffset }}
          />
        ) : null}
        {!last ? (
          <div
            className={clsx("absolute bottom-0 top-4 w-px", current ? "bg-black" : latest ? "bg-gray-500" : "bg-gray-200")}
            style={{ left: laneOffset }}
          />
        ) : null}
        {visibleLane > 0 ? (
          <div
            className={clsx("absolute top-4 h-px bg-gray-200", isCurrentBranch && "bg-gray-400")}
            style={{ left: 6, width: laneOffset }}
          />
        ) : null}
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onPreview();
          }}
          onContextMenu={handleOpenDetail}
          className={clsx(
            "absolute top-2 flex h-4 w-4 items-center justify-center rounded-full border text-[0px] shadow-sm transition",
            current
              ? "border-black bg-black"
              : active
                ? "border-blue-400 bg-blue-50"
                : isBranchHead
                  ? "border-gray-400 bg-white"
                  : "border-gray-300 bg-white hover:border-gray-500"
          )}
          style={{ left: laneOffset - 8 }}
          aria-label={`打开版本 ${commit.label}`}
          title={`${commit.label} · 左键预览，右键详情`}
        >
          <span className={clsx("h-1.5 w-1.5 rounded-full", current ? "bg-white" : "bg-gray-400")} />
        </button>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label={`打开版本 ${commit.label}`}
        onClick={onPreview}
        onKeyDown={handlePreviewKeyDown}
        onContextMenu={handleOpenDetail}
        className={clsx(
          "group min-w-0 border-b border-gray-100 py-2 outline-none transition focus-visible:ring-2 focus-visible:ring-black/20",
          current ? "bg-gray-50/80" : active ? "bg-blue-50/50" : "hover:bg-gray-50"
        )}
      >
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={clsx(
              "shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em]",
              isCurrentBranch ? "bg-black text-white" : "bg-gray-100 text-gray-500"
            )}
          >
            {label}
          </span>
          {current ? (
            <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-emerald-700">
              Current
            </span>
          ) : null}
          {latest && !current ? (
            <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-gray-600">
              Latest
            </span>
          ) : null}
          {autoApplied ? (
            <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-emerald-700">
              Applied
            </span>
          ) : null}
          <p className="min-w-0 flex-1 truncate text-xs font-bold text-gray-900">{commit.label}</p>
          <span className="shrink-0 text-[10px] text-gray-400">{formatDate(commit.created_at)}</span>
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onPreview();
              }}
              className="rounded-md p-1 text-gray-400 hover:bg-white hover:text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/20"
              aria-label={`Preview ${actionContext}`}
              title={`Preview ${actionContext}`}
            >
              <Eye className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onRestore();
              }}
              className="rounded-md p-1 text-gray-400 hover:bg-white hover:text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/20"
              aria-label={`Restore ${actionContext}`}
              title={`Restore ${actionContext}`}
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onBranch();
              }}
              className="rounded-md p-1 text-gray-400 hover:bg-white hover:text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/20"
              aria-label={`Branch ${actionContext}`}
              title={`Branch ${actionContext}`}
            >
              <GitBranch className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <p className="mt-1 truncate text-[10px] text-gray-400">
          {kindLabel}
          {parentLabel ? ` · 基于 ${compactText(parentLabel, 34)}` : ""}
          {childCount > 0 ? ` · ${childCount} 个后续节点` : ""}
        </p>

        {detailOpen ? (
          <div
            className="mt-3 rounded-lg border border-gray-200 bg-white p-3 text-left shadow-sm"
            onClick={(event) => event.stopPropagation()}
            onContextMenu={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                  <MessageSquareText className="h-3.5 w-3.5" />
                  <span>{hasChatTurn ? "ChatTurn" : "版本详情"}</span>
                </div>
                <p className="mt-2 truncate text-xs font-bold text-gray-900">{commit.label}</p>
                <p className="mt-1 text-[10px] text-gray-400">
                  {label} · {formatDate(commit.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onCloseDetail();
                }}
                className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-black"
                aria-label="关闭版本详情"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {hasChatTurn ? (
              <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3 text-[11px] leading-5 text-gray-600">
                <p className="font-bold text-gray-400">用户输入</p>
                <p className="mt-1 whitespace-pre-wrap text-gray-700">{userMessage || "这个版本没有用户输入记录。"}</p>
                <p className="mt-3 font-bold text-gray-400">AI 讲解</p>
                <p className="mt-1 whitespace-pre-wrap text-gray-700">{assistantMessage || "这个版本没有 AI 回复记录。"}</p>
                {boardAction ? (
                  <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                    Action: {boardAction}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3 text-[11px] leading-5 text-gray-600">
                <p className="font-bold text-gray-400">版本说明</p>
                <p className="mt-1 whitespace-pre-wrap text-gray-700">{commit.message}</p>
                <p className="mt-3 font-bold text-gray-400">文档快照</p>
                <p className="mt-1 whitespace-pre-wrap text-gray-700">{snapshotPreview}</p>
              </div>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onPreview();
                }}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
              >
                <Eye className="h-3 w-3" />
                Preview
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onRestore();
                }}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
              >
                <RotateCcw className="h-3 w-3" />
                Restore
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onBranch();
                }}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
              >
                <GitBranch className="h-3 w-3" />
                Branch
              </button>
            </div>
            <BranchSequenceSelector
              branches={branchSequence}
              currentBranchName={currentBranchName}
              onSelectBranch={onSwitchBranch}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
