from __future__ import annotations

import re

MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8
EXPLAIN_REQUEST_PATTERN = re.compile(r"(讲解|解释|说明|讲一下|解释一下|帮我理解)")
APPEND_REQUEST_PATTERN = re.compile(
    r"(续写|继续写|接着写|往后写|后续|新增|追加|新加|新章节|新小节|下一节|下一章|下一部分|末尾)"
)
EXPAND_REQUEST_PATTERN = re.compile(
    r"(扩写|扩展|补充|增加|添加|补(?:一个|一段|一节|一章|一部分|个|段|上))"
)
CONTEXTUAL_CONTINUATION_EXPLANATION_PATTERN = re.compile(
    r"(更(?:大篇幅|详细|深入|完整|系统).{0,12}(?:展开|讲解|讲|说明|分析)|"
    r"(?:展开|详细|深入|继续|接着|往下).{0,8}(?:讲解|讲|说明|分析)|"
    r"按(?:刚才|上面|前面|这个).{0,12}(?:结构|总结|内容).{0,8}(?:讲解|讲|说明|分析)|"
    r"讲透(?:一点|一些)?|展开讲|详细讲|继续讲|接着讲)"
)
SIMPLIFY_REQUEST_PATTERN = re.compile(r"(简化|简单(?:一点|点|些)?|更简单|通俗|更容易懂|更好懂|好理解|容易理解|降低难度|浅显)")
DOCUMENT_TRANSFORM_REQUEST_PATTERN = re.compile(r"(翻译|译成|翻成|转换成|转成|改成|改为)")
WHOLE_DOCUMENT_TARGET_PATTERN = re.compile(
    r"(文档内容|黑板内容|板书内容|版书内容|全文|整篇|整份|这份内容|这篇内容|当前内容|全部内容|所有内容)"
)
REWRITE_REQUEST_PATTERN = re.compile(
    r"(改写|重写|修改|编辑|润色|优化|"
    r"改(?:得|的)?(?:简单|通俗|容易|好懂|具体|更具体|明确|更明确|细致|更细致|清楚|更清楚|更难|难一点|有难度|更有区分度)|"
    r"(?:提高|增加|提升).{0,6}难度|换(?:个|一种)说法)"
)
TARGET_LOCATION_HINT_PATTERN = re.compile(
    r"(选中|这一段|这段|这部分|这里|前面|后面|上面|下面|"
    r"第.{0,8}[章节部分段空题项条句行]|定义|概念|例子|示例|结论|总结|表格|为什么)"
)
RESOURCE_REFERENCE_HINT_PATTERN = re.compile(r"(资料|材料|文档|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word|章节|小节|第.{0,8}[章节部分])", re.IGNORECASE)
EXPLICIT_RESOURCE_REFERENCE_PATTERN = re.compile(r"(资料|材料|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word)", re.IGNORECASE)
RESOURCE_OUTPUT_EXPLANATION_PATTERN = re.compile(
    r"((文档框|右侧|板书|版书|讲义).{0,24}(讲解|解释|说明).{0,48}(章节|小节|内容|资料|材料|文档)|"
    r"(讲解|解释|说明).{0,48}(章节|小节).{0,24}(文档框|右侧|板书|版书|讲义))"
)
LEARNING_START_REQUEST_PATTERN = re.compile(r"(我要学|我想学|想学习|学习一下|开始学|帮我学|学一学)")
FOLLOWUP_EXECUTION_PATTERN = re.compile(r"^(写啊|写|开始|执行|可以|好的|好|就这样|按这个来|照这个来|继续)$")
INTERACTION_RULE_REQUEST_PATTERN = re.compile(r"(规则|互动|轮流|你问我答|按.{0,12}来)")
EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}
DOCUMENT_GENERATION_ACTIONS = r"(生成|写|撰写|创建|整理|制作|设计|输出|产出|编写)"
DOCUMENT_ARTIFACT_NOUNS = (
    r"(文档|讲义|板书|版书|课文|文章|作文|报告|对话|练习|题目|试题|测验|课程|"
    r"教案|教程|学习计划|提纲|大纲|案例|表格|清单|材料|页面|章节|小节)"
)
DOCUMENT_ARTIFACT_REQUEST_PATTERN = re.compile(
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,48}}{DOCUMENT_ARTIFACT_NOUNS}"
    r"|"
    rf"{DOCUMENT_ARTIFACT_NOUNS}.{{0,24}}{DOCUMENT_GENERATION_ACTIONS}"
    r"|"
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,12}}(?:一|几|若干|多)?(?:篇|份|个|套|道|组|页|段|部分)[^吧吗呢啊。！？!?；;\n]{{2,80}}"
)
COMPLEX_REASONING_REQUEST_PATTERN = re.compile(
    r"(深入|深度|严谨|复杂|难题|多步骤|推理|推导|证明|系统分析|仔细分析|完整分析|高质量|complex|reasoning)",
    re.IGNORECASE,
)
PRO_REASONING_REQUEST_PATTERN = re.compile(r"(最高|最强|pro|专家级|特别难|高风险|高价值)", re.IGNORECASE)
