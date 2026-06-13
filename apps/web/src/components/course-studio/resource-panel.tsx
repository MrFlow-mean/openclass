import clsx from "clsx";
import { useRef, useState, type DragEvent } from "react";
import { FileText, ImagePlus, LoaderCircle, Upload } from "lucide-react";

import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import type { CoursePackage, Lesson } from "@/types";

type ResourcePanelProps = {
  activeLesson: Lesson;
  resources: CoursePackage["resources"];
  isUploading: boolean;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
  onUploadResource: (file: File) => void | Promise<void>;
};

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function ResourcePanel({
  activeLesson,
  resources,
  isUploading,
  relatedEdges,
  lessonMap,
  onOpenLesson,
  onUploadResource,
}: ResourcePanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const [isDragActive, setIsDragActive] = useState(false);

  function handleFile(file: File | null | undefined) {
    if (!file || isUploading) {
      return;
    }
    void onUploadResource(file);
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    if (!isUploading) {
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
    event.dataTransfer.dropEffect = isUploading ? "none" : "copy";
    if (!isUploading) {
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
    if (isUploading) {
      return;
    }
    handleFile(event.dataTransfer.files.item(0));
  }

  return (
    <div className="space-y-8">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">资料区</p>
        <div className="mt-4 space-y-3">
          <div
            className={clsx(
              "flex min-h-40 items-center justify-center rounded-lg border border-dashed bg-white px-4 py-5 transition",
              isUploading
                ? "cursor-not-allowed border-gray-200 text-gray-400"
                : "border-gray-200 hover:border-gray-300",
              isDragActive && !isUploading && "border-blue-400 bg-blue-50 text-blue-700 ring-2 ring-blue-100"
            )}
            aria-busy={isUploading}
            aria-disabled={isUploading}
            data-testid="resource-upload-dropzone"
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept=".pdf,.doc,.docx,.txt,.md,.markdown,.epub,image/*"
              onChange={(event) => {
                handleFile(event.currentTarget.files?.item(0));
                event.currentTarget.value = "";
              }}
            />
            <div className="flex flex-col items-center gap-3 text-center">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-800 shadow-sm transition hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isUploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {isDragActive && !isUploading ? "松开上传" : isUploading ? "上传中" : "上传资料"}
              </button>
              <p className="text-xs text-gray-500">PDF / Word / Markdown / EPUB / 图片</p>
            </div>
          </div>

          {resources.map((resource) => (
            <article key={resource.id} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-gray-50 text-gray-600">
                  {resource.resource_type === "image" || resource.mime_type.startsWith("image/") ? (
                    <ImagePlus className="h-4 w-4" />
                  ) : (
                    <FileText className="h-4 w-4" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <h5 className="truncate text-sm font-semibold text-gray-900">{resource.name}</h5>
                  <p className="mt-1 text-xs text-gray-500">
                    {resource.extracted_text_available
                      ? `已索引 ${resource.outline.length} 个章节入口`
                      : "当前仅做入口索引"}
                  </p>
                </div>
              </div>

              {resource.outline.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {resource.outline.slice(0, 3).map((chapter) => (
                    <div key={chapter.id} className="rounded-md bg-gray-50 px-3 py-2">
                      <p className="truncate text-xs font-medium text-gray-800">{chapter.title}</p>
                      <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-gray-500">{chapter.summary}</p>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
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
