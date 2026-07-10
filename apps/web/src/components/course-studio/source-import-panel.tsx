"use client";

import clsx from "clsx";
import { BookOpen, ChevronDown, ChevronRight, Globe2, RefreshCw, TextQuote, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { createSourceChapterSelection } from "@/components/course-studio/source-reference";
import {
  getSourceProcessingState,
  SourceProcessingProgress,
} from "@/components/course-studio/source-processing-progress";
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

export function SourceImportPanel({ packageId, disabled = false, onError, onSourceReference }: SourceImportPanelProps) {
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
                "flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-white px-4 text-center text-xs transition-colors",
                isDragActive && !disabled && !isImporting ? "border-blue-400 text-blue-700" : "border-gray-200 text-gray-400"
              )}
            >
              {uploadButton}
              {isImporting ? (
                <SourceProcessingProgress className="w-full max-w-60 text-left" label="正在上传并解析资料" />
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
              <SourceProcessingProgress className="w-full max-w-60 text-left" label="正在上传并解析资料" />
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
}: {
  packageId: string;
  source: SourceIngestionRecord;
  isRemoving: boolean;
  onRemove: () => void;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  const [structureView, setStructureView] = useState<SourceStructureView | null>(null);
  const [isStructureOpen, setIsStructureOpen] = useState(false);
  const [isLoadingStructure, setIsLoadingStructure] = useState(false);
  const [expandedChapterIds, setExpandedChapterIds] = useState<Set<string>>(new Set());
  const isReady = source.status === "ready";
  const isFailed = source.status === "failed";
  const structureLabel = structureStatusLabel(source);
  const structureIsGood = source.structure_status === "ready";
  const structureIsFailed = source.structure_status === "failed";
  const openNotebookSyncMessage = getOpenNotebookSyncMessage(source);
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
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料结构读取失败");
    } finally {
      setIsLoadingStructure(false);
    }
  }

  const verifiedChapters = (structureView?.chapters ?? []).filter((chapter) => chapter.anchor_status === "verified");
  const chapterTree = buildChapterTree(verifiedChapters);
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
          <p className="mt-1 truncate text-xs text-gray-500">{source.source_uri || source.file_name || source.mime_type}</p>
          {processingState ? (
            <SourceProcessingProgress className="mt-2" label={processingState.label} value={processingState.value} />
          ) : null}
          {isReady && source.structure_status === "linear_only" ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">未发现可验证目录，本资料将按全文片段检索。</p>
          ) : null}
          {isReady && source.structure_has_verified_toc ? (
            <p className="mt-2 text-xs leading-5 text-gray-500">
              已建立可验证目录；引用时会检查正文，扫描文件将自动尝试 OCR。
            </p>
          ) : null}
          {openNotebookSyncMessage ? <p className="mt-2 text-xs leading-5 text-amber-700">{openNotebookSyncMessage}</p> : null}
          {source.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{source.error}</p> : null}
          {source.structure_error ? <p className="mt-2 text-xs leading-5 text-amber-700">{source.structure_error}</p> : null}
          {isStructureOpen ? (
            <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/40 p-2">
              {chapterTree.length ? (
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
  const message =
    source.structure_status === "failed"
      ? source.structure_error || structure?.error || "目录结构索引失败。"
      : source.structure_status === "linear_only"
      ? isRemoteOnlyWebUrl
        ? "URL 资料 V1 暂不在 OpenClass 本地重建目录，当前使用 Open Notebook 全文检索。"
        : "未发现可验证目录，本资料当前只能按全文片段检索。旧上传资料如果没有保存本地原文件，需要重新上传后才能尝试建立目录。"
      : "目录结构还没有完成，稍后刷新资料状态。";
  const visibleWarnings = (structure?.warnings ?? []).filter(
    (warning) => isRemoteOnlyWebUrl || !warning.startsWith("URL 资料 V1")
  );
  return (
    <div className="space-y-2">
      <p className="text-xs leading-5 text-gray-600">{message}</p>
      {visibleWarnings.length ? (
        <div className="space-y-1">
          {visibleWarnings.slice(0, 2).map((warning) => (
            <p key={warning} className="text-[11px] leading-4 text-amber-700">
              {warning}
            </p>
          ))}
        </div>
      ) : null}
    </div>
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
  const title = chapterDisplayTitle(node.chapter);
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
        {onSourceReference ? (
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

function chapterDisplayTitle(chapter: SourceChapter) {
  if (!chapter.number || chapter.title.trim().startsWith(chapter.number)) {
    return chapter.title;
  }
  return `${chapter.number} ${chapter.title}`;
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

function structureStatusLabel(source: SourceIngestionRecord) {
  if (source.structure_has_verified_toc) {
    return "有可信目录";
  }
  return STRUCTURE_STATUS_LABELS[source.structure_status] ?? "结构状态";
}

function getOpenNotebookSyncMessage(source: SourceIngestionRecord) {
  const syncStatus = metadataString(source, "open_notebook_sync_status");
  if (!syncStatus || (source.source_type !== "local_file" && source.source_type !== "web_url")) {
    return "";
  }
  if (syncStatus === "unavailable" || syncStatus === "failed") {
    if (source.source_type === "web_url") {
      return "Open Notebook 未连接，已使用 OpenClass 本地网页快照解析；启动服务后重新导入可同步到 Open Notebook 检索。";
    }
    return "Open Notebook 未连接，已使用 OpenClass 本地解析；启动服务后重新上传可同步到 Open Notebook 检索。";
  }
  if (!["ready", "completed", "complete", "success", "succeeded", "done"].includes(syncStatus)) {
    return "已使用 OpenClass 本地解析，Open Notebook 同步仍在后台处理中。";
  }
  return "";
}

function metadataString(source: SourceIngestionRecord, key: string) {
  const value = source.metadata?.[key];
  return typeof value === "string" ? value : "";
}
