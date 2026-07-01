from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import LearningClarificationStatus, LearningRequirementSheet


BoardMode = Literal[
    "field_map",
    "concept_explanation",
    "scenario_dialogue",
    "practice_drill",
    "review_lesson",
]
BoardContentBlockType = Literal[
    "paragraph",
    "bullet_list",
    "formula",
    "table",
    "example",
    "exercise",
    "misconception",
    "diagram_prompt",
]
BoardScopeKind = Literal["single_lesson", "lesson_series"]
BoardQualitySeverity = Literal["warning", "error"]


class BoardQualityIssue(BaseModel):
    dimension: str
    message: str
    severity: BoardQualitySeverity = "error"
    evidence: str = ""


class BoardQualityValidationResult(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    issues: list[BoardQualityIssue] = Field(default_factory=list)


class BoardContentBlock(BaseModel):
    type: BoardContentBlockType
    text: str = ""
    items: list[str] = Field(default_factory=list)
    latex: str = ""
    explanation: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    title: str = ""
    steps: list[str] = Field(default_factory=list)
    question: str = ""
    answer: str = ""
    hint: str = ""
    misconception: str = ""
    correction: str = ""
    description: str = ""


class BoardSection(BaseModel):
    title: str
    purpose: str = ""
    content_blocks: list[BoardContentBlock] = Field(default_factory=list)


class BoardLesson(BaseModel):
    title: str
    learner_profile: str = ""
    lesson_objective: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    sections: list[BoardSection] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    next_lesson: str | None = None


class CourseSeriesPlan(BaseModel):
    course_title: str
    lessons: list[str] = Field(default_factory=list)
    current_lesson: str = ""
    deferred_lessons: list[str] = Field(default_factory=list)


class BoardTeachingPlan(BaseModel):
    source_summary: str = ""
    domain_hint: str = ""
    content_to_learn: str = ""
    learning_context: str = ""
    learner_profile: str = ""
    board_mode: BoardMode = "concept_explanation"
    scope_kind: BoardScopeKind = "single_lesson"
    course_series_plan: CourseSeriesPlan | None = None
    current_lesson: BoardLesson
    required_structure: list[str] = Field(default_factory=list)
    deferred_topics: list[str] = Field(default_factory=list)
    quality_notes: list[str] = Field(default_factory=list)
    math_adapter_enabled: bool = False


_BASE_BOARD_STRUCTURE = [
    "标题",
    "学习对象",
    "本节目标",
    "前置知识",
    "核心直觉",
    "关键概念",
    "例子",
    "常见误区",
    "课堂练习",
    "本节小结",
    "下一节预告",
]

_MODE_SECTION_TITLES: dict[BoardMode, list[str]] = {
    "field_map": [
        "这个领域是什么",
        "它由哪些部分组成",
        "一个完整流程",
        "第一个最适合学习的入口",
        "后续路线",
    ],
    "concept_explanation": [
        "核心直觉",
        "关键概念",
        "例子",
        "图像或类比",
        "常见误区",
        "课堂练习",
        "本节小结",
    ],
    "scenario_dialogue": [
        "场景目标",
        "核心表达",
        "对话课文",
        "逐句解释",
        "替换练习",
        "用户输出任务",
    ],
    "practice_drill": [
        "当前水平",
        "练习目标",
        "示例题或示例代码",
        "分步提示",
        "用户练习",
        "反馈规则",
    ],
    "review_lesson": [
        "旧知识唤醒",
        "核心框架",
        "易忘点",
        "工作场景例子",
        "典型例子",
        "练习题",
    ],
}

_MATH_DOMAIN_SIGNALS = (
    "极限",
    "函数",
    "导数",
    "积分",
    "分式",
    "公式",
    "方程",
    "数列",
    "概率",
)


def build_board_teaching_plan(
    requirement: LearningRequirementSheet | dict[str, Any],
    clarification: LearningClarificationStatus | dict[str, Any] | None = None,
) -> BoardTeachingPlan:
    content = _content_to_learn(requirement)
    domain = _domain_hint(requirement, content)
    learner = _learner_profile(requirement, clarification)
    learning_context = _learning_context(requirement, clarification)
    scope_controller = LessonScopeController()
    scope_kind = scope_controller.classify(content=content, requirement=requirement, learner_profile=learner)
    board_mode = _select_board_mode(requirement, content=content, learner_profile=learner, scope_kind=scope_kind)
    normalized = BoardTeachingPlan(
        source_summary=_summary(requirement, clarification),
        domain_hint=domain,
        content_to_learn=content,
        learning_context=learning_context,
        learner_profile=learner,
        board_mode=board_mode,
        scope_kind=scope_kind,
        course_series_plan=None,
        current_lesson=BoardLesson(title=content or "当前学习内容"),
        required_structure=list(_BASE_BOARD_STRUCTURE),
        math_adapter_enabled=MathBoardAdapter.is_applicable(domain=domain, content=content, context=learning_context),
    )
    if scope_kind == "lesson_series":
        course_series = split_broad_topic_into_lessons(normalized)
        return build_current_lesson_board_plan(normalized.model_copy(update={"course_series_plan": course_series}))
    return build_current_lesson_board_plan(normalized)


def normalize_board_plan(board_plan: BoardTeachingPlan) -> BoardTeachingPlan:
    section_titles = _MODE_SECTION_TITLES.get(board_plan.board_mode, _MODE_SECTION_TITLES["concept_explanation"])
    sections = board_plan.current_lesson.sections or _sections_from_titles(section_titles)
    required_structure = list(dict.fromkeys([*board_plan.required_structure, *section_titles]))
    return board_plan.model_copy(
        update={
            "required_structure": required_structure,
            "current_lesson": board_plan.current_lesson.model_copy(update={"sections": sections}),
        }
    )


def split_broad_topic_into_lessons(board_plan: BoardTeachingPlan) -> CourseSeriesPlan:
    content = board_plan.content_to_learn or board_plan.current_lesson.title
    parts = _split_topic_parts(content)
    if len(parts) >= 3:
        lessons = [f"第 {index} 课：{part}如何协同工作" for index, part in enumerate(parts, start=1)]
    elif len(parts) == 2:
        first, second = parts
        lessons = [
            f"第 1 课：{first}的直观含义",
            f"第 2 课：{first}与{second}的基本关系",
            f"第 3 课：{content}的基本方法",
            f"第 4 课：{content}中的典型例子",
            f"第 5 课：{content}的常见误区",
            f"第 6 课：{second}的直观判断",
            f"第 7 课：{content}的应用与总结",
        ]
    else:
        entry = parts[0] if parts else content or "当前主题"
        if board_plan.board_mode == "field_map":
            lessons = [
                f"第 1 课：{entry}的领域地图",
                f"第 2 课：{entry}的核心组成",
                f"第 3 课：{entry}的完整流程",
                f"第 4 课：{entry}的第一个实践入口",
            ]
        else:
            lessons = [
                f"第 1 课：{entry}的核心直觉",
                f"第 2 课：{entry}的关键概念",
                f"第 3 课：{entry}的典型例子",
                f"第 4 课：{entry}的练习与应用",
            ]
    return CourseSeriesPlan(
        course_title=_course_title(board_plan.domain_hint, content),
        lessons=lessons,
        current_lesson=lessons[0] if lessons else content,
        deferred_lessons=lessons[1:],
    )


def build_current_lesson_board_plan(board_plan: BoardTeachingPlan) -> BoardTeachingPlan:
    course_series = board_plan.course_series_plan
    title = course_series.current_lesson if course_series else board_plan.current_lesson.title
    next_lesson = course_series.lessons[1] if course_series and len(course_series.lessons) > 1 else None
    objectives = _lesson_objectives(board_plan, title)
    lesson = BoardLesson(
        title=title,
        learner_profile=board_plan.learner_profile,
        lesson_objective=objectives,
        prerequisites=_prerequisites(board_plan),
        sections=_sections_from_titles(_MODE_SECTION_TITLES.get(board_plan.board_mode, _MODE_SECTION_TITLES["concept_explanation"])),
        summary=[
            "用一句话说清本节核心概念。",
            "能把本节例子和学习目标对应起来。",
            "知道下一步要学什么，不把后续模块提前讲完。",
        ],
        next_lesson=next_lesson,
    )
    deferred_topics = _deferred_topics(course_series)
    notes = _quality_notes(board_plan, deferred_topics)
    return normalize_board_plan(
        board_plan.model_copy(
            update={
                "current_lesson": lesson,
                "deferred_topics": deferred_topics,
                "quality_notes": notes,
            }
        )
    )


def validate_board_plan(board_plan: BoardTeachingPlan) -> BoardQualityValidationResult:
    issues: list[BoardQualityIssue] = []
    score = 100
    if board_plan.scope_kind == "lesson_series" and not board_plan.course_series_plan:
        issues.append(BoardQualityIssue(dimension="scopeControl", message="宽泛主题缺少 courseSeriesPlan。"))
        score -= 25
    if not board_plan.current_lesson.learner_profile:
        issues.append(BoardQualityIssue(dimension="learnerFit", message="缺少学习对象画像。"))
        score -= 10
    section_text = "\n".join(section.title for section in board_plan.current_lesson.sections)
    for signal, dimension in [
        ("目标", "structureCompleteness"),
        ("例", "structureCompleteness"),
        ("练习", "exerciseQuality"),
        ("小结", "structureCompleteness"),
    ]:
        if signal not in section_text and signal not in "\n".join(board_plan.required_structure):
            issues.append(BoardQualityIssue(dimension=dimension, message=f"板书计划缺少“{signal}”相关结构。"))
            score -= 10
    if len(board_plan.current_lesson.sections) < 5:
        issues.append(BoardQualityIssue(dimension="boardReadability", message="板书分段过少，不利于课堂呈现。", severity="warning"))
        score -= 8
    if board_plan.scope_kind == "lesson_series" and not board_plan.current_lesson.next_lesson:
        issues.append(BoardQualityIssue(dimension="nextStepClarity", message="拆课后缺少下一节预告。"))
        score -= 8
    math_result = validate_math_rendering(board_plan)
    if math_result.issues:
        issues.extend(math_result.issues)
        score -= max(0, 100 - math_result.score)
    score = max(0, min(100, score))
    passed = score >= 80 and not any(issue.severity == "error" for issue in issues)
    return BoardQualityValidationResult(passed=passed, score=score, issues=issues)


def validate_math_rendering(board_plan: BoardTeachingPlan) -> BoardQualityValidationResult:
    issues: list[BoardQualityIssue] = []
    for path, block_type, value in _iter_plan_strings(board_plan):
        issues.extend(_math_fragment_issues(value, block_type=block_type, path=path))
    score = max(0, 100 - len([issue for issue in issues if issue.severity == "error"]) * 25)
    return BoardQualityValidationResult(passed=not issues, score=score, issues=issues)


def validate_generated_board_text(text: str, board_plan: BoardTeachingPlan) -> BoardQualityValidationResult:
    issues: list[BoardQualityIssue] = []
    score = 100
    stripped = text.strip()
    if not stripped:
        return BoardQualityValidationResult(
            passed=False,
            score=0,
            issues=[BoardQualityIssue(dimension="boardReadability", message="板书正文为空。")],
        )
    for signal, message, dimension in [
        ("目标", "生成结果缺少本节目标。", "structureCompleteness"),
        ("例", "生成结果缺少例子。", "structureCompleteness"),
        ("练习", "生成结果缺少课堂练习。", "exerciseQuality"),
        ("小结", "生成结果缺少本节小结。", "structureCompleteness"),
        ("下一", "生成结果缺少下一步或下一节预告。", "nextStepClarity"),
    ]:
        if signal not in stripped and not (signal == "下一" and "后续" in stripped):
            issues.append(BoardQualityIssue(dimension=dimension, message=message))
            score -= 10
    heading_count = len(re.findall(r"(?m)^#{1,3}\s+", stripped))
    if heading_count < 3:
        issues.append(BoardQualityIssue(dimension="boardReadability", message="生成结果标题层级不足。"))
        score -= 15
    math_issues = _math_fragment_issues(stripped, block_type="paragraph", path="generated_content")
    if math_issues:
        issues.extend(math_issues)
        score -= 25
    for topic in board_plan.deferred_topics:
        clean = re.sub(r"^第\s*\d+\s*课[:：]\s*", "", topic).strip()
        if clean and clean in stripped and _appears_before_next_step(stripped, clean):
            issues.append(
                BoardQualityIssue(
                    dimension="scopeControl",
                    message="生成结果在当前第一课正文中过早展开后续课程主题。",
                    evidence=clean,
                )
            )
            score -= 12
            break
    score = max(0, min(100, score))
    return BoardQualityValidationResult(passed=score >= 80 and not any(issue.severity == "error" for issue in issues), score=score, issues=issues)


def generate_board_ai_input(
    board_plan: BoardTeachingPlan,
    validation: BoardQualityValidationResult | None = None,
) -> dict[str, Any]:
    validation = validation or validate_board_plan(board_plan)
    return {
        "pipeline": "BoardGenerationQualityPipeline",
        "board_mode": board_plan.board_mode,
        "scope_kind": board_plan.scope_kind,
        "domain_hint": board_plan.domain_hint,
        "content_to_learn": board_plan.content_to_learn,
        "learner_profile": board_plan.learner_profile,
        "learning_context": board_plan.learning_context,
        "course_series_plan": board_plan.course_series_plan.model_dump(mode="json") if board_plan.course_series_plan else None,
        "current_lesson": board_plan.current_lesson.model_dump(mode="json"),
        "quality_contract": {
            "must_include": board_plan.required_structure,
            "must_follow_board_mode": board_plan.board_mode,
            "must_generate_only_current_lesson": True,
            "defer_topics_to_next_lessons": board_plan.deferred_topics,
            "quality_notes": board_plan.quality_notes,
            "math_adapter_enabled": board_plan.math_adapter_enabled,
            "math_rules": MathBoardAdapter.rules() if board_plan.math_adapter_enabled else [],
        },
        "validation": validation.model_dump(mode="json"),
    }


def generate_board_document(board_plan: BoardTeachingPlan) -> dict[str, Any]:
    return generate_board_ai_input(board_plan, validate_board_plan(board_plan))


class LessonScopeController:
    def classify(
        self,
        *,
        content: str,
        requirement: LearningRequirementSheet | dict[str, Any],
        learner_profile: str,
    ) -> BoardScopeKind:
        granularity = str(_field(requirement, "granularity") or "")
        if granularity == "single_knowledge_point":
            return "single_lesson"
        if granularity == "broad_topic":
            return "lesson_series"
        parts = _split_topic_parts(content)
        if len(parts) >= 3:
            return "lesson_series"
        if len(parts) == 2 and (_is_beginner(learner_profile) or len(content) >= 5):
            return "lesson_series"
        if len(content) >= 24 and re.search(r"[、,，/]|如何|体系|入门|路线|总览", content):
            return "lesson_series"
        return "single_lesson"


class MathBoardAdapter:
    @staticmethod
    def is_applicable(*, domain: str, content: str, context: str) -> bool:
        haystack = f"{domain}\n{content}\n{context}"
        return any(signal in haystack for signal in _MATH_DOMAIN_SIGNALS)

    @staticmethod
    def rules() -> list[str]:
        return [
            "所有真实公式使用标准 LaTeX；普通解释不要包进公式定界符。",
            "分段函数必须使用 \\begin{cases} ... \\end{cases}。",
            "极限、积分、导数、分式优先用 formula block 或独立公式行。",
            "不得出现 displaystyle、begincases、endcases、xsim x 等半渲染文本。",
            "预习第一课优先讲核心直觉、表格、图像化描述和简单例子。",
        ]


def validateMathRendering(boardPlan: BoardTeachingPlan) -> BoardQualityValidationResult:
    return validate_math_rendering(boardPlan)


def validateBoardPlan(boardPlan: BoardTeachingPlan) -> BoardQualityValidationResult:
    return validate_board_plan(boardPlan)


def buildBoardTeachingPlan(
    requirement: LearningRequirementSheet | dict[str, Any],
    clarification: LearningClarificationStatus | dict[str, Any] | None = None,
) -> BoardTeachingPlan:
    return build_board_teaching_plan(requirement, clarification)


def normalizeBoardPlan(boardPlan: BoardTeachingPlan) -> BoardTeachingPlan:
    return normalize_board_plan(boardPlan)


def splitBroadTopicIntoLessons(boardPlan: BoardTeachingPlan) -> CourseSeriesPlan:
    return split_broad_topic_into_lessons(boardPlan)


def buildCurrentLessonBoardPlan(boardPlan: BoardTeachingPlan) -> BoardTeachingPlan:
    return build_current_lesson_board_plan(boardPlan)


def generateBoardAIInput(boardPlan: BoardTeachingPlan) -> dict[str, Any]:
    return generate_board_ai_input(boardPlan)


def generateBoardDocument(boardPlan: BoardTeachingPlan) -> dict[str, Any]:
    return generate_board_document(boardPlan)


def _content_to_learn(requirement: LearningRequirementSheet | dict[str, Any]) -> str:
    return _first_text(
        _field(requirement, "contentToLearn"),
        _field(requirement, "contentToPractice"),
        _field(requirement, "action_instruction"),
        _first_list_item(_field(requirement, "board_scope")),
        _field(requirement, "theme"),
        _field(requirement, "learning_goal"),
        "当前学习内容",
    )


def _domain_hint(requirement: LearningRequirementSheet | dict[str, Any], content: str) -> str:
    return _first_text(_field(requirement, "domain"), _field(requirement, "theme"), content)


def _learner_profile(
    requirement: LearningRequirementSheet | dict[str, Any],
    clarification: LearningClarificationStatus | dict[str, Any] | None,
) -> str:
    return _first_text(
        _field(requirement, "startingPoint"),
        _field(requirement, "currentLevel"),
        _field(requirement, "level"),
        _field(requirement, "known_background"),
        _field(clarification, "summary"),
        "学习对象未明确",
    )


def _learning_context(
    requirement: LearningRequirementSheet | dict[str, Any],
    clarification: LearningClarificationStatus | dict[str, Any] | None,
) -> str:
    return _first_text(
        _field(requirement, "learningContext"),
        _field(requirement, "targetScenario"),
        _field(requirement, "target_depth"),
        _field(requirement, "success_criteria"),
        _field(clarification, "reason"),
    )


def _summary(
    requirement: LearningRequirementSheet | dict[str, Any],
    clarification: LearningClarificationStatus | dict[str, Any] | None,
) -> str:
    return _first_text(_field(clarification, "summary"), _field(requirement, "learning_goal"), _field(requirement, "theme"))


def _select_board_mode(
    requirement: LearningRequirementSheet | dict[str, Any],
    *,
    content: str,
    learner_profile: str,
    scope_kind: BoardScopeKind,
) -> BoardMode:
    combined = "\n".join(
        [
            str(_field(requirement, "output_preference") or ""),
            str(_field(requirement, "action_instruction") or ""),
            str(_field(requirement, "targetScenario") or ""),
            str(_field(requirement, "learning_goal") or ""),
            content,
            learner_profile,
        ]
    )
    if re.search(r"情景|场景|对话|dialogue|role", combined, flags=re.IGNORECASE):
        return "scenario_dialogue"
    if re.search(r"练习|刷题|操练|drill|题目|代码练", combined, flags=re.IGNORECASE):
        return "practice_drill"
    if re.search(r"复习|回顾|遗忘|重新用起来|review", combined, flags=re.IGNORECASE):
        return "review_lesson"
    topic_parts = _split_topic_parts(content)
    if scope_kind == "lesson_series" and _is_beginner(learner_profile) and (
        len(topic_parts) >= 3 or re.search(r"领域|体系|开发|协同|入门|路线", content)
    ):
        return "field_map"
    return "concept_explanation"


def _lesson_objectives(board_plan: BoardTeachingPlan, title: str) -> list[str]:
    topic = re.sub(r"^第\s*\d+\s*课[:：]\s*", "", title).strip() or board_plan.content_to_learn
    if board_plan.board_mode == "field_map":
        return [
            f"说清“{topic}”要解决什么问题。",
            "看懂核心组成之间如何协同。",
            "找到最适合当前水平的第一个学习入口。",
        ]
    if board_plan.board_mode == "scenario_dialogue":
        return [
            "完成当前场景中的核心表达理解。",
            "能替换关键信息进行一次自己的输出。",
        ]
    if board_plan.board_mode == "practice_drill":
        return [
            "明确本轮练习目标。",
            "能跟着示例完成一个同类任务。",
        ]
    if board_plan.board_mode == "review_lesson":
        return [
            "唤醒旧知识框架。",
            "找回容易遗忘的判断点和使用流程。",
        ]
    return [
        f"理解“{topic}”的核心直觉。",
        "能用一个例子解释它。",
        "完成一个与本节目标匹配的小练习。",
    ]


def _prerequisites(board_plan: BoardTeachingPlan) -> list[str]:
    if board_plan.learner_profile and board_plan.learner_profile != "学习对象未明确":
        return [board_plan.learner_profile]
    return ["先使用学习者已经明确透露的基础，不额外假设背景。"]


def _sections_from_titles(titles: list[str]) -> list[BoardSection]:
    return [
        BoardSection(
            title=title,
            purpose=f"围绕“{title}”组织一小段可定位板书。",
            content_blocks=[BoardContentBlock(type="paragraph", text=f"生成时展开“{title}”。")],
        )
        for title in titles
    ]


def _quality_notes(board_plan: BoardTeachingPlan, deferred_topics: list[str]) -> list[str]:
    notes = [
        "只生成当前第一节课，后续内容只放进简短路线，不在正文完整展开。",
        "板书要包含目标、核心直觉、例子、常见误区、课堂练习、小结和下一步。",
    ]
    if board_plan.math_adapter_enabled:
        notes.extend(MathBoardAdapter.rules())
    if deferred_topics:
        notes.append("这些主题只作为后续路线出现：" + "；".join(deferred_topics[:6]))
    return notes


def _deferred_topics(course_series: CourseSeriesPlan | None) -> list[str]:
    if not course_series:
        return []
    return course_series.deferred_lessons or course_series.lessons[1:]


def _course_title(domain: str, content: str) -> str:
    if domain and content and content not in domain:
        return f"{domain}：{content}"
    return content or domain or "学习课程"


def _split_topic_parts(content: str) -> list[str]:
    raw_parts = re.split(r"[、,，/]+|以及|和|与", content or "")
    parts = [re.sub(r"\s+", " ", part).strip(" ：:") for part in raw_parts]
    return [part for part in parts if part]


def _is_beginner(text: str) -> bool:
    return bool(re.search(r"新手|零基础|刚开始|刚接触|刚学|入门|预习|初学|beginner", text or "", flags=re.IGNORECASE))


def _appears_before_next_step(text: str, topic: str) -> bool:
    topic_index = text.find(topic)
    if topic_index < 0:
        return False
    next_markers = [index for marker in ("后续", "下一", "路线", "预告") if (index := text.find(marker)) >= 0]
    if not next_markers:
        return True
    return topic_index < min(next_markers)


def _iter_plan_strings(board_plan: BoardTeachingPlan) -> list[tuple[str, BoardContentBlockType | None, str]]:
    values: list[tuple[str, BoardContentBlockType | None, str]] = [
        ("content_to_learn", None, board_plan.content_to_learn),
        ("current_lesson.title", None, board_plan.current_lesson.title),
        ("current_lesson.learner_profile", None, board_plan.current_lesson.learner_profile),
    ]
    for index, section in enumerate(board_plan.current_lesson.sections):
        values.append((f"sections[{index}].title", None, section.title))
        values.append((f"sections[{index}].purpose", None, section.purpose))
        for block_index, block in enumerate(section.content_blocks):
            block_type = block.type
            block_values = [
                block.text,
                block.latex,
                block.explanation,
                block.title,
                "\n".join(block.items),
                "\n".join(block.steps),
                block.question,
                block.answer,
                block.hint,
                block.misconception,
                block.correction,
                block.description,
                "\n".join(block.headers),
                "\n".join(" | ".join(row) for row in block.rows),
            ]
            for value_index, value in enumerate(block_values):
                if value:
                    values.append((f"sections[{index}].blocks[{block_index}].value[{value_index}]", block_type, value))
    return values


def _math_fragment_issues(
    value: str,
    *,
    block_type: BoardContentBlockType | None,
    path: str,
) -> list[BoardQualityIssue]:
    issues: list[BoardQualityIssue] = []
    checks = [
        ("displaystyle", "出现 displaystyle，可能是半渲染公式。"),
        ("begincases", "出现 begincases，分段函数没有使用标准 LaTeX cases。"),
        ("endcases", "出现 endcases，分段函数没有使用标准 LaTeX cases。"),
        ("ε δ-", "出现 ε δ- 断裂字符串。"),
    ]
    for needle, message in checks:
        if needle in value:
            issues.append(BoardQualityIssue(dimension="mathRendering", message=message, evidence=f"{path}: {needle}"))
    if block_type != "formula" and re.search(r"(?<!\\)\bfrac\b", value):
        issues.append(BoardQualityIssue(dimension="mathRendering", message="普通文本中出现 frac，疑似公式未进入公式块。", evidence=path))
    if re.search(r"lim\s*x\s*→", value):
        issues.append(BoardQualityIssue(dimension="mathRendering", message="出现 lim x→ 这类半渲染极限文本。", evidence=path))
    if block_type != "formula" and re.search(r"\w+\s*sim\s*\w+|\wsim\s*\w", value):
        issues.append(BoardQualityIssue(dimension="mathRendering", message="出现 sim 代替 \\sim 的半渲染文本。", evidence=path))
    return issues


def _field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _first_list_item(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            value = "；".join(str(item) for item in value if str(item).strip())
        text = str(value).strip()
        if text:
            return text
    return ""
