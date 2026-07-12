from __future__ import annotations

LEARNING_INTAKE_STRATEGIES = (
    "starting_point",
    "light_self_report",
    "recent_experience",
    "known_unknown",
    "mode_split",
    "scenario",
    "goal_output",
    "stuck_point",
    "choice_cards",
    "domain_map",
    "recommended_entry",
    "implicit_observation",
)

BLANK_BOARD_LEARNING_INTAKE_POLICY = (
    "通用 learning intake 策略：每轮先判断用户新增信息正在收敛哪一类不确定项，"
    "再选择一个最能推进学习行动的探寻方法；不要机械补字段，不要把用户带进问卷。\n"
    "可用探寻方法：起点定位法、轻量自述法、最近经历法、已会/未会法、学习模式分流法、"
    "场景定位法、目标产出法、卡点定位法、选择卡片法、领域地图法、推荐入口法、隐性观察法。"
    "这些方法只用于自然语言引导，不要变成固定模板，也不要要求用户填写 LearningRequirementSheet。\n"
    "策略选择规则：\n"
    "- 用户只说宽泛领域且起点未知：先用领域地图法打开结构，再用起点定位法或选择卡片法确认起点；"
    "推荐入口只能是暂定入口，主问题优先问当前水平、已会/未会或最近接触情况。\n"
    "- 用户给出背景 + 宽泛学习方向，但 current_level、known_background 和 learner_profile_inference "
    "没有可靠依据：必须优先使用 choice_cards 或 starting_point；chatbot_message 要输出 3-5 个 "
    "A/B/C/D 当前水平画像，让用户低成本选择最像自己的状态；entry_point_options 同步记录这些水平卡片。"
    "卡片必须围绕前置能力、已会/未会、常见卡点、目标深度或最近接触程度动态生成，"
    "不要把卡片做成高级路线选择，也不要在代码里写具体学科、教材、测评或 demo 专属知识。\n"
    "- 用户说“不知道、你安排、帮我安排路线”：用选择卡片法降低表达成本，同时给出推荐入口和推荐理由；"
    "如果同时是纯新手委托式入门，则直接落定领域总览型第一课。\n"
    "- 用户自述“已经会/学过/还没学/忘得多”：优先用轻量自述法或已会/未会法，"
    "把已会、未会、忘得多、下一步入口写入 known_background、learner_profile_inference 和 key_facts。\n"
    "- 用户选择了上轮卡片：把选择对应的画像归入 current_level、known_background 和 "
    "learner_profile_inference；然后推荐一个可开始入口。只有入口已经具体到单一知识点、概念、"
    "方法、步骤或单一问题时，才进入 ready_for_board=true；如果权威入口已给出经资料解析的 source_chapter，"
    "该章节也可直接进入 ready_for_board=true，且不得再追问章节内子主题；否则继续只问一个最关键问题。\n"
    "- 用户说“最近学到、最近做过、最近卡在”：优先用最近经历法；如果有明显卡点，优先用卡点定位法，"
    "先判断卡在概念、步骤、公式/规则、应用迁移、表达或不知道从哪开始。\n"
    "- 用户说“练习、训练、提高、复习、测验、实战、角色扮演”等：优先归为 practice_artifact，"
    "自然收敛想练的内容、当前水平、面向场景；不要只给领域地图，也不要把练习需求当成新知识点教学。\n"
    "- 练习型需求中，如果用户已经说清想练的内容，但没有说明当前水平，必须优先用选择卡片法探寻水平；"
    "chatbot_message 必须先承接用户的练习目标，再给一个自然标题、一个降低选择压力的副标题，以及 "
    "4-6 个 A/B/C 卡片选项。卡片选项由你根据当前技能自主生成，应该覆盖从纯入门、基础规则/结构、"
    "写过基础产物、能完成标准任务、想练复杂任务到不确定等通用水平梯度。entry_point_options 也要记录这些水平卡片。"
    "不要默认用户从基础练起，不要先推荐具体练习难度，也不要在同一轮同时追问面向场景；"
    "等当前水平明确后再继续收敛场景。\n"
    "- 用户表达“为了、用来、应对、解决、学完能做到/会做/看懂/写出”：使用场景定位法或目标产出法，"
    "练习型面向场景写入 target_scenario，新知识点教学的深度倾向写入 target_depth；不要生成泛化 success_criteria。\n"
    "- 用户表达不清时，可以给 3-6 个 A/B/C 选择卡片，但卡片必须是通用学习状态或内容形态；"
    "选择卡片后仍只问一个主问题。\n"
    "文本卡片输出要求：chatbot_message 需要包含自然承接、为什么先定位起点、A/B/C/D 选项、"
    "一个推荐默认项或“不确定也可以选最像的”说明，最后只问一个问题。"
)

BLANK_BOARD_LEARNING_INTAKE_RESPONSE_CONTRACT = {
    "guidance_strategy": (
        "本轮采用的通用引导策略。只能使用 none、starting_point、light_self_report、"
        "recent_experience、known_unknown、mode_split、scenario、goal_output、stuck_point、"
        "choice_cards、domain_map、recommended_entry、implicit_observation。必须和用户当前表达形态匹配："
        "宽泛领域用 domain_map/starting_point/choice_cards；用户给出背景 + 宽泛目标但当前水平未知时，"
        "优先用 choice_cards 或 starting_point；自述已会未会用 known_unknown/light_self_report；"
        "最近经历用 recent_experience；卡点用 stuck_point；练习需求用 mode_split/starting_point/scenario/goal_output；"
        "练习需求缺当前水平时优先用 choice_cards；不知道你安排用 choice_cards/recommended_entry。"
    ),
    "entry_point_options": (
        "2-6 个候选入口或水平卡片，每项包含 label、why_it_matters、best_for；"
        "背景 + 宽泛目标但当前水平未知时，这里必须是当前水平画像卡片，而不是高级内容路线；"
        "练习型缺当前水平时这里必须是当前技能水平卡片，而不是练习任务清单。没有必要时可为空。"
    ),
    "next_question": (
        "清单未完整时下一轮最有价值的一个问题；如果已推荐入口但不了解用户水平，"
        "优先询问当前水平、已会/未会或最近学到哪里；如果使用选择卡片，问题要让用户选择最像自己的状态；"
        "如果用户已说明纯新手入门，必须直接落定基础总览型第一课，next_question 为空；ready_for_board=true 时可为空。"
    ),
}
