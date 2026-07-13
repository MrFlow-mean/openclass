"use client";

import { Check, LoaderCircle, PencilLine, Plus, Save, StickyNote, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ResearchNote } from "@/types";

type PackageNotesPanelProps = {
  packageId: string;
  disabled?: boolean;
  onError: (message: string) => void;
};

type NoteDraft = {
  id: string | null;
  title: string;
  content: string;
  tags: string;
};

const EMPTY_DRAFT: NoteDraft = { id: null, title: "", content: "", tags: "" };

function parseTags(value: string) {
  return Array.from(
    new Set(
      value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  );
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function PackageNotesPanel({ packageId, disabled = false, onError }: PackageNotesPanelProps) {
  const [notes, setNotes] = useState<ResearchNote[]>([]);
  const [draft, setDraft] = useState<NoteDraft | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const refreshNotes = useCallback(async () => {
    if (!packageId) {
      return;
    }
    setIsLoading(true);
    try {
      setNotes(await api.listResearchNotes(packageId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究笔记读取失败");
    } finally {
      setIsLoading(false);
    }
  }, [onError, packageId]);

  useEffect(() => {
    let disposed = false;
    void api
      .listResearchNotes(packageId)
      .then((result) => {
        if (!disposed) {
          setNotes(result);
        }
      })
      .catch((error) => {
        if (!disposed) {
          onError(error instanceof Error ? error.message : "研究笔记读取失败");
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

  function editNote(note: ResearchNote) {
    setDraft({
      id: note.id,
      title: note.title,
      content: note.content,
      tags: note.tags.join("，"),
    });
  }

  async function saveDraft() {
    if (!draft?.content.trim() || disabled || isSaving) {
      return;
    }
    setIsSaving(true);
    try {
      const payload = {
        title: draft.title.trim(),
        content: draft.content.trim(),
        tags: parseTags(draft.tags),
      };
      const saved = draft.id
        ? await api.updateResearchNote(packageId, draft.id, payload)
        : await api.createResearchNote(packageId, payload);
      setNotes((current) => [saved, ...current.filter((note) => note.id !== saved.id)]);
      setDraft(null);
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究笔记保存失败");
    } finally {
      setIsSaving(false);
    }
  }

  async function deleteNote(note: ResearchNote) {
    if (disabled || deletingId || !window.confirm(`删除研究笔记“${note.title || "未命名笔记"}”？`)) {
      return;
    }
    setDeletingId(note.id);
    try {
      await api.deleteResearchNote(packageId, note.id);
      setNotes((current) => current.filter((item) => item.id !== note.id));
      if (draft?.id === note.id) {
        setDraft(null);
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究笔记删除失败");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">研究笔记</p>
          <p className="mt-1 text-xs leading-5 text-gray-500">保存自己的判断、摘录与后续研究线索。</p>
        </div>
        <button
          type="button"
          onClick={() => setDraft({ ...EMPTY_DRAFT })}
          disabled={disabled || isSaving}
          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-black px-2.5 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" />
          新建
        </button>
      </div>

      {draft ? (
        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold text-gray-700">{draft.id ? "编辑笔记" : "新建笔记"}</p>
            <button
              type="button"
              onClick={() => setDraft(null)}
              className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
              aria-label="关闭笔记编辑器"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <input
            value={draft.title}
            onChange={(event) => setDraft((current) => (current ? { ...current, title: event.target.value } : current))}
            placeholder="标题（可选）"
            disabled={disabled || isSaving}
            className="mt-2 h-9 w-full rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
          />
          <textarea
            value={draft.content}
            onChange={(event) => setDraft((current) => (current ? { ...current, content: event.target.value } : current))}
            placeholder="记录你的观察、推理或摘录…"
            disabled={disabled || isSaving}
            rows={8}
            className="custom-scrollbar mt-2 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-sm leading-6 outline-none transition focus:border-black"
          />
          <input
            value={draft.tags}
            onChange={(event) => setDraft((current) => (current ? { ...current, tags: event.target.value } : current))}
            placeholder="标签，用逗号分隔"
            disabled={disabled || isSaving}
            className="mt-2 h-9 w-full rounded-md border border-gray-200 px-3 text-sm outline-none transition focus:border-black"
          />
          <button
            type="button"
            onClick={() => void saveDraft()}
            disabled={!draft.content.trim() || disabled || isSaving}
            className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md bg-black px-3 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSaving ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            保存
          </button>
        </section>
      ) : null}

      {isLoading && !notes.length ? (
        <div className="flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white py-8 text-xs text-gray-500">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          正在读取笔记
        </div>
      ) : notes.length ? (
        <div className="space-y-2">
          {notes.map((note) => (
            <article key={note.id} className="group rounded-lg border border-gray-200 bg-white p-3">
              <div className="flex items-start gap-2">
                <StickyNote className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className="min-w-0 flex-1 truncate text-sm font-semibold text-gray-900">
                      {note.title || "未命名笔记"}
                    </p>
                    <div className="flex shrink-0 items-center gap-1 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
                      <button
                        type="button"
                        onClick={() => editNote(note)}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
                        aria-label={`编辑笔记 ${note.title || "未命名笔记"}`}
                      >
                        <PencilLine className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void deleteNote(note)}
                        disabled={deletingId === note.id}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                        aria-label={`删除笔记 ${note.title || "未命名笔记"}`}
                      >
                        {deletingId === note.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                  </div>
                  <p className="mt-1 line-clamp-4 whitespace-pre-wrap break-words text-xs leading-5 text-gray-600">{note.content}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-gray-400">
                    {note.tags.map((tag) => (
                      <span key={tag} className="rounded-full bg-gray-100 px-2 py-0.5 text-gray-600">
                        {tag}
                      </span>
                    ))}
                    {note.citations.length ? <span>{note.citations.length} 条引用</span> : null}
                    <span className="ml-auto inline-flex items-center gap-1">
                      <Check className="h-3 w-3" />
                      {formatUpdatedAt(note.updated_at)}
                    </span>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-gray-200 bg-white px-4 py-8 text-center">
          <StickyNote className="mx-auto h-7 w-7 text-gray-300" />
          <p className="mt-2 text-xs leading-5 text-gray-500">还没有研究笔记。</p>
        </div>
      )}

      {notes.length ? (
        <button
          type="button"
          onClick={() => void refreshNotes()}
          disabled={disabled || isLoading}
          className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-500 transition hover:border-gray-300 hover:text-gray-800 disabled:opacity-50"
        >
          {isLoading ? "正在刷新…" : "刷新笔记"}
        </button>
      ) : null}
    </div>
  );
}
