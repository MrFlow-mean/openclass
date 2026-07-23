"use client";

import Image from "next/image";
import { Video } from "lucide-react";
import { useEffect, useState } from "react";

import { selectionForModelOption } from "@/components/course-studio/model-catalog";
import { api } from "@/lib/api";
import type {
  AIModelOption,
  AIModelSelection,
  SourceIngestionRecord,
  SourceVisualAsset,
} from "@/types";

export function MediaUrlOptions({
  checked,
  disabled,
  transcriptionOptions,
  transcriptionSelection,
  visionOptions,
  visionSelection,
  browserAuthorization,
  browserAuthorizationAvailable,
  onCheckedChange,
  onTranscriptionChange,
  onVisionChange,
  onBrowserAuthorizationChange,
}: {
  checked: boolean;
  disabled: boolean;
  transcriptionOptions: AIModelOption[];
  transcriptionSelection: AIModelSelection;
  visionOptions: AIModelOption[];
  visionSelection: AIModelSelection;
  browserAuthorization: boolean;
  browserAuthorizationAvailable: boolean;
  onCheckedChange: (checked: boolean) => void;
  onTranscriptionChange: (selection: AIModelSelection) => void;
  onVisionChange: (selection: AIModelSelection) => void;
  onBrowserAuthorizationChange: (checked: boolean) => void;
}) {
  return (
    <>
      <label className="mt-2 flex items-center gap-2 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onCheckedChange(event.target.checked)}
          disabled={disabled}
          className="h-4 w-4 rounded border-gray-300 accent-black"
        />
        <Video className="h-3.5 w-3.5" />
        这是公开单视频 URL
      </label>
      {checked ? (
        <div className="mt-2 grid gap-2 rounded-md border border-blue-100 bg-blue-50/50 p-2">
          <MediaModelSelect
            label="语音转写模型"
            options={transcriptionOptions}
            selection={transcriptionSelection}
            onChange={onTranscriptionChange}
          />
          <MediaModelSelect
            label="视觉分析模型"
            options={visionOptions}
            selection={visionSelection}
            onChange={onVisionChange}
          />
          <label className="flex items-start gap-2 text-[11px] leading-4 text-gray-600">
            <input
              type="checkbox"
              checked={browserAuthorization}
              onChange={(event) => onBrowserAuthorizationChange(event.target.checked)}
              disabled={disabled || !browserAuthorizationAvailable}
              className="mt-0.5 h-3.5 w-3.5 rounded border-gray-300 accent-black"
            />
            <span>
              如 YouTube 要求验证，允许本次任务使用本机浏览器会话。
              {!browserAuthorizationAvailable ? " 当前部署未启用此授权。" : ""}
            </span>
          </label>
          <p className="text-[10px] leading-4 text-gray-500">优先使用视频自带字幕；无可用字幕时才调用语音转写模型。</p>
        </div>
      ) : null}
    </>
  );
}

export function SourceMediaSummary({
  source,
  isRetrying,
  onRetry,
}: {
  source: SourceIngestionRecord;
  isRetrying: boolean;
  onRetry: (operation: "retranscribe" | "visuals" | "browser_authorization") => void;
}) {
  const media = source.media_package;
  if (source.source_type !== "video_url") {
    return null;
  }
  const errorCode = String(source.metadata?.media_error_code ?? "");
  return (
    <div className="mt-2 rounded-md border border-blue-100 bg-blue-50/40 p-2 text-[11px] leading-5 text-gray-600">
      {media ? (
        <>
          <p>逐字稿 {media.transcript_segment_count} 段 · 章节 {media.chapter_count} 个 · 关键帧 {media.visual_count} 张</p>
          {media.warnings.map((warning) => <p key={warning} className="text-amber-700">{warning}</p>)}
        </>
      ) : null}
      {source.status === "failed" && errorCode === "youtube_authorization_required" ? (
        <button
          type="button"
          onClick={() => onRetry("browser_authorization")}
          disabled={isRetrying}
          className="mt-1 rounded border border-amber-200 bg-white px-2 py-1 font-medium text-amber-700 disabled:opacity-50"
        >
          授权本机浏览器后重试
        </button>
      ) : null}
      {source.status === "ready" ? (
        <div className="mt-1 flex gap-2">
          <button type="button" onClick={() => onRetry("retranscribe")} disabled={isRetrying} className="rounded border border-blue-200 bg-white px-2 py-1 font-medium text-blue-700 disabled:opacity-50">
            重新转写
          </button>
          <button type="button" onClick={() => onRetry("visuals")} disabled={isRetrying} className="rounded border border-blue-200 bg-white px-2 py-1 font-medium text-blue-700 disabled:opacity-50">
            重试关键帧
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function MediaVisualGrid({
  packageId,
  sourceId,
  visuals,
}: {
  packageId: string;
  sourceId: string;
  visuals: SourceVisualAsset[];
}) {
  if (!visuals.length) {
    return null;
  }
  return (
    <div className="mt-3 grid grid-cols-2 gap-2">
      {visuals.map((visual) => (
        <MediaVisualPreview key={visual.id} packageId={packageId} sourceId={sourceId} visual={visual} />
      ))}
    </div>
  );
}

function MediaModelSelect({
  label,
  options,
  selection,
  onChange,
}: {
  label: string;
  options: AIModelOption[];
  selection: AIModelSelection;
  onChange: (selection: AIModelSelection) => void;
}) {
  const enabledOptions = options.filter((option) => option.enabled);
  return (
    <label className="grid gap-1 text-[10px] font-semibold text-gray-600">
      {label}
      <select
        value={`${selection.provider}:${selection.model}`}
        onChange={(event) => {
          const option = enabledOptions.find((item) => `${item.provider}:${item.model}` === event.target.value);
          if (option) onChange(selectionForModelOption(option, selection));
        }}
        className="h-8 min-w-0 rounded-md border border-blue-100 bg-white px-2 text-xs font-normal text-gray-700 outline-none focus:border-blue-300"
      >
        {enabledOptions.map((option) => (
          <option key={`${option.provider}:${option.model}`} value={`${option.provider}:${option.model}`}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}

function MediaVisualPreview({ packageId, sourceId, visual }: { packageId: string; sourceId: string; visual: SourceVisualAsset }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void api
      .getSourceVisualContent(packageId, sourceId, visual.id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (active) setUrl("");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [packageId, sourceId, visual.id]);
  return (
    <figure className="overflow-hidden rounded border border-blue-100 bg-white">
      {url ? <Image src={url} alt={visual.caption || "视频关键帧"} width={320} height={180} unoptimized className="aspect-video w-full object-cover" /> : <div className="aspect-video animate-pulse bg-gray-100" />}
      <figcaption className="px-1.5 py-1 text-[10px] leading-4 text-gray-500">
        {formatMediaTimestamp(visual.timestamp_ms)} · {visual.caption || visual.media_role}
      </figcaption>
    </figure>
  );
}

function formatMediaTimestamp(timestampMs?: number | null) {
  const totalSeconds = Math.floor(Math.max(0, timestampMs ?? 0) / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}
