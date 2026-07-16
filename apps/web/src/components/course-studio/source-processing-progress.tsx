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

const SOURCE_STATUS_PROGRESS: Partial<Record<SourceIngestionRecord["status"], SourceProcessingState>> = {
  queued: { label: "等待开始解析", value: 12 },
  fetching: { label: "正在获取资料", value: 32 },
  parsing: { label: "正在解析正文", value: 58 },
  indexing: { label: "正在建立检索索引", value: 78 },
};

const JOB_PHASE_LABELS: Record<string, string> = {
  uploaded: "文件已接收，正在准备解析",
  parsing: "正在解析正文",
  reading_pages: "正在逐页读取正文",
  mapping_structure: "正在识别目录与正文结构",
  building_chunks: "正在建立检索片段",
  extracting_visuals: "正在提取图表与图片",
  persisting: "正在保存资料索引",
  transforming: "正在生成导入产物",
};

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
  const job = source.ingestion_job;
  if (job && ACTIVE_JOB_STATUSES.has(job.status)) {
    const phase = job.phase_history.at(-1) ?? "";
    return {
      label: JOB_PHASE_LABELS[phase] ?? SOURCE_STATUS_PROGRESS[job.status]?.label ?? "正在处理资料",
      value: job.progress,
    };
  }
  const ingestionState = SOURCE_STATUS_PROGRESS[source.status];
  if (ingestionState) {
    return ingestionState;
  }
  if (source.status !== "ready") {
    return null;
  }
  if (source.structure_status === "pending") {
    return { label: "正在准备结构索引", value: 88 };
  }
  if (source.structure_status === "building") {
    return { label: "正在绑定目录与正文", value: 94 };
  }
  return null;
}

const ACTIVE_JOB_STATUSES = new Set<SourceIngestionRecord["status"]>([
  "queued",
  "fetching",
  "parsing",
  "indexing",
]);
