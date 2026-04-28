from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

from app.models import BoardDocument
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import build_document, html_to_text


CHART_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("看趋势", "折线图"),
    ("比较大小", "柱状图 / 条形图"),
    ("看占比", "饼图 / 环形图"),
    ("看分布", "直方图 / 箱线图"),
    ("看两个变量关系", "散点图"),
    ("看三个变量关系", "气泡图"),
    ("看整体随时间变化", "面积图"),
    ("看多个维度能力", "雷达图"),
    ("看对象之间关系", "网络图"),
    ("看地理位置数据", "地图"),
    ("看总量和增长率", "组合图"),
)

_BLOCK_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|li|tr|blockquote)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?\s*(?:%|％|万人|亿元|万元|元|万|千|百|人|次|件|分|小时|天|kg|g|km|m|℃)?"
)
_TIME_RE = re.compile(r"(?:19|20)\d{2}|Q[1-4]|第[一二三四1234]季度|\d{1,2}\s*月")


@dataclass(frozen=True)
class ChartDataFragment:
    chart_type: str
    source_heading: str
    text: str
    reason: str
    source_hash: str


def chart_type_rules_text() -> str:
    return "\n".join(f"- {intent}：{chart_type}" for intent, chart_type in CHART_TYPE_RULES)


def augment_document_with_generated_charts(
    document: BoardDocument,
    *,
    request_message: str,
    max_charts: int = 2,
) -> BoardDocument:
    fragments = extract_chart_data_fragments(document, request_message=request_message, limit=max_charts)
    if not fragments:
        return document

    additions: list[str] = []
    for fragment in fragments:
        if fragment.source_hash in document.content_html:
            continue
        prompt = build_chart_image_prompt(fragment)
        image_src = openai_course_ai.generate_chart_image(
            prompt=prompt,
            chart_type=fragment.chart_type,
            source_excerpt=fragment.text,
        )
        if not image_src:
            continue
        additions.append(_chart_image_html(fragment, image_src))

    if not additions:
        return document

    next_html = "\n".join(part for part in [document.content_html.strip(), *additions] if part.strip())
    return build_document(
        title=document.title,
        content_html=next_html,
        document_id=document.id,
        page_settings=document.page_settings,
    )


def extract_chart_data_fragments(
    document: BoardDocument,
    *,
    request_message: str,
    limit: int = 2,
) -> list[ChartDataFragment]:
    explicit_chart_request = _is_explicit_chart_request(request_message)
    sections = _document_sections(document)
    fragments: list[ChartDataFragment] = []
    seen_hashes: set[str] = set()
    for heading, body in sections:
        text = _compact_text(body, limit=900)
        if not _has_chartable_data(text, explicit_chart_request=explicit_chart_request):
            continue
        chart_type, reason = _choose_chart_type(text, request_message)
        source_hash = _source_hash(text)
        if source_hash in seen_hashes:
            continue
        seen_hashes.add(source_hash)
        fragments.append(
            ChartDataFragment(
                chart_type=chart_type,
                source_heading=heading or document.title,
                text=text,
                reason=reason,
                source_hash=source_hash,
            )
        )
        if len(fragments) >= limit:
            break
    return fragments


def build_chart_image_prompt(fragment: ChartDataFragment) -> str:
    return "\n".join(
        [
            "你是教学板书里的数据图表设计师。请根据给定数据片段生成一张准确、清晰、适合插入中文讲义的图表图片。",
            f"图表类型：{fragment.chart_type}",
            f"选择原因：{fragment.reason}",
            "图表选择规则：",
            chart_type_rules_text(),
            "数据片段：",
            fragment.text,
            "生成要求：",
            "1. 严格只使用数据片段中出现的数据、标签、时间、地点和变量，不编造额外数据。",
            "2. 白底、中文标题、清晰坐标轴/图例/单位，字号足够在 A4 讲义中阅读。",
            "3. 如果数据片段含有总量和增长率，使用双轴组合图；如果含百分比且表达组成关系，使用占比图。",
            "4. 只输出图表本身，不要加入人物、装饰插画、水印或无关说明。",
        ]
    )


def _chart_image_html(fragment: ChartDataFragment, image_src: str) -> str:
    heading = html.escape(f"AI 图表：{fragment.chart_type} - {fragment.source_heading}")
    alt = html.escape(f"AI 图表 {fragment.chart_type} {fragment.source_hash}")
    caption = html.escape(f"图表依据：{fragment.source_heading}。{fragment.reason}")
    return "\n".join(
        [
            f"<h3>{heading}</h3>",
            f'<p data-openclass-chart-source="{fragment.source_hash}">{caption}</p>',
            f'<img src="{html.escape(image_src, quote=True)}" alt="{alt}" />',
        ]
    )


def _document_sections(document: BoardDocument) -> list[tuple[str, str]]:
    html_content = document.content_html or ""
    if html_content.strip():
        sections: list[tuple[str, list[str]]] = []
        current_heading = document.title
        current_parts: list[str] = []
        for match in _BLOCK_RE.finditer(html_content):
            tag = match.group("tag").lower()
            text = _html_block_text(match.group("body"))
            if not text:
                continue
            if tag.startswith("h"):
                if current_parts:
                    sections.append((current_heading, current_parts))
                    current_parts = []
                current_heading = text
                continue
            current_parts.append(text)
        if current_parts:
            sections.append((current_heading, current_parts))
        if sections:
            return [(heading, "\n".join(parts)) for heading, parts in sections]

    text = document.content_text.strip() or html_to_text(html_content)
    if not text:
        return []
    return [(document.title, text)]


def _html_block_text(value: str) -> str:
    normalized = re.sub(r"</(?:td|th)>", " | ", value, flags=re.IGNORECASE)
    normalized = re.sub(r"</(?:p|li|tr)>", "\n", normalized, flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", "", normalized)
    return _compact_text(html.unescape(without_tags), limit=900)


def _compact_text(value: str, *, limit: int) -> str:
    cleaned = re.sub(r"[ \t]+", " ", value or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def _source_hash(text: str) -> str:
    return hashlib.sha1(_compact_text(text, limit=1200).encode("utf-8")).hexdigest()[:12]


def _is_explicit_chart_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message or "")
    return any(term in compact for term in ("图表", "画图", "生成图", "可视化", "趋势图", "柱状图", "折线图", "饼图"))


def _has_chartable_data(text: str, *, explicit_chart_request: bool) -> bool:
    numbers = [match.group(0).strip() for match in _NUMBER_RE.finditer(text)]
    if len(numbers) < 2:
        return False
    compact = re.sub(r"\s+", "", text)
    data_cues = (
        "数据",
        "占比",
        "比例",
        "份额",
        "百分比",
        "增长",
        "趋势",
        "比较",
        "排名",
        "分布",
        "频率",
        "变量",
        "收入",
        "成本",
        "利润",
        "销量",
        "用户",
        "人数",
        "转化率",
        "季度",
        "年份",
        "月份",
        "城市",
        "地区",
        "评分",
        "能力",
        "关系",
        "总量",
        "增长率",
        "%",
        "％",
        "|",
    )
    if explicit_chart_request:
        return True
    if not any(cue in compact for cue in data_cues):
        return False
    if "=" in compact and not any(cue in compact for cue in ("数据", "增长", "比例", "占比", "%", "％", "|")):
        return False
    return True


def _choose_chart_type(text: str, request_message: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", f"{request_message}\n{text}")
    has_time = bool(_TIME_RE.search(compact)) or any(term in compact for term in ("年", "季度", "月份", "月度", "年度"))

    if any(term in compact for term in ("经纬度", "地图", "城市", "省份", "地区分布", "地理", "位置")):
        return "地图", "片段包含地理位置、城市或地区数据，适合用地图呈现空间差异。"
    if "总量" in compact and "增长率" in compact:
        return "组合图", "片段同时包含总量和增长率，适合用柱线组合图表达规模与增速。"
    if any(term in compact for term in ("关系网络", "节点", "边", "连接", "对象之间关系", "网络关系")):
        return "网络图", "片段描述对象之间的连接关系，适合用网络图呈现结构。"
    if any(term in compact for term in ("三个变量", "气泡", "气泡大小", "规模")):
        return "气泡图", "片段需要同时表现三个变量，适合用气泡大小承载第三个变量。"
    if any(term in compact for term in ("两个变量", "相关性", "相关关系", "散点", "横轴", "纵轴")):
        return "散点图", "片段关注两个变量之间的关系，适合用散点图观察相关模式。"
    if any(term in compact for term in ("能力", "维度", "评分", "画像", "雷达")) and len(_NUMBER_RE.findall(text)) >= 3:
        return "雷达图", "片段包含多个维度的能力或评分，适合用雷达图比较轮廓。"
    if any(term in compact for term in ("分布", "频率", "区间", "中位数", "四分位", "箱线", "直方")):
        return "直方图 / 箱线图", "片段关注数据分布、区间或离散程度，适合用分布类图表。"
    if any(term in compact for term in ("占比", "比例", "份额", "构成", "组成", "百分比", "%", "％")):
        return "饼图 / 环形图", "片段关注部分占整体的比例，适合用占比图。"
    if has_time and any(term in compact for term in ("累计", "面积", "总规模", "总用户", "总收入", "整体规模", "总体规模")):
        return "面积图", "片段描述整体随时间变化，适合用面积图突出累计规模。"
    if has_time or any(term in compact for term in ("趋势", "走势", "变化", "增长", "下降", "波动")):
        return "折线图", "片段强调随时间变化的趋势，适合用折线图。"
    return "柱状图 / 条形图", "片段主要用于比较不同对象的数值大小，适合用柱状图或条形图。"
