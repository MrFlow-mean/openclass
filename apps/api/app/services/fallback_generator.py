from __future__ import annotations

import html

from app.models import ResourceReferenceContext
from app.services.reference_utils import compact_reference_text, reference_key_points, reference_passages


GENERIC_SECTION_LABELS = (
    "问题入口",
    "核心概念",
    "关系与流程",
    "材料证据或推理依据",
    "例子拆解",
    "常见误区",
    "练习任务",
    "参考答案与总结",
)


def generic_document_fallback_html(topic: str) -> str:
    safe_topic = html.escape(topic)
    return f"""
<h1>{safe_topic}</h1>
<p><strong>学习目标：</strong>明确“{safe_topic}”的核心问题，形成可讲解、可练习、可复盘的学习结构。</p>
<h2>一、核心概念</h2>
<p>先说明这个主题要解决什么问题、涉及哪些关键对象，以及判断是否理解的最低标准。</p>
<h2>二、解释与推理</h2>
<p>把概念放到一个最小任务里讲清楚：已知什么、要判断什么、步骤如何连接、结论为何成立。</p>
<h2>三、例子</h2>
<p>给一个简短例子，展示如何把概念应用到真实问题中，并说明常见误区。</p>
<h2>四、练习</h2>
<p>练习 1：用一句话解释“{safe_topic}”解决的核心问题。</p>
<p>练习 2：给出一个可应用的场景和一个不适用的场景，并说明原因。</p>
<h2>五、总结</h2>
<p>回顾关键概念、适用边界和下一步学习方向，确保可迁移到新问题。</p>
""".strip()


def generic_handout_fallback_html(
    topic: str,
    *,
    key_terms: list[str] | None = None,
    section_count: int = 8,
) -> str:
    safe_topic = html.escape(topic)
    terms = [term.strip() for term in key_terms or [] if term.strip()]
    headings = list(GENERIC_SECTION_LABELS)
    while len(headings) < section_count:
        index = len(headings) - len(GENERIC_SECTION_LABELS)
        term = terms[index] if index < len(terms) else f"拓展点 {index + 1}"
        headings.insert(-2, f"{term} 的深入讲解")
    headings = headings[: max(3, section_count)]

    body = [f"<h1>{safe_topic}</h1>"]
    body.append(
        f"<p><strong>学习目标：</strong>围绕“{safe_topic}”建立一条可讲解、可练习、可迁移的学习主线。</p>"
    )
    for index, heading in enumerate(headings, start=1):
        safe_heading = html.escape(heading)
        term_hint = terms[index - 1] if index - 1 < len(terms) else topic
        safe_hint = html.escape(term_hint)
        body.extend(
            [
                f"<h2>第 {index} 小节：{safe_heading}</h2>",
                (
                    f"<p>本节围绕“{safe_hint}”说明它在本课中的作用、与前后内容的关系、"
                    "可观察的判断标准，以及学习者容易混淆的边界。</p>"
                ),
                (
                    "<p>讲解时先给一句话主线，再补充必要条件、推理步骤或材料证据，"
                    "最后用一个小检查确认能否迁移到新问题。</p>"
                ),
            ]
        )
    return "\n".join(body).strip()


def reference_document_fallback_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = html.escape(reference_context.chapter_title or topic)
    lead = compact_reference_text(reference_context.summary, limit=520)
    passages = reference_passages(reference_context)
    points = reference_key_points(reference_context)

    first_passage = passages[0] if passages else lead
    detail_passages = [item for item in passages[1:4] if item.strip()]
    if not detail_passages and first_passage:
        detail_passages = [first_passage]

    key_points = points[:5] if points else ([first_passage] if first_passage else [])
    detail_html = "\n".join(f"<p>{html.escape(item)}</p>" for item in detail_passages)
    points_html = "\n".join(f"<li>{html.escape(item)}</li>" for item in key_points)

    return f"""
<h1>{title}</h1>
<p><strong>学习目标：</strong>基于参考资料梳理本节主线，提炼关键概念并完成可迁移的理解。</p>
<h2>一、资料主线</h2>
<p>{html.escape(first_passage or lead)}</p>
<h2>二、核心概念</h2>
<ol>
{points_html}
</ol>
<h2>三、解释与推理</h2>
{detail_html}
<h2>四、例子</h2>
<p>用一个最小场景演示如何把本节概念应用到具体判断：先列条件，再给步骤，最后说明结论。</p>
<h2>五、练习</h2>
<p>练习 1：用自己的话复述本节主线。</p>
<p>练习 2：选择一个关键概念，说明它的适用边界与常见误区。</p>
<h2>六、总结</h2>
<p>总结本节关键点，并明确继续深入时应优先补充的前置知识或案例。</p>
""".strip()
