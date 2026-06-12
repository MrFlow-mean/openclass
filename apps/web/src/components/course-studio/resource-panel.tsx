import clsx from "clsx";
import { useRef, useState, type DragEvent } from "react";
import { LoaderCircle, Upload } from "lucide-react";

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

const ROLE_LABEL: Record<string, string> = {
  cover: "封面",
  front_matter: "前言",
  toc: "目录",
  body: "正文",
  appendix: "附录",
  epilogue: "尾声",
  unknown: "未分类",
};

function pageRange(start?: number | null, end?: number | null) {
  if (start == null) {
    return "未定位";
  }
  if (end == null || end === start) {
    return String(start);
  }
  return `${start}-${end}`;
}

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
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h5 className="truncate text-sm font-semibold text-gray-900">{resource.name}</h5>
                  <p className="mt-1 text-xs text-gray-500">
                    {resource.structure_regions.length} 个结构分区 · {resource.toc_entries.length} 个目录项 ·{" "}
                    {resource.chapter_shards.length} 个章节索引
                  </p>
                </div>
                {resource.parse_warnings.length > 0 ? (
                  <span className="shrink-0 rounded-md bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-700">
                    {resource.parse_warnings.length} 项需复核
                  </span>
                ) : null}
              </div>

              {resource.structure_regions.length > 0 ? (
                <section className="mt-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">结构地图</p>
                  <div className="mt-2 divide-y divide-gray-100 rounded-md border border-gray-100">
                    {resource.structure_regions.slice(0, 5).map((region) => (
                      <div key={region.id} className="grid grid-cols-[72px_1fr] gap-3 px-3 py-2 text-xs">
                        <span className="font-medium text-gray-700">{ROLE_LABEL[region.role] ?? region.role}</span>
                        <span className="text-gray-500">
                          全文页 {pageRange(region.physical_page_start, region.physical_page_end)}
                          {region.body_page_start != null
                            ? ` · 正文页 ${pageRange(region.body_page_start, region.body_page_end)}`
                            : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {resource.toc_entries.length > 0 ? (
                <section className="mt-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">目录到正文</p>
                  <div className="mt-2 space-y-2">
                    {resource.toc_entries.slice(0, 5).map((entry) => (
                      <div key={entry.id} className="rounded-md bg-gray-50 px-3 py-2">
                        <p className="truncate text-xs font-medium text-gray-800">{entry.title}</p>
                        <p className="mt-1 text-[11px] text-gray-500">
                          目录页码 {entry.printed_page_label ?? "未标注"} → 正文页{" "}
                          {entry.body_page_no ?? "未定位"} → 全文页 {entry.physical_page_no ?? "未定位"}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {resource.chapter_shards.length > 0 ? (
                <section className="mt-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">章节索引</p>
                  <div className="mt-2 space-y-2">
                    {resource.chapter_shards.slice(0, 4).map((shard) => (
                      <div key={shard.id} className="rounded-md border border-gray-100 px-3 py-2">
                        <p className="truncate text-xs font-medium text-gray-800">
                          {shard.heading_path.length > 0 ? shard.heading_path.join(" / ") : shard.title}
                        </p>
                        <p className="mt-1 text-[11px] text-gray-500">
                          正文页 {pageRange(shard.body_page_start, shard.body_page_end)} ·{" "}
                          {shard.block_ids.length} 个正文块
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
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
