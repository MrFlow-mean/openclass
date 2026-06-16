import clsx from "clsx";
import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { FileText, TriangleAlert, UploadCloud } from "lucide-react";

import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import type { CoursePackage, Lesson } from "@/types";

type ResourcePanelProps = {
  activeLesson: Lesson;
  resources: CoursePackage["resources"];
  isUploadingResource: boolean;
  onUploadResource: (file: File) => void | Promise<void>;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

function parserLabel(provider: string) {
  if (provider.startsWith("raganything")) {
    return "RAG-Anything";
  }
  if (provider === "native_fallback") {
    return "原生解析（回退）";
  }
  return "原生解析";
}

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function ResourcePanel({
  activeLesson,
  resources,
  isUploadingResource,
  onUploadResource,
  relatedEdges,
  lessonMap,
  onOpenLesson,
}: ResourcePanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const [isDragActive, setIsDragActive] = useState(false);
  const visibleResources = resources.filter(
    (resource) => !resource.scope_lesson_id || resource.scope_lesson_id === activeLesson.id
  );

  function handleFile(file: File | null | undefined) {
    if (file && !isUploadingResource) {
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
    if (!isUploadingResource) {
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
    event.dataTransfer.dropEffect = isUploadingResource ? "none" : "copy";
    if (!isUploadingResource) {
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
    "aria-busy": isUploadingResource,
    "aria-disabled": isUploadingResource,
    "data-testid": "resource-upload-dropzone",
    onDragEnter: handleDragEnter,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  };

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">资料区</p>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploadingResource}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <UploadCloud className="h-3.5 w-3.5" />
            {isUploadingResource ? "解析中" : "上传资料"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.md,.epub,image/*"
            onChange={handleFileChange}
          />
        </div>
        <div
          {...dropzoneProps}
          aria-label="资料上传区域"
          className={clsx(
            "mt-4 rounded-lg transition",
            isDragActive && !isUploadingResource && "bg-blue-50/70 ring-2 ring-blue-200"
          )}
        >
          {visibleResources.length ? (
            <div className="space-y-3">
              {visibleResources.map((resource) => (
                <article
                  key={resource.id}
                  className={clsx(
                    "rounded-lg border bg-white p-3 shadow-sm transition-colors",
                    isDragActive && !isUploadingResource ? "border-blue-300" : "border-gray-200"
                  )}
                >
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 rounded-md border border-gray-200 bg-gray-50 p-1.5 text-gray-500">
                      <FileText className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-gray-900">{resource.name}</p>
                      <p className="mt-1 text-xs text-gray-500">
                        {parserLabel(resource.parser_provider)} · {resource.outline.length} 个索引片段 ·{" "}
                        {resource.source_units.length} 个解析单元
                      </p>
                      {resource.parser_message ? (
                        <p className="mt-2 line-clamp-2 text-xs text-gray-500">{resource.parser_message}</p>
                      ) : null}
                    </div>
                  </div>
                  {resource.parse_warnings.length ? (
                    <div className="mt-3 space-y-1 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-800">
                      {resource.parse_warnings.slice(0, 2).map((warning) => (
                        <p key={warning} className="flex gap-1.5">
                          <TriangleAlert className="mt-0.5 h-3 w-3 flex-none" />
                          <span className="line-clamp-2">{warning}</span>
                        </p>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          ) : (
            <div
              aria-label="资料区预留位"
              className={clsx(
                "flex min-h-40 items-center justify-center rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !isUploadingResource
                  ? "border-blue-400 text-blue-700"
                  : "border-gray-200 text-gray-400"
              )}
            >
              {isUploadingResource ? "正在解析资料。" : isDragActive ? "松开上传资料。" : "当前课还没有资料。"}
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
