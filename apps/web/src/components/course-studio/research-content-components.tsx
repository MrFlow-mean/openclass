"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { markdownToChatHtml } from "@/lib/markdown";
import type { ResearchArtifact } from "@/types";

export function ResearchMarkdown({ content }: { content: string }) {
  return (
    <div
      className="chat-markdown space-y-2 text-xs leading-6 text-gray-700 [&_code:not(.hljs)]:rounded [&_code:not(.hljs)]:bg-black/5 [&_code:not(.hljs)]:px-1 [&_strong]:font-semibold"
      dangerouslySetInnerHTML={{ __html: markdownToChatHtml(content) }}
    />
  );
}

export function ChoiceList({
  title,
  items,
  selectedIds,
  onChange,
}: {
  title: string;
  items: Array<{ id: string; title: string }>;
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  if (!items.length) return null;
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{title}</legend>
      {items.map((item) => {
        const checked = selectedIds.includes(item.id);
        return (
          <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-xs text-gray-700 hover:bg-gray-50">
            <input type="checkbox" checked={checked} onChange={() => onChange(checked ? selectedIds.filter((id) => id !== item.id) : [...selectedIds, item.id])} className="mt-0.5" />
            <span className="min-w-0 flex-1 truncate">{item.title}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

export function ArtifactAudioPlayer({ packageId, artifact }: { packageId: string; artifact: ResearchArtifact }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false;
    let objectUrl = "";
    void api.getResearchArtifactAudio(packageId, artifact.id).then((blob) => {
      if (!disposed) {
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      }
    }).catch((loadError) => {
      if (!disposed) setError(loadError instanceof Error ? loadError.message : "音频读取失败");
    });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.audio_url, artifact.id, packageId]);

  if (error) return <p className="text-xs leading-5 text-rose-700">{error}</p>;
  if (!src) return <p className="inline-flex items-center gap-1.5 text-xs text-gray-500"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />正在准备音频</p>;
  return <audio controls preload="none" src={src} className="w-full" />;
}
