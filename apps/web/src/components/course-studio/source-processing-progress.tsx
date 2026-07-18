import clsx from "clsx";

import type { SourceIngestionRecord } from "@/types";

type SourceProcessingProgressProps = {
  label: string;
  value?: number;
  className?: string;
};

type SourceProcessingState = {
  label: string;
  value: number;
};

const LEGACY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "等待开始解析", value: 12 },
  fetching: { label: "正在获取资料", value: 32 },
  parsing: { label: "正在解析正文", value: 58 },
  indexing: { label: "正在建立检索索引", value: 78 },
};

const DIRECTORY_SOURCE_STATUS_PROGRESS: Partial<
  Record<SourceIngestionRecord["status"], SourceProcessingState>
> = {
  queued: { label: "等待建立目录", value: 12 },
  fetching: { label: "正在获取资料", value: 32 },
  parsing: { label: "正在读取文件结构", value: 58 },
  indexing: { label: "正在建立目录", value: 78 },
};

const LEGACY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "文件已接收，正在准备解析",
  parsing: "正在解析正文",
  reading_pages: "正在逐页读取正文",
  mapping_structure: "正在识别目录与正文结构",
  building_chunks: "正在建立检索片段",
  extracting_visuals: "正在提取图表与图片",
  persisting: "正在保存资料索引",
  transforming: "正在生成导入产物",
};

const DIRECTORY_JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "文件已接收，正在准备建立目录",
  parsing: "正在读取文件结构",
  reading_directory_metadata: "正在读取目录元数据",
  locating_toc_pages: "正在定位目录页",
  mapping_directory_to_pages: "正在绑定目录与文件范围",
  scanning_heading_regions: "正在检查页面标题区域",
  normalizing_directory: "正在整理目录层级",
  validating_directory_ranges: "正在验证目录范围",
  publishing_catalog: "正在保存目录",
  catalog_ready: "目录已经保存",
};

export function isDirectoryCatalogSource(source: SourceIngestionRecord) {
  return (
    source.structure_strategy === "codex_directory_v1" ||
    source.ingestion_job?.adapter === "codex_directory_v1" ||
    source.metadata.catalog_pipeline === "codex_directory_v1"
  );
}

export function SourceProcessingProgress({ label, value, className }: SourceProcessingProgressProps) {
  const isDeterminate = typeof value === "number";
  const normalizedValue = isDeterminate ? Math.max(0, Math.min(100, Math.round(value))) : undefined;

  return (
    <div className={clsx("min-w-0", className)} aria-live="polite">
      <div className="mb-1.5 flex items-center justify-between gap-3 text-[11px] font-medium text-gray-500">
        <span className="truncate">{label}</span>
        {isDeterminate ? <span className="shrink-0 tabular-nums text-emerald-700">{normalizedValue}%</span> : null}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={isDeterminate ? 0 : undefined}
        aria-valuemax={isDeterminate ? 100 : undefined}
        aria-valuenow={normalizedValue}
        aria-valuetext={isDeterminate ? undefined : "处理中"}
        className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100"
      >
        <div
          className={clsx(
            "h-full rounded-full bg-emerald-500 transition-[width] duration-500 ease-out",
            !isDeterminate && "source-processing-progress__indeterminate"
          )}
          style={isDeterminate ? { width: `${normalizedValue}%` } : undefined}
        />
      </div>
    </div>
  );
}

export function getSourceProcessingState(source: SourceIngestionRecord): SourceProcessingState | null {
  const isDirectoryCatalog = isDirectoryCatalogSource(source);
  const statusProgress = isDirectoryCatalog
    ? DIRECTORY_SOURCE_STATUS_PROGRESS
    : LEGACY_SOURCE_STATUS_PROGRESS;
  const phaseLabels = isDirectoryCatalog
    ? DIRECTORY_JOB_PHASE_LABELS
    : LEGACY_JOB_PHASE_LABELS;
  const job = source.ingestion_job;
  if (job && ACTIVE_JOB_STATUSES.has(job.status)) {
    const phase = job.phase_history.at(-1) ?? "";
    return {
      label:
        phaseLabels[phase] ??
        statusProgress[job.status]?.label ??
        (isDirectoryCatalog ? "正在建立目录" : "正在处理资料"),
      value: job.progress,
    };
  }
  const ingestionState = statusProgress[source.status];
  if (ingestionState) {
    return ingestionState;
  }
  if (source.status !== "ready") {
    return null;
  }
  if (source.structure_status === "pending") {
    return {
      label: isDirectoryCatalog ? "正在准备建立目录" : "正在准备结构索引",
      value: 88,
    };
  }
  if (source.structure_status === "building") {
    return {
      label: isDirectoryCatalog ? "正在验证目录范围" : "正在绑定目录与正文",
      value: 94,
    };
  }
  return null;
}

const ACTIVE_JOB_STATUSES = new Set<SourceIngestionRecord["status"]>([
  "queued",
  "fetching",
  "parsing",
  "indexing",
]);
