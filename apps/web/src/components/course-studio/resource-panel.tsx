import clsx from "clsx";
import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { ArrowRight, ChevronDown, ChevronRight, FileText, Link2, TriangleAlert, UploadCloud } from "lucide-react";

import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import {
  buildResourceOutlineTree,
  ResourceOutlineTree,
  type ResourceOutlineNode,
} from "@/components/course-studio/resource-outline-tree";
import type { CoursePackage, LearningResourceReference, Lesson, LibraryChapter } from "@/types";

type ResourcePanelProps = {
  activeLesson: Lesson;
  resources: CoursePackage["resources"];
  isUploadingResource: boolean;
  onUploadResource: (file: File) => void | Promise<void>;
  isAddingResourceUrl: boolean;
  onAddResourceUrl: (url: string) => void | Promise<void>;
  selectedResourceReference?: LearningResourceReference | null;
  onSelectResourceChapter: (
    resource: CoursePackage["resources"][number],
    chapter: LibraryChapter
  ) => void | Promise<void>;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

function sourceTypeLabel(sourceType: CoursePackage["resources"][number]["source_type"]) {
  switch (sourceType) {
    case "web_url":
      return "网页链接";
    case "audio_file":
      return "音频文件";
    case "video_file":
      return "视频文件";
    case "video_url":
      return "视频链接";
    case "pasted_text":
      return "粘贴文本";
    case "transcript":
      return "转写文本";
    default:
      return "本地文件";
  }
}

function ingestionStatusLabel(status: CoursePackage["resources"][number]["ingestion_status"]) {
  switch (status) {
    case "queued":
      return "排队中";
    case "fetching":
      return "抓取中";
    case "parsing":
      return "解析中";
    case "indexing":
      return "索引中";
    case "failed":
      return "失败";
    default:
      return "可用";
  }
}

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function collectDefaultExpandedOutlineKeys(resourceId: string, nodes: ResourceOutlineNode[], level = 0): string[] {
  const keys: string[] = [];
  nodes.forEach((node) => {
    if (!node.children.length || level >= 2) {
      return;
    }
    keys.push(`${resourceId}:${node.chapter.id}`);
    keys.push(...collectDefaultExpandedOutlineKeys(resourceId, node.children, level + 1));
  });
  return keys;
}

export function ResourcePanel({
  activeLesson,
  resources,
  isUploadingResource,
  onUploadResource,
  isAddingResourceUrl,
  onAddResourceUrl,
  selectedResourceReference,
  onSelectResourceChapter,
  relatedEdges,
  lessonMap,
  onOpenLesson,
}: ResourcePanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const [isDragActive, setIsDragActive] = useState(false);
  const [resourceUrl, setResourceUrl] = useState("");
  const [expandedResourceIds, setExpandedResourceIds] = useState<Set<string>>(new Set());
  const [expandedOutlineNodeIds, setExpandedOutlineNodeIds] = useState<Set<string>>(new Set());
  const isResourceBusy = isUploadingResource || isAddingResourceUrl;
  const visibleResources = resources.filter(
    (resource) => !resource.scope_lesson_id || resource.scope_lesson_id === activeLesson.id
  );

  function handleFile(file: File | null | undefined) {
    if (file && !isResourceBusy) {
      void onUploadResource(file);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    handleFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    if (!isResourceBusy) {
      event.dataTransfer.dropEffect = "copy";
      setIsDragActive(true);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = isResourceBusy ? "none" : "copy";
    if (!isResourceBusy) {
      setIsDragActive(true);
    }
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragActive(false);
    handleFile(event.dataTransfer.files.item(0));
  }

  const dropzoneProps = {
    "aria-busy": isResourceBusy,
    "aria-disabled": isResourceBusy,
    "data-testid": "resource-upload-dropzone",
    onDragEnter: handleDragEnter,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  };

  function handleUrlSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextUrl = resourceUrl.trim();
    if (!nextUrl || isResourceBusy) {
      return;
    }
    setResourceUrl("");
    void onAddResourceUrl(nextUrl);
  }

  function toggleResourceOutline(resourceId: string, outlineTree: ResourceOutlineNode[]) {
    const willExpand = !expandedResourceIds.has(resourceId);
    setExpandedResourceIds((current) => {
      const next = new Set(current);
      if (next.has(resourceId)) {
        next.delete(resourceId);
      } else {
        next.add(resourceId);
      }
      return next;
    });
    if (willExpand) {
      setExpandedOutlineNodeIds((current) => {
        const next = new Set(current);
        collectDefaultExpandedOutlineKeys(resourceId, outlineTree).forEach((key) => next.add(key));
        return next;
      });
    }
  }

  function toggleOutlineNode(resourceId: string, chapterId: string) {
    const key = `${resourceId}:${chapterId}`;
    setExpandedOutlineNodeIds((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  const uploadButton = (
    <button
      type="button"
      onClick={() => fileInputRef.current?.click()}
      disabled={isResourceBusy}
      className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <UploadCloud className="h-3.5 w-3.5" />
      {isUploadingResource ? "解析中" : isDragActive ? "松开上传" : "上传资料"}
    </button>
  );

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">资料区</p>
          {visibleResources.length ? uploadButton : null}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.md,.epub,image/*"
            onChange={handleFileChange}
          />
        </div>
        <form onSubmit={handleUrlSubmit} className="mt-4 flex items-center gap-2">
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-gray-200 bg-white px-2.5 py-2 text-xs text-gray-600 shadow-sm focus-within:border-gray-400">
            <Link2 className="h-3.5 w-3.5 flex-none text-gray-400" />
            <input
              value={resourceUrl}
              onChange={(event) => setResourceUrl(event.target.value)}
              disabled={isResourceBusy}
              placeholder="粘贴网页链接"
              className="min-w-0 flex-1 bg-transparent text-xs text-gray-900 outline-none placeholder:text-gray-400 disabled:cursor-not-allowed"
            />
          </label>
          <button
            type="submit"
            disabled={!resourceUrl.trim() || isResourceBusy}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={isAddingResourceUrl ? "正在添加链接" : "添加链接"}
          >
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </form>
        <div
          {...dropzoneProps}
          aria-label="资料上传区域"
          className={clsx(
            "mt-4 rounded-lg transition",
            isDragActive && !isResourceBusy && "bg-blue-50/70 ring-2 ring-blue-200"
          )}
        >
          {visibleResources.length ? (
            <div className="space-y-3">
              {visibleResources.map((resource) => {
                const isFailed = resource.ingestion_status === "failed";
                const isOutlineExpanded = expandedResourceIds.has(resource.id);
                const selectedChapterId =
                  selectedResourceReference?.resource_id === resource.id
                    ? selectedResourceReference.chapter_id
                    : null;
                const outlineTree = buildResourceOutlineTree(resource.outline);
                const expandedNodeIds = new Set<string>();
                expandedOutlineNodeIds.forEach((key) => {
                  const [resourceId, chapterId] = key.split(":");
                  if (resourceId === resource.id && chapterId) {
                    expandedNodeIds.add(chapterId);
                  }
                });
                const warnings = Array.from(
                  new Set([resource.ingestion_error, ...resource.parse_warnings].filter(Boolean))
                );
                const ResourceIcon = resource.source_type === "web_url" ? Link2 : FileText;
                return (
                  <article
                    key={resource.id}
                    className={clsx(
                      "rounded-lg border bg-white p-3 shadow-sm transition-colors",
                      isFailed
                        ? "border-red-200"
                        : isDragActive && !isResourceBusy
                          ? "border-blue-300"
                          : "border-gray-200"
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className={clsx(
                          "mt-0.5 rounded-md border p-1.5",
                          isFailed ? "border-red-200 bg-red-50 text-red-500" : "border-gray-200 bg-gray-50 text-gray-500"
                        )}
                      >
                        <ResourceIcon className="h-3.5 w-3.5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-gray-900">{resource.name}</p>
                        <p className="mt-1 text-xs text-gray-500">
                          {sourceTypeLabel(resource.source_type)} · {ingestionStatusLabel(resource.ingestion_status)} ·{" "}
                          {resource.outline.length} 个索引片段 · {resource.source_units.length} 个证据单元
                        </p>
                        {resource.source_uri ? (
                          <p className="mt-1 truncate text-xs text-gray-400">{resource.source_uri}</p>
                        ) : null}
                        {resource.parser_message ? (
                          <p className="mt-2 line-clamp-2 text-xs text-gray-500">{resource.parser_message}</p>
                        ) : null}
                      </div>
                    </div>
                    {resource.outline.length ? (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <button
                          type="button"
                          onClick={() => toggleResourceOutline(resource.id, outlineTree)}
                          className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-700 shadow-sm transition hover:border-gray-300"
                          aria-expanded={isOutlineExpanded}
                        >
                          {isOutlineExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          {isOutlineExpanded ? "收起目录" : "展开目录"}
                        </button>
                        {isOutlineExpanded ? (
                          <div className="mt-2 max-h-80 overflow-y-auto pr-1 custom-scrollbar">
                            <ResourceOutlineTree
                              resource={resource}
                              nodes={outlineTree}
                              expandedNodeIds={expandedNodeIds}
                              selectedChapterId={selectedChapterId}
                              onToggleNode={toggleOutlineNode}
                              onSelectNode={onSelectResourceChapter}
                            />
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {warnings.length ? (
                      <div
                        className={clsx(
                          "mt-3 space-y-1 rounded-md border px-2.5 py-2 text-xs",
                          isFailed
                            ? "border-red-200 bg-red-50 text-red-700"
                            : "border-amber-200 bg-amber-50 text-amber-800"
                        )}
                      >
                        {warnings.slice(0, 2).map((warning) => (
                          <p key={warning} className="flex gap-1.5">
                            <TriangleAlert className="mt-0.5 h-3 w-3 flex-none" />
                            <span className="line-clamp-2">{warning}</span>
                          </p>
                        ))}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <div
              aria-label="资料区预留位"
              className={clsx(
                "flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !isResourceBusy
                  ? "border-blue-400 text-blue-700"
                  : "border-gray-200 text-gray-400"
              )}
            >
              {uploadButton}
              <span>{isResourceBusy ? "正在处理资料。" : "当前课还没有资料。"}</span>
            </div>
          )}
        </div>
      </div>

      <CourseGraphPanel
        activeLesson={activeLesson}
        relatedEdges={relatedEdges}
        lessonMap={lessonMap}
        onOpenLesson={onOpenLesson}
      />
    </div>
  );
}
