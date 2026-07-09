"use client";

import clsx from "clsx";
import { BookOpen, Globe2, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { api } from "@/lib/api";
import type { SourceIngestionRecord, SourceStructureView } from "@/types";

type SourceImportPanelProps = {
  packageId: string;
  disabled?: boolean;
  onError: (message: string) => void;
};

const STATUS_LABELS: Record<SourceIngestionRecord["status"], string> = {
  queued: "等待",
  fetching: "获取",
  parsing: "解析",
  indexing: "索引",
  ready: "就绪",
  failed: "失败",
};

const STRUCTURE_STATUS_LABELS: Record<SourceIngestionRecord["structure_status"], string> = {
  pending: "待建索引",
  building: "结构索引",
  ready: "有可信目录",
  linear_only: "仅全文检索",
  failed: "结构失败",
};

const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>(["queued", "fetching", "parsing", "indexing"]);
const ACTIVE_STRUCTURE_STATUSES = new Set<SourceIngestionRecord["structure_status"]>(["pending", "building"]);

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function SourceImportPanel({ packageId, disabled = false, onError }: SourceImportPanelProps) {
  const [sources, setSources] = useState<SourceIngestionRecord[]>([]);
  const [sourceUri, setSourceUri] = useState("");
  const [title, setTitle] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
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
    try {
      const imported: SourceIngestionRecord[] = [];
      for (const file of fileList) {
        imported.push(
          await api.importPackageSource(packageId, {
            file,
            title: fileList.length === 1 ? title.trim() : "",
          })
        );
      }
      setSources((current) => [
        ...imported,
        ...current.filter((item) => !imported.some((record) => record.id === item.id)),
      ]);
      setTitle("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "文件导入失败");
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
        <div className="mt-3 flex items-center justify-end">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.epub,.doc,.docx,.txt,.md,.markdown,application/pdf,application/epub+zip,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
                "flex min-h-20 flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
              )}
            >
              {uploadButton}
              <span>{isImporting ? "正在解析资料。" : "继续拖入资料，或点击上传。"}</span>
            </div>
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                packageId={packageId}
                source={source}
                isRemoving={removingSourceId === source.id}
                onRemove={() => void removeSource(source.id)}
                onError={onError}
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
            <span>{isImporting ? "正在解析资料。" : isDragActive ? "松开上传资料。" : "拖拽文件到这里，或点击上传资料。"}</span>
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
}: {
  packageId: string;
  source: SourceIngestionRecord;
  isRemoving: boolean;
  onRemove: () => void;
  onError: (message: string) => void;
}) {
  const [structureView, setStructureView] = useState<SourceStructureView | null>(null);
  const [isStructureOpen, setIsStructureOpen] = useState(false);
  const [isLoadingStructure, setIsLoadingStructure] = useState(false);
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
  const isActive = ACTIVE_SOURCE_STATUSES.has(source.status);
  const structureLabel = structureStatusLabel(source);
  const structureIsGood = source.structure_status === "ready";
  const structureIsFailed = source.structure_status === "failed";

  async function toggleStructure() {
    if (!isReady || !source.structure_has_verified_toc) {
      return;
    }
    const nextOpen = !isStructureOpen;
    setIsStructureOpen(nextOpen);
    if (!nextOpen || structureView || isLoadingStructure) {
      return;
    }
    setIsLoadingStructure(true);
    try {
      setStructureView(await api.getPackageSourceStructure(packageId, source.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料结构读取失败");
    } finally {
      setIsLoadingStructure(false);
    }
  }

  const verifiedChapters = (structureView?.chapters ?? []).filter((chapter) => chapter.anchor_status === "verified");
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
          <div className="flex items-start justify-between gap-2">
            <p className="truncate text-sm font-semibold text-gray-900">{source.title}</p>
            <div className="flex shrink-0 items-center gap-1.5">
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
                    structureIsGood
                      ? "bg-blue-50 text-blue-700"
                      : structureIsFailed
                      ? "bg-amber-50 text-amber-700"
                      : source.structure_status === "linear_only"
                      ? "bg-gray-100 text-gray-600"
                      : "bg-sky-50 text-sky-700"
                  )}
                >
                  {structureLabel}
                </span>
              ) : null}
              {isReady && source.structure_has_verified_toc ? (
                <button
                  type="button"
                  onClick={() => void toggleStructure()}
                  disabled={isLoadingStructure}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-gray-400 transition hover:border-blue-100 hover:bg-blue-50 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                  title="查看目录"
                  aria-label={`查看资料目录 ${source.title}`}
                >
                  {isLoadingStructure ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <BookOpen className="h-3.5 w-3.5" />}
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
          <p className="mt-1 truncate text-xs text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {isActive ? <p className="mt-2 text-xs leading-5 text-gray-500">正在处理资料，大文件可能需要几分钟。</p> : null}
          {isReady && ACTIVE_STRUCTURE_STATUSES.has(source.structure_status) ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">正在建立目录与正文索引。</p>
          ) : null}
          {isReady && source.structure_status === "linear_only" ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">未发现可验证目录，本资料将按全文片段检索。</p>
          ) : null}
          {isReady && source.structure_has_verified_toc ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">已建立可验证目录，可按章节编号定位正文。</p>
          ) : null}
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
          {source.structure_error ? <p className="mt-2 text-xs leading-5 text-amber-700">{source.structure_error}</p> : null}
          {isStructureOpen && source.structure_has_verified_toc ? (
            <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/40 p-2">
              {verifiedChapters.length ? (
                <div className="space-y-1">
                  {verifiedChapters.slice(0, 10).map((chapter) => (
                    <div
                      key={chapter.id}
                      className="truncate text-xs text-gray-700"
                      style={{ paddingLeft: `${Math.min(Math.max(chapter.level - 1, 0), 4) * 12}px` }}
                      title={chapter.path.join(" > ") || chapter.title}
                    >
                      {chapter.number ? `${chapter.number} ` : ""}
                      {chapter.title}
                    </div>
                  ))}
                  {verifiedChapters.length > 10 ? (
                    <p className="text-[11px] text-gray-500">还有 {verifiedChapters.length - 10} 个已验证目录节点。</p>
                  ) : null}
                </div>
              ) : (
                <p className="text-xs text-gray-500">暂无可展示的已验证目录节点。</p>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function sourceNeedsRefresh(source: SourceIngestionRecord) {
  if (ACTIVE_SOURCE_STATUSES.has(source.status)) {
    return true;
  }
  return source.status === "ready" && ACTIVE_STRUCTURE_STATUSES.has(source.structure_status);
}

function structureStatusLabel(source: SourceIngestionRecord) {
  if (source.structure_has_verified_toc) {
    return "有可信目录";
  }
  return STRUCTURE_STATUS_LABELS[source.structure_status] ?? "结构状态";
}
