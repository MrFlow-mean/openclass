"use client";

import clsx from "clsx";
import { AlertTriangle, BadgeCheck, ChevronDown, FileText, MapPin, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { ResourceContextChunk, ResourceMatch, ResourceReferenceContext, ResourceReferencePrompt } from "@/types";

type ResourceReferencePromptCardProps = {
  prompt: ResourceReferencePrompt;
  matches: ResourceMatch[];
  onReferenceAction: (action: "confirm" | "skip") => void | Promise<void>;
};

type SelectedResourceReferenceCardProps = {
  reference: ResourceReferenceContext;
};

export function ResourceReferencePromptCard({
  prompt,
  matches,
  onReferenceAction,
}: ResourceReferencePromptCardProps) {
  const { texts: txt } = useInterfaceLanguage();
  const s = txt.studio.chatSidebar;
  const evidenceText = txt.studio.chatSidebar.evidence;
  const hasTextEvidence = prompt.text_evidence_available !== false && !prompt.requires_text_fallback_confirmation;
  const tone = evidenceTone(hasTextEvidence);

  return (
    <article className={clsx("rounded-xl border p-4", tone.panel)}>
      <div className="flex items-start gap-3">
        <span className={clsx("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", tone.icon)}>
          {hasTextEvidence ? <Sparkles className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className={clsx("text-[11px] font-bold uppercase tracking-widest", tone.title)}>{s.referenceTitle}</p>
            <EvidenceStatusBadge hasTextEvidence={hasTextEvidence} textSource={firstTextSource(matches)} />
          </div>
          <p className={clsx("mt-2 text-sm leading-6", tone.body)}>{prompt.question}</p>
          <p className={clsx("mt-2 text-xs leading-6", tone.muted)}>{prompt.reason}</p>
        </div>
      </div>

      {matches.length ? (
        <div className={clsx("mt-3 space-y-3 border-t pt-3", tone.divider)}>
          {matches.slice(0, 3).map((match) => (
            <MatchEvidenceBlock key={`${match.resource_id}-${match.segment_id ?? match.chapter_id}`} match={match} />
          ))}
        </div>
      ) : null}

      <div className="mt-3 grid gap-2">
        <button
          type="button"
          onClick={() => void onReferenceAction("confirm")}
          className={clsx("w-full rounded-xl border bg-white px-4 py-3 text-left transition", tone.button)}
        >
          <span className="block text-sm font-semibold text-gray-900">{prompt.confirm_label}</span>
        </button>
        <button
          type="button"
          onClick={() => void onReferenceAction("skip")}
          className={clsx("w-full rounded-xl border bg-white px-4 py-3 text-left transition", tone.button)}
        >
          <span className="block text-sm font-semibold text-gray-900">{prompt.skip_label}</span>
        </button>
      </div>

      {!hasTextEvidence ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-100/70 px-3 py-2 text-[11px] leading-5 text-amber-900">
          {evidenceText.degraded}
        </p>
      ) : null}
    </article>
  );
}

export function SelectedResourceReferenceCard({ reference }: SelectedResourceReferenceCardProps) {
  const { texts: txt } = useInterfaceLanguage();
  const s = txt.studio.chatSidebar;
  const evidenceText = txt.studio.chatSidebar.evidence;
  const hasTextEvidence = reference.text_evidence_available !== false;
  const tone = evidenceTone(hasTextEvidence);
  const targetChunk =
    reference.chunks.find((chunk) => chunk.segment_id === reference.segment_id) ?? reference.chunks[0] ?? null;

  return (
    <details
      key={`${reference.resource_id}-${reference.chapter_id}-${reference.segment_id ?? "chapter"}`}
      className={clsx("group rounded-xl border p-3 [&>summary::-webkit-details-marker]:hidden", tone.panel)}
    >
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
        <span className="min-w-0">
          <span className={clsx("block text-[11px] font-bold uppercase tracking-widest", tone.title)}>
            {s.selectedReferenceTitle}
          </span>
          <span className="mt-1 block truncate text-sm font-semibold text-gray-900">
            {reference.resource_name} / {reference.chapter_title}
          </span>
          <span className="mt-2 flex flex-wrap gap-1.5">
            <EvidenceStatusBadge hasTextEvidence={hasTextEvidence} textSource={reference.text_evidence_status} />
            {targetChunk?.page_range ? <MetaPill icon={<MapPin className="h-3 w-3" />} text={evidenceText.page(targetChunk.page_range)} /> : null}
            {targetChunk?.text_source ? <MetaPill icon={<FileText className="h-3 w-3" />} text={evidenceText.sourceLabel(targetChunk.text_source)} /> : null}
          </span>
        </span>
        <span className={clsx("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border bg-white shadow-sm transition-colors", tone.chevron)}>
          <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
        </span>
      </summary>

      {targetChunk?.excerpt ? (
        <p className={clsx("mt-3 border-l-2 pl-3 text-xs leading-5", tone.quote)}>{targetChunk.excerpt}</p>
      ) : null}

      {reference.summary ? (
        <p className={clsx("mt-3 text-[11px] leading-5", tone.muted)}>{reference.summary}</p>
      ) : null}

      {reference.chunks.length ? (
        <div className="mt-3 space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-500">{evidenceText.context}</p>
          {reference.chunks.slice(0, 3).map((chunk) => (
            <ChunkEvidenceBlock key={`${chunk.segment_id ?? chunk.title}-${chunk.text_hash ?? chunk.excerpt}`} chunk={chunk} />
          ))}
        </div>
      ) : null}
    </details>
  );
}

function MatchEvidenceBlock({ match }: { match: ResourceMatch }) {
  const { texts: txt } = useInterfaceLanguage();
  const evidenceText = txt.studio.chatSidebar.evidence;
  const headingPath = match.heading_path?.length ? match.heading_path.join(" / ") : match.chapter_title;
  const excerpt = match.excerpt || match.evidence?.[0]?.value || "";

  return (
    <div className="text-xs leading-5 text-gray-900">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 font-semibold">
          <span className="break-words">{match.resource_name} / {headingPath}</span>
        </p>
        <span className="shrink-0 rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-bold text-gray-700">
          {evidenceText.score(Math.round(match.score * 100))}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {match.page_range ? <MetaPill icon={<MapPin className="h-3 w-3" />} text={evidenceText.page(match.page_range)} /> : null}
        {match.text_source ? <MetaPill icon={<FileText className="h-3 w-3" />} text={evidenceText.sourceLabel(match.text_source)} /> : null}
      </div>
      {excerpt ? (
        <p className="mt-2 border-l-2 border-gray-300 pl-3 text-gray-700">{excerpt}</p>
      ) : null}
      {match.evidence?.length ? (
        <div className="mt-2 space-y-1 text-[11px] text-gray-600">
          {match.evidence.slice(0, 3).map((item) => (
            <p key={`${item.label}-${item.value}`} className="break-words">
              <span className="font-semibold">{item.label}: </span>
              {item.value}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ChunkEvidenceBlock({ chunk }: { chunk: ResourceContextChunk }) {
  const { texts: txt } = useInterfaceLanguage();
  const evidenceText = txt.studio.chatSidebar.evidence;

  return (
    <div className="rounded-lg bg-white/80 px-3 py-2 text-[11px] leading-5 text-gray-700">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold text-gray-900">{chunk.title}</span>
        {chunk.page_range ? <MetaPill icon={<MapPin className="h-3 w-3" />} text={evidenceText.page(chunk.page_range)} /> : null}
        {chunk.text_source ? <MetaPill icon={<FileText className="h-3 w-3" />} text={evidenceText.sourceLabel(chunk.text_source)} /> : null}
      </div>
      {chunk.excerpt ? <p className="mt-1 break-words">{chunk.excerpt}</p> : null}
      {chunk.before_text || chunk.after_text ? (
        <details className="mt-2 [&>summary::-webkit-details-marker]:hidden">
          <summary className="flex cursor-pointer items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-gray-500">
            <ChevronDown className="h-3 w-3" />
            {evidenceText.neighborContext}
          </summary>
          <div className="mt-2 space-y-1 text-gray-500">
            {chunk.before_text ? <p><span className="font-semibold">{evidenceText.before}: </span>{chunk.before_text}</p> : null}
            {chunk.after_text ? <p><span className="font-semibold">{evidenceText.after}: </span>{chunk.after_text}</p> : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function EvidenceStatusBadge({
  hasTextEvidence,
  textSource,
}: {
  hasTextEvidence: boolean;
  textSource?: string | null;
}) {
  const { texts: txt } = useInterfaceLanguage();
  const evidenceText = txt.studio.chatSidebar.evidence;
  const sourceLabel = textSource ? evidenceText.sourceLabel(textSource) : null;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
        hasTextEvidence ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"
      )}
    >
      {hasTextEvidence ? <BadgeCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {hasTextEvidence ? evidenceText.bodyEvidence(sourceLabel) : evidenceText.metadataOnly}
    </span>
  );
}

function MetaPill({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white/80 px-2 py-0.5 text-[10px] font-semibold text-gray-600">
      {icon}
      {text}
    </span>
  );
}

function firstTextSource(matches: ResourceMatch[]) {
  return matches.find((match) => match.text_source)?.text_source ?? null;
}

function evidenceTone(hasTextEvidence: boolean) {
  return hasTextEvidence
    ? {
        panel: "border-emerald-200 bg-emerald-50",
        icon: "bg-white text-emerald-700 shadow-sm",
        title: "text-emerald-700",
        body: "text-emerald-950",
        muted: "text-emerald-900/80",
        divider: "border-emerald-200/80",
        button: "border-emerald-200 hover:border-emerald-300",
        chevron: "border-emerald-200 text-emerald-700 group-open:bg-emerald-100",
        quote: "border-emerald-300 text-emerald-900/90",
      }
    : {
        panel: "border-amber-200 bg-amber-50",
        icon: "bg-white text-amber-700 shadow-sm",
        title: "text-amber-700",
        body: "text-amber-950",
        muted: "text-amber-900/80",
        divider: "border-amber-200/80",
        button: "border-amber-200 hover:border-amber-300",
        chevron: "border-amber-200 text-amber-700 group-open:bg-amber-100",
        quote: "border-amber-300 text-amber-900/90",
      };
}
