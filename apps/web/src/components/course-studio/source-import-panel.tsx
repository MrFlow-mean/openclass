"use client";

import clsx from "clsx";
import { Globe2, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { api } from "@/lib/api";
import type { SourceIngestionRecord } from "@/types";

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

const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>(["queued", "fetching", "parsing", "indexing"]);

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
    if (disabled || !sources.some((source) => ACTIVE_SOURCE_STATUSES.has(source.status))) {
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
            accept=".pdf,.doc,.docx,.txt,.md,.markdown,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
                source={source}
                isRemoving={removingSourceId === source.id}
                onRemove={() => void removeSource(source.id)}
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
  source,
  isRemoving,
  onRemove,
}: {
  source: SourceIngestionRecord;
  isRemoving: boolean;
  onRemove: () => void;
}) {
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
  const isActive = ACTIVE_SOURCE_STATUSES.has(source.status);
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
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
        </div>
      </div>
    </div>
  );
}
