from __future__ import annotations

import html
import re

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    ResourceReferenceContext,
    TeachingGuide,
    TeachingGuideMapping,
    new_id,
    now_iso,
)
from app.services.rich_document import build_document


def slugify(value: str) -> str:
    lowered = re.sub(r"\s+", "-", value.strip().lower())
    lowered = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", lowered)
    return lowered.strip("-") or new_id("lesson")


def build_requirements(topic: str) -> LearningRequirementSheet:
    is_language = any(keyword in topic.lower() for keyword in ["french", "法语", "dialogue", "对话"])
    if is_language:
        return LearningRequirementSheet(
            theme=topic,
            learning_goal="用一篇完整场景讲义掌握真实交流表达、语法重点和可替换练习",
            level="B1-B2 可调整",
            known_background="需要从完整对话、中文理解和语法迁移三条线一起建立语感",
            current_questions=["如何在真实场景中开口", "如何把时态自然放进对话"],
            learning_need_checklist=["理解完整场景中的表达意图", "掌握关键语法并能替换造句", "能复述并迁移到类似对话"],
            target_depth="能朗读、复述并替换生成一段同类对话",
            output_preference="Word 式连续讲义：标题、场景、完整对话、语法、词汇、练习、答案",
            boundary="聚焦单个沟通场景，不拆成零散知识点卡片",
            board_scope=["场景说明", "完整双语对话", "语法重点", "词汇表达", "练习与答案"],
            success_criteria="用户能理解对话主线，识别过去将来时，并能替换点餐内容完成新对话",
        )
    return LearningRequirementSheet(
        theme=topic,
        learning_goal="理解概念、能跟着连续讲义讲清楚并完成基础练习",
        level="初学到进阶之间",
        known_background="已有零散印象，但需要结构化讲解",
        current_questions=[f"{topic}的定义是什么", "它为什么重要", "应该怎么用"],
        learning_need_checklist=[f"说清楚{topic}的核心概念", "解释它为什么重要", "用例子或练习检查是否理解"],
        target_depth="先做到能讲清基本定义并会做入门题",
        output_preference="Word 式连续讲义：定义、直觉、例题、练习、总结",
        boundary="先不无限展开相邻学科的更大知识域",
        board_scope=["定义", "直觉", "核心公式或规律", "例题", "练习"],
        success_criteria="用户能复述核心概念并完成一题相关练习",
    )


def _language_cafe_html() -> str:
    return """
<h1>法国咖啡厅点餐情景对话：过去将来时综合讲义</h1>
<p><strong>适用水平：</strong>B1-B2。<strong>学习目标：</strong>通过一段较完整的咖啡厅点餐场景，掌握礼貌点餐、服务员推荐、回忆昨日安排、表达原本打算与将来计划的说法。</p>
<h2>一、场景说明</h2>
<p>你刚到巴黎，下午走进一家街角咖啡厅。你想点一杯咖啡和甜点，同时向服务员询问有没有今天推荐的东西。对话中会自然出现过去、将来和“过去将来时”的表达，例如 <em>je prendrais</em>、<em>nous reviendrions</em>、<em>j’allais commander</em>。</p>
<h2>二、完整双语对话</h2>
<p><strong>Serveur：</strong>Bonjour madame, bienvenue au Café des Lilas. Vous voulez vous installer près de la fenêtre ?<br><strong>服务员：</strong>您好，女士，欢迎来到丁香咖啡馆。您想坐在窗边吗？</p>
<p><strong>Cliente：</strong>Oui, merci. Je viens d’arriver à Paris, et je cherchais justement un endroit calme pour prendre un café.<br><strong>顾客：</strong>好的，谢谢。我刚到巴黎，正好在找一个安静的地方喝杯咖啡。</p>
<p><strong>Serveur：</strong>Très bon choix. Aujourd’hui, nous avons une tarte aux pommes maison et un gâteau au chocolat. Hier, la tarte est partie très vite.<br><strong>服务员：</strong>您选得很好。今天我们有自制苹果挞和巧克力蛋糕。昨天苹果挞很快就卖完了。</p>
<p><strong>Cliente：</strong>Ah, parfait. Dans le train, je pensais que je prendrais seulement un café, mais votre tarte me tente beaucoup.<br><strong>顾客：</strong>太好了。在火车上我原本以为我只会点一杯咖啡，但你们的苹果挞很吸引我。</p>
<p><strong>Serveur：</strong>Je vous comprends. Si vous voulez quelque chose de doux mais pas trop lourd, je vous conseillerais la tarte.<br><strong>服务员：</strong>我理解。如果您想要甜一点但又不太腻的东西，我会推荐苹果挞。</p>
<p><strong>Cliente：</strong>Alors je vais prendre un café crème et une part de tarte aux pommes, s’il vous plaît.<br><strong>顾客：</strong>那我要一杯加奶咖啡和一块苹果挞，谢谢。</p>
<p><strong>Serveur：</strong>Très bien. Vous désirez aussi de l’eau ?<br><strong>服务员：</strong>好的。您还需要水吗？</p>
<p><strong>Cliente：</strong>Oui, une carafe d’eau, s’il vous plaît. J’avais dit à mes amis que nous reviendrions peut-être ici demain matin, alors je voudrais bien goûter la spécialité de la maison.<br><strong>顾客：</strong>好的，请来一壶水。我之前跟朋友说我们明天早上也许会再来这里，所以我很想尝尝店里的招牌。</p>
<p><strong>Serveur：</strong>Dans ce cas, vous avez raison de commencer par la tarte. Beaucoup de clients disent qu’ils reviendraient rien que pour elle.<br><strong>服务员：</strong>这样的话，您先尝苹果挞很对。很多客人说他们光是为了它也会再来。</p>
<p><strong>Cliente：</strong>C’est noté. Et si mes amis venaient avec moi demain, nous commanderions sûrement plusieurs desserts à partager.<br><strong>顾客：</strong>我记住了。如果我的朋友们明天和我一起来，我们肯定会点几份甜点一起分享。</p>
<p><strong>Serveur：</strong>Excellente idée. Je vous apporte ça tout de suite.<br><strong>服务员：</strong>好主意。我马上给您拿来。</p>
<p><strong>Cliente：</strong>Merci beaucoup. Et après le dessert, je voudrais l'addition, s'il vous plaît.<br><strong>顾客：</strong>非常感谢。甜点之后我想买单，谢谢。</p>
<p><strong>Serveur：</strong>Bien sûr, madame. Je vous apporterai l'addition avec plaisir.<br><strong>服务员：</strong>当然，女士。我很乐意把账单拿给您。</p>
<p><strong>Cliente：</strong>Merci beaucoup. Je suis contente d’avoir trouvé ce café avant la pluie.<br><strong>顾客：</strong>非常感谢。我很高兴在下雨前找到了这家咖啡馆。</p>
<h2>三、语法重点：过去将来时与条件式</h2>
<p>法语里中文常说的“过去将来时”，很多时候会用 <strong>条件式现在时 conditionnel présent</strong> 来表达：站在过去某个时间点看未来将要发生的事。结构常见为：<strong>过去主句 + conditionnel présent</strong>。</p>
<p><strong>例句 1：</strong><em>Je pensais que je prendrais seulement un café.</em> 我原本以为我只会点一杯咖啡。这里 <em>pensais</em> 把视角放在过去，<em>prendrais</em> 表示从那个过去时刻看出去的未来动作。</p>
<p><strong>例句 2：</strong><em>J’avais dit à mes amis que nous reviendrions peut-être ici demain matin.</em> 我之前跟朋友说我们明天早上也许会再来这里。<em>avais dit</em> 是过去完成的说话动作，<em>reviendrions</em> 是当时说话内容里的未来。</p>
<p><strong>例句 3：</strong><em>Si mes amis venaient avec moi demain, nous commanderions sûrement plusieurs desserts.</em> 如果我的朋友明天和我一起来，我们肯定会点几份甜点。这里是条件句：<em>si + imparfait</em>，主句用 <em>conditionnel présent</em>。</p>
<h2>四、点餐表达整理</h2>
<p><strong>礼貌点餐：</strong><em>Je vais prendre...</em> 我想要……；<em>Je voudrais...</em> 我想要……；<em>Je voudrais l'addition, s'il vous plaît.</em> 我想买单，谢谢；<em>Qu’est-ce que vous me conseillez ?</em> 您推荐什么？</p>
<p><strong>甜点与饮品：</strong><em>un café crème</em> 加奶咖啡；<em>une tarte aux pommes</em> 苹果挞；<em>un gâteau au chocolat</em> 巧克力蛋糕；<em>une carafe d’eau</em> 一壶水。</p>
<p><strong>服务员常用语：</strong><em>Vous désirez aussi de l’eau ?</em> 您还需要水吗？<em>Je vous apporte ça tout de suite.</em> 我马上给您拿来。</p>
<h2>五、替换练习</h2>
<ol>
<li>把 <em>un café crème</em> 换成 <em>un thé au citron</em>，重写顾客点单句。</li>
<li>用 <em>Je pensais que...</em> 写一句“我原本以为我会点热巧克力”。</li>
<li>用 <em>J’avais dit que...</em> 写一句“我之前说我们会明天回来”。</li>
<li>用 <em>Si mes amis venaient...</em> 写一句“如果朋友们来，我们会分享一个蛋糕”。</li>
</ol>
<h2>六、参考答案</h2>
<p>1. <em>Je vais prendre un thé au citron et une part de tarte, s’il vous plaît.</em></p>
<p>2. <em>Je pensais que je prendrais un chocolat chaud.</em></p>
<p>3. <em>J’avais dit que nous reviendrions demain.</em></p>
<p>4. <em>Si mes amis venaient, nous partagerions un gâteau.</em></p>
<h2>七、课堂朗读建议</h2>
<p>第一遍只读法语，抓住场景动作；第二遍对照中文，确认每句话在做什么；第三遍重点朗读含有 <em>prendrais</em>、<em>reviendrions</em>、<em>commanderions</em> 的句子，体会“站在过去看未来”的语感。</p>
""".strip()


def _compact_reference_text(value: str, limit: int = 420) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _reference_passages(reference_context: ResourceReferenceContext) -> list[str]:
    passages = [
        _compact_reference_text(chunk.excerpt)
        for chunk in reference_context.chunks
        if chunk.excerpt.strip()
    ]
    if passages:
        return passages[:4]

    raw_passages = [
        _compact_reference_text(segment)
        for segment in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", reference_context.full_text)
        if len(segment.strip()) >= 8
    ]
    if raw_passages:
        return raw_passages[:4]

    return [_compact_reference_text(reference_context.summary)]


def _reference_key_points(reference_context: ResourceReferenceContext) -> list[str]:
    points = [
        point
        for point in reference_context.teaching_points
        if point.strip() and "不要照搬原文" not in point
    ]
    if points:
        return points[:5]

    points = [
        chunk.teaching_hint
        for chunk in reference_context.chunks
        if chunk.teaching_hint.strip()
    ]
    return points[:5]


def _is_pattern_recognition_reference(reference_context: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference_context.chapter_title,
            reference_context.summary,
            *reference_context.teaching_points,
            *(chunk.excerpt for chunk in reference_context.chunks[:4]),
        ]
    )
    compact = re.sub(r"\s+", "", corpus)
    compact_title = re.sub(r"\s+", "", reference_context.chapter_title)
    return (
        ("概论" in compact_title or "第一章" in compact_title or "第1章" in compact_title)
        and "模式识别" in compact
        and ("监督" in compact or "分类器" in compact or "聚类" in compact)
    )


def _is_statistical_learning_reference(reference_context: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference_context.chapter_title,
            reference_context.summary,
            *reference_context.teaching_points,
            *(chunk.excerpt for chunk in reference_context.chunks[:4]),
            reference_context.full_text[:3000],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    return (
        ("统计学习理论" in compact or "statisticallearning" in compact)
        and any(term in compact for term in ("经验风险", "真实风险", "期望风险", "vc", "一致性", "推广能力"))
    )


def _is_density_estimation_reference(reference_context: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference_context.chapter_title,
            reference_context.summary,
            *reference_context.teaching_points,
            *(chunk.excerpt for chunk in reference_context.chunks[:4]),
            reference_context.full_text[:4000],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    return any(
        term in compact
        for term in (
            "概率密度函数的估计",
            "概率密度估计",
            "密度函数估计",
            "类条件概率密度",
        )
    ) and any(term in compact for term in ("最大似然", "贝叶斯", "先验概率", "训练样本", "似然函数"))


_HUMANITIES_MARKERS = (
    "文科",
    "文学",
    "历史",
    "哲学",
    "政治",
    "法律",
    "法学",
    "社会",
    "文化",
    "教育",
    "伦理",
    "艺术",
    "美学",
    "传播",
    "新闻",
    "心理",
    "经济",
    "管理",
    "语文",
    "古文",
    "诗歌",
    "小说",
    "散文",
    "制度",
    "思想",
    "观点",
    "论证",
    "叙事",
    "人物",
    "原因",
    "影响",
    "意义",
    "评价",
    "改革",
    "变法",
    "革命",
    "战争",
)

_TECHNICAL_MARKERS = (
    "统计学习理论",
    "模式识别",
    "机器学习",
    "概率密度",
    "密度函数",
    "类条件概率",
    "最大似然",
    "贝叶斯估计",
    "算法",
    "函数",
    "公式",
    "定理",
    "vc",
    "python",
    "计算机",
    "虚拟内存",
)


def _humanities_marker_count(text: str) -> int:
    compact = re.sub(r"\s+", "", text).lower()
    return sum(1 for marker in _HUMANITIES_MARKERS if marker.lower() in compact)


def _is_humanities_reference(reference_context: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference_context.resource_name,
            reference_context.chapter_title,
            reference_context.summary,
            *reference_context.teaching_points,
            *(chunk.excerpt for chunk in reference_context.chunks[:4]),
            reference_context.full_text[:2600],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    if any(marker in compact for marker in _TECHNICAL_MARKERS):
        return False
    return _humanities_marker_count(corpus) >= 2


def _humanities_key_terms(text: str) -> list[str]:
    candidates = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z\s-]{2,30}", text)
    skip = {
        "这一章",
        "本章",
        "材料",
        "内容",
        "可以",
        "说明",
        "通过",
        "因为",
        "所以",
        "如果",
        "一个",
        "这种",
        "重要",
        "影响",
        "意义",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = " ".join(candidate.split()).strip(" ，,。！？!?；;：:")
        if len(cleaned) < 2 or cleaned in skip or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
    return terms[:5]


def _humanities_expansion_html(index: int, title: str, excerpt: str) -> str:
    escaped_excerpt = html.escape(_compact_reference_text(excerpt, limit=360))
    terms = _humanities_key_terms(excerpt)
    term_text = "、".join(terms[:4]) if terms else title
    if any(marker in excerpt for marker in ("原因", "因为", "导致", "促成", "推动", "背景")):
        angle = (
            "这处内容适合按因果链展开：先讲背景条件，再讲触发因素，最后讲结果如何改变原有局面。"
            "文科讲解不能只给结论，要让学生看见“为什么会发生”和“为什么会产生这个后果”。"
        )
    elif any(marker in excerpt for marker in ("观点", "认为", "主张", "提出", "思想", "论证")):
        angle = (
            "这处内容适合按观点论证展开：先把作者或材料的核心判断翻译成直白语言，再说明它依靠哪些事实、概念或价值判断成立。"
            "讲清论证链条后，学生才不只是记住一句观点，而是能判断这句话为什么有说服力。"
        )
    elif any(marker in excerpt for marker in ("形象", "描写", "叙事", "象征", "情感", "人物", "语言")):
        angle = (
            "这处内容适合做文本细读：先抓关键词和叙述角度，再解释它怎样塑造人物、情感或主题。"
            "文学类材料尤其要避免只说“表达了思想感情”，而要指出文本细节如何一步步产生这种效果。"
        )
    else:
        angle = (
            "这处内容适合做概念展开：先把材料中的关键词放回上下文，再说明它和前后内容的关系。"
            "讲解时要从“是什么”推进到“为什么重要”，再落到“怎样在材料中识别它”。"
        )
    return f"""
<h3>重点 {index}：{html.escape(term_text)}</h3>
<blockquote><p>{escaped_excerpt}</p></blockquote>
<p><strong>材料原意：</strong>这段话的重点不是孤立记忆“{html.escape(term_text)}”，而是看它在本节中承担什么功能：它可能在交代背景、提出观点、铺开因果关系，或者用具体细节支撑一个判断。</p>
<p><strong>扩讲：</strong>{html.escape(angle)}</p>
<p><strong>延伸理解：</strong>把“{html.escape(term_text)}”放回整节课的主线中看，它不是一个可替换的标签，而是理解材料的支点。学生需要说清它和前文条件、后文结论之间的连接：它改变了什么关系，强化了什么判断，又留下了什么值得讨论的限制。</p>
<p><strong>课堂落点：</strong>讲到这里可以追问学生：这段材料给了哪些事实或细节？它们共同指向什么结论？如果删掉其中一个条件，原来的判断会不会变弱？这样能把泛泛的概括变成可分析、可复述、可迁移的理解。</p>
""".strip()


def _humanities_reference_lesson_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = html.escape(reference_context.chapter_title or topic)
    source_name = html.escape(reference_context.resource_name)
    passages = [
        passage
        for passage in _reference_passages(reference_context)
        if passage.strip() and "目录顺序" not in passage
    ]
    raw_segments = [
        _compact_reference_text(segment, limit=320)
        for segment in re.split(r"\n+|(?<=[。！？!?])", reference_context.full_text)
        if len(segment.strip()) >= 18
    ]
    seen_passages = {passage[:80] for passage in passages}
    for segment in raw_segments:
        key = segment[:80]
        if key in seen_passages:
            continue
        seen_passages.add(key)
        passages.append(segment)
        if len(passages) >= 4:
            break
    if not passages:
        passages = [reference_context.summary or reference_context.chapter_title or topic]
    lead = _compact_reference_text(reference_context.summary or passages[0], limit=460)
    expansions = "\n".join(
        _humanities_expansion_html(index, reference_context.chapter_title or topic, passage)
        for index, passage in enumerate(passages[:4], start=1)
    )
    terms = _humanities_key_terms(" ".join(passages[:3]) or reference_context.full_text)
    term_items = "\n".join(f"<li>{html.escape(term)}</li>" for term in terms[:6])
    if not term_items:
        term_items = f"<li>{title}</li>"
    return f"""
<h1>{title}：重点扩讲板书</h1>
<p><strong>参考资料：</strong>本讲义依据《{source_name}》中“{title}”组织。文科资料不能只列目录或提纲，本板书会把材料中的关键观点、事件、人物、概念或文本细节展开讲清。</p>
<h2>一、本节主线</h2>
<p>{html.escape(lead)}</p>
<p>讲这类材料时，先帮助学生建立三个问题：材料在讲什么对象？它为什么重要？作者或教材用哪些事实、细节、论证或叙事方式来支撑这个重点？</p>
<h2>二、关键术语与分析抓手</h2>
<ol>
{term_items}
</ol>
<p>这些词不是背诵清单，而是阅读材料的抓手。每个词都要追问：它出现在哪里，和谁形成关系，推动了什么变化，或者揭示了什么主题。</p>
<h2>三、重要内容扩讲</h2>
{expansions}
<h2>四、讲解顺序</h2>
<ol>
<li>先交代背景和对象，避免学生一上来只记碎片信息。</li>
<li>再挑出最重要的观点、事件、人物或文本细节，逐段解释其含义和作用。</li>
<li>接着补出因果链、论证链或文本细读链，让学生知道结论如何从材料中长出来。</li>
<li>最后用比较和追问收束：它和相近概念有什么区别？换一个条件后结论是否仍成立？</li>
</ol>
<h2>五、课堂检查</h2>
<ol>
<li>用一句话概括本节最重要的观点或事件，并说出依据。</li>
<li>选一个关键词，说明它在材料中连接了哪些人物、事实、情节或观点。</li>
<li>找一处可以展开的细节，按“材料原意 -> 背景/原因 -> 影响/意义”讲给同学听。</li>
</ol>
<h2>六、小结</h2>
<p>文科板书的质量不在于标题多，而在于能否把重要内容讲厚：既保留材料中的具体词句和事实，又能说明背后的背景、逻辑、价值和误区。学完这一节，学生应当能从材料出发说出自己的分析，而不是只复述一个空泛结论。</p>
""".strip()


def _statistical_learning_lesson_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = html.escape(reference_context.chapter_title or topic)
    source_name = html.escape(reference_context.resource_name)
    return f"""
<h1>{title}：讲解板书</h1>
<p><strong>参考资料：</strong>本讲义依据《{source_name}》中“{title}”组织，但不直接粘贴抽取原文；下面把章节内容改写成可以连续讲授、可以直接板书的课堂版本。生成原则是：先保留教材主线，再主动扩讲核心知识点，让每个概念都有“为什么提出、解决什么问题、怎样理解、容易错在哪里”。</p>
<h2>一、本章定位</h2>
<p>第七章讨论的问题不是“某一个分类器怎么训练”，而是“为什么训练出来的机器能在未知样本上表现好”。前面章节常见的思路是：训练样本 -> 选择模型 -> 最小化训练误差 -> 得到分类器。</p>
<p>统计学习理论追问的是：训练误差小，是否一定代表测试误差小？答案是不一定。本章的核心矛盾可以先写成一句话：</p>
<blockquote><p>经验风险小 ≠ 真实风险小。机器学习真正追求的是推广能力，而不是只在训练集上表现好。</p></blockquote>
<p><strong>主动扩讲：</strong>模式识别前几章会介绍贝叶斯决策、线性分类器、非线性分类器等方法，这些方法看起来都在回答“怎样训练一个分类器”。但真正部署系统时，训练集只是现实世界的一小部分样本。一个分类器如果只在这批样本上表现好，却不能处理未来样本，就没有实际价值。所以第七章把问题从“如何把训练误差降下来”推进到“如何保证学到的规律能推广”。</p>
<p>这也是统计学习理论和普通算法讲解的区别：算法课容易关注求解过程，统计学习理论更关心学习原则本身是否可靠。它会追问：样本数量有限时，经验风险能否代表真实风险？函数集合太复杂时会不会把噪声也学进去？模型复杂度和样本数量之间应该怎样平衡？这些问题共同构成后续 VC 维、推广界、SVM 与正则化的逻辑基础。</p>
<h2>二、机器学习问题的数学提法</h2>
<p>设输入样本为 x，真实类别或输出为 y，训练集为 S = &#123;(x1,y1),(x2,y2),...,(xn,yn)&#125;。学习的目标是在候选函数集合 F 中选择一个函数 f(x)，使它不仅能解释训练样本，也能对未来未知样本预测正确。</p>
<h3>1. 损失函数</h3>
<p>损失函数 L(y,f(x)) 衡量预测结果和真实结果之间的差距。分类问题里常见的是 0-1 损失：预测正确损失为 0，预测错误损失为 1。</p>
<p><strong>扩讲：</strong>损失函数的作用，是把“模型好不好”变成可以计算的量。没有损失函数时，我们只能笼统说一个分类器“准不准”；有了损失函数，就可以比较不同模型、不同参数、不同训练策略的效果。0-1 损失最贴近分类问题的直觉：分对就是 0，分错就是 1。但在实际优化中，0-1 损失往往不容易直接最小化，所以很多算法会使用更平滑、更容易优化的替代损失。</p>
<h3>2. 真实风险</h3>
<p>真实风险 R(f)=E[L(y,f(x))] 表示模型在总体分布上的平均错误。它对应“未来无限多样本都拿来测试，模型平均会错多少”。真实分布未知，所以 R(f) 通常无法直接计算。</p>
<p><strong>为什么重要：</strong>真实风险才是机器学习真正想最小化的对象。模式识别系统最终面对的是新图像、新语音、新病人、新传感器数据，而不是训练集里已经见过的样本。真实风险小，才说明系统具有推广能力；训练集表现好，只说明它把已有样本处理得不错。</p>
<h3>3. 经验风险</h3>
<p>经验风险 R_emp(f) 是训练集上的平均损失，可以从样本直接计算。很多学习方法采用经验风险最小化原则，也就是先找训练误差最小的函数。</p>
<p><strong>为什么要区分真实风险和经验风险：</strong>真实风险是目标，但看不见；经验风险看得见，但只是目标的样本近似。学习算法经常只能先优化经验风险，再希望它能代表真实风险。第七章的核心就在这里：这种“用可计算的训练误差替代不可计算的总体误差”的做法什么时候可靠，什么时候会失败。</p>
<p><strong>课堂例子：</strong>假设我们训练一个手写数字识别器，训练集中数字 8 的样本大多写得很规整。模型如果只记住这类写法，训练误差可能很低；但遇到真实用户写得倾斜、连笔、模糊的 8 时就会错。这里训练集上的经验风险很小，但真实场景里的风险并不小。</p>
<h2>三、经验风险最小化及其问题</h2>
<p>经验风险最小化的直觉是：在训练集上错得越少，模型越好。但这个直觉只在一定条件下成立。如果模型太复杂，它可能把训练样本中的偶然噪声也记住。</p>
<h3>1. 过学习问题</h3>
<p>过学习也叫过拟合，表现为训练误差很低，甚至为 0；测试误差却很高。它提醒我们：不能只看训练误差，还要控制函数集合的复杂度。</p>
<h3>2. 一个最小例子</h3>
<p>假设用多项式曲线拟合一组数据点。低阶多项式可能欠拟合，训练误差和测试误差都大；适当阶数的多项式训练误差较小，测试误差也较小；过高阶多项式可以穿过所有训练点，但对新数据反而预测更差。</p>
<p><strong>深入扩写：</strong>过学习的本质不是模型“能力强”本身有错，而是模型能力强到足以解释训练样本中的偶然性。训练样本里可能有测量误差、标注错误、采样偏差或噪声。如果函数集合过于灵活，模型可以把这些偶然现象也拟合进去。这样训练误差会继续下降，但它学到的并不是稳定规律，而是训练集中的偶然细节。</p>
<p>从模式识别角度看，过学习很危险。比如做医学图像分类时，模型可能没有学到病灶特征，而是学到某个医院扫描仪的水印、图像分辨率或拍摄习惯；在训练集上它似乎很准，换一家医院就失效。这说明经验风险最小化如果不配合复杂度控制，就可能把“相关但无意义的线索”当成分类依据。</p>
<p><strong>常见误区：</strong>很多学生会把“训练误差为 0”理解成模型已经完美。第七章要纠正这个直觉：训练误差为 0 只是说明模型能解释已知样本，不说明它掌握了可迁移规律。真正要问的是：如果明天来了一个同分布但没见过的新样本，它还能不能判断对？</p>
<h2>四、学习过程的一致性</h2>
<p>一致性讨论的是：当训练样本数 n 越来越大时，经验风险是否能够逼近真实风险。只要求某一个固定函数满足大数定律还不够，因为学习算法会在许多候选函数中挑一个。</p>
<blockquote><p>关键问题是：整个函数集合上的经验风险，能否一致地逼近真实风险。</p></blockquote>
<p>如果函数集合太大、太复杂，有限训练集就可能不足以代表所有函数的真实表现，经验风险最小化就会失去可靠基础。</p>
<p><strong>主动扩讲：</strong>这里最容易误解的是“大数定律不是自动解决一切”。对一个固定函数来说，样本多了以后，它的经验风险通常会接近真实风险；但学习算法不是事先固定一个函数，而是在一大堆候选函数中挑选经验风险最小的那个。候选函数越多，越容易出现某个函数“碰巧”在训练集上表现特别好。</p>
<p>因此，一致性要关心的不是单个函数，而是整个函数集合。我们希望对函数集合中的所有候选函数，经验风险都能比较均匀地接近真实风险。只有这样，经验风险最小的函数才有理由接近真实风险最小的函数。否则，算法可能选中一个训练集上的“幸运函数”，它在总体上并不好。</p>
<p><strong>讲课抓手：</strong>可以把函数集合想成一群参加考试的学生。只看一次小测成绩，人数越多，越可能有人靠运气考高分；但这个高分不一定代表真实水平。如果考试题足够多、覆盖足够全面，高分才更可信。样本数量、函数集合规模和测试可靠性之间的关系，就是一致性要表达的直觉。</p>
<h2>五、函数集容量与 VC 维</h2>
<p>函数集容量描述模型集合的复杂程度。容量越大，模型能表达的分类方式越多；容量太大，越容易把训练样本“记住”；容量太小，模型又可能表达能力不足。</p>
<h3>1. 打散</h3>
<p>如果一个函数集合可以对 m 个样本点实现所有可能的二分类标记方式，就说它可以打散这 m 个点。二分类中 m 个点一共有 2^m 种可能标记方式。</p>
<h3>2. VC 维</h3>
<p>VC 维是函数集合能够打散的最大样本点数。直观地说，VC 维越大，模型越复杂；VC 维越小，模型越简单。它不是参数个数本身，但常常和模型复杂度相关。</p>
<p><strong>为什么引入 VC 维：</strong>仅仅说“模型复杂”太模糊。参数多不一定总是复杂，参数少也不一定总是简单；关键要看函数集合能把样本划分得多灵活。VC 维提供了一种刻画表达能力的方式：一个函数集合能任意打散越多样本点，它的分类能力越强，过拟合风险也越需要警惕。</p>
<p><strong>例子展开：</strong>二维平面中的线性分类器可以用一条直线分类。对某些位置的 3 个点，它可以实现所有二分类标记方式；但对一般位置的 4 个点，不可能所有标记方式都由一条直线分开。因此二维线性分类器的 VC 维为 3。这个例子说明 VC 维不是在数参数个数，而是在问“这个模型族最多能灵活到什么程度”。</p>
<p><strong>和模式识别的联系：</strong>如果特征提取得很高维，或者分类器非常复杂，函数集合容量会变大。容量大可以降低训练误差，但也提高了样本需求。第七章不是反对复杂模型，而是提醒：复杂模型需要足够数据、合适约束和可靠验证，否则表达能力会变成记忆噪声的能力。</p>
<h2>六、推广能力的界</h2>
<p>推广能力界把训练误差和测试误差联系起来。直观形式可以写成：真实风险 ≤ 经验风险 + 复杂度惩罚项。复杂度惩罚项通常和样本数、置信度、函数集容量有关。</p>
<p>这条不等式的教学重点是：要想真实风险小，需要同时做到训练误差小、模型复杂度不能太高、样本数量足够大。模型越复杂，需要的训练样本越多。</p>
<p><strong>深入解释：</strong>推广能力界的价值，不在于课堂上把每个常数都背下来，而在于理解它给出的判断结构。真实风险由两部分控制：一部分是经验风险，也就是模型在训练集上的表现；另一部分是和模型复杂度、样本数量、置信水平相关的惩罚项。训练误差再低，如果复杂度惩罚很大，真实风险上界仍然可能不理想。</p>
<p>这个思想给了机器学习实践一个非常重要的原则：不要只比较训练集准确率。两个模型训练误差相同，容量较小、结构更稳定、间隔更大的模型，往往更值得信任；一个复杂模型如果要可靠，就需要更多样本、更强正则化或更严格的验证方式。</p>
<p><strong>课堂检查：</strong>如果一个模型训练误差为 1%，另一个为 3%，是否一定选择 1% 的模型？答案是不一定。还要看模型复杂度、验证集表现、样本规模和泛化界中的复杂度项。这个问题可以帮助学生把“训练误差最低”从唯一目标降级为多个目标之一。</p>
<h2>七、支持向量机的理论分析</h2>
<p>支持向量机是统计学习理论的重要应用。SVM 不是单纯让训练误差最小，而是寻找最大间隔分类超平面。间隔越大，分类边界对样本扰动越不敏感，模型有效容量越低，推广能力通常越好。</p>
<p>在线性可分情形下，SVM 寻找满足分类约束的超平面，并最大化分类间隔；在现实数据不可完全分开时，引入松弛变量和惩罚参数 C，形成软间隔模型。C 大时更重视训练集正确，可能过拟合；C 小时允许更多训练误差，模型更平滑。</p>
<p><strong>为什么最大间隔有用：</strong>如果分类边界离样本点很近，稍微有一点噪声或扰动，样本就可能跨过边界被分错。最大间隔分类器把边界尽量放在两类样本之间最宽的位置，相当于给未来样本留出缓冲区。这个缓冲区越大，分类规则对小扰动越稳健。</p>
<p>SVM 与统计学习理论的连接点在于：它不是只追求训练样本全部分对，而是在分对的同时控制分类面的稳定性。最大间隔可以看作一种复杂度控制方式；软间隔进一步承认现实数据可能有噪声和重叠，不强迫模型为每个异常点过度弯曲。这样 SVM 把“经验风险”和“模型容量控制”放进同一个优化目标中。</p>
<p><strong>C 参数的直觉：</strong>C 大，模型更不愿意容忍训练错误，可能为了少数异常点调整边界；C 小，模型允许更多训练错误，边界更平滑。选择 C 的过程，本质上就是在训练误差和模型复杂度之间做权衡。</p>
<h2>八、不适定问题与正则化</h2>
<p>机器学习问题常常是不适定的：解可能不存在，可能不唯一，也可能对数据扰动非常敏感。正则化的作用，是在经验风险之外加入复杂度惩罚，让问题更稳定。</p>
<p>L2 正则化限制参数过大，使模型更平滑；L1 正则化促进稀疏解，有助于特征选择。从统计学习理论看，正则化本质上是在控制函数集容量；控制容量，就是控制过拟合；控制过拟合，就是提高推广能力。</p>
<p><strong>深入扩写：</strong>不适定问题在模式识别中很常见。特征很多、样本较少时，可能有许多分类器都能把训练样本分对；但这些分类器对新样本的行为可能完全不同。正则化相当于在“分对训练样本”之外再加一个偏好：更喜欢参数小、结构简单、变化平滑或特征更稀疏的解。</p>
<p>L2 正则化通常让权重整体变小，使模型不要过度依赖某一个特征；L1 正则化会促使部分权重变为 0，从而起到特征选择作用。对于模式识别任务，这意味着模型不只是利用所有可能线索，而是倾向于保留更稳定、更有解释力的线索。</p>
<p><strong>SVM 和正则化的共同点：</strong>SVM 的最大间隔、软间隔惩罚和正则化方法都在做同一件事：不要让模型为了追求训练集漂亮而变得过度复杂。它们是统计学习理论思想落到算法层面的表现。</p>
<h2>九、本章逻辑主线</h2>
<p>可以用一条线串起来：训练误差小 -> 不一定测试误差小 -> 需要研究真实风险与经验风险的关系 -> 函数集容量决定泛化难度 -> VC 维刻画容量 -> 推广能力界说明真实风险受经验风险和复杂度共同影响 -> SVM 和正则化都是控制复杂度的具体方法。</p>
<p><strong>按课堂节奏重讲一遍：</strong>第一步，学生要明白机器学习真正要小的是真实风险，不是训练集上的错误。第二步，因为真实风险不可直接计算，所以算法用经验风险作为替代。第三步，这个替代会带来风险：函数集合太复杂时，经验风险最小化会选择训练集上的偶然赢家。第四步，为了判断这种替代什么时候可靠，我们需要一致性、函数集容量和 VC 维。第五步，推广能力界告诉我们：真实风险受训练误差、模型复杂度和样本数量共同影响。第六步，SVM、正则化等方法就是把这种理论约束变成具体算法策略。</p>
<p>这条主线能把第七章和整本《模式识别》联系起来：前面章节讲“有哪些分类器”，第七章讲“为什么这些分类器可能泛化”；后面章节讲非参数、集成、特征选择、降维和深度学习时，也会不断回到同一个问题：模型表达能力、样本数量、复杂度控制和泛化能力之间如何平衡。</p>
<h2>十、核心知识点扩讲清单</h2>
<ol>
<li><strong>真实风险：</strong>最终目标，代表模型在未知总体上的平均损失；它体现的是系统真正的使用效果。</li>
<li><strong>经验风险：</strong>可计算替代，代表训练样本上的平均损失；它有用，但不能被误认为最终目标。</li>
<li><strong>经验风险最小化：</strong>很多算法的基本原则，但只有在函数集合容量受控、样本足够代表总体时才可靠。</li>
<li><strong>一致性：</strong>说明样本增多时经验风险是否能稳定逼近真实风险；关键是对整个函数集合成立，而不是只对某个固定函数成立。</li>
<li><strong>VC 维和容量：</strong>刻画模型族能表达多少种分类方式；容量越大越灵活，也越需要样本和约束。</li>
<li><strong>推广界：</strong>把训练误差、复杂度和样本数量放进同一个判断框架，解释为什么不能只看训练误差。</li>
<li><strong>SVM 与正则化：</strong>通过最大间隔、惩罚项或参数约束控制复杂度，是统计学习理论的算法化体现。</li>
</ol>
<h2>十一、课堂总结与检查</h2>
<p>本章的核心不是某个算法，而是机器学习的理论基础：为什么不能只追求训练集表现，为什么模型复杂度必须被控制，以及为什么 SVM、正则化这些方法能帮助提高推广能力。</p>
<ol>
<li>为什么“训练误差小”不能直接推出“测试误差小”？</li>
<li>VC 维想刻画的到底是什么？</li>
<li>用一句话说明正则化和推广能力之间的关系。</li>
<li>如果一个复杂模型训练误差更低，但验证集表现更差，你会怎样用本章概念解释？</li>
<li>为什么 SVM 要追求最大间隔，而不是只找到任意一个能分开训练样本的平面？</li>
</ol>
""".strip()


def _density_estimation_lesson_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = html.escape(reference_context.chapter_title or topic)
    source_name = html.escape(reference_context.resource_name)
    return f"""
<h1>{title}：概率密度估计讲解板书</h1>
<p><strong>参考资料：</strong>本讲义依据《{source_name}》中“{title}”组织，但不直接粘贴 PDF 抽取片段。这里采用测试7那种细分章节的组织方式：先把全章拆成清楚的小教学单元，再在每个小章里补出问题背景、数学直觉、例子、误区和检查题。</p>
<h2>一、本章定位：从“已知概率”走向“估计概率”</h2>
<p>前一类贝叶斯决策问题常常默认我们已经知道先验概率 P(w_i) 和类条件概率密度 p(x|w_i)。但真实模式识别任务里，这两个量通常不会直接摆在桌面上。我们手里只有有限训练样本：某一类有多少样本、这些样本的特征分布长什么样、它们能不能代表未来样本。</p>
<p>所以第三章要解决的问题不是“有了概率密度之后怎么分类”，而是更前一步：怎样根据训练样本估计出分类器需要的概率信息。可以把本章主线写成一句话：贝叶斯决策需要概率密度，现实中概率密度未知，因此必须从样本中估计它。</p>
<p><strong>教学落点：</strong>学生先要明白本章为什么出现。没有概率密度估计，贝叶斯分类器只能停留在理论公式；有了估计方法，理论才有可能落到真实数据上。</p>
<h2>二、问题形式化：先验概率与类条件概率密度</h2>
<p>在分类问题中，先验概率 P(w_i) 表示某一类在总体中出现的可能性；类条件概率密度 p(x|w_i) 表示在类别 w_i 已知的前提下，特征 x 出现的可能性。贝叶斯分类器会把这两类信息合起来，判断一个新样本更像哪一类。</p>
<p>先验概率通常比较容易估计：如果训练样本足够代表总体，可以用各类样本数量占比来近似。例如一共有 1000 个训练样本，其中 400 个属于 A 类，就可以先把 P(A) 估成 0.4。当然，如果样本采集有偏，或者类别比例由业务背景决定，也需要结合领域知识修正。</p>
<p>真正困难的是类条件概率密度 p(x|w_i)。特征 x 可能是一维、二维，也可能是高维向量；样本数量有限时，我们只能看到分布的一部分。如何从这些有限点推断整体密度，就是本章的核心。</p>
<p><strong>易混点：</strong>p(x|w_i) 不是“看到 x 后属于第 i 类的概率”，而是“已经知道类别是 w_i 时，观察到特征 x 的密度”。真正的后验概率 P(w_i|x) 要把类条件密度和先验概率合起来再归一化。很多学生会把这两个方向看反，本章开头必须先把条件概率的方向讲清楚。</p>
<h2>三、两步贝叶斯决策：先估计，再判别</h2>
<p>这一章可以用“两步贝叶斯决策”来串起来。第一步是估计：利用训练样本估计 P(w_i) 和 p(x|w_i)。第二步是判别：把估计出来的概率代回贝叶斯判别规则，选择后验概率最大或风险最小的类别。</p>
<p>这和第二章理想状态下的贝叶斯分类器不同。第二章更像在问：如果真实分布已经知道，最优决策规则是什么？第三章则回到现实：真实分布不知道，只能用样本估计。估计误差会进入后续分类结果，所以分类器性能不只取决于判别规则，也取决于估计质量。</p>
<p><strong>课堂例子：</strong>做语音识别时，同一个音素在不同人、不同设备、不同噪声环境下的特征分布不完全一样。我们不能凭空写出它的真实密度，只能收集样本并估计分布。估计得粗糙，后面的贝叶斯判别也会跟着不稳定。</p>
<h2>四、参数估计路线：先假设形状，再估参数</h2>
<p>参数估计的思路是：先假设概率密度函数属于某个参数化族，例如高斯分布 p(x|θ)，其中 θ 可能包含均值、方差或协方差矩阵；再根据训练样本估计这些未知参数。这样做的好处是问题被压缩了：不用估计整条复杂曲线，只要估计少数参数。</p>
<p>例如一维特征如果近似服从高斯分布，我们只需要估计均值 μ 和方差 σ²。均值决定分布中心，方差决定分布宽窄。样本集中在某个位置附近，估计出的均值就会靠近那里；样本分散得越开，估计出的方差就越大。</p>
<p><strong>边界条件：</strong>参数估计依赖模型假设。如果真实分布很不像高斯分布，却硬套高斯模型，计算会很整齐，但估计结果可能系统性偏差。因此讲参数估计时要同时讲两个问题：估计方法怎么做，以及分布假设是否合理。</p>
<p>高维模式识别任务里，θ 可能不只是一个数，而是一组参数。例如多维高斯分布要估计均值向量和协方差矩阵。均值向量描述样本云的中心，协方差矩阵描述各个特征怎样一起变化。协方差估不好，分类器就可能误判哪些方向上差异重要、哪些方向只是噪声。</p>
<h2>五、最大似然估计：让样本出现得“最合理”</h2>
<p>最大似然估计的直觉是：既然这些训练样本已经真实出现了，那就选择一个参数 θ，使得在这个参数下，观察到这批样本的可能性最大。对独立同分布样本 x_1, x_2, ..., x_N，似然函数通常写成 L(θ)=∏ p(x_k|θ)。最大似然估计就是寻找让 L(θ) 最大的 θ。</p>
<p>课堂上可以把它讲成“倒过来问问题”。普通概率问题是：给定参数，某个样本出现的概率是多少？最大似然估计反过来问：样本已经出现了，哪个参数最能解释它们？这个反向视角很重要，因为训练数据固定，未知的是分布参数。</p>
<p><strong>最小例子：</strong>如果我们假设某类样本的一维特征服从高斯分布，并且方差已知，那么让观测样本最可能出现的均值，通常就是样本平均值。这个结果符合直觉：一堆点集中在哪里，分布中心就应该估到哪里。</p>
<p><strong>常见误区：</strong>最大似然不是让每个单独样本概率都最大，而是让整批样本的联合出现可能性最大。它也不是保证估计结果一定等于真实参数；样本太少、模型假设不合适或数据有偏时，最大似然估计仍然可能偏离真实分布。</p>
<p><strong>课堂推导节奏：</strong>先写出样本独立时的联合概率，再说明连乘容易计算困难，所以常取对数似然，把乘法变成加法。对数不会改变最大值位置，却能让求导和优化更清楚。学生不必第一遍背完整推导，但要知道“似然函数 -> 对数似然 -> 求最大点”这条计算路线。</p>
<h2>六、贝叶斯估计：把先验知识也放进估计</h2>
<p>贝叶斯估计和最大似然估计的区别在于，它不把参数 θ 当成一个固定但未知的常数，而是把 θ 也看成随机变量。我们先用先验分布表达对参数的已有认识，再用训练样本更新这个认识，得到后验分布。</p>
<p>这种方法在样本较少时很有价值。最大似然估计完全听数据的，如果样本少且偶然性强，估计结果可能波动很大；贝叶斯估计会用先验知识提供一点稳定性。比如医学识别中，如果历史经验表明某类指标通常集中在某个范围，新样本很少时就不应该完全被少数异常点带偏。</p>
<p><strong>讲解对比：</strong>最大似然估计像是只问“这批样本最支持哪个参数”；贝叶斯估计还会问“在看到样本之前，我们对参数有哪些合理预期”。前者更简洁，后者更能处理小样本和先验知识。</p>
<p>从结果形式看，贝叶斯估计也可以有不同落点：有时我们取后验分布的均值作为估计，有时取后验概率最大的参数，也就是 MAP 估计。MAP 和最大似然很像，但多了先验项；当先验比较平坦、样本很多时，两者可能接近；当样本很少或先验很强时，两者差别会明显。</p>
<h2>七、非参数估计：不强行假设密度形状</h2>
<p>参数估计需要先选一个分布族，但很多真实数据未必服从简单分布。非参数估计的思路是：不预先规定密度必须是高斯或某个固定形式，而是让样本自己决定密度的大致形状。常见路线包括直方图估计、Parzen 窗估计和 k 近邻密度估计。</p>
<p>直方图估计容易理解：把特征空间切成小格子，数每个格子里有多少样本，再用频率近似密度。窗口方法则像拿一个小窗口在空间中滑动，看目标点附近聚集了多少样本。k 近邻方法反过来固定邻居数量，看为了包含 k 个样本需要多大体积。</p>
<p><strong>权衡：</strong>窗口太宽，估计会过度平滑，细节被抹掉；窗口太窄，估计会对样本噪声过于敏感。非参数方法灵活，但样本需求更高，维度升高后尤其容易遇到“样本看起来到处都很稀疏”的问题。</p>
<p>可以用地图热力图来类比非参数估计：每个样本像地图上的一个点，密度估计就是根据点的聚集程度画出“哪里更热”。窗口宽度像热力图的模糊半径，半径太大，城市中心和郊区被抹平；半径太小，每个点都变成孤立尖峰，看不出稳定趋势。</p>
<h2>八、估计质量：样本数、偏差方差与维数影响</h2>
<p>概率密度估计最终要关心质量：样本增多时，估计能不能越来越接近真实密度？如果估计量随着样本数增加能收敛到真实值，我们会说它具有一致性。这个思想和统计学习理论里的泛化问题相通：有限样本看到的是局部证据，我们希望样本足够多时能逼近总体规律。</p>
<p>估计误差通常可以从偏差和方差两方面理解。偏差大，说明模型假设或估计方式系统性偏离真实分布；方差大，说明换一批样本估计结果就波动很大。参数方法往往偏差风险更明显，非参数方法往往方差和样本量压力更明显。</p>
<p>高维特征会让问题更难。维度越高，同样数量的样本在空间里越稀疏，局部邻域里可用样本越少。模式识别中常说要做特征选择或降维，不只是为了计算快，也是为了让密度估计和分类判断更可靠。</p>
<p><strong>判断标准：</strong>看一个估计方法好不好，不能只看它在训练样本附近画得是否漂亮，还要看换一批样本是否稳定、样本增加时是否改善、用于分类时是否降低错误率。概率密度估计是服务于统计决策的，所以最终仍要回到“能否帮助新样本判断得更准”。</p>
<h2>九、从估计回到分类器：估计误差会传递</h2>
<p>估计概率密度不是终点，分类才是模式识别里的使用场景。得到 P(w_i) 和 p(x|w_i) 的估计后，我们会计算后验概率或判别函数，再决定新样本属于哪一类。这里要强调：估计误差会直接影响分类边界。</p>
<p>如果两个类别的密度估计都比较准确，贝叶斯判别边界会比较可靠；如果某一类样本太少、密度估得太窄或太宽，边界就会偏向另一类。对实际系统来说，分类错误有时不是判别公式错了，而是输入给公式的概率估计不稳。</p>
<p><strong>最小分类例子：</strong>假设一维特征 x 表示某个测量值，A 类和 B 类都近似高斯分布。A 类均值较小，B 类均值较大。我们先用各自训练样本估计均值和方差，再比较 p(x|A)P(A) 与 p(x|B)P(B)。新样本落在哪个分布更可能，就判给哪一类。这个例子能把估计和判别连成一条完整链路。</p>
<p>实际建模时，这条链路还会多几个检查环节：训练集用来估计密度，验证集用来比较模型假设和参数选择，测试集用来评估最终分类性能。如果只在训练集上看密度曲线，容易误以为估计很准；只有放到新样本分类上，才能发现估计误差是否真正影响决策。</p>
<h2>十、逻辑主线、误区与课堂检查</h2>
<p>本章可以用一条线串起来：贝叶斯决策需要先验概率和类条件概率密度 -> 现实中这些概率未知 -> 用训练样本估计它们 -> 参数估计先假设分布族再估参数 -> 最大似然选择最能解释样本的参数 -> 贝叶斯估计把先验知识纳入更新 -> 非参数估计让样本决定密度形状 -> 估计质量受样本数、维数和模型假设影响 -> 最终估计结果会进入分类器并影响边界。</p>
<ol>
<li><strong>误区一：</strong>把最大似然估计理解成“让每个样本概率都最大”。正确理解是让整批样本在模型下整体最可能。</li>
<li><strong>误区二：</strong>以为样本越多就一定没问题。样本多有帮助，但如果采样有偏、分布假设错或维度过高，估计仍然可能不可靠。</li>
<li><strong>误区三：</strong>把密度估计和分类割裂。密度估计的好坏会进入后验概率和判别边界，最后影响分类结果。</li>
</ol>
<p><strong>检查问题：</strong>为什么第三章不能只讲贝叶斯判别公式？最大似然估计在“反向问问题”时到底反过来了什么？参数估计和非参数估计各自的优势与风险是什么？如果一个类别样本很少，它的密度估计会怎样影响分类边界？</p>
""".strip()


def _reference_lesson_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = reference_context.chapter_title or topic
    if _is_statistical_learning_reference(reference_context):
        return _statistical_learning_lesson_html(topic, reference_context)
    if _is_density_estimation_reference(reference_context):
        return _density_estimation_lesson_html(topic, reference_context)
    if _is_humanities_reference(reference_context):
        return _humanities_reference_lesson_html(topic, reference_context)

    passages = _reference_passages(reference_context)
    lead = _compact_reference_text(reference_context.summary, limit=520)
    outline = passages[0] if passages else lead
    detail_passages = [passage for passage in passages if passage != outline][:4]
    detail_html = "\n".join(f"<p>{html.escape(passage)}</p>" for passage in detail_passages)
    points = _reference_key_points(reference_context)
    point_items = "\n".join(f"<li>{html.escape(point)}</li>" for point in points)
    if not detail_html:
        detail_html = f"<p>{html.escape(outline)}</p>"
    if not point_items:
        point_items = f"<li>{html.escape(outline)}</li>"
    is_pattern_reference = _is_pattern_recognition_reference(reference_context)
    problem_paragraph = (
        "模式识别第一章先建立三个问题：研究对象是什么、特征如何表示对象、系统怎样从数据走到分类或聚类。先把这一章当成“全书入口”来讲：它不急着推公式，而是先告诉学生这门课研究什么对象、为什么需要特征、什么时候做分类、什么时候做聚类，以及一个完整识别系统通常如何流动。"
        if is_pattern_reference
        else "先把这一章当成整份资料的入口来讲：它通常不急着展开全部技术细节，而是先交代研究对象、关键概念、典型问题和后续章节会反复使用的分析路线。"
    )
    teaching_version = (
        """
<p>讲课时不要从目录机械念起。可以先用一个生活识别例子打开：人看到照片、心电图或消费记录时，并不是记住每一个具体样本，而是在抓一类对象的稳定特征。然后再把这个直觉翻译成机器学习语言：样本、特征、类别、分类器和聚类。</p>
<p>如果学生只记住“模式识别就是分类”，还不够。要继续追问：分类依据是什么？特征从哪里来？有标签样本和没有标签样本时，学习方式有什么不同？最终系统怎样从原始数据走到决策或解释？</p>
""".strip()
        if is_pattern_reference
        else """
<p>讲课时不要从目录机械念起。可以先用一个最小场景说明这一章为什么存在，再把资料中的关键术语翻译成自己的话，最后用一个检查问题确认学生不是只记住标题。</p>
<p>如果学生能说出这一章的核心问题、两个关键概念之间的关系、以及一个适用或不适用的场景，就说明已经抓住了第一遍学习该章需要的主线。</p>
""".strip()
    )
    example_block = (
        """
<h2>五、一个最小例子</h2>
<p>可以拿“识别一张照片是不是江南水乡”做最小例子：原始数据是一张图片；可观察线索包括小河、房屋、游船、建筑风格；这些线索组合成特征；系统根据特征判断它属于哪个类别。这个例子能把“模式”“特征”“分类”三件事放在同一条线上。</p>
""".strip()
        if is_pattern_reference
        else """
<h2>五、一个最小例子</h2>
<p>把本章内容压成一个最小场景：已知一个对象、两三个关键条件和一个需要判断的问题。先说明这些条件为什么重要，再指出本章概念如何把条件连接到结论，最后补一个容易误用的反例。</p>
""".strip()
    )
    check_items = (
        """
<li>用一句话说明“模式”和“单个样本”有什么区别。</li>
<li>监督模式识别和非监督模式识别的根本差别是什么？</li>
<li>一个典型模式识别系统从原始数据到结果，大致经过哪些环节？</li>
""".strip()
        if is_pattern_reference
        else f"""
<li>用一句话说明《{html.escape(title)}》到底在解决什么问题。</li>
<li>说出本章一个关键词，并解释它在例子里承担什么作用。</li>
<li>举一个本章概念适用的场景，再举一个容易误用的场景。</li>
""".strip()
    )
    summary_paragraph = (
        "这一章的价值，是把后面所有算法先放进同一张地图：现实对象先被观测成数据，数据再被整理成特征，特征进入分类或聚类方法，最后得到可解释的识别结果。后续章节讲的贝叶斯决策、线性分类器、神经网络、支持向量机和深度学习，本质上都在这张地图里的某个环节上变得更强。"
        if is_pattern_reference
        else f"这一章的价值，是先给后续学习搭一张地图：知道《{html.escape(title)}》在讲什么、为什么重要、关键概念怎样连接，以及接下来读正文时应该盯住哪些判断条件。"
    )

    return f"""
<h1>{html.escape(title)}</h1>
<p><strong>本节主线：</strong>{html.escape(lead)}</p>
<h2>一、这一章先解决什么问题</h2>
<p>{html.escape(outline)}</p>
<p>{html.escape(problem_paragraph)}</p>
<p>讲义不把这一章压成几个标签，而是先拆成可连续讲授的小单元。每个小单元都回答一个明确问题：它为什么出现、解决什么困难、和前后章节怎样连接，以及学生最容易在哪里误解。</p>
<h2>二、关键概念与讲解顺序</h2>
<ol>
{point_items}
</ol>
<p>这些关键概念不适合当成词表背诵。讲解时要按“问题 -> 概念 -> 关系 -> 例子 -> 检查”的顺序推进，让学生知道每个概念在本章中承担的角色，而不是只记住章节标题。</p>
<h2>三、关键内容讲解</h2>
{detail_html}
<p>上面的资料线索需要被改写成课堂语言：先删掉页码、断裂公式和 OCR 噪声，再保留真正有教学价值的对象、条件、关系和结论。学生听到的应该是一条清楚的知识链，而不是原文碎片的拼接。</p>
<h2>四、课堂讲解版本</h2>
{teaching_version}
{example_block}
<h2>六、章节之间的关系</h2>
<p>这一章不能孤立讲。开头要说明它接住了前面哪一个问题，中段要说明每个概念怎样往下推进，结尾要说明它为后续章节留下了什么工具。这样学生看到的是一张路线图，而不是七八个互不相干的小标题。</p>
<p>如果是技术材料，可以用“问题形式化 -> 方法提出 -> 例子演示 -> 边界条件 -> 应用回路”来组织；如果是文科材料，可以用“背景 -> 观点或事件 -> 证据 -> 影响 -> 评价”来组织。无论哪种材料，小节划分都要服务于理解路径。</p>
<h2>七、常见误区</h2>
<ol>
<li>只记章节标题，不知道标题下面真正要解决的问题。</li>
<li>把资料片段当成板书正文，导致讲义像摘录而不像老师整理过的讲解。</li>
<li>只给一个例子，没有指出例子成立的条件和不适用的边界。</li>
</ol>
<h2>八、检查问题</h2>
<ol>
{check_items}
</ol>
<h2>九、课堂推进节奏</h2>
<p>第一遍讲主线，不急着展开所有细节；第二遍讲关键概念之间的关系；第三遍用例子检查学生是否能迁移。每讲完一个小节，都用一个很短的问题确认学生是否知道“这一节解决了什么”。</p>
<p>如果学生卡住，不要直接换话题，而是回到本节的最小问题：已知什么、要判断什么、哪个概念能连接条件和结论。这样能把抽象内容重新落到可操作的理解上。</p>
<h2>十、小结</h2>
<p>{summary_paragraph}</p>
<p>学完这一章，学生至少要能完成三件事：用一句话讲出全章主线，说清两个核心概念之间的关系，并用一个例子说明这个知识点怎样进入真实判断或分析任务。</p>
    """.strip()


def _is_ring_algebra_geometry_topic(topic: str) -> bool:
    normalized = topic.lower()
    strong_terms = (
        "抽象代数",
        "交换代数",
        "代数几何",
        "环论",
        "理想",
        "商环",
        "整环",
        "素理想",
        "极大理想",
        "根理想",
        "坐标环",
        "局部化",
        "noether",
        "spec",
        "zariski",
        "hilbert",
        "仿射概形",
    )
    return any(term in normalized for term in strong_terms)


def _ring_algebra_geometry_html() -> str:
    sections = [
        (
            "第0章 为什么数学系学生必须认真学习“环”",
            [
                "环是把数、函数、多项式、矩阵和几何对象统一起来的代数结构。对数学系学生来说，环不是一个孤立定义，而是一条主线：从整数的可除性，到多项式方程，再到代数簇和概形，很多问题都在问“某个环里哪些元素、理想或同态控制了对象的性质”。",
                "学习时要先建立三个来源：整数环 Z 给出整除和同余，多项式环 k[x1,...,xn] 给出方程和函数，坐标环 k[x1,...,xn]/I 给出几何对象上的函数。这样看，环既是运算系统，也是记录几何信息的语言。",
                "检查点：你能说出为什么代数几何会把几何对象翻译成一个环吗？",
            ],
        ),
        (
            "第1章 环的定义与第一批例子",
            [
                "一个环 R 通常包含加法和乘法。加法让 R 成为交换群，乘法满足结合律，并且乘法对加法满足左右分配律。若乘法交换，称为交换环；若存在乘法单位元 1，称为含幺环。后续代数几何默认主要讨论含幺交换环。",
                "核心例子包括 Z、Q、R、C、多项式环 k[x]、多元多项式环 k[x1,...,xn]、商环 Z/nZ、矩阵环 Mn(k)。非例子同样重要：只给集合没有封闭运算、乘法不满足分配律、或把不兼容的运算硬拼在一起，都不是环。",
                "常见误区：把“环”理解成有形的圆环。数学中的环是两个运算之间稳定配合的结构。",
            ],
        ),
        (
            "第2章 环同态、核与第一同构定理",
            [
                "环同态 f:R -> S 是保持加法、乘法，并在含幺语境中保持 1 的映射。它不是普通函数，而是尊重两套运算结构的翻译器。",
                "核 ker f={r in R | f(r)=0} 记录哪些元素在翻译后消失。像 im f 记录 R 在 S 中真正抵达的部分。核之所以重要，是因为它天然是理想，可以用来构造商环。",
                "第一同构定理说 R/ker f 与 im f 同构。直觉是：既然核里的元素都会被 f 看成 0，就把它们全部压成 0，剩下的结构正好就是像。",
            ],
        ),
        (
            "第3章 理想：为什么它替代子环成为商结构的核心",
            [
                "理想 I 是环 R 的加法子群，并且满足对任意 r in R、a in I，都有 ra in I 和 ar in I。交换环里只需写 r a in I。这个吸收性质保证我们能在 R/I 中定义良好的乘法。",
                "在 Z 中，所有理想都是 nZ；在 k[x] 中，单个多项式 f 生成理想 (f)；在 k[x,y] 中，(x,y) 表示所有没有常数项的多项式。多元素生成理想 (f1,...,fm) 由这些生成元的环系数组合构成。",
                "直觉：理想是一组准备被视为 0 的关系。把 I 中元素压成 0，就得到商环 R/I。",
            ],
        ),
        (
            "第4章 商环：把关系加入一个环",
            [
                "商环 R/I 的元素是陪集 r+I。两个元素 r,s 在商环中相等，当且仅当 r-s in I。也就是说，I 中的元素都被系统性地当作 0。",
                "例子 Z/nZ 是把 n 当作 0，因此整数按模 n 同余分类。例子 k[x]/(x^2) 中有一个非零元素 x，但 x^2=0，这会产生幂零元。",
                "商环的学习重点不是记号，而是理解“加入关系”。例如 k[x,y]/(y-x^2) 表示在多项式函数中强制 y=x^2。",
            ],
        ),
        (
            "第5章 零因子、幂零元、整环与域",
            [
                "若非零 a,b 满足 ab=0，则 a,b 是零因子。若 a^n=0 且 a 不一定为 0，则 a 是幂零元。零因子说明乘法会丢信息，幂零元说明某些无限小或厚化信息存在。",
                "整环是非零含幺交换环且没有零因子。域是每个非零元素都有乘法逆元的交换环。域一定是整环，但整环不一定是域，例如 Z 和 k[x]。",
                "代数几何中的对应直觉：整环常对应不可约对象；幂零元常对应非约化结构，记录普通点集看不见的厚度。",
            ],
        ),
        (
            "第6章 素理想与极大理想",
            [
                "理想 p 是素理想，如果 ab in p 推出 a in p 或 b in p。等价地，R/p 是整环。理想 m 是极大理想，如果 R/m 是域。",
                "在 Z 中，(p) 对素数 p 是素理想也是极大理想；(0) 在 Z 中是素理想但不是极大理想。在 k[x] 中，不可约多项式生成的理想是极大理想。",
                "素理想像不可再分的几何成分，极大理想在代数闭域上的多项式环中常对应具体点。",
            ],
        ),
        (
            "第7章 根理想与 Hilbert 零点定理",
            [
                "根理想 rad(I)={f in R | 存在 n>=1 使 f^n in I}。它把所有“幂次上已经由 I 控制”的函数也纳入进来。",
                "弱形式的 Hilbert 零点定理说明，在代数闭域 k 上，k[x1,...,xn] 的极大理想对应点 (a1,...,an)，形式为 (x1-a1,...,xn-an)。",
                "强形式把几何零点集和根理想联系起来：I(V(I))=rad(I)。这解释了为什么普通点集只能看见根理想，不能看见幂零厚度。",
            ],
        ),
        (
            "第8章 坐标环与仿射代数集",
            [
                "给定多项式集合 S，零点集 V(S) 是所有同时满足 f=0 的点。若 I 是这些方程生成的理想，仿射代数集 V(I) 的坐标环定义为 k[x1,...,xn]/I(V)，常写 k[V]。",
                "坐标环可以理解为代数集上的多项式函数环：两个多项式若在 V 上取值总相同，就在坐标环中被认为相等。",
                "例子：抛物线 V(y-x^2) 的坐标环 k[x,y]/(y-x^2) 与 k[x] 同构，因为 y 可以被 x^2 代替。",
            ],
        ),
        (
            "第9章 不可约性与整环",
            [
                "代数集 V 不可约，直观上表示它不能写成两个真闭子集的并。代数对应是：在合适条件下，V 不可约当且仅当坐标环 k[V] 是整环。",
                "原因在于如果坐标环有零因子 fg=0，几何上就暗示 V 被 f=0 和 g=0 两部分覆盖；反过来几何分裂也会制造零因子。",
                "例子 V(xy) 是两条坐标轴的并，它的坐标环 k[x,y]/(xy) 有零因子 x 和 y，因此不是整环。",
            ],
        ),
        (
            "第10章 局部化：只观察某个区域或某个点附近",
            [
                "局部化 S^{-1}R 是把乘法闭集 S 中的元素强制变成可逆。元素写成 r/s，像分数一样运算。它的目的不是制造复杂记号，而是允许我们只关注某些元素不为零的区域。",
                "若 p 是素理想，R_p 表示在 p 外所有元素都变成可逆。它是研究 p 附近局部性质的基本工具。",
                "几何直觉：D(f) 是 f 不为零的开集，R_f 描述这个开集上的函数。局部化是代数版的“放大某个区域”。",
            ],
        ),
        (
            "第11章 Noether 环：有限生成条件让理论可控",
            [
                "Noether 环是每个理想都有限生成的环。等价地，任意升链 I1 subset I2 subset ... 最终稳定。",
                "Hilbert 基定理说：若 R 是 Noether 环，则 R[x] 也是 Noether 环。因此域上的多项式环 k[x1,...,xn] 是 Noether 的。",
                "这条性质保证代数几何中的方程系统可以用有限多个方程控制，避免无限生成的病态对象把理论拖垮。",
            ],
        ),
        (
            "第12章 Spec：从环制造几何空间",
            [
                "Spec R 是 R 的所有素理想构成的集合。它把环本身转化为一个几何空间的点集，其中点不只包括极大理想对应的经典点，也包括素理想对应的一般点。",
                "在 Spec Z 中，点包括 (0) 和每个素数 p 对应的 (p)。这说明整数环也可以被看成一条带有算术信息的几何对象。",
                "学习 Spec 的关键是接受：点可以是素理想，函数来自环元素，闭集由理想定义。",
            ],
        ),
        (
            "第13章 Zariski 拓扑",
            [
                "在 Spec R 上，闭集定义为 V(I)={p in Spec R | I subset p}。也就是说，闭集由理想决定，而不是由距离或度量决定。",
                "基本开集 D(f)={p | f notin p}。这些 D(f) 构成拓扑基，也与局部化 R_f 紧密相连。",
                "Zariski 拓扑通常很粗，但正因为闭集由方程控制，它非常适合表达代数方程的几何结构。",
            ],
        ),
        (
            "第14章 环同态与几何映射的反向关系",
            [
                "环同态 phi:R -> S 会诱导连续映射 Spec S -> Spec R，方向反过来。一个素理想 q in Spec S 被送到 phi^{-1}(q) in Spec R。",
                "这解释了代数几何中的反变性：几何空间之间的映射，对应到函数环时方向反转。几何上从 X 到 Y 的映射，会把 Y 上的函数拉回到 X 上。",
                "例子：包含 k[x] -> k[x,y] 对应投影 A2 -> A1，因为一元函数可以拉回成二元函数。",
            ],
        ),
        (
            "第15章 从仿射代数集到仿射概形",
            [
                "经典仿射代数集主要看代数闭域上的点。仿射概形 Spec R 则把所有素理想都作为点，并保留幂零元、局部环和层结构。",
                "这样做的好处是：非约化结构、算术环、族和退化都能统一放在同一语言里。k[x]/(x^2) 的 Spec 只有一个底层点，但结构层记录了厚化信息。",
                "入门阶段先把概形理解成“带有函数环和局部信息的增强几何空间”，不要急着把层论细节一次吃完。",
            ],
        ),
        (
            "第16章 几个核心例子完整贯通",
            [
                "例子一：Z -> Z/nZ 展示商环和同余。例子二：k[x,y]/(xy) 展示零因子与可约几何。例子三：k[x,y]/(y-x^2) 展示坐标环如何把曲线方程变成函数关系。",
                "例子四：R_p 展示局部化如何关注一个素理想附近。例子五：k[x]/(x^2) 展示幂零元和非约化结构。",
                "学习策略：每个例子都从“环是什么、理想是什么、商掉了什么、几何对象是什么、有哪些点或素理想”五个问题检查。",
            ],
        ),
        (
            "第17章 常用概念关系表",
            [
                "理想 I 对应要被视为 0 的关系；商环 R/I 对应加入这些关系后的新函数系统；素理想 p 对应不可约方向或一般点；极大理想 m 在经典情形下对应普通点。",
                "整环对应没有零因子的函数环，几何上常意味着不可约；域对应只剩一个经典点的函数取值世界；根理想对应普通点集能检测到的方程。",
                "局部化对应限制到开集或点附近；Noether 条件对应有限可控；Spec 对应把环整体几何化。",
            ],
        ),
        (
            "第18章 最容易混淆的地方",
            [
                "第一，子环和理想不同：子环要求内部能运算，理想还要求被整个环吸收。只有理想才能稳定地做商环。",
                "第二，素理想和极大理想不同：极大理想给域，素理想给整环。极大理想一定素，但素理想不一定极大。",
                "第三，点集和概形不同：点集看不到幂零厚度，而概形能通过结构环记录这些信息。",
            ],
        ),
        (
            "第19章 推荐学习路线",
            [
                "第一轮：掌握环、理想、商环、同态、整环、域。目标是会算基本例子。第二轮：学习素理想、极大理想、根理想、局部化、Noether 环。目标是理解代数结构如何编码几何性质。",
                "第三轮：进入坐标环、仿射代数集、Spec、Zariski 拓扑。目标是把“方程-理想-环-空间”连成一条线。",
                "第四轮：通过例子复盘，包括 k[x,y]/(xy)、k[x]/(x^2)、Spec Z、R_f。不要只读定义，要反复问每个定义解决了什么问题。",
            ],
        ),
        (
            "第20章 练习题",
            [
                "练习 1：证明环同态的核是理想。练习 2：描述 Z/12Z 中的零因子。练习 3：证明 k[x]/(x) 与 k 同构。",
                "练习 4：判断 k[x,y]/(xy) 是否为整环，并解释几何意义。练习 5：说明为什么 (x-a,y-b) 是 k[x,y] 的极大理想。",
                "练习 6：写出 D(f) 与 R_f 的关系。练习 7：解释为什么 rad((x^2))=(x)，并说出点集为什么看不见 x^2 的厚度。",
            ],
        ),
        (
            "第21章 一页总结",
            [
                "环是函数和运算的组织方式；理想是准备设为 0 的关系；商环是加入关系后的世界；素理想是构成 Spec 的点；局部化是看局部；Noether 条件保证有限可控。",
                "从抽象代数走向代数几何的主线可以压成一句话：用多项式环描述方程，用理想描述关系，用商环描述函数，用素理想组成空间，用局部化研究附近性质。",
                "如果你能把 Z/nZ、k[x,y]/(xy)、k[x,y]/(y-x^2)、k[x]/(x^2) 四个例子讲清楚，就已经抓住了“环”连接代数和几何的核心骨架。",
            ],
        ),
    ]

    parts = [
        "<h1>抽象代数、交换代数与代数几何中的“环”：系统板书讲义</h1>",
        "<p><strong>学习定位：</strong>面向已经学过群、环基本定义，并希望把环作为连接抽象代数、交换代数和代数几何主线的数学系本科生。</p>",
        "<p><strong>讲解节奏：</strong>每次只讲一个小节。讲完后先检查理解，再询问是否继续下一小节。</p>",
    ]
    for heading, paragraphs in sections:
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        for paragraph in paragraphs:
            parts.append(f"<p>{html.escape(paragraph)}</p>")
    return "\n".join(parts)


def _is_virtual_memory_topic(topic: str) -> bool:
    normalized = topic.lower()
    return any(term in normalized for term in ("虚拟内存", "virtual memory", "页表", "tlb", "缺页", "地址空间"))


def _virtual_memory_html() -> str:
    return """
<h1>虚拟内存：操作系统核心讲义</h1>
<p><strong>学习目标：</strong>理解虚拟内存如何把程序看到的地址空间和真实物理内存隔开，并能说清地址转换、页表、TLB、缺页异常和页面置换之间的关系。</p>
<h2>一、先抓主线：虚拟内存解决什么问题</h2>
<p>虚拟内存让每个进程都像独占一大片连续内存。程序使用虚拟地址，硬件和操作系统再把虚拟地址翻译到物理内存。这样可以同时获得隔离、保护、按需加载和更灵活的内存管理。</p>
<p>不要把虚拟内存理解成“假的内存”。它更像一层地址抽象：程序看到的是虚拟地址空间，真正的数据可能在物理内存中，也可能暂时在磁盘上的换页区域里。</p>
<h2>二、地址空间：每个进程自己的地图</h2>
<p>地址空间是一套进程可见的地址编号。不同进程可以使用相同的虚拟地址，但映射到不同的物理页，因此一个进程通常不能直接读写另一个进程的内存。</p>
<p>这也是内存保护的基础：只要页表中没有授权映射，访问就会被硬件拦住，并交给操作系统处理。</p>
<h2>三、分页：把内存切成固定大小的页</h2>
<p>虚拟内存通常按页管理。虚拟地址被拆成虚拟页号和页内偏移，物理内存被拆成物理页框。页大小常见为 4KB，也可能更大。</p>
<p>分页的好处是映射粒度稳定：操作系统不必移动整段连续内存，只要修改页表，就能把某个虚拟页指向某个物理页框。</p>
<h2>四、页表：虚拟页到物理页的翻译表</h2>
<p>页表记录虚拟页号到物理页框号的映射，还会带上有效位、读写权限、用户/内核权限、脏位和访问位等状态。地址转换时，MMU 根据页表项找到物理页框，再加上页内偏移得到物理地址。</p>
<p>核心公式可以先记成：虚拟地址 = 虚拟页号 + 页内偏移；物理地址 = 物理页框号 + 同一个页内偏移。</p>
<h2>五、TLB：给页表查询加速的缓存</h2>
<p>如果每次访存都去内存里查页表，代价会很高。TLB 是 MMU 里的地址转换缓存，保存最近用过的虚拟页到物理页框映射。</p>
<p>TLB 命中时，地址转换很快；TLB 未命中时，需要查页表并把结果放回 TLB。理解性能时，要同时看普通缓存命中和 TLB 命中。</p>
<h2>六、缺页异常：需要的页暂时不在内存</h2>
<p>如果页表项显示某个虚拟页当前不在物理内存中，就会触发缺页异常。CPU 转入内核，操作系统找到这个页在磁盘上的位置，选择一个空闲页框或淘汰旧页，把需要的页调入内存，然后恢复进程执行。</p>
<p>缺页不是普通函数调用，而是一次硬件异常加操作系统处理。它能让程序按需加载数据，但频繁缺页会让程序非常慢。</p>
<h2>七、页面置换：内存满了怎么办</h2>
<p>当没有空闲页框时，操作系统需要选择一个页面换出。常见策略包括 FIFO、LRU 近似、Clock 算法等。好的策略尽量保留近期还会使用的页，减少未来缺页。</p>
<p>如果工作集大于物理内存，系统可能反复换入换出，出现抖动。此时 CPU 看似忙着处理异常，真正业务进展很慢。</p>
<h2>八、一个完整例子</h2>
<p>程序访问虚拟地址 0x12345。硬件先拆出虚拟页号和偏移，查 TLB；如果 TLB 命中，就直接得到物理页框。如果未命中，就查页表；如果页表有效，就更新 TLB 再访问物理内存；如果页表无效，就触发缺页异常，由操作系统把页调入后重试。</p>
<h2>九、练习</h2>
<ol>
<li>为什么两个进程可以使用相同虚拟地址却互不干扰？</li>
<li>TLB 命中和页表命中有什么区别？</li>
<li>缺页异常发生时，操作系统大致做哪几步？</li>
<li>为什么工作集太大会导致抖动？</li>
</ol>
<h2>十、小结</h2>
<p>虚拟内存的主线是：地址空间提供抽象，页表提供映射，TLB 提供加速，缺页异常提供按需调入，页面置换处理内存不足。把这五件事连起来，就能理解大多数虚拟内存题目。</p>
""".strip()


def _generic_lesson_html(topic: str, reference_context: ResourceReferenceContext | None = None) -> str:
    if reference_context is not None:
        return _reference_lesson_html(topic, reference_context)

    if _is_ring_algebra_geometry_topic(topic):
        return _ring_algebra_geometry_html()

    if any(keyword in topic.lower() for keyword in ["勾股", "pythagorean", "triangle", "直角三角形"]):
        return f"""
<h1>{topic}</h1>
<p><strong>学习目标：</strong>理解直角三角形三边关系，知道公式何时能用，并能完成基础例题。</p>
<h2>一、直观定义</h2>
<p>在直角三角形中，两条直角边的平方和，等于斜边的平方。通常写作：a² + b² = c²，其中 c 是斜边。</p>
<h2>二、为什么成立</h2>
<p>可以把三条边看成三个正方形的边长。两条直角边上正方形面积加起来，刚好等于斜边上正方形的面积。这就是公式背后的面积直觉。</p>
<h2>三、例题</h2>
<p>若直角三角形两条直角边分别为 3 和 4，则斜边 c 满足 3² + 4² = c²，所以 c² = 25，c = 5。</p>
<h2>四、常见误区</h2>
<p>这个公式只能直接用于直角三角形。若题目没有给出直角，需要先证明或判断它是不是直角三角形。</p>
<h2>五、练习</h2>
<p>1. 两直角边为 5 和 12，斜边是多少？答案：13。</p>
<p>2. 斜边为 10，一条直角边为 6，另一条直角边是多少？答案：8。</p>
""".strip()

    safe_topic = html.escape(topic)
    return f"""
<h1>{safe_topic}：系统讲义</h1>
<p><strong>学习目标：</strong>围绕“{safe_topic}”建立一份可连续阅读、可分节讲解、可继续扩写的 Word 式讲义。先让学习者知道这一主题解决什么问题，再把关键概念、例子、误区和练习连成一条线。</p>
<h2>一、问题入口：为什么现在要学它</h2>
<p>学习“{safe_topic}”不能只从名词开始。第一步要说明它在当前学科里解决的核心问题：它帮助我们理解什么现象、解释什么材料、完成什么推理，或者处理哪一类题目与任务。</p>
<p>如果学习者是为了考试，就要把它放进常见题型；如果是为了论文、项目或课堂展示，就要把它放进论证、案例或应用场景。</p>
<h2>二、已有基础与预备知识</h2>
<p>学习前先检查三件事：已经知道哪些基础概念，哪些符号或背景还不熟，当前目标是入门理解、考试解题、阅读材料，还是能对外讲清楚。</p>
<p>这一步决定讲解深度。基础薄弱时先补直觉和例子；已经有基础时就进入概念边界、推理链条和容易混淆的比较。</p>
<h2>三、核心概念：先给清楚定义</h2>
<p>把“{safe_topic}”拆成定义、对象、条件、结论四层。定义回答“它是什么”，对象回答“它处理谁”，条件回答“什么时候能用”，结论回答“能推出什么”。</p>
<p>讲概念时不要只背一句话，而要说明这个概念为什么被提出，以及如果少了某个条件会出现什么问题。</p>
<h2>四、关键机制或推理链</h2>
<p>第二层要讲“它怎样工作”。对于理科内容，通常要把公式、变量、步骤和边界条件连起来；对于文科内容，则要把背景、证据、观点和影响连起来。</p>
<p>学习者至少要能复述一条主线：从问题出发，经过哪些概念或材料，最后怎样得到判断、解释或结论。</p>
<h2>五、典型例子：从一个最小场景走通</h2>
<p>用一个足够小的例子把“{safe_topic}”跑一遍：先给已知条件或材料，再说明如何识别关键信息，最后展示怎样使用概念完成判断。</p>
<p>例子不追求复杂，而要能暴露核心结构。理科例子要有步骤，文科例子要有证据，语言类例子要有可替换表达。</p>
<h2>六、反例、边界与常见误区</h2>
<p>每个主题都要配反例或边界：哪些情况看起来相似但不能直接套用，哪些说法是常见误读，哪些条件一旦改变结论就不成立。</p>
<p>这一节的目的，是防止学习者只记住标签，却不能在新题目、新材料或真实场景中判断能不能用。</p>
<h2>七、题型、任务或应用场景</h2>
<p>把“{safe_topic}”放回学习目的中：考试中可能怎样问，论文或展示中怎样组织，项目或实验中怎样使用，口语或写作中怎样迁移。</p>
<p>如果学习者目标明确，就优先围绕那个目标展开；如果目标还不明确，就用概念理解、例题训练、材料分析和迁移应用四条线给出选择。</p>
<h2>八、练习与自我检查</h2>
<p>练习 1：用一句话说出“{safe_topic}”解决的核心问题。</p>
<p>练习 2：列出两个关键条件，并说明缺少其中一个会怎样。</p>
<p>练习 3：做一个最小例子或材料分析，把定义、步骤和结论完整写出来。</p>
<h2>九、学习路线与下一步</h2>
<p>第一遍先抓主线和基本例子；第二遍补充细节、反例和易混点；第三遍做练习或材料分析；最后尝试把它讲给别人听。</p>
<p>如果这一节能跟上，下一步就继续讲第一个核心概念；如果卡住，就回到问题入口，重新确认学习者当前水平和具体目标。</p>
""".strip()


def build_document_for_topic(
    topic: str,
    reference_context: ResourceReferenceContext | None = None,
) -> BoardDocument:
    normalized = topic.lower()
    if any(keyword in normalized for keyword in ["dialogue", "对话", "咖啡", "café", "cafe", "点餐"]):
        title = "法国咖啡厅点餐情景对话（含过去将来时）"
        return build_document(title=title, content_html=_language_cafe_html())
    if reference_context is None and _is_virtual_memory_topic(topic):
        title = "虚拟内存"
        return build_document(title=title, content_html=_virtual_memory_html())
    if reference_context is None and _is_ring_algebra_geometry_topic(topic):
        title = "抽象代数、交换代数与代数几何中的环"
        return build_document(title=title, content_html=_ring_algebra_geometry_html())
    return build_document(title=topic, content_html=_generic_lesson_html(topic, reference_context))


def build_blank_document(topic: str) -> BoardDocument:
    return build_document(title=topic, content_html="<p></p>")


def _outline_from_document(document: BoardDocument) -> list[str]:
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    headings = [line for line in lines if len(line) <= 42][:8]
    return headings or [document.title]


def build_teaching_guide(
    lesson_id: str,
    title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    mappings = [
        TeachingGuideMapping(
            block_id=f"section_{index}",
            supports_goal=requirements.learning_goal,
            teaching_mode="dialogue" if "对话" in heading else "definition",
            focus_points=[heading],
            optional_points=["根据用户追问扩写当前段落，而不是拆成卡片。"],
            difficult_points=["如果用户只问一个词或一句话，优先结合整篇讲义上下文解释。"],
            check_questions=[f"你能用自己的话复述“{heading}”的重点吗？"],
        )
        for index, heading in enumerate(_outline_from_document(document), start=1)
    ]
    return TeachingGuide(
        lesson_id=lesson_id,
        summary=f"围绕《{title}》的连续讲义进行讲解，服务于：{requirements.learning_goal}",
        structure_note="以整篇文档为课堂板书，优先维持标题、正文、对话、练习的连续阅读体验。",
        pacing="场景/定义 -> 主体讲解 -> 例句或例题 -> 练习 -> 检查理解",
        mappings=mappings,
        strategy="讲解和编辑都围绕整篇富文档快照推进，避免回到分块卡片式板书。",
    )


def build_lesson(
    topic: str,
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    commit_label: str,
    commit_message: str,
    tags: list[str],
) -> Lesson:
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, topic, document, requirements)
    commit = CommitRecord(
        label=commit_label,
        message=commit_message,
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main",
                head_commit_id=commit.id,
                base_commit_id=commit.id,
            )
        },
        commits=[commit],
        current_branch="main",
    )
    return Lesson(
        id=lesson_id,
        title=topic,
        slug=slugify(topic),
        summary=requirements.learning_goal,
        tags=tags,
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def create_lesson(
    topic: str,
    requirements: LearningRequirementSheet | None = None,
    reference_context: ResourceReferenceContext | None = None,
) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_document_for_topic(topic, reference_context)
    return build_lesson(
        document.title,
        document=document,
        requirements=requirements,
        commit_label="Initial document draft",
        commit_message=f"Generated starter rich document for {topic}",
        tags=[topic, *requirements.board_scope[:2]],
    )


def create_empty_lesson(topic: str, requirements: LearningRequirementSheet | None = None) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_blank_document(topic)
    return build_lesson(
        topic,
        document=document,
        requirements=requirements,
        commit_label="Initial blank document",
        commit_message=f"Created empty rich document for {topic}",
        tags=[topic],
    )
