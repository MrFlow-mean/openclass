"use client";

import clsx from "clsx";
import { FileUp, Globe2, RefreshCw, UploadCloud } from "lucide-react";
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

export function SourceImportPanel({ packageId, disabled = false, onError }: SourceImportPanelProps) {
  const [sources, setSources] = useState<SourceIngestionRecord[]>([]);
  const [sourceUri, setSourceUri] = useState("");
  const [title, setTitle] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
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

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (disabled || isImporting || !event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current += 1;
    setIsDragActive(true);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled || isImporting || !event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDragActive(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current = 0;
    setIsDragActive(false);
    if (disabled || isImporting) {
      return;
    }
    void submitFiles(event.dataTransfer.files);
  }

  return (
    <div className="space-y-4">
      <div
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={clsx(
          "rounded-lg border bg-white p-3 transition",
          isDragActive ? "border-black bg-gray-50" : "border-gray-200"
        )}
      >
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
        <div className="mt-3 flex items-center gap-2">
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
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || isImporting}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-gray-200 bg-white px-3 text-sm font-semibold text-gray-700 transition hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            <FileUp className="h-4 w-4" />
            文件
          </button>
          <span className="min-w-0 flex-1 truncate text-xs text-gray-500">
            {isDragActive ? "松开以上传文件" : "可拖入 PDF、DOCX、TXT 或 Markdown"}
          </span>
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

      <div className="space-y-2">
        {sources.length ? (
          sources.map((source) => <SourceRow key={source.id} source={source} />)
        ) : (
          <div className="rounded-lg border border-dashed border-gray-200 bg-white p-4 text-sm text-gray-500">
            暂无资料
          </div>
        )}
      </div>
    </div>
  );
}

function SourceRow({ source }: { source: SourceIngestionRecord }) {
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
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
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-sm font-semibold text-gray-900">{source.title}</p>
            <span
              className={clsx(
                "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold",
                isReady
                  ? "bg-emerald-50 text-emerald-700"
                  : isFailed
                  ? "bg-rose-50 text-rose-700"
                  : "bg-gray-100 text-gray-600"
              )}
            >
              {STATUS_LABELS[source.status]}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
        </div>
      </div>
    </div>
  );
}
