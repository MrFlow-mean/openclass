import type {
  InitialLearningGranularity,
  InitialLearningWorkMode,
  LearningClarificationStatus,
  LearningRequirementKeyFact,
  LearningRequirementSheet,
} from "@/types";

export type LearningRequirementDisplayStatus = "collecting" | "ready" | "unknown";
export type LearningRequirementTeachingType = "新知识点教学" | "练习" | "未确定";

export interface LearningRequirementDisplayFactor {
  key: string;
  label: string;
  value: string;
  filled: boolean;
  required: boolean;
}

export interface LearningRequirementDisplay {
  teachingType: LearningRequirementTeachingType;
  status: LearningRequirementDisplayStatus;
  progress: number;
  summary: string;
  coreFactors: LearningRequirementDisplayFactor[];
  auxiliaryFactors: LearningRequirementDisplayFactor[];
}

type BuildLearningRequirementDisplayInput = {
  requirementSheet?: LearningRequirementSheet | null;
  clarification?: LearningClarificationStatus | null;
  keyFacts?: LearningRequirementKeyFact[];
};

const STATUS_LABELS: Record<LearningRequirementDisplayStatus, string> = {
  collecting: "收集中",
  ready: "已齐全",
  unknown: "未确定",
};

export function learningRequirementStatusLabel(status: LearningRequirementDisplayStatus) {
  return STATUS_LABELS[status];
}

export function buildLearningRequirementDisplay({
  requirementSheet,
  clarification,
  keyFacts = [],
}: BuildLearningRequirementDisplayInput): LearningRequirementDisplay {
  const facts = dedupeFacts([...(keyFacts ?? []), ...(clarification?.key_facts ?? [])]);
  const workMode = normalizeWorkMode(requirementSheet?.work_mode ?? clarification?.work_mode ?? null);
  const granularity = normalizeGranularity(requirementSheet?.granularity ?? clarification?.granularity ?? null);
  const learningGoal = meaningfulText(requirementSheet?.learning_goal ?? latestFactByCategory(facts, "learning")?.value);
  const progress = Math.max(0, Math.min(100, clarification?.progress ?? 0));

  if (workMode === "knowledge_board") {
    const isSingleKnowledgePoint = granularity === "single_knowledge_point";
    const knowledgePoint = isSingleKnowledgePoint && learningGoal ? learningGoal : "待收敛到具体知识点";
    const coreFactors = [
      factor({
        key: "knowledge_point",
        label: "知识点",
        value: knowledgePoint,
        filled: Boolean(isSingleKnowledgePoint && learningGoal),
      }),
    ];
    return {
      teachingType: "新知识点教学",
      status: displayStatus(coreFactors, clarification),
      progress: displayProgress(progress, coreFactors, clarification),
      summary: compactText(clarification?.summary || clarification?.reason || learningGoal || ""),
      coreFactors,
      auxiliaryFactors: buildAuxiliaryFactors({
        requirementSheet,
        facts,
        usedValues: [knowledgePoint],
        broadTopic: !isSingleKnowledgePoint ? learningGoal : "",
      }),
    };
  }

  if (workMode === "practice_artifact") {
    const currentLevel = meaningfulText(latestFactByCategory(facts, "level")?.value) || meaningfulText(requirementSheet?.level);
    const targetScenario =
      meaningfulText(latestFactByCategory(facts, "scenario")?.value, { allowNoScenario: true }) ||
      meaningfulText(requirementSheet?.success_criteria, { allowNoScenario: true });
    const coreFactors = [
      factor({
        key: "practice_content",
        label: "练习内容",
        value: learningGoal || "待明确",
        filled: Boolean(learningGoal),
      }),
      factor({
        key: "current_level",
        label: "当前水平",
        value: currentLevel || "待明确",
        filled: Boolean(currentLevel),
      }),
      factor({
        key: "target_scenario",
        label: "目的场景",
        value: targetScenario || "待明确",
        filled: Boolean(targetScenario),
      }),
    ];
    return {
      teachingType: "练习",
      status: displayStatus(coreFactors, clarification),
      progress: displayProgress(progress, coreFactors, clarification),
      summary: compactText(clarification?.summary || clarification?.reason || learningGoal || ""),
      coreFactors,
      auxiliaryFactors: buildAuxiliaryFactors({
        requirementSheet,
        facts,
        usedValues: [learningGoal, currentLevel, targetScenario],
      }),
    };
  }

  const coreFactors = [
    factor({
      key: "learning_type",
      label: "学习类型",
      value: "待判断",
      filled: false,
    }),
  ];
  return {
    teachingType: "未确定",
    status: "unknown",
    progress,
    summary: compactText(clarification?.summary || clarification?.reason || ""),
    coreFactors,
    auxiliaryFactors: buildAuxiliaryFactors({
      requirementSheet,
      facts,
      usedValues: [],
    }),
  };
}

function normalizeWorkMode(value: InitialLearningWorkMode | null | undefined): InitialLearningWorkMode | "unknown" {
  if (value === "knowledge_board" || value === "narrow_topic") {
    return "knowledge_board";
  }
  if (value === "practice_artifact") {
    return "practice_artifact";
  }
  return "unknown";
}

function normalizeGranularity(value: InitialLearningGranularity | null | undefined): InitialLearningGranularity | "unclear" {
  if (value === "single_knowledge_point" || value === "broad_topic" || value === "practice_artifact") {
    return value;
  }
  return "unclear";
}

function factor({
  key,
  label,
  value,
  filled,
  required = true,
}: {
  key: string;
  label: string;
  value: string;
  filled: boolean;
  required?: boolean;
}): LearningRequirementDisplayFactor {
  return {
    key,
    label,
    value: compactText(value),
    filled,
    required,
  };
}

function displayStatus(
  coreFactors: LearningRequirementDisplayFactor[],
  clarification?: LearningClarificationStatus | null
): LearningRequirementDisplayStatus {
  if (clarification?.ready_for_board) {
    return "ready";
  }
  return coreFactors.every((item) => item.filled) ? "ready" : "collecting";
}

function displayProgress(
  progress: number,
  coreFactors: LearningRequirementDisplayFactor[],
  clarification?: LearningClarificationStatus | null
) {
  if (clarification?.ready_for_board || coreFactors.every((item) => item.filled)) {
    return Math.max(progress, 100);
  }
  return progress;
}

function buildAuxiliaryFactors({
  requirementSheet,
  facts,
  usedValues,
  broadTopic,
}: {
  requirementSheet?: LearningRequirementSheet | null;
  facts: LearningRequirementKeyFact[];
  usedValues: Array<string | undefined>;
  broadTopic?: string;
}) {
  const used = new Set(usedValues.map((value) => normalizeText(value ?? "")).filter(Boolean));
  const factors: LearningRequirementDisplayFactor[] = [];

  pushAuxiliary(factors, used, "broad_topic", "当前方向", broadTopic);
  pushAuxiliary(factors, used, "known_background", "已有背景", requirementSheet?.known_background);
  pushAuxiliary(factors, used, "target_depth", "目标深度", requirementSheet?.target_depth);
  pushAuxiliary(factors, used, "output_preference", "输出偏好", requirementSheet?.output_preference);
  pushAuxiliary(factors, used, "success_criteria", "成功标准", requirementSheet?.success_criteria);
  pushAuxiliary(factors, used, "board_scope", "板书范围", requirementSheet?.board_scope?.join(" / "));
  pushAuxiliary(factors, used, "learning_need_checklist", "已记录信息", requirementSheet?.learning_need_checklist?.join(" / "));

  for (const factItem of facts) {
    const label = auxiliaryLabelForFact(factItem);
    if (!label) {
      continue;
    }
    pushAuxiliary(factors, used, `fact_${label}`, label, factItem.value);
  }

  return factors.slice(0, 6);
}

function pushAuxiliary(
  factors: LearningRequirementDisplayFactor[],
  used: Set<string>,
  key: string,
  label: string,
  rawValue?: string | null
) {
  const value = meaningfulText(rawValue, { allowNoScenario: true });
  const normalized = normalizeText(value);
  if (!value || used.has(normalized)) {
    return;
  }
  used.add(normalized);
  factors.push(
    factor({
      key,
      label,
      value,
      filled: true,
      required: false,
    })
  );
}

function auxiliaryLabelForFact(factItem: LearningRequirementKeyFact) {
  if (factItem.category === "vocabulary") {
    return "词汇量";
  }
  if (factItem.category === "output") {
    return "输出需求";
  }
  if (factItem.category === "other") {
    return compactText(factItem.label);
  }
  return "";
}

function latestFactByCategory(facts: LearningRequirementKeyFact[], category: NonNullable<LearningRequirementKeyFact["category"]>) {
  for (let index = facts.length - 1; index >= 0; index -= 1) {
    const factItem = facts[index];
    if (factItem.category === category || legacyCategoryFromLabel(factItem) === category) {
      return factItem;
    }
  }
  return null;
}

function legacyCategoryFromLabel(factItem: LearningRequirementKeyFact): LearningRequirementKeyFact["category"] {
  const label = normalizeText(factItem.label);
  if (["学习内容", "学习主题", "学习目标", "学习方向", "想学", "具体内容", "知识点"].some((item) => label.includes(item))) {
    return "learning";
  }
  if (["当前水平", "自己水平", "已有基础", "基础", "水平"].some((item) => label.includes(item))) {
    return "level";
  }
  if (["面向场景", "使用场景", "应用场景", "目的场景", "场景"].some((item) => label.includes(item))) {
    return "scenario";
  }
  if (["词汇量", "词汇"].some((item) => label.includes(item))) {
    return "vocabulary";
  }
  if (["输出需求", "产出需求", "目标产出", "输出"].some((item) => label.includes(item))) {
    return "output";
  }
  return "other";
}

function dedupeFacts(facts: LearningRequirementKeyFact[]) {
  const seen = new Set<string>();
  return facts.filter((factItem) => {
    const label = compactText(factItem.label);
    const value = compactText(factItem.value);
    if (!label || !value) {
      return false;
    }
    const key = `${normalizeText(label)}:${normalizeText(value)}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function meaningfulText(value: string | null | undefined, options: { allowNoScenario?: boolean } = {}) {
  const text = compactText(value ?? "");
  if (!text) {
    return "";
  }
  if (options.allowNoScenario && text === "无明确应用场景") {
    return text;
  }
  if (
    text.includes("待确认") ||
    text.includes("尚未明确") ||
    text.includes("尚未完全明确") ||
    text.includes("动态决定") ||
    text.includes("根据用户")
  ) {
    return "";
  }
  return text;
}

function compactText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function normalizeText(value: string) {
  return compactText(value).replace(/[：:，,。.；;\s/_-]+/g, "").toLowerCase();
}
