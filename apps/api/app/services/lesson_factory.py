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
<p><strong>Cliente：</strong>Merci beaucoup. Je suis contente d’avoir trouvé ce café avant la pluie.<br><strong>顾客：</strong>非常感谢。我很高兴在下雨前找到了这家咖啡馆。</p>
<h2>三、语法重点：过去将来时与条件式</h2>
<p>法语里中文常说的“过去将来时”，很多时候会用 <strong>条件式现在时 conditionnel présent</strong> 来表达：站在过去某个时间点看未来将要发生的事。结构常见为：<strong>过去主句 + conditionnel présent</strong>。</p>
<p><strong>例句 1：</strong><em>Je pensais que je prendrais seulement un café.</em> 我原本以为我只会点一杯咖啡。这里 <em>pensais</em> 把视角放在过去，<em>prendrais</em> 表示从那个过去时刻看出去的未来动作。</p>
<p><strong>例句 2：</strong><em>J’avais dit à mes amis que nous reviendrions peut-être ici demain matin.</em> 我之前跟朋友说我们明天早上也许会再来这里。<em>avais dit</em> 是过去完成的说话动作，<em>reviendrions</em> 是当时说话内容里的未来。</p>
<p><strong>例句 3：</strong><em>Si mes amis venaient avec moi demain, nous commanderions sûrement plusieurs desserts.</em> 如果我的朋友明天和我一起来，我们肯定会点几份甜点。这里是条件句：<em>si + imparfait</em>，主句用 <em>conditionnel présent</em>。</p>
<h2>四、点餐表达整理</h2>
<p><strong>礼貌点餐：</strong><em>Je vais prendre...</em> 我想要……；<em>Je voudrais...</em> 我想要……；<em>Qu’est-ce que vous me conseillez ?</em> 您推荐什么？</p>
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


def _generic_lesson_html(topic: str, reference_context: ResourceReferenceContext | None = None) -> str:
    if any(keyword in topic.lower() for keyword in ["勾股", "pythagorean", "triangle", "几何"]):
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

    reference_note = ""
    reference_chunks = ""
    if reference_context is not None:
        points = "；".join(reference_context.teaching_points[:3]) or reference_context.summary
        reference_note = f"<p><strong>参考资料主线：</strong>本讲义参考《{reference_context.resource_name}》的《{reference_context.chapter_title}》，重点吸收：{points}</p>"
        if reference_context.chunks:
            chunk_html = "".join(
                f"<p><strong>参考片段 {index}：</strong>{html.escape(chunk.excerpt)}</p>"
                for index, chunk in enumerate(reference_context.chunks[:3], start=1)
                if chunk.excerpt.strip()
            )
            if chunk_html:
                reference_chunks = f"<h2>零、参考资料切入</h2>{chunk_html}"

    return f"""
<h1>{topic}</h1>
<p><strong>学习目标：</strong>围绕“{topic}”建立一份可连续阅读、可讲解、可继续扩写的 Word 式讲义。</p>
{reference_note}
{reference_chunks}
<h2>一、问题入口</h2>
<p>先回答一个最核心的问题：为什么我们现在要学习“{topic}”？它通常是为了解决某类概念理解、推理或实际应用问题。</p>
<h2>二、核心概念</h2>
<p>把“{topic}”拆成定义、条件、例子和反例四个层次。先抓住定义，再看它在哪些条件下成立，最后用例子确认理解。</p>
<h2>三、课堂讲解版本</h2>
<p>如果要讲给别人听，可以先用一句话给出主线，再用一个小例子说明它如何工作。讲解时避免一次堆太多术语，而是让每个术语都服务于当前问题。</p>
<h2>四、最小例题</h2>
<p>请补入一个最小例子：它应该只包含本节最关键的变量、条件和结论。完成后，再解释每一步为什么成立。</p>
<h2>五、练习与检查</h2>
<p>1. 用自己的话复述本节核心概念。</p>
<p>2. 写出一个例子和一个反例。</p>
<p>3. 说明它和你已经学过的一个知识点有什么关系。</p>
""".strip()


def build_document_for_topic(
    topic: str,
    reference_context: ResourceReferenceContext | None = None,
) -> BoardDocument:
    normalized = topic.lower()
    if any(keyword in normalized for keyword in ["法语", "french", "dialogue", "对话", "咖啡", "café", "cafe", "点餐"]):
        title = "法国咖啡厅点餐情景对话（含过去将来时）"
        return build_document(title=title, content_html=_language_cafe_html())
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
