"use client";

import clsx from "clsx";
import { BookOpen, Check, ChevronDown, ChevronRight, ClipboardPaste, Download, FileText, Globe2, Pencil, RefreshCw, RotateCcw, TextQuote, Trash2, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { createSourceChapterSelection, sourceChapterLabel } from "@/components/course-studio/source-reference";
import {
  getSourceProcessingState,
  SourceProcessingProgress,
} from "@/components/course-studio/source-processing-progress";
import {
  SourceStructureQualitySummary,
  sourceStructureBadgeClass,
  sourceStructureBadgeLabel,
  sourceStructureQualityLevel,
  sourceStructureQualityNote,
} from "@/components/course-studio/source-structure-quality";
import { api } from "@/lib/api";
import type { SelectionRef, SourceChapter, SourceIngestionRecord, SourceStructureView } from "@/types";

type SourceImportPanelProps = {
  packageId: string;
  disabled?: boolean;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
};

type ChapterTreeNode = {
  chapter: SourceChapter;
  children: ChapterTreeNode[];
};

const STATUS_LABELS: Record<SourceIngestionRecord["status"], string> = {
  queued: "等待",
  fetching: "获取",
  parsing: "解析",
  indexing: "索引",
  ready: "就绪",
  failed: "失败",
};

const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>(["queued", "fetching", "parsing", "indexing"]);
const ACTIVE_STRUCTURE_STATUSES = new Set<SourceIngestionRecord["structure_status"]>(["pending", "building"]);

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function SourceImportPanel({ packageId, disabled = false, onError, onSourceReference }: SourceImportPanelProps) {
  const [sources, setSources] = useState<SourceIngestionRecord[]>([]);
  const [sourceUri, setSourceUri] = useState("");
  const [pastedText, setPastedText] = useState("");
  const [title, setTitle] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [removingSourceId, setRemovingSourceId] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);

  const refreshSources = useCallback(async () => {
    if (!packageId) {
      return;
    }
    setIsLoading(true);
    try {
      setSources(await api.listPackageSources(packageId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料列表读取失败");
    } finally {
      setIsLoading(false);
    }
  }, [onError, packageId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshSources();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshSources]);

  useEffect(() => {
    if (disabled || !sources.some(sourceNeedsRefresh)) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void refreshSources();
    }, 3000);
    return () => window.clearInterval(intervalId);
  }, [disabled, refreshSources, sources]);

  async function submitUrl() {
    const uri = sourceUri.trim();
    if (!uri || disabled || isImporting) {
      return;
    }
    setIsImporting(true);
    try {
      const record = await api.importPackageSource(packageId, { sourceUri: uri, title: title.trim() });
      setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
      setSourceUri("");
      setTitle("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "URL 导入失败");
    } finally {
      setIsImporting(false);
    }
  }

  async function submitFiles(files: FileList | File[] | null) {
    const fileList = Array.from(files ?? []);
    if (!fileList.length || disabled || isImporting) {
      return;
    }
    setIsImporting(true);
    setUploadProgress(0);
    const imported: SourceIngestionRecord[] = [];
    const failures: string[] = [];
    try {
      for (const [fileIndex, file] of fileList.entries()) {
        try {
          const record = await api.importPackageSource(
            packageId,
            {
              file,
              title: fileList.length === 1 ? title.trim() : "",
            },
            {
              onUploadProgress: (fileProgress) => {
                const overallProgress = ((fileIndex + fileProgress / 100) / fileList.length) * 100;
                setUploadProgress(Math.round(overallProgress));
              },
            }
          );
          imported.push(record);
          setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "导入失败"}`);
        }
      }
      setTitle("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } finally {
      setIsImporting(false);
      setUploadProgress(null);
    }
    if (failures.length) {
      onError(`${imported.length} 个文件导入成功，${failures.length} 个失败：${failures.join("；")}`);
    }
  }

  async function submitPastedText() {
    const text = pastedText.trim();
    if (!text || disabled || isImporting) {
      return;
    }
    setIsImporting(true);
    try {
      const record = await api.importPackageSource(packageId, { text, title: title.trim() });
      setSources((current) => [record, ...current.filter((item) => item.id !== record.id)]);
      setPastedText("");
      setTitle("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "粘贴文本导入失败");
    } finally {
      setIsImporting(false);
    }
  }

  async function removeSource(sourceId: string) {
    if (!sourceId || disabled || removingSourceId) {
      return;
    }
    setRemovingSourceId(sourceId);
    try {
      await api.deletePackageSource(packageId, sourceId);
      setSources((current) => current.filter((source) => source.id !== sourceId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料移除失败");
    } finally {
      setRemovingSourceId(null);
    }
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!dragIncludesFiles(event)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    if (!disabled && !isImporting) {
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
    event.dataTransfer.dropEffect = disabled || isImporting ? "none" : "copy";
    if (!disabled && !isImporting) {
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
    if (disabled || isImporting) {
      return;
    }
    void submitFiles(event.dataTransfer.files);
  }

  const uploadButton = (
    <button
      type="button"
      onClick={() => fileInputRef.current?.click()}
      disabled={disabled || isImporting}
      className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <UploadCloud className="h-3.5 w-3.5" />
      {isImporting ? "解析中" : isDragActive ? "松开上传" : "上传资料"}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <label className="text-[11px] font-bold uppercase tracking-widest text-gray-500">标题</label>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="可选"
          className="mt-2 h-9 w-full rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
          disabled={disabled || isImporting}
        />
        <label className="mt-3 block text-[11px] font-bold uppercase tracking-widest text-gray-500">URL</label>
        <div className="mt-2 flex gap-2">
          <input
            value={sourceUri}
            onChange={(event) => setSourceUri(event.target.value)}
            placeholder="https://"
            className="h-9 min-w-0 flex-1 rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
            disabled={disabled || isImporting}
          />
          <button
            type="button"
            onClick={() => void submitUrl()}
            disabled={!sourceUri.trim() || disabled || isImporting}
            className="flex h-9 w-9 items-center justify-center rounded-md bg-black text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
            title="导入 URL"
            aria-label="导入 URL"
          >
            <Globe2 className="h-4 w-4" />
          </button>
        </div>
        <label className="mt-3 block text-[11px] font-bold uppercase tracking-widest text-gray-500">粘贴文本</label>
        <div className="mt-2 flex items-end gap-2">
          <textarea
            value={pastedText}
            onChange={(event) => setPastedText(event.target.value)}
            placeholder="粘贴需要索引的正文或 Markdown"
            rows={3}
            className="min-h-20 min-w-0 flex-1 resize-y rounded-md border border-gray-200 px-3 py-2 text-sm outline-none transition focus:border-black"
            disabled={disabled || isImporting}
          />
          <button
            type="button"
            onClick={() => void submitPastedText()}
            disabled={!pastedText.trim() || disabled || isImporting}
            className="flex h-9 w-9 items-center justify-center rounded-md bg-black text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
            title="导入粘贴文本"
            aria-label="导入粘贴文本"
          >
            <ClipboardPaste className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-3 flex items-center justify-end">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.epub,.docx,.pptx,.xlsx,.csv,.txt,.md,.markdown,.html,.htm,.json,.xml,.png,.jpg,.jpeg,.webp,.gif,.mp3,.m4a,.wav,.ogg,.mp4,.mov,.webm,.mpeg,application/pdf,application/epub+zip,text/*,image/*,audio/*,video/*,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => void submitFiles(event.target.files)}
            className="hidden"
            disabled={disabled || isImporting}
          />
          <button
            type="button"
            onClick={() => void refreshSources()}
            disabled={disabled || isLoading}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-200 text-gray-500 transition hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
            title="刷新"
            aria-label="刷新资料状态"
          >
            <RefreshCw className={clsx("h-4 w-4", isLoading && "animate-spin")} />
          </button>
        </div>
      </div>

      <div
        aria-busy={isImporting}
        aria-disabled={disabled || isImporting}
        aria-label="资料上传区域"
        data-testid="source-upload-dropzone"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={clsx(
          "rounded-lg transition",
          isDragActive && !disabled && !isImporting && "bg-blue-50/70 ring-2 ring-blue-200"
        )}
      >
        {sources.length ? (
          <div className="space-y-2">
            <div
              className={clsx(
                "flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
              )}
            >
              {uploadButton}
              {isImporting ? (
                <SourceProcessingProgress
                  className="w-full max-w-60 text-left"
                  label={uploadProgress === 100 ? "上传完成，正在创建处理任务" : "正在上传资料"}
                  value={uploadProgress ?? undefined}
                />
              ) : (
                <span>继续拖入资料，或点击上传。</span>
              )}
            </div>
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                packageId={packageId}
                source={source}
                isRemoving={removingSourceId === source.id}
                onRemove={() => void removeSource(source.id)}
                onError={onError}
                onSourceReference={onSourceReference}
                onSourceUpdate={(updatedSource) =>
                  setSources((current) =>
                    current.map((item) => (item.id === updatedSource.id ? updatedSource : item))
                  )
                }
              />
            ))}
          </div>
        ) : (
          <div
            className={clsx(
              "flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
              isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
            )}
          >
            <UploadCloud className={clsx("h-8 w-8", isDragActive ? "text-blue-600" : "text-gray-300")} />
            {uploadButton}
            {isImporting ? (
              <SourceProcessingProgress
                className="w-full max-w-60 text-left"
                label={uploadProgress === 100 ? "上传完成，正在创建处理任务" : "正在上传资料"}
                value={uploadProgress ?? undefined}
              />
            ) : (
              <span>{isDragActive ? "松开上传资料。" : "拖拽文件到这里，或点击上传资料。"}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SourceRow({
  packageId,
  source,
  isRemoving,
  onRemove,
  onError,
  onSourceReference,
  onSourceUpdate,
}: {
  packageId: string;
  source: SourceIngestionRecord;
  isRemoving: boolean;
  onRemove: () => void;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  onSourceUpdate: (source: SourceIngestionRecord) => void;
}) {
  const [structureView, setStructureView] = useState<SourceStructureView | null>(null);
  const [isStructureOpen, setIsStructureOpen] = useState(false);
  const [isLoadingStructure, setIsLoadingStructure] = useState(false);
  const [isRebuildingStructure, setIsRebuildingStructure] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(source.title);
  const [content, setContent] = useState<string | null>(null);
  const [draftContent, setDraftContent] = useState("");
  const [isContentOpen, setIsContentOpen] = useState(false);
  const [isLoadingContent, setIsLoadingContent] = useState(false);
  const [isEditingContent, setIsEditingContent] = useState(false);
  const [isSavingContent, setIsSavingContent] = useState(false);
  const [expandedChapterIds, setExpandedChapterIds] = useState<Set<string>>(new Set());
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
  const sourceQuality = source.structure_quality;
  const viewQuality = structureView?.structure?.quality;
  const structureQuality =
    viewQuality?.level && viewQuality.level !== "unassessed"
      ? viewQuality
      : sourceQuality;
  const structureQualityLevel = sourceStructureQualityLevel(source, structureQuality);
  const structureLabel = sourceStructureBadgeLabel(
    source,
    structureQualityLevel,
    structureQuality
  );
  const structureNote = sourceStructureQualityNote(
    source,
    structureQuality,
    structureQualityLevel
  );
  const processingState = getSourceProcessingState(source);

  async function toggleStructure() {
    if (!isReady) {
      return;
    }
    const nextOpen = !isStructureOpen;
    setIsStructureOpen(nextOpen);
    if (!nextOpen || structureView || isLoadingStructure) {
      return;
    }
    setIsLoadingStructure(true);
    try {
      const view = await api.getPackageSourceStructure(packageId, source.id);
      setStructureView(view);
      setExpandedChapterIds(new Set());
      onSourceUpdate(view.source);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料结构读取失败");
    } finally {
      setIsLoadingStructure(false);
    }
  }

  async function rebuildStructure() {
    if (!isReady || isRebuildingStructure) {
      return;
    }
    setIsRebuildingStructure(true);
    try {
      const view = await api.rebuildPackageSourceStructure(packageId, source.id);
      setStructureView(view);
      setIsStructureOpen(true);
      setExpandedChapterIds(new Set());
      onSourceUpdate(view.source);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料目录重建失败");
    } finally {
      setIsRebuildingStructure(false);
    }
  }

  async function retrySource() {
    if (isRetrying) {
      return;
    }
    setIsRetrying(true);
    try {
      onSourceUpdate(await api.retryPackageSource(packageId, source.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料重试失败");
    } finally {
      setIsRetrying(false);
    }
  }

  async function saveTitle() {
    const nextTitle = draftTitle.trim();
    if (!nextTitle || nextTitle === source.title) {
      setDraftTitle(source.title);
      setIsEditingTitle(false);
      return;
    }
    try {
      onSourceUpdate(await api.renamePackageSource(packageId, source.id, nextTitle));
      setIsEditingTitle(false);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料重命名失败");
    }
  }

  async function toggleContent() {
    const nextOpen = !isContentOpen;
    setIsContentOpen(nextOpen);
    if (!nextOpen || content !== null || isLoadingContent) {
      return;
    }
    setIsLoadingContent(true);
    try {
      const nextContent = (await api.getPackageSourceContent(packageId, source.id)).content;
      setContent(nextContent);
      setDraftContent(nextContent);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料正文读取失败");
    } finally {
      setIsLoadingContent(false);
    }
  }

  async function saveContent() {
    const nextContent = draftContent.trim();
    if (!nextContent || isSavingContent) {
      return;
    }
    setIsSavingContent(true);
    try {
      const result = await api.updatePackageSourceContent(packageId, source.id, nextContent);
      setContent(result.content);
      setDraftContent(result.content);
      setStructureView(null);
      setExpandedChapterIds(new Set());
      setIsEditingContent(false);
      onSourceUpdate(result.source);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料正文保存失败");
    } finally {
      setIsSavingContent(false);
    }
  }

  async function downloadSource() {
    try {
      const blob = await api.downloadPackageSource(packageId, source.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = source.file_name || source.title;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料下载失败");
    }
  }

  const chapterTree = buildChapterTree(structureView?.chapters ?? []);
  function toggleChapter(chapterId: string) {
    setExpandedChapterIds((current) => {
      const next = new Set(current);
      if (next.has(chapterId)) {
        next.delete(chapterId);
      } else {
        next.add(chapterId);
      }
      return next;
    });
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex items-start gap-3">
        <div
          className={clsx(
            "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
            isReady ? "bg-emerald-50 text-emerald-700" : isFailed ? "bg-rose-50 text-rose-700" : "bg-gray-50 text-gray-500"
          )}
        >
          <UploadCloud className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="space-y-2">
            {isEditingTitle ? (
              <div className="flex min-w-0 flex-1 items-center gap-1">
                <input
                  value={draftTitle}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void saveTitle();
                    if (event.key === "Escape") setIsEditingTitle(false);
                  }}
                  className="h-7 min-w-0 flex-1 rounded border border-gray-300 px-2 text-sm outline-none focus:border-black"
                  autoFocus
                />
                <button type="button" onClick={() => void saveTitle()} className="rounded p-1 text-emerald-700" aria-label="保存资料标题">
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button type="button" onClick={() => setIsEditingTitle(false)} className="rounded p-1 text-gray-500" aria-label="取消修改资料标题">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex min-w-0 items-start gap-1">
                <p className="min-w-0 flex-1 break-words text-sm font-semibold leading-5 text-gray-900">{source.title}</p>
                <button type="button" onClick={() => setIsEditingTitle(true)} className="shrink-0 rounded p-1 text-gray-400 hover:text-black" aria-label={`重命名资料 ${source.title}`}>
                  <Pencil className="h-3 w-3" />
                </button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={clsx(
                  "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                  isReady
                    ? "bg-emerald-50 text-emerald-700"
                    : isFailed
                    ? "bg-rose-50 text-rose-700"
                    : "bg-gray-100 text-gray-600"
                )}
              >
                {STATUS_LABELS[source.status]}
              </span>
              {isReady ? (
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    sourceStructureBadgeClass(
                      source,
                      structureQualityLevel,
                      structureQuality
                    )
                  )}
                >
                  {structureLabel}
                </span>
              ) : null}
              {isReady ? (
                <button
                  type="button"
                  onClick={() => void toggleContent()}
                  disabled={isLoadingContent}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black disabled:opacity-50"
                  title="查看完整正文"
                  aria-label={`查看资料正文 ${source.title}`}
                >
                  {isLoadingContent ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void downloadSource()}
                className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-black"
                title="下载原始资料"
                aria-label={`下载资料 ${source.title}`}
              >
                <Download className="h-3.5 w-3.5" />
              </button>
              {isFailed ? (
                <button
                  type="button"
                  onClick={() => void retrySource()}
                  disabled={isRetrying}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-amber-600 transition hover:bg-amber-50 disabled:opacity-50"
                  title="重试资料处理"
                  aria-label={`重试资料 ${source.title}`}
                >
                  <RotateCcw className={clsx("h-3.5 w-3.5", isRetrying && "animate-spin")} />
                </button>
              ) : null}
              {isReady ? (
                <button
                  type="button"
                  onClick={() => void toggleStructure()}
                  disabled={isLoadingStructure}
                  className={clsx(
                    "flex min-w-10 flex-col items-center justify-center gap-0.5 rounded-md border border-transparent px-1 py-1 text-[10px] leading-none transition disabled:cursor-not-allowed disabled:opacity-50",
                    source.structure_has_verified_toc
                      ? "text-blue-600 hover:border-blue-100 hover:bg-blue-50"
                      : "text-gray-400 hover:border-gray-200 hover:bg-gray-50 hover:text-gray-600"
                  )}
                  title={source.structure_has_verified_toc ? "查看目录" : "查看目录状态"}
                  aria-label={`${source.structure_has_verified_toc ? "查看资料目录" : "查看资料目录状态"} ${source.title}`}
                >
                  {isLoadingStructure ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <BookOpen className="h-3.5 w-3.5" />}
                  <span>{isStructureOpen ? "收起" : "展开"}</span>
                </button>
              ) : null}
              <button
                type="button"
                onClick={onRemove}
                disabled={isRemoving}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-gray-400 transition hover:border-rose-100 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
                title="移除资料"
                aria-label={`移除资料 ${source.title}`}
              >
                {isRemoving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>
          <p className="mt-2 break-all text-xs leading-5 text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {processingState ? (
            <SourceProcessingProgress className="mt-2" label={processingState.label} value={processingState.value} />
          ) : null}
          {isReady ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">{structureNote}</p>
          ) : null}
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
          {source.structure_error ? <p className="mt-2 text-xs leading-5 text-amber-700">{source.structure_error}</p> : null}
          {isContentOpen ? (
            <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-2">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[11px] font-semibold text-gray-600">可检索正文</p>
                {isEditingContent ? (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => void saveContent()}
                      disabled={!draftContent.trim() || isSavingContent}
                      className="rounded p-1 text-emerald-700 disabled:opacity-40"
                      aria-label={`保存资料正文 ${source.title}`}
                    >
                      {isSavingContent ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDraftContent(content ?? "");
                        setIsEditingContent(false);
                      }}
                      disabled={isSavingContent}
                      className="rounded p-1 text-gray-500 disabled:opacity-40"
                      aria-label={`取消编辑资料正文 ${source.title}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setDraftContent(content ?? "");
                      setIsEditingContent(true);
                    }}
                    disabled={!content}
                    className="rounded p-1 text-gray-500 hover:bg-white hover:text-black disabled:opacity-40"
                    aria-label={`编辑资料正文 ${source.title}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              {isEditingContent ? (
                <textarea
                  value={draftContent}
                  onChange={(event) => setDraftContent(event.target.value)}
                  rows={14}
                  className="w-full resize-y rounded border border-gray-200 bg-white px-2 py-2 text-[11px] leading-5 text-gray-700 outline-none focus:border-black"
                />
              ) : (
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-gray-700">
                  {content || "资料中没有可显示的文字正文。"}
                </pre>
              )}
            </div>
          ) : null}
          {isStructureOpen ? (
            <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/40 p-2">
              <SourceStructureQualitySummary
                source={source}
                quality={structureQuality}
                warnings={structureView?.structure?.warnings}
              />
              <div className="mb-1 mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => void rebuildStructure()}
                  disabled={isRebuildingStructure}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-blue-100 bg-white text-blue-600 transition hover:border-blue-200 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                  title="重新建立目录"
                  aria-label={`重新建立资料目录 ${source.title}`}
                >
                  <RefreshCw className={clsx("h-3.5 w-3.5", isRebuildingStructure && "animate-spin")} />
                </button>
              </div>
              {isLoadingStructure ? (
                <p className="text-xs leading-5 text-gray-600">正在读取目录…</p>
              ) : chapterTree.length ? (
                <SourceChapterTree
                  source={source}
                  nodes={chapterTree}
                  expandedIds={expandedChapterIds}
                  onToggle={toggleChapter}
                  onSourceReference={onSourceReference}
                />
              ) : (
                <SourceStructureEmptyState source={source} structureView={structureView} />
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SourceStructureEmptyState({
  source,
  structureView,
}: {
  source: SourceIngestionRecord;
  structureView: SourceStructureView | null;
}) {
  const structure = structureView?.structure;
  const isWebUrl = source.source_type === "web_url";
  const hasLocalSnapshot = Boolean(metadataString(source, "local_source_path"));
  const isRemoteOnlyWebUrl = isWebUrl && !hasLocalSnapshot;
  const quality = structure?.quality ?? source.structure_quality;
  const hasNoIndexedBody =
    source.structure_status === "linear_only" && structure?.chunk_count === 0;
  const message =
    quality?.text_readiness === "empty" || hasNoIndexedBody
      ? "这份资料没有提取到可检索正文，目录引用和全文检索当前都不可用。请检查文件文字层或 OCR 结果后重建。"
      : source.structure_status === "failed"
      ? source.structure_error || structure?.error || "目录结构索引失败。"
      : source.structure_status === "linear_only"
      ? isRemoteOnlyWebUrl
        ? "这份 URL 资料未形成可验证目录，当前使用 OpenClass 原生全文片段检索。"
        : "未发现可验证目录，本资料当前只能按全文片段检索。旧上传资料如果没有保存本地原文件，需要重新上传后才能尝试建立目录。"
      : "目录结构还没有完成，稍后刷新资料状态。";
  return (
    <p className="text-xs leading-5 text-gray-600">{message}</p>
  );
}

function SourceChapterTree({
  source,
  nodes,
  expandedIds,
  onToggle,
  onSourceReference,
}: {
  source: SourceIngestionRecord;
  nodes: ChapterTreeNode[];
  expandedIds: Set<string>;
  onToggle: (chapterId: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  return (
    <div className="space-y-1">
      {nodes.map((node) => (
        <SourceChapterNode
          key={node.chapter.id}
          source={source}
          node={node}
          expandedIds={expandedIds}
          onToggle={onToggle}
          onSourceReference={onSourceReference}
          depth={0}
        />
      ))}
    </div>
  );
}

function SourceChapterNode({
  source,
  node,
  expandedIds,
  onToggle,
  onSourceReference,
  depth,
}: {
  source: SourceIngestionRecord;
  node: ChapterTreeNode;
  expandedIds: Set<string>;
  onToggle: (chapterId: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  depth: number;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expandedIds.has(node.chapter.id);
  const isVerified = node.chapter.anchor_status === "verified";
  const title = sourceChapterLabel(node.chapter);
  return (
    <div>
      <div
        className="group flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-gray-700 transition hover:bg-white"
        style={{ paddingLeft: `${Math.min(depth, 5) * 12 + 6}px` }}
      >
        <button
          type="button"
          onClick={() => (hasChildren ? onToggle(node.chapter.id) : undefined)}
          className={clsx("flex min-w-0 flex-1 items-center gap-1 text-left", !hasChildren && "cursor-default")}
          title={node.chapter.path.join(" > ") || title}
        >
          {hasChildren ? (
            isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-blue-600" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-gray-400" />
            )
          ) : (
            <span className="h-3.5 w-3.5 shrink-0" />
          )}
          <span className="min-w-0 flex-1 truncate">{title || "未命名章节"}</span>
        </button>
        {!isVerified ? (
          <span className="shrink-0 text-[10px] font-medium text-amber-700" title="目录条目已识别，正文范围尚未验证">
            正文待验证
          </span>
        ) : null}
        {onSourceReference && isVerified ? (
          <button
            type="button"
            onClick={() => onSourceReference(createSourceChapterSelection(source, node.chapter))}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-emerald-200 bg-emerald-50 text-emerald-700 shadow-sm transition hover:border-emerald-300 hover:bg-emerald-100 hover:text-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
            title="引用到输入框"
            aria-label={`引用章节到输入框 ${title || "未命名章节"}`}
          >
            <TextQuote className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      {hasChildren && isExpanded ? (
        <div className="mt-0.5 space-y-0.5">
          {node.children.map((child) => (
            <SourceChapterNode
              key={child.chapter.id}
              source={source}
              node={child}
              expandedIds={expandedIds}
              onToggle={onToggle}
              onSourceReference={onSourceReference}
              depth={depth + 1}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function buildChapterTree(chapters: SourceChapter[]): ChapterTreeNode[] {
  const sorted = [...chapters].sort((left, right) => left.order_index - right.order_index);
  if (sorted.some((chapter) => chapter.parent_id)) {
    const nodeById = new Map(sorted.map((chapter) => [chapter.id, { chapter, children: [] as ChapterTreeNode[] }]));
    const roots: ChapterTreeNode[] = [];
    for (const chapter of sorted) {
      const node = nodeById.get(chapter.id);
      if (!node) {
        continue;
      }
      const parent = chapter.parent_id ? nodeById.get(chapter.parent_id) : null;
      if (parent) {
        parent.children.push(node);
      } else {
        roots.push(node);
      }
    }
    return roots;
  }
  const roots: ChapterTreeNode[] = [];
  const stack: ChapterTreeNode[] = [];
  for (const chapter of sorted) {
    const node: ChapterTreeNode = { chapter, children: [] };
    while (stack.length && stack[stack.length - 1].chapter.level >= chapter.level) {
      stack.pop();
    }
    const parent = stack[stack.length - 1];
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
    stack.push(node);
  }
  return roots;
}

function sourceNeedsRefresh(source: SourceIngestionRecord) {
  if (ACTIVE_SOURCE_STATUSES.has(source.status)) {
    return true;
  }
  return source.status === "ready" && ACTIVE_STRUCTURE_STATUSES.has(source.structure_status);
}

function metadataString(source: SourceIngestionRecord, key: string) {
  const value = source.metadata?.[key];
  return typeof value === "string" ? value : "";
}
