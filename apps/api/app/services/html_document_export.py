from __future__ import annotations

import html
import base64
import re
from pathlib import Path
from typing import Callable

from app.models import BoardDocument
from app.services.rich_document import text_to_html, tiptap_doc_to_html

_KATEX_VERSION = "0.16.45"
_SCRIPT_TAG_RE = re.compile(r"<script\b[\s\S]*?</script\s*>", re.IGNORECASE)
_DANGEROUS_TAG_RE = re.compile(r"</?(?:iframe|object|embed|link|meta)\b[^>]*>", re.IGNORECASE)
_EVENT_HANDLER_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"\s+(href|src)\s*=\s*(['\"])\s*javascript:[\s\S]*?\2", re.IGNORECASE)
_BOARD_ASSET_URL_RE = re.compile(
    r"(?P<prefix>\bsrc\s*=\s*(?P<quote>['\"]))"
    r"/api/board-assets/(?P<asset_id>basset_[A-Za-z0-9_-]+)/content"
    r"(?P=quote)",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(
    r"(?P<open><section\b[^>]*>)(?P<body>[\s\S]*?)(?P<close></section\s*>)",
    re.IGNORECASE,
)
_FIGURE_OPEN_RE = re.compile(r"<figure\b[^>]*>", re.IGNORECASE)
_IMAGE_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_BOARD_ASSET_ID_RE = re.compile(r"^basset_[A-Za-z0-9_-]+$")
_ROUNDTRIP_ORIGINAL_SRC_RE = re.compile(
    r"\s+data-original-src\s*=\s*(?:\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)
AssetResolver = Callable[[str], tuple[str, bytes] | None]


def _safe_document_body(content_html: str) -> str:
    body = _SCRIPT_TAG_RE.sub("", content_html)
    body = _DANGEROUS_TAG_RE.sub("", body)
    body = _EVENT_HANDLER_ATTR_RE.sub("", body)
    return _JAVASCRIPT_URL_RE.sub("", body)


def standalone_html(document: BoardDocument, *, asset_resolver: AssetResolver | None = None) -> str:
    title = html.escape(document.title or "OpenClass Document")
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    json_nodes = content_json.get("content")
    if _has_meaningful_tiptap_nodes(json_nodes):
        source_html = tiptap_doc_to_html(content_json)
    else:
        source_html = document.content_html or text_to_html(document.content_text)
    content = _safe_document_body(source_html)
    content = _ROUNDTRIP_ORIGINAL_SRC_RE.sub("", content)
    content = _embed_board_assets(content, asset_resolver)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
      margin: 20px 0;
    }}
    [data-type="resource-visual-block"] figure {{
      margin: 0;
    }}
    [data-type="resource-visual-block"] figcaption {{
      margin-top: 8px;
      color: #43526a;
      font-size: 13px;
      text-align: center;
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


def export_html(
    document: BoardDocument,
    path: Path,
    *,
    asset_resolver: AssetResolver | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(standalone_html(document, asset_resolver=asset_resolver), encoding="utf-8")
    return path


def _embed_board_assets(content_html: str, asset_resolver: AssetResolver | None) -> str:
    if asset_resolver is None:
        return content_html
    resolved: dict[str, str | None] = {}

    def resolve_data_uri(asset_id: str) -> str | None:
        if asset_id not in resolved:
            asset = asset_resolver(asset_id)
            if asset is None:
                resolved[asset_id] = None
            else:
                mime_type, content = asset
                encoded = base64.b64encode(content).decode("ascii")
                resolved[asset_id] = f"data:{mime_type};base64,{encoded}"
        return resolved[asset_id]

    def replace_resource_section(match: re.Match[str]) -> str:
        open_tag = match.group("open")
        if _html_attribute(open_tag, "data-type") != "resource-visual-block":
            return match.group(0)
        asset_id = _html_attribute(open_tag, "data-board-asset-id")
        if not _BOARD_ASSET_ID_RE.fullmatch(asset_id):
            return match.group(0)
        data_uri = resolve_data_uri(asset_id)
        if data_uri is None:
            return match.group(0)

        caption = _html_attribute(open_tag, "data-caption") or "资料视觉素材"
        image = (
            f'<img src="{html.escape(data_uri, quote=True)}" '
            f'alt="{html.escape(caption, quote=True)}">'
        )
        # A controlled resource block represents exactly one permanent visual.
        # Remove any legacy round-trip image before injecting the resolved one.
        body = _IMAGE_TAG_RE.sub("", match.group("body"))
        figure = _FIGURE_OPEN_RE.search(body)
        if figure is None:
            body = image + body
        else:
            body = body[: figure.end()] + image + body[figure.end() :]
        return open_tag + body + match.group("close")

    content_html = _SECTION_RE.sub(replace_resource_section, content_html)

    def replace(match: re.Match[str]) -> str:
        asset_id = match.group("asset_id")
        data_uri = resolve_data_uri(asset_id)
        if data_uri is None:
            return match.group(0)
        return f"{match.group('prefix')}{data_uri}{match.group('quote')}"

    return _BOARD_ASSET_URL_RE.sub(replace, content_html)


def _html_attribute(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(?P<quote>['\"])(?P<value>[\s\S]*?)(?P=quote)",
        tag,
        re.IGNORECASE,
    )
    return html.unescape(match.group("value")) if match else ""


def _has_meaningful_tiptap_nodes(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for node in value:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "paragraph":
            return True
        content = node.get("content")
        if isinstance(content, list) and content:
            return True
    return False
