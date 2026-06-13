import clsx from "clsx";
import { useRef, useState, type DragEvent } from "react";
import { LoaderCircle, Send, ShieldAlert, ShieldCheck, Upload, X } from "lucide-react";

import { CourseGraphPanel } from "@/components/course-studio/course-graph-panel";
import { api } from "@/lib/api";
import type { CoursePackage, Lesson, ResourceLibraryItem } from "@/types";

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

function auditBadge(resource: ResourceLibraryItem) {
  const audit = resource.copyright_audit;
  if (audit.public_distribution === "allowed") {
    return {
      label: audit.override_source === "admin_appeal" ? "已解封公开" : "可公开",
      className: "border-emerald-200 bg-emerald-50 text-emerald-700",
      Icon: ShieldCheck,
    };
  }
  if (audit.public_distribution === "blocked") {
    return {
      label: "禁止公开",
      className: "border-rose-200 bg-rose-50 text-rose-700",
      Icon: ShieldAlert,
    };
  }
  return {
    label: audit.status === "error" ? "审核失败" : "待复核",
    className: "border-amber-200 bg-amber-50 text-amber-700",
    Icon: ShieldAlert,
  };
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
  const [appealResource, setAppealResource] = useState<ResourceLibraryItem | null>(null);
  const [appealMessage, setAppealMessage] = useState("");
  const [appealEvidence, setAppealEvidence] = useState("");
  const [appealStatus, setAppealStatus] = useState<string | null>(null);
  const [isSubmittingAppeal, setIsSubmittingAppeal] = useState(false);

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

  async function submitAppeal() {
    if (!appealResource || isSubmittingAppeal) {
      return;
    }
    setIsSubmittingAppeal(true);
    setAppealStatus(null);
    try {
      await api.createResourceCopyrightAppeal(appealResource.id, {
        message: appealMessage,
        evidence_text: appealEvidence,
      });
      setAppealStatus("申诉已提交");
      setAppealMessage("");
      setAppealEvidence("");
    } catch (error) {
      setAppealStatus(error instanceof Error ? error.message : "申诉提交失败");
    } finally {
      setIsSubmittingAppeal(false);
    }
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

          {resources.map((resource) => {
            const badge = auditBadge(resource);
            const BadgeIcon = badge.Icon;
            const showAppeal = resource.copyright_audit.public_distribution !== "allowed";
            return (
            <article key={resource.id} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h5 className="truncate text-sm font-semibold text-gray-900">{resource.name}</h5>
                  <p className="mt-1 text-xs text-gray-500">
                    {resource.structure_regions.length} 个结构分区 · {resource.toc_entries.length} 个目录项 ·{" "}
                    {resource.chapter_shards.length} 个章节索引
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-2">
                  <span className={clsx("inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-semibold", badge.className)}>
                    <BadgeIcon className="h-3 w-3" />
                    {badge.label}
                  </span>
                  {resource.parse_warnings.length > 0 ? (
                    <span className="rounded-md bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-700">
                      {resource.parse_warnings.length} 项需复核
                    </span>
                  ) : null}
                </div>
              </div>

              <section className="mt-4 rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                <p className="text-xs font-medium text-gray-800">{resource.copyright_audit.reason || "版权公开传播状态待确认"}</p>
                {resource.copyright_audit.signals.length > 0 ? (
                  <p className="mt-1 truncate text-[11px] text-gray-500">{resource.copyright_audit.signals.join(" · ")}</p>
                ) : null}
                {showAppeal ? (
                  <button
                    type="button"
                    onClick={() => {
                      setAppealResource(resource);
                      setAppealStatus(null);
                    }}
                    className="mt-3 inline-flex h-8 items-center justify-center gap-2 rounded-md border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-700 transition hover:border-gray-300 hover:text-gray-950"
                  >
                    <ShieldAlert className="h-3.5 w-3.5" />
                    申诉
                  </button>
                ) : null}
              </section>

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
            );
          })}
        </div>
      </div>

      <CourseGraphPanel
        activeLesson={activeLesson}
        relatedEdges={relatedEdges}
        lessonMap={lessonMap}
        onOpenLesson={onOpenLesson}
      />

      {appealResource ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/35 px-4">
          <div className="w-full max-w-lg rounded-lg border border-gray-200 bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-gray-950">资料公开申诉</h3>
                <p className="mt-1 max-w-md truncate text-xs text-gray-500">{appealResource.name}</p>
              </div>
              <button
                type="button"
                onClick={() => setAppealResource(null)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <label className="mt-4 block text-xs font-semibold text-gray-700">
              申诉说明
              <textarea
                value={appealMessage}
                onChange={(event) => setAppealMessage(event.target.value)}
                className="mt-2 min-h-24 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-sm font-normal text-gray-900 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              />
            </label>
            <label className="mt-4 block text-xs font-semibold text-gray-700">
              证明内容
              <textarea
                value={appealEvidence}
                onChange={(event) => setAppealEvidence(event.target.value)}
                className="mt-2 min-h-24 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-sm font-normal text-gray-900 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              />
            </label>
            {appealStatus ? <p className="mt-3 text-xs font-medium text-gray-600">{appealStatus}</p> : null}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setAppealResource(null)}
                className="inline-flex h-9 items-center justify-center rounded-md border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-700 transition hover:border-gray-300"
              >
                关闭
              </button>
              <button
                type="button"
                onClick={submitAppeal}
                disabled={isSubmittingAppeal}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-gray-950 px-3 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmittingAppeal ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                提交
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
