"use client";

import clsx from "clsx";

import type { CommitRecord, LearningClarificationStatus, LearningRequirementKeyFact, Lesson } from "@/types";

type SummaryRow = {
  key: string;
  label: string;
  value: string;
};

type SpecificNeedParts = {
  scenario?: string;
  deliverable?: string;
  output?: string;
  learningTarget?: string;
};

const SUMMARY_LABELS: Record<string, string> = {
  learning: "用户要学什么",
  level: "自己的水平",
  vocabulary: "词汇量",
  scenario: "面向场景",
  output: "输出需求",
};
const SUMMARY_CATEGORIES = ["learning", "level", "vocabulary", "scenario", "output"] as const;

type SummaryCategory = (typeof SUMMARY_CATEGORIES)[number];

function compactText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function normalizeText(value: string) {
  return compactText(value).replace(/[：:，,。.；;\s/_-]+/g, "").toLowerCase();
}

function isMeaningfulFact(fact: LearningRequirementKeyFact) {
  return compactText(fact.label).length > 0 && compactText(fact.value).length > 0;
}

function factMatches(fact: LearningRequirementKeyFact, needles: string[]) {
  const label = normalizeText(fact.label);
  return needles.some((needle) => label.includes(needle));
}

function isSummaryCategory(value: unknown): value is SummaryCategory {
  return typeof value === "string" && SUMMARY_CATEGORIES.includes(value as SummaryCategory);
}

function legacyCategoryFromLabel(fact: LearningRequirementKeyFact): SummaryCategory | "other" {
  const label = normalizeText(fact.label);
  if (label.includes("需求") || label.includes("输出") || label.includes("产出")) {
    return "output";
  }
  if (
    [
      "学习内容",
      "学习主题",
      "学习目标",
      "学习意愿",
      "目标语言",
      "学习语言",
      "具体领域",
      "学习方向",
      "主题",
      "想学",
      "具体内容",
    ].some((needle) => label.includes(needle))
  ) {
    return "learning";
  }
  if (["当前水平", "自己水平", "已有基础", "基础", "水平"].some((needle) => label.includes(needle))) {
    return "level";
  }
  if (["词汇量", "词汇"].some((needle) => label.includes(needle))) {
    return "vocabulary";
  }
  if (["面向场景", "使用场景", "应用场景", "任务场景", "场景"].some((needle) => label.includes(needle))) {
    return "scenario";
  }
  return "other";
}

function factCategory(fact: LearningRequirementKeyFact): SummaryCategory | "other" {
  return isSummaryCategory(fact.category) ? fact.category : legacyCategoryFromLabel(fact);
}

function latestFactWhere(
  facts: LearningRequirementKeyFact[],
  predicate: (fact: LearningRequirementKeyFact) => boolean
) {
  for (let index = facts.length - 1; index >= 0; index -= 1) {
    const fact = facts[index];
    if (predicate(fact)) {
      return fact;
    }
  }
  return null;
}

function latestFact(facts: LearningRequirementKeyFact[], needles: string[]) {
  return latestFactWhere(facts, (fact) => factMatches(fact, needles));
}

function latestFactByCategory(facts: LearningRequirementKeyFact[], category: SummaryCategory) {
  return latestFactWhere(facts, (fact) => factCategory(fact) === category);
}

function isLearningObjectFact(fact: LearningRequirementKeyFact) {
  return factCategory(fact) === "learning";
}

function isSpecificNeedFact(fact: LearningRequirementKeyFact) {
  return factCategory(fact) === "output" || factMatches(fact, ["具体学习需求", "学习内容需求", "生成需求", "输出需求", "产出需求"]);
}

function cleanLearningValue(value: string) {
  return compactText(value)
    .replace(/^(?:我|用户)?(?:想要|想|希望|打算|准备)?/, "")
    .replace(/^(?:学|学习|复习|练习|了解|理解|掌握|研究)(?:一下|下)?/, "")
    .replace(/^(?:关于|有关|围绕)/, "")
    .trim();
}

function parseSpecificNeed(value: string): SpecificNeedParts {
  const text = compactText(value);
  const aboutMatch = text.match(/^(.*?)(?:关于|围绕|面向|基于)(.+?)的(.+)$/u);
  if (!aboutMatch) {
    return {};
  }

  const action = compactText(aboutMatch[1] ?? "");
  const scenario = compactText(aboutMatch[2] ?? "");
  const deliverable = compactText(aboutMatch[3] ?? "");
  if (!scenario || !deliverable) {
    return {};
  }

  return {
    scenario,
    deliverable,
    learningTarget: `${scenario}${deliverable}`,
    output: action ? `${action}${deliverable}` : deliverable,
  };
}

function joinUnique(parts: Array<string | undefined>) {
  const seen = new Set<string>();
  return parts
    .map((part) => compactText(part ?? ""))
    .filter((part) => {
      if (!part) {
        return false;
      }
      const key = normalizeText(part);
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .join(" / ");
}

function clarityFromCommit(commit: CommitRecord): LearningClarificationStatus | null {
  const value = commit.metadata?.learning_clarification;
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Partial<LearningClarificationStatus>;
  if (!Array.isArray(record.key_facts)) {
    return null;
  }

  return {
    progress: typeof record.progress === "number" ? record.progress : 0,
    label: typeof record.label === "string" ? record.label : "",
    reason: typeof record.reason === "string" ? record.reason : "",
    missing_items: [],
    can_start: record.can_start === true,
    forced_start: record.forced_start === true,
    summary: typeof record.summary === "string" ? record.summary : "",
    key_facts: record.key_facts.flatMap((item) => {
      if (!item || typeof item !== "object") {
        return [];
      }
      const raw = item as unknown as Record<string, unknown>;
      if (typeof raw.label !== "string" || typeof raw.value !== "string") {
        return [];
      }
          return [
            {
              label: raw.label,
              value: raw.value,
              evidence: typeof raw.evidence === "string" ? raw.evidence : "",
              category: typeof raw.category === "string" ? (raw.category as LearningRequirementKeyFact["category"]) : null,
            },
          ];
        }),
    checklist: [],
    next_question: "",
    ready_for_board: record.ready_for_board === true,
  };
}

function lineageCommitIds(lesson: Lesson, targetCommitId: string | null | undefined) {
  const commitsById = new Map(lesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const ids = new Set<string>();
  const stack = targetCommitId ? [targetCommitId] : [];

  while (stack.length) {
    const commitId = stack.pop();
    if (!commitId || ids.has(commitId)) {
      continue;
    }
    ids.add(commitId);
    commitsById.get(commitId)?.parent_ids.forEach((parentId) => stack.push(parentId));
  }

  return ids;
}

function collectFacts(
  clarityStatus: LearningClarificationStatus,
  lesson: Lesson | null | undefined,
  targetCommitId: string | null | undefined
) {
  const facts: LearningRequirementKeyFact[] = [];
  if (lesson) {
    const lineageIds = lineageCommitIds(lesson, targetCommitId);
    lesson.history_graph.commits.forEach((commit) => {
      if (!lineageIds.has(commit.id)) {
        return;
      }
      const clarity = clarityFromCommit(commit);
      if (clarity) {
        facts.push(...clarity.key_facts);
      }
    });
  }
  facts.push(...clarityStatus.key_facts);
  return facts.filter(isMeaningfulFact);
}

function buildSummaryRows(
  clarityStatus: LearningClarificationStatus,
  lesson: Lesson | null | undefined,
  targetCommitId: string | null | undefined
): SummaryRow[] {
  const facts = collectFacts(clarityStatus, lesson, targetCommitId);
  const learningFact = latestFactWhere(facts, isLearningObjectFact);
  const levelFact = latestFactByCategory(facts, "level") ?? latestFact(facts, ["当前水平", "自己水平", "已有基础", "基础", "水平"]);
  const vocabularyFact = latestFactByCategory(facts, "vocabulary") ?? latestFact(facts, ["词汇量", "词汇"]);
  const scenarioFact = latestFactByCategory(facts, "scenario") ?? latestFact(facts, ["面向场景", "使用场景", "应用场景", "任务场景", "场景"]);
  const outputFact = latestFactByCategory(facts, "output") ?? latestFact(facts, ["输出需求", "输出偏好", "产出需求", "生成需求", "学习需求", "需求类型", "输出"]);
  const specificNeedFact = latestFactWhere(facts, isSpecificNeedFact);
  const specificNeedParts = specificNeedFact ? parseSpecificNeed(specificNeedFact.value) : {};

  const rows: SummaryRow[] = [];
  const learningValue = joinUnique([
    learningFact ? cleanLearningValue(learningFact.value) : undefined,
    scenarioFact?.value && specificNeedParts.deliverable
      ? `${scenarioFact.value}${specificNeedParts.deliverable}`
      : specificNeedParts.learningTarget,
  ]);
  if (learningValue) {
    rows.push({ key: "learning", label: SUMMARY_LABELS.learning, value: learningValue });
  }
  if (levelFact?.value) {
    rows.push({ key: "level", label: SUMMARY_LABELS.level, value: compactText(levelFact.value) });
  }
  if (vocabularyFact?.value) {
    rows.push({ key: "vocabulary", label: SUMMARY_LABELS.vocabulary, value: compactText(vocabularyFact.value) });
  }

  const scenarioValue = compactText(scenarioFact?.value ?? specificNeedParts.scenario ?? "");
  if (scenarioValue) {
    rows.push({ key: "scenario", label: SUMMARY_LABELS.scenario, value: scenarioValue });
  }

  const outputValue = compactText(outputFact?.value ?? specificNeedParts.output ?? specificNeedFact?.value ?? "");
  if (outputValue) {
    rows.push({ key: "output", label: SUMMARY_LABELS.output, value: outputValue });
  }

  return rows;
}

interface LearningClarityCardProps {
  barTone: string;
  clarityStatus: LearningClarificationStatus;
  lesson?: Lesson | null;
  targetCommitId?: string | null;
}

export function LearningClarityCard({ barTone, clarityStatus, lesson, targetCommitId }: LearningClarityCardProps) {
  const rows = buildSummaryRows(clarityStatus, lesson, targetCommitId);

  return (
    <div className="rounded-xl border border-blue-100/50 bg-[#f4f6ff] p-4">
      <div className="flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-white shadow-inner">
          <div
            className={clsx("h-full rounded-full transition-all duration-500", barTone)}
            style={{ width: `${clarityStatus.progress}%` }}
          />
        </div>
        <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-bold text-blue-800 shadow-sm">
          {clarityStatus.progress}%
        </span>
      </div>
      {rows.length ? (
        <dl className="mt-4 space-y-3 text-xs leading-6 text-blue-950">
          {rows.map((row) => (
            <div key={row.key} className="grid grid-cols-[86px_minmax(0,1fr)] gap-3">
              <dt className="font-semibold text-blue-700">{row.label}</dt>
              <dd className="min-w-0 break-words text-blue-950">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}
