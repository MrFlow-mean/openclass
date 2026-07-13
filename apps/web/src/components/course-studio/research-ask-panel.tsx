"use client";

import { Check, LoaderCircle, Sparkles } from "lucide-react";
import { useState } from "react";

import { ChoiceList, ResearchMarkdown } from "@/components/course-studio/research-content-components";
import { api } from "@/lib/api";
import type { ResearchAskResponse, ResearchCitation, SelectionRef, SourceIngestionRecord } from "@/types";

type ResearchAskPanelProps = {
  packageId: string;
  sources: SourceIngestionRecord[];
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
};

function citationSelection(citation: ResearchCitation): SelectionRef {
  return {
    kind: "source",
    excerpt: [citation.source_title, citation.section_path.join(" > "), citation.page_range, citation.excerpt]
      .filter(Boolean)
      .join(" · "),
    heading_path: citation.section_path,
    source_ingestion_id: citation.source_ingestion_id,
    source_title: citation.source_title,
    source_uri: citation.source_uri,
    source_chapter_id: citation.chapter_id || null,
    source_chapter_number: "",
    source_chapter_title: citation.section_path.at(-1) ?? "",
    source_page_range: citation.page_range,
    source_locator: "",
    source_page_start: null,
    source_page_end: null,
  };
}

export function ResearchAskPanel({ packageId, sources, onError, onSourceReference }: ResearchAskPanelProps) {
  const [question, setQuestion] = useState("");
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [includeNotes, setIncludeNotes] = useState(true);
  const [answer, setAnswer] = useState<ResearchAskResponse | null>(null);
  const [isAsking, setIsAsking] = useState(false);

  async function ask() {
    if (!question.trim() || isAsking) return;
    setIsAsking(true);
    try {
      setAnswer(await api.askResearch(packageId, {
        question: question.trim(),
        source_ingestion_ids: sourceIds,
        include_notes: includeNotes,
        max_queries: 5,
      }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "资料回答生成失败");
    } finally {
      setIsAsking(false);
    }
  }

  return (
    <section className="rounded-lg border border-violet-100 bg-violet-50/30 p-3">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-violet-600" />
        <p className="text-xs font-semibold text-gray-800">基于资料回答</p>
      </div>
      <textarea
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            void ask();
          }
        }}
        rows={3}
        placeholder="输入需要综合多处资料回答的问题…"
        className="mt-2 w-full resize-y rounded-md border border-violet-100 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-violet-400"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input type="checkbox" checked={includeNotes} onChange={(event) => setIncludeNotes(event.target.checked)} />
          包含研究笔记
        </label>
        <button
          type="button"
          onClick={() => void ask()}
          disabled={!question.trim() || isAsking}
          className="inline-flex h-9 items-center gap-1.5 rounded-md bg-violet-700 px-3 text-xs font-semibold text-white disabled:opacity-50"
        >
          {isAsking ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          生成回答
        </button>
      </div>
      {sources.length ? (
        <details className="mt-3 rounded-md border border-violet-100 bg-white p-2">
          <summary className="cursor-pointer text-xs font-medium text-gray-600">
            资料范围：{sourceIds.length ? `${sourceIds.length} 项` : "全部资料"}
          </summary>
          <div className="mt-2 max-h-44 overflow-y-auto">
            <ChoiceList
              title="只使用所选资料"
              items={sources.map((source) => ({ id: source.id, title: source.title }))}
              selectedIds={sourceIds}
              onChange={setSourceIds}
            />
          </div>
        </details>
      ) : null}
      {answer ? (
        <article className="mt-3 rounded-md border border-violet-100 bg-white p-3">
          <ResearchMarkdown content={answer.answer} />
          {answer.search_queries.length ? (
            <div className="mt-3 flex flex-wrap gap-1">
              {answer.search_queries.map((query) => <span key={query} className="rounded-full bg-violet-50 px-2 py-1 text-[10px] text-violet-700">{query}</span>)}
            </div>
          ) : null}
          {answer.citations.length ? (
            <div className="mt-3 space-y-1.5 border-t border-gray-100 pt-3">
              {answer.citations.map((citation, index) => (
                <button
                  key={`${citation.source_ingestion_id}-${citation.chapter_id}-${index}`}
                  type="button"
                  onClick={() => onSourceReference?.(citationSelection(citation))}
                  disabled={!onSourceReference}
                  className="flex w-full items-start gap-1.5 rounded-md bg-gray-50 px-2 py-1.5 text-left text-[11px] text-gray-600 disabled:cursor-default"
                >
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" />
                  <span>{[citation.source_title, citation.section_path.join(" > "), citation.page_range].filter(Boolean).join(" / ")}</span>
                </button>
              ))}
            </div>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
