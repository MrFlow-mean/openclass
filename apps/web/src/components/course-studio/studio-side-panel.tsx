import clsx from "clsx";
import { BookOpen, BrainCircuit, FileText, GitBranch, ImagePlus, LoaderCircle, Trash2, X } from "lucide-react";

import { CommitTimelineItem } from "@/components/course-studio/commit-timeline-item";
import { branchSequenceForCommit } from "@/components/course-studio/history-utils";
import { ResourceUploadDropzone } from "@/components/resource-upload-dropzone";
import type { BoardDecision, CommitRecord, CoursePackage, Lesson } from "@/types";

export type CourseStudioSidebarTab = "history" | "branch" | "library";

type CourseStudioSidePanelProps = {
  open: boolean;
  sidebarTab: CourseStudioSidebarTab;
  onSidebarTabChange: (tab: CourseStudioSidebarTab) => void;
  onClose: () => void;
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  busyAction: string | null;
  resources: CoursePackage["resources"];
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onCreateBranch: () => void | Promise<void>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onUploadResource: (file: File | null) => void | Promise<void>;
  onDeleteResource: (resourceId: string, resourceName: string) => void | Promise<void>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function CourseStudioSidePanel({
  open,
  sidebarTab,
  onSidebarTabChange,
  onClose,
  activeLesson,
  previewCommit,
  previewCommitId,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  busyAction,
  resources,
  relatedEdges,
  lessonMap,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onUploadResource,
  onDeleteResource,
  onOpenLesson,
}: CourseStudioSidePanelProps) {
  return (
    <aside
      className={clsx(
        "h-full min-h-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
        open ? "hidden xl:flex" : "hidden"
      )}
    >
      <div className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-5">
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">课程工作台辅助</h4>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex border-b border-gray-200 bg-white">
        {[
          { value: "history", label: "History" },
          { value: "branch", label: "Branch" },
          { value: "library", label: "Library" },
        ].map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => onSidebarTabChange(tab.value as CourseStudioSidebarTab)}
            className={clsx(
              "flex-1 py-3 text-[10px] font-bold uppercase tracking-wider transition-colors",
              sidebarTab === tab.value
                ? "border-b-2 border-black text-black"
                : "text-gray-400 hover:text-black"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-5 custom-scrollbar">
        {sidebarTab === "history" ? (
          <div className="space-y-8">
            <div className="space-y-4">
              <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
              {[...activeLesson.history_graph.commits].reverse().map((commit, index) => (
                <CommitTimelineItem
                  key={commit.id}
                  commit={commit}
                  active={commit.id === previewCommitId}
                  latest={index === 0}
                  branchSequence={branchSequenceForCommit(activeLesson, commit)}
                  currentBranchName={activeLesson.history_graph.current_branch}
                  onPreview={() => void onPreviewCommit(commit)}
                  onRestore={() => void onRestoreCommit(commit.id)}
                  onBranch={() => void onCreateBranchFromCommit(commit)}
                  onSwitchBranch={(branchName) => void onSwitchBranch(branchName)}
                />
              ))}
            </div>
          </div>
        ) : null}

        {sidebarTab === "branch" ? (
          <div className="space-y-8">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">分支管理</p>
              <div className="mt-4 flex gap-2">
                <input
                  value={newBranchName}
                  onChange={(event) => onNewBranchNameChange(event.target.value)}
                  placeholder="新分支名"
                  className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm outline-none focus:border-black"
                />
                <button
                  type="button"
                  onClick={() => void onCreateBranch()}
                  className="rounded-xl bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
                >
                  <GitBranch className="mr-1.5 inline h-3.5 w-3.5" />
                  开分支
                </button>
              </div>
              <p className="mt-2 text-[11px] leading-5 text-gray-400">
                {previewCommit
                  ? `当前会从历史节点「${previewCommit.label}」开启分支；未填写名称时会自动生成。`
                  : "先在 History 中 Preview 某个节点，或直接从当前最新节点开启分支。未填写名称时会自动生成。"}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {Object.values(activeLesson.history_graph.branches).map((branch) => (
                  <button
                    key={branch.name}
                    type="button"
                    onClick={() => void onSwitchBranch(branch.name)}
                    className={clsx(
                      "rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                      activeLesson.history_graph.current_branch === branch.name
                        ? "border-black bg-black text-white"
                        : "border-gray-200 bg-white text-gray-500 hover:text-black"
                    )}
                  >
                    {branch.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="border-t border-gray-200 pt-6">
              <div className="flex items-center gap-2">
                <BrainCircuit className="h-4 w-4 text-gray-400" />
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">需求清单</p>
              </div>
              <p className="mt-4 text-sm leading-7 text-gray-700">
                {activeRequirements?.learning_goal ?? "等待下一次任务需求：说明要操作的位置、动作类型，以及希望怎么讲解或怎么编写。"}
              </p>
              <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                <p className="text-xs font-semibold text-gray-900">
                  {activeRequirements?.action_type ?? activeRequirements?.target_depth ?? "暂无待执行任务"}
                </p>
                <p className="mt-2 text-[11px] leading-6 text-gray-500">
                  {activeRequirements?.action_instruction || activeRequirements?.success_criteria || "执行完成后，当前清单会归档到历史并清空。"}
                </p>
              </div>
              {latestBoardDecision ? (
                <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
                  <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
                  <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {sidebarTab === "library" ? (
          <div className="space-y-8">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">关联资料库</p>
              <ResourceUploadDropzone
                disabled={Boolean(busyAction)}
                uploading={busyAction === "upload"}
                onUpload={(file) => void onUploadResource(file)}
              />
              <div className="mt-4 space-y-3">
                {resources.length ? (
                  resources.map((resource) => {
                    const isDeletingResource = busyAction === `delete-resource:${resource.id}`;
                    return (
                      <div
                        key={resource.id}
                        className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-gray-300"
                      >
                        <div className="flex items-start gap-3">
                          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                            {resource.resource_type === "image" || resource.mime_type.startsWith("image/") ? (
                              <ImagePlus className="h-4 w-4" />
                            ) : (
                              <FileText className="h-4 w-4" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-xs font-bold text-gray-900">{resource.name}</p>
                            <p className="mt-1 text-[11px] text-gray-500">
                              {resource.extracted_text_available
                                ? `已索引 ${resource.outline.length} 个章节入口`
                                : "当前仅做入口索引"}
                            </p>
                          </div>
                          <button
                            type="button"
                            onClick={() => void onDeleteResource(resource.id, resource.name)}
                            disabled={Boolean(busyAction)}
                            title={`删除 ${resource.name}`}
                            aria-label={`删除资料 ${resource.name}`}
                            className={clsx(
                              "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600",
                              busyAction && "cursor-not-allowed opacity-50 hover:bg-transparent hover:text-gray-400"
                            )}
                          >
                            {isDeletingResource ? (
                              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="h-3.5 w-3.5" />
                            )}
                          </button>
                        </div>
                        <div className="mt-3 space-y-2">
                          {resource.outline.slice(0, 3).map((chapter) => (
                            <div key={chapter.id} className="rounded-lg bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
                              <p className="font-semibold text-gray-800">{chapter.title}</p>
                              <p className="mt-1 leading-6">{chapter.summary}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })
                ) : null}
              </div>
            </div>

            <div className="border-t border-gray-200 pt-6">
              <div className="mb-4 flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-gray-400" />
                <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">课程图谱</p>
              </div>
              <div className="space-y-3">
                {relatedEdges.length ? (
                  relatedEdges.map((edge) => {
                    const source = lessonMap.get(edge.source_lesson_id);
                    const target = lessonMap.get(edge.target_lesson_id);
                    if (!source || !target) {
                      return null;
                    }
                    const nextLesson = edge.source_lesson_id === activeLesson.id ? target : source;
                    return (
                      <button
                        key={edge.id}
                        type="button"
                        onClick={() => void onOpenLesson(nextLesson.id)}
                        className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:border-gray-300"
                      >
                        <p className="text-xs font-bold text-gray-900">
                          {source.title} → {target.title}
                        </p>
                        <p className="mt-1 text-[11px] text-gray-500">关系：{edge.relationship}</p>
                      </button>
                    );
                  })
                ) : (
                  <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">
                    当前 lesson 还没有更多图谱关系。
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
