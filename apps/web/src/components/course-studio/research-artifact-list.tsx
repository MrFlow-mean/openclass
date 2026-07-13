"use client";

import clsx from "clsx";
import { LoaderCircle, RefreshCw, RotateCcw, Sparkles, Trash2, Volume2 } from "lucide-react";
import { useState } from "react";

import { ArtifactAudioPlayer, ResearchMarkdown } from "@/components/course-studio/research-content-components";
import { api } from "@/lib/api";
import type { ResearchArtifact, ResearchArtifactKind } from "@/types";

const KIND_LABELS: Record<ResearchArtifactKind, string> = {
  insight: "洞见",
  summary: "摘要",
  study_guide: "学习指南",
  faq: "问答集",
  timeline: "时间线",
  custom: "自定义",
  podcast: "播客",
};

const STATUS_LABELS: Record<ResearchArtifact["status"], string> = {
  queued: "等待生成",
  generating: "生成中",
  ready: "已完成",
  failed: "生成失败",
};

type ResearchArtifactListProps = {
  packageId: string;
  artifacts: ResearchArtifact[];
  currentKind: ResearchArtifactKind;
  onChange: (artifacts: ResearchArtifact[]) => void;
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
};

export function ResearchArtifactList({ packageId, artifacts, currentKind, onChange, onRefresh, onError }: ResearchArtifactListProps) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function remove(artifact: ResearchArtifact) {
    if (busyId || artifact.status === "queued" || artifact.status === "generating") return;
    if (!window.confirm(`删除生成物“${artifact.title || KIND_LABELS[artifact.kind]}”？`)) return;
    setBusyId(artifact.id);
    try {
      await api.deleteResearchArtifact(packageId, artifact.id);
      onChange(artifacts.filter((item) => item.id !== artifact.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究生成物删除失败");
    } finally {
      setBusyId(null);
    }
  }

  async function retry(artifact: ResearchArtifact) {
    if (busyId || artifact.status !== "failed") return;
    setBusyId(artifact.id);
    try {
      const queued = await api.retryResearchArtifact(packageId, artifact.id);
      onChange(artifacts.map((item) => item.id === queued.id ? queued : item));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究生成物重试失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-gray-700">已有生成物</p>
        <button type="button" onClick={() => void onRefresh()} className="flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:text-black" aria-label="刷新研究生成物">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      {artifacts.length ? artifacts.map((artifact) => (
        <article key={artifact.id} className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">{KIND_LABELS[artifact.kind]}</span>
                <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold", artifact.status === "ready" ? "bg-emerald-50 text-emerald-700" : artifact.status === "failed" ? "bg-rose-50 text-rose-700" : "bg-gray-100 text-gray-600")}>{STATUS_LABELS[artifact.status]}</span>
              </div>
              <p className="mt-2 truncate text-sm font-semibold text-gray-900">{artifact.title || KIND_LABELS[artifact.kind]}</p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {artifact.status === "failed" ? (
                <button type="button" onClick={() => void retry(artifact)} disabled={busyId === artifact.id} className="flex h-7 w-7 items-center justify-center rounded-md text-amber-600 hover:bg-amber-50 disabled:opacity-50" aria-label={`重试生成物 ${artifact.title || KIND_LABELS[artifact.kind]}`}>
                  {busyId === artifact.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                </button>
              ) : null}
              <button type="button" onClick={() => void remove(artifact)} disabled={busyId === artifact.id || artifact.status === "queued" || artifact.status === "generating"} className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-35" aria-label={`删除生成物 ${artifact.title || KIND_LABELS[artifact.kind]}`}>
                {busyId === artifact.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>
          {artifact.status === "queued" || artifact.status === "generating" ? <p className="mt-3 flex items-center gap-2 text-xs text-gray-500"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />{STATUS_LABELS[artifact.status]}</p> : null}
          {artifact.error ? <p className="mt-2 text-xs leading-5 text-rose-700">{artifact.error}</p> : null}
          {artifact.audio_url ? <div className="mt-3"><ArtifactAudioPlayer packageId={packageId} artifact={artifact} /></div> : null}
          {artifact.content ? <details className="mt-3 rounded-md border border-gray-100 bg-gray-50 p-2"><summary className="cursor-pointer text-xs font-semibold text-gray-600">查看内容</summary><div className="mt-2"><ResearchMarkdown content={artifact.content} /></div></details> : null}
          {artifact.transcript ? <details className="mt-2 rounded-md border border-gray-100 bg-gray-50 p-2"><summary className="cursor-pointer text-xs font-semibold text-gray-600">查看逐字稿</summary><p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-gray-700">{artifact.transcript}</p></details> : null}
        </article>
      )) : (
        <div className="rounded-lg border border-dashed border-gray-200 bg-white px-4 py-8 text-center">
          {currentKind === "podcast" ? <Volume2 className="mx-auto h-7 w-7 text-gray-300" /> : <Sparkles className="mx-auto h-7 w-7 text-gray-300" />}
          <p className="mt-2 text-xs text-gray-500">还没有研究生成物。</p>
        </div>
      )}
    </div>
  );
}
