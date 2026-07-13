"use client";

import clsx from "clsx";
import { LoaderCircle, Pencil, Play, Plus, Save, Trash2, X } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import type {
  ResearchArtifact,
  ResearchArtifactKind,
  ResearchArtifactLength,
  ResearchEpisodeProfile,
  ResearchNote,
  ResearchSpeaker,
  ResearchSpeakerProfile,
  ResearchTransformation,
  SourceIngestionRecord,
} from "@/types";

type ConfigurationView = "transformations" | "speakers" | "episodes";
type SpeakerDraft = Required<ResearchSpeaker>;

const EMPTY_SPEAKER: SpeakerDraft = { name: "", role: "", voice: "", instructions: "" };
const OUTPUT_KINDS: Array<{ value: Exclude<ResearchArtifactKind, "podcast">; label: string }> = [
  { value: "insight", label: "洞见" },
  { value: "summary", label: "摘要" },
  { value: "study_guide", label: "学习指南" },
  { value: "faq", label: "问答集" },
  { value: "timeline", label: "时间线" },
  { value: "custom", label: "自定义" },
];
const LENGTH_LABELS: Record<ResearchArtifactLength, string> = { short: "简短", medium: "适中", long: "详细" };

type ResearchConfigurationPanelProps = {
  packageId: string;
  sources: SourceIngestionRecord[];
  notes: ResearchNote[];
  transformations: ResearchTransformation[];
  speakerProfiles: ResearchSpeakerProfile[];
  episodeProfiles: ResearchEpisodeProfile[];
  onTransformationsChange: (items: ResearchTransformation[]) => void;
  onSpeakerProfilesChange: (items: ResearchSpeakerProfile[]) => void;
  onEpisodeProfilesChange: (items: ResearchEpisodeProfile[]) => void;
  onArtifactCreated: (artifact: ResearchArtifact) => void;
  onError: (message: string) => void;
};

function updateById<T extends { id: string }>(items: T[], next: T) {
  return items.map((item) => (item.id === next.id ? next : item));
}

function ProfileActions({ onEdit, onDelete, busy }: { onEdit: () => void; onDelete: () => void; busy: boolean }) {
  return (
    <div className="flex items-center gap-1">
      <button type="button" onClick={onEdit} className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-black" aria-label="编辑">
        <Pencil className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={onDelete}
        disabled={busy}
        className="rounded-md p-1.5 text-gray-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
        aria-label="删除"
      >
        {busy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

function FormActions({ editing, busy, disabled, onSave, onCancel }: { editing: boolean; busy: boolean; disabled: boolean; onSave: () => void; onCancel: () => void }) {
  return (
    <div className="mt-3 flex items-center gap-2">
      <button
        type="button"
        onClick={onSave}
        disabled={disabled || busy}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-black px-3 text-xs font-semibold text-white disabled:opacity-50"
      >
        {busy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : editing ? <Save className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
        {editing ? "保存" : "新建"}
      </button>
      {editing ? (
        <button type="button" onClick={onCancel} className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs text-gray-500 hover:bg-gray-100">
          <X className="h-3.5 w-3.5" />取消
        </button>
      ) : null}
    </div>
  );
}

export function ResearchConfigurationPanel(props: ResearchConfigurationPanelProps) {
  const [view, setView] = useState<ConfigurationView>("transformations");
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 rounded-lg border border-gray-200 bg-white p-1">
        {([
          ["transformations", "转换"],
          ["speakers", "发言人"],
          ["episodes", "节目"],
        ] as Array<[ConfigurationView, string]>).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setView(value)}
            className={clsx("rounded-md px-2 py-2 text-xs font-semibold", view === value ? "bg-gray-900 text-white" : "text-gray-500 hover:bg-gray-50")}
          >
            {label}
          </button>
        ))}
      </div>
      {view === "transformations" ? <TransformationManager {...props} /> : null}
      {view === "speakers" ? <SpeakerProfileManager {...props} /> : null}
      {view === "episodes" ? <EpisodeProfileManager {...props} /> : null}
    </div>
  );
}

function TransformationManager({
  packageId,
  sources,
  notes,
  transformations,
  onTransformationsChange,
  onArtifactCreated,
  onError,
}: ResearchConfigurationPanelProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [outputKind, setOutputKind] = useState<Exclude<ResearchArtifactKind, "podcast">>("custom");
  const [runOnImport, setRunOnImport] = useState(false);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [noteIds, setNoteIds] = useState<string[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  function reset() {
    setEditingId(null);
    setName("");
    setInstructions("");
    setOutputKind("custom");
    setRunOnImport(false);
  }

  function edit(item: ResearchTransformation) {
    setEditingId(item.id);
    setName(item.name);
    setInstructions(item.instructions);
    setOutputKind(item.output_kind === "podcast" ? "custom" : item.output_kind);
    setRunOnImport(item.run_on_import);
  }

  async function save() {
    if (!name.trim() || !instructions.trim() || busyId) return;
    setBusyId(editingId ?? "new");
    try {
      const payload = { name: name.trim(), instructions: instructions.trim(), output_kind: outputKind, run_on_import: runOnImport };
      const item = editingId
        ? await api.updateResearchTransformation(packageId, editingId, payload)
        : await api.createResearchTransformation(packageId, payload);
      onTransformationsChange(editingId ? updateById(transformations, item) : [item, ...transformations]);
      reset();
    } catch (error) {
      onError(error instanceof Error ? error.message : "转换保存失败");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(item: ResearchTransformation) {
    if (busyId || !window.confirm(`删除转换“${item.name}”？`)) return;
    setBusyId(item.id);
    try {
      await api.deleteResearchTransformation(packageId, item.id);
      onTransformationsChange(transformations.filter((candidate) => candidate.id !== item.id));
      if (editingId === item.id) reset();
    } catch (error) {
      onError(error instanceof Error ? error.message : "转换删除失败");
    } finally {
      setBusyId(null);
    }
  }

  async function run(item: ResearchTransformation) {
    if (busyId) return;
    setBusyId(`run:${item.id}`);
    try {
      onArtifactCreated(await api.runResearchTransformation(packageId, item.id, { source_ingestion_ids: sourceIds, note_ids: noteIds }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "转换执行失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <p className="text-xs font-semibold text-gray-800">{editingId ? "编辑转换" : "新建转换"}</p>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="转换名称" className="mt-3 h-9 w-full rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black" />
        <textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={4} placeholder="描述转换目标、内容结构与输出要求…" className="mt-2 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-xs leading-5 outline-none focus:border-black" />
        <select value={outputKind} onChange={(event) => setOutputKind(event.target.value as Exclude<ResearchArtifactKind, "podcast">)} className="mt-2 h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black">
          {OUTPUT_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
        </select>
        <label className="mt-3 flex items-center gap-2 text-xs text-gray-600">
          <input type="checkbox" checked={runOnImport} onChange={(event) => setRunOnImport(event.target.checked)} />资料导入完成后运行
        </label>
        <FormActions editing={Boolean(editingId)} busy={busyId === (editingId ?? "new")} disabled={!name.trim() || !instructions.trim()} onSave={() => void save()} onCancel={reset} />
      </section>

      {transformations.length ? (
        <details className="rounded-lg border border-gray-200 bg-white p-3">
          <summary className="cursor-pointer text-xs font-semibold text-gray-700">执行范围：{sourceIds.length ? `${sourceIds.length} 份资料` : "全部资料"}，{noteIds.length ? `${noteIds.length} 条笔记` : "相关笔记"}</summary>
          <div className="mt-3 grid gap-2">
            {[...sources.map((item) => ({ id: item.id, label: item.title, type: "source" as const })), ...notes.map((item) => ({ id: item.id, label: item.title || "未命名笔记", type: "note" as const }))].map((item) => {
              const ids = item.type === "source" ? sourceIds : noteIds;
              const setter = item.type === "source" ? setSourceIds : setNoteIds;
              return <label key={`${item.type}:${item.id}`} className="flex items-center gap-2 text-xs text-gray-600"><input type="checkbox" checked={ids.includes(item.id)} onChange={() => setter(ids.includes(item.id) ? ids.filter((id) => id !== item.id) : [...ids, item.id])} /><span className="truncate">{item.label}</span></label>;
            })}
          </div>
        </details>
      ) : null}

      <div className="space-y-2">
        {transformations.map((item) => (
          <article key={item.id} className="rounded-lg border border-gray-200 bg-white p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0"><p className="truncate text-xs font-semibold text-gray-900">{item.name}</p><p className="mt-1 text-[10px] text-gray-400">{OUTPUT_KINDS.find((kind) => kind.value === item.output_kind)?.label ?? item.output_kind}{item.run_on_import ? " · 导入后运行" : ""}</p></div>
              <ProfileActions onEdit={() => edit(item)} onDelete={() => void remove(item)} busy={busyId === item.id} />
            </div>
            <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-gray-600">{item.instructions}</p>
            <button type="button" onClick={() => void run(item)} disabled={Boolean(busyId) || item.output_kind === "podcast"} className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 px-3 text-xs font-semibold text-gray-700 hover:border-gray-400 disabled:opacity-50">
              {busyId === `run:${item.id}` ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}执行
            </button>
          </article>
        ))}
        {!transformations.length ? <p className="rounded-lg border border-dashed border-gray-200 bg-white py-8 text-center text-xs text-gray-400">还没有保存转换。</p> : null}
      </div>
    </div>
  );
}

function SpeakerProfileManager({ packageId, speakerProfiles, onSpeakerProfilesChange, onError }: ResearchConfigurationPanelProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [speakers, setSpeakers] = useState<SpeakerDraft[]>([{ ...EMPTY_SPEAKER }]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const validSpeakers = speakers.filter((speaker) => speaker.name.trim());

  function reset() { setEditingId(null); setName(""); setSpeakers([{ ...EMPTY_SPEAKER }]); }
  function edit(item: ResearchSpeakerProfile) {
    setEditingId(item.id); setName(item.name); setSpeakers(item.speakers.map((speaker) => ({ name: speaker.name, role: speaker.role ?? "", voice: speaker.voice ?? "", instructions: speaker.instructions ?? "" })));
  }
  function changeSpeaker(index: number, key: keyof SpeakerDraft, value: string) {
    setSpeakers((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item));
  }
  async function save() {
    if (!name.trim() || !validSpeakers.length || busyId) return;
    setBusyId(editingId ?? "new");
    try {
      const payload = { name: name.trim(), speakers: validSpeakers.map((speaker) => ({ name: speaker.name.trim(), role: speaker.role.trim(), voice: speaker.voice.trim(), instructions: speaker.instructions.trim() })) };
      const item = editingId ? await api.updateResearchSpeakerProfile(packageId, editingId, payload) : await api.createResearchSpeakerProfile(packageId, payload);
      onSpeakerProfilesChange(editingId ? updateById(speakerProfiles, item) : [item, ...speakerProfiles]); reset();
    } catch (error) { onError(error instanceof Error ? error.message : "发言人档案保存失败"); } finally { setBusyId(null); }
  }
  async function remove(item: ResearchSpeakerProfile) {
    if (busyId || !window.confirm(`删除发言人档案“${item.name}”？`)) return;
    setBusyId(item.id);
    try { await api.deleteResearchSpeakerProfile(packageId, item.id); onSpeakerProfilesChange(speakerProfiles.filter((candidate) => candidate.id !== item.id)); if (editingId === item.id) reset(); }
    catch (error) { onError(error instanceof Error ? error.message : "发言人档案删除失败"); } finally { setBusyId(null); }
  }

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <p className="text-xs font-semibold text-gray-800">{editingId ? "编辑发言人档案" : "新建发言人档案"}</p>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="档案名称" className="mt-3 h-9 w-full rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black" />
        <div className="mt-2 space-y-2">
          {speakers.map((speaker, index) => (
            <div key={index} className="rounded-md border border-gray-100 bg-gray-50 p-2">
              <div className="grid grid-cols-2 gap-2">
                {(["name", "role", "voice", "instructions"] as Array<keyof SpeakerDraft>).map((key) => <input key={key} value={speaker[key]} onChange={(event) => changeSpeaker(index, key, event.target.value)} placeholder={{ name: "名称", role: "角色", voice: "语音 ID", instructions: "表达要求" }[key]} className="h-8 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black" />)}
              </div>
              {speakers.length > 1 ? <button type="button" onClick={() => setSpeakers((items) => items.filter((_, itemIndex) => itemIndex !== index))} className="mt-2 text-[10px] text-rose-600">移除</button> : null}
            </div>
          ))}
        </div>
        {speakers.length < 4 ? <button type="button" onClick={() => setSpeakers((items) => [...items, { ...EMPTY_SPEAKER }])} className="mt-2 text-xs font-semibold text-gray-500">+ 添加发言人</button> : null}
        <FormActions editing={Boolean(editingId)} busy={busyId === (editingId ?? "new")} disabled={!name.trim() || !validSpeakers.length} onSave={() => void save()} onCancel={reset} />
      </section>
      {speakerProfiles.map((item) => <article key={item.id} className="rounded-lg border border-gray-200 bg-white p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-xs font-semibold text-gray-900">{item.name}</p><p className="mt-1 text-[10px] text-gray-400">{item.speakers.length} 位发言人</p></div><ProfileActions onEdit={() => edit(item)} onDelete={() => void remove(item)} busy={busyId === item.id} /></div><p className="mt-2 text-xs text-gray-600">{item.speakers.map((speaker) => speaker.name).join(" · ")}</p></article>)}
      {!speakerProfiles.length ? <p className="rounded-lg border border-dashed border-gray-200 bg-white py-8 text-center text-xs text-gray-400">还没有保存发言人档案。</p> : null}
    </div>
  );
}

function EpisodeProfileManager({ packageId, episodeProfiles, onEpisodeProfilesChange, onError }: ResearchConfigurationPanelProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("");
  const [tone, setTone] = useState("");
  const [length, setLength] = useState<ResearchArtifactLength>("medium");
  const [segmentCount, setSegmentCount] = useState(6);
  const [instructions, setInstructions] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  function reset() { setEditingId(null); setName(""); setLanguage(""); setTone(""); setLength("medium"); setSegmentCount(6); setInstructions(""); }
  function edit(item: ResearchEpisodeProfile) { setEditingId(item.id); setName(item.name); setLanguage(item.language); setTone(item.tone); setLength(item.length); setSegmentCount(item.segment_count); setInstructions(item.instructions); }
  async function save() {
    if (!name.trim() || busyId) return;
    setBusyId(editingId ?? "new");
    try {
      const payload = { name: name.trim(), language: language.trim(), tone: tone.trim(), length, segment_count: segmentCount, instructions: instructions.trim() };
      const item = editingId ? await api.updateResearchEpisodeProfile(packageId, editingId, payload) : await api.createResearchEpisodeProfile(packageId, payload);
      onEpisodeProfilesChange(editingId ? updateById(episodeProfiles, item) : [item, ...episodeProfiles]); reset();
    } catch (error) { onError(error instanceof Error ? error.message : "节目档案保存失败"); } finally { setBusyId(null); }
  }
  async function remove(item: ResearchEpisodeProfile) {
    if (busyId || !window.confirm(`删除节目档案“${item.name}”？`)) return;
    setBusyId(item.id);
    try { await api.deleteResearchEpisodeProfile(packageId, item.id); onEpisodeProfilesChange(episodeProfiles.filter((candidate) => candidate.id !== item.id)); if (editingId === item.id) reset(); }
    catch (error) { onError(error instanceof Error ? error.message : "节目档案删除失败"); } finally { setBusyId(null); }
  }

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-gray-200 bg-white p-3">
        <p className="text-xs font-semibold text-gray-800">{editingId ? "编辑节目档案" : "新建节目档案"}</p>
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="档案名称" className="mt-3 h-9 w-full rounded-md border border-gray-200 px-3 text-xs outline-none focus:border-black" />
        <div className="mt-2 grid grid-cols-2 gap-2"><input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="语言（可选）" className="h-9 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-black" /><input value={tone} onChange={(event) => setTone(event.target.value)} placeholder="表达语气（可选）" className="h-9 rounded-md border border-gray-200 px-2 text-xs outline-none focus:border-black" /></div>
        <div className="mt-2 grid grid-cols-2 gap-2"><select value={length} onChange={(event) => setLength(event.target.value as ResearchArtifactLength)} className="h-9 rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black">{(Object.keys(LENGTH_LABELS) as ResearchArtifactLength[]).map((value) => <option key={value} value={value}>{LENGTH_LABELS[value]}</option>)}</select><label className="flex h-9 items-center gap-2 rounded-md border border-gray-200 px-2 text-xs text-gray-500">段落数<input type="number" min={3} max={20} value={segmentCount} onChange={(event) => setSegmentCount(Math.min(20, Math.max(3, Number(event.target.value) || 3)))} className="min-w-0 flex-1 text-right outline-none" /></label></div>
        <textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={3} placeholder="补充节目结构与表达要求（可选）…" className="mt-2 w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-xs leading-5 outline-none focus:border-black" />
        <FormActions editing={Boolean(editingId)} busy={busyId === (editingId ?? "new")} disabled={!name.trim()} onSave={() => void save()} onCancel={reset} />
      </section>
      {episodeProfiles.map((item) => <article key={item.id} className="rounded-lg border border-gray-200 bg-white p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-xs font-semibold text-gray-900">{item.name}</p><p className="mt-1 text-[10px] text-gray-400">{LENGTH_LABELS[item.length]} · {item.segment_count} 段{item.language ? ` · ${item.language}` : ""}</p></div><ProfileActions onEdit={() => edit(item)} onDelete={() => void remove(item)} busy={busyId === item.id} /></div>{item.instructions ? <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-gray-600">{item.instructions}</p> : null}</article>)}
      {!episodeProfiles.length ? <p className="rounded-lg border border-dashed border-gray-200 bg-white py-8 text-center text-xs text-gray-400">还没有保存节目档案。</p> : null}
    </div>
  );
}
