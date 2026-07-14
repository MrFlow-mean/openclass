from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal


TeachingDiagramDirection = Literal["down", "up", "right", "left"]

MAX_TEACHING_DIAGRAM_NODES = 16
_VALID_DIRECTIONS = frozenset({"down", "up", "right", "left"})
_MAX_CAPTION_CHARS = 240
_MAX_NODE_ID_CHARS = 64
_MAX_NODE_LABEL_CHARS = 360
_MAX_EDGE_LABEL_CHARS = 160
_MAX_CANVAS_DIMENSION = 8_000

_BACKGROUND_COLOR = "#FFFFFF"
_NODE_FILL_COLOR = "#F7F8FA"
_NODE_BORDER_COLOR = "#52606D"
_NODE_TEXT_COLOR = "#18212B"
_EDGE_COLOR = "#66788A"
_EDGE_LABEL_COLOR = "#334155"
_CAPTION_COLOR = "#18212B"

_CANVAS_MARGIN = 48
_CAPTION_GAP = 34
_NODE_WIDTH = 260
_NODE_TEXT_WIDTH = 216
_NODE_PADDING_Y = 18
_MIN_NODE_HEIGHT = 72
_NODE_GAP = 48
_LAYER_GAP = 112

_REGULAR_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/arialuni.ttf",
)

_BOLD_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


@dataclass(frozen=True, slots=True)
class TeachingDiagramNode:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class TeachingDiagramEdge:
    source: str
    target: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class TeachingDiagramSpec:
    caption: str
    direction: TeachingDiagramDirection
    nodes: tuple[TeachingDiagramNode, ...]
    edges: tuple[TeachingDiagramEdge, ...]


@dataclass(frozen=True, slots=True)
class _PreparedNode:
    text: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class _Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def parse_teaching_diagram(raw: object) -> TeachingDiagramSpec:
    """Parse and validate a compact directed-acyclic teaching diagram specification."""

    if isinstance(raw, TeachingDiagramSpec):
        _validate_spec(raw)
        return raw

    payload = _decode_payload(raw)
    caption = _required_text(
        payload,
        "caption",
        context="teaching diagram",
        max_chars=_MAX_CAPTION_CHARS,
        allow_newlines=True,
    )
    direction_raw = _required_text(
        payload,
        "direction",
        context="teaching diagram",
        max_chars=16,
        allow_newlines=False,
    ).lower()
    if direction_raw not in _VALID_DIRECTIONS:
        choices = ", ".join(sorted(_VALID_DIRECTIONS))
        raise ValueError(f"teaching diagram direction must be one of: {choices}")

    raw_nodes = _required_array(payload, "nodes", context="teaching diagram")
    if not raw_nodes:
        raise ValueError("teaching diagram nodes must contain at least one node")
    if len(raw_nodes) > MAX_TEACHING_DIAGRAM_NODES:
        raise ValueError(
            f"teaching diagram supports at most {MAX_TEACHING_DIAGRAM_NODES} nodes; "
            f"received {len(raw_nodes)}"
        )

    nodes: list[TeachingDiagramNode] = []
    seen_node_ids: set[str] = set()
    for index, item in enumerate(raw_nodes):
        node_payload = _mapping(item, context=f"teaching diagram node {index}")
        node_id = _required_text(
            node_payload,
            "id",
            context=f"teaching diagram node {index}",
            max_chars=_MAX_NODE_ID_CHARS,
            allow_newlines=False,
        )
        label = _aliased_required_text(
            node_payload,
            primary="label",
            alias="text",
            context=f"teaching diagram node {index}",
            max_chars=_MAX_NODE_LABEL_CHARS,
            allow_newlines=True,
        )
        if node_id in seen_node_ids:
            raise ValueError(f"teaching diagram node id must be unique: {node_id!r}")
        seen_node_ids.add(node_id)
        nodes.append(TeachingDiagramNode(id=node_id, label=label))

    raw_edges = _required_array(payload, "edges", context="teaching diagram")
    edges: list[TeachingDiagramEdge] = []
    seen_edge_pairs: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_edges):
        edge_payload = _mapping(item, context=f"teaching diagram edge {index}")
        source = _aliased_required_text(
            edge_payload,
            primary="source",
            alias="from",
            context=f"teaching diagram edge {index}",
            max_chars=_MAX_NODE_ID_CHARS,
            allow_newlines=False,
        )
        target = _aliased_required_text(
            edge_payload,
            primary="target",
            alias="to",
            context=f"teaching diagram edge {index}",
            max_chars=_MAX_NODE_ID_CHARS,
            allow_newlines=False,
        )
        label = _optional_text(
            edge_payload,
            "label",
            context=f"teaching diagram edge {index}",
            max_chars=_MAX_EDGE_LABEL_CHARS,
            allow_newlines=True,
        )
        if source not in seen_node_ids:
            raise ValueError(f"teaching diagram edge {index} references unknown source node: {source!r}")
        if target not in seen_node_ids:
            raise ValueError(f"teaching diagram edge {index} references unknown target node: {target!r}")
        if source == target:
            raise ValueError(f"teaching diagram edge {index} cannot connect a node to itself: {source!r}")
        edge_pair = (source, target)
        if edge_pair in seen_edge_pairs:
            raise ValueError(f"teaching diagram contains a duplicate edge: {source!r} -> {target!r}")
        seen_edge_pairs.add(edge_pair)
        edges.append(TeachingDiagramEdge(source=source, target=target, label=label))

    spec = TeachingDiagramSpec(
        caption=caption,
        direction=direction_raw,  # type: ignore[arg-type]
        nodes=tuple(nodes),
        edges=tuple(edges),
    )
    _validate_spec(spec)
    return spec


def render_teaching_diagram_png(
    spec: TeachingDiagramSpec,
    *,
    include_caption: bool = False,
) -> bytes:
    """Render a validated teaching diagram to deterministic PNG bytes with Pillow.

    The document adapter owns the numbered figure caption by default. Standalone
    callers can opt in when they need a self-contained image.
    """

    validated = parse_teaching_diagram(spec)
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on deployment packaging
        raise ValueError("Pillow is required to render a teaching diagram PNG") from exc

    try:
        body_font = _load_font(22)
        edge_font = _load_font(17)
        caption_font = _load_font(27, bold=True)

        measuring_image = Image.new("RGB", (1, 1), _BACKGROUND_COLOR)
        measuring_draw = ImageDraw.Draw(measuring_image)
        caption_text = (
            _wrap_text(measuring_draw, validated.caption, caption_font, 960)
            if include_caption
            else ""
        )
        caption_width, caption_height = (
            _multiline_size(measuring_draw, caption_text, caption_font, spacing=7)
            if caption_text
            else (0, 0)
        )
        caption_gap = _CAPTION_GAP if caption_text else 0

        prepared_nodes = {
            node.id: _prepare_node(measuring_draw, node, body_font)
            for node in validated.nodes
        }
        ranks = _layer_ranks(validated)
        rectangles, graph_width, graph_height = _layout_rectangles(
            validated,
            prepared_nodes,
            ranks,
            graph_top=_CANVAS_MARGIN + caption_height + caption_gap,
            minimum_width=caption_width,
        )

        canvas_width = max(
            int(math.ceil(graph_width + _CANVAS_MARGIN * 2)),
            int(math.ceil(caption_width + _CANVAS_MARGIN * 2)),
        )
        canvas_height = int(
            math.ceil(_CANVAS_MARGIN + caption_height + caption_gap + graph_height + _CANVAS_MARGIN)
        )
        if canvas_width > _MAX_CANVAS_DIMENSION or canvas_height > _MAX_CANVAS_DIMENSION:
            raise ValueError(
                "teaching diagram layout exceeds the maximum canvas size "
                f"({_MAX_CANVAS_DIMENSION} x {_MAX_CANVAS_DIMENSION})"
            )

        image = Image.new("RGB", (max(canvas_width, 1), max(canvas_height, 1)), _BACKGROUND_COLOR)
        draw = ImageDraw.Draw(image)
        if caption_text:
            caption_x = (canvas_width - caption_width) / 2
            draw.multiline_text(
                (caption_x, _CANVAS_MARGIN),
                caption_text,
                font=caption_font,
                fill=_CAPTION_COLOR,
                spacing=7,
                align="center",
            )

        for edge_index, edge in enumerate(validated.edges):
            points = _edge_points(
                rectangles[edge.source],
                rectangles[edge.target],
                validated.direction,
                edge_index,
            )
            draw.line(points, fill=_EDGE_COLOR, width=3, joint="curve")
            _draw_arrowhead(draw, points[-2], points[-1], fill=_EDGE_COLOR)
            if edge.label:
                _draw_edge_label(draw, edge.label, points, edge_font)

        for node in validated.nodes:
            rect = rectangles[node.id]
            prepared = prepared_nodes[node.id]
            draw.rounded_rectangle(
                (rect.left, rect.top, rect.right, rect.bottom),
                radius=12,
                fill=_NODE_FILL_COLOR,
                outline=_NODE_BORDER_COLOR,
                width=3,
            )
            text_width, text_height = _multiline_size(draw, prepared.text, body_font, spacing=6)
            draw.multiline_text(
                (rect.center_x - text_width / 2, rect.center_y - text_height / 2),
                prepared.text,
                font=body_font,
                fill=_NODE_TEXT_COLOR,
                spacing=6,
                align="center",
            )

        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        png_bytes = output.getvalue()
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("teaching diagram renderer did not produce a valid PNG payload")
        return png_bytes
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"failed to render teaching diagram PNG: {exc}") from exc


def _decode_payload(raw: object) -> Mapping[str, object]:
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("teaching diagram bytes must be valid UTF-8 JSON") from exc
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError("teaching diagram JSON must not be empty")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "teaching diagram must be valid JSON: "
                f"{exc.msg} at line {exc.lineno}, column {exc.colno}"
            ) from exc
    return _mapping(raw, context="teaching diagram")


def _mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _required_array(payload: Mapping[str, object], key: str, *, context: str) -> list[object] | tuple[object, ...]:
    if key not in payload:
        raise ValueError(f"{context} is missing required field {key!r}")
    value = payload[key]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{context} field {key!r} must be an array")
    return value


def _required_text(
    payload: Mapping[str, object],
    key: str,
    *,
    context: str,
    max_chars: int,
    allow_newlines: bool,
) -> str:
    if key not in payload:
        raise ValueError(f"{context} is missing required field {key!r}")
    return _validated_text(
        payload[key],
        field_name=f"{context} field {key!r}",
        max_chars=max_chars,
        allow_empty=False,
        allow_newlines=allow_newlines,
    )


def _aliased_required_text(
    payload: Mapping[str, object],
    *,
    primary: str,
    alias: str,
    context: str,
    max_chars: int,
    allow_newlines: bool,
) -> str:
    has_primary = primary in payload
    has_alias = alias in payload
    if not has_primary and not has_alias:
        raise ValueError(f"{context} is missing required field {primary!r}")
    if has_primary and has_alias and payload[primary] != payload[alias]:
        raise ValueError(f"{context} fields {primary!r} and {alias!r} conflict")
    value = payload[primary] if has_primary else payload[alias]
    return _validated_text(
        value,
        field_name=f"{context} field {primary!r}",
        max_chars=max_chars,
        allow_empty=False,
        allow_newlines=allow_newlines,
    )


def _optional_text(
    payload: Mapping[str, object],
    key: str,
    *,
    context: str,
    max_chars: int,
    allow_newlines: bool,
) -> str:
    if key not in payload or payload[key] is None:
        return ""
    return _validated_text(
        payload[key],
        field_name=f"{context} field {key!r}",
        max_chars=max_chars,
        allow_empty=True,
        allow_newlines=allow_newlines,
    )


def _validated_text(
    value: object,
    *,
    field_name: str,
    max_chars: int,
    allow_empty: bool,
    allow_newlines: bool,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if not allow_newlines and "\n" in normalized:
        raise ValueError(f"{field_name} must be a single line")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must not contain NUL characters")
    if len(normalized) > max_chars:
        raise ValueError(f"{field_name} must contain at most {max_chars} characters")
    return normalized


def _validate_spec(spec: TeachingDiagramSpec) -> None:
    if not isinstance(spec.caption, str) or not spec.caption.strip():
        raise ValueError("teaching diagram caption must be a non-empty string")
    if len(spec.caption) > _MAX_CAPTION_CHARS:
        raise ValueError(f"teaching diagram caption must contain at most {_MAX_CAPTION_CHARS} characters")
    if spec.direction not in _VALID_DIRECTIONS:
        choices = ", ".join(sorted(_VALID_DIRECTIONS))
        raise ValueError(f"teaching diagram direction must be one of: {choices}")
    if not spec.nodes:
        raise ValueError("teaching diagram nodes must contain at least one node")
    if len(spec.nodes) > MAX_TEACHING_DIAGRAM_NODES:
        raise ValueError(
            f"teaching diagram supports at most {MAX_TEACHING_DIAGRAM_NODES} nodes; "
            f"received {len(spec.nodes)}"
        )

    node_ids: set[str] = set()
    for index, node in enumerate(spec.nodes):
        if not isinstance(node, TeachingDiagramNode):
            raise ValueError(f"teaching diagram node {index} must be a TeachingDiagramNode")
        _validated_text(
            node.id,
            field_name=f"teaching diagram node {index} id",
            max_chars=_MAX_NODE_ID_CHARS,
            allow_empty=False,
            allow_newlines=False,
        )
        _validated_text(
            node.label,
            field_name=f"teaching diagram node {index} label",
            max_chars=_MAX_NODE_LABEL_CHARS,
            allow_empty=False,
            allow_newlines=True,
        )
        if node.id in node_ids:
            raise ValueError(f"teaching diagram node id must be unique: {node.id!r}")
        node_ids.add(node.id)

    seen_edge_pairs: set[tuple[str, str]] = set()
    for index, edge in enumerate(spec.edges):
        if not isinstance(edge, TeachingDiagramEdge):
            raise ValueError(f"teaching diagram edge {index} must be a TeachingDiagramEdge")
        if edge.source not in node_ids:
            raise ValueError(f"teaching diagram edge {index} references unknown source node: {edge.source!r}")
        if edge.target not in node_ids:
            raise ValueError(f"teaching diagram edge {index} references unknown target node: {edge.target!r}")
        if edge.source == edge.target:
            raise ValueError(f"teaching diagram edge {index} cannot connect a node to itself: {edge.source!r}")
        _validated_text(
            edge.label,
            field_name=f"teaching diagram edge {index} label",
            max_chars=_MAX_EDGE_LABEL_CHARS,
            allow_empty=True,
            allow_newlines=True,
        )
        edge_pair = (edge.source, edge.target)
        if edge_pair in seen_edge_pairs:
            raise ValueError(f"teaching diagram contains a duplicate edge: {edge.source!r} -> {edge.target!r}")
        seen_edge_pairs.add(edge_pair)

    _topological_order(spec)


def _topological_order(spec: TeachingDiagramSpec) -> list[str]:
    node_order = {node.id: index for index, node in enumerate(spec.nodes)}
    adjacency: dict[str, list[str]] = {node.id: [] for node in spec.nodes}
    indegree = {node.id: 0 for node in spec.nodes}
    for edge in spec.edges:
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
    for targets in adjacency.values():
        targets.sort(key=node_order.__getitem__)

    ready = [(node_order[node_id], node_id) for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        _, node_id = heapq.heappop(ready)
        ordered.append(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, (node_order[target], target))

    if len(ordered) != len(spec.nodes):
        raise ValueError("teaching diagram edges must form a DAG; a directed cycle was detected")
    return ordered


def _layer_ranks(spec: TeachingDiagramSpec) -> dict[str, int]:
    ordered = _topological_order(spec)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in spec.edges:
        outgoing[edge.source].append(edge.target)
    rank = {node.id: 0 for node in spec.nodes}
    for source in ordered:
        for target in outgoing[source]:
            rank[target] = max(rank[target], rank[source] + 1)
    return rank


def _load_font(size: int, *, bold: bool = False) -> Any:
    try:
        from PIL import ImageFont
    except ImportError as exc:  # pragma: no cover - depends on deployment packaging
        raise ValueError("Pillow is required to render a teaching diagram PNG") from exc

    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    for font_name in ("NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before the sized default font
        return ImageFont.load_default()


def _wrap_text(draw: Any, value: str, font: Any, max_width: int) -> str:
    wrapped_lines: list[str] = []
    for raw_line in value.replace("\t", "    ").split("\n"):
        remaining = raw_line.strip()
        if not remaining:
            wrapped_lines.append("")
            continue
        while remaining:
            if _text_width(draw, remaining, font) <= max_width:
                wrapped_lines.append(remaining)
                break
            low = 1
            high = len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if _text_width(draw, remaining[:middle], font) <= max_width:
                    low = middle
                else:
                    high = middle - 1
            cut = max(1, low)
            whitespace_cut = remaining.rfind(" ", 0, cut + 1)
            if whitespace_cut > 0:
                cut = whitespace_cut
            wrapped_lines.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
    return "\n".join(wrapped_lines)


def _text_width(draw: Any, value: str, font: Any) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), value or " ", font=font)
    return max(0, right - left)


def _multiline_size(draw: Any, value: str, font: Any, *, spacing: int) -> tuple[int, int]:
    left, top, right, bottom = draw.multiline_textbbox(
        (0, 0),
        value or " ",
        font=font,
        spacing=spacing,
        align="center",
    )
    return max(1, right - left), max(1, bottom - top)


def _prepare_node(draw: Any, node: TeachingDiagramNode, font: Any) -> _PreparedNode:
    text = _wrap_text(draw, node.label, font, _NODE_TEXT_WIDTH)
    _text_width_value, text_height = _multiline_size(draw, text, font, spacing=6)
    return _PreparedNode(
        text=text,
        width=_NODE_WIDTH,
        height=max(_MIN_NODE_HEIGHT, text_height + _NODE_PADDING_Y * 2),
    )


def _layout_rectangles(
    spec: TeachingDiagramSpec,
    prepared_nodes: Mapping[str, _PreparedNode],
    ranks: Mapping[str, int],
    *,
    graph_top: int,
    minimum_width: int,
) -> tuple[dict[str, _Rect], float, float]:
    layers: dict[int, list[TeachingDiagramNode]] = defaultdict(list)
    for node in spec.nodes:
        layers[ranks[node.id]].append(node)

    vertical = spec.direction in {"down", "up"}
    ordered_ranks = sorted(layers, reverse=spec.direction in {"up", "left"})
    if vertical:
        layer_cross_sizes = {
            rank: sum(prepared_nodes[node.id].width for node in nodes) + _NODE_GAP * max(0, len(nodes) - 1)
            for rank, nodes in layers.items()
        }
        layer_axis_sizes = {
            rank: max(prepared_nodes[node.id].height for node in nodes)
            for rank, nodes in layers.items()
        }
        graph_width = max(float(minimum_width), float(max(layer_cross_sizes.values())))
        graph_height = float(sum(layer_axis_sizes[rank] for rank in ordered_ranks)) + _LAYER_GAP * max(
            0, len(ordered_ranks) - 1
        )
        rectangles: dict[str, _Rect] = {}
        axis_cursor = float(graph_top)
        for rank in ordered_ranks:
            nodes = layers[rank]
            cross_cursor = _CANVAS_MARGIN + (graph_width - layer_cross_sizes[rank]) / 2
            layer_height = layer_axis_sizes[rank]
            for node in nodes:
                prepared = prepared_nodes[node.id]
                top = axis_cursor + (layer_height - prepared.height) / 2
                rectangles[node.id] = _Rect(
                    left=cross_cursor,
                    top=top,
                    right=cross_cursor + prepared.width,
                    bottom=top + prepared.height,
                )
                cross_cursor += prepared.width + _NODE_GAP
            axis_cursor += layer_height + _LAYER_GAP
        return rectangles, graph_width, graph_height

    layer_axis_sizes = {
        rank: max(prepared_nodes[node.id].width for node in nodes)
        for rank, nodes in layers.items()
    }
    layer_cross_sizes = {
        rank: sum(prepared_nodes[node.id].height for node in nodes) + _NODE_GAP * max(0, len(nodes) - 1)
        for rank, nodes in layers.items()
    }
    graph_width = float(sum(layer_axis_sizes[rank] for rank in ordered_ranks)) + _LAYER_GAP * max(
        0, len(ordered_ranks) - 1
    )
    graph_width = max(graph_width, float(minimum_width))
    graph_height = float(max(layer_cross_sizes.values()))
    rectangles = {}
    axis_cursor = _CANVAS_MARGIN + max(0.0, (graph_width - (
        sum(layer_axis_sizes[rank] for rank in ordered_ranks) + _LAYER_GAP * max(0, len(ordered_ranks) - 1)
    )) / 2)
    for rank in ordered_ranks:
        nodes = layers[rank]
        cross_cursor = graph_top + (graph_height - layer_cross_sizes[rank]) / 2
        layer_width = layer_axis_sizes[rank]
        for node in nodes:
            prepared = prepared_nodes[node.id]
            left = axis_cursor + (layer_width - prepared.width) / 2
            rectangles[node.id] = _Rect(
                left=left,
                top=cross_cursor,
                right=left + prepared.width,
                bottom=cross_cursor + prepared.height,
            )
            cross_cursor += prepared.height + _NODE_GAP
        axis_cursor += layer_width + _LAYER_GAP
    return rectangles, graph_width, graph_height


def _edge_points(
    source: _Rect,
    target: _Rect,
    direction: TeachingDiagramDirection,
    edge_index: int,
) -> list[tuple[float, float]]:
    lane_offset = ((edge_index % 5) - 2) * 6
    if direction == "down":
        start = (source.center_x, source.bottom)
        end = (target.center_x, target.top)
        middle = (start[1] + end[1]) / 2 + lane_offset
        return [start, (start[0], middle), (end[0], middle), end]
    if direction == "up":
        start = (source.center_x, source.top)
        end = (target.center_x, target.bottom)
        middle = (start[1] + end[1]) / 2 + lane_offset
        return [start, (start[0], middle), (end[0], middle), end]
    if direction == "right":
        start = (source.right, source.center_y)
        end = (target.left, target.center_y)
        middle = (start[0] + end[0]) / 2 + lane_offset
        return [start, (middle, start[1]), (middle, end[1]), end]
    start = (source.left, source.center_y)
    end = (target.right, target.center_y)
    middle = (start[0] + end[0]) / 2 + lane_offset
    return [start, (middle, start[1]), (middle, end[1]), end]


def _draw_arrowhead(
    draw: Any,
    previous: tuple[float, float],
    end: tuple[float, float],
    *,
    fill: str,
) -> None:
    angle = math.atan2(end[1] - previous[1], end[0] - previous[0])
    size = 12
    spread = math.pi / 7
    left = (
        end[0] - size * math.cos(angle - spread),
        end[1] - size * math.sin(angle - spread),
    )
    right = (
        end[0] - size * math.cos(angle + spread),
        end[1] - size * math.sin(angle + spread),
    )
    draw.polygon([end, left, right], fill=fill)


def _draw_edge_label(draw: Any, label: str, points: list[tuple[float, float]], font: Any) -> None:
    label_text = _wrap_text(draw, label, font, 180)
    width, height = _multiline_size(draw, label_text, font, spacing=4)
    middle_index = max(0, (len(points) - 1) // 2)
    start = points[middle_index]
    end = points[middle_index + 1]
    center_x = (start[0] + end[0]) / 2
    center_y = (start[1] + end[1]) / 2
    padding_x = 7
    padding_y = 4
    draw.rounded_rectangle(
        (
            center_x - width / 2 - padding_x,
            center_y - height / 2 - padding_y,
            center_x + width / 2 + padding_x,
            center_y + height / 2 + padding_y,
        ),
        radius=5,
        fill=_BACKGROUND_COLOR,
    )
    draw.multiline_text(
        (center_x - width / 2, center_y - height / 2),
        label_text,
        font=font,
        fill=_EDGE_LABEL_COLOR,
        spacing=4,
        align="center",
    )


__all__ = [
    "MAX_TEACHING_DIAGRAM_NODES",
    "TeachingDiagramDirection",
    "TeachingDiagramEdge",
    "TeachingDiagramNode",
    "TeachingDiagramSpec",
    "parse_teaching_diagram",
    "render_teaching_diagram_png",
]
