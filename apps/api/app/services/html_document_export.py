from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from app.models import BoardDocument
from app.services.board_asset_store import BoardAssetStore, get_board_asset_store
from app.services.rich_document import text_to_html, tiptap_doc_to_html

_KATEX_VERSION = "0.16.45"
_RESOURCE_VISUAL_SECTION_RE = re.compile(
    r"(?P<open><section\b(?=[^>]*\bdata-type\s*=\s*['\"]resource-visual-block['\"])[^>]*>)"
    r"(?P<body>[\s\S]*?)(?P<close></section\s*>)",
    re.IGNORECASE,
)
_IMAGE_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_BOARD_ASSET_ID_RE = re.compile(r"^basset_[A-Za-z0-9_-]+$")
_EMBEDDED_IMAGE_MIME_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
_MAX_HTML_EXPORT_VISUAL_BLOCKS = 256
_MAX_HTML_EXPORT_EMBEDDED_IMAGE_BYTES = 64 * 1024 * 1024
_ALLOWED_HTML_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "s",
        "section",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_VOID_HTML_TAGS = frozenset({"br", "hr", "img"})
_DROP_CONTENT_TAGS = frozenset({"embed", "iframe", "noscript", "object", "script", "style", "template"})
_PLAIN_HTML_ATTRS = frozenset(
    {"alt", "class", "colspan", "id", "rel", "rowspan", "start", "target", "title"}
)
_SAFE_CSS_PROPERTIES = frozenset(
    {"background-color", "color", "font-family", "font-size", "text-align"}
)


class HtmlExportBudgetError(ValueError):
    """Raised before an HTML export would exceed its bounded image budget."""


def _safe_document_body(content_html: str) -> str:
    parser = _SafeHtmlParser()
    parser.feed(content_html)
    parser.close()
    return "".join(parser.output)


class _SafeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._blocked_depth:
            if tag not in _VOID_HTML_TAGS:
                self._blocked_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            if tag not in _VOID_HTML_TAGS:
                self._blocked_depth = 1
            return
        if tag not in _ALLOWED_HTML_TAGS:
            return
        rendered_attrs = _safe_html_attrs(tag, attrs)
        suffix = f" {' '.join(rendered_attrs)}" if rendered_attrs else ""
        self.output.append(f"<{tag}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._blocked_depth:
            self._blocked_depth -= 1
            return
        if tag in _ALLOWED_HTML_TAGS and tag not in _VOID_HTML_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.output.append(html.escape(data))


def _safe_html_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> list[str]:
    rendered: list[str] = []
    for raw_name, raw_value in attrs:
        name = raw_name.lower()
        value = raw_value or ""
        if name.startswith("on") or name == "xmlns" or ":" in name:
            continue
        if name == "data-recreation-html":
            continue
        if name == "style":
            value = _safe_inline_style(value)
            if not value:
                continue
        elif name in {"href", "src"}:
            value = _safe_html_url(value, image=name == "src" and tag == "img")
            if not value:
                continue
        elif name == "data-original-src":
            value = _safe_html_url(value, image=True)
            if not value:
                continue
        elif name.startswith("data-"):
            pass
        elif name not in _PLAIN_HTML_ATTRS:
            continue
        elif name in {"colspan", "rowspan", "start"} and not value.isdigit():
            continue
        elif name == "target" and value not in {"_blank", "_self"}:
            continue
        rendered.append(f'{name}="{html.escape(value, quote=True)}"')
    return rendered


def _safe_inline_style(value: str) -> str:
    declarations: list[str] = []
    for declaration in value.split(";"):
        name, separator, raw_value = declaration.partition(":")
        name = name.strip().lower()
        raw_value = raw_value.strip()
        lowered = re.sub(r"\s+", "", raw_value).lower()
        if (
            not separator
            or name not in _SAFE_CSS_PROPERTIES
            or not raw_value
            or any(token in lowered for token in ("url(", "expression(", "javascript:", "@import"))
        ):
            continue
        declarations.append(f"{name}:{raw_value}")
    return ";".join(declarations)


def _safe_html_url(value: str, *, image: bool) -> str:
    normalized = html.unescape(value).strip()
    if not normalized or re.search(r"[\x00-\x1f\x7f]", normalized):
        return ""
    compact = re.sub(r"\s+", "", normalized).lower()
    if compact.startswith(("javascript:", "vbscript:", "file:")):
        return ""
    if compact.startswith("data:"):
        return normalized if image and re.match(
            r"^data:image/(?:png|jpe?g|gif|webp);base64,",
            normalized,
            re.IGNORECASE,
        ) else ""
    scheme = urlsplit(normalized).scheme.lower()
    allowed_schemes = {"http", "https"} if image else {"http", "https", "mailto"}
    if scheme and scheme not in allowed_schemes:
        return ""
    return normalized


def standalone_html(
    document: BoardDocument,
    *,
    owner_user_id: str = "",
    asset_store: BoardAssetStore | None = None,
    asset_resolver: Callable[[str], tuple[str, bytes] | None] | None = None,
) -> str:
    title = html.escape(document.title or "OpenClass Document")
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    json_content = content_json.get("content")
    if isinstance(json_content, list) and any(_meaningful_tiptap_node(node) for node in json_content):
        raw_content = tiptap_doc_to_html(content_json)
    else:
        raw_content = document.content_html or text_to_html(document.content_text)
    content = _safe_document_body(raw_content)
    content = _embed_resource_visual_assets(
        content,
        owner_user_id=owner_user_id,
        asset_store=asset_store,
        asset_resolver=asset_resolver,
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: http: https:; style-src 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'unsafe-inline' https://cdn.jsdelivr.net; font-src data: https://cdn.jsdelivr.net; connect-src 'none'; base-uri 'none'; form-action 'none'">
  <title>{title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/katex.min.css">
  <style>
    :root {{
      color-scheme: light;
      font-family: "Inter", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif;
      color: #172033;
      background: #eef2f7;
    }}
    body {{
      margin: 0;
      padding: 40px 20px;
      background: #eef2f7;
    }}
    main {{
      box-sizing: border-box;
      max-width: 860px;
      min-height: calc(100vh - 80px);
      margin: 0 auto;
      padding: 56px 64px;
      background: #fff;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
    }}
    h1, h2, h3 {{
      line-height: 1.35;
      color: #1d4f91;
    }}
    h1 {{
      margin: 0 0 22px;
      padding-bottom: 14px;
      border-bottom: 1px solid #b9c7d8;
      font-size: 28px;
    }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 21px;
    }}
    h3 {{
      margin: 22px 0 10px;
      font-size: 17px;
    }}
    p, li {{
      font-size: 15px;
      line-height: 1.85;
    }}
    table {{
      width: 100%;
      margin: 16px 0 20px;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 9px 11px;
      border: 1px solid #cfd7e3;
      vertical-align: top;
    }}
    th {{
      background: #f3f6fa;
      font-weight: 700;
    }}
    img {{
      max-width: 100%;
      height: auto;
    }}
    [data-type="resource-visual-block"] {{
      margin: 18px 0;
      padding: 14px;
      border: 1px solid #d8e1ec;
      border-radius: 8px;
      break-inside: avoid;
    }}
    .openclass-resource-visual-media {{
      display: flex;
      justify-content: center;
      margin-bottom: 10px;
    }}
    .openclass-resource-visual-missing {{
      color: #9a5b13;
      font-size: 13px;
    }}
    [data-type="inline-math"] {{
      display: inline-block;
      max-width: 100%;
      overflow-x: auto;
      vertical-align: middle;
    }}
    [data-type="block-math"] {{
      display: block;
      max-width: 100%;
      margin: 16px auto;
      overflow-x: auto;
      text-align: center;
    }}
    .math-fallback {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap;
    }}
    @media print {{
      body {{
        padding: 0;
        background: #fff;
      }}
      main {{
        max-width: none;
        min-height: 0;
        padding: 0;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    {content}
  </main>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@{_KATEX_VERSION}/dist/contrib/mhchem.min.js"></script>
  <script>
    window.addEventListener("load", function () {{
      document.querySelectorAll('[data-type="inline-math"], [data-type="block-math"]').forEach(function (node) {{
        var latex = node.getAttribute("data-latex") || "";
        var displayMode = node.getAttribute("data-type") === "block-math";
        if (!latex) {{
          return;
        }}
        if (window.katex) {{
          try {{
            window.katex.render(latex, node, {{
              displayMode: displayMode,
              throwOnError: false,
              strict: "ignore"
            }});
            return;
          }} catch (error) {{
            // Fall through to readable source fallback.
          }}
        }}
        node.classList.add("math-fallback");
        node.textContent = displayMode ? "\\\\[" + latex + "\\\\]" : "\\\\(" + latex + "\\\\)";
      }});
    }});
  </script>
</body>
</html>
"""


def _meaningful_tiptap_node(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    node_type = str(value.get("type") or "")
    if node_type and node_type != "paragraph":
        return True
    content = value.get("content")
    return isinstance(content, list) and bool(content)


def export_html(
    document: BoardDocument,
    path: Path,
    *,
    owner_user_id: str = "",
    asset_resolver: Callable[[str], tuple[str, bytes] | None] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        standalone_html(
            document,
            owner_user_id=owner_user_id,
            asset_resolver=asset_resolver,
        ),
        encoding="utf-8",
    )
    return path


def _embed_resource_visual_assets(
    content: str,
    *,
    owner_user_id: str,
    asset_store: BoardAssetStore | None,
    asset_resolver: Callable[[str], tuple[str, bytes] | None] | None,
) -> str:
    if (not owner_user_id and asset_resolver is None) or "data-board-asset-id" not in content:
        return content
    store = (asset_store or get_board_asset_store()) if asset_resolver is None else None
    resolved_assets: dict[str, tuple[str, bytes] | None] = {}
    data_uris: dict[str, str] = {}
    visual_block_count = 0
    embedded_image_bytes = 0

    def replace_section(match: re.Match[str]) -> str:
        nonlocal visual_block_count, embedded_image_bytes
        visual_block_count += 1
        if visual_block_count > _MAX_HTML_EXPORT_VISUAL_BLOCKS:
            raise HtmlExportBudgetError(
                f"HTML export exceeds the {_MAX_HTML_EXPORT_VISUAL_BLOCKS}-image limit."
            )
        open_tag = match.group("open")
        asset_id = _html_attribute(open_tag, "data-board-asset-id")
        if not _BOARD_ASSET_ID_RE.fullmatch(asset_id):
            return match.group(0)
        if asset_id not in resolved_assets:
            if asset_resolver is not None:
                resolved_assets[asset_id] = asset_resolver(asset_id)
            else:
                stored = store.read_bytes(asset_id, owner_user_id) if store is not None else None
                resolved_assets[asset_id] = (
                    (stored[0].mime_type, stored[1]) if stored is not None else None
                )
        resolved = resolved_assets[asset_id]
        if resolved is None or resolved[0] not in _EMBEDDED_IMAGE_MIME_TYPES:
            missing = '<p class="openclass-resource-visual-missing">图片内容不可用（板书资产缺失）</p>'
            return f"{open_tag}{missing}{match.group('body')}{match.group('close')}"
        mime_type, image_bytes = resolved
        encoded_size = 4 * ((len(image_bytes) + 2) // 3)
        projected_size = embedded_image_bytes + len(f"data:{mime_type};base64,") + encoded_size
        if projected_size > _MAX_HTML_EXPORT_EMBEDDED_IMAGE_BYTES:
            raise HtmlExportBudgetError(
                "HTML export exceeds the 64 MiB embedded-image limit."
            )
        embedded_image_bytes = projected_size
        caption = (
            _html_attribute(open_tag, "data-original-alt")
            or _html_attribute(open_tag, "data-caption")
            or "资料图片"
        )
        data_uri = data_uris.get(asset_id)
        if data_uri is None:
            data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            data_uris[asset_id] = data_uri
        image_markup = (
            '<div class="openclass-resource-visual-media">'
            f'<img src="{data_uri}" alt="{html.escape(caption, quote=True)}">'
            "</div>"
        )
        # Resource visual markup is system-owned. Recreate its image from the
        # authenticated asset and discard any stale or model-authored img tag.
        body = _IMAGE_TAG_RE.sub("", match.group("body"))
        return f"{open_tag}{image_markup}{body}{match.group('close')}"

    return _RESOURCE_VISUAL_SECTION_RE.sub(replace_section, content)


def _html_attribute(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(['\"])(?P<value>[\s\S]*?)\1",
        tag,
        re.IGNORECASE,
    )
    return html.unescape(match.group("value")).strip() if match else ""
