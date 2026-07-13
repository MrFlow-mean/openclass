"use client";

import clsx from "clsx";
import {
  Check,
  ChevronDown,
  FileSearch,
  LoaderCircle,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ChoiceList, ResearchMarkdown } from "@/components/course-studio/research-content-components";
import { ResearchArtifactList } from "@/components/course-studio/research-artifact-list";
import { ResearchAskPanel } from "@/components/course-studio/research-ask-panel";
import { ResearchConfigurationPanel } from "@/components/course-studio/research-configuration-panel";
import { api } from "@/lib/api";
import type {
  ResearchArtifact,
  ResearchArtifactKind,
  ResearchCapabilities,
  ResearchChatMessage,
  ResearchChatThread,
  ResearchContextMode,
  ResearchEpisodeProfile,
  ResearchNote,
  ResearchSearchMode,
  ResearchSearchResult,
  ResearchSpeaker,
  ResearchSpeakerProfile,
  ResearchTransformation,
  RetrievalEvidence,
  SelectionRef,
  SourceIngestionRecord,
} from "@/types";

type ResearchStudioPanelProps = {
  packageId: string;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
};

type StudioView = "search" | "chat" | "artifacts" | "configuration";

type SpeakerDraft = Required<ResearchSpeaker>;

const VIEW_OPTIONS: Array<{ value: StudioView; label: string }> = [
  { value: "search", label: "检索" },
  { value: "chat", label: "会话" },
  { value: "artifacts", label: "生成物" },
  { value: "configuration", label: "配置" },
];

const SEARCH_MODE_LABELS: Record<ResearchSearchMode, string> = {
  text: "全文",
  semantic: "语义",
  hybrid: "混合",
};

const CONTEXT_MODE_LABELS: Record<ResearchContextMode, string> = {
  retrieval: "按问题检索",
  full: "使用所选全文",
  notes: "仅使用笔记",
  off: "不附加资料",
};

const ARTIFACT_KIND_LABELS: Record<ResearchArtifactKind, string> = {
  insight: "洞见",
  summary: "摘要",
  study_guide: "学习指南",
  faq: "问答集",
  timeline: "时间线",
  custom: "自定义",
  podcast: "播客",
};

const EMPTY_SPEAKER: SpeakerDraft = { name: "", role: "", voice: "", instructions: "" };

function compactText(value: string, limit = 180) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function evidenceLocation(evidence: RetrievalEvidence) {
  return [evidence.source_title, evidence.section_path.join(" > "), evidence.page_range].filter(Boolean).join(" / ");
}

function evidenceSelection(evidence: RetrievalEvidence): SelectionRef {
  return {
    kind: "source",
    excerpt: [evidenceLocation(evidence), compactText(evidence.excerpt)].filter(Boolean).join(" · "),
    heading_path: evidence.section_path,
    source_ingestion_id: evidence.source_ingestion_id,
    source_title: evidence.source_title,
    source_uri: evidence.source_uri,
    source_chapter_id: evidence.chapter_id || null,
    source_chapter_number: "",
    source_chapter_title: evidence.section_path.at(-1) ?? "",
    source_page_range: evidence.page_range,
    source_locator: typeof evidence.metadata.source_locator === "string" ? evidence.metadata.source_locator : "",
    source_page_start: null,
    source_page_end: null,
  };
}

function citationSelection(citation: ResearchChatMessage["citations"][number]): SelectionRef {
  return {
    kind: "source",
    excerpt: [citation.source_title, citation.section_path.join(" > "), citation.page_range, compactText(citation.excerpt)]
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

export function ResearchStudioPanel({ packageId, onError, onSourceReference }: ResearchStudioPanelProps) {
  const [view, setView] = useState<StudioView>("search");
  const [sources, setSources] = useState<SourceIngestionRecord[]>([]);
  const [notes, setNotes] = useState<ResearchNote[]>([]);
  const [capabilities, setCapabilities] = useState<ResearchCapabilities | null>(null);
  const [threads, setThreads] = useState<ResearchChatThread[]>([]);
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [transformations, setTransformations] = useState<ResearchTransformation[]>([]);
  const [speakerProfiles, setSpeakerProfiles] = useState<ResearchSpeakerProfile[]>([]);
  const [episodeProfiles, setEpisodeProfiles] = useState<ResearchEpisodeProfile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const readySources = sources.filter((source) => source.status === "ready");

  const loadThreads = useCallback(async () => {
    try {
      setThreads(await api.listResearchThreads(packageId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究会话读取失败");
    }
  }, [onError, packageId]);

  const loadArtifacts = useCallback(async () => {
    try {
      setArtifacts(await api.listResearchArtifacts(packageId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究生成物读取失败");
    }
  }, [onError, packageId]);

  useEffect(() => {
    let disposed = false;
    Promise.all([
      api.listPackageSources(packageId),
      api.listResearchNotes(packageId),
      api.getResearchCapabilities(packageId),
      api.listResearchThreads(packageId),
      api.listResearchArtifacts(packageId),
      api.listResearchTransformations(packageId),
      api.listResearchSpeakerProfiles(packageId),
      api.listResearchEpisodeProfiles(packageId),
    ])
      .then(([nextSources, nextNotes, nextCapabilities, nextThreads, nextArtifacts, nextTransformations, nextSpeakerProfiles, nextEpisodeProfiles]) => {
        if (disposed) {
          return;
        }
        setSources(nextSources);
        setNotes(nextNotes);
        setCapabilities(nextCapabilities);
        setThreads(nextThreads);
        setArtifacts(nextArtifacts);
        setTransformations(nextTransformations);
        setSpeakerProfiles(nextSpeakerProfiles);
        setEpisodeProfiles(nextEpisodeProfiles);
      })
      .catch((error) => {
        if (!disposed) {
          onError(error instanceof Error ? error.message : "研究工作台读取失败");
        }
      })
      .finally(() => {
        if (!disposed) {
          setIsLoading(false);
        }
      });
    return () => {
      disposed = true;
    };
  }, [onError, packageId]);

  useEffect(() => {
    if (!artifacts.some((artifact) => artifact.status === "queued" || artifact.status === "generating")) {
      return;
    }
    const timer = window.setInterval(() => void loadArtifacts(), 3000);
    return () => window.clearInterval(timer);
  }, [artifacts, loadArtifacts]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white py-10 text-xs text-gray-500">
        <LoaderCircle className="h-4 w-4 animate-spin" />
        正在读取研究工作台
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 rounded-lg border border-gray-200 bg-white p-1">
        {VIEW_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setView(option.value)}
            className={clsx(
              "rounded-md px-2 py-2 text-xs font-semibold transition",
              view === option.value ? "bg-black text-white" : "text-gray-500 hover:bg-gray-50 hover:text-gray-900"
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

      {view === "search" ? (
        <ResearchSearchView
          packageId={packageId}
          capabilities={capabilities}
          sources={readySources}
          onError={onError}
          onSourceReference={onSourceReference}
        />
      ) : view === "chat" ? (
        <ResearchChatView
          packageId={packageId}
          sources={readySources}
          notes={notes}
          threads={threads}
          onThreadsChange={setThreads}
          onRefreshThreads={loadThreads}
          onError={onError}
          onSourceReference={onSourceReference}
        />
      ) : view === "artifacts" ? (
        <ResearchArtifactsView
          packageId={packageId}
          capabilities={capabilities}
          sources={readySources}
          notes={notes}
          artifacts={artifacts}
          speakerProfiles={speakerProfiles}
          episodeProfiles={episodeProfiles}
          onArtifactsChange={setArtifacts}
          onRefreshArtifacts={loadArtifacts}
          onError={onError}
        />
      ) : (
        <ResearchConfigurationPanel
          packageId={packageId}
          sources={readySources}
          notes={notes}
          transformations={transformations}
          speakerProfiles={speakerProfiles}
          episodeProfiles={episodeProfiles}
          onTransformationsChange={setTransformations}
          onSpeakerProfilesChange={setSpeakerProfiles}
          onEpisodeProfilesChange={setEpisodeProfiles}
          onArtifactCreated={(artifact) => setArtifacts((items) => [artifact, ...items.filter((item) => item.id !== artifact.id)])}
          onError={onError}
        />
      )}
    </div>
  );
}

function ResearchSearchView({
  packageId,
  capabilities,
  sources,
  onError,
  onSourceReference,
}: {
  packageId: string;
  capabilities: ResearchCapabilities | null;
  sources: SourceIngestionRecord[];
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<ResearchSearchMode>(capabilities?.semantic_search ? "hybrid" : "text");
  const [includeNotes, setIncludeNotes] = useState(true);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [results, setResults] = useState<ResearchSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  async function runSearch() {
    if (!query.trim() || isSearching) {
      return;
    }
    setIsSearching(true);
    try {
      const response = await api.searchResearch(packageId, {
        query: query.trim(),
        mode,
        source_ingestion_ids: sourceIds,
        include_notes: includeNotes,
      });
      setResults(response.results);
      setHasSearched(true);
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究资料检索失败");
    } finally {
      setIsSearching(false);
    }
  }

  return (
    <div className="space-y-3">
      <ResearchAskPanel
        packageId={packageId}
        sources={sources}
        onError={onError}
        onSourceReference={onSourceReference}
      />
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <label className="text-[10px] font-bold uppercase tracking-widest text-gray-400">检索资料与笔记</label>
        <textarea
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void runSearch();
            }
          }}
          rows={3}
          placeholder="输入需要查找的问题或概念…"
          className="mt-2 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-sm leading-6 outline-none transition focus:border-black"
        />
        <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as ResearchSearchMode)}
            className="h-9 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
          >
            {(["text", "semantic", "hybrid"] as ResearchSearchMode[]).map((item) => (
              <option key={item} value={item} disabled={item !== "text" && capabilities?.semantic_search === false}>
                {SEARCH_MODE_LABELS[item]}检索
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void runSearch()}
            disabled={!query.trim() || isSearching}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-black px-3 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:opacity-50"
          >
            {isSearching ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <FileSearch className="h-3.5 w-3.5" />}
            检索
          </button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-xs text-gray-600">
          <input type="checkbox" checked={includeNotes} onChange={(event) => setIncludeNotes(event.target.checked)} />
          同时检索研究笔记
        </label>
        {sources.length ? (
          <details className="mt-3 rounded-md border border-gray-100 bg-gray-50 p-2">
            <summary className="cursor-pointer text-xs font-medium text-gray-600">
              资料范围：{sourceIds.length ? `${sourceIds.length} 项` : "全部资料"}
            </summary>
            <div className="mt-2 max-h-44 overflow-y-auto">
              <ChoiceList
                title="只检索所选资料"
                items={sources.map((source) => ({ id: source.id, title: source.title }))}
                selectedIds={sourceIds}
                onChange={setSourceIds}
              />
            </div>
          </details>
        ) : null}
      </section>

      {results.length ? (
        <div className="space-y-2">
          {results.map((result, index) => (
            <SearchResultCard
              key={result.evidence?.id ?? result.note?.id ?? index}
              result={result}
              onSourceReference={onSourceReference}
            />
          ))}
        </div>
      ) : hasSearched && !isSearching ? (
        <p className="rounded-lg border border-dashed border-gray-200 bg-white px-3 py-6 text-center text-xs text-gray-500">
          当前还没有检索结果。
        </p>
      ) : null}
    </div>
  );
}

function SearchResultCard({
  result,
  onSourceReference,
}: {
  result: ResearchSearchResult;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  const score = result.score > 0 ? `${Math.round(result.score * 100)}%` : "";
  if (result.kind === "note" && result.note) {
    return (
      <article className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="truncate text-xs font-semibold text-gray-900">{result.note.title || "未命名笔记"}</p>
          {score ? <span className="text-[10px] text-gray-400">{score}</span> : null}
        </div>
        <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-gray-600">{result.note.content}</p>
      </article>
    );
  }
  const evidence = result.evidence;
  if (!evidence) {
    return null;
  }
  return (
    <article className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-gray-900">{evidenceLocation(evidence) || evidence.source_title}</p>
          <p className="mt-1 line-clamp-4 text-xs leading-5 text-gray-600">{evidence.excerpt}</p>
        </div>
        {score ? <span className="shrink-0 text-[10px] text-gray-400">{score}</span> : null}
      </div>
      {onSourceReference ? (
        <button
          type="button"
          onClick={() => onSourceReference(evidenceSelection(evidence))}
          className="mt-2 inline-flex h-7 items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-100"
        >
          <Check className="h-3.5 w-3.5" />
          引用到输入框
        </button>
      ) : null}
    </article>
  );
}

function ResearchChatView({
  packageId,
  sources,
  notes,
  threads,
  onThreadsChange,
  onRefreshThreads,
  onError,
  onSourceReference,
}: {
  packageId: string;
  sources: SourceIngestionRecord[];
  notes: ResearchNote[];
  threads: ResearchChatThread[];
  onThreadsChange: (threads: ResearchChatThread[]) => void;
  onRefreshThreads: () => Promise<void>;
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
}) {
  const [activeThreadId, setActiveThreadId] = useState(threads[0]?.id ?? "");
  const [newThreadTitle, setNewThreadTitle] = useState("");
  const [messages, setMessages] = useState<ResearchChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const activeThread = threads.find((thread) => thread.id === activeThreadId) ?? null;
  const [threadTitle, setThreadTitle] = useState(activeThread?.title ?? "");
  const [contextMode, setContextMode] = useState<ResearchContextMode>(activeThread?.context_mode ?? "retrieval");
  const [sourceIds, setSourceIds] = useState<string[]>(activeThread?.source_ingestion_ids ?? []);
  const [noteIds, setNoteIds] = useState<string[]>(activeThread?.note_ids ?? []);

  useEffect(() => {
    if (!activeThread) {
      return;
    }
    let disposed = false;
    void api
      .listResearchThreadMessages(packageId, activeThread.id)
      .then((result) => {
        if (!disposed) {
          setMessages(result);
        }
      })
      .catch((error) => {
        if (!disposed) {
          onError(error instanceof Error ? error.message : "研究会话消息读取失败");
        }
      });
    return () => {
      disposed = true;
    };
  }, [activeThread, onError, packageId]);

  function selectThread(threadId: string) {
    const thread = threads.find((item) => item.id === threadId) ?? null;
    setActiveThreadId(threadId);
    setMessages([]);
    if (thread) {
      setThreadTitle(thread.title);
      setContextMode(thread.context_mode);
      setSourceIds(thread.source_ingestion_ids);
      setNoteIds(thread.note_ids);
    }
  }

  async function createThread() {
    if (!newThreadTitle.trim() || isBusy) {
      return;
    }
    setIsBusy(true);
    try {
      const created = await api.createResearchThread(packageId, {
        title: newThreadTitle.trim(),
        context_mode: "retrieval",
      });
      onThreadsChange([created, ...threads]);
      setActiveThreadId(created.id);
      setThreadTitle(created.title);
      setContextMode(created.context_mode);
      setSourceIds(created.source_ingestion_ids);
      setNoteIds(created.note_ids);
      setMessages([]);
      setNewThreadTitle("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究会话创建失败");
    } finally {
      setIsBusy(false);
    }
  }

  async function saveContext() {
    if (!activeThread || isBusy) {
      return;
    }
    setIsBusy(true);
    try {
      const updated = await api.updateResearchThread(packageId, activeThread.id, {
        title: threadTitle.trim(),
        context_mode: contextMode,
        source_ingestion_ids: sourceIds,
        note_ids: noteIds,
      });
      onThreadsChange(threads.map((thread) => (thread.id === updated.id ? updated : thread)));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究会话范围保存失败");
    } finally {
      setIsBusy(false);
    }
  }

  async function deleteThread() {
    if (!activeThread || isBusy || !window.confirm(`删除研究会话“${activeThread.title || "未命名会话"}”？`)) {
      return;
    }
    setIsBusy(true);
    try {
      await api.deleteResearchThread(packageId, activeThread.id);
      const next = threads.filter((thread) => thread.id !== activeThread.id);
      onThreadsChange(next);
      const nextThread = next[0] ?? null;
      setActiveThreadId(nextThread?.id ?? "");
      setMessages([]);
      if (nextThread) {
        setThreadTitle(nextThread.title);
        setContextMode(nextThread.context_mode);
        setSourceIds(nextThread.source_ingestion_ids);
        setNoteIds(nextThread.note_ids);
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究会话删除失败");
    } finally {
      setIsBusy(false);
    }
  }

  async function sendMessage() {
    if (!activeThread || !message.trim() || isBusy) {
      return;
    }
    const nextMessage = message.trim();
    setMessage("");
    setIsBusy(true);
    try {
      const response = await api.sendResearchThreadMessage(packageId, activeThread.id, {
        message: nextMessage,
        context_mode: contextMode,
        source_ingestion_ids: sourceIds,
        note_ids: noteIds,
      });
      setMessages(await api.listResearchThreadMessages(packageId, activeThread.id));
      onThreadsChange(threads.map((thread) => (thread.id === response.thread.id ? response.thread : thread)));
    } catch (error) {
      setMessage(nextMessage);
      onError(error instanceof Error ? error.message : "研究会话发送失败");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <div className="flex gap-2">
          <input
            value={newThreadTitle}
            onChange={(event) => setNewThreadTitle(event.target.value)}
            placeholder="新会话标题"
            className="h-9 min-w-0 flex-1 rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black"
          />
          <button
            type="button"
            onClick={() => void createThread()}
            disabled={!newThreadTitle.trim() || isBusy}
            className="flex h-9 w-9 items-center justify-center rounded-md bg-black text-white disabled:opacity-50"
            aria-label="新建研究会话"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
        {threads.length ? (
          <div className="mt-2 flex gap-2">
            <select
              value={activeThreadId}
              onChange={(event) => selectThread(event.target.value)}
              className="h-9 min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
            >
              {threads.map((thread) => (
                <option key={thread.id} value={thread.id}>
                  {thread.title || "未命名会话"}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void onRefreshThreads()}
              disabled={isBusy}
              className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:text-black disabled:opacity-50"
              aria-label="刷新研究会话"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => void deleteThread()}
              disabled={isBusy || !activeThread}
              className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-200 text-gray-400 hover:border-rose-200 hover:text-rose-600 disabled:opacity-50"
              aria-label="删除研究会话"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
      </section>

      {activeThread ? (
        <>
          <details className="rounded-lg border border-gray-200 bg-white p-3">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-xs font-semibold text-gray-700">
              <span>上下文范围</span>
              <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
            </summary>
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="text-[10px] font-bold uppercase tracking-widest text-gray-400">会话标题</span>
                <input
                  value={threadTitle}
                  onChange={(event) => setThreadTitle(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black"
                />
              </label>
              <select
                value={contextMode}
                onChange={(event) => setContextMode(event.target.value as ResearchContextMode)}
                className="h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
              >
                {(Object.keys(CONTEXT_MODE_LABELS) as ResearchContextMode[]).map((mode) => (
                  <option key={mode} value={mode}>
                    {CONTEXT_MODE_LABELS[mode]}
                  </option>
                ))}
              </select>
              <ChoiceList
                title="资料"
                items={sources.map((source) => ({ id: source.id, title: source.title }))}
                selectedIds={sourceIds}
                onChange={setSourceIds}
              />
              <ChoiceList
                title="笔记"
                items={notes.map((note) => ({ id: note.id, title: note.title || "未命名笔记" }))}
                selectedIds={noteIds}
                onChange={setNoteIds}
              />
              <button
                type="button"
                onClick={() => void saveContext()}
                disabled={isBusy}
                className="h-8 rounded-md bg-black px-3 text-xs font-semibold text-white disabled:opacity-50"
              >
                保存范围
              </button>
            </div>
          </details>

          <section className="rounded-lg border border-gray-200 bg-white">
            <div className="custom-scrollbar max-h-[34rem] space-y-3 overflow-y-auto p-3">
              {messages.length ? (
                messages.map((item) => (
                  <article
                    key={item.id}
                    className={clsx(
                      "rounded-xl px-3 py-2",
                      item.role === "user" ? "ml-7 bg-gray-900 text-white" : "mr-4 border border-gray-200 bg-gray-50"
                    )}
                  >
                    {item.role === "user" ? (
                      <p className="whitespace-pre-wrap text-xs leading-6">{item.content}</p>
                    ) : (
                      <ResearchMarkdown content={item.content} />
                    )}
                    {item.citations.length && onSourceReference ? (
                      <div className="mt-2 flex flex-wrap gap-1 border-t border-gray-200 pt-2">
                        {item.citations.map((citation, index) => (
                          <button
                            key={`${citation.source_ingestion_id}:${citation.chapter_id}:${index}`}
                            type="button"
                            onClick={() => onSourceReference(citationSelection(citation))}
                            className="rounded-full bg-white px-2 py-1 text-[10px] font-medium text-emerald-700 shadow-sm"
                          >
                            {index + 1}. {citation.source_title || "资料引用"}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </article>
                ))
              ) : (
                <p className="py-8 text-center text-xs text-gray-400">从当前资料与笔记开始一段研究会话。</p>
              )}
              {isBusy ? (
                <p className="flex items-center gap-2 text-xs text-gray-500">
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  正在处理
                </p>
              ) : null}
            </div>
            <div className="flex items-end gap-2 border-t border-gray-100 p-3">
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                rows={2}
                placeholder="继续研究…"
                className="min-w-0 flex-1 resize-none rounded-md border border-gray-200 px-3 py-2 text-xs leading-5 outline-none focus:border-black"
              />
              <button
                type="button"
                onClick={() => void sendMessage()}
                disabled={!message.trim() || isBusy}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-black text-white disabled:opacity-40"
                aria-label="发送研究消息"
              >
                <Send className="h-3.5 w-3.5" />
              </button>
            </div>
          </section>
        </>
      ) : (
        <div className="rounded-lg border border-dashed border-gray-200 bg-white px-4 py-8 text-center">
          <MessageSquare className="mx-auto h-7 w-7 text-gray-300" />
          <p className="mt-2 text-xs text-gray-500">新建一个会话后即可开始。</p>
        </div>
      )}
    </div>
  );
}

function ResearchArtifactsView({
  packageId,
  capabilities,
  sources,
  notes,
  artifacts,
  speakerProfiles,
  episodeProfiles,
  onArtifactsChange,
  onRefreshArtifacts,
  onError,
}: {
  packageId: string;
  capabilities: ResearchCapabilities | null;
  sources: SourceIngestionRecord[];
  notes: ResearchNote[];
  artifacts: ResearchArtifact[];
  speakerProfiles: ResearchSpeakerProfile[];
  episodeProfiles: ResearchEpisodeProfile[];
  onArtifactsChange: (artifacts: ResearchArtifact[]) => void;
  onRefreshArtifacts: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [kind, setKind] = useState<ResearchArtifactKind>("custom");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [language, setLanguage] = useState("");
  const [tone, setTone] = useState("");
  const [length, setLength] = useState<"short" | "medium" | "long">("medium");
  const [segmentCount, setSegmentCount] = useState(6);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [noteIds, setNoteIds] = useState<string[]>([]);
  const [speakers, setSpeakers] = useState<SpeakerDraft[]>([{ ...EMPTY_SPEAKER }]);
  const [synthesizeAudio, setSynthesizeAudio] = useState(false);
  const [speakerProfileId, setSpeakerProfileId] = useState("");
  const [episodeProfileId, setEpisodeProfileId] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const isPodcast = kind === "podcast";
  const hasContext = sources.length > 0 || notes.length > 0 || instructions.trim().length > 0;
  const hasPodcastSpeaker = !isPodcast || speakers.some((speaker) => speaker.name.trim());
  const canCreate = Boolean(title.trim()) && hasContext && hasPodcastSpeaker;

  async function createArtifact() {
    if (!canCreate || isCreating) {
      return;
    }
    setIsCreating(true);
    try {
      const created = await api.createResearchArtifact(packageId, {
        kind,
        title: title.trim(),
        instructions: instructions.trim(),
        language: language.trim(),
        tone: tone.trim(),
        length,
        segment_count: isPodcast ? segmentCount : null,
        source_ingestion_ids: sourceIds,
        note_ids: noteIds,
        speakers: isPodcast
          ? speakers
              .filter((speaker) => speaker.name.trim())
              .map((speaker) => ({
                name: speaker.name.trim(),
                role: speaker.role.trim(),
                ...(speaker.voice.trim() ? { voice: speaker.voice.trim() } : {}),
                instructions: speaker.instructions.trim(),
              }))
          : [],
        synthesize_audio: isPodcast && Boolean(capabilities?.podcast_audio) && synthesizeAudio,
      });
      onArtifactsChange([created, ...artifacts.filter((artifact) => artifact.id !== created.id)]);
      setTitle("");
      setInstructions("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究生成物创建失败");
    } finally {
      setIsCreating(false);
    }
  }

  function updateSpeaker(index: number, key: keyof SpeakerDraft, value: string) {
    setSpeakers((current) => current.map((speaker, speakerIndex) => (speakerIndex === index ? { ...speaker, [key]: value } : speaker)));
  }

  function applySpeakerProfile(profileId: string) {
    setSpeakerProfileId(profileId);
    const profile = speakerProfiles.find((item) => item.id === profileId);
    if (profile) {
      setSpeakers(profile.speakers.map((speaker) => ({
        name: speaker.name,
        role: speaker.role ?? "",
        voice: speaker.voice ?? "",
        instructions: speaker.instructions ?? "",
      })));
    }
  }

  function applyEpisodeProfile(profileId: string) {
    setEpisodeProfileId(profileId);
    const profile = episodeProfiles.find((item) => item.id === profileId);
    if (profile) {
      setLanguage(profile.language);
      setTone(profile.tone);
      setLength(profile.length);
      setSegmentCount(profile.segment_count);
      setInstructions(profile.instructions);
    }
  }

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-600" />
          <p className="text-xs font-semibold text-gray-800">创建通用研究生成物</p>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as ResearchArtifactKind)}
            className="h-9 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
          >
            {(Object.keys(ARTIFACT_KIND_LABELS) as ResearchArtifactKind[]).map((item) => (
              <option key={item} value={item} disabled={item === "podcast" && capabilities?.podcast_script === false}>
                {ARTIFACT_KIND_LABELS[item]}
              </option>
            ))}
          </select>
          <select
            value={length}
            onChange={(event) => setLength(event.target.value as "short" | "medium" | "long")}
            className="h-9 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
          >
            <option value="short">简短</option>
            <option value="medium">适中</option>
            <option value="long">详细</option>
          </select>
        </div>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="生成物标题"
          className="mt-2 h-9 w-full rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black"
        />
        <textarea
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          rows={4}
          placeholder="说明你希望生成物解决的问题、采用的结构或重点…"
          className="mt-2 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-xs leading-5 outline-none focus:border-black"
        />
        <div className="mt-2 grid grid-cols-2 gap-2">
          <input
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            placeholder="语言（可选）"
            className="h-9 rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black"
          />
          <input
            value={tone}
            onChange={(event) => setTone(event.target.value)}
            placeholder="表达语气（可选）"
            className="h-9 rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black"
          />
        </div>
        <details className="mt-3 rounded-md border border-gray-100 bg-gray-50 p-2">
          <summary className="cursor-pointer text-xs font-medium text-gray-600">
            内容范围：{sourceIds.length ? `${sourceIds.length} 份资料` : sources.length ? "全部可用资料" : "无资料"}，
            {noteIds.length ? `${noteIds.length} 条笔记` : notes.length ? "按相关性选取笔记" : "无笔记"}
          </summary>
          <div className="mt-3 space-y-3">
            <ChoiceList
              title="资料"
              items={sources.map((source) => ({ id: source.id, title: source.title }))}
              selectedIds={sourceIds}
              onChange={setSourceIds}
            />
            <ChoiceList
              title="笔记"
              items={notes.map((note) => ({ id: note.id, title: note.title || "未命名笔记" }))}
              selectedIds={noteIds}
              onChange={setNoteIds}
            />
          </div>
        </details>

        {isPodcast ? (
          <div className="mt-3 space-y-3 rounded-md border border-violet-100 bg-violet-50/40 p-3">
            {speakerProfiles.length || episodeProfiles.length ? (
              <div className="grid grid-cols-2 gap-2">
                <select value={speakerProfileId} onChange={(event) => applySpeakerProfile(event.target.value)} className="h-8 rounded-md border border-violet-100 bg-white px-2 text-xs outline-none">
                  <option value="">发言人档案</option>
                  {speakerProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
                <select value={episodeProfileId} onChange={(event) => applyEpisodeProfile(event.target.value)} className="h-8 rounded-md border border-violet-100 bg-white px-2 text-xs outline-none">
                  <option value="">节目档案</option>
                  {episodeProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-violet-900">说话人</p>
              {speakers.length < 4 ? (
                <button
                  type="button"
                  onClick={() => setSpeakers((current) => [...current, { ...EMPTY_SPEAKER }])}
                  className="text-[11px] font-semibold text-violet-700"
                >
                  + 添加
                </button>
              ) : null}
            </div>
            {speakers.map((speaker, index) => (
              <div key={index} className="rounded-md border border-violet-100 bg-white p-2">
                <div className="grid grid-cols-2 gap-2">
                  <input
                    value={speaker.name}
                    onChange={(event) => updateSpeaker(index, "name", event.target.value)}
                    placeholder="名称"
                    className="h-8 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-violet-400"
                  />
                  <input
                    value={speaker.role}
                    onChange={(event) => updateSpeaker(index, "role", event.target.value)}
                    placeholder="角色"
                    className="h-8 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-violet-400"
                  />
                  <input
                    value={speaker.voice}
                    onChange={(event) => updateSpeaker(index, "voice", event.target.value)}
                    placeholder="语音 ID（可选）"
                    className="h-8 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-violet-400"
                  />
                  <input
                    value={speaker.instructions}
                    onChange={(event) => updateSpeaker(index, "instructions", event.target.value)}
                    placeholder="表达要求（可选）"
                    className="h-8 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-violet-400"
                  />
                </div>
                {speakers.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => setSpeakers((current) => current.filter((_, speakerIndex) => speakerIndex !== index))}
                    className="mt-2 text-[10px] font-medium text-rose-600"
                  >
                    移除说话人
                  </button>
                ) : null}
              </div>
            ))}
            <label className={clsx("flex items-start gap-2 text-xs", capabilities?.podcast_audio ? "text-gray-700" : "text-gray-400")}>
              <input
                type="checkbox"
                checked={synthesizeAudio && Boolean(capabilities?.podcast_audio)}
                onChange={(event) => setSynthesizeAudio(event.target.checked)}
                disabled={!capabilities?.podcast_audio}
                className="mt-0.5"
              />
              <span>{capabilities?.podcast_audio ? "同时合成音频" : "当前模型配置仅生成播客脚本"}</span>
            </label>
            <label className="flex items-center justify-between gap-3 text-xs text-gray-700">
              <span>节目段落数</span>
              <input
                type="number"
                min={3}
                max={20}
                value={segmentCount}
                onChange={(event) => setSegmentCount(Math.max(3, Math.min(20, Number(event.target.value) || 3)))}
                className="h-8 w-20 rounded-md border border-violet-100 bg-white px-2 text-xs outline-none focus:border-violet-400"
              />
            </label>
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => void createArtifact()}
          disabled={!canCreate || isCreating}
          className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-md bg-black px-3 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:opacity-50"
        >
          {isCreating ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          开始生成
        </button>
      </section>

      <ResearchArtifactList
        packageId={packageId}
        artifacts={artifacts}
        currentKind={kind}
        onChange={onArtifactsChange}
        onRefresh={onRefreshArtifacts}
        onError={onError}
      />
    </div>
  );
}
